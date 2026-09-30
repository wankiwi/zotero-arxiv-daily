"""Stable identifiers shared by retrievers, deduplication and output channels."""
import hashlib
import re
from urllib.parse import unquote, urlsplit, urlunsplit


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


def paper_id(paper) -> str:
    doi = normalize_doi(paper.doi)
    if doi:
        # Research Square versions share one recommendation identity.
        if doi.startswith('10.21203/rs.'):
            doi = re.sub(r'/v\d+$', '', doi)
        return 'doi:' + doi
    url = paper.url.strip()
    if 'arxiv.org/' in url:
        match = re.search(r'/(?:abs|pdf|html)/(.+?)(?:v\d+)?(?:\.pdf)?$', url)
        if match:
            return 'arxiv:' + match.group(1)
    if url:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), '', ''))
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
            if match is not None and (not normalize_doi(match.doi) or not normalize_doi(paper.doi) or normalize_doi(match.doi) == normalize_doi(paper.doi)):
                previous = match
        if previous is not None:
            # Prefer the published record while preserving useful preprint text.
            if paper.journal and not previous.journal:
                for field in ('doi', 'journal', 'issns', 'published', 'url', 'source'):
                    setattr(previous, field, getattr(paper, field))
            for field in ('abstract', 'full_text', 'pdf_url', 'doi'):
                if not getattr(previous, field) and getattr(paper, field):
                    setattr(previous, field, getattr(paper, field))
            keys[key] = previous
            keys[paper_id(previous)] = previous
        else:
            result.append(paper)
            keys[key] = paper
            if title:
                titles[title] = paper
    return result
