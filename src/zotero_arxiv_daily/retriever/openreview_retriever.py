"""Public OpenReview API v2 submissions with explicit venue/subject/keyword gates."""
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping
import re
import os
from urllib.parse import quote
from loguru import logger
from .base import BaseRetriever, register_retriever
from ..http import session
from ..protocol import Paper
from ..abstracts import clean_abstract
from ..preprint_interests import keyword_text, _terms

API = 'https://api2.openreview.net'
VENUES = {'ICLR': 'ICLR.cc/{year}/Conference', 'NeurIPS': 'NeurIPS.cc/{year}/Conference',
          'ICML': 'ICML.cc/{year}/Conference', 'CoRL': 'robot-learning.org/CoRL/{year}/Conference', 'TMLR': 'TMLR'}
SUBJECTS = {
    'AI for Science': ('ai 4 physical sciences', 'machine learning for sciences', 'physical sciences',
                       'chemistry physics and earth sciences', 'ai for science'),
    'Scientific Machine Learning': ('scientific machine learning', 'physics informed', 'neural operator',
                                    'machine learning for sciences', 'ai 4 physical sciences', 'physical sciences',
                                    'chemistry physics and earth sciences'),
    'Machine Learning': ('machine learning', 'deep learning', 'reinforcement learning', 'representation learning',
                         'neural network', 'supervised learning', 'unsupervised learning'),
}

def value(content, key, default=None):
    result = content.get(key, default)
    return result.get('value', default) if isinstance(result, dict) else result

def public_fields(content):
    # Fields without their own ACL inherit the public note/group ACL. Explicit
    # field ACLs override it; never pass restricted values to filters or output.
    return {key: item for key, item in content.items()
            if not isinstance(item, dict) or 'readers' not in item or
            isinstance(item['readers'], list) and 'everyone' in item['readers']}


def strings(item):
    return [item] if isinstance(item, str) else [v for v in item if isinstance(v, str)] if isinstance(item, list) else []

def contains(texts, terms):
    return any(' ' + keyword_text(term) + ' ' in ' ' + keyword_text(text.replace('_', ' ')) + ' ' for term in terms for text in texts)

@register_retriever('openreview')
class OpenReviewRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        self.options = dict(self.retriever_config)
        self.options.update(self.interests)
        self.venues = _terms(self.options.get('venues'), 'openreview.venues')
        self.subjects = _terms(self.options.get('subject_areas'), 'openreview.subject_areas')
        self.keywords = _terms(self.options.get('keywords'), 'openreview.keywords')
        if set(self.venues) - VENUES.keys(): raise ValueError('Unsupported OpenReview venue; supported: ICLR, NeurIPS, ICML, TMLR, CoRL')
        if set(self.subjects) - SUBJECTS.keys(): raise ValueError('Unsupported OpenReview subject area; use AI for Science, Machine Learning, Scientific Machine Learning')
        self.days, self.max_pages = self.options.get('window_days', 7), self.options.get('max_pages', 100)
        if type(self.days) is not int or not 1 <= self.days <= 90 or type(self.max_pages) is not int or self.max_pages < 1:
            raise ValueError('OpenReview requires window_days 1-90 and positive max_pages')
        self.authenticated = False
        self.failures = []
        self.venue_ids = {}
        self.fallback_count = 0

    def _authenticate(self, client):
        """Official API v2 login; credentials/token stay only in memory."""
        self.authenticated = False
        username, password = os.getenv('OPENREVIEW_USERNAME'), os.getenv('OPENREVIEW_PASSWORD')
        if not username and not password:
            return
        if not username or not password:
            raise RuntimeError('Set both OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD repository secrets')
        try:
            response = client.post(API + '/login', json={'id': username, 'password': password, 'expiresIn': 3600},
                                   timeout=(10, 30), allow_redirects=False)
            if response.status_code != 200:
                raise RuntimeError('login rejected')
            data = response.json()
        except Exception:
            raise RuntimeError('OpenReview official login failed; verify credentials/account access (response withheld)') from None
        if not isinstance(data, dict):
            raise RuntimeError('OpenReview official login returned an invalid response')
        if data.get('mfaPending'):
            raise RuntimeError('OpenReview login requires MFA; unattended login cannot continue; complete an officially supported account authentication setup')
        token = data.get('token')
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise RuntimeError('OpenReview official login returned no valid session token')
        client.headers['Authorization'] = 'Bearer ' + token
        self.authenticated = True

    def _get(self, client, path, params):
        response = client.get(API + path, params=params, timeout=(10, 30), allow_redirects=False)
        if response.status_code in (401, 403, 429):
            raise RuntimeError(f'OpenReview API access blocked HTTP {response.status_code}; ' +
                ('rate limited: wait for the next run; respect provider limits' if response.status_code == 429 else
                 'configure OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD secrets for official login, or verify account access if already configured'))
        if 300 <= response.status_code < 400:
            raise RuntimeError("OpenReview API redirected; authenticate through the official supported path")
        response.raise_for_status()
        return response.json()

    def _groups(self, client, venue, year):
        years = [year - 1, year, year + 1] if venue == 'ICLR' else [year - 1, year]
        for identity in ([VENUES[venue]] if venue == 'TMLR' else [VENUES[venue].format(year=y) for y in years]):
            data = self._get(client, '/groups', {'id': identity})
            groups = [g for g in data.get('groups', []) if g.get('id') == identity]
            for group in groups:
                content = public_fields(group.get('content', {}))
                invitation = value(content, 'submission_id')
                name = value(content, 'submission_name')
                if not invitation and isinstance(name, str): invitation = identity + '/-/' + name
                if not isinstance(invitation, str) or not invitation.startswith(identity + '/-/'):
                    raise ValueError(f'{identity}: missing supported submission invitation metadata')
                self.venue_ids[identity] = {
                    'under_review': value(content, 'under_review_venue_id'),
                    'decision_pending': value(content, 'decision_pending_venue_id'),
                    # API v2 accepted-paper queries use content.venueid=<venue>.
                    # Require pdate as separate acceptance/publication evidence.
                    'accepted': value(content, 'accepted_venue_id') or identity,
                }
                yield identity, invitation

    def _retrieve_raw_papers(self):
        until = datetime.now(timezone.utc)
        since = until.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=self.days)
        self.since, self.until = since, until
        self.failures, self.fallback_count = [], 0
        records, seen = [], set()
        with session() as client:
            self._authenticate(client)
            for venue in self.venues:
                try:
                    groups = list(self._groups(client, venue, until.year))
                    if not groups: raise RuntimeError('No public API v2 conference groups found')
                    for group, invitation in groups:
                        offset, previous = 0, None
                        for _ in range(self.max_pages):
                            data = self._get(client, '/notes', {'invitation': invitation, 'limit': 1000, 'offset': offset,
                                'mintmdate': int(since.timestamp() * 1000), 'sort': 'tmdate:asc'})
                            notes = data.get('notes')
                            if not isinstance(notes, list): raise ValueError('Malformed OpenReview notes response')
                            fingerprint = tuple(n.get('id') for n in notes)
                            if notes and fingerprint == previous: raise RuntimeError('OpenReview pagination did not advance')
                            previous = fingerprint
                            for note in notes:
                                identity = note.get('id')
                                if not identity or identity in seen: continue
                                seen.add(identity)
                                records.append((venue, group, note))
                            offset += len(notes)
                            count = data.get('count')
                            if not notes:
                                if isinstance(count, int) and offset < count: raise RuntimeError('OpenReview pagination incomplete')
                                break
                            if isinstance(count, int) and offset >= count or len(notes) < 1000 and count is None: break
                        else: raise RuntimeError(f'{group}: OpenReview page limit reached; increase max_pages')
                except Exception as exc:
                    self.failures.append(f'{venue}: {exc}')
                    logger.warning(f'OpenReview {venue}: {exc}')
        return records

    def convert_to_paper(self, raw):
        venue, group, note = raw
        if not isinstance(note, Mapping) or not isinstance(note.get('content'), Mapping):
            raise ValueError('OpenReview note content must be a mapping')
        readers = note.get('readers', [] if self.authenticated else ['everyone'])
        if note.get('ddate') or not isinstance(readers, list) or 'everyone' not in readers: return None
        content = public_fields(note['content'])
        if 'withdraw' in str(value(content, 'venue', '')).casefold(): return None
        title, abstract = clean_abstract(value(content, 'title', '')), clean_abstract(value(content, 'abstract', ''))
        if not title: return None
        status_ids = self.venue_ids.get(group, {})
        venue_id = value(content, 'venueid')
        status, kind = 'unverified', 'preprint'
        if venue_id and venue_id == status_ids.get('under_review'):
            status = 'under_review'
        elif venue_id and venue_id == status_ids.get('decision_pending'):
            status = 'decision_pending'
        elif venue_id and venue_id == status_ids.get('accepted') and type(note.get('pdate')) is int and note['pdate'] > 0:
            status = 'published'
            kind = 'journal' if venue == 'TMLR' else 'conference'
        # odate is first public visibility. Older schemas may expose only cdate.
        timestamp = note['pdate'] if status == 'published' else note.get('odate') or note.get('cdate') or note.get('tcdate')
        if not timestamp: raise ValueError('OpenReview submission has no public/creation timestamp')
        published = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
        if not self.since <= published <= self.until: return None
        author_keywords = [text for key in ('keywords', 'primary_keyword', 'secondary_keyword', 'free_keyword_1', 'free_keyword_2')
                           for text in strings(value(content, key))]
        if not contains([title, abstract, *author_keywords], self.keywords): return None
        areas = [text for key in ('primary_area', 'secondary_area', 'subject_areas', 'subject_area', 'area') for text in strings(value(content, key))]
        if contains(areas, [term for subject in self.subjects for term in SUBJECTS[subject]]):
            reason = 'subject field matched configured area'
        elif 'Machine Learning' in self.subjects:
            # These five verified main venues are ML venues, not a universal taxonomy.
            reason = 'Machine Learning venue-scope fallback (subject absent or venue-specific taxonomy)'
            self.fallback_count += 1
        elif not areas and contains([title, abstract, *author_keywords], [term for s in self.subjects for term in SUBJECTS[s]]):
            reason = 'subject text fallback (venue has no subject field)'
            self.fallback_count += 1
        else: return None
        logger.debug(f'OpenReview {note["id"]}: {reason}')
        identity = quote(note['id'], safe='')
        return Paper(source='openreview', title=title, abstract=abstract,
                     authors=strings(value(content, 'authors', [])), url='https://openreview.net/forum?id=' + identity,
                     pdf_url='https://openreview.net/pdf?id=' + identity if value(content, 'pdf') else None,
                     published=published, subject_match_reason=reason, abstract_source='OpenReview' if abstract else None,
                     publication_kind=kind, publication_status=status, publication_venue=group,
                     journal='Transactions on Machine Learning Research' if kind == 'journal' else None)

    def retrieve_papers(self):
        # Keyword filtering includes author keywords, unlike the common title/abstract filter.
        if not self.options.get('enabled', True): return []
        result = []
        for raw in self._retrieve_raw_papers():
            try:
                paper = self.convert_to_paper(raw)
                if paper: result.append(paper)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                self.failures.append(f'{raw[0]}: malformed submission ({type(exc).__name__})')
        if self.fallback_count:
            logger.warning(f'OpenReview: {self.fallback_count} selections used documented subject fallback; inspect subject_match_reason')
        return result
