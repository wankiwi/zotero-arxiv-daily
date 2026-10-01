"""Tests for zotero_arxiv_daily.construct_email: render_email, get_block_html."""

from zotero_arxiv_daily.construct_email import render_email, get_block_html
from tests.canned_responses import make_sample_paper


def test_render_email_with_papers():
    papers = [make_sample_paper(score=7.5, tldr="A great paper.", affiliations=["MIT"])]
    html = render_email(papers)
    assert "Sample Paper Title" in html
    assert "A great paper." in html
    assert "MIT" in html


def test_render_email_empty_list():
    html = render_email([])
    assert "No new recommendations" in html


def test_render_email_author_truncation():
    authors = [f"Author {i}" for i in range(10)]
    paper = make_sample_paper(authors=authors, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Author 0" in html
    assert "Author 1" in html
    assert "Author 2" in html
    assert "..." in html
    assert "Author 8" in html
    assert "Author 9" in html
    # Middle authors should be truncated
    assert "Author 5" not in html


def test_render_email_affiliation_truncation():
    affiliations = [f"Uni {i}" for i in range(8)]
    paper = make_sample_paper(affiliations=affiliations, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Uni 0" in html
    assert "Uni 4" in html
    assert "..." in html
    assert "Uni 7" not in html


def test_render_email_no_affiliations():
    paper = make_sample_paper(affiliations=None, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Unknown Affiliation" in html


def test_get_block_html_contains_all_fields():
    html = get_block_html("Title", "Auth", "3.5", "Summary", "http://pdf.url", "MIT")
    assert "Title" in html
    assert "Auth" in html
    assert "3.5" in html
    assert "Summary" in html
    assert "http://pdf.url" in html
    assert "MIT" in html


def test_numbering_scores_links_and_plain_text():
    from zotero_arxiv_daily.construct_email import email_plain_text
    papers = [make_sample_paper(title=f'Paper {i}', score=-0.15, doi=f'10.1000/example{i}',
                              tldr=None, abstract='原文 & abstract', tldr_status='not_generated') for i in range(50)]
    html = render_email(papers)
    plain = email_plain_text(html)
    for i in range(1, 51):
        assert f'{i}. Paper {i-1}' in html and f'{i}. Paper {i-1}' in plain
    assert 'Relevance: -0.1' in plain
    assert 'Original abstract (AI summary not generated)' in plain
    assert 'https://doi.org/10.1000/example0' in html and 'https://doi.org/10.1000/example0' in plain
    assert '<style' not in plain and 'font-size' not in plain
    assert '1.' not in email_plain_text(render_email([]))


def test_email_rejects_unsafe_links_and_escapes_untrusted_fields():
    from zotero_arxiv_daily.construct_email import email_plain_text
    paper = make_sample_paper(title='<img src=x onerror=alert(1)>', pdf_url='javascript:alert(1)',
                              url='https://[broken', score=None, authors=[], tldr='<b>text</b>',
                              tldr_status='generated', doi='10.1000/a" onclick="bad')
    html = render_email([paper])
    assert '<img' not in html and 'javascript:' not in html
    assert 'href="https://[broken' not in html
    assert 'Relevance: Unknown' in html and 'Authors unavailable' in html
    assert 'AI summary' in html and '&lt;b&gt;text&lt;/b&gt;' in html
    assert 'AI summary' in email_plain_text(html)


def test_email_inline_font_coverage():
    from html.parser import HTMLParser

    class FontCheck(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag in {'body', 'table', 'td', 'p', 'h1', 'h2', 'a', 'span'}:
                style = dict(attrs).get('style', '')
                assert 'font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;' in style, tag

    paper = make_sample_paper(doi='10.1000/example', score=7.5)
    for html in [render_email([paper]), render_email([])]:
        FontCheck().feed(html)
        assert '@font-face' not in html
