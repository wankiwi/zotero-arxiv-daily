"""Table-based email cards inspired by TideDra/zotero-arxiv-daily."""
from .protocol import Paper
import math
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
    return (f'<a href="{escape(url, quote=True)}" style="display:inline-block;margin:4px 8px 4px 0;'
            f'padding:10px 16px;border-radius:5px;background:{background};color:{color};'
            f'font-size:14px;font-weight:bold;text-decoration:none;">{escape(label)}</a>')


framework = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Papers</title></head>
<body style="margin:0;padding:0;background:#eef1f4;color:#253244;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#eef1f4"><tr><td align="center" style="padding:24px 12px;">
<!--[if mso]><table role="presentation" width="680"><tr><td><![endif]-->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:680px;table-layout:fixed;">
<tr><td style="padding:24px;background:#24364b;border-top:4px solid #b8423c;color:#ffffff;">
<p style="margin:0 0 8px;font-size:11px;letter-spacing:2px;">ZOTERO · RESEARCH DIGEST</p>
<h1 style="margin:0;font-size:28px;line-height:1.2;">Daily Papers</h1>
<p style="margin:12px 0 0;font-size:14px;line-height:1.6;color:#dce5ee;">__COUNT__ · Ranked by relevance to your Zotero library</p></td></tr>
<tr><td style="padding:16px 4px;font-size:12px;line-height:1.6;color:#526174;">Relevance uses text similarity weighted by when papers were added to your library. Higher scores mean a closer match, not a probability. Scores are shown to one decimal place.</td></tr>
__CONTENT__
<tr><td style="padding:16px 4px;font-size:12px;line-height:1.7;color:#526174;">Based on Zotero-arXiv-Daily. To stop delivery, disable the scheduled workflow in GitHub Actions.</td></tr>
</table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''


def get_empty_html():
    return '<p style="margin:0;font-size:20px;font-weight:bold;">No new recommendations</p><p style="line-height:1.7;">No new papers were selected after filtering and deduplication. Take a rest!</p>'


def get_block_html(title, authors, rate, tldr, pdf_url, affiliations=None, summary_label='AI summary',
                   *, number=None, metadata='', basis='abstract', article_url=None, doi=None):
    ordinal = f'{number}. ' if number is not None else ''
    heading = escape(ordinal + title)
    details = escape(metadata)
    buttons = link('PDF', pdf_url, True) + link('Article', article_url, not safe_url(pdf_url))
    doi_url = 'https://doi.org/' + quote(doi, safe='/') if doi else None
    doi_line = (f'<p style="margin:10px 0 0;font-size:12px;overflow-wrap:anywhere;word-break:break-word;">'
                f'<a style="color:#526174;" href="{escape(doi_url, quote=True)}">DOI: {escape(doi)}</a></p>') if doi else ''
    return f'''<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="table-layout:fixed;border:1px solid #dbe1e8;border-radius:8px;">
<tr><td style="padding:22px;overflow-wrap:anywhere;word-break:break-word;">
<p style="margin:0 0 10px;color:#687589;font-size:12px;line-height:1.6;">{details}</p>
<h2 style="margin:0 0 12px;color:#253244;font-size:20px;line-height:1.4;">{heading}</h2>
<p style="margin:0 0 5px;color:#526174;font-size:13px;line-height:1.7;">{escape(authors or 'Authors unavailable')}</p>
<p style="margin:0 0 16px;color:#687589;font-size:12px;line-height:1.6;">{escape(affiliations or 'Unknown Affiliation')}</p>
<p style="margin:0 0 18px;font-size:14px;line-height:1.7;color:#8e302c;"><strong>Relevance: {escape(str(rate))}</strong><br><span style="color:#687589;font-size:12px;">Scored using {escape(basis)} similarity to your library.</span></p>
<p style="margin:0 0 8px;font-size:12px;font-weight:bold;color:#526174;">{escape(summary_label)}</p>
<p style="margin:0 0 18px;font-size:15px;line-height:1.8;color:#334155;">{escape(tldr or 'No abstract available').replace(chr(10), '<br>')}</p>
<p style="margin:0;">{buttons}</p>{doi_line}
</td></tr></table>'''


def render_email(papers: list[Paper]) -> str:
    parts = []
    for number, p in enumerate(papers, 1):
        authors = p.authors if len(p.authors) <= 5 else p.authors[:3] + ['...'] + p.authors[-2:]
        affiliations = ', '.join(p.affiliations[:5]) if p.affiliations else None
        if p.affiliations and len(p.affiliations) > 5:
            affiliations += ', ...'
        date = p.published.strftime('%Y-%m-%d') if p.published else 'Date unavailable'
        metadata = f'{p.journal or p.source} · {date} · Source: {p.source}'
        block = get_block_html(p.title, ', '.join(authors), round(p.score, 1) if p.score is not None else 'Unknown',
                               p.summary_text, p.pdf_url, affiliations, p.summary_label, number=number,
                               metadata=metadata, basis=p.scoring_basis, article_url=p.url, doi=p.doi)
        parts.append('<tr><td style="padding:0 0 16px;">' + block + '</td></tr>')
    if not parts:
        parts.append('<tr><td style="padding:24px;background:#ffffff;">' + get_empty_html() + '</td></tr>')
    count = f'{len(papers)} recommendation' + ('' if len(papers) == 1 else 's')
    return framework.replace('__COUNT__', count).replace('__CONTENT__', ''.join(parts))


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


def get_stars(score:float):
    full_star = '<span class="full-star">⭐</span>'
    half_star = '<span class="half-star">⭐</span>'
    low = 6
    high = 8
    if score <= low:
        return ''
    elif score >= high:
        return full_star * 5
    else:
        interval = (high-low) / 10
        star_num = math.ceil((score-low) / interval)
        full_star_num = int(star_num/2)
        half_star_num = star_num - full_star_num * 2
        return '<div class="star-wrapper">'+full_star * full_star_num + half_star * half_star_num + '</div>'
