"""Reproduce the Oct 5 empty-window, metadata, digest and budget regressions offline."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from omegaconf import open_dict

from tests.canned_responses import make_sample_paper, make_chat_response
from tests.test_pipeline import pipeline  # noqa: F401 -- isolated providers/transports
from zotero_arxiv_daily import budget
from zotero_arxiv_daily.construct_email import render_email, email_plain_text
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.identity import deduplicate
from zotero_arxiv_daily.retriever.biorxiv_retriever import BiorxivRetriever
from zotero_arxiv_daily.retriever.medrxiv_retriever import MedrxivRetriever
from zotero_arxiv_daily.retriever.openreview_retriever import OpenReviewRetriever
from zotero_arxiv_daily.retriever.journal_retriever import JournalRetriever
from zotero_arxiv_daily.journals import Journal
from zotero_arxiv_daily.state import State, load_paper, paper_dict


def bio_client(monkeypatch, responses):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        payload = responses[len(calls) - 1]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr('zotero_arxiv_daily.retriever.biorxiv_retriever.session',
                        lambda: nullcontext(SimpleNamespace(get=get)))
    return calls


@pytest.mark.parametrize('cls,name', [(BiorxivRetriever, 'biorxiv'), (MedrxivRetriever, 'medrxiv')])
@pytest.mark.parametrize('collection', [{}, {'collection': []}])
def test_bio_no_posts_is_successful_empty_window(config, monkeypatch, cls, name, collection):
    with open_dict(config.source):
        config.source[name] = {'category': ['*'], 'window_days': 1}
    calls = bio_client(monkeypatch, [{'messages': [{'status': 'no posts found'}]} | collection])
    assert cls(config)._retrieve_raw_papers() == []
    assert len(calls) == 1


def test_empty_category_does_not_discard_other_categories(config, monkeypatch):
    with open_dict(config.source):
        config.source.biorxiv = {'category': ['biochemistry', 'bioinformatics'], 'window_days': 1}
    record = {'doi': '10.1101/2026.10.04.123456', 'version': '1', 'category': 'bioinformatics',
              'date': datetime.now(timezone.utc).date().isoformat()}
    bio_client(monkeypatch, [{'messages': [{'status': 'no posts found'}]},
                            {'messages': [{'status': 'ok', 'total': 1}], 'collection': [record]}])
    assert BiorxivRetriever(config)._retrieve_raw_papers() == [record]


@pytest.mark.parametrize('payload', [
    {'messages': [{'status': 'server error'}], 'collection': []},
    {'messages': [{'status': 'no posts found', 'total': 1}], 'collection': []},
    {'messages': [{'status': 'no posts found'}], 'collection': [{}]},
    {'messages': [{'status': 'no posts found'}], 'collection': None},
    {'messages': [{'status': 'ok', 'total': 1}], 'collection': []},
    {'messages': []},
])
def test_malformed_or_failed_bio_response_stays_fatal(config, monkeypatch, payload):
    with open_dict(config.source):
        config.source.biorxiv = {'category': ['*'], 'window_days': 1}
    bio_client(monkeypatch, [payload])
    with pytest.raises((RuntimeError, ValueError, KeyError, TypeError)):
        BiorxivRetriever(config)._retrieve_raw_papers()


def test_bio_empty_page_cannot_hide_incomplete_pagination(config, monkeypatch):
    with open_dict(config.source):
        config.source.biorxiv = {'category': ['*'], 'window_days': 1}
    bio_client(monkeypatch, [{'messages': [{'status': 'ok', 'total': 2}], 'collection': [{}]},
                            {'messages': [{'status': 'no posts found'}]}])
    with pytest.raises(RuntimeError, match='incomplete'):
        BiorxivRetriever(config)._retrieve_raw_papers()


def test_daily_pipeline_empty_bio_window_succeeds_without_redelivery(pipeline, monkeypatch):
    with open_dict(pipeline):
        pipeline.source.biorxiv = {'category': ['*'], 'window_days': 1}
        pipeline.output.email.enabled = True
        pipeline.output.rss.enabled = False
        pipeline.state.enabled = True
    bio_client(monkeypatch, [{'messages': [{'status': 'no posts found'}]}] * 2)
    sent = []
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email', lambda *args: sent.append(args[1]))
    for _ in range(2):
        executor = Executor(pipeline)
        executor.retrievers['biorxiv'] = BiorxivRetriever(pipeline)
        monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: [make_sample_paper()])
        executor.run()
    assert len(sent) == 1
    assert len(State(pipeline.state.path).records) == 1
    assert not State(pipeline.state.path).pending('email')


@pytest.mark.parametrize('reverse', [False, True])
def test_duplicate_records_preserve_verified_authors_and_affiliations(reverse):
    incomplete = make_sample_paper(authors=[], affiliations=None, doi='10.5555/same', abstract='RSS abstract')
    complete = make_sample_paper(authors=['Research Consortium'], affiliations=['University A'],
                                 doi='10.5555/same', abstract='')
    items = [incomplete, complete]
    merged = deduplicate(items[::-1] if reverse else items)
    assert len(merged) == 1 and merged[0].authors == ['Research Consortium']
    assert merged[0].affiliations == ['University A'] and merged[0].abstract == 'RSS abstract'


def test_duplicate_different_doi_version_never_borrows_affiliations():
    first = make_sample_paper(doi='10.21203/rs.3.rs-100/v2', authors=[], affiliations=None)
    older = make_sample_paper(doi='10.21203/rs.3.rs-100/v1', authors=['Old author'], affiliations=['Old University'])
    assert deduplicate([first, older])[0].affiliations is None


@pytest.mark.parametrize('body,expected', [('', ''), ('Substantive scientific evidence.', 'Substantive scientific evidence.')])
def test_rss_boilerplate_and_repeated_title_are_not_summary_input(config, monkeypatch, body, expected):
    r = JournalRetriever(config)
    now = datetime.now(timezone.utc)
    doi, title = '10.1038/test', 'A molecular study'
    summary = f'Nature, Published online: 4 October 2026; doi:{doi} {title} {body}'
    from time import gmtime
    entry = {'title': title, 'summary': summary, 'link': 'https://doi.org/' + doi,
             'published_parsed': gmtime(now.timestamp())}
    import feedparser
    feed = SimpleNamespace(bozo=False, entries=[entry])
    monkeypatch.setattr(feedparser, 'parse', lambda _: feed)
    client = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, content=b'fixture'))
    papers = r._rss(Journal('nature', 'Nature', ('0028-0836',), 'https://www.nature.com/nature.rss'),
                    client, now-timedelta(days=1), now+timedelta(seconds=1))
    assert papers[0].abstract == expected


@pytest.mark.parametrize('title', [
    'Author Correction: A molecular study', 'Publisher Correction: A molecular study',
    'News: A molecular study', 'Reply to a molecular study',
])
@pytest.mark.parametrize('body', ['', 'Substantive scientific evidence.'])
def test_notices_and_replies_remain_in_rss_with_only_boilerplate_removed(config, monkeypatch, title, body):
    r = JournalRetriever(config)
    now = datetime.now(timezone.utc)
    import feedparser
    from time import gmtime
    doi = '10.1038/correction'
    entry = {'title': title, 'link': 'https://doi.org/' + doi,
             'published_parsed': gmtime(now.timestamp()),
             'summary': f'Nature, Published online: 4 October 2026; doi:{doi} {title} {body}'}
    monkeypatch.setattr(feedparser, 'parse', lambda _: SimpleNamespace(bozo=False, entries=[entry]))
    client = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, content=b'fixture'))
    papers = r._rss(Journal('nature', 'Nature', ('0028-0836',), 'https://www.nature.com/nature.rss'),
                    client, now-timedelta(days=1), now+timedelta(seconds=1))
    assert len(papers) == 1 and papers[0].title == title
    assert papers[0].abstract == body


@pytest.mark.parametrize('title', [
    'Author Correction: A molecular study', 'Publisher Correction: A molecular study',
    'News: A molecular study', 'Reply to a molecular study',
])
def test_crossref_title_filters_do_not_expand_to_author_publisher_corrections_or_replies(config, title):
    r = JournalRetriever(config)
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    item = {'title': [title], 'DOI': '10.1038/correction', 'ISSN': ['0028-0836'],
            'published': {'date-parts': [[2026, 10, 4]]}, 'abstract': 'Substantive evidence.'}
    client = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None,
                             json=lambda: {'message': {'items': [item]}}))
    papers = r._crossref(Journal('nature', 'Nature', ('0028-0836',)),
                         client, now-timedelta(days=1), now+timedelta(days=1))
    assert len(papers) == 1 and papers[0].title == title
    assert papers[0].abstract == 'Substantive evidence.'


def test_header_scoring_explanation_occurs_once_and_uses_effective_weights():
    papers = [make_sample_paper(title=f'Oct5 paper {i}', interest_keyword_weight=.4,
                               interest_zotero_weight=.6, scoring_basis='title only' if i else 'abstract') for i in range(2)]
    html = render_email(papers)
    plain = email_plain_text(html)
    explanation = 'Scored using abstract similarity to keywords (40%) and your library (60%).'
    for output in (html, plain):
        assert output.count(explanation) == 1
        assert output.index(explanation) < output.index('Oct5 paper 0')
        assert output.count('Scored using') == 1
        assert 'title only' in output
        assert 'weights shown on each card' not in output


@pytest.mark.parametrize('weights,expected', [((1., 0.), 'keywords (100%)'), ((0., 1.), 'your library (100%)'),
                                            ((.25, .75), 'keywords (25%) and your library (75%)')])
def test_scoring_explanation_uses_each_actual_setting(weights, expected):
    paper = make_sample_paper(interest_keyword_weight=weights[0], interest_zotero_weight=weights[1])
    assert expected in email_plain_text(render_email([paper]))
    assert expected in email_plain_text(render_email([], interest_weights=weights))


def test_pending_papers_with_different_effective_weights_are_not_misrepresented():
    papers = [make_sample_paper(interest_keyword_weight=.4, interest_zotero_weight=.6), make_sample_paper()]
    plain = email_plain_text(render_email(papers))
    assert plain.count('Scored using') == 1
    assert '40%/60%' in plain and '0%/100%' in plain
    assert plain.index('Scored using') < plain.index('1. ')


def raw_openreview(r, **fields):
    content = {'title': {'value': 'A molecular study'}, 'abstract': {'value': 'Evidence'},
               'primary_area': {'value': 'ai_4_physical_sciences'}} | fields
    return ('NeurIPS', 'NeurIPS.cc/2026/Conference', {'id': 'offline-fixture', 'readers': ['everyone'],
            'odate': int((r.until - timedelta(days=1)).timestamp()*1000), 'content': content})


def openreview(config):
    r = OpenReviewRetriever(config)
    r.until = datetime.now(timezone.utc)
    r.since = r.until - timedelta(days=7)
    return r


def test_openreview_reads_only_public_paper_affiliations(config):
    r = openreview(config)
    raw = raw_openreview(r, authors={'value': ['Author A'], 'readers': ['everyone']},
                        affiliations={'value': ['University A'], 'readers': ['everyone']})
    p = r.convert_to_paper(raw)
    assert p.authors == ['Author A'] and p.affiliations == ['University A']
    assert p.authors_status == p.affiliations_status == 'provided'
    raw[2]['content']['affiliations']['nonreaders'] = ['excluded']
    p = r.convert_to_paper(raw)
    assert p.affiliations is None and p.affiliations_status == 'not_public'


def test_openreview_withheld_identity_is_explained_without_deanonymization(config):
    r = openreview(config)
    p = r.convert_to_paper(raw_openreview(r, authors={'value': ['Private author marker'], 'readers': ['private']}))
    assert p.authors == [] and p.authors_status == 'not_public'
    plain = email_plain_text(render_email([load_paper(paper_dict(p))]))
    assert 'Authors withheld by OpenReview' in plain and 'Private author marker' not in plain
    p = r.convert_to_paper(raw_openreview(r, authors={'value': ['Anonymous']}))
    assert p.authors == [] and p.authors_status == 'anonymized'


@pytest.mark.parametrize('cap,expected', [('0.20', 46), ('0.30', 50)])
def test_budget_cap_preserves_96_tokens_and_originals_on_exhaustion(config, monkeypatch, cap, expected):
    monkeypatch.setattr(budget, 'utc_day', lambda: '2026-10-04')
    config.llm.budget.enabled = True
    config.llm.budget.daily_cny = float(cap)
    config.llm.api.base_url = 'https://api.siliconflow.cn/v1'
    config.llm.generation_kwargs.model = 'deepseek-ai/DeepSeek-V4-Flash'
    cap, per_call = budget.budget_plan(config.llm)
    assert per_call == Decimal('.004320')
    guard = budget.BudgetRequests(cap, per_call, '2026-10-04')
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return make_chat_response('该研究给出了经验证的结果。', model=kwargs['model'])
    client = SimpleNamespace(max_retries=0, chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    papers = [make_sample_paper(title=f'Offline paper {i}', abstract=f'Original evidence [{i}]') for i in range(50)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda p: p.generate_tldr(client, config.llm, guard), papers))
    assert len(calls) == expected and guard.remaining == cap - expected * per_call
    # This flag reports model availability, independently of the budget balance.
    assert guard.unavailable is False
    assert sum(p.tldr_status == 'generated' for p in papers) == expected
    failed = [p for p in papers if p.tldr_error]
    assert len(failed) == 50 - expected and all(p.tldr_error == 'budget_unavailable' for p in failed)
    assert all(p.tldr_error_reason == 'daily_budget_exhausted' and p.tldr == p.abstract for p in failed)
    assert all(kw['extra_body'] == {'enable_thinking': False} and kw['max_tokens'] == 96 for kw in calls)
    plain = email_plain_text(render_email(papers))
    assert ('daily AI budget reached' in plain) is bool(failed)
    assert all(p.abstract in plain for p in failed)
    assert all(p.abstract not in plain for p in papers if p.tldr_status == 'generated')
