"""Stable identifiers shared by retrievers, deduplication and output channels."""
import hashlib
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = unquote(value).strip()
    if value.startswith(('https://', 'http://')):
        # Tracking query strings and fragments are not part of a DOI.
        value = urlsplit(value).path
    match = re.search(r'10\.\d{4,9}/[^\s<>"\']+', value, re.I)
    return match.group(0).rstrip('.,;').lower() if match else None


def title_key(title: str) -> str:
    return ' '.join(re.findall(r'\w+', title.casefold()))


def canonical_doi(value):
    doi = normalize_doi(value)
    # Research Square versions share one recommendation identity.
    if doi and doi.startswith('10.21203/rs.'):
        doi = re.sub(r'/v\d+$', '', doi)
    return doi


def paper_doi(paper):
    return canonical_doi(paper.doi) or canonical_doi(paper.url)


def paper_id(paper) -> str:
    doi = paper_doi(paper)
    if doi:
        return 'doi:' + doi
    url = paper.url.strip()
    parts = urlsplit(url)
    if parts.hostname in ('arxiv.org', 'www.arxiv.org', 'export.arxiv.org'):
        match = re.fullmatch(r'/(?:abs|pdf|html)/(.+?)(?:v\d+)?(?:\.pdf)?', parts.path)
        if match:
            return 'arxiv:' + match.group(1)
    if url:
        tracking = {'fbclid', 'gclid', 'mc_cid', 'mc_eid'}
        query = urlencode([(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                           if not key.lower().startswith('utm_') and key.lower() not in tracking])
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), query, ''))
    return 'title:' + hashlib.sha256(title_key(paper.title).encode()).hexdigest()


def deduplicate(papers):
    """Merge matching DOI/URL identities and conservative normalized titles."""
    result, keys, titles = [], {}, {}
    for paper in papers:
        key, title = paper_id(paper), title_key(paper.title)
        previous = keys.get(key)
        if previous is None and title:
            match = titles.get(title)
            # Different DOIs can legitimately share a generic article title.
            if match is not None and (not paper_doi(match) or not paper_doi(paper) or paper_doi(match) == paper_doi(paper)):
                previous = match
        if previous is not None:
            # Prefer the published record while preserving useful preprint text.
            if paper.journal and not previous.journal:
                for field in ('doi', 'journal', 'issns', 'published', 'url', 'source', 'publication_kind', 'publication_status', 'publication_venue'):
                    setattr(previous, field, getattr(paper, field))
            for field in ('abstract', 'full_text', 'pdf_url', 'doi'):
                if not getattr(previous, field) and getattr(paper, field):
                    setattr(previous, field, getattr(paper, field))
                    if field == 'abstract':
                        previous.abstract_source = paper.abstract_source
            keys[key] = previous
            keys[paper_id(previous)] = previous
        else:
            result.append(paper)
            keys[key] = paper
            if title:
                titles[title] = paper
    return result
