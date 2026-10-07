"""Paid paths require a synthetic guard even when clients are fake."""
from types import SimpleNamespace
import pytest
from tests.canned_responses import make_sample_paper, make_stub_openai_client, make_budget_guard, make_chat_response
from zot2dailypaper.budget import BudgetUnavailable

@pytest.fixture
def llm_params():
    return {'language':'Chinese','generation_kwargs':{'model':'test-model','max_tokens':16384}}

def test_tldr_returns_response(llm_params):
    paper=make_sample_paper()
    result=paper.generate_tldr(make_stub_openai_client(),llm_params,make_budget_guard())
    assert result=='该研究报告了经过验证的分子模拟结果。' and paper.tldr_status=='generated'

@pytest.mark.parametrize('params',[{}, {'budget':{}}, {'budget':{'enabled':False}}])
def test_missing_or_disabled_reservation_blocks_all_direct_summary_paths(params):
    calls=[]
    client=SimpleNamespace(max_retries=0,chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:calls.append(kw))))
    paper=make_sample_paper()
    paper.generate_tldr(client,params)
    assert paper.tldr_error=='budget_unavailable' and not calls
    with pytest.raises(BudgetUnavailable):paper._generate_tldr_with_llm(client,params)
    if params.get('budget',{}).get('enabled') is False:
        paper.generate_tldr(client,params,make_budget_guard())
        assert not calls

def test_tldr_without_evidence_never_calls(llm_params):
    paper=make_sample_paper(abstract='',full_text=None)
    assert paper.generate_tldr(None,llm_params)=='' and paper.tldr_status=='not_generated'

def test_tldr_fallback_after_reserved_failure(llm_params):
    client=SimpleNamespace(max_retries=0,chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:(_ for _ in ()).throw(RuntimeError('API down')))))
    paper=make_sample_paper()
    assert paper.generate_tldr(client,llm_params,make_budget_guard())==paper.abstract
    assert paper.tldr_error=='request_failed'

def test_affiliations_api_cannot_spend(llm_params):
    paper=make_sample_paper()
    assert paper.generate_affiliations(None,llm_params) is None
    paper.affiliations=['Publisher metadata']
    assert paper.generate_affiliations(None,llm_params)==['Publisher metadata']

def test_long_unicode_abstract_is_bounded_without_tokenizer(llm_params):
    requests=[]
    def create(**kwargs):
        requests.append(kwargs)
        return make_chat_response('中文摘要。', model=kwargs.get('model'))
    client=SimpleNamespace(max_retries=0,chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    paper=make_sample_paper(abstract='科学摘要'*10000)
    paper.generate_tldr(client,llm_params,make_budget_guard())
    assert len(requests[0]['messages'][1]['content'].encode())<=768
    assert paper.abstract=='科学摘要'*10000
