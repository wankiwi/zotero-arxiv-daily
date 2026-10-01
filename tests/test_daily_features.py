from datetime import datetime, timedelta, timezone
import random
from types import SimpleNamespace
import pytest
from omegaconf import OmegaConf
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.selection import select_papers, pending_batch, quotas_for, paper_group
from zotero_arxiv_daily.abstracts import clean_abstract, recover_abstracts
from zotero_arxiv_daily.construct_email import render_email, email_plain_text
from zotero_arxiv_daily.retriever.openreview_retriever import OpenReviewRetriever

def paper(i, source='journals', **kwargs):
    return Paper(source=source,title=f'Paper {i}',abstract='A molecular materials study.',authors=[],url=f'https://example.org/{i}',score=10-i/100,**kwargs)

def test_quotas_and_unbiased_remaining_sample():
    pool=[paper(i) for i in range(40)]+[paper(i,'openreview') for i in range(40,70)]
    quotas={'journals':25,'preprints':15,'random':5}
    selected=select_papers(pool,quotas,rng=random.Random(42))
    assert [sum(paper_group(p)==g for p in selected) for g in quotas]==[25,15,5]
    assert len({p.url for p in selected})==45
    assert selected[:25]==pool[:25]
    assert selected[25:40]==pool[40:55]
    assert all(p in pool[25:40]+pool[55:] for p in selected[40:])

def test_shortage_pending_reservation_and_persistence(tmp_path):
    from zotero_arxiv_daily.state import State
    quotas={'journals':25,'preprints':15,'random':5}
    old=[paper(200,recommendation_group='random')]
    selected=select_papers([paper(1),paper(2,'openreview')],quotas,old)
    assert len(selected)==2
    state=State(tmp_path/'history.json');state.add(old+selected);state.mark([selected[0]],'email');state.save()
    restored=State(tmp_path/'history.json')
    assert restored.has(selected[0])
    assert {p.url for p in restored.pending('email')}=={old[0].url,selected[1].url}
    assert paper_group(restored.pending('email')[0])=='random'
    assert len(pending_batch([paper(i) for i in range(30)],quotas,45))==25

@pytest.mark.parametrize('value',[{'journals':-1,'preprints':15,'random':5},{'journals':True,'preprints':15,'random':5},{'journals':25}, {'journals':25,'preprints':15,'random':5}])
def test_invalid_quotas(value):
    with pytest.raises(ValueError):quotas_for(OmegaConf.create({'quotas':value,'max_paper_num':20}))

def test_clean_abstract():
    assert clean_abstract('<jats:p>A &amp; B</jats:p><script>bad()</script><p>C</p>')=='A & B C'
    assert clean_abstract('&lt;p&gt;Readable&lt;/p&gt;')=='Readable'
    assert clean_abstract('No abstract available')==''
    assert clean_abstract(None)==''

def test_recovery_validates_doi_and_falls_back(monkeypatch):
    import zotero_arxiv_daily.abstracts as module
    calls=[]
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):
            calls.append(url)
            data={'message':{'DOI':'10.1000/wrong','abstract':'Wrong paper'}} if 'crossref' in url else {'doi':'https://doi.org/10.1000/right','abstract_inverted_index':{'Molecular':[0],'study':[1]}}
            return SimpleNamespace(status_code=200,raise_for_status=lambda:None,json=lambda:data)
    monkeypatch.setattr(module,'session',lambda *a:Client())
    p=paper(1,doi='10.1000/right');p.abstract=''
    recover_abstracts([p],{'enabled':True,'max_papers':1})
    assert p.abstract=='Molecular study' and p.abstract_source=='OpenAlex' and len(calls)==2

def test_recovery_access_denied_visible_and_bounded(monkeypatch, capsys):
    import zotero_arxiv_daily.abstracts as module
    calls=[]
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):calls.append(url);return SimpleNamespace(status_code=403)
    monkeypatch.setattr(module,'session',lambda *a:Client())
    papers=[paper(i,doi=f'10.1000/{i}') for i in range(5)]
    for p in papers:p.abstract=''
    recover_abstracts(papers,{'enabled':True,'max_papers':3})
    assert len(calls)==2 and all(not p.abstract for p in papers)

def retriever(config):
    r=OpenReviewRetriever(config)
    r.until=datetime.now(timezone.utc);r.since=r.until-timedelta(days=7)
    return r

def note(r,**content):
    fields={'title':'A molecular study','abstract':'Experiments','authors':['A'],'primary_area':['ai_4_physical_sciences']}
    fields.update(content)
    return ('NeurIPS','NeurIPS.cc/2026/Conference',{'id':'test', 'odate':int((r.until-timedelta(days=1)).timestamp()*1000),
        'cdate':int((r.until-timedelta(days=200)).timestamp()*1000),'content':{k:{'value':v} for k,v in fields.items()}})

def test_openreview_array_schema_old_creation_new_public(config):
    r=retriever(config);p=r.convert_to_paper(note(r))
    assert p and p.source=='openreview' and p.subject_match_reason.startswith('subject field')

def test_openreview_keyword_gate_excludes_subject_labels(config):
    r=retriever(config)
    assert r.convert_to_paper(note(r,title='General optimization',abstract='A study',primary_area='applications to physical sciences (physics, chemistry, biology, etc.)')) is None
    assert r.convert_to_paper(note(r,title='General optimization',abstract='New molecular dynamics methods',primary_area=None)) is not None
    assert r.convert_to_paper(note(r,title='General optimization',abstract='A study',primary_area=None,keywords=['atomistic'])) is not None

def test_openreview_explicit_subject_fallback_and_reject_unknown(config):
    r=retriever(config)
    p=r.convert_to_paper(note(r,primary_area=None))
    assert 'fallback' in p.subject_match_reason
    config.source.openreview.subject_areas=['Unsupported']
    with pytest.raises(ValueError,match='subject'):OpenReviewRetriever(config)

def test_openreview_timestamp_window_and_private_notes(config):
    r=retriever(config);raw=note(r);raw[2]['odate']=int((r.until-timedelta(days=20)).timestamp()*1000)
    assert r.convert_to_paper(raw) is None
    raw=note(r);raw[2]['readers']=['private'];assert r.convert_to_paper(raw) is None

def test_openreview_access_error_is_not_empty_success(config):
    r=retriever(config)
    client=SimpleNamespace(get=lambda *a,**kw:SimpleNamespace(status_code=403))
    with pytest.raises(RuntimeError,match='access blocked HTTP 403'):r._get(client,'/notes',{})

def test_openreview_pagination_deduplicates_and_reports_partial(config,monkeypatch):
    import zotero_arxiv_daily.retriever.openreview_retriever as module
    config.source.openreview.venues=['TMLR'];r=retriever(config)
    records=[note(r)[2]]
    pages=iter([{'notes':records,'count':3},{'notes':records+[dict(records[0],id='second')],'count':3}])
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(module,'session',lambda:Client())
    monkeypatch.setattr(r,'_groups',lambda *a:[('TMLR','TMLR/-/Submission')])
    monkeypatch.setattr(r,'_get',lambda *a:next(pages))
    assert len(r._retrieve_raw_papers())==2 and not r.failures
    monkeypatch.setattr(r,'_get',lambda *a:(_ for _ in ()).throw(RuntimeError('blocked')))
    assert r._retrieve_raw_papers()==[] and r.failures

def test_three_numbered_groups_plain_and_html():
    papers=[paper(1),paper(2,'openreview'),paper(3,recommendation_group='random')]
    papers[2].abstract=''
    html=render_email(papers);plain=email_plain_text(html)
    for title in ('1. Paper 1','1. Paper 2','1. Paper 3'):assert title in html and title in plain
    assert html.count('class="digest-column"')==3
    assert 'max-width:720px' in html and 'width="33.33%"' in html
    assert 'No abstract available' in html
    assert plain.index('Journals')<plain.index('Preprints')<plain.index('Random')
    assert 'No new recommendations in this group' in render_email([])

def test_chinese_prompt_without_live_llm(monkeypatch):
    import zotero_arxiv_daily.protocol as module
    monkeypatch.setattr(module,'truncate_prompt',lambda text,limit:text)
    requests=[]
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='该研究提出了分子模拟方法。'))])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p=paper(1);p.generate_tldr(client,{'language':'Chinese','generation_kwargs':{'model':'test'}})
    assert p.tldr_status=='generated' and len(requests)==1
    assert 'exactly one sentence in Chinese' in requests[0]['messages'][0]['content']

@pytest.mark.parametrize('group',['journals','preprints','random'])
@pytest.mark.parametrize('status',['generated','fallback','not_generated'])
@pytest.mark.parametrize('abstract',['Original abstract ' * 100, ''])
def test_original_abstract_always_preserved(group,status,abstract):
    p=paper(1,recommendation_group=group);p.abstract=abstract
    p.tldr='该研究提出新方法。' if status=='generated' else abstract
    p.tldr_status=status
    html=render_email([p]);plain=email_plain_text(html)
    if abstract:
        assert abstract in html and abstract.strip() in plain
    else:
        assert 'No abstract available' in html and 'No abstract available' in plain
    if status=='generated':
        assert '该研究提出新方法。' in html and 'Original abstract / 原摘要' in plain

@pytest.mark.parametrize('mode,full_text,expected,reason',[
    ('abstract','Entire full text','abstract',None),
    ('full_text','Entire full text','full_text',None),
    ('full_text',None,'abstract','full_text_unavailable'),
    ('full_text','Long text '*10000,'abstract','full_text_exceeds_context_limit'),
])
def test_summary_input_selection_and_no_fulltext_prefix(mode,full_text,expected,reason,monkeypatch):
    import zotero_arxiv_daily.protocol as module
    calls=[]
    monkeypatch.setattr(module,'truncate_prompt',lambda text,limit:text[:limit])
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='该研究提出新方法。'))])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p=paper(1,full_text=full_text)
    p.generate_tldr(client,{'input_mode':mode})
    assert p.summary_input_source==expected and p.summary_input_fallback==reason
    assert len(calls)==1
    prompt=calls[0]['messages'][1]['content']
    if expected=='full_text':assert full_text in prompt and 'Abstract:' not in prompt
    else:assert p.abstract in prompt and 'Full text:' not in prompt
    assert p.abstract in render_email([p])

def test_full_text_budget_fallback_is_one_call(config):
    from decimal import Decimal
    from zotero_arxiv_daily.budget import BudgetRequests, utc_day
    config.llm.budget.enabled=True;config.llm.input_mode='full_text'
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='该研究提出新方法。'))])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p=paper(1,full_text='Full paper '*10000)
    p.generate_tldr(client,config.llm,BudgetRequests(Decimal('.20'),Decimal('.004608'),utc_day()))
    assert len(calls)==1 and p.summary_input_source=='abstract'
    assert p.summary_input_fallback=='full_text_exceeds_budget_input_bound'
    assert p.summary_input_fallback in render_email([p])

def test_abstract_mode_never_fetches_fulltext(config):
    from zotero_arxiv_daily.executor import Executor
    from zotero_arxiv_daily.llm import ModelRequests
    executor=Executor.__new__(Executor);executor.config=config;executor.openai_client=None
    executor.model_requests=ModelRequests()
    executor.retrievers={'journals':SimpleNamespace(enrich=lambda p:pytest.fail('abstract mode fetched full text'))}
    executor._enrich(paper(1))

@pytest.mark.parametrize('fields',[
    {'primary_area':['unrelated'], 'secondary_area':['ai_4_physical_sciences']},
    {'primary_area':'applications->chemistry_physics_and_earth_sciences'},
    {'primary_area':'applications to physical sciences (physics, chemistry, biology, etc.)'},
])
def test_venue_schema_subject_aliases(config,fields):
    r=retriever(config)
    assert r.convert_to_paper(note(r,**fields)).subject_match_reason.startswith('subject field')

def test_corl_author_keyword_gate(config):
    r=retriever(config)
    raw=note(r,title='Robot planning',abstract='Experiments',primary_area=None,free_keyword_1='molecular dynamics')
    assert r.convert_to_paper(raw)

def test_next_year_iclr_group_discovery(config):
    r=retriever(config);seen=[]
    def get(client,path,params):
        identity=params['id'];seen.append(identity)
        return {'groups':[{'id':identity,'content':{'submission_id':{'value':identity+'/-/Submission'}}}]}
    r._get=get
    groups=list(r._groups(None,'ICLR',2026))
    assert 'ICLR.cc/2027/Conference' in seen
    assert all('/Workshop' not in group for group,inv in groups)

def test_invalid_summary_mode_fails_before_source_or_api_setup(config):
    from zotero_arxiv_daily.executor import Executor
    config.llm.input_mode='prefix'
    with pytest.raises(ValueError,match='input_mode'):Executor(config)
