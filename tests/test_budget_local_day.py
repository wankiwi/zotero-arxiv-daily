"""Local-calendar migration with synthetic ledgers, fake time and local bare Git only."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import subprocess
import sys

import pytest

from tests.test_budget import make_git
from zot2dailypaper import budget
from zot2dailypaper.budget_calendar import LOCAL_NAMESPACE, LOCAL_TIMEZONE, window_at, legacy_digest

CAP, COST = Decimal('.30'), Decimal('.00432')


def fixture_ledger(tmp_path, monkeypatch, days=None):
    git, checkout, remote = make_git(tmp_path, monkeypatch)
    data = {'version': 1, 'initialized_at': '2026-10-01T00:00:00+00:00', 'days': days or {}}
    publish(git, checkout, data)
    return git, checkout, remote, data


def publish(git, checkout, data):
    git('fetch', 'origin', 'paper-state')
    git('merge', '--ff-only', 'FETCH_HEAD')
    (checkout / 'llm_budget.json').write_text(json.dumps(data))
    git('add', 'llm_budget.json')
    git('commit', '--allow-empty', '-m', 'Synthetic budget fixture')
    git('push', 'origin', 'HEAD:paper-state')


def snapshot(git, remote):
    head = git('rev-parse', 'refs/heads/paper-state', cwd=remote).decode().strip()
    return head, json.loads(git('show', head + ':llm_budget.json'))


def clock(monkeypatch, instant='2026-10-07T03:00:00+00:00'):
    current = [datetime.fromisoformat(instant)]
    monkeypatch.setattr(budget, 'budget_now', lambda: current[0])
    return current


def utc_claim(created='2026-10-06T01:09:20.177522+00:00', cap='0.3'):
    return {'created_at': created, 'reserved_cny': cap, 'policy': 'whole-day-no-refund'}


def test_actual_utc_claim_migrates_without_reclaim_and_blocks_overlapping_day(tmp_path, monkeypatch):
    git, checkout, remote, original = fixture_ledger(tmp_path, monkeypatch,
                                                   {'2026-10-06': utc_claim(), '2026-10-04': utc_claim('2026-10-04T22:57:31+00:00', '0.20')})
    current = clock(monkeypatch)
    with pytest.raises(budget.BudgetUnavailable) as rejected:
        budget.reserve_local_day(CAP)
    assert rejected.value.reason == 'legacy_budget_overlap'
    assert rejected.value.day == '2026-10-07' and rejected.value.timezone_name == LOCAL_TIMEZONE
    assert rejected.value.retry_at == '2026-10-07T16:00:00+00:00'
    head, ledger = snapshot(git, remote)
    assert ledger['version'] == 2 and ledger['legacy_utc'] == original and not ledger['claims']
    history = git('show', head + ':recommendations.json')
    assert b'preserved' in history
    # The old v1 reservation entry point cannot start another UTC allowance.
    with pytest.raises(budget.BudgetUnavailable, match='Invalid budget ledger'):
        budget.reserve_day(CAP, '2026-10-07')
    assert snapshot(git, remote)[0] == head
    current[0] = datetime.fromisoformat('2026-10-07T16:00:00+00:00')
    window = budget.reserve_local_day(CAP)
    assert window.day == '2026-10-08' and window.start == current[0]
    after, ledger = snapshot(git, remote)
    assert ledger['legacy_utc'] == original
    assert ledger['claims'][LOCAL_NAMESPACE + '2026-10-08']['reserved_cny'] == '0.30'
    assert git('show', after + ':recommendations.json') == history
    with pytest.raises(budget.BudgetUnavailable) as rerun:
        budget.reserve_local_day(Decimal('.40'))
    assert rerun.value.reason == 'daily_budget_reserved' and snapshot(git, remote)[0] == after


def test_inflight_old_guard_is_covered_by_overlap_fence(tmp_path, monkeypatch):
    git, checkout, remote, original = fixture_ledger(tmp_path, monkeypatch,
                                                   {'2026-10-07': utc_claim('2026-10-07T01:00:00+00:00')})
    current = clock(monkeypatch)
    old = budget.BudgetRequests(CAP, COST, '2026-10-07')
    monkeypatch.setattr(budget, 'utc_day', lambda: '2026-10-07')
    with pytest.raises(budget.BudgetUnavailable, match='Legacy UTC reservation'):
        budget.reserve_local_day(CAP)
    calls = []
    old.call(lambda: calls.append('old admitted operation'))
    assert len(calls) == 1 and old.remaining == CAP - COST
    current[0] = datetime.fromisoformat('2026-10-07T16:00:00+00:00')  # Singapore Oct 8 is STILL covered.
    with pytest.raises(budget.BudgetUnavailable) as blocked:
        budget.reserve_local_day(CAP)
    assert blocked.value.reason == 'legacy_budget_overlap'
    assert blocked.value.retry_at == '2026-10-08T16:00:00+00:00'
    assert snapshot(git, remote)[1]['legacy_utc'] == original


@pytest.mark.parametrize('instant,local_day', [('2026-10-06T01:00:00+00:00', '2026-10-06'),
                                            ('2026-10-06T23:00:00+00:00', '2026-10-07')])
def test_clean_clock_identity_matches_singapore_calendar(instant, local_day):
    window = window_at(datetime.fromisoformat(instant))
    assert window.day == local_day and window.key == LOCAL_NAMESPACE + local_day


def test_clean_cutover_allows_different_local_days_in_one_utc_day(tmp_path, monkeypatch):
    git, checkout, remote, original = fixture_ledger(tmp_path, monkeypatch)
    current = clock(monkeypatch, '2026-10-06T01:09:20+00:00')
    first = budget.reserve_local_day(CAP)
    current[0] = datetime.fromisoformat('2026-10-06T23:16:27+00:00')
    second = budget.reserve_local_day(CAP)
    assert first.day == '2026-10-06' and second.day == '2026-10-07'
    ledger = snapshot(git, remote)[1]
    assert len(ledger['claims']) == 2 and ledger['legacy_utc'] == original
    assert all(c['reserved_cny'] == '0.30' for c in ledger['claims'].values())


def test_delays_can_still_collide_in_one_local_day(tmp_path, monkeypatch):
    git, checkout, remote, _ = fixture_ledger(tmp_path, monkeypatch)
    current = clock(monkeypatch, '2026-10-06T19:17:00+00:00')
    budget.reserve_local_day(CAP)
    before = snapshot(git, remote)[0]
    current[0] = datetime.fromisoformat('2026-10-07T15:55:00+00:00')
    with pytest.raises(budget.BudgetUnavailable) as blocked:
        budget.reserve_local_day(CAP)
    assert blocked.value.reason == 'daily_budget_reserved' and snapshot(git, remote)[0] == before


@pytest.mark.parametrize('bad', [None, 'garbage', '2026-10-06T01:00:00',
                               '2026-10-07T01:00:00+00:00', '2026-10-08T01:00:00+00:00'])
def test_bad_legacy_timestamps_never_migrate_or_authorize(tmp_path, monkeypatch, bad):
    git, checkout, remote, _ = fixture_ledger(tmp_path, monkeypatch, {'2026-10-06': utc_claim(bad)})
    clock(monkeypatch)
    before = snapshot(git, remote)[0]
    with pytest.raises(budget.BudgetUnavailable) as invalid:
        budget.reserve_local_day(CAP)
    assert invalid.value.reason == 'budget_ledger_invalid' and snapshot(git, remote)[0] == before


@pytest.mark.parametrize('bad', ['legacy_amount', 'legacy_digest', 'clock', 'key', 'window',
                               'claim_timestamp', 'cutover_timestamp', 'cutover_source', 'deleted_claim'])
def test_corrupt_v2_identity_timestamps_or_old_costs_fail_closed(tmp_path, monkeypatch, bad):
    git, checkout, remote, _ = fixture_ledger(tmp_path, monkeypatch, {'2026-10-04': utc_claim('2026-10-04T22:00:00+00:00', '0.20')})
    current = clock(monkeypatch, '2026-10-07T16:00:00+00:00')
    budget.reserve_local_day(CAP)
    _, ledger = snapshot(git, remote)
    claim = ledger['claims'][LOCAL_NAMESPACE + '2026-10-08']
    if bad == 'legacy_amount':
        ledger['legacy_utc']['days']['2026-10-04']['reserved_cny'] = '0.00'
    elif bad == 'legacy_digest':
        ledger['legacy_utc']['days'] = {}
    elif bad == 'clock':
        ledger['timezone'] = 'Asia/Shanghai'
    elif bad == 'key':
        ledger['claims']['utc:v1:2026-10-08'] = ledger['claims'].pop(LOCAL_NAMESPACE + '2026-10-08')
    elif bad == 'window':
        claim['window_start_utc'] = '2026-10-08T00:00:00+00:00'
    elif bad == 'claim_timestamp':
        claim['created_at'] = '2026-10-07T16:00:00'
    elif bad == 'cutover_timestamp':
        ledger['cutover']['created_at'] = '2099-01-01T00:00:00+00:00'
    elif bad == 'deleted_claim':
        ledger['claims'] = {}
    else:
        ledger['cutover']['source_commit'] = None
    if bad in ('key', 'window', 'claim_timestamp'):
        ledger['claims_sha256'] = legacy_digest(ledger['claims'])  # Identity/timestamp checks also survive a rewritten digest.
    publish(git, checkout, ledger)
    current[0] = datetime.fromisoformat('2026-10-08T16:00:00+00:00')
    before = snapshot(git, remote)[0]
    with pytest.raises(budget.BudgetUnavailable) as invalid:
        budget.reserve_local_day(CAP)
    assert invalid.value.reason == 'budget_ledger_invalid' and snapshot(git, remote)[0] == before


@pytest.mark.parametrize('winner', ['utc', 'local'])
def test_independent_old_new_writer_interleavings_grant_only_one(tmp_path, monkeypatch, winner):
    git, checkout, remote, _ = fixture_ledger(tmp_path, monkeypatch)
    clock(monkeypatch)
    # Freeze both old datetime.now() and new budget_now(), including subprocesses.
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat('2026-10-07T03:00:00+00:00').astimezone(tz or timezone.utc)
    monkeypatch.setattr(budget, 'datetime', Fixed)
    second = tmp_path / 'second'
    git('clone', str(remote), str(second))
    code = ("from datetime import datetime, timezone\nfrom decimal import Decimal\n"
            "from zot2dailypaper import budget\n"
            "class Fixed(datetime):\n @classmethod\n def now(cls,tz=None):\n"
            "  return datetime.fromisoformat('2026-10-07T03:00:00+00:00').astimezone(tz or timezone.utc)\n"
            "budget.datetime=Fixed\n"
            + ("budget.reserve_day(Decimal('.30'))\n" if winner == 'utc' else "budget.reserve_local_day(Decimal('.30'))\n"))
    push = budget._push_ledger
    interleaved = []
    def competing(parent, data, message):
        if not interleaved:
            interleaved.append(True)
            subprocess.run([sys.executable, '-c', code], cwd=second, check=True,
                           capture_output=True, timeout=30, env=os.environ.copy())
        return push(parent, data, message)
    monkeypatch.setattr(budget, '_push_ledger', competing)
    with pytest.raises(budget.BudgetUnavailable):
        (budget.reserve_local_day(CAP) if winner == 'utc' else budget.reserve_day(CAP))
    ledger = snapshot(git, remote)[1]
    assert ledger['version'] == 2 and len(interleaved) == 1
    assert len(ledger['legacy_utc']['days']) + len(ledger['claims']) == 1
    assert (len(ledger['legacy_utc']['days']) == 1) is (winner == 'utc')


def test_ambiguous_successful_push_never_authorizes_or_reclaims_on_rerun(tmp_path, monkeypatch):
    git, checkout, remote, _ = fixture_ledger(tmp_path, monkeypatch)
    clock(monkeypatch)
    real = budget._push_ledger
    def uncertain(parent, data, message):
        assert real(parent, data, message)
        raise budget.BudgetUnavailable('Synthetic ambiguous push')
    monkeypatch.setattr(budget, '_push_ledger', uncertain)
    with pytest.raises(budget.BudgetUnavailable, match='ambiguous'):
        budget.reserve_local_day(CAP)
    before = snapshot(git, remote)[0]
    monkeypatch.setattr(budget, '_push_ledger', real)
    with pytest.raises(budget.BudgetUnavailable) as denied:
        budget.reserve_local_day(CAP)
    assert denied.value.reason == 'daily_budget_reserved' and snapshot(git, remote)[0] == before


def test_local_guard_uses_utc16_midnight_and_does_not_roll_over(monkeypatch):
    current = clock(monkeypatch, '2026-10-07T15:59:59+00:00')
    window = window_at(current[0])
    guarded = budget.BudgetRequests(CAP, COST, window.day, window=window)
    calls = []
    guarded.call(lambda: calls.append(True))
    current[0] = datetime.fromisoformat('2026-10-07T16:00:00+00:00')
    with pytest.raises(budget.BudgetUnavailable) as ended:
        guarded.call(lambda: calls.append(True))
    assert ended.value.reason == 'day_changed' and len(calls) == 1 and guarded.remaining == CAP - COST
    assert guarded.timezone_name == LOCAL_TIMEZONE


def test_utc_midnight_does_not_end_same_local_day(monkeypatch):
    current = clock(monkeypatch, '2026-10-06T23:59:59+00:00')
    window = window_at(current[0])
    guarded = budget.BudgetRequests(CAP, COST, window.day, window=window)
    guarded.call(lambda: None)
    current[0] = datetime.fromisoformat('2026-10-07T00:00:01+00:00')
    guarded.call(lambda: None)
    assert guarded.remaining == CAP - COST * 2 and guarded.day == '2026-10-07'


def test_prepare_budget_binds_local_window_and_preserves_config_amount(config, monkeypatch):
    current = clock(monkeypatch)
    config.llm.api.base_url = 'https://api.siliconflow.cn/v1'
    config.llm.generation_kwargs.model = 'deepseek-ai/DeepSeek-V4-Flash'
    seen = []
    def reserve(cap, zone):
        seen.append((cap, zone))
        return window_at(current[0])
    monkeypatch.setattr(budget, 'reserve_local_day', reserve)
    guarded = budget.prepare_budget(config.llm)
    assert seen == [(CAP, LOCAL_TIMEZONE)] and guarded.day == '2026-10-07'
    assert guarded.timezone_name == LOCAL_TIMEZONE and guarded.remaining == CAP
    assert guarded.expected_model == config.llm.generation_kwargs.model


def test_local_cutover_failed_push_cannot_authorize(tmp_path, monkeypatch):
    git, checkout, remote, _ = fixture_ledger(tmp_path, monkeypatch)
    clock(monkeypatch)
    before = snapshot(git, remote)[0]
    hook = remote / 'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    with pytest.raises(budget.BudgetUnavailable, match='Could not persist'):
        budget.reserve_local_day(CAP)
    assert snapshot(git, remote)[0] == before


def test_slow_successful_push_crossing_local_midnight_returns_guard_that_cannot_dispatch(tmp_path, monkeypatch):
    git, checkout, remote, _ = fixture_ledger(tmp_path, monkeypatch)
    current = clock(monkeypatch, '2026-10-07T15:59:59+00:00')
    real = budget._push_ledger
    def late(parent, data, message):
        success = real(parent, data, message)
        current[0] = datetime.fromisoformat('2026-10-07T16:00:00+00:00')
        return success
    monkeypatch.setattr(budget, '_push_ledger', late)
    window = budget.reserve_local_day(CAP)
    guarded = budget.BudgetRequests(CAP, COST, window.day, window=window)
    with pytest.raises(budget.BudgetUnavailable) as expired:
        guarded.call(lambda: pytest.fail('Expired reservation must not dispatch'))
    assert expired.value.reason == 'day_changed' and guarded.remaining == CAP
    assert LOCAL_NAMESPACE + '2026-10-07' in snapshot(git, remote)[1]['claims']


def test_two_independent_local_processes_grant_only_one(tmp_path, monkeypatch):
    git, checkout, remote, _ = fixture_ledger(tmp_path, monkeypatch)
    second = tmp_path / 'second'
    git('clone', str(remote), str(second))
    # Identical Git commit time, owner, and admission time reproduce the subtle
    # "already up-to-date" double grant. The unique reservation ID must prevent it.
    monkeypatch.setenv('GIT_AUTHOR_DATE', '2026-10-07T03:00:00+00:00')
    monkeypatch.setenv('GIT_COMMITTER_DATE', '2026-10-07T03:00:00+00:00')
    monkeypatch.setenv('GITHUB_RUN_ID', '1000')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    code = ("from datetime import datetime\nfrom decimal import Decimal\nfrom zot2dailypaper import budget\n"
            "budget.budget_now=lambda:datetime.fromisoformat('2026-10-07T03:00:00+00:00')\n"
            "budget.reserve_local_day(Decimal('.30'))\n")
    jobs = [subprocess.Popen([sys.executable, '-c', code], cwd=path, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=os.environ.copy()) for path in (checkout, second)]
    outputs = [job.communicate(timeout=30) for job in jobs]
    assert [job.returncode for job in jobs].count(0) == 1, outputs
    assert len(snapshot(git, remote)[1]['claims']) == 1


def test_cooldown_crossing_singapore_midnight_never_sends_second_attempt(monkeypatch):
    import httpx
    from datetime import timedelta
    from tests.test_summary_reliability import client, params
    from tests.canned_responses import make_sample_paper
    from zot2dailypaper import llm, protocol
    current = clock(monkeypatch, '2026-10-07T15:59:59+00:00')
    elapsed = [0.0]
    def sleep(seconds):
        elapsed[0] += seconds
        current[0] += timedelta(seconds=seconds)
    monkeypatch.setattr(llm, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(llm, 'sleep', sleep)
    monkeypatch.setattr(protocol, 'sleep', sleep)
    window = window_at(current[0])
    guarded = budget.BudgetRequests(CAP, COST, window.day, window=window)
    calls = []
    def handler(request):
        calls.append(True)
        return httpx.Response(503, headers={'Retry-After': '3'}, json={'error': {'message': 'offline'}})
    paper = make_sample_paper()
    with client(handler) as sdk:
        paper.generate_tldr(sdk, params(), guarded)
    assert len(calls) == 1 and guarded.remaining == CAP - COST
    assert paper.tldr_error_reason == 'day_changed' and paper.tldr_budget_timezone == LOCAL_TIMEZONE
    assert paper.tldr == paper.abstract


def test_client_setup_crossing_local_midnight_is_checked_again_before_dispatch(monkeypatch):
    from types import SimpleNamespace
    from tests.test_summary_reliability import params
    from tests.canned_responses import make_sample_paper
    current = clock(monkeypatch, '2026-10-07T15:59:59+00:00')
    window = window_at(current[0])
    guarded = budget.BudgetRequests(CAP, COST, window.day, window=window)
    calls = []
    sdk = SimpleNamespace(max_retries=0, chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: calls.append(True))))
    def setup(**kwargs):
        current[0] = datetime.fromisoformat('2026-10-07T16:00:00+00:00')
        return sdk
    sdk.with_options = setup
    paper = make_sample_paper()
    paper.generate_tldr(sdk, params(), guarded)
    assert not calls and paper.tldr_attempts == 0 and paper.tldr_error_reason == 'day_changed'
