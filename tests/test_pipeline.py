from types import SimpleNamespace
import numpy as np
from omegaconf import open_dict
import pytest
from xml.etree import ElementTree as ET

from tests.canned_responses import make_sample_paper, make_stub_zotero_client
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.state import State
from zotero_arxiv_daily.reranker.api import ApiReranker
from zotero_arxiv_daily.reranker.base import BaseReranker


@pytest.fixture()
def pipeline(config, tmp_path, monkeypatch):
    with open_dict(config):
        config.executor.source = ['arxiv']
        config.executor.fetch_full_text = False
        config.executor.reranker = 'api'
        config.llm.enabled = False
        config.output.email.enabled = False
        config.output.rss.enabled = True
        config.output.rss.path = str(tmp_path / 'feed.xml')
        config.state.path = str(tmp_path / 'state.json')
    monkeypatch.setattr('zotero_arxiv_daily.executor.zotero.Zotero', lambda *a, **kw: make_stub_zotero_client())
    def similarity(self, left, right):
        return np.ones((len(left), len(right))) * .8
    monkeypatch.setattr(ApiReranker, 'get_similarity_score', similarity)
    return config


def test_rss_only_does_not_resolve_smtp_or_llm_credentials(pipeline, monkeypatch):
    with open_dict(pipeline):
        pipeline.email = {'sender': '???'}
        pipeline.llm.api = {'key': '???'}
    executor = Executor(pipeline)
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: [make_sample_paper()])
    executor.run()
    assert len(ET.parse(pipeline.output.rss.path).findall('./channel/item')) == 1
    repeat = Executor(pipeline)
    monkeypatch.setattr(repeat.retrievers['arxiv'], 'retrieve_papers', lambda: [make_sample_paper()])
    monkeypatch.setattr(repeat.reranker, 'rerank', lambda *a: pytest.fail('Already delivered papers must not be ranked again'))
    repeat.run()
    assert len(ET.parse(pipeline.output.rss.path).findall('./channel/item')) == 1


def test_email_failure_does_not_block_rss_and_retries_without_retrieval(pipeline, monkeypatch):
    with open_dict(pipeline):
        pipeline.output.email.enabled = True
    executor = Executor(pipeline)
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: [make_sample_paper()])
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email', lambda *a: (_ for _ in ()).throw(OSError('smtp down')))
    with pytest.raises(RuntimeError, match='smtp down'):
        executor.run()
    state = State(pipeline.state.path)
    assert state.pending('email') and not state.pending('rss')
    assert len(ET.parse(pipeline.output.rss.path).findall('./channel/item')) == 1
    sent = []
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email', lambda *a: sent.append(a))
    repeat = Executor(pipeline)
    monkeypatch.setattr(repeat.retrievers['arxiv'], 'retrieve_papers', lambda: [])
    repeat.run()
    assert len(sent) == 1 and not State(pipeline.state.path).pending('email')


def test_enrichment_runs_only_for_selected_papers(pipeline, monkeypatch):
    with open_dict(pipeline):
        pipeline.executor.max_paper_num = 1
        pipeline.executor.fetch_full_text = True
        pipeline.llm.input_mode = 'full_text'
        pipeline.llm.enabled = True
    from tests.canned_responses import make_stub_openai_client
    monkeypatch.setattr('zotero_arxiv_daily.executor.OpenAI', lambda **kwargs: make_stub_openai_client())
    executor = Executor(pipeline)
    papers = [make_sample_paper(title=f'Paper {i}', url=f'https://example.org/{i}') for i in range(3)]
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: papers)
    enriched = []
    def enrich(p):
        enriched.append(p.title)
        return p
    monkeypatch.setattr(executor.retrievers['arxiv'], 'enrich', enrich)
    executor.run()
    assert len(enriched) == 1


def test_partial_source_failure_is_not_reported_as_success(pipeline, monkeypatch):
    executor = Executor(pipeline)
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: (_ for _ in ()).throw(OSError('feed down')))
    with pytest.raises(RuntimeError, match='feed down'):
        executor.run()


def test_abstract_recovery_budget_targets_selected_papers(pipeline, monkeypatch):
    with open_dict(pipeline):
        pipeline.executor.max_paper_num = 1
        pipeline.abstracts = {'enabled': True, 'max_papers': 1}
    executor = Executor(pipeline)
    papers = [make_sample_paper(title=f'Candidate {i}', abstract='',
              doi=f'10.1234/paper{i}', url=f'https://example.org/{i}') for i in range(60)]
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: papers)
    def rank(items, corpus):
        assert all(not p.abstract for p in items)
        for i, p in enumerate(items):
            p.score, p.scoring_basis = i, 'title only'
        return list(reversed(items))
    monkeypatch.setattr(executor.reranker, 'rerank', rank)
    looked_up = []
    def recover(items, config):
        assert config.max_papers == 1 and len(items) == 1
        looked_up.extend(items)
        items[0].abstract = 'Recovered original abstract'
    monkeypatch.setattr('zotero_arxiv_daily.executor.recover_abstracts', recover)
    executor.run()
    assert looked_up == [papers[-1]]
    saved = next(iter(State(pipeline.state.path).records.values()))['paper']
    assert saved['abstract'] == 'Recovered original abstract'
    assert saved['scoring_basis'] == 'title only'


def test_missing_zotero_collection_and_abstract_are_handled(pipeline, monkeypatch):
    items = [{'data': {'title': 'Title', 'dateAdded': '2026-03-02T00:00:00Z', 'abstractNote': 'Useful', 'collections': ['missing']}},
             {'data': {'title': 'No abstract', 'dateAdded': '2026-03-02T00:00:00Z'}}]
    monkeypatch.setattr('zotero_arxiv_daily.executor.zotero.Zotero', lambda *a, **kw: make_stub_zotero_client(items=items))
    corpus = Executor(pipeline).fetch_zotero_corpus()
    assert len(corpus) == 1 and corpus[0].paths == []


def test_collection_cycles_raise_useful_error(pipeline, monkeypatch):
    collections = [{'key': 'COL1', 'data': {'name': 'A', 'parentCollection': 'COL2'}}, {'key': 'COL2', 'data': {'name': 'B', 'parentCollection': 'COL1'}}]
    monkeypatch.setattr('zotero_arxiv_daily.executor.zotero.Zotero', lambda *a, **kw: make_stub_zotero_client(collections=collections))
    with pytest.raises(ValueError, match='Cycle'):
        Executor(pipeline).fetch_zotero_corpus()


def test_embeddings_batch_unique_texts_and_restore_response_indices(config, monkeypatch):
    calls = []
    def create(input, **kwargs):
        calls.append(input)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[i + 1., 1.]) for i in reversed(range(len(input)))])
    monkeypatch.setattr('zotero_arxiv_daily.reranker.api.OpenAI', lambda **kw: SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    reranker = ApiReranker(config)
    first = reranker.get_similarity_score(['same', 'other', 'same'], ['same'])
    assert calls == [['same', 'other']]
    assert first[0, 0] == pytest.approx(1) and first[0, 0] == first[2, 0]
    reranker.get_similarity_score(['same'], ['other'])
    assert len(calls) == 1


def test_zero_embedding_is_rejected(config, monkeypatch):
    client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kw: SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0., 0.])])) )
    monkeypatch.setattr('zotero_arxiv_daily.reranker.api.OpenAI', lambda **kw: client)
    with pytest.raises(ValueError, match='zero vector'):
        ApiReranker(config).get_similarity_score(['same'], ['same'])


def test_missing_abstract_ranks_with_title(config):
    class Reranker(BaseReranker):
        def get_similarity_score(self, left, right):
            assert left == ['A title']
            return np.ones((1, 1))
    from tests.canned_responses import make_sample_corpus
    paper = make_sample_paper(title='A title', abstract='')
    assert Reranker(config).rerank([paper], make_sample_corpus(1))[0].scoring_basis == 'title only'


@pytest.mark.parametrize('failure_stage', ['corpus', 'ranking'])
def test_pending_delivery_survives_new_recommendation_failure(pipeline, monkeypatch, failure_stage):
    with open_dict(pipeline):
        pipeline.output.email.enabled = True
    previous = make_sample_paper(title='Previously selected', url='https://example.org/previous', score=8)
    state = State(pipeline.state.path)
    state.add([previous])
    state.save()
    executor = Executor(pipeline)
    def fail(*args):
        raise OSError('upstream unavailable')
    if failure_stage == 'corpus':
        monkeypatch.setattr(executor, 'fetch_zotero_corpus', fail)
    else:
        monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: [make_sample_paper()])
        monkeypatch.setattr(executor.reranker, 'rerank', fail)
    sent = []
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email', lambda *args: sent.append(args))
    with pytest.raises((RuntimeError, OSError), match='upstream unavailable'):
        executor.run()
    assert len(sent) == 1
    assert not State(pipeline.state.path).pending('email')
    assert not State(pipeline.state.path).pending('rss')
    assert ET.parse(pipeline.output.rss.path).findtext('./channel/item/title') == 'Previously selected'


def test_existing_research_square_version_is_not_recommended_again(pipeline, monkeypatch):
    items = [{'data': {'title': 'Earlier title', 'DOI': '10.21203/rs.3.rs-123/v1',
                      'dateAdded': '2026-03-02T00:00:00Z', 'abstractNote': 'Useful abstract', 'collections': []}}]
    monkeypatch.setattr('zotero_arxiv_daily.executor.zotero.Zotero', lambda *a, **kw: make_stub_zotero_client(items=items))
    executor = Executor(pipeline)
    paper = make_sample_paper(title='Revised title', doi='10.21203/rs.3.rs-123/v2', url='https://doi.org/10.21203/rs.3.rs-123/v2')
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: [paper])
    monkeypatch.setattr(executor.reranker, 'rerank', lambda *a: pytest.fail('Existing DOI identity must be excluded'))
    executor.run()
    assert not ET.parse(pipeline.output.rss.path).findall('./channel/item')


def test_partial_researchsquare_results_save_delivery_without_repeat(pipeline, monkeypatch):
    pipeline.output.email.enabled = True
    pipeline.executor.send_empty = False
    sent=[]
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email',lambda *args:sent.append(args))
    paper=make_sample_paper(source='researchsquare')
    for _ in range(2):
        executor=Executor(pipeline)
        executor.retrievers={'researchsquare':SimpleNamespace(retrieve_papers=lambda:[paper], failures=['S4306402450: OpenAlex source HTTP 404'])}
        with pytest.raises(RuntimeError, match='researchsquare: incomplete retrieval'):
            executor.run()
    assert len(sent)==1
    state=State(pipeline.state.path)
    assert not state.pending('email') and not state.pending('rss')
