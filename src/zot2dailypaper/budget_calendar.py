"""Calendar identities and conservative validation for the UTC -> Singapore cutover."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOCAL_TIMEZONE = 'Asia/Singapore'
LOCAL_NAMESPACE = 'tz:v2:Asia/Singapore:'


class LedgerInvalid(ValueError):
    pass


@dataclass(frozen=True)
class BudgetWindow:
    day: str
    timezone_name: str
    start: datetime
    end: datetime

    @property
    def key(self):
        return LOCAL_NAMESPACE + self.day if self.timezone_name == LOCAL_TIMEZONE else 'utc:v1:' + self.day


def timestamp(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        instant = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError
        return instant.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise LedgerInvalid('Invalid or naive budget timestamp; preserve the ledger') from exc


def window_for_day(day, timezone_name):
    try:
        parsed = date.fromisoformat(day)
        if parsed.isoformat() != day or timezone_name not in ('UTC', LOCAL_TIMEZONE):
            raise ValueError
        zone = timezone.utc if timezone_name == 'UTC' else ZoneInfo(LOCAL_TIMEZONE)
        start = datetime.combine(parsed, time(), zone).astimezone(timezone.utc)
        end = datetime.combine(parsed + timedelta(days=1), time(), zone).astimezone(timezone.utc)
        return BudgetWindow(day, timezone_name, start, end)
    except (TypeError, ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
        raise LedgerInvalid('Invalid budget date/timezone; no allowance authorized') from exc


def window_at(instant, timezone_name=LOCAL_TIMEZONE):
    if timezone_name != LOCAL_TIMEZONE:
        raise LedgerInvalid('Only the approved Asia/Singapore budget clock is supported')
    try:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise LedgerInvalid('Naive budget clock; no allowance authorized')
        day = instant.astimezone(ZoneInfo(LOCAL_TIMEZONE)).date().isoformat()
    except (AttributeError, ZoneInfoNotFoundError) as exc:
        raise LedgerInvalid('Budget timezone data unavailable') from exc
    return window_for_day(day, timezone_name)


def overlaps(left, right):
    return max(left.start, right.start) < min(left.end, right.end)


def _amount(record):
    try:
        amount = Decimal(str(record['reserved_cny']))
        if not amount.is_finite() or amount <= 0 or record.get('policy') != 'whole-day-no-refund':
            raise ValueError
    except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
        raise LedgerInvalid('Invalid budget amount/policy; preserve the ledger') from exc


def validate_legacy(data, now):
    if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1
            or not isinstance(data.get('days'), dict)):
        raise LedgerInvalid('Invalid legacy budget ledger')
    if 'initialized_at' in data and timestamp(data['initialized_at']) > now:
        raise LedgerInvalid('Future budget initialization timestamp')
    for day, record in data['days'].items():
        window = window_for_day(day, 'UTC')
        if not isinstance(record, dict):
            raise LedgerInvalid('Invalid legacy reservation')
        _amount(record)
        created = timestamp(record.get('created_at'))
        if not window.start <= created < window.end or created > now:
            raise LedgerInvalid('Legacy timestamp contradicts its UTC date or is in the future')


def legacy_digest(data):
    try:
        return sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError) as exc:
        raise LedgerInvalid('Invalid legacy ledger serialization') from exc


def migrated_ledger(data, parent, now):
    validate_legacy(data, now)
    # Preserve every field and exact amount/date string; references never become refunds.
    legacy = json.loads(json.dumps(data, allow_nan=False))
    return {'version': 2, 'timezone': LOCAL_TIMEZONE, 'legacy_utc': legacy, 'claims': {}, 'claims_sha256': legacy_digest({}),
            'cutover': {'created_at': now.isoformat(), 'source_commit': parent,
                        'legacy_sha256': legacy_digest(legacy),
                        'policy': 'preserve-whole-UTC-windows-no-refund'}}


def overlapping_legacy(data, window):
    return ['utc:v1:' + day for day in data['days'] if overlaps(window_for_day(day, 'UTC'), window)]


def validate_local(data, now):
    if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 2
            or data.get('timezone') != LOCAL_TIMEZONE or not isinstance(data.get('claims'), dict)
            or not isinstance(data.get('cutover'), dict)):
        raise LedgerInvalid('Unsupported or corrupt local budget ledger')
    cutover = data['cutover']
    created = timestamp(cutover.get('created_at'))
    if (created > now or cutover.get('policy') != 'preserve-whole-UTC-windows-no-refund'
            or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', cutover.get('source_commit', ''))):
        raise LedgerInvalid('Invalid budget cutover marker')
    legacy = data.get('legacy_utc')
    validate_legacy(legacy, created)
    if cutover.get('legacy_sha256') != legacy_digest(legacy):
        raise LedgerInvalid('Legacy reservations changed after cutover; preserve and investigate')
    if data.get('claims_sha256') != legacy_digest(data['claims']):
        raise LedgerInvalid('Local reservation snapshot changed unexpectedly; preserve and investigate')
    for key, record in data['claims'].items():
        if not isinstance(key, str) or not key.startswith(LOCAL_NAMESPACE) or not isinstance(record, dict):
            raise LedgerInvalid('Invalid local reservation identity')
        window = window_for_day(key[len(LOCAL_NAMESPACE):], LOCAL_TIMEZONE)
        _amount(record)
        if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', record.get('reservation_id', '')):
            raise LedgerInvalid('Invalid unique reservation identity')
        admitted = timestamp(record.get('created_at'))
        if (record.get('timezone') != LOCAL_TIMEZONE or record.get('day') != window.day
                or timestamp(record.get('window_start_utc')) != window.start
                or timestamp(record.get('window_end_utc')) != window.end
                or not window.start <= admitted < window.end or admitted < created or admitted > now
                or overlapping_legacy(legacy, window)):
            raise LedgerInvalid('Local reservation contradicts its clock/window or overlaps a legacy claim')


def next_unblocked_window(data, current):
    # Valid ledgers cannot contain future claims. At most today's UTC legacy
    # window can additionally block tomorrow locally, but check rather than assume.
    candidate = current
    for _ in range(4):
        candidate = window_for_day((date.fromisoformat(candidate.day) + timedelta(days=1)).isoformat(), LOCAL_TIMEZONE)
        if candidate.key not in data['claims'] and not overlapping_legacy(data['legacy_utc'], candidate):
            return candidate.start.isoformat()
    raise LedgerInvalid('Unexpected future reservation windows')
