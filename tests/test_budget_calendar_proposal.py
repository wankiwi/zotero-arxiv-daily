"""Independent calendar/overlap proofs complement the real Git migration tests."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest


SINGAPORE = ZoneInfo('Asia/Singapore')


def proposed_window(day, zone):
    start = datetime.combine(date.fromisoformat(day), time(), zone)
    end = datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(), zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def overlaps(left, right):
    return max(left[0], right[0]) < min(left[1], right[1])


@pytest.mark.parametrize('local_day,blocked', [('2026-10-05', False), ('2026-10-06', True),
                                             ('2026-10-07', True), ('2026-10-08', False)])
def test_legacy_utc_claim_blocks_both_overlapping_local_days_without_refund(local_day, blocked):
    old_claim = proposed_window('2026-10-06', timezone.utc)
    new_day = proposed_window(local_day, SINGAPORE)
    assert overlaps(old_claim, new_day) is blocked


def test_actual_oct7_pair_has_different_singapore_days_but_same_utc_day():
    old = datetime.fromisoformat('2026-10-06T01:09:20+00:00')
    latest = datetime.fromisoformat('2026-10-06T23:16:27+00:00')
    assert old.date() == latest.date()
    assert old.astimezone(SINGAPORE).date().isoformat() == '2026-10-06'
    assert latest.astimezone(SINGAPORE).date().isoformat() == '2026-10-07'
    # The benefit applies after migration. An existing uncertain UTC claim
    # still blocks both overlapping local dates during a conservative cutover.
    assert overlaps(proposed_window(old.date().isoformat(), timezone.utc),
                    proposed_window(latest.astimezone(SINGAPORE).date().isoformat(), SINGAPORE))


def test_local_day_still_collides_after_large_schedule_delay_or_rerun():
    first = datetime.fromisoformat('2026-10-06T19:17:00+00:00')
    delayed = datetime.fromisoformat('2026-10-07T15:55:00+00:00')
    assert first.astimezone(SINGAPORE).date() == delayed.astimezone(SINGAPORE).date()


def test_cutover_namespaces_cannot_be_treated_as_independent_allowances():
    claims = {'utc:v1:2026-10-06': {'reserved_cny': '0.30', 'policy': 'whole-day-no-refund'}}
    before = repr(claims)
    local_key = 'tz:v2:Asia/Singapore:2026-10-07'
    inherited = [key for key in claims if overlaps(proposed_window(key.rsplit(':', 1)[1], timezone.utc),
                                                  proposed_window(local_key.rsplit(':', 1)[1], SINGAPORE))]
    assert inherited == ['utc:v1:2026-10-06']
    assert repr(claims) == before  # No rekey/reset/refund, even for a crashed legacy run.
    assert local_key not in claims  # No second full-cap claim for the same uncertainty window.


def test_boundary_is_half_open_and_next_unblocked_day_is_not_reserved_early():
    old = proposed_window('2026-10-06', timezone.utc)
    assert old[1] == datetime.fromisoformat('2026-10-07T00:00:00+00:00')
    local = proposed_window('2026-10-08', SINGAPORE)
    assert local[0] == datetime.fromisoformat('2026-10-07T16:00:00+00:00')
    assert not overlaps(old, local)
    at_boundary = local[0].astimezone(SINGAPORE)
    before_boundary = (local[0] - timedelta(microseconds=1)).astimezone(SINGAPORE)
    assert at_boundary.date().isoformat() == '2026-10-08'
    assert before_boundary.date().isoformat() == '2026-10-07'
