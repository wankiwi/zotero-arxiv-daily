"""Budget safety uses only synthetic prices, fake APIs and local bare Git repos."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
from zotero_arxiv_daily import budget
from zotero_arxiv_daily.protocol import Paper


def test_production_pricing_is_fail_closed(config):
    with pytest.raises(budget.BudgetUnavailable,match='not verified'):
        budget.budget_plan(config.llm)


def test_verified_plan_rejects_unknown_endpoint_model_and_limit(config,monkeypatch):
    monkeypatch.setattr(budget,'VERIFIED_PRICING',{'host':'test.example','model':'test-model','valid_through':'2099-01-01','input_cny_per_million':'3','output_cny_per_million':'9'})
    config.llm.api.base_url='https://test.example/v1';config.llm.generation_kwargs.model='test-model'
    cap,cost=budget.budget_plan(config.llm)
    assert cap==Decimal('0.20') and cost*45<=Decimal('0.21')
    config.llm.api.base_url='https://unknown.example/v1'
    with pytest.raises(budget.BudgetUnavailable,match='Endpoint'):budget.budget_plan(config.llm)
    config.llm.budget.daily_cny=0.21
    with pytest.raises(budget.BudgetUnavailable,match='no more'):budget.budget_plan(config.llm)


def test_concurrent_calls_timeouts_and_day_rollover(monkeypatch):
    monkeypatch.setattr(budget,'utc_day',lambda:'2026-10-01')
    guard=budget.BudgetRequests(Decimal('.20'),Decimal('.004608'),'2026-10-01')
    calls=[]
    def attempt(i):
        def operation():
            calls.append(i)
            raise TimeoutError('ambiguous billing')
        try:guard.call(operation)
        except (TimeoutError,budget.BudgetUnavailable):pass
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(attempt,range(60)))
    assert len(calls)==43 and guard.remaining>=0
    before=guard.remaining
    monkeypatch.setattr(budget,'utc_day',lambda:'2026-10-02')
    with pytest.raises(budget.BudgetUnavailable,match='day changed'):guard.call(lambda:None)
    assert guard.remaining==before


def make_git(tmp_path, monkeypatch):
    remote=tmp_path/'origin.git';checkout=tmp_path/'checkout'
    def git(*args,cwd=None):return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True).stdout
    git('init','--bare',str(remote));git('init','-b','main',str(checkout))
    git('config','user.name','Test',cwd=checkout);git('config','user.email','test@example.org',cwd=checkout)
    (checkout/'recommendations.json').write_text('{"version":1,"records":{"sent":"preserved"}}')
    git('add','recommendations.json',cwd=checkout);git('commit','-m','Initial state',cwd=checkout)
    git('remote','add','origin',str(remote),cwd=checkout);git('push','origin','HEAD:paper-state',cwd=checkout)
    monkeypatch.chdir(checkout)
    return git,checkout,remote


def test_durable_claim_survives_crash_and_keeps_history(tmp_path,monkeypatch):
    git,checkout,remote=make_git(tmp_path,monkeypatch)
    index=git('write-tree')
    assert budget.reserve_day(Decimal('.20'),'2026-10-01')=='2026-10-01'
    # No post-call save or refund: simulate crash immediately after reservation.
    with pytest.raises(budget.BudgetUnavailable,match='already reserved'):
        budget.reserve_day(Decimal('.20'),'2026-10-01')
    head=git('rev-parse','refs/heads/paper-state',cwd=remote).decode().strip()
    assert b'preserved' in git('show',head+':recommendations.json',cwd=remote)
    assert git('write-tree')==index
    ledger=json.loads(git('show',head+':llm_budget.json',cwd=remote))
    assert ledger['days']['2026-10-01']['reserved_cny']=='0.20'


def test_concurrent_repository_claims_only_one_wins(tmp_path,monkeypatch):
    git,checkout,remote=make_git(tmp_path,monkeypatch)
    # Two independent processes, like workflow reruns, share only the remote ref.
    second=tmp_path/'second';git('clone',str(remote),str(second))
    code="from zotero_arxiv_daily.budget import reserve_day; from decimal import Decimal; reserve_day(Decimal('.20'),'2026-10-01')"
    import sys
    jobs=[subprocess.Popen([sys.executable,'-c',code],cwd=path,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for path in (checkout,second)]
    statuses=[]
    for job in jobs:job.communicate();statuses.append(job.returncode)
    assert statuses.count(0)==1


def test_failed_push_cannot_authorize(tmp_path,monkeypatch):
    git,checkout,remote=make_git(tmp_path,monkeypatch)
    hook=remote/'hooks/pre-receive';hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o755)
    with pytest.raises(budget.BudgetUnavailable,match='Could not persist'):
        budget.reserve_day(Decimal('.20'),'2026-10-01')


def test_corrupt_ledger_refuses_claim(tmp_path,monkeypatch):
    git,checkout,remote=make_git(tmp_path,monkeypatch)
    (checkout/'llm_budget.json').write_text('{"version":999}')
    git('add','llm_budget.json');git('commit','-m','Corrupt fixture');git('push','origin','HEAD:paper-state')
    with pytest.raises(budget.BudgetUnavailable,match='Invalid budget ledger'):
        budget.reserve_day(Decimal('.20'),'2026-10-01')


def test_unicode_prompt_bound_and_reasoning_stops_calls(config,monkeypatch):
    config.llm.budget.enabled=True
    guard=budget.BudgetRequests(Decimal('.20'),Decimal('.005'),budget.utc_day())
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='中文摘要。',reasoning_content='Unexpected'))])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p=Paper('journals','长标题'*1000,[],'分子材料摘要'*1000,'https://example.org/paper')
    p.generate_tldr(client,config.llm,guard)
    assert len(calls)==1 and p.tldr_status=='fallback' and guard.remaining==0
    request=calls[0]
    assert len(request['messages'][1]['content'].encode())<=budget.PROMPT_BYTES
    assert len(request['messages'][0]['content'].encode())<=budget.SYSTEM_BYTES
    assert 'Abstract:' in request['messages'][1]['content']
    assert request['n']==1 and request['max_tokens']==128 and request['extra_body']=={'enable_thinking':False}
    p.generate_tldr(client,config.llm,guard)
    assert len(calls)==1
