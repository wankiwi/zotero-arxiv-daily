from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
import shutil
from urllib.parse import urlparse, parse_qs

import pytest
from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf

from scripts.prepare_workflow import prepare
from zotero_arxiv_daily.preprint_interests import validate_interests, enabled_sources, categories_for, matches_keywords
from zotero_arxiv_daily.retriever.biorxiv_retriever import BiorxivRetriever
from zotero_arxiv_daily.retriever.medrxiv_retriever import MedrxivRetriever
from zotero_arxiv_daily.retriever.base import BaseRetriever

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('value', [{}, {'journals': {'keywords': ['AI']}}, {'arxiv': {}},
    {'arxiv': {'categories': []}}, {'arxiv': {'categories': [' ']}},
    {'arxiv': {'categories': ['*', 'cs.AI']}}, {'biorxiv': {'categories': ['cs.AI']}},
    {'researchsquare': {'categories': ['physics']}}, {'arxiv': {'enabled': 'false'}},
    {'arxiv': {'keywords': None}}, {'arxiv': {'keywords': ['*']}}, {'arxiv': {'category': ['cs.AI']}}])
def test_invalid_interests_fail_closed(config, value):
    config.preprint_interests = OmegaConf.create(value)
    with pytest.raises(ValueError):
        validate_interests(config)


def test_category_precedence_normalization_and_null(config):
    config.preprint_interests = {'arxiv': {'categories': ['cs.AI ', 'physics.chem-ph']}}
    assert categories_for(config, 'arxiv') == ['cs.AI', 'physics.chem-ph']
    config.preprint_interests.arxiv.categories = None
    assert categories_for(config, 'arxiv') == ['cs.AI', 'cs.CV']
    assert enabled_sources(config) == ['arxiv']


@pytest.mark.parametrize('mode', ['all', 'configured', 'journals'])
def test_disabled_wins_over_source_overrides_and_custom_config(tmp_path, mode):
    shutil.copytree(ROOT / 'config', tmp_path / 'config')
    prepare(tmp_path, {'SOURCE_MODE': mode, 'PREPRINT_PROFILE': 'interests',
                      'CUSTOM_CONFIG': 'executor:\n  source: [arxiv, medrxiv, journals]\npreprint_interests:\n  medrxiv:\n    enabled: true\n  researchsquare:\n    keywords: [obsolete]\n    source_ids: [S4306525896, S4306402450]\n'})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        cfg = compose(config_name='runtime')
    assert 'medrxiv' not in cfg.executor.source
    assert cfg.preprint_interests.medrxiv.enabled is False
    assert cfg.preprint_interests.researchsquare.backend == 'openalex'
    assert list(cfg.preprint_interests.researchsquare.source_ids) == ['S4306525896']
    assert 'keywords' not in cfg.preprint_interests.researchsquare
    assert categories_for(cfg, 'arxiv')[-1] == 'cs.AI'
    assert cfg.source.journals == _profile().source.journals
    if mode == 'all':
        assert list(cfg.executor.source) == ['journals', 'arxiv', 'biorxiv', 'researchsquare', 'openreview']


def _profile():
    with initialize_config_dir(config_dir=str(ROOT / 'config'), version_base=None):
        return compose(config_name='interests')


def test_disabled_medrxiv_never_fetches_even_with_null_legacy_category(config, monkeypatch):
    config.preprint_interests = {'medrxiv': {'enabled': False}}
    config.source.medrxiv.category = None
    retriever = MedrxivRetriever(config)
    monkeypatch.setattr(retriever, '_retrieve_window', lambda *a: pytest.fail('disabled source fetched'))
    assert retriever.retrieve_papers() == []
    assert retriever._retrieve_raw_papers() == []


def test_biorxiv_server_categories_paginated_independently(config, monkeypatch):
    config.preprint_interests = {'biorxiv': {'categories': ['biophysics', 'biochemistry']}}
    config.source.biorxiv.window_days = 1
    calls = []
    class Client:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **kwargs):
            calls.append(url)
            parsed = urlparse(url)
            category = parse_qs(parsed.query)['category'][0]
            cursor = int(parsed.path.rsplit('/', 1)[1])
            item = dict(category=category, date=datetime.now(timezone.utc).date().isoformat(),
                        doi=f'10.1101/{category}{cursor}', version=1)
            return SimpleNamespace(raise_for_status=lambda: None,
                json=lambda: {'messages': [{'status': 'ok', 'total': 2}], 'collection': [item]})
    monkeypatch.setattr('zotero_arxiv_daily.retriever.biorxiv_retriever.session', Client)
    assert len(BiorxivRetriever(config)._retrieve_raw_papers()) == 4
    assert [urlparse(url).path.rsplit('/', 1)[1] for url in calls] == ['0', '1', '0', '1']
    assert set(parse_qs(urlparse(url).query)['category'][0] for url in calls) == {'biophysics', 'biochemistry'}


def test_keywords_do_not_filter_journals(config):
    config.preprint_interests = {'researchsquare': {'keywords': ['artificial intelligence']}}
    class Retriever(BaseRetriever):
        name = 'journals'
        def _retrieve_raw_papers(self): return [SimpleNamespace(title='Unrelated subject', abstract='text')]
        def convert_to_paper(self, raw): return raw
    assert len(Retriever(config).retrieve_papers()) == 1
    Retriever.name = 'researchsquare'
    assert Retriever(config).retrieve_papers() == []


def test_keyword_phrase_matching():
    spec = {'keywords': ['AI', 'soft matter', 'biochemistry']}
    assert not matches_keywords(SimpleNamespace(title='pain', abstract='unrelated'), spec)
    assert matches_keywords(SimpleNamespace(title='SOFT-MATTER study', abstract=''), spec)
    assert not matches_keywords(SimpleNamespace(title='soft', abstract='matter'), spec)


def test_arxiv_interest_query_is_server_side(config, monkeypatch):
    import zotero_arxiv_daily.retriever.arxiv_retriever as module
    config.preprint_interests = {'arxiv': {'categories': ['physics.chem-ph', 'cs.AI '], 'keywords': ['soft matter']}}
    searches = []
    class Client:
        def __init__(self, **kwargs): pass
        def results(self, search):
            searches.append(search.query)
            return iter([])
    monkeypatch.setattr(module.arxiv, 'Client', Client)
    assert module.ArxivRetriever(config)._retrieve_raw_papers() == []
    assert 'cat:physics.chem-ph OR cat:cs.AI' in searches[0]
    assert 'ti:"soft matter" OR abs:"soft matter"' in searches[0]
    assert 'cat:cs.CV' not in searches[0]


def test_researchsquare_filter_cannot_resurrect_older_matching_version(config, monkeypatch):
    from tests.retriever.test_preprint_windows import fake_session, rs_item
    from zotero_arxiv_daily.retriever.researchsquare_retriever import ResearchSquareRetriever
    config.preprint_interests = {'researchsquare': {'keywords': ['soft matter']}}
    fake_session(monkeypatch, 'zotero_arxiv_daily.retriever.researchsquare_retriever.session', [
        {'message': {'items': [rs_item(title=['Soft matter']), rs_item('10.21203/rs.3.rs-123/v2', title=['Unrelated topic'])], 'total-results': 2}},
    ])
    assert ResearchSquareRetriever(config).retrieve_papers() == []


@pytest.mark.parametrize('text,expected',[
    ('Straße / SOFT-matter','strasse soft matter'),
    ('ＡＩ and AI','ａｉ and ai'),
    ('x_y / molecular dynamics','x_y molecular dynamics'),
])
def test_keyword_and_identity_normalization_preserve_existing_unicode(text,expected):
    from zotero_arxiv_daily.identity import title_key
    from zotero_arxiv_daily.preprint_interests import keyword_text
    assert title_key(text)==keyword_text(text)==expected
