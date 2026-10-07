"""Oct 7 UTC-budget collision and realistic summary failures, entirely offline."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import httpx
from loguru import logger
from omegaconf import open_dict
from openai import OpenAI
import pytest

from tests.canned_responses import make_sample_paper, make_chat_response
from tests.test_budget import make_git
from tests.test_pipeline import pipeline  # noqa: F401 -- shared isolated fixture
from zotero_arxiv_daily import budget, llm, protocol
from zotero_arxiv_daily.construct_email import render_email, email_plain_text
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.state import State, paper_dict, load_paper


MODEL = 'deepseek-ai/DeepSeek-V4-Flash'
CAP, COST = Decimal('.30'), Decimal('.00432')


def params(attempts=2):
    return {'enabled': True, 'language': 'Chinese', 'input_mode': 'abstract',
            'generation_kwargs': {'model': MODEL}, 'budget': {'enabled': True},
            'request': {'max_attempts': attempts, 'timeout_seconds': 30,
                        'retry_backoff_seconds': 1, 'max_retry_wait_seconds': 5}}


def guard(*, cap=CAP, threshold=3):
    return budget.BudgetRequests(cap, COST, budget.utc_day(), failure_threshold=threshold)


def good_json(**updates):
    data = {'id': 'offline', 'object': 'chat.completion', 'model': MODEL,
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': '该研究报告了经验证的模拟结果。'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}
    data.update(updates)
    return data


def client(handler):
    return OpenAI(api_key='offline-test-only', base_url='https://api.siliconflow.cn/v1',
                  # Deliberately use the SDK default retries; the paid boundary must override it.
                  http_client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))


def clock(monkeypatch):
    elapsed = [0.0]
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds
    monkeypatch.setattr(protocol, 'sleep', sleep)
    monkeypatch.setattr(llm, 'sleep', sleep)
    monkeypatch.setattr(llm, 'monotonic', lambda: elapsed[0])
    return elapsed, sleeps


@pytest.mark.parametrize('text,reason', [('INVALID_SAVED_SUMMARY', 'output_truncated'), (' ', None)])
def test_inconsistent_persisted_success_retains_abstract_and_reports_degradation(text, reason):
    paper = load_paper(paper_dict(make_sample_paper(tldr=text, tldr_status='generated',
                                                  tldr_error=None, tldr_error_reason=reason)))
    assert not paper.has_ai_summary and paper.summary_text == paper.abstract
    plain = email_plain_text(render_email([paper]))
    assert '0/1 generated' in plain and paper.abstract in plain
    assert 'INVALID_SAVED_SUMMARY' not in plain
    assert 'Original abstract' in plain


def test_delayed_schedule_collision_and_crashed_rerun_never_reclaim(tmp_path, monkeypatch):
    git, checkout, remote = make_git(tmp_path, monkeypatch)
    monkeypatch.setenv('GITHUB_RUN_ID', '37396029673')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    monkeypatch.setenv('GITHUB_SHA', 'b' * 40)
    budget.reserve_day(CAP, '2026-10-06')
    before = git('rev-parse', 'refs/heads/paper-state', cwd=remote)
    for run, attempt in [('37544458529', '1'), ('37396029673', '2')]:
        monkeypatch.setenv('GITHUB_RUN_ID', run)
        monkeypatch.setenv('GITHUB_RUN_ATTEMPT', attempt)
        with pytest.raises(budget.BudgetUnavailable) as rejected:
            budget.reserve_day(CAP, '2026-10-06')
        assert rejected.value.reason == 'daily_budget_reserved'
        assert rejected.value.day == '2026-10-06'
        assert rejected.value.retry_at == '2026-10-07T00:00:00+00:00'
        assert git('rev-parse', 'refs/heads/paper-state', cwd=remote) == before
    old = json.loads(git('show', before.decode().strip() + ':llm_budget.json'))['days']['2026-10-06']
    assert old['run_id'] == '37396029673' and old['run_attempt'] == '1'
    budget.reserve_day(CAP, '2026-10-07')
    after = git('rev-parse', 'refs/heads/paper-state', cwd=remote).decode().strip()
    ledger = json.loads(git('show', after + ':llm_budget.json'))
    assert ledger['days']['2026-10-06'] == old
    assert ledger['days']['2026-10-07']['reserved_cny'] == '0.30'
    assert b'preserved' in git('show', after + ':recommendations.json')


@pytest.mark.parametrize('mode', ['503', 'timeout', 'connection'])
def test_transient_retry_is_bounded_and_each_actual_attempt_is_charged(monkeypatch, mode):
    _, sleeps = clock(monkeypatch)
    calls = []
    reserved = guard()
    def handler(request):
        calls.append(json.loads(request.content))
        assert request.extensions['timeout'] == dict.fromkeys(('connect', 'read', 'write', 'pool'), 30)
        assert reserved.remaining == CAP - COST * len(calls)  # Charge BEFORE the transport.
        if len(calls) == 1:
            if mode == 'timeout':
                raise httpx.ReadTimeout('ambiguous billing', request=request)
            if mode == 'connection':
                raise httpx.ConnectError('temporary network failure', request=request)
            return httpx.Response(503, json={'error': {'message': 'PRIVATE_PROVIDER_BODY'}})
        return httpx.Response(200, json=good_json())
    paper = make_sample_paper()
    original = paper.abstract
    with client(handler) as sdk:
        paper.generate_tldr(sdk, params(), reserved)
    assert len(calls) == 2 and sleeps == [1]
    assert all(c['n'] == 1 and c['max_tokens'] == 96 and c['enable_thinking'] is False for c in calls)
    assert paper.has_ai_summary and paper.tldr_attempts == 2 and paper.abstract == original
    assert 'PRIVATE_PROVIDER_BODY' not in json.dumps(paper_dict(paper))


def test_retry_does_not_use_an_unreserved_slot(monkeypatch):
    clock(monkeypatch)
    calls = []
    paper, reserved = make_sample_paper(), guard(cap=COST)
    def handler(request):
        calls.append(True)
        raise httpx.ReadTimeout('unknown spend', request=request)
    with client(handler) as sdk:
        paper.generate_tldr(sdk, params(), reserved)
    assert len(calls) == paper.tldr_attempts == 1 and reserved.remaining == 0
    assert paper.tldr_error_reason == 'daily_budget_exhausted' and paper.tldr == paper.abstract


def test_midnight_during_backoff_never_dispatches_on_unreserved_day(monkeypatch):
    day = ['2026-10-06']
    monkeypatch.setattr(budget, 'utc_day', lambda: day[0])
    monkeypatch.setattr(protocol, 'sleep', lambda seconds: day.__setitem__(0, '2026-10-07'))
    calls = []
    paper, reserved = make_sample_paper(), guard()
    def handler(request):
        calls.append(True)
        raise httpx.ReadTimeout('uncertain response', request=request)
    with client(handler) as sdk:
        paper.generate_tldr(sdk, params(), reserved)
    assert len(calls) == 1 and reserved.remaining == CAP - COST
    assert paper.tldr_error_reason == 'day_changed' and paper.tldr_attempts == 1


@pytest.mark.parametrize('status,reason', [(401, 'authentication_failed'), (403, 'access_denied'),
                                         (429, 'rate_limited'), (400, 'request_rejected')])
def test_provider_restrictions_stop_parallel_papers_without_retry_or_bypass(status, reason):
    calls = []
    reserved = guard()
    papers = [make_sample_paper(title=f'Offline paper {i}') for i in range(8)]
    def handler(request):
        calls.append(True)
        return httpx.Response(status, headers={'Retry-After': '60'},
                              json={'error': {'message': 'PRIVATE_PROVIDER_BODY'}})
    with client(handler) as sdk, ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda p: p.generate_tldr(sdk, params(), reserved), papers))
    assert len(calls) == 1 and reserved.remaining == CAP - COST
    assert reserved.stop_reason == reason and not reserved.unavailable
    assert all(p.tldr == p.abstract and p.tldr_error_reason == reason for p in papers)
    assert sum(p.tldr_attempts for p in papers) == 1
    assert 'PRIVATE_PROVIDER_BODY' not in render_email(papers) + json.dumps([paper_dict(p) for p in papers])


def test_retry_after_applies_to_later_papers_even_when_first_has_no_retry(monkeypatch):
    elapsed, _ = clock(monkeypatch)
    calls = []
    def handler(request):
        calls.append(elapsed[0])
        if len(calls) == 1:
            return httpx.Response(503, headers={'Retry-After': '3'}, json={'error': {'message': 'offline'}})
        return httpx.Response(200, json=good_json())
    reserved = guard()
    first, second = make_sample_paper(), make_sample_paper()
    with client(handler) as sdk:
        first.generate_tldr(sdk, params(1), reserved)
        second.generate_tldr(sdk, params(1), reserved)
    assert calls == [0, 3] and first.tldr_status == 'fallback' and second.has_ai_summary


@pytest.mark.parametrize('header', ['60', 'garbage', 'NaN', 'Infinity'])
def test_long_or_unparseable_retry_after_stops_all_calls_instead_of_retrying_early(monkeypatch, header):
    clock(monkeypatch)
    calls = []
    def handler(request):
        calls.append(True)
        return httpx.Response(503, headers={'Retry-After': header}, json={'error': {'message': 'offline'}})
    reserved = guard()
    papers = [make_sample_paper(), make_sample_paper()]
    with client(handler) as sdk:
        for paper in papers:
            paper.generate_tldr(sdk, params(), reserved)
    assert len(calls) == 1 and reserved.remaining == CAP - COST
    assert all(p.tldr_error_reason == 'retry_wait_exceeded' for p in papers)


def test_http_date_retry_after(monkeypatch):
    now = datetime(2026, 10, 7, 0, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(llm, 'datetime', SimpleNamespace(now=lambda tz: now))
    exc = SimpleNamespace(response=SimpleNamespace(headers={'retry-after': 'Wed, 07 Oct 2026 00:00:04 GMT'}))
    assert llm.retry_after_seconds(exc) == 4


def test_repeated_transient_outage_opens_circuit_without_spending_every_paper(monkeypatch):
    clock(monkeypatch)
    calls = []
    def handler(request):
        calls.append(True)
        return httpx.Response(500, json={'error': {'message': 'offline'}})
    papers, reserved = [make_sample_paper(title=f'Offline {i}') for i in range(20)], guard()
    with client(handler) as sdk:
        for paper in papers:
            paper.generate_tldr(sdk, params(), reserved)
    assert len(calls) == 3 and reserved.remaining == CAP - COST * 3
    assert reserved.stop_reason == 'circuit_open'
    assert all(p.tldr == p.abstract and not p.has_ai_summary for p in papers)


@pytest.mark.parametrize('kind,reason', [('length', 'output_truncated'), ('content_filter', 'content_filtered'),
                                       ('empty', 'empty_response'), ('english', 'summary_language_mismatch'),
                                       ('tool', 'invalid_response'), ('sentences', 'summary_format_invalid'),
                                       ('list', 'summary_format_invalid')])
def test_unsuccessful_response_never_claims_generated_or_retries(kind, reason):
    calls = []
    def handler(request):
        calls.append(True)
        data = good_json()
        choice = data['choices'][0]
        if kind in ('length', 'content_filter'):
            choice['finish_reason'] = kind
        elif kind == 'empty':
            choice['message']['content'] = ' '
        elif kind == 'english':
            choice['message']['content'] = 'The study measured validated simulation results.'
        elif kind == 'sentences':
            choice['message']['content'] = '该研究报告了模拟结果。精度有所提高。'
        elif kind == 'list':
            choice['message']['content'] = '- 该研究报告了模拟结果。'
        else:
            choice['message']['tool_calls'] = [{'id': 'offline', 'type': 'function',
                                               'function': {'name': 'unexpected', 'arguments': '{}'}}]
        return httpx.Response(200, json=data)
    paper = make_sample_paper()
    with client(handler) as sdk:
        paper.generate_tldr(sdk, params(), guard())
    assert len(calls) == 1 and paper.tldr == paper.abstract
    assert paper.tldr_status == 'fallback' and paper.tldr_error_reason == reason
    assert 'AI summary</p>' not in render_email([paper])


@pytest.mark.parametrize('bad', ['choices', 'message', 'usage', 'reasoning_boolean', 'reasoning_content', 'model'])
def test_malformed_billing_metadata_aborts_later_requests(bad):
    calls = []
    def create(**kwargs):
        calls.append(True)
        response = make_chat_response('该研究报告了经验证的结果。', MODEL)
        if bad == 'choices':
            response.choices = None
        elif bad == 'message':
            response.choices[0].message = None
        elif bad == 'usage':
            response.usage = None
        elif bad == 'reasoning_boolean':
            response.usage.completion_tokens_details.reasoning_tokens = False
        elif bad == 'reasoning_content':
            response.choices[0].message.reasoning_content = 'PRIVATE_REASONING_BODY'
        else:
            response.model = 'unreserved-model'
        return response
    sdk = SimpleNamespace(max_retries=0, chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reserved = guard()
    papers = [make_sample_paper(), make_sample_paper()]
    for paper in papers:
        paper.generate_tldr(sdk, params(), reserved)
    assert len(calls) == 1 and reserved.remaining == 0
    assert all(p.tldr_error_reason == 'billing_unverified' and p.tldr == p.abstract for p in papers)


@pytest.mark.parametrize('enabled', ['false', 'true', 0, 1, None])
def test_explicit_enable_requires_boolean_before_any_provider_setup(config, enabled):
    config.llm.enabled = enabled
    with pytest.raises(ValueError, match='llm.enabled'):
        Executor(config)


def test_disabled_direct_and_private_entry_points_never_spend():
    paper, reserved = make_sample_paper(), guard()
    policy = dict(params(), enabled=False)
    paper.generate_tldr(None, policy, reserved)
    with pytest.raises(llm.SummaryUnavailable):
        paper._generate_tldr_with_llm(None, policy, reserved)
    assert paper.tldr_error is None and paper.tldr_error_reason == 'llm_disabled'
    assert paper.tldr_attempts == 0 and reserved.remaining == CAP


def test_all_missing_or_whitespace_inputs_do_not_reserve_a_day(pipeline, monkeypatch):
    pipeline.llm.enabled = True
    executor = Executor(pipeline)
    papers = [make_sample_paper(title=f'Offline {i}', url=f'https://example.org/{i}', abstract=text)
              for i, text in enumerate((' ', ''))]
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: papers)
    monkeypatch.setattr('zotero_arxiv_daily.executor.prepare_budget', lambda cfg: pytest.fail('No input must not reserve'))
    monkeypatch.setattr('zotero_arxiv_daily.executor.OpenAI', lambda **kw: pytest.fail('No input must not create client'))
    executor.run()
    restored = State(pipeline.state.path).pending('email')
    assert len(restored) == 2 and all(p.tldr_error_reason == 'input_unavailable' for p in restored)


def test_local_client_setup_failure_keeps_ranked_papers_without_reserving(pipeline, monkeypatch):
    pipeline.llm.enabled = True
    executor = Executor(pipeline)
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: [make_sample_paper()])
    monkeypatch.setattr('zotero_arxiv_daily.executor.prepare_budget', lambda cfg: pytest.fail('Setup failure must not reserve'))
    def broken(**kwargs):
        raise ValueError('PRIVATE_CREDENTIAL_MARKER')
    monkeypatch.setattr('zotero_arxiv_daily.executor.OpenAI', broken)
    logs = []
    sink = logger.add(lambda msg: logs.append(str(msg)))
    try:
        executor.run()
    finally:
        logger.remove(sink)
    paper = State(pipeline.state.path).pending('email')[0]
    assert paper.tldr == paper.abstract and paper.tldr_error_reason == 'client_setup_failed'
    assert 'PRIVATE_CREDENTIAL_MARKER' not in ''.join(logs) + json.dumps(paper_dict(paper))


def test_reserved_day_reason_survives_pipeline_state_and_email(pipeline, monkeypatch):
    pipeline.llm.enabled = True
    executor = Executor(pipeline)
    papers = [make_sample_paper(title=f'Offline {i}', url=f'https://example.org/{i}') for i in range(3)]
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: papers)
    def already_reserved(cfg):
        raise budget.BudgetUnavailable('UTC day already reserved', reason='daily_budget_reserved',
                                       day='2026-10-06', retry_at='2026-10-07T00:00:00+00:00')
    monkeypatch.setattr('zotero_arxiv_daily.executor.prepare_budget', already_reserved)
    monkeypatch.setattr('zotero_arxiv_daily.executor.OpenAI', lambda **kw: SimpleNamespace(close=lambda: None))
    executor.run()
    restored = State(pipeline.state.path).pending('email')
    plain = email_plain_text(render_email(restored))
    assert '0/3 generated' in plain and 'budget already reserved by an earlier run' in plain
    assert 'UTC 2026-10-06' in plain and 'reservation preserved' in plain
    assert all(p.tldr_budget_day == '2026-10-06' and p.tldr_attempts == 0 for p in restored)
    assert all(load_paper(paper_dict(p)).tldr_retry_at == '2026-10-07T00:00:00+00:00' for p in restored)


def test_partial_success_and_delivery_resume_reuse_stored_results_without_paid_calls(pipeline, monkeypatch):
    clock(monkeypatch)
    with open_dict(pipeline):
        pipeline.llm.enabled = True
        pipeline.llm.generation_kwargs.model = MODEL
        pipeline.output.email.enabled = True
        pipeline.output.rss.enabled = False
        pipeline.state.enabled = True
    calls, reservations, sends = [], [], []
    def handler(request):
        calls.append(True)
        if len(calls) == 2:
            return httpx.Response(503, json={'error': {'message': 'offline'}})
        data = good_json()
        if len(calls) == 4:
            data['choices'][0]['finish_reason'] = 'length'
        return httpx.Response(200, json=data)
    monkeypatch.setattr('zotero_arxiv_daily.executor.OpenAI', lambda **kw: client(handler))
    def reserve(cfg):
        reservations.append(True)
        return guard()
    monkeypatch.setattr('zotero_arxiv_daily.executor.prepare_budget', reserve)
    def smtp_failure(*args):
        raise OSError('offline SMTP failure')
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email', smtp_failure)
    papers = [make_sample_paper(title=f'Offline {i}', url=f'https://example.org/{i}') for i in range(3)]
    first = Executor(pipeline)
    monkeypatch.setattr(first.retrievers['arxiv'], 'retrieve_papers', lambda: papers)
    with pytest.raises(RuntimeError, match='offline SMTP failure'):
        first.run()
    pending = State(pipeline.state.path).pending('email')
    assert len(calls) == 4 and len(reservations) == 1
    assert sum(p.has_ai_summary for p in pending) == 2 and sum(p.tldr_status == 'fallback' for p in pending) == 1
    assert sorted(p.tldr_attempts for p in pending) == [1, 1, 2]
    repeat = Executor(pipeline)
    monkeypatch.setattr(repeat.retrievers['arxiv'], 'retrieve_papers', lambda: [])
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email', lambda *args: sends.append(args[1]))
    repeat.run()
    assert len(calls) == 4 and len(reservations) == 1 and len(sends) == 1
    assert '2/3 generated' in sends[0] and not State(pipeline.state.path).pending('email')


@pytest.mark.parametrize('cap,cost', [('NaN', '.001'), ('.30', '-.01'), ('.30', 'NaN'),
                                    ('0', '.001'), ('.30', '0'), ('.30', 'Infinity')])
def test_invalid_attempt_bounds_cannot_authorize_any_dispatch(cap, cost):
    with pytest.raises(budget.BudgetUnavailable, match='reservation bounds'):
        budget.BudgetRequests(cap, cost, budget.utc_day())


@pytest.mark.parametrize('mismatch', ['model', 'endpoint'])
def test_dispatch_is_bound_to_the_reserved_provider_and_model(mismatch):
    calls = []
    reserved = budget.BudgetRequests(CAP, COST, budget.utc_day(), expected_model=MODEL,
                                    expected_endpoint='https://api.siliconflow.cn/v1')
    sdk = client(lambda request: calls.append(True))
    policy = params()
    if mismatch == 'model':
        policy['generation_kwargs']['model'] = 'unreserved-model'
    else:
        sdk.base_url = 'https://different-provider.example/v1'
    paper = make_sample_paper()
    with sdk:
        paper.generate_tldr(sdk, policy, reserved)
    assert not calls and paper.tldr_attempts == 0 and reserved.remaining == 0
    assert paper.tldr_error_reason == 'billing_unverified' and paper.tldr == paper.abstract


def test_provider_explicit_no_retry_header_is_respected(monkeypatch):
    clock(monkeypatch)
    calls = []
    def handler(request):
        calls.append(True)
        return httpx.Response(503, headers={'x-should-retry': 'false'}, json={'error': {'message': 'offline'}})
    reserved = guard()
    papers = [make_sample_paper(), make_sample_paper()]
    with client(handler) as sdk:
        for paper in papers:
            paper.generate_tldr(sdk, params(), reserved)
    assert len(calls) == 1 and reserved.remaining == CAP - COST
    assert all(p.tldr_error_reason == 'retry_not_permitted' for p in papers)


def test_workflow_state_save_preserves_the_durable_budget(tmp_path, monkeypatch):
    from scripts.workflow_state import save
    git, checkout, remote = make_git(tmp_path, monkeypatch)
    budget.reserve_day(CAP, '2026-10-06')
    before = git('rev-parse', 'refs/heads/paper-state', cwd=remote).decode().strip()
    ledger = git('show', before + ':llm_budget.json')
    (checkout / 'data').mkdir()
    (checkout / 'data/recommendations.json').write_text('{"version":1,"records":{"offline":"pending"}}')
    save()
    after = git('rev-parse', 'refs/heads/paper-state', cwd=remote).decode().strip()
    assert git('show', after + ':llm_budget.json') == ledger


@pytest.mark.parametrize('key,value', [('max_attempts', 0), ('max_attempts', 2.5),
                                     ('timeout_seconds', 600), ('timeout_seconds', float('nan')),
                                     ('failure_threshold', 1), ('max_retry_wait_seconds', float('inf'))])
def test_invalid_policy_fails_before_provider_setup(config, key, value):
    config.llm.request[key] = value
    with pytest.raises(ValueError, match='llm.request'):
        Executor(config)
