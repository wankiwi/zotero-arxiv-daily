"""Compose a local runtime configuration without exposing secret values in logs."""
import os
from pathlib import Path
import re

from omegaconf import OmegaConf
from hydra import compose, initialize_config_dir


def prepare(root: Path, environ=os.environ):
    name = environ.get('PAPER_CONFIG', '').strip() or 'all'
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', name) or not (root / 'config' / f'{name}.yaml').is_file():
        raise ValueError('PAPER_CONFIG must name an existing configuration under config/')
    config = OmegaConf.create({'defaults': [name, '_self_']})
    custom = environ.get('CUSTOM_CONFIG', '')
    if custom.strip():
        supplied = OmegaConf.create(custom)
        if not OmegaConf.is_dict(supplied) or 'defaults' in supplied:
            raise ValueError('CUSTOM_CONFIG must be a YAML mapping without defaults')
        config = OmegaConf.merge(config, supplied)
    channel = environ.get('OUTPUT_CHANNEL', 'configured')
    if channel not in ('configured', 'email', 'rss', 'both'):
        raise ValueError('Invalid output channel')
    if channel != 'configured':
        config = OmegaConf.merge(config, {'output': {'email': {'enabled': channel in ('email', 'both')},
                                                   'rss': {'enabled': channel in ('rss', 'both')}}})
    recipient = environ.get('RECEIVER_OVERRIDE', '').strip()
    if recipient:
        if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', recipient) or '${' in recipient:
            raise ValueError('recipient must be one plain email address')
        # An explicit per-run destination takes precedence over CUSTOM_CONFIG.
        config = OmegaConf.merge(config, {'email': {'receiver': recipient}})
    days = environ.get('WINDOW_DAYS', '')
    if days:
        if not days.isdigit() or not 1 <= int(days) <= 90:
            raise ValueError('window_days must be between 1 and 90')
        config = OmegaConf.merge(config, {'source': {name: {'window_days': int(days)}
                                                   for name in ('journals', 'arxiv', 'biorxiv', 'medrxiv', 'researchsquare')}})
    # Workflow history lives in paper-state; never rely on an immutable cache key.
    config = OmegaConf.merge(config, {'state': {'enabled': True, 'path': 'data/recommendations.json'}})
    config = OmegaConf.merge(config, {'output': {'rss': {'path': 'public/feed.xml'}}})
    OmegaConf.save(config, root / 'config' / 'runtime.yaml')
    with initialize_config_dir(config_dir=str((root / 'config').resolve()), version_base=None):
        effective = compose(config_name='runtime')
    # Avoid redeploying an old restored feed during an email-only run.
    if not effective.output.rss.enabled:
        (root / 'public' / 'feed.xml').unlink(missing_ok=True)


if __name__ == '__main__':
    prepare(Path(__file__).resolve().parents[1])
