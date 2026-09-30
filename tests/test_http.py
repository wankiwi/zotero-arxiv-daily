from types import SimpleNamespace
import pytest
import requests
from zotero_arxiv_daily.http import CrossrefAdapter, BoundedRetry, session


def test_crossref_uses_shared_gate_and_bounded_retries():
    with session() as client:
        adapter = client.get_adapter('https://api.crossref.org/journals')
        assert isinstance(adapter, CrossrefAdapter)
        assert adapter.max_retries.total == 5
        assert adapter.max_retries.backoff_max == 30
        assert 429 in adapter.max_retries.status_forcelist
        assert not isinstance(client.get_adapter('https://www.nature.com/'), CrossrefAdapter)


def test_crossref_pacing_across_sessions(monkeypatch):
    clock=[100.0]
    starts=[]
    monkeypatch.setattr('zotero_arxiv_daily.http.time.monotonic',lambda:clock[0])
    monkeypatch.setattr('zotero_arxiv_daily.http.time.sleep',lambda delay:clock.__setitem__(0,clock[0]+delay))
    monkeypatch.setattr(CrossrefAdapter,'_last_start',0.0)
    monkeypatch.setattr('requests.adapters.HTTPAdapter.send',lambda *a,**kw:starts.append(clock[0]))
    CrossrefAdapter().send(object())
    CrossrefAdapter().send(object())
    assert starts == [100.0,101.0]


def test_retry_after_respected_or_budget_error():
    retry=BoundedRetry(total=5)
    assert retry.get_retry_after(SimpleNamespace(headers={'Retry-After':'30'})) == 30
    with pytest.raises(requests.exceptions.RetryError, match='budget'):
        retry.get_retry_after(SimpleNamespace(headers={'Retry-After':'600'}))
