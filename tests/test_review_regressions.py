"""Review regressions; all external responses are fabricated."""
from datetime import datetime, timezone
from types import SimpleNamespace
from dataclasses import replace
import re
from omegaconf import open_dict
from zotero_arxiv_daily.identity import deduplicate
from zotero_arxiv_daily.journals import CORE
from zotero_arxiv_daily.retriever.journal_retriever import JournalRetriever
from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever
from tests.canned_responses import make_sample_paper

UTC = timezone.utc

def test_distinct_query_article_urls_are_not_deduplicated(tmp_path):
    first = make_sample_paper(title='First article', doi=None, url='https://publisher.example/article?id=101')
    second = make_sample_paper(title='Second article', doi=None, url='https://publisher.example/article?id=102')
    assert len(deduplicate([first, second])) == 2


def test_atom_non_doi_id_does_not_mask_doi_link(config):
    xml = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Journal</title>
    <entry><id>urn:uuid:abc</id><title>A discovery</title><updated>2026-03-02T00:00:00Z</updated>
    <link href="https://doi.org/10.1021/example"/></entry></feed>'''
    client = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(content=xml, raise_for_status=lambda: None))
    papers = JournalRetriever(config)._rss(CORE['jacs'], client, datetime(2026,3,1,tzinfo=UTC), datetime(2026,3,5,tzinfo=UTC))
    assert papers[0].doi == '10.1021/example'


def test_arxiv_default_window_sees_delayed_announcements(config, monkeypatch):
    # Submitted Friday 12:00 EDT, public Sunday 20:00 EDT; daily job is 22:00 UTC.
    submitted = datetime(2026,9,25,16,tzinfo=UTC)
    public = datetime(2026,9,28,0,tzinfo=UTC)
    class Clock(datetime):
        current = None
        @classmethod
        def now(cls, tz=None): return cls.current
    monkeypatch.setattr('zotero_arxiv_daily.retriever.arxiv_retriever.datetime', Clock)
    paper = SimpleNamespace(primary_category='cs.AI')
    def results(search):
        left,right = re.search(r'submittedDate:\[(\d+) TO (\d+)\]',search.query).groups()
        start = datetime.strptime(left,'%Y%m%d%H%M').replace(tzinfo=UTC)
        end = datetime.strptime(right,'%Y%m%d%H%M').replace(tzinfo=UTC)
        return iter([paper] if Clock.current >= public and start <= submitted <= end else [])
    monkeypatch.setattr('zotero_arxiv_daily.retriever.arxiv_retriever.arxiv.Client',lambda **kw: SimpleNamespace(results=results))
    with open_dict(config):
        config.source.arxiv.category = ['*']
        # Use the composed production default rather than a hard-coded test window.
        from hydra import compose, initialize_config_dir
        from pathlib import Path
        with initialize_config_dir(config_dir=str(Path(__file__).resolve().parents[1] / 'config'), version_base=None):
            config.source.arxiv.window_days = compose(config_name='all').source.arxiv.window_days
    found = []
    for day in range(25,30):
        Clock.current = datetime(2026,9,day,22,tzinfo=UTC)
        found.extend(ArxivRetriever(config)._retrieve_raw_papers())
    assert paper in found


def test_one_bad_crossref_record_preserves_other_records(config, monkeypatch):
    from contextlib import contextmanager
    from tests.test_journals import entry, response
    good = entry()
    bad = entry(doi='10.1021/bad',title='Invalid metadata')
    bad['author'] = None
    client = SimpleNamespace(get=lambda *a, **kw: response({'items':[good,bad]}))
    @contextmanager
    def session(*a): yield client
    monkeypatch.setattr('zotero_arxiv_daily.retriever.journal_retriever.session',session)
    journal = replace(CORE['jacs'],rss=None,issns=('0002-7863',))
    _, papers, failed = JournalRetriever(config)._journal(journal, datetime(2026,3,1,tzinfo=UTC),datetime(2026,3,5,tzinfo=UTC))
    assert [p.doi for p in papers] == ['10.1021/test']
    assert failed
