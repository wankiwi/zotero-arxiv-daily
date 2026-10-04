"""The manual cross-runner probe cannot accidentally use production inputs."""
import importlib.util
from pathlib import Path

import pytest
import yaml
from cryptography.exceptions import InvalidTag


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cache_validation', ROOT/'scripts/validate_encrypted_cache.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_independent_cold_save_and_restore(tmp_path):
    package = tmp_path/'private.enc'
    key = bytes(range(32))  # Test fixture, never a live credential.
    saved = probe.run('save', package, key)
    restored = probe.run('restore', package, key)
    assert saved['stats']['misses'] == 3 and saved['stats']['writes'] == 3
    assert restored['stats'] == dict(memory_hits=0, disk_hits=3, misses=0, corrupt=0, writes=0)
    assert restored['vectors_equal'] is True
    assert saved['ciphertext_sha256'] == restored['ciphertext_sha256']
    assert list(tmp_path.iterdir()) == [package]
    with pytest.raises(FileExistsError):
        probe.run('save', package, key)
    with pytest.raises(InvalidTag):
        probe.run('restore', package, bytes(reversed(range(32))))


@pytest.mark.parametrize('field,value', [('GITHUB_REF', 'refs/heads/feature'),
                                        ('GITHUB_REPOSITORY', 'other/repo'),
                                        ('GITHUB_EVENT_NAME', 'pull_request')])
def test_cli_rejects_untrusted_context(monkeypatch, tmp_path, field, value):
    for name, content in {'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': 'wankiwi/zotero-arxiv-daily',
                          'GITHUB_EVENT_NAME': 'workflow_dispatch'}.items():
        monkeypatch.setenv(name, content)
    monkeypatch.setenv(field, value)
    monkeypatch.setattr('sys.argv', ['probe', 'save', '--package', str(tmp_path/'private.enc')])
    with pytest.raises(SystemExit, match='trusted main'):
        probe.main()
    assert not list(tmp_path.iterdir())


def test_manual_workflow_isolated_namespace_and_credentials():
    text = (ROOT/'.github/workflows/validate-encrypted-cache.yml').read_text()
    workflow = yaml.safe_load(text)
    assert workflow.get('on', workflow.get(True)) == {'workflow_dispatch': None}
    assert workflow['permissions'] == {'contents': 'read'}
    assert workflow['jobs']['restore']['needs'] == 'save'
    for job in workflow['jobs'].values():
        assert "github.ref == 'refs/heads/main'" in job['if']
        assert "github.repository == 'wankiwi/zotero-arxiv-daily'" in job['if']
        for step in job['steps']:
            if step.get('uses', '').startswith('actions/cache/'):
                assert step['with']['key'].startswith('embedding-validation-v1-')
                assert step['with']['path'].endswith('/private.enc')
                assert 'restore-keys' not in step['with']
    for forbidden in ['secrets.ZOTERO', 'secrets.SMTP', 'secrets.OPENAI', 'CUSTOM_CONFIG',
                      'workflow_state.py', 'zotero_arxiv_daily.main', 'schedule:', 'embedding-private-v1-']:
        assert forbidden not in text
    assert text.count('secrets.EMBEDDING_CACHE_KEY') == 2
