"""One explicitly requested, budget-guarded summary; no email or delivery writes."""
import json
import os
from pathlib import Path
import re

import httpx
from omegaconf import OmegaConf
from openai import OpenAI, APIStatusError

from zotero_arxiv_daily.budget import budget_plan, prepare_budget, git
from zotero_arxiv_daily.state import load_paper


def request_audit(events):
    def observe(request):
        if request.method == 'POST' and request.url.path == '/v1/chat/completions':
            events.append({'http_status': None})
    return observe


def response_audit(events):
    def observe(response):
        if response.request.method != 'POST' or response.request.url.path != '/v1/chat/completions':
            return
        event = events[-1]
        event['http_status'] = response.status_code
        response.read()
        try:
            usage = response.json().get('usage', {})
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                if type(usage.get(key)) is int:
                    event[key] = usage[key]
        except (ValueError, TypeError, AttributeError):
            pass
    return observe


def validate(config, paper, client, events):
    budget_plan(config)  # Verify destination before transmitting even a model-list request.
    if config.get('language') != 'Chinese' or config.get('input_mode', 'abstract') != 'abstract':
        raise ValueError('Validation requires Chinese abstract mode')
    original = paper.abstract
    if not original:
        raise ValueError('A public paper with an original abstract is required')
    models = client.models.list()
    if config.generation_kwargs.model not in {model.id for model in models.data}:
        return {'status': 'model_not_listed', 'paid_request_count': 0}
    guard = prepare_budget(config)  # Durable whole-day reservation, with no refund/reset.
    paper.generate_tldr(client, config, requests=guard)
    chinese = bool(re.search(r'[\u4e00-\u9fff]', paper.tldr or ''))
    generated = paper.tldr_status == 'generated'
    sentences = len([part for part in re.split(r'[。！？!?]+', paper.tldr or '') if part.strip()])
    preserved = paper.abstract == original
    return {'status': 'success' if generated and chinese and sentences == 1 and preserved else 'summary_failed',
            'summary_status': paper.tldr_status, 'summary_error': paper.tldr_error,
            'contains_chinese': chinese if generated else False,
            'sentence_count': sentences if generated else 0,
            'summary_characters': len(paper.tldr or '') if generated else 0,
            'original_abstract_preserved': preserved, 'paid_request_count': len(events),
            'usage': events, 'reserved_day': guard.day}


def state_snapshot():
    ref = git('ls-remote', '--heads', 'origin', 'refs/heads/paper-state').decode().split()
    if len(ref) != 2 or ref[1] != 'refs/heads/paper-state':
        raise ValueError('Existing delivery state required')
    git('fetch', '--no-tags', '--no-write-fetch-head', 'origin', ref[0])
    blob = git('rev-parse', f'{ref[0]}:recommendations.json').decode()
    state = json.loads(git('show', f'{ref[0]}:recommendations.json'))
    return blob, state


def main():
    events = []
    report = {'status': 'validation_failed'}
    try:
        root = Path(__file__).resolve().parents[1]
        base = OmegaConf.load(root / 'config/base.yaml').llm
        supplied = OmegaConf.create(os.environ['CUSTOM_CONFIG']).llm
        config = OmegaConf.merge(base, supplied)
        budget_plan(config)
        before, state = state_snapshot()
        papers = [record for record in state['records'].values()
                  if record.get('channels', {}).get('email') and record['paper'].get('abstract')]
        paper = load_paper(max(papers, key=lambda record: record['added'])['paper'])
        with httpx.Client(follow_redirects=False, event_hooks={
                'request': [request_audit(events)], 'response': [response_audit(events)]}) as transport:
            with OpenAI(api_key=config.api.key, base_url=config.api.base_url,
                        max_retries=0, timeout=60, http_client=transport) as client:
                report = validate(config, paper, client, events)
        after, _ = state_snapshot()
        report['delivery_state_unchanged'] = before == after
        if before != after:
            report['status'] = 'delivery_state_changed_concurrently'
    except APIStatusError as exc:
        report = {'status': 'provider_rejected', 'http_status': exc.status_code,
                  'paid_request_count': len(events)}
    except Exception as exc:
        report = {'status': 'validation_failed', 'error_type': type(exc).__name__,
                  'paid_request_count': len(events)}
    print(json.dumps(report, ensure_ascii=False))  # No paper text, IDs, credentials or provider bodies.
    return 0 if report['status'] == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
