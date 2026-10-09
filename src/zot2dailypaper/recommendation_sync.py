"""Bounded public citation sync to the approved D1 database before email."""
import hashlib
import json
import os
import re
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

import requests

from .identity import normalize_doi, paper_id
from .zotero_action import confirmation_origin


WORKER_ORIGIN = 'https://zot2dailypaper-save.wankaiweii.workers.dev'
ACCOUNT_ID = '7d50defa6cf2776ad44d6da8c9669607'
DATABASE_ID = '7b5f060d-6375-4159-a548-89cc9ba91a41'
QUERY_URL = f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/d1/database/{DATABASE_ID}/query'
BATCH_SIZE = 20
MAX_PAPERS = 1000
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_SYNC_SECONDS = 120
UPSERT = (
    'INSERT INTO papers(id,canonical_identity,payload,updated_at) VALUES {values} '
    'ON CONFLICT(id) DO UPDATE SET canonical_identity=excluded.canonical_identity,'
    'payload=excluded.payload,updated_at=excluded.updated_at '
    'WHERE papers.payload!=excluded.payload OR papers.canonical_identity!=excluded.canonical_identity'
)
FAILURE = 'Recommendation metadata sync failed; email remains pending. Check D1 access and retry the normal schedule.'


class SyncUnavailable(RuntimeError):
    """Only fixed, non-secret messages may cross the transport boundary."""


def _bounded_text(value, limit):
    # Worker validates JS UTF-16 length. Never split an astral character.
    if not isinstance(value, str):
        raise SyncUnavailable('Invalid recommendation metadata; email remains pending.')
    return value.encode('utf-16-le')[:limit * 2].decode('utf-16-le', errors='ignore')


def citation_payload(paper):
    """Match v8's seven public citation fields, excluding scores/TLDR/full text."""
    try:
        url = paper.url
        parts = urlsplit(url)
        if (not isinstance(url, str) or len(url.encode('utf-16-le')) > 4096
                or re.search(r'[\x00-\x20\x7f\\]', url)
                or parts.scheme != 'https' or not parts.hostname
                or parts.username or parts.password or parts.port not in (None, 443)
                or '.' not in parts.hostname or ':' in parts.hostname
                or re.fullmatch(r'[\d.]+', parts.hostname)
                or re.search(r'(?:^|\.)(?:localhost|local|internal|test|invalid)$', parts.hostname)):
            raise ValueError
        if not isinstance(paper.authors, list) or len(paper.authors) > 100:
            raise ValueError
        title = _bounded_text(paper.title, 512)
        if not title.strip():
            raise ValueError
        doi = normalize_doi(paper.doi or paper.url) or ''
        if len(doi.encode('utf-16-le')) > 1024:
            raise ValueError
        payload = {
            'title': title,
            'authors': [_bounded_text(author, 160) for author in paper.authors],
            'abstract': _bounded_text(paper.abstract or '', 6000),
            'url': urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, '')),
            'doi': doi,
            'journal': _bounded_text(paper.journal or '', 256),
            'published': paper.published.strftime('%Y-%m-%d') if paper.published else '',
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > 32768:
            raise ValueError
        return encoded
    except (AttributeError, TypeError, ValueError, UnicodeError):
        raise SyncUnavailable('Invalid recommendation metadata; email remains pending.') from None


class RecommendationSync:
    def __init__(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', token):
            raise SyncUnavailable('A dedicated ZOTERO_WORKER_D1_TOKEN Actions secret is required before enabling sync.')
        self._token = token

    def _query(self, client, sql, params, deadline):
        response = None
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ValueError
            response = client.post(
                QUERY_URL, json={'sql': sql, 'params': params},
                headers={'Authorization': 'Bearer ' + self._token, 'Accept': 'application/json'},
                timeout=(min(5, remaining), min(30, remaining)), allow_redirects=False, stream=True,
            )
            if response.status_code != 200:
                raise ValueError
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES or monotonic() > deadline:
                    raise ValueError
            data = json.loads(raw)
            result = data.get('result')
            if (data.get('success') is not True or data.get('errors')
                    or not isinstance(result, list) or len(result) != 1
                    or not isinstance(result[0], dict) or result[0].get('success') is not True):
                raise ValueError
            return result[0]
        except Exception:
            # Never expose provider bodies, SQL parameters, headers, or raw errors.
            raise SyncUnavailable(FAILURE) from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    raise SyncUnavailable(FAILURE) from None

    def sync(self, papers, state, *, client=None):
        deadline = monotonic() + MAX_SYNC_SECONDS
        if len(papers) > MAX_PAPERS:
            raise SyncUnavailable('Recommendation batch exceeds sync limit; email remains pending.')
        # Validate the entire batch before any network mutation.
        rows = []
        for paper in papers:
            identity = paper_id(paper)
            if len(identity) > 2048 or re.search(r'[\x00-\x1f\x7f]', identity):
                raise SyncUnavailable('Invalid recommendation identity; email remains pending.')
            identifier = hashlib.sha256(identity.encode('utf-8')).hexdigest()
            rows.append([identifier, identity, citation_payload(paper), state.records[identity]['added']])
        if not rows:
            return
        owned_client = client is None
        if owned_client:
            client = requests.Session()
            client.trust_env = False  # No ambient proxy credentials or .netrc authentication.
        try:
            for offset in range(0, len(rows), BATCH_SIZE):
                batch = rows[offset:offset + BATCH_SIZE]
                sql = UPSERT.format(values=','.join(['(?,?,?,?)'] * len(batch)))
                self._query(client, sql, [value for row in batch for value in row], deadline)
                # Exact ID/key/payload readback, indexed by primary key; no private tables.
                sql = ('SELECT id,canonical_identity,payload FROM papers WHERE id IN ('
                       + ','.join(['?'] * len(batch)) + ')')
                result = self._query(client, sql, [row[0] for row in batch], deadline).get('results')
                expected = {row[0]: (row[1], row[2]) for row in batch}
                if (not isinstance(result, list) or len(result) != len(expected)
                        or any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                               or not isinstance(row.get('canonical_identity'), str)
                               or not isinstance(row.get('payload'), str) for row in result)):
                    raise SyncUnavailable(FAILURE)
                actual = {row.get('id'): (row.get('canonical_identity'), row.get('payload')) for row in result}
                if actual != expected:
                    raise SyncUnavailable(FAILURE)
        finally:
            if owned_client:
                try:
                    client.close()
                except Exception:
                    raise SyncUnavailable(FAILURE) from None


def sync_client(email_config, environ=os.environ):
    mode = email_config.get('worker_sync', 'disabled')
    origin = confirmation_origin(email_config.get('zotero_action_origin'))
    if mode == 'disabled':
        if origin == WORKER_ORIGIN:
            raise SyncUnavailable('Worker email links require verified metadata sync.')
        return None
    if mode != 'free' or origin != WORKER_ORIGIN:
        raise SyncUnavailable('Worker sync requires the approved origin and explicit Free-plan acknowledgment.')
    return RecommendationSync(environ.get('ZOTERO_WORKER_D1_TOKEN'))
