"""Table-based email cards inspired by TideDra/zotero-arxiv-daily."""
from .protocol import Paper
from .selection import GROUPS, paper_group
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit, quote


def safe_url(value):
    try:
        parsed = urlsplit(value or '')
        return value if parsed.scheme in ('http', 'https') and parsed.netloc else ''
    except ValueError:
        return ''


def link(label, url, primary=False):
    url = safe_url(url)
    if not url:
        return ''
    color, background = ('#ffffff', '#b8423c') if primary else ('#334155', '#edf1f5')
    return (f'<a href="{escape(url, quote=True)}" style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;display:inline-block;margin:4px 8px 4px 0;'
            f'padding:10px 16px;border-radius:5px;background:{background};color:{color};'
            f'font-size:14px;font-weight:bold;text-decoration:none;">{escape(label)}</a>')


framework = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Papers</title><style>@media only screen and (max-width:720px){.digest-column{display:block!important;width:100%!important;box-sizing:border-box!important;padding:0 0 18px!important;}}</style></head>
<body style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0;padding:0;background:#eef1f4;color:#253244;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#eef1f4" style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;table-layout:fixed;"><tr><td align="center" style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;padding:24px 12px;">
<!--[if mso]><table role="presentation" width="1200" style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;"><tr><td style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;"><![endif]-->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;max-width:1200px;table-layout:fixed;">
<tr><td style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;padding:24px;background:#24364b;border-top:4px solid #b8423c;color:#ffffff;">
<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 8px;font-size:11px;letter-spacing:2px;">ZOTERO · RESEARCH DIGEST</p>
<h1 style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0;font-size:28px;line-height:1.2;">Daily Papers</h1>
<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:12px 0 0;font-size:14px;line-height:1.6;color:#dce5ee;">__COUNT__ · Journal and preprint selections ranked by relevance; random picks sampled from remaining eligible papers</p></td></tr>
<tr><td style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;padding:16px 4px;font-size:12px;line-height:1.6;color:#526174;">Relevance uses text similarity weighted by when papers were added to your library. Higher scores mean a closer match, not a probability. Scores are shown to one decimal place.</td></tr>
__CONTENT__
<tr><td style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;padding:16px 4px;font-size:12px;line-height:1.7;color:#526174;">Based on Zotero-arXiv-Daily. To stop delivery, disable the scheduled workflow in GitHub Actions.</td></tr>
</table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''


def get_empty_html():
    return '<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0;font-size:20px;font-weight:bold;">No new recommendations</p><p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;line-height:1.7;">No new papers were selected after filtering and deduplication. Take a rest!</p>'


def get_block_html(title, authors, rate, tldr, pdf_url, affiliations=None, summary_label='AI summary',
                   *, number=None, metadata='', basis='abstract', article_url=None, doi=None, original_abstract=None):
    ordinal = f'{number}. ' if number is not None else ''
    heading = escape(ordinal + title)
    details = escape(metadata)
    buttons = link('PDF', pdf_url, True) + link('Article', article_url, not safe_url(pdf_url))
    doi_url = 'https://doi.org/' + quote(doi, safe='/') if doi else None
    doi_line = (f'<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:10px 0 0;font-size:12px;overflow-wrap:anywhere;word-break:break-word;">'
                f'<a style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;color:#526174;" href="{escape(doi_url, quote=True)}">DOI: {escape(doi)}</a></p>') if doi else ''
    original = ''
    if original_abstract is not None and (summary_label == 'AI summary' or summary_label.startswith('Summary (legacy')):
        original = '<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:16px 0 8px;font-size:12px;font-weight:bold;">Original abstract / 原摘要</p><p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 18px;font-size:15px;line-height:1.8;">' + escape(original_abstract or 'No abstract available').replace(chr(10), '<br>') + '</p>'
    return f'''<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;table-layout:fixed;border:1px solid #dbe1e8;border-radius:8px;">
<tr><td style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;padding:16px;overflow-wrap:anywhere;word-break:break-word;">
<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 10px;color:#687589;font-size:12px;line-height:1.6;">{details}</p>
<h2 style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 12px;color:#253244;font-size:18px;line-height:1.4;">{heading}</h2>
<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 5px;color:#526174;font-size:13px;line-height:1.7;">{escape(authors or 'Authors unavailable')}</p>
<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 16px;color:#687589;font-size:12px;line-height:1.6;">{escape(affiliations or 'Unknown Affiliation')}</p>
<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 18px;font-size:14px;line-height:1.7;color:#8e302c;"><strong>Relevance: {escape(str(rate))}</strong><br><span style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;color:#687589;font-size:12px;">Scored using {escape(basis)} similarity to your library.</span></p>
<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 8px;font-size:12px;font-weight:bold;color:#526174;">{escape(summary_label)}</p>
<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 18px;font-size:15px;line-height:1.8;color:#334155;">{escape(tldr or 'No abstract available').replace(chr(10), '<br>')}</p>
{original}<p style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0;">{buttons}</p>{doi_line}
</td></tr></table>'''


def render_email(papers: list[Paper]) -> str:
    labels = {'journals': '期刊 / Journals', 'preprints': '预印本 / Preprints（含会议论文）', 'random': '随机推荐 / Random'}
    columns = []
    for group in GROUPS:
        selected = [p for p in papers if paper_group(p) == group]
        parts = []
        for number, p in enumerate(selected, 1):
            authors = p.authors if len(p.authors) <= 5 else p.authors[:3] + ['...'] + p.authors[-2:]
            affiliations = ', '.join(p.affiliations[:5]) if p.affiliations else None
            if p.affiliations and len(p.affiliations) > 5:
                affiliations += ', ...'
            date = p.published.strftime('%Y-%m-%d') if p.published else 'Date unavailable'
            metadata = f'{p.journal or p.source} · {date} · Source: {p.source}'
            if p.publication_kind:
                metadata += f' · Type: {p.publication_kind} / {p.publication_status or "unverified"}'
            if group == 'random':
                metadata += ' · Random selection'
            if p.summary_input_source:
                metadata += f' · Summary input: {p.summary_input_source}'
                if p.summary_input_fallback:
                    metadata += f' ({p.summary_input_fallback})'
            if p.abstract_source:
                metadata += f' · Abstract: {p.abstract_source}'
            block = get_block_html(p.title, ', '.join(authors), round(p.score, 1) if p.score is not None else 'Unknown',
                                   p.summary_text, p.pdf_url, affiliations, p.summary_label, number=number,
                                   metadata=metadata, basis=p.scoring_basis, article_url=p.url, doi=p.doi, original_abstract=p.abstract)
            parts.append('<tr><td style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;padding:0 0 16px;">' + block + '</td></tr>')
        if not parts:
            parts.append('<tr><td style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;padding:16px;background:#ffffff;">No new recommendations in this group.</td></tr>')
        heading = f'<h2 style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;margin:0 0 14px;font-size:18px;color:#24364b;">{labels[group]} ({len(selected)})</h2>'
        columns.append('<td class="digest-column" width="33.33%" valign="top" style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;width:33.33%;padding:0 6px;">' + heading +
                       '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;table-layout:fixed;">' + ''.join(parts) + '</table></td>')
    content = '<tr><td style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="font-family:Aptos,Calibri,Arial,Helvetica,sans-serif;table-layout:fixed;"><tr>' + ''.join(columns) + '</tr></table></td></tr>'
    count = f'{len(papers)} recommendation' + ('' if len(papers) == 1 else 's')
    return framework.replace('__COUNT__', count).replace('__CONTENT__', content)


class _PlainEmail(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden, self.href = [], 0, ''

    def handle_starttag(self, tag, attrs):
        if tag in ('head', 'script', 'style'):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ('p', 'h1', 'h2', 'tr', 'br'):
            self.parts.append('\n')
        if tag == 'a':
            self.href = safe_url(dict(attrs).get('href'))

    def handle_endtag(self, tag):
        if tag in ('head', 'script', 'style'):
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag == 'a' and self.href:
            self.parts.append(f' ({self.href})')
            self.href = ''
        if tag in ('p', 'h1', 'h2', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def email_plain_text(html):
    parser = _PlainEmail()
    parser.feed(html)
    return '\n\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
