"""RSS 2.0 output with stable identifiers and a rolling recommendation history."""
from datetime import datetime, timedelta
from email.utils import format_datetime
from html import escape
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

from ..identity import paper_id
from ..state import load_paper, utcnow


def write_rss(state, config):
    days, limit = int(config.get('retention_days', 30)), int(config.get('max_items', 300))
    if days < 1 or limit < 1:
        raise ValueError('RSS retention_days and max_items must be positive')
    cutoff = utcnow() - timedelta(days=days)
    records = sorted((v for v in state.records.values() if datetime.fromisoformat(v['added']) >= cutoff),
                     key=lambda v: v['added'], reverse=True)[:limit]
    root = ET.Element('rss', version='2.0')
    channel = ET.SubElement(root, 'channel')
    for key, value in {'title': config.get('title', 'Daily Paper Recommendations'),
                       'link': config.get('site_url', ''),
                       'description': 'Papers selected using your Zotero research interests',
                       'lastBuildDate': format_datetime(utcnow())}.items():
        ET.SubElement(channel, key).text = value
    for record in records:
        paper = load_paper(record['paper'])
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = paper.title
        ET.SubElement(item, 'link').text = paper.url
        ET.SubElement(item, 'guid', isPermaLink='false').text = paper_id(paper)
        # Use discovery date when publisher only supplies incomplete date metadata.
        published = paper.published or datetime.fromisoformat(record['added'])
        ET.SubElement(item, 'pubDate').text = format_datetime(published)
        ET.SubElement(item, 'category').text = paper.journal or paper.source
        score = f'{paper.score:.2f}' if paper.score is not None else 'Unknown'
        # RSS descriptions are HTML after XML decoding; escape third-party text
        # at the HTML layer as well as letting ElementTree escape the XML layer.
        ET.SubElement(item, 'description').text = '<br/>'.join(escape(line) for line in [
            f'Journal: {paper.journal or paper.source}', f'Authors: {", ".join(paper.authors)}',
            f'Published: {paper.published.isoformat() if paper.published else "Unknown"}',
            f'Relevance: {score} ({paper.scoring_basis})',
            f'{paper.summary_label}: {paper.summary_text}',
            f'DOI: {paper.doi or "Unavailable"}', f'Article: {paper.url}'])
    path = Path(config.get('path', 'public/feed.xml'))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    ET.ElementTree(root).write(temporary, encoding='utf-8', xml_declaration=True)
    temporary.replace(path)
    papers = [load_paper(record['paper']) for record in records]
    write_rss_page(path, papers)
    return path, papers


def write_rss_page(feed_path, papers):
    """Public, static view of the same recommendations included in the feed."""
    articles = []
    for paper in papers:
        score = f'{paper.score:.2f}' if paper.score is not None else 'Unknown'
        try:
            url = paper.url if urlsplit(paper.url).scheme in ('https', 'http') else '#'
        except ValueError:
            url = '#'
        articles.append(
            '<article><h2><a href="' + escape(url, quote=True) + '">' + escape(paper.title) + '</a></h2>'
            '<p class="score">相关性分数 / Relevance: <strong>' + score + '</strong></p>'
            '<p>' + escape(paper.journal or paper.source) + ' · ' + escape(paper.scoring_basis) + '</p>'
            '<p>' + escape(', '.join(paper.authors)) + '</p>'
            '<p><strong>' + escape(paper.summary_label) + '</strong></p>'
            '<p class="summary">' + escape(paper.summary_text) + '</p></article>')
    html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Paper recommendations</title>'
            '<style>body{max-width:900px;margin:2rem auto;padding:0 1rem;font-family:system-ui;line-height:1.6}'
            'article{border-top:1px solid #ccc;padding:1rem 0}h2{font-size:1.2rem}'
            '.score{font-size:1.1rem}.summary{white-space:pre-wrap}a{color:#145da0}</style>'
            '</head><body><h1>论文推荐 / Paper recommendations</h1>'
            '<p><a href="' + escape(quote(feed_path.name), quote=True) + '">订阅 RSS / Subscribe</a></p>'
            '<p>相关性分数基于文库相似度与时间权重，不是概率。分数越高，相关性越强。'
            '缺少摘要时使用标题评分。</p>'
            + ''.join(articles) + '</body></html>')
    path = feed_path.with_name('index.html')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(html, encoding='utf-8')
    temporary.replace(path)
