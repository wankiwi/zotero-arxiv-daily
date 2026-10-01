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
    assert '<script>' not in first.findtext('./channel/item/description')
    assert '&lt;script&gt;' in first.findtext('./channel/item/description')
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


def test_historical_rss_covers_are_filtered_before_limit_without_history_mutation(tmp_path):
    state = State(tmp_path / 'state.json')
    research = make_sample_paper(title='Front cover surfaces control transport', url='https://example.org/research')
    cover = make_sample_paper(title='Outside Front Cover: Materials', url='https://example.org/cover')
    state.add([research, cover])
    state.save()
    before = state.path.read_bytes()
    path, emitted = write_rss(state, {'path':str(tmp_path/'feed.xml'), 'max_items':1})
    assert [p.title for p in emitted] == [research.title]
    assert ET.parse(path).findtext('./channel/item/title') == research.title
    assert cover.title not in path.with_name('index.html').read_text()
    assert state.path.read_bytes() == before and len(state.records) == 2


def test_direct_rss_page_uses_same_cover_filter(tmp_path):
    from zotero_arxiv_daily.output.rss import write_rss_page
    cover = make_sample_paper(title='Inside Back Cover: Image')
    research = make_sample_paper(title='Cover times in random walks')
    write_rss_page(tmp_path/'feed.xml', [cover,research])
    page = (tmp_path/'index.html').read_text()
    assert cover.title not in page and research.title in page


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


def test_query_identity_preserves_article_ids_and_ignores_only_tracking(tmp_path):
    first = make_sample_paper(title='First', doi=None, url='https://example.org/article?id=1&utm_source=rss#top')
    repeated = make_sample_paper(title='First renamed', doi=None, url='https://example.org/article?id=1')
    second = make_sample_paper(title='Second', doi=None, url='https://example.org/article?id=2')
    assert paper_id(first) == paper_id(repeated)
    state = State(tmp_path / 'state.json')
    state.add([first])
    assert state.has(repeated) and not state.has(second)


def test_arxiv_query_and_fragment_do_not_break_version_identity():
    assert paper_id(make_sample_paper(url='https://arxiv.org/abs/2401.01234v3?context=cs#top')) == 'arxiv:2401.01234'
    assert not paper_id(make_sample_paper(url='https://notarxiv.org/abs/2401.01234')).startswith('arxiv:')


def test_old_state_keys_migrate_without_repeating_or_breaking_pending_delivery(tmp_path):
    from zotero_arxiv_daily.state import paper_dict
    first = make_sample_paper(title='First', doi=None, url='https://example.org/article?id=1')
    second = make_sample_paper(title='Second', doi=None, url='https://doi.org/10.1021/second')
    path = tmp_path / 'state.json'
    path.write_text(json.dumps({'version': 1, 'records': {
        'https://example.org/article': {'added': datetime.now(timezone.utc).isoformat(), 'paper': paper_dict(first), 'channels': {'email': True}},
        'https://doi.org/10.1021/second': {'added': datetime.now(timezone.utc).isoformat(), 'paper': paper_dict(second), 'channels': {'rss': True}},
    }}))
    state = State(path)
    assert state.has(first) and state.has(second)
    assert [p.title for p in state.pending('email')] == ['Second']
    state.mark(state.pending('email'), 'email')
    state.save()
    assert not State(path).pending('email')
    assert [p.title for p in State(path).pending('rss')] == ['First']


def test_doi_links_protect_identical_titles_from_false_deduplication(tmp_path):
    first = make_sample_paper(title='Introduction', doi=None, url='https://doi.org/10.1021/one')
    second = make_sample_paper(title='Introduction', doi='10.1021/two', url='https://doi.org/10.1021/two')
    assert len(deduplicate([first, second])) == 2
    state = State(tmp_path / 'state.json')
    state.add([first])
    state.save()
    assert not State(state.path).has(second)


def test_rss_page_displays_saved_scores_without_changing_delivery_state(tmp_path):
    state = State(tmp_path / 'state.json')
    papers = [make_sample_paper(title='A <script> & B', doi='10.1000/scored',
                               url='https://example.org/paper?a=1&b=2', score=7.125,
                               abstract='<script>private script</script>'),
              make_sample_paper(title='Unknown score', doi='10.1000/unknown',
                                url='javascript:alert(1)', score=None)]
    state.add(papers)
    state.mark(papers, 'email')
    state.mark(papers, 'rss')
    before = json.dumps(state.records, sort_keys=True)
    state.save()
    restored = State(state.path)
    path, _ = write_rss(restored, {'path': str(tmp_path / 'feed.xml')})
    html = path.with_name('index.html').read_text()
    assert 'Relevance: <strong>7.12</strong>' in html
    assert 'Relevance: <strong>Unknown</strong>' in html
    assert 'A &lt;script&gt; &amp; B' in html and '<script>' not in html
    assert 'href="https://example.org/paper?a=1&amp;b=2"' in html
    assert 'javascript:' not in html and 'href="feed.xml"' in html
    assert any(
        'Relevance: 7.12' in i.findtext('description') for i in ET.parse(path).findall('./channel/item'))
    assert json.dumps(restored.records, sort_keys=True) == before
    assert not restored.pending('email') and not restored.pending('rss')
