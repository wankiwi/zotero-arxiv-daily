"""Research Square metadata via OpenAlex's public, keyless API."""
from datetime import datetime, timedelta, timezone
import re

from loguru import logger
from omegaconf import ListConfig

from ..http import session
from ..identity import canonical_doi, normalize_doi
from ..protocol import Paper
from .journal_retriever import clean_text

API = 'https://api.openalex.org'


class MissingOpenAlexRecord(RuntimeError):
    pass


def string_list(value, field):
    if not isinstance(value, (list, ListConfig)) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(f'Research Square {field} requires a non-empty string list')
    return list(dict.fromkeys(v.strip() for v in value))


def get_json(client, path, params=None):
    response = client.get(API + path, params=params, timeout=(10, 30))
    if response.status_code == 404:
        raise MissingOpenAlexRecord('OpenAlex record returned HTTP 404')
    if response.status_code in (401, 403, 429):
        raise RuntimeError(f'OpenAlex access/budget error HTTP {response.status_code}; check access or free quota; no paid fallback')
    response.raise_for_status()
    return response.json()


def abstract_text(index):
    if not isinstance(index, dict):
        return ''
    # Sort sparse positions: do not allocate an array sized by untrusted offsets.
    words = {}
    for word, positions in index.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for position in positions:
            if type(position) is int and 0 <= position < 100000:
                words.setdefault(position, word)
    return clean_text(' '.join(words[pos] for pos in sorted(words)))


def publication_date(item):
    try:
        return datetime.strptime(item.get('publication_date', ''), '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class OpenAlexResearchSquare:
    def __init__(self, config):
        self.config = config
        self.failures = []
        self.sources = string_list(config.get('source_ids'), 'source_ids')
        if len(self.sources) > 100 or any(not re.fullmatch(r'S\d+', s) for s in self.sources):
            raise ValueError('Research Square source_ids must contain at most 100 OpenAlex S IDs')
        self.types = string_list(config.get('type'), 'type')
        if self.types != ['preprint']:
            raise ValueError('Research Square OpenAlex currently supports type: [preprint] only')
        self.subfields = string_list(config.get('subfield'), 'subfield')
        if len(self.subfields) > 100:
            raise ValueError('Research Square supports at most 100 subfields')

    def verify_sources(self, client):
        self.failures.clear()
        verified = []
        for source_id in self.sources:
            try:
                item = get_json(client, '/sources/' + source_id)
            except MissingOpenAlexRecord:
                self.failures.append(f'{source_id}: OpenAlex source HTTP 404')
                logger.warning(f'OpenAlex source {source_id} returned 404; continuing verified sources with incomplete coverage')
                continue
            name = re.sub(r'[^a-z]', '', str(item.get('display_name', '')).lower())
            if item.get('id') != 'https://openalex.org/' + source_id or name not in ('researchsquare', 'researchsquareresearchsquare'):
                raise ValueError(f'OpenAlex source {source_id} is not a verified Research Square record')
            verified.append(source_id)
        if not verified:
            raise ValueError('No verified Research Square sources remain; configured sources are unavailable')
        return verified

    def resolve_subfields(self, client):
        # Resolve exact names against the live official catalog, never guess IDs.
        matches = {name: set() for name in self.subfields}
        received = 0
        for page in range(1, 11):
            data = get_json(client, '/subfields', {'per_page': 100, 'page': page})
            items, count = data['results'], int(data['meta']['count'])
            for item in items:
                name, identity = item.get('display_name'), item.get('id', '')
                if name in matches and re.fullmatch(r'https://openalex.org/subfields/\d{4}', identity):
                    matches[name].add(identity.rsplit('/', 1)[-1])
            received += len(items)
            if received >= count:
                break
            if not items:
                raise RuntimeError('OpenAlex subfield catalog pagination incomplete')
        else:
            raise RuntimeError('OpenAlex subfield catalog exceeds safety page limit')
        bad = [name for name, ids in matches.items() if len(ids) != 1]
        if bad:
            raise ValueError(f'OpenAlex subfields missing or ambiguous: {bad}')
        resolved = {name: next(iter(ids)) for name, ids in matches.items()}
        logger.info(f'OpenAlex verified subfields: {resolved}')
        return set(resolved.values())

    def retrieve(self):
        days, max_pages = int(self.config.get('window_days', 1)), int(self.config.get('max_pages', 100))
        if not 1 <= days <= 90 or max_pages < 1:
            raise ValueError('Research Square requires window_days 1–90 and positive max_pages')
        until = datetime.now(timezone.utc).date()
        since = until - timedelta(days=days)
        with session() as client:
            verified_sources = self.verify_sources(client)
            subfields = self.resolve_subfields(client)
            filters = ','.join(['locations.source.id:' + '|'.join(verified_sources), 'type:preprint',
                'topics.subfield.id:' + '|'.join(sorted(subfields)),
                f'from_publication_date:{since}', f'to_publication_date:{until}'])
            cursor, seen, records, received = '*', set(), [], 0
            for _ in range(max_pages):
                data = get_json(client, '/works', {'filter': filters, 'per_page': 100, 'cursor': cursor})
                items, meta = data['results'], data['meta']
                received += len(items)
                records.extend(item for item in items if isinstance(item, dict))
                count, next_cursor = int(meta['count']), meta.get('next_cursor')
                if received >= count:
                    break
                if not items or not next_cursor or next_cursor == cursor or next_cursor in seen:
                    raise RuntimeError('OpenAlex pagination stopped before all records were retrieved')
                seen.add(cursor)
                cursor = next_cursor
            else:
                raise RuntimeError('OpenAlex max_pages reached; narrow window_days or increase max_pages')
        scoped = []
        for item in records:
            try:
                if item.get('doi') is not None and not isinstance(item['doi'], str):
                    raise TypeError('Invalid DOI')
                sources = {(loc.get('source') or {}).get('id', '').rsplit('/', 1)[-1] for loc in item.get('locations', [])}
                fields = {(topic.get('subfield') or {}).get('id', '').rsplit('/', 1)[-1] for topic in item.get('topics', [])}
                date = publication_date(item)
                if item.get('type') == 'preprint' and sources.intersection(verified_sources) and fields.intersection(subfields) and date and since <= date.date() <= until:
                    scoped.append(item)
            except (AttributeError, TypeError):
                logger.warning('Skipping malformed OpenAlex work scope metadata')
        # Select versions before suppressing withdrawn records. A higher version
        # that is present in this filtered window must not resurrect an older one.
        def version(item):
            match = re.search(r'/v(\d+)$', normalize_doi(item.get('doi')) or '')
            return (int(match[1]) if match else 0, bool(item.get('is_retracted')))
        scoped.sort(key=version, reverse=True)
        newest, work_ids = {}, set()
        for item in scoped:
            identity = item.get('id')
            if not isinstance(identity, str) or not re.fullmatch(r'https://openalex.org/W\d+', identity):
                continue
            key = canonical_doi(item.get('doi')) or identity
            if identity not in work_ids:
                newest.setdefault(key, item)
                work_ids.add(identity)
        return list(newest.values())

    @staticmethod
    def convert(item):
        title = clean_text(item.get('title') or item.get('display_name'))
        if not title or item.get('is_retracted') or re.match(r'^(?:withdrawn|retracted|withdrawal|retraction)\s*[:：]|^\[(?:withdrawn|retracted)\]', title, re.I):
            return None
        doi = normalize_doi(item.get('doi'))
        identity = item.get('id', '')
        if not re.fullmatch(r'https://openalex.org/W\d+', identity):
            return None
        authors = [(entry.get('author') or {}).get('display_name', '') for entry in item.get('authorships', []) if isinstance(entry, dict)]
        return Paper(source='researchsquare', title=title, authors=[a for a in authors if a],
                     abstract=abstract_text(item.get('abstract_inverted_index')),
                     url=f'https://doi.org/{doi}' if doi else identity, doi=doi,
                     pdf_url=None, full_text=None, published=publication_date(item))
