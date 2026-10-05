"""Exact identity, provider refusal and article-specific metadata recovery."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily import metadata, aps_metadata, abstracts
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.state import load_paper, paper_dict, State
from tests.test_pipeline import pipeline  # noqa: F401 -- isolated pipeline fixture


def transport(monkeypatch, respond):
    calls = []
    class Response:
        def __init__(self, code, payload):
            self.status_code, self.payload = code, payload
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def raise_for_status(self):
            if self.status_code != 200:
                raise RuntimeError('Synthetic provider failure')
        def iter_content(self, size):
            yield json.dumps(self.payload).encode()
    def get(url, **kwargs):
        calls.append((url, kwargs))
        result = respond(url)
        return Response(*result)
    monkeypatch.setattr(metadata, 'abstract_session', lambda *a: nullcontext(SimpleNamespace(get=get)))
    monkeypatch.setattr(aps_metadata.time, 'sleep', lambda _: None)
    return calls


def crossref(doi='10.5555/test', title='Test paper', **kwargs):
    return {'message': {'DOI': doi, 'title': [title]} | kwargs}


def openalex(doi='10.5555/test', title='Test paper', **kwargs):
    return {'doi': 'https://doi.org/' + doi, 'title': title} | kwargs


def test_public_crossref_corporate_names_and_affiliations():
    authors, affiliations = metadata.crossref_metadata({'author': [
        {'name': 'Research Consortium', 'affiliation': [{'name': 'University A'}]},
        {'given': 'Jane', 'family': 'Doe', 'affiliation': [{'name': 'University A'}]},
        {'given': '', 'family': ''},
    ]})
    assert authors == ['Research Consortium', 'Jane Doe'] and affiliations == ['University A']


@pytest.mark.parametrize('authors', [None, 'Author', [None], [{'affiliation': None}]])
def test_malformed_crossref_author_fields_are_not_silently_accepted(authors):
    with pytest.raises(ValueError, match='Crossref'):
        metadata.crossref_metadata({'author': authors})


def test_openalex_uses_work_affiliations_and_never_current_author_profile():
    authors, affiliations = metadata.openalex_metadata({'authorships': [{
        'author': {'display_name': 'Author A', 'last_known_institution': {'display_name': 'Unrelated current employer'}},
        'institutions': [{'display_name': 'Indexed University'}],
        'raw_affiliation_strings': ['Paper Department, Paper University'],
    }]})
    assert authors == ['Author A'] and affiliations == ['Paper Department, Paper University']


def test_recovery_uses_exact_doi_and_title_and_preserves_existing_authors(monkeypatch):
    calls = transport(monkeypatch, lambda url: (200, crossref(author=[{'name': 'Different deposited author',
                                                        'affiliation': [{'name': 'University A'}]}])))
    p = make_sample_paper(doi='10.5555/test', title='Test paper', authors=['Existing author'], affiliations=None)
    metadata.recover_metadata([p], {'enabled': True})
    assert p.authors == ['Existing author'] and p.affiliations == ['University A']
    assert p.affiliations_source == 'Crossref' and p.affiliations_status == 'provided'
    assert len(calls) == 1 and all(kw['allow_redirects'] is False for _, kw in calls)
    restored = load_paper(paper_dict(p))
    assert restored.affiliations_source_url.endswith('10.5555%2Ftest')
    assert restored.metadata_recovery_attempts == [{'provider': 'Crossref', 'status': 'recovered'}]


@pytest.mark.parametrize('doi,title', [('10.5555/other', 'Test paper'), ('10.5555/test', 'Other title'),
                                     ('10.21203/rs.3.rs-10/v1', 'Test paper')])
def test_metadata_with_wrong_doi_title_or_version_is_rejected(monkeypatch, doi, title):
    transport(monkeypatch, lambda url: (200, crossref(doi, title, author=[{'name': 'Wrong author',
                'affiliation': [{'name': 'Wrong institution'}]}]) if 'crossref' in url else openalex(doi, title,
                authorships=[{'author': {'display_name': 'Wrong author'}, 'institutions': [{'display_name': 'Wrong institution'}]}])))
    target = '10.21203/rs.3.rs-10/v2' if doi.startswith('10.21203') else '10.5555/test'
    p = make_sample_paper(doi=target, title='Test paper', authors=[], affiliations=None)
    metadata.recover_metadata([p], {})
    assert not p.authors and not p.affiliations
    assert [a['status'] for a in p.metadata_recovery_attempts] == ['identity_mismatch'] * 2


@pytest.mark.parametrize('code', [401, 403, 429])
def test_provider_refusal_stops_every_further_request_to_that_provider(monkeypatch, code):
    calls = transport(monkeypatch, lambda url: (code, {'private-provider-marker': 'withheld'}))
    papers = [make_sample_paper(doi=f'10.5555/test{i}', authors=[], affiliations=None) for i in range(3)]
    metadata.recover_metadata(papers, {})
    assert len(calls) == 2  # One call per provider, no retries or alternate identities.
    assert all(p.authors_status == p.affiliations_status == 'access_blocked' for p in papers)
    assert 'private-provider-marker' not in json.dumps([paper_dict(p) for p in papers])


def test_abstract_provider_refusal_is_shared_with_metadata_recovery(monkeypatch):
    calls = transport(monkeypatch, lambda url: (404, {}))
    context = abstracts.RecoveryContext(blocked={'Crossref'})
    p = make_sample_paper(doi='10.5555/test', affiliations=None)
    metadata.recover_metadata([p], {}, context)
    assert len(calls) == 1 and 'openalex' in calls[0][0]
    assert {'provider': 'Crossref', 'status': 'access_blocked'} in p.metadata_recovery_attempts


def test_metadata_recovery_skips_complete_non_doi_and_openreview_papers(monkeypatch):
    calls = transport(monkeypatch, lambda _: pytest.fail('Unexpected metadata network request'))
    complete = make_sample_paper(doi='10.5555/test', authors=['A'], affiliations=['University A'])
    non_doi = make_sample_paper(doi=None, authors=[], affiliations=None)
    blind = make_sample_paper(source='openreview', doi='10.5555/test', authors=[], affiliations=None,
                             authors_status='not_public')
    metadata.recover_metadata([complete, non_doi, blind], {})
    assert not calls and blind.authors_status == 'not_public'
    assert non_doi.authors_status == non_doi.affiliations_status == 'doi_unavailable'


def test_optional_metadata_limits_and_disabled_setting(monkeypatch):
    calls = transport(monkeypatch, lambda _: pytest.fail('Limit must prevent requests'))
    p = make_sample_paper(doi='10.5555/test', authors=[], affiliations=None)
    metadata.recover_metadata([p], {'max_papers': 0})
    assert not calls and p.authors_status == 'lookup_limit'
    original = paper_dict(p)
    metadata.recover_metadata([p], {'enabled': False})
    assert paper_dict(p) == original


def test_optional_metadata_timeout_is_visible_without_hiding_paper(monkeypatch):
    calls = transport(monkeypatch, lambda _: pytest.fail('Expired deadline must prevent requests'))
    monkeypatch.setattr(metadata, 'monotonic', lambda: 0)
    monkeypatch.setattr(abstracts, 'monotonic', lambda: 100)
    p = make_sample_paper(doi='10.5555/test', authors=[], affiliations=None)
    metadata.recover_metadata([p], {'max_seconds': 1})
    assert not calls and p.authors_status == p.affiliations_status == 'lookup_limit'
    assert p.metadata_recovery_attempts == [{'provider': 'Public metadata', 'status': 'time_limit'}]


def test_todays_four_journal_metadata_cases_recover_three_affiliations_offline(monkeypatch):
    fixture = json.loads((Path(__file__).parent / 'fixtures/oct5_public_metadata.json').read_text())
    cases = fixture['cases']
    def respond(url):
        from urllib.parse import unquote
        case = next(c for c in cases if unquote(url).endswith(c['doi']))
        return 200, case['crossref'] if 'crossref' in url else case['openalex']
    calls = transport(monkeypatch, respond)
    papers = []
    for case in cases:
        authors, _ = metadata.crossref_metadata(case['crossref']['message'])
        papers.append(make_sample_paper(title=case['title'], doi=case['doi'], authors=authors, affiliations=None))
    metadata.recover_metadata(papers, {})
    assert len(calls) == 8 and sum(bool(p.affiliations) for p in papers) == 3
    assert sum(not p.authors for p in papers) == 1
    unavailable = next(p for p in papers if not p.authors)
    assert unavailable.doi == '10.1038/s41557-026-02262-y' and not unavailable.affiliations
    assert unavailable.authors_status == unavailable.affiliations_status == 'not_provided'
    assert all(p.affiliations_source == 'OpenAlex' for p in papers if p.affiliations)


def test_pipeline_recovers_selected_metadata_with_llm_disabled(pipeline, monkeypatch):
    pipeline.executor.max_paper_num = 1
    calls = transport(monkeypatch, lambda _: (200, crossref(author=[{'name': 'Author A',
                                                           'affiliation': [{'name': 'University A'}]}])))
    monkeypatch.setattr('zotero_arxiv_daily.executor.recover_metadata', metadata.recover_metadata)
    executor = Executor(pipeline)
    selected = make_sample_paper(doi='10.5555/test', title='Test paper', authors=[], affiliations=None)
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: [selected])
    executor.run()
    saved = State(pipeline.state.path).pending('email')[0]
    assert len(calls) == 1 and saved.authors == ['Author A'] and saved.affiliations == ['University A']
    assert saved.tldr_status == 'not_generated'
