"""Model outages, optional summaries, and backwards-compatible output metadata."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import json

import httpx
from loguru import logger
from openai import NotFoundError
from omegaconf import open_dict
import pytest

from tests.canned_responses import make_sample_paper, make_stub_openai_client, make_budget_guard
from tests.test_pipeline import pipeline  # noqa: F401 -- reuse the isolated pipeline fixture
from zot2dailypaper.construct_email import render_email
from zot2dailypaper.executor import Executor
from zot2dailypaper.llm import ModelRequests
from zot2dailypaper.output.rss import write_rss
from zot2dailypaper.state import State, load_paper, paper_dict


def not_found(message, code=404):
    return NotFoundError(message, response=httpx.Response(404, request=httpx.Request('POST', 'https://example.org/v1/chat/completions')),
                         body={'error': {'message': message, 'code': code}, 'user_id': 'private-account-marker'})


def client_with(operation):
    return SimpleNamespace(max_retries=0,chat=SimpleNamespace(completions=SimpleNamespace(create=operation)))


def test_unavailable_model_stops_parallel_summary_and_affiliation_requests():
    calls = []
    def unavailable(**kwargs):
        calls.append(kwargs)
        raise not_found('This model is unavailable for free. Use paid version instead.')
    client, guard = client_with(unavailable), make_budget_guard()
    papers = [make_sample_paper(title=f'Paper {i}') for i in range(8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda p: p.generate_tldr(client, {'generation_kwargs': {'model': 'test-model'}}, guard), papers))
    papers[0].generate_affiliations(client, {}, guard)
    assert len(calls) == 1
    assert all(p.tldr_status == 'fallback' and p.tldr_error == 'model_unavailable' for p in papers)
    assert all(p.tldr == p.abstract for p in papers)


@pytest.mark.parametrize('error', [not_found('Route not found'), RuntimeError('temporary network error')])
def test_other_errors_do_not_disable_later_summaries(error):
    calls = []
    success = make_stub_openai_client()
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise error
        return success.chat.completions.create(**kwargs)
    client, guard = client_with(create), make_budget_guard()
    first, second = make_sample_paper(), make_sample_paper()
    first.generate_tldr(client, {'generation_kwargs': {'model': 'test-model'}}, guard)
    second.generate_tldr(client, {'generation_kwargs': {'model': 'test-model'}}, guard)
    assert len(calls) == 2 and not guard.unavailable
    assert first.tldr_status == 'fallback' and second.tldr_status == 'generated'


def test_model_not_found_code_is_recognized():
    guard = ModelRequests()
    with pytest.raises(NotFoundError):
        guard.call(lambda: (_ for _ in ()).throw(not_found('Missing', 'model_not_found')))
    assert guard.unavailable


def test_empty_summary_is_not_reported_as_generated():
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='  '))])
    paper = make_sample_paper()
    paper.generate_tldr(client_with(lambda **kw: response), {'generation_kwargs': {'model': 'test-model'}}, make_budget_guard())
    assert paper.tldr_status == 'fallback' and paper.tldr == paper.abstract


@pytest.mark.parametrize('status,label', [
    ('generated', 'AI summary'), ('fallback', 'Original abstract (AI summary unavailable)'),
    ('not_generated', 'Original abstract (AI summary not generated)'), ('legacy', 'Summary (legacy; origin unknown)'),
])
def test_summary_status_roundtrips_and_labels_both_outputs(tmp_path, status, label):
    paper = make_sample_paper(tldr='Summary text' if status in ('generated', 'legacy') else None)
    paper.tldr_status = status
    paper.tldr_error = 'request_failed' if status == 'fallback' else None
    state = State(tmp_path / 'state.json')
    state.add([paper])
    state.mark([paper], 'email')
    state.save()
    restored = State(state.path)
    assert not restored.pending('email')
    restored_paper = restored.pending('rss')[0]
    assert restored_paper.tldr_status == status and restored_paper.tldr_error == paper.tldr_error
    email_label = 'Original abstract (AI summary unavailable)' if status == 'legacy' else label
    assert email_label in render_email([restored_paper])
    path, _ = write_rss(restored, {'path': str(tmp_path / 'feed.xml')})
    assert label in ET.parse(path).findtext('./channel/item/description')


def test_legacy_state_does_not_claim_ai_origin_or_reset_delivery(tmp_path):
    paper = make_sample_paper(tldr='Historical text')
    state = State(tmp_path / 'state.json')
    state.add([paper])
    state.mark([paper], 'email')
    state.save()
    data = json.loads(state.path.read_text())
    for record in data['records'].values():
        record['paper'].pop('tldr_status')
        record['paper'].pop('tldr_error')
    state.path.write_text(json.dumps(data))
    restored = State(state.path)
    assert not restored.pending('email')
    assert restored.pending('rss')[0].summary_label == 'Summary (legacy; origin unknown)'
    original = paper_dict(make_sample_paper(tldr=None))
    original.pop('tldr_status')
    original.pop('tldr_error')
    assert load_paper(original).tldr_status == 'not_generated'
    assert load_paper(original).tldr_error is None


def test_pipeline_summarizes_degradation_without_provider_payload(pipeline, monkeypatch):
    with open_dict(pipeline):
        pipeline.llm.enabled = True
        pipeline.executor.enrichment_workers = 4
    calls = []
    def unavailable(**kw):
        calls.append(kw)
        raise not_found('This model is unavailable for free')
    monkeypatch.setattr('zot2dailypaper.executor.OpenAI', lambda **kw: client_with(unavailable))
    executor = Executor(pipeline)
    papers = [make_sample_paper(title=f'Paper {i}', url=f'https://example.org/{i}') for i in range(6)]
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: papers)
    logs = []
    sink = logger.add(lambda msg: logs.append(str(msg)))
    try:
        executor.run()
    finally:
        logger.remove(sink)
    assert len(calls) == 1
    warnings = [line for line in logs if 'AI summary degradation:' in line]
    assert len(warnings) == 1 and '6/6 unavailable' in warnings[0]
    assert 'private-account-marker' not in ''.join(logs) + State(pipeline.state.path).path.read_text()
    assert all(p.tldr_error == 'model_unavailable' for p in State(pipeline.state.path).pending('email'))


def test_disabled_llm_needs_no_credentials_and_emits_no_degradation_warning(pipeline, monkeypatch):
    with open_dict(pipeline):
        pipeline.llm.api = {'key': '???', 'base_url': '???'}
        pipeline.llm.generation_kwargs = {'model': '???'}
    monkeypatch.setattr('zot2dailypaper.executor.OpenAI', lambda **kw: pytest.fail('Disabled LLM must not be constructed'))
    executor = Executor(pipeline)
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: [make_sample_paper()])
    logs = []
    sink = logger.add(lambda msg: logs.append(str(msg)))
    try:
        executor.run()
    finally:
        logger.remove(sink)
    paper = State(pipeline.state.path).pending('email')[0]
    assert paper.tldr_status == 'not_generated' and paper.tldr_error is None
    assert not any('AI summary degradation:' in line for line in logs)
    config_lines = [line for line in logs if 'Effective configuration:' in line]
    assert len(config_lines) == 1 and 'llm_enabled=False' in config_lines[0]
    assert 'fake-zotero-key' not in ''.join(logs) and 'sk-fake' not in ''.join(logs)
