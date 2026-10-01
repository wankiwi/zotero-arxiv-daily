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

class BudgetUnavailable(RuntimeError):
    pass

# No paid requests until the exact provider/model's non-thinking guarantee and
# current tariff have been verified. Enable only with a reviewed pricing record.
VERIFIED_PRICING = None
MAX_DAILY_CNY = Decimal('0.20')
PROMPT_BYTES = 768
SYSTEM_BYTES = 256
FRAMING_TOKENS = 128
MAX_OUTPUT_TOKENS = 128


def utc_day():
    return datetime.now(timezone.utc).date().isoformat()


def budget_plan(config):
    budget = config.get('budget', {})
    cap = Decimal(str(budget.get('daily_cny', '0.20')))
    if not cap.is_finite() or not 0 < cap <= MAX_DAILY_CNY:
        raise BudgetUnavailable('Daily CNY budget must be positive and no more than 0.20')
    if VERIFIED_PRICING is None:
        raise BudgetUnavailable('Exact provider pricing/non-thinking token bound is not verified; paid calls disabled')
    pricing = VERIFIED_PRICING
    base = urlsplit(str(config.api.base_url))
    if base.scheme != 'https' or base.hostname != pricing['host'] or base.path.rstrip('/') != '/v1' or base.query or base.fragment or base.username or base.password:
        raise BudgetUnavailable('Endpoint does not match the verified pricing record')
    if config.generation_kwargs.get('model') != pricing['model'] or utc_day() > pricing['valid_through']:
        raise BudgetUnavailable('Model or pricing validity does not match the verified pricing record')
    input_tokens = PROMPT_BYTES + SYSTEM_BYTES + FRAMING_TOKENS
    per_call = (Decimal(input_tokens) * Decimal(pricing['input_cny_per_million']) +
                Decimal(MAX_OUTPUT_TOKENS) * Decimal(pricing['output_cny_per_million'])) / Decimal(1000000)
    if not per_call.is_finite() or per_call <= 0 or per_call > cap:
        raise BudgetUnavailable('Invalid or insufficient verified budget')
    return cap, per_call


def git(*args, **kwargs):
    result = subprocess.run(['git', *args], capture_output=True, **kwargs)
    if result.returncode:
        raise BudgetUnavailable('Budget ledger Git operation failed; no model call authorized')
    return result.stdout.strip()


def reserve_day(cap, day=None):
    """Compare-and-swap via fast-forward push; no force and no history rewrite."""
    day = day or utc_day()
    branch, name = 'paper-state', 'llm_budget.json'
    # Require existing delivery state: do not silently initialize another ledger.
    for _ in range(3):
        git('fetch', '--no-tags', 'origin', f'refs/heads/{branch}')
        parent = git('rev-parse', 'FETCH_HEAD').decode()
        exists = subprocess.run(['git', 'cat-file', '-e', f'{parent}:{name}'], capture_output=True)
        data = json.loads(git('show', f'{parent}:{name}')) if exists.returncode == 0 else {'version': 1, 'days': {}}
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('days'), dict):
            raise BudgetUnavailable('Invalid budget ledger; preserve and investigate')
        if day in data['days']:
            raise BudgetUnavailable('Daily LLM allowance already reserved; retaining original abstracts')
        data['days'][day] = {'reserved_cny': str(cap), 'policy': 'whole-day-no-refund',
                             'created_at': datetime.now(timezone.utc).isoformat()}
        with TemporaryDirectory() as td:
            config = os.environ.copy()
            config.update(GIT_INDEX_FILE=str(Path(td)/'index'),
                GIT_AUTHOR_NAME='github-actions[bot]', GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',
                GIT_COMMITTER_NAME='github-actions[bot]', GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
            git('read-tree', parent, env=config)
            blob = git('hash-object','-w','--stdin', input=json.dumps(data,sort_keys=True).encode()).decode()
            git('update-index','--add','--cacheinfo',f'100644,{blob},{name}',env=config)
            tree = git('write-tree',env=config).decode()
            commit = git('commit-tree',tree,'-p',parent,'-m','Reserve daily LLM budget before requests',env=config).decode()
            pushed = subprocess.run(['git','push','origin',f'{commit}:refs/heads/{branch}'],capture_output=True)
            if pushed.returncode == 0:
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
        with self._budget_lock:
            if utc_day() != self.day:
                raise BudgetUnavailable('UTC day changed; no new request until a fresh run reserves that day')
            if self.remaining < self.per_call:
                raise BudgetUnavailable('Daily LLM budget exhausted; retaining original abstract')
            self.remaining -= self.per_call  # Charge pessimistically even on timeout/failure.
        try:
            return super().call(operation)
        except BudgetUnavailable:
            with self._budget_lock:
                self.remaining = Decimal(0)
            raise


def prepare_budget(config):
    cap, per_call = budget_plan(config)
    day = reserve_day(cap)
    return BudgetRequests(cap, per_call, day)
