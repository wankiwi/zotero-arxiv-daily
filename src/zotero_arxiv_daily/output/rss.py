"""RSS 2.0 output with stable identifiers and a rolling recommendation history."""
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
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
        ET.SubElement(item, 'description').text = '\n'.join([
            f'Journal: {paper.journal or paper.source}', f'Authors: {", ".join(paper.authors)}',
            f'Published: {paper.published.isoformat() if paper.published else "Unknown"}',
            f'Relevance: {score} ({paper.scoring_basis})',
            f'TLDR: {paper.tldr or paper.abstract or "No abstract available"}',
            f'DOI: {paper.doi or "Unavailable"}', f'Article: {paper.url}'])
    path = Path(config.get('path', 'public/feed.xml'))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    ET.ElementTree(root).write(temporary, encoding='utf-8', xml_declaration=True)
    temporary.replace(path)
    return path, [load_paper(record['paper']) for record in records]
