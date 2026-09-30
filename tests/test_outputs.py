from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET
import json
import pytest

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.identity import deduplicate, normalize_doi, paper_id
from zotero_arxiv_daily.state import State
from zotero_arxiv_daily.output.rss import write_rss
from zotero_arxiv_daily.construct_email import render_email


def test_doi_url_normalization_and_arxiv_versions():
    assert normalize_doi('https://doi.org/10.1021/ABC') == '10.1021/abc'
    assert paper_id(make_sample_paper(url='https://arxiv.org/abs/2401.01234v2')) == 'arxiv:2401.01234'
    assert paper_id(make_sample_paper(url='https://arxiv.org/pdf/2401.01234v1.pdf')) == 'arxiv:2401.01234'


def test_cross_source_deduplication_preserves_abstract_and_published_link():
    preprint = make_sample_paper(doi='10.1021/Test')
    journal = make_sample_paper(source='journals', journal='JACS', doi='https://doi.org/10.1021/test', abstract='', full_text=None, url='https://doi.org/10.1021/test')
    merged = deduplicate([preprint, journal])
    assert len(merged) == 1
    assert merged[0].journal == 'JACS' and merged[0].abstract
    assert merged[0].url == journal.url


def test_history_roundtrip_channels_and_atomic_write(tmp_path):
    path = tmp_path / 'state.json'
    state = State(path)
    paper = make_sample_paper(doi='10.1021/one', score=7.5)
    state.add([paper])
    state.mark([paper], 'rss')
    state.save()
    restored = State(path)
    assert restored.has(paper)
    assert not restored.pending('rss')
    assert len(restored.pending('email')) == 1
    assert 'full_text' not in json.loads(path.read_text())['records'][paper_id(paper)]['paper']
    assert not path.with_suffix('.tmp').exists()


def test_rss_stable_guid_escape_and_history_without_new_results(tmp_path):
    state = State(tmp_path / 'state.json')
    paper = make_sample_paper(title='A & B < C', doi='10.1021/a', journal='JACS', score=8, tldr='A <script> & B')
    state.add([paper])
    config = {'path': str(tmp_path / 'feed.xml'), 'site_url': 'https://example.org'}
    write_rss(state, config)
    first = ET.parse(config['path'])
    guid = first.findtext('./channel/item/guid')
    assert first.findtext('./channel/item/title') == paper.title
    assert '<script>' in first.findtext('./channel/item/description')
    write_rss(state, config)
    second = ET.parse(config['path'])
    assert second.findtext('./channel/item/guid') == guid
    assert len(second.findall('./channel/item')) == 1


def test_rss_retention_and_max_items(tmp_path):
    state = State(tmp_path / 'state.json')
    papers = [make_sample_paper(title=f'P{i}', url=f'https://example.org/{i}') for i in range(4)]
    state.add(papers)
    state.records[paper_id(papers[0])]['added'] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    config = {'path': str(tmp_path / 'feed.xml'), 'retention_days': 30, 'max_items': 2}
    write_rss(state, config)
    items = ET.parse(config['path']).findall('./channel/item')
    assert len(items) == 2 and all(i.findtext('title') != 'P0' for i in items)


def test_corrupt_state_is_not_silently_overwritten(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text('{invalid')
    with pytest.raises(ValueError):
        State(path)
    assert path.read_text() == '{invalid'


def test_email_escapes_content_and_links_to_article_without_pdf():
    paper = make_sample_paper(title='<script>evil</script>', abstract='A & B', tldr=None, pdf_url=None, journal='JACS')
    html = render_email([paper])
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '>Article</a>' in html and paper.url in html and 'JACS' in html


def test_identical_titles_with_distinct_dois_are_not_merged(tmp_path):
    a = make_sample_paper(title='Introduction', doi='10.1021/one')
    b = make_sample_paper(title='Introduction', doi='10.1021/two')
    assert len(deduplicate([a, b])) == 2
    state = State(tmp_path / 'state.json')
    state.add([a])
    assert not state.has(b)


def test_doi_tracking_parameters_do_not_change_identity():
    assert normalize_doi('https://doi.org/10.1021/ABC?utm_source=email#section') == '10.1021/abc'
