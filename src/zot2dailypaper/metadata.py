"""Recover paper authors/affiliations from public, exact DOI/title metadata only."""
import json
from time import monotonic
from urllib.parse import quote

from loguru import logger

from .abstracts import clean_abstract, DeadlineClient, RecoveryContext
from .aps_metadata import fetch
from .http import abstract_session
from .identity import normalize_doi, title_key


def names(values):
    if not isinstance(values, (list, tuple)):
        return []
    return list(dict.fromkeys(text for value in values if isinstance(value, str)
                             and (text := clean_abstract(value))))


def crossref_metadata(item):
    authors, affiliations = [], []
    people = item.get('author', [])
    if not isinstance(people, list):
        raise ValueError('Crossref author field must be a list')
    for person in people:
        if not isinstance(person, dict):
            raise ValueError('Crossref author must be an object')
        author = person.get('name') or ' '.join(v for k in ('given', 'family')
                                               if isinstance(v := person.get(k), str))
        authors.append(author)
        deposited = person.get('affiliation', [])
        if not isinstance(deposited, list):
            raise ValueError('Crossref affiliation field must be a list')
        for affiliation in deposited:
            if isinstance(affiliation, dict):
                affiliations.append(affiliation.get('name'))
    return names(authors), names(affiliations)


def openalex_metadata(item):
    authors, affiliations = [], []
    for authorship in item.get('authorships') or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get('author') or {}
        if isinstance(author, dict):
            authors.append(author.get('display_name'))
        # Affiliations belong to this work; never consult current author profiles.
        raw = names(authorship.get('raw_affiliation_strings'))
        affiliations.extend(raw or [institution.get('display_name')
            for institution in authorship.get('institutions') or [] if isinstance(institution, dict)])
    return names(authors), names(affiliations)


def merge_metadata(paper, authors, affiliations, provider, url):
    for field, values in (('authors', authors), ('affiliations', affiliations)):
        if not getattr(paper, field) and values:
            setattr(paper, field, values)
            setattr(paper, field + '_status', 'provided')
            setattr(paper, field + '_source', provider)
            setattr(paper, field + '_source_url', url)


def record(paper, provider, status):
    entry = {'provider': provider, 'status': status}
    if entry not in paper.metadata_recovery_attempts:
        paper.metadata_recovery_attempts.append(entry)


def recover_metadata(papers, config, context=None):
    if not config.get('enabled', True):
        return
    limit, seconds = config.get('max_papers', 50), config.get('max_seconds', 90)
    if type(limit) is not int or not 0 <= limit <= 100:
        raise ValueError('metadata.max_papers must be an integer from 0 to 100')
    if type(seconds) is not int or not 1 <= seconds <= 600:
        raise ValueError('metadata.max_seconds must be an integer from 1 to 600')
    context = RecoveryContext(blocked=context.blocked if context else set(), deadline=monotonic() + seconds)
    missing = []
    for paper in papers:
        for field in ('authors', 'affiliations'):
            if getattr(paper, field):
                setattr(paper, field + '_status', 'provided')
        if paper.authors and paper.affiliations:
            continue
        if paper.source == 'openreview':
            # Conversion has already applied note/field ACLs. Never deanonymize
            # via title searches, profiles, PDFs or unauthenticated alternate routes.
            continue
        if not normalize_doi(paper.doi or paper.url):
            for field in ('authors', 'affiliations'):
                if not getattr(paper, field):
                    setattr(paper, field + '_status', 'doi_unavailable')
            continue
        missing.append(paper)
    for paper in missing[limit:]:
        record(paper, 'Public metadata', 'lookup_limit')
    with abstract_session(config.get('mailto')) as transport:
        client = DeadlineClient(transport, context)
        for paper in missing[:limit]:
            if context.expired():
                record(paper, 'Public metadata', 'time_limit')
                continue
            doi = normalize_doi(paper.doi or paper.url)
            for provider, url, parser in (
                ('Crossref', 'https://api.crossref.org/works/' + quote(doi, safe=''), crossref_metadata),
                ('OpenAlex', 'https://api.openalex.org/works/https://doi.org/' + quote(doi, safe=''), openalex_metadata),
            ):
                if context.expired():
                    record(paper, provider, 'time_limit')
                    continue
                try:
                    raw = fetch(client, url, provider, context.blocked)
                    if raw is None:
                        record(paper, provider, 'access_blocked' if provider in context.blocked else 'not_found')
                        continue
                    data = json.loads(raw)
                    item = data.get('message', {}) if provider == 'Crossref' else data
                    found_doi = item.get('DOI') if provider == 'Crossref' else item.get('doi')
                    if provider == 'Crossref':
                        titles = item.get('title') or []
                        found_title = titles[0] if isinstance(titles, list) and titles else ''
                    else:
                        found_title = item.get('title') or item.get('display_name') or ''
                    if normalize_doi(found_doi) != doi or title_key(clean_abstract(found_title)) != title_key(paper.title):
                        record(paper, provider, 'identity_mismatch')
                        continue
                    authors, affiliations = parser(item)
                    before = (bool(paper.authors), bool(paper.affiliations))
                    merge_metadata(paper, authors, affiliations, provider, url)
                    record(paper, provider, 'recovered' if before != (bool(paper.authors), bool(paper.affiliations)) else 'metadata_absent')
                    if paper.authors and paper.affiliations:
                        break
                except Exception as exc:
                    record(paper, provider, 'request_failed_' + type(exc).__name__)
                    logger.warning(f'{provider} author/affiliation lookup failed ({type(exc).__name__}); metadata retained')
    for paper in missing:
        attempts = {a['status'] for a in paper.metadata_recovery_attempts}
        status = ('access_blocked' if 'access_blocked' in attempts else
                  'lookup_limit' if attempts & {'lookup_limit', 'time_limit'} else
                  'lookup_failed' if any(s.startswith('request_failed_') for s in attempts) else 'not_provided')
        for field in ('authors', 'affiliations'):
            if not getattr(paper, field):
                setattr(paper, field + '_status', status)
