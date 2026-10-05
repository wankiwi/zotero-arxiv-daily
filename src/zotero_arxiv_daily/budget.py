"""Fail-closed daily CNY budget with a durable pre-call Git reservation.

The entire daily allowance is consumed by one run, including failed calls and
unused balance. This intentionally trades utilization for crash safety.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.parse import urlsplit
from .llm import ModelRequests
from loguru import logger

class BudgetUnavailable(RuntimeError):
    def __init__(self, message, *, reason='budget_guard_unavailable'):
        super().__init__(message)
        self.reason = reason

# Reviewed public provider contract: explicit non-thinking, peak CNY tariff.
# Different endpoints/models fail closed; stale pricing warns and continues.
VERIFIED_PRICING = {
    'host': 'api.siliconflow.cn',
    'model': 'deepseek-ai/DeepSeek-V4-Flash',
    'input_cny_per_million': '3', 'output_cny_per_million': '9',
    'verified_on': '2026-10-01', 'valid_through': '2026-10-08',
    'pricing_source': 'https://www.siliconflow.cn/pricing',
    'model_contract': 'https://api-docs.siliconflow.cn/docs/api/chat-completions-post',
}
MAX_DAILY_CNY = Decimal('0.30')
PROMPT_BYTES = 768
SYSTEM_BYTES = 256
FRAMING_TOKENS = 128
MAX_OUTPUT_TOKENS = 96


def utc_day():
    return datetime.now(timezone.utc).date().isoformat()


def pricing_warning():
    if VERIFIED_PRICING and utc_day() > VERIFIED_PRICING['valid_through']:
        return ('LLM 价格复核已过期：继续按最后复核费率估算并执行每日 ¥0.30 记账额度；'
                '若供应商涨价，实际费用可能超过估算及 ¥0.30，请尽快复核价格。'
                ' Stale LLM pricing: estimates may understate actual charges.')
    return ''


def budget_plan(config):
    budget = config.get('budget', {})
    if budget.get('enabled', True) is not True:
        raise BudgetUnavailable('Budget guard disabled: paid calls prohibited, not unlimited')
    cap = Decimal(str(budget.get('daily_cny', '0.30')))
    if not cap.is_finite() or not 0 < cap <= MAX_DAILY_CNY:
        raise BudgetUnavailable('Daily CNY budget must be positive and no more than 0.30')
    if VERIFIED_PRICING is None:
        raise BudgetUnavailable('Exact provider pricing/non-thinking token bound is not verified; paid calls disabled')
    pricing = VERIFIED_PRICING
    try:
        base = urlsplit(str(config.api.base_url))
        port = base.port
    except ValueError as exc:
        raise BudgetUnavailable('Endpoint is not a valid verified HTTPS origin') from exc
    if base.scheme != 'https' or base.hostname != pricing['host'] or port not in (None, 443) or base.path.rstrip('/') != '/v1' or base.query or base.fragment or base.username or base.password:
        raise BudgetUnavailable('Endpoint does not match the verified pricing record')
    if config.generation_kwargs.get('model') != pricing['model']:
        raise BudgetUnavailable('Model does not match the verified pricing record')
    warning = pricing_warning()
    if warning:
        logger.warning(warning)
    input_tokens = PROMPT_BYTES + SYSTEM_BYTES + FRAMING_TOKENS
    per_call = (Decimal(input_tokens) * Decimal(pricing['input_cny_per_million']) +
                Decimal(MAX_OUTPUT_TOKENS) * Decimal(pricing['output_cny_per_million'])) / Decimal(1000000)
    if not per_call.is_finite() or per_call <= 0 or per_call > cap:
        raise BudgetUnavailable('Invalid or insufficient verified budget')
    return cap, per_call


def git(*args, **kwargs):
    try:
        result = subprocess.run(['git', *args], capture_output=True, timeout=60, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BudgetUnavailable('Budget ledger Git operation unavailable; no model call authorized') from exc
    if result.returncode:
        raise BudgetUnavailable('Budget ledger Git operation failed; no model call authorized')
    return result.stdout.strip()


def _push_ledger(parent, data, message):
    name, branch = 'llm_budget.json', 'paper-state'
    with TemporaryDirectory() as td:
        config = os.environ.copy()
        config.update(GIT_INDEX_FILE=str(Path(td)/'index'),
            GIT_AUTHOR_NAME='github-actions[bot]', GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',
            GIT_COMMITTER_NAME='github-actions[bot]', GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
        git('read-tree', parent, env=config)
        blob = git('hash-object','-w','--stdin', input=json.dumps(data,sort_keys=True).encode()).decode()
        git('update-index','--add','--cacheinfo',f'100644,{blob},{name}',env=config)
        tree = git('write-tree',env=config).decode()
        commit = git('commit-tree',tree,'-p',parent,'-m',message,env=config).decode()
        try:
            pushed = subprocess.run(['git','push','origin',f'{commit}:refs/heads/{branch}'],capture_output=True,timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BudgetUnavailable('Budget reservation push ambiguous; no paid calls authorized') from exc
    return pushed.returncode == 0

def reserve_day(cap, day=None):
    """Compare-and-swap via fast-forward push; no force and no history rewrite."""
    day = day or utc_day()
    branch, name = 'paper-state', 'llm_budget.json'
    # Require existing delivery state: do not silently initialize another ledger.
    for _ in range(3):
        refs = git('ls-remote', '--heads', 'origin', f'refs/heads/{branch}').decode().split()
        if len(refs) != 2 or refs[1] != f'refs/heads/{branch}':
            raise BudgetUnavailable('Existing budget state branch is required')
        parent = refs[0]
        git('fetch', '--no-tags', '--no-write-fetch-head', 'origin', parent)
        names = git('ls-tree', '--name-only', parent, '--', name).decode().splitlines()
        if name not in names:
            raise BudgetUnavailable('Budget ledger is missing; explicit audited bootstrap required')
        try:
            data = json.loads(git('show', f'{parent}:{name}'))
        except (ValueError, TypeError) as exc:
            raise BudgetUnavailable('Invalid budget ledger; preserve and investigate') from exc
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('days'), dict):
            raise BudgetUnavailable('Invalid budget ledger; preserve and investigate')
        if day in data['days']:
            raise BudgetUnavailable('Daily LLM allowance already reserved; retaining original abstracts')
        data['days'][day] = {'reserved_cny': str(cap), 'policy': 'whole-day-no-refund',
                             'created_at': datetime.now(timezone.utc).isoformat()}
        if _push_ledger(parent, data, 'Reserve daily LLM budget before requests'):
            return day
        # A concurrent writer may have advanced the branch. Re-fetch and
        # inspect: never call on an ambiguous/failed reservation.
    raise BudgetUnavailable('Could not persist daily budget reservation; paid calls disabled')


class BudgetRequests(ModelRequests):
    def __init__(self, cap, per_call, day):
        super().__init__()
        self.remaining, self.per_call, self.day = cap, per_call, day
        self._budget_lock = Lock()

    def call(self, operation):
        # Serialize reservation and actual dispatch. Queued callers must observe
        # terminal aborts before sending, not merely before precharging a slot.
        with self._budget_lock:
            if getattr(self, '_aborted', False):
                raise BudgetUnavailable('Paid requests stopped after an unexpected billing response', reason='billing_unverified')
            if utc_day() != self.day:
                raise BudgetUnavailable('UTC day changed; no new request until a fresh run reserves that day', reason='day_changed')
            if self.remaining < self.per_call:
                raise BudgetUnavailable('Daily LLM budget exhausted; retaining original abstract', reason='daily_budget_exhausted')
            self.remaining -= self.per_call
            try:
                return super().call(operation)
            except BudgetUnavailable:
                self._aborted = True
                self.remaining = Decimal(0)
                raise


def prepare_budget(config):
    cap, per_call = budget_plan(config)
    day = reserve_day(cap)
    return BudgetRequests(cap, per_call, day)


def audit_response(response, expected_model):
    """Unexpected metadata terminates queued calls; never refund uncertain billing."""
    if getattr(response, 'model', None) != expected_model:
        raise BudgetUnavailable('Provider response model does not match reserved model', reason='billing_unverified')
    usage = getattr(response, 'usage', None)
    if usage is None:
        raise BudgetUnavailable('Provider response has no billable usage metadata', reason='billing_unverified')
    counts = [getattr(usage, key, None) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')]
    if any(type(count) is not int or count < 0 for count in counts):
        raise BudgetUnavailable('Provider response usage is invalid', reason='billing_unverified')
    prompt, completion, total = counts
    details = getattr(usage, 'completion_tokens_details', None)
    reasoning = getattr(details, 'reasoning_tokens', 0) or 0
    if type(reasoning) is not int or reasoning != 0 or prompt > PROMPT_BYTES + SYSTEM_BYTES + FRAMING_TOKENS or completion > MAX_OUTPUT_TOKENS or total != prompt + completion:
        raise BudgetUnavailable('Provider usage exceeded or contradicted the reserved bound', reason='billing_unverified')
    choices = getattr(response, 'choices', [])
    if len(choices) != 1 or getattr(choices[0].message, 'reasoning_content', None):
        raise BudgetUnavailable('Provider unexpectedly emitted reasoning or extra choices', reason='billing_unverified')


def bootstrap_ledger():
    """Explicit one-time operation, never called by a paid execution path."""
    if git('rev-parse', '--is-shallow-repository') != b'false':
        raise BudgetUnavailable('Bootstrap requires a non-shallow clone with complete history')
    branch, name = 'paper-state', 'llm_budget.json'
    refs = git('ls-remote', '--heads', 'origin', f'refs/heads/{branch}').decode().split()
    if len(refs) != 2 or refs[1] != f'refs/heads/{branch}':
        raise BudgetUnavailable('Existing delivery state branch is required for bootstrap')
    parent = refs[0]
    git('fetch', '--no-tags', '--no-write-fetch-head', 'origin', parent)
    if git('ls-tree', '--name-only', parent, '--', name):
        raise BudgetUnavailable('Budget ledger already exists; bootstrap will not overwrite it')
    if git('log', '-1', '--format=%H', parent, '--', name):
        raise BudgetUnavailable('Missing ledger existed in history; recover it instead of reinitializing')
    data = {'version': 1, 'days': {}, 'initialized_at': datetime.now(timezone.utc).isoformat()}
    if not _push_ledger(parent, data, 'Initialize new LLM budget ledger explicitly'):
        raise BudgetUnavailable('Bootstrap push failed; inspect remote before retrying')


def no_retry_client(client):
    """Enforce the reservation's one-request contract at the paid boundary."""
    options = getattr(client, 'with_options', None)
    request_client = options(max_retries=0) if callable(options) else client
    retries = getattr(request_client, 'max_retries', None)
    if type(retries) is not int or retries != 0:
        raise BudgetUnavailable('Paid client must explicitly disable automatic retries')
    return request_client
