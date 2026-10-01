"""Exercise runtime configuration and real Git state persistence without GitHub writes."""
from pathlib import Path
import shutil
import subprocess
import sys

from hydra import compose, initialize_config_dir
import pytest

from scripts.prepare_workflow import prepare

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_configuration_forces_email_without_resolving_secrets(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    prepare(tmp_path, {'PAPER_CONFIG': 'journals', 'OUTPUT_CHANNEL': 'rss', 'WINDOW_DAYS': '14', 'CUSTOM_CONFIG': 'llm:\n  enabled: false\n'})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert config.source.journals.window_days == 14
    assert not config.output.rss.enabled and config.output.email.enabled
    assert not config.llm.enabled and config.state.enabled
    assert config.executor.source == ['journals']


def test_email_only_workflow_does_not_publish_restored_feed(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    (tmp_path / 'public').mkdir()
    (tmp_path / 'public/feed.xml').write_text('old feed')
    prepare(tmp_path, {'PAPER_CONFIG': 'journals', 'OUTPUT_CHANNEL': 'email'})
    assert not (tmp_path / 'public/feed.xml').exists()


@pytest.mark.parametrize('environ', [
    {'PAPER_CONFIG': '../../outside'}, {'PAPER_CONFIG': 'journals', 'WINDOW_DAYS': '-1'},
    {'PAPER_CONFIG': 'journals', 'CUSTOM_CONFIG': 'defaults: [outside]'},
])
def test_invalid_runtime_overrides_are_rejected(tmp_path, environ):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    with pytest.raises(ValueError):
        prepare(tmp_path, environ)


def test_state_branch_roundtrip_preserves_working_branch_and_user_index(tmp_path):
    remote, checkout = tmp_path / 'remote.git', tmp_path / 'checkout'
    def git(*args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True).stdout.strip()
    git('init', '--bare', str(remote))
    git('init', '-b', 'main', str(checkout))
    git('config', 'user.name', 'Test', cwd=checkout)
    git('config', 'user.email', 'test@example.org', cwd=checkout)
    git('remote', 'add', 'origin', str(remote), cwd=checkout)
    (checkout / 'README').write_text('Initial')
    git('add', 'README', cwd=checkout)
    git('commit', '-m', 'Initial', cwd=checkout)
    git('push', 'origin', 'main', cwd=checkout)
    (checkout / 'README').write_text('User change')
    git('add', 'README', cwd=checkout)
    index = git('write-tree', cwd=checkout)
    (checkout / 'data').mkdir()
    (checkout / 'data/recommendations.json').write_text('{"version":1,"records":{}}')
    script = ROOT / 'scripts/workflow_state.py'
    subprocess.run([sys.executable, str(script), 'save'], cwd=checkout, check=True)
    first = git('ls-remote', 'origin', 'refs/heads/paper-state', cwd=checkout)
    assert first
    subprocess.run([sys.executable, str(script), 'save'], cwd=checkout, check=True)
    assert git('ls-remote', 'origin', 'refs/heads/paper-state', cwd=checkout) == first
    (checkout / 'data/recommendations.json').unlink()
    subprocess.run([sys.executable, str(script), 'restore'], cwd=checkout, check=True)
    assert '"version":1' in (checkout / 'data/recommendations.json').read_text()
    assert git('branch', '--show-current', cwd=checkout) == b'main'
    assert git('write-tree', cwd=checkout) == index
    assert (checkout / 'README').read_text() == 'User change'


@pytest.mark.parametrize('name, sources', [
    (None, ['journals', 'arxiv', 'biorxiv', 'medrxiv', 'researchsquare', 'openreview']),
    ('', ['journals', 'arxiv', 'biorxiv', 'medrxiv', 'researchsquare', 'openreview']),
    ('  ', ['journals', 'arxiv', 'biorxiv', 'medrxiv', 'researchsquare', 'openreview']),
    ('default', ['journals', 'arxiv', 'biorxiv', 'medrxiv', 'researchsquare', 'openreview']),
    ('preprints', ['arxiv', 'biorxiv', 'medrxiv', 'researchsquare']),
    ('researchsquare', ['researchsquare']), ('arxiv', ['arxiv']),
    ('biorxiv', ['biorxiv']), ('medrxiv', ['medrxiv']),
])
def test_preprint_configuration_selection(tmp_path, name, sources):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    environ = {} if name is None else {'PAPER_CONFIG': name}
    prepare(tmp_path, environ)
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert list(config.executor.source) == sources
    assert list(config.source.arxiv.category) == ['*']
    assert config.source.arxiv.window_days == 7
    assert config.source.journals.window_days == 7
    assert not config.output.rss.enabled


def test_mixed_source_customization_and_common_window(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    prepare(tmp_path, {'PAPER_CONFIG': 'all', 'WINDOW_DAYS': '3', 'CUSTOM_CONFIG':
                      'executor:\n  source: [journals, arxiv, researchsquare]\nsource:\n  arxiv:\n    category: [physics.chem-ph]\n'})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert list(config.executor.source) == ['journals', 'arxiv', 'researchsquare']
    assert list(config.source.arxiv.category) == ['physics.chem-ph']
    assert all(config.source[name].window_days == 3 for name in ['journals', 'arxiv', 'biorxiv', 'medrxiv', 'researchsquare', 'openreview'])


def test_explicit_run_recipient_overrides_custom_config(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    prepare(tmp_path, {'PAPER_CONFIG': 'all', 'OUTPUT_CHANNEL': 'both',
                       'CUSTOM_CONFIG': 'email:\n  receiver: old@example.org\n',
                       'RECEIVER_OVERRIDE': 'verified@example.com'})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert config.email.receiver == 'verified@example.com'
    assert config.output.email.enabled and not config.output.rss.enabled


@pytest.mark.parametrize('recipient', ['a@example.com,b@example.com', 'a@example.com\r\nBcc: b@example.com', '${oc.env:RECEIVER}', 'bad-address'])
def test_invalid_explicit_recipient_rejected(tmp_path, recipient):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    with pytest.raises(ValueError, match='recipient'):
        prepare(tmp_path, {'RECEIVER_OVERRIDE': recipient})


@pytest.mark.parametrize('sources,expected', [
    ('configured', ['arxiv', 'biorxiv']),
    ('all', ['journals', 'arxiv', 'biorxiv', 'medrxiv', 'researchsquare', 'openreview']),
    ('journals', ['journals']),
])
@pytest.mark.parametrize('llm_mode', ['configured', 'disabled'])
def test_single_run_sources_and_llm_override_custom_config(tmp_path, sources, expected, llm_mode):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    original = (tmp_path / 'config' / 'custom.yaml').read_bytes()
    prepare(tmp_path, {'PAPER_CONFIG': 'all', 'SOURCE_MODE': sources, 'LLM_MODE': llm_mode,
                      'CUSTOM_CONFIG': 'executor:\n  source: [arxiv, biorxiv]\nllm:\n  enabled: true\n  generation_kwargs:\n    model: existing-model\n'})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert list(config.executor.source) == expected
    assert config.llm.enabled == (llm_mode == 'configured')
    assert config.llm.generation_kwargs.model == 'existing-model'
    assert (tmp_path / 'config' / 'custom.yaml').read_bytes() == original


def test_default_modes_retain_custom_disabled_llm_and_sources(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    prepare(tmp_path, {'CUSTOM_CONFIG': 'executor:\n  source: [biorxiv]\nllm:\n  enabled: false\n'})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert list(config.executor.source) == ['biorxiv'] and not config.llm.enabled


def test_all_sources_fills_unconfigured_legacy_platforms(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    prepare(tmp_path, {'PAPER_CONFIG': 'legacy', 'SOURCE_MODE': 'all', 'LLM_MODE': 'disabled', 'OUTPUT_CHANNEL': 'rss'})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    # Preserve explicitly configured arXiv categories and announcement mode.
    assert list(config.source.arxiv.category) == ['cs.AI', 'cs.CV', 'cs.LG', 'cs.CL']
    assert config.source.arxiv.window_days is None
    assert list(config.source.medrxiv.category) == ['*'] and config.source.medrxiv.window_days == 1
    assert list(config.source.biorxiv.category) == ['*'] and config.source.biorxiv.window_days == 1


@pytest.mark.parametrize('overrides', [{'SOURCE_MODE': 'unknown'}, {'LLM_MODE': 'paid'}])
def test_invalid_run_modes_are_rejected(tmp_path, overrides):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    with pytest.raises(ValueError, match='Invalid'):
        prepare(tmp_path, overrides)


@pytest.mark.parametrize('channel', ['configured', 'both', 'rss', 'email'])
def test_stale_rss_config_and_dispatch_cannot_reenable_publishing(tmp_path, channel):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    (tmp_path / 'public').mkdir()
    (tmp_path / 'public/feed.xml').write_text('historical feed')
    prepare(tmp_path, {'OUTPUT_CHANNEL': channel, 'CUSTOM_CONFIG':
                      'output: {email: {enabled: false}, rss: {enabled: true}}'})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert config.output.email.enabled and not config.output.rss.enabled
    assert not (tmp_path / 'public/feed.xml').exists()


def test_schedule_uses_confirmed_profile_over_stale_overrides(tmp_path, monkeypatch):
    from omegaconf import OmegaConf
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    monkeypatch.setenv('RECEIVER', 'daily@example.org')
    prepare(tmp_path, {'GITHUB_EVENT_NAME': 'schedule', 'PAPER_CONFIG': 'arxiv',
                      'SOURCE_MODE': 'configured', 'PREPRINT_PROFILE': 'configured', 'LLM_MODE': 'configured',
                      'RECEIVER_OVERRIDE': 'stale-dispatch@example.org', 'CUSTOM_CONFIG': '''
executor:
  source: [arxiv, biorxiv]
  max_paper_num: 50
llm:
  enabled: true
email:
  receiver: stale-custom@example.org
  smtp_server: mail.cstnet.cn
  smtp_port: 994
output:
  rss: {enabled: true}
preprint_interests:
  medrxiv: {enabled: true}
  researchsquare:
    source_ids: [S4306402450]
'''})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert list(config.executor.source) == ['journals', 'arxiv', 'biorxiv', 'researchsquare', 'openreview']
    assert config.llm.enabled and config.llm.budget.enabled and config.llm.budget.daily_cny == 0.20
    assert config.output.email.enabled and not config.output.rss.enabled
    assert config.email.receiver == 'daily@example.org'
    assert config.email.smtp_server == 'mail.cstnet.cn' and config.email.smtp_port == 994
    assert config.executor.max_paper_num == 45 and config.state.enabled
    assert config.state.path == 'data/recommendations.json'
    assert list(config.preprint_interests.arxiv.categories) == ['physics.chem-ph', 'physics.comp-ph', 'cond-mat.mtrl-sci', 'cond-mat.soft', 'cs.LG', 'cs.AI']
    assert list(config.preprint_interests.biorxiv.categories) == ['biophysics', 'biochemistry']
    assert not config.preprint_interests.medrxiv.enabled
    assert list(config.preprint_interests.researchsquare.source_ids) == ['S4306525896']
    assert len(config.preprint_interests.researchsquare.subfield) == 7
    assert {'pnas', 'acs_catalysis', 'npjcompumats', 'angew', 'chemical_science', 'mlst'} <= set(config.source.journals.presets)
    assert config.source.arxiv.window_days == config.source.journals.window_days == 7
    assert config.source.biorxiv.window_days == config.source.researchsquare.window_days == 1
    assert 'daily@example.org' not in (tmp_path / 'config/runtime.yaml').read_text()
    assert OmegaConf.to_container(config.email, resolve=False)['receiver'] == '${oc.env:RECEIVER}'


def test_daily_schedule_uses_requested_utc_time():
    import yaml

    workflow = yaml.load((ROOT / '.github/workflows/main.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['schedule'] == [{'cron': '17 20 * * *'}]
