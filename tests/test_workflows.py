"""Exercise runtime configuration and real Git state persistence without GitHub writes."""
from pathlib import Path
import shutil
import subprocess
import sys

from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf
import pytest

from scripts.prepare_workflow import prepare

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_configuration_supports_rss_without_resolving_secrets(tmp_path):
    shutil.copytree(ROOT / 'config', tmp_path / 'config', ignore=shutil.ignore_patterns('runtime.yaml', 'private.yaml'))
    prepare(tmp_path, {'PAPER_CONFIG': 'journals', 'OUTPUT_CHANNEL': 'rss', 'WINDOW_DAYS': '14', 'CUSTOM_CONFIG': 'llm:\n  enabled: false\n'})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert config.source.journals.window_days == 14
    assert config.output.rss.enabled and not config.output.email.enabled
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
