"""All-category pagination and Research Square metadata/version handling."""
from datetime import datetime, timezone
from types import SimpleNamespace

from omegaconf import open_dict
import pytest

from zot2dailypaper.identity import paper_id
from zot2dailypaper.state import State
from zot2dailypaper.retriever.arxiv_retriever import ArxivRetriever
from zot2dailypaper.retriever.biorxiv_retriever import BiorxivRetriever
from zot2dailypaper.retriever.medrxiv_retriever import MedrxivRetriever
from zot2dailypaper.retriever.researchsquare_retriever import ResearchSquareRetriever


def fake_session(monkeypatch, target, payloads):
    calls = []
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            payload = payloads[len(calls) - 1]
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr(target, lambda *args: Client())
    return calls


def rs_item(doi='10.21203/rs.3.rs-123/v1', **kwargs):
    item = {'DOI': doi, 'type': 'posted-content', 'subtype': 'preprint', 'group-title': 'In Review',
            'title': ['<i>Test</i> &amp; paper'], 'abstract': '<jats:p>Abstract</jats:p>',
            'published': {'date-parts': [[int(n) for n in datetime.now(timezone.utc).strftime('%Y-%m-%d').split('-')]]},
            'author': [{'given': 'Kai', 'family': 'Wan'}, {'name': 'Research Group'}]}
    return item | kwargs


def test_research_square_pagination_versions_and_state(config, monkeypatch, tmp_path):
    first = [rs_item(f'10.21203/rs.3.rs-{number}/v1') for number in range(200)]
    last = [rs_item('10.21203/rs.3.rs-0/v2'), rs_item(subtype='other'),
            rs_item('10.21203/unrelated'), rs_item(published={'date-parts': [[2000, 1, 1]]})]
    calls = fake_session(monkeypatch, 'zot2dailypaper.retriever.researchsquare_retriever.session', [
        {'message': {'items': first, 'total-results': 204, 'next-cursor': 'second'}},
        {'message': {'items': last, 'total-results': 204}},
    ])
    papers = ResearchSquareRetriever(config).retrieve_papers()
    assert len(papers) == 200
    latest = papers[0]
    assert latest.doi == '10.21203/rs.3.rs-0/v2'
    assert latest.title == 'Test & paper' and latest.abstract == 'Abstract'
    assert latest.authors == ['Kai Wan', 'Research Group']
    assert latest.url.endswith('/v2') and latest.pdf_url is None
    assert calls[1][1]['params']['cursor'] == 'second'
    assert 'type:posted-content' in calls[0][1]['params']['filter']
    assert 'from-pub-date:' in calls[0][1]['params']['filter']
    previous = ResearchSquareRetriever(config).convert_to_paper(first[0])
    assert paper_id(previous) == paper_id(latest)
    state = State(tmp_path / 'state.json')
    state.add([previous])
    assert state.has(latest)


@pytest.mark.parametrize('max_pages, next_cursor', [(1, 'second'), (2, '*')])
def test_research_square_incomplete_pagination_fails(config, monkeypatch, max_pages, next_cursor):
    config.source.researchsquare.max_pages = max_pages
    fake_session(monkeypatch, 'zot2dailypaper.retriever.researchsquare_retriever.session', [
        {'message': {'items': [rs_item()] * 200, 'total-results': 201, 'next-cursor': next_cursor}},
    ])
    with pytest.raises(RuntimeError, match='max_pages|pagination'):
        ResearchSquareRetriever(config).retrieve_papers()


@pytest.mark.parametrize('retriever_cls, name', [(BiorxivRetriever, 'biorxiv'), (MedrxivRetriever, 'medrxiv')])
def test_all_bio_med_categories_paginate_and_select_newest(config, monkeypatch, retriever_cls, name):
    with open_dict(config.source):
        config.source[name] = {'category': ['*'], 'window_days': 1, 'max_pages': 3}
    today = datetime.now(timezone.utc).date().isoformat()
    first = [{'doi': f'10.1101/2026.09.30.{number}', 'date': today, 'category': 'different subject', 'version': '1'}
             for number in range(100)]
    last = [first[0] | {'version': '2'}, first[1] | {'date': '2000-01-01'}]
    calls = fake_session(monkeypatch, 'zot2dailypaper.retriever.biorxiv_retriever.session', [
        {'messages': [{'status': 'ok', 'total': '102'}], 'collection': first},
        {'messages': [{'status': 'ok', 'total': '102'}], 'collection': last},
    ])
    records = retriever_cls(config)._retrieve_raw_papers()
    assert len(records) == 100 and records[0]['version'] == '2'
    assert f'/details/{name}/' in calls[0][0]
    assert calls[1][0].endswith('/100')


def test_bio_incomplete_window_fails(config, monkeypatch):
    with open_dict(config.source):
        config.source.biorxiv = {'category': ['*'], 'window_days': 1, 'max_pages': 1}
    fake_session(monkeypatch, 'zot2dailypaper.retriever.biorxiv_retriever.session', [
        {'messages': [{'status': 'ok', 'total': 101}], 'collection': [{}] * 100},
    ])
    with pytest.raises(RuntimeError, match='max_pages'):
        BiorxivRetriever(config)._retrieve_raw_papers()


@pytest.mark.parametrize('categories, days', [(['*'], None), (['physics.chem-ph'], 3)])
def test_arxiv_all_categories_use_bounded_api_query(config, monkeypatch, categories, days):
    import zot2dailypaper.retriever.arxiv_retriever as module
    config.source.arxiv.category = categories
    config.source.arxiv.window_days = days
    searches = []
    class Client:
        def __init__(self, **kwargs):
            pass
        def results(self, search):
            searches.append(search)
            return iter([])
    monkeypatch.setattr(module.arxiv, 'Client', Client)
    monkeypatch.setattr(module.feedparser, 'parse', lambda *args: pytest.fail('Date-window search must use the API'))
    assert ArxivRetriever(config)._retrieve_raw_papers() == []
    assert 'submittedDate:[' in searches[0].query
    assert searches[0].max_results is None
    assert ('cat:physics.chem-ph' in searches[0].query) == ('*' not in categories)


def test_withdrawn_latest_research_square_version_suppresses_old_version(config, monkeypatch):
    fake_session(monkeypatch, 'zot2dailypaper.retriever.researchsquare_retriever.session', [
        {'message': {'items': [rs_item(), rs_item('10.21203/rs.3.rs-123/v2', title=['WITHDRAWN: Test & paper'])],
                     'total-results': 2}},
    ])
    assert ResearchSquareRetriever(config).retrieve_papers() == []


def test_research_square_uses_posted_date(config, monkeypatch):
    item = rs_item()
    item['posted'] = item['published']
    item['published'] = {'date-parts': [[2000, 1, 1]]}
    fake_session(monkeypatch, 'zot2dailypaper.retriever.researchsquare_retriever.session', [
        {'message': {'items': [item], 'total-results': 1}},
    ])
    papers = ResearchSquareRetriever(config).retrieve_papers()
    assert len(papers) == 1
    assert papers[0].published.date() == datetime.now(timezone.utc).date()


@pytest.mark.parametrize('name, retriever_cls', [('arxiv', ArxivRetriever), ('biorxiv', BiorxivRetriever),
                                                ('researchsquare', ResearchSquareRetriever)])
def test_invalid_zero_preprint_window_rejected(config, name, retriever_cls):
    config.source[name].window_days = 0
    if name != 'researchsquare':
        config.source[name].category = ['*']
    with pytest.raises(ValueError, match='window_days'):
        retriever_cls(config)._retrieve_raw_papers()


def test_arxiv_date_window_respects_cross_list_selection(config, monkeypatch):
    import zot2dailypaper.retriever.arxiv_retriever as module
    config.source.arxiv.category = ['physics.chem-ph']
    config.source.arxiv.window_days = 1
    primary = SimpleNamespace(primary_category='physics.chem-ph')
    cross = SimpleNamespace(primary_category='cond-mat.mtrl-sci')
    class Client:
        def __init__(self, **kwargs):
            pass
        def results(self, search):
            return iter([primary, cross])
    monkeypatch.setattr(module.arxiv, 'Client', Client)
    retriever = ArxivRetriever(config)
    assert retriever._retrieve_raw_papers() == [primary]
    config.source.arxiv.include_cross_list = True
    assert retriever._retrieve_raw_papers() == [primary, cross]
