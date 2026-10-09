"""Offline D1 SQL/HTTP contract and SMTP ordering; never contact production."""
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.canned_responses import make_sample_paper
from zot2dailypaper.executor import Executor
from zot2dailypaper.identity import paper_id
from zot2dailypaper.recommendation_sync import (
    BATCH_SIZE, FAILURE, MAX_RESPONSE_BYTES, QUERY_URL, WORKER_ORIGIN,
    RecommendationSync, SyncUnavailable, citation_payload, sync_client,
)
from zot2dailypaper.state import State
from zot2dailypaper.zotero_action import confirmation_link

FAKE_TOKEN = 'test-token-never-a-real-secret-0000'
SQLITE_CLIENTS = []


@pytest.fixture(autouse=True)
def close_test_databases():
    yield
    while SQLITE_CLIENTS:
        SQLITE_CLIENTS.pop().db.close()


class Response:
    def __init__(self, data=None, status=200, raw=None):
        self.status_code = status
        self.raw = raw if raw is not None else json.dumps(data).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.raw), chunk_size):
            yield self.raw[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class D1:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        SQLITE_CLIENTS.append(self)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE papers(id TEXT PRIMARY KEY, canonical_identity TEXT NOT NULL,
                                payload TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE credentials(key_encrypted TEXT);
            INSERT INTO credentials VALUES('ciphertext-placeholder');
            CREATE TABLE saves(identity TEXT);
            INSERT INTO saves VALUES('existing-save');
        """)
        self.calls = []
        self.responses = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        cursor = self.db.execute(kwargs['json']['sql'], kwargs['json']['params'])
        response = Response({'success': True, 'errors': [], 'result': [
            {'success': True, 'results': [dict(row) for row in cursor.fetchall()]}]})
        self.responses.append(response)
        return response

    def close(self):
        self.closed = True


def history(tmp_path, count=1):
    state = State(tmp_path / 'state.json')
    papers = [make_sample_paper(doi=f'10.1000/paper-{i}', url=f'https://example.org/{i}')
              for i in range(count)]
    state.add(papers)
    state.save()
    return state, papers


def test_parameterized_batches_readback_and_public_only_fields(tmp_path):
    state, papers = history(tmp_path, BATCH_SIZE + 1)
    client = D1()
    RecommendationSync(FAKE_TOKEN).sync(papers, state, client=client)
    assert len(client.calls) == 4
    assert all(url == QUERY_URL for url, _ in client.calls)
    assert all(kwargs['allow_redirects'] is False and kwargs['stream'] is True
               for _, kwargs in client.calls)
    assert max(len(kwargs['json']['params']) for _, kwargs in client.calls) == 80
    assert all(response.closed for response in client.responses)
    for row in client.db.execute('SELECT * FROM papers'):
        assert row['id'] == hashlib.sha256(row['canonical_identity'].encode()).hexdigest()
        payload = json.loads(row['payload'])
        assert set(payload) == {'title', 'authors', 'abstract', 'url', 'doi', 'journal', 'published'}
        assert FAKE_TOKEN not in row['payload']
    assert client.db.execute('SELECT * FROM credentials').fetchone()[0] == 'ciphertext-placeholder'
    assert client.db.execute('SELECT * FROM saves').fetchone()[0] == 'existing-save'
    assert [paper_id(p) for p in State(state.path).pending('email')] == [paper_id(p) for p in papers]
    assert not client.closed  # Caller owns a supplied transport.


def test_retry_is_idempotent_and_preserves_added_timestamp(tmp_path):
    state, papers = history(tmp_path)
    papers[0].title = "A quote: ' ; DROP TABLE credentials; --"
    client = D1()
    sync = RecommendationSync(FAKE_TOKEN)
    sync.sync(papers, state, client=client)
    first = dict(client.db.execute('SELECT * FROM papers').fetchone())
    sync.sync(papers, state, client=client)
    assert dict(client.db.execute('SELECT * FROM papers').fetchone()) == first
    assert client.db.execute('SELECT COUNT(*) FROM papers').fetchone()[0] == 1
    assert client.db.execute('SELECT COUNT(*) FROM credentials').fetchone()[0] == 1
    papers[0].abstract = 'Updated public abstract'
    sync.sync(papers, state, client=client)
    updated = dict(client.db.execute('SELECT * FROM papers').fetchone())
    assert updated['id'] == first['id'] and updated['updated_at'] == first['updated_at']
    assert json.loads(updated['payload'])['abstract'] == 'Updated public abstract'


@pytest.mark.parametrize('overrides', [
    {'doi': 'https://doi.org/10.1021/ABC?utm_source=email', 'url': 'https://doi.org/10.1021/ABC'},
    {'doi': None, 'url': 'https://arxiv.org/abs/2401.01234v3?context=cs#top'},
    {'doi': None, 'url': 'https://example.org/article?id=2&utm_source=email#top'},
    {'doi': '10.21203/rs.3.rs-123/v2', 'url': 'https://example.org/rs'},
])
def test_exact_email_identifier_and_worker_payload_identity(tmp_path, overrides):
    paper = make_sample_paper(**overrides)
    state = State(tmp_path / 'state.json')
    state.add([paper])
    client = D1()
    RecommendationSync(FAKE_TOKEN).sync([paper], state, client=client)
    row = client.db.execute('SELECT * FROM papers').fetchone()
    assert confirmation_link(paper, WORKER_ORIGIN).endswith('?paper=' + row['id'])
    assert row['canonical_identity'] == paper_id(paper)
    assert '#' not in json.loads(row['payload'])['url']


def test_unicode_worker_limits_and_no_full_text_or_ai_summary():
    paper = make_sample_paper(title='😀' * 600, abstract='😀' * 7000,
                              authors=['😀' * 200], journal='😀' * 300,
                              full_text='private downloaded text', tldr='AI text')
    payload = json.loads(citation_payload(paper))
    assert len(payload['title'].encode('utf-16-le')) == 1024
    assert len(payload['abstract'].encode('utf-16-le')) == 12000
    assert len(payload['authors'][0].encode('utf-16-le')) == 320
    assert len(payload['journal'].encode('utf-16-le')) == 512
    assert 'private downloaded text' not in json.dumps(payload)
    assert 'AI text' not in json.dumps(payload)


@pytest.mark.parametrize('url', [
    'http://example.org/paper', 'https://u:password@example.org/paper',
    'https://127.0.0.1/paper', 'https://localhost/paper', 'https://example.org:444/a',
    'https://example.internal/a', 'https://example.org/a\\b', 'https://example.org/ bad',
])
def test_invalid_record_blocks_entire_batch_before_mutation(tmp_path, url):
    state, papers = history(tmp_path, 2)
    papers[1].url = url
    client = D1()
    with pytest.raises(SyncUnavailable):
        RecommendationSync(FAKE_TOKEN).sync(papers, state, client=client)
    assert client.calls == []


@pytest.mark.parametrize('response', [
    Response(status=302, raw=b'secret provider body'),
    Response(status=429, raw=b'secret provider body'),
    Response(status=503, raw=b'secret provider body'),
    Response(raw=b'<html>secret provider body</html>'),
    Response({'success': True, 'result': []}),
    Response({'success': True, 'result': [{'success': False}]}),
    Response({'success': False, 'result': [{'success': True}]}),
    Response({'success': True, 'errors': [{'message': 'secret'}], 'result': [{'success': True}]}),
    Response(raw=b'x' * (MAX_RESPONSE_BYTES + 1)),
])
def test_transport_failure_never_discloses_details_or_retries(tmp_path, response):
    state, papers = history(tmp_path)
    calls = []
    def post(*args, **kwargs):
        calls.append(args)
        return response
    with pytest.raises(SyncUnavailable) as error:
        RecommendationSync(FAKE_TOKEN).sync(papers, state, client=SimpleNamespace(post=post))
    assert str(error.value) == FAILURE and len(calls) == 1 and response.closed
    assert FAKE_TOKEN not in str(error.value) and 'secret' not in str(error.value)


def test_exception_and_unverified_readback_leave_email_pending(tmp_path):
    state, papers = history(tmp_path)
    def post(*args, **kwargs):
        raise requests_error
    requests_error = OSError('secret token ' + FAKE_TOKEN)
    with pytest.raises(SyncUnavailable, match='email remains pending'):
        RecommendationSync(FAKE_TOKEN).sync(papers, state, client=SimpleNamespace(post=post))
    client = D1()
    original = client.post
    def wrong_readback(url, **kwargs):
        response = original(url, **kwargs)
        if kwargs['json']['sql'].startswith('SELECT'):
            response.raw = json.dumps({'success': True, 'result': [
                {'success': True, 'results': []}]}).encode()
        return response
    client.post = wrong_readback
    with pytest.raises(SyncUnavailable, match='email remains pending'):
        RecommendationSync(FAKE_TOKEN).sync(papers, state, client=client)
    assert len(State(state.path).pending('email')) == 1


def test_gate_requires_secure_user_setup_and_preserves_legacy_origin():
    class NoSecretAccess(dict):
        def get(self, key, default=None):
            pytest.fail('Disabled mode must not read credentials')
    assert sync_client({'worker_sync': 'disabled', 'zotero_action_origin': 'https://old.example.org'},
                       NoSecretAccess()) is None
    for origin in (WORKER_ORIGIN, WORKER_ORIGIN + '/'):
        with pytest.raises(SyncUnavailable):
            sync_client({'worker_sync': 'disabled', 'zotero_action_origin': origin}, {})
    with pytest.raises(SyncUnavailable, match='Actions secret'):
        sync_client({'worker_sync': 'free', 'zotero_action_origin': WORKER_ORIGIN}, {})
    with pytest.raises(SyncUnavailable):
        sync_client({'worker_sync': 'paid', 'zotero_action_origin': WORKER_ORIGIN},
                    {'ZOTERO_WORKER_D1_TOKEN': FAKE_TOKEN})


def test_sync_precedes_smtp_and_failed_sync_does_not_mark_delivery(config, tmp_path, monkeypatch):
    config.state.enabled = True
    config.state.path = str(tmp_path / 'state.json')
    config.llm.enabled = False
    config.email.zotero_action_origin = WORKER_ORIGIN
    state, _ = history(tmp_path)
    events = []
    def sync(pending, current_state):
        events.append('sync')
        assert len(pending) == 1 and current_state.path.exists()
        raise SyncUnavailable(FAILURE)
    monkeypatch.setattr('zot2dailypaper.executor.sync_client',
                        lambda _: SimpleNamespace(sync=sync))
    monkeypatch.setattr('zot2dailypaper.executor.send_email', lambda *args: events.append('smtp'))
    executor = Executor(config)
    monkeypatch.setattr(executor, '_recommend', lambda *args: [])
    with pytest.raises(RuntimeError, match='email remains pending'):
        executor.run()
    assert events == ['sync'] and State(state.path).pending('email')
    def success(pending, current_state):
        events.append('sync')
    monkeypatch.setattr('zot2dailypaper.executor.sync_client',
                        lambda _: SimpleNamespace(sync=success))
    executor.run()
    assert events == ['sync', 'sync', 'smtp']
    assert not State(state.path).pending('email')


def test_missing_secret_blocks_before_recommendation_or_paid_work(config, monkeypatch):
    config.email.worker_sync = 'free'
    config.email.zotero_action_origin = WORKER_ORIGIN
    monkeypatch.delenv('ZOTERO_WORKER_D1_TOKEN', raising=False)
    executor = Executor(config)
    monkeypatch.setattr(executor, '_recommend', lambda *args: pytest.fail('Must validate setup first'))
    with pytest.raises(SyncUnavailable, match='Actions secret'):
        executor.run()


def test_owned_transport_disables_ambient_auth_and_is_closed(tmp_path, monkeypatch):
    state, papers = history(tmp_path)
    client = D1()
    client.trust_env = True
    monkeypatch.setattr('zot2dailypaper.recommendation_sync.requests.Session', lambda: client)
    RecommendationSync(FAKE_TOKEN).sync(papers, state)
    assert client.trust_env is False and client.closed


def test_empty_batch_and_admission_cap_make_no_requests(tmp_path):
    state, papers = history(tmp_path)
    client = D1()
    sync = RecommendationSync(FAKE_TOKEN)
    sync.sync([], state, client=client)
    assert client.calls == []
    with pytest.raises(SyncUnavailable, match='batch exceeds'):
        sync.sync(papers * 1001, state, client=client)
    assert client.calls == []


def test_deadline_closes_response_and_stops_remaining_batches(tmp_path, monkeypatch):
    state, papers = history(tmp_path, 21)
    client = D1()
    clock = iter([0, 1, 121])
    monkeypatch.setattr('zot2dailypaper.recommendation_sync.monotonic', lambda: next(clock))
    with pytest.raises(SyncUnavailable, match='email remains pending'):
        RecommendationSync(FAKE_TOKEN).sync(papers, state, client=client)
    assert len(client.calls) == 1 and client.responses[0].closed


def test_partial_failure_can_replay_all_batches_without_duplicate_rows(tmp_path):
    state, papers = history(tmp_path, 21)
    client = D1()
    original = client.post
    def fail_second_write(url, **kwargs):
        if len(client.calls) == 2:
            client.calls.append((url, kwargs))
            return Response(status=503, raw=b'private upstream message')
        return original(url, **kwargs)
    client.post = fail_second_write
    sync = RecommendationSync(FAKE_TOKEN)
    with pytest.raises(SyncUnavailable):
        sync.sync(papers, state, client=client)
    assert client.db.execute('SELECT COUNT(*) FROM papers').fetchone()[0] == 20
    client.post = original
    sync.sync(papers, state, client=client)
    assert client.db.execute('SELECT COUNT(*) FROM papers').fetchone()[0] == 21
    assert len(State(state.path).pending('email')) == 21
