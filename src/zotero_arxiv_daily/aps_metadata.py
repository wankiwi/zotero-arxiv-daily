"""Public DOI-verified APS alternatives; indexed/manuscript provenance stays explicit."""
import json
import re
import time
from threading import Lock
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET
from loguru import logger

from .identity import paper_doi, canonical_doi, title_key
from .publisher_abstracts import MAX_BYTES

_LOCK = Lock()
_LAST = {}


def fetch(client, url, provider, blocked, params=None):
    if provider in blocked:
        return None
    interval = 3.0 if provider == 'arXiv' else 1.0
    with _LOCK:
        wait = interval - (time.monotonic() - _LAST.get(provider, float('-inf')))
        if wait > 0:
            time.sleep(wait)
        try:
            with client.get(url, params=params, timeout=(5, 20), allow_redirects=False, stream=True) as response:
                if response.status_code in (401, 403, 429):
                    blocked.add(provider)
                    logger.warning(f'{provider} metadata access blocked HTTP {response.status_code}; no retries')
                    return None
                if response.status_code == 404:
                    return None
                if 300 <= response.status_code < 400:
                    raise ValueError('Metadata redirects are not followed')
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('Metadata response too large')
                    chunks.append(chunk)
                return b''.join(chunks)
        finally:
            _LAST[provider] = time.monotonic()


def semantic_scholar(paper, client, blocked):
    from .abstracts import clean_abstract
    doi = paper_doi(paper)
    url = 'https://api.semanticscholar.org/graph/v1/paper/DOI:' + quote(doi, safe='')
    raw = fetch(client, url, 'Semantic Scholar', blocked,
                {'fields':'title,abstract,externalIds'})
    if raw is None:
        return None
    data = json.loads(raw)
    if (canonical_doi((data.get('externalIds') or {}).get('DOI')) != doi or
            title_key(data.get('title') or '') != title_key(paper.title)):
        return None
    text = clean_abstract(data.get('abstract'))
    if len(text) >= 40 and not text.rstrip().endswith(('…', '...')):
        return text, 'Semantic Scholar (indexed; version unverified)', url, 'recovered_indexed_abstract'
    return None


def arxiv_manuscript(paper, client, blocked):
    from .abstracts import clean_abstract
    # Title locates at most three candidates; it never establishes identity.
    query_title = ' '.join(paper.title.replace('"', ' ').split())[:300]
    url = 'https://export.arxiv.org/api/query'
    raw = fetch(client, url, 'arXiv', blocked,
                {'search_query':f'ti:"{query_title}"', 'max_results':3})
    if raw is None or b'<!DOCTYPE' in raw.upper():
        return None
    root = ET.fromstring(raw)
    ns = {'a':'http://www.w3.org/2005/Atom', 'x':'http://arxiv.org/schemas/atom'}
    for entry in root.findall('a:entry', ns):
        if canonical_doi(entry.findtext('x:doi', '', ns)) != paper_doi(paper):
            continue
        if title_key(entry.findtext('a:title', '', ns)) != title_key(paper.title):
            continue
        identity = urlsplit(entry.findtext('a:id', '', ns))
        version = re.fullmatch(r'/abs/(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)', identity.path)
        if identity.hostname != 'arxiv.org' or not version or identity.query or identity.fragment:
            continue
        text = clean_abstract(entry.findtext('a:summary', '', ns))
        if len(text) >= 40 and not text.rstrip().endswith(('…', '...')):
            return (text, f'arXiv {version[1]} (DOI-linked manuscript)',
                    'https://arxiv.org' + identity.path, 'recovered_doi_linked_manuscript')
    return None
