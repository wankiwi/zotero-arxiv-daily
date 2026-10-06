"""Real offline SentenceTransformer loads across separate runners/processes."""
import json
from pathlib import Path
import subprocess
import sys
import uuid

import pytest


WORKER = Path(__file__).with_name('cache_model_worker.py')


def run_worker(root, **options):
    root.mkdir(parents=True, exist_ok=True)
    request = root / f'{uuid.uuid4().hex}.json'
    request.write_text(json.dumps({'root': str(root), 'action': 'evaluate'} | options), encoding='utf-8')
    completed = subprocess.run([sys.executable, str(WORKER), str(request)], capture_output=True,
                               text=True, timeout=60, cwd=WORKER.parents[2])
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


@pytest.fixture(scope='module')
def snapshots(tmp_path_factory):
    root = tmp_path_factory.mktemp('offline-model-snapshots')
    return run_worker(root, action='build')['snapshots']


def cold_package(root, snapshot):
    root.mkdir(parents=True, exist_ok=True)
    cipher = root / 'private.enc'
    cold = run_worker(root / 'runner-a', snapshot=snapshot, cache=str(root / 'runner-a' / 'cache'),
                      bf16=False, seal=str(cipher))
    assert cold['loaded'][0]['dtype'] == 'torch.bfloat16' and cold['dtype'] == 'torch.float32'
    assert cold['encoded'] == [3] and cold['stats']['misses'] == 3 and cold['stats']['disk_hits'] == 0
    assert b'alpha beta' not in cipher.read_bytes() and b'manifest.json' not in cipher.read_bytes()
    return cold, cipher


@pytest.fixture(scope='module')
def fp32_package(tmp_path_factory, snapshots):
    return cold_package(tmp_path_factory.mktemp('synthetic-fp32-package'), snapshots[0])


def test_actual_model_load_and_encrypted_cross_process_precision_reuse(tmp_path, snapshots, fp32_package):
    cold, cipher = fp32_package
    warm = run_worker(tmp_path / 'runner-b', snapshot=snapshots[0], cache=str(tmp_path / 'runner-b' / 'cache'),
                      bf16=True, restore=str(cipher))
    assert warm['restored'] == 3 and warm['loaded'][0]['dtype'] == 'torch.bfloat16'
    assert warm['dtype'] == 'torch.float32' and warm['namespace'] == cold['namespace']
    assert warm['stats']['disk_hits'] == 3 and warm['stats']['misses'] == 0 and warm['encoded'] == []
    assert warm['score_digest'] == cold['score_digest']
    extended = run_worker(tmp_path / 'runner-c', snapshot=snapshots[0], cache=str(tmp_path / 'runner-c' / 'cache'),
                          bf16=True, restore=str(cipher), new_text=True)
    assert extended['stats']['disk_hits'] == 3 and extended['stats']['misses'] == 1
    assert extended['encoded'] == [1] and extended['dtype'] == 'torch.float32'


def test_actual_bf16_pool_has_disk_hits_in_fresh_process(tmp_path, snapshots):
    cipher = tmp_path / 'bf16.enc'
    cold = run_worker(tmp_path / 'runner-a', snapshot=snapshots[0], cache=str(tmp_path / 'runner-a' / 'cache'),
                      bf16=True, seal=str(cipher))
    assert cold['dtype'] == 'torch.bfloat16' and cold['encoded'] == [3] and cold['stats']['misses'] == 3
    warm = run_worker(tmp_path / 'runner-b', snapshot=snapshots[0], cache=str(tmp_path / 'runner-b' / 'cache'),
                      bf16=True, restore=str(cipher))
    assert warm['dtype'] == 'torch.bfloat16' and warm['namespace'] == cold['namespace']
    assert warm['stats']['disk_hits'] == 3 and warm['encoded'] == []
    assert warm['score_digest'] == cold['score_digest']


@pytest.mark.parametrize('change', ['revision', 'prompt_definition', 'prompt_name', 'native_dtype', 'normalization'])
def test_actual_model_cross_process_invalidation(tmp_path, snapshots, fp32_package, change):
    cold, cipher = fp32_package
    settings = {'snapshot': snapshots[0], 'bf16': True}
    if change == 'revision':
        settings['snapshot'] = snapshots[1]
        assert snapshots[1]['revision'] != snapshots[0]['revision']
    elif change == 'prompt_definition':
        settings['prompt_definition'] = 'Query: '
    elif change == 'prompt_name':
        settings['prompt_name'] = 'query'
    elif change == 'native_dtype':
        settings['dtype'] = 'native'
    else:
        settings['normalize'] = False
    result = run_worker(tmp_path / 'runner-b', cache=str(tmp_path / 'runner-b' / 'cache'),
                        restore=str(cipher), **settings)
    assert result['restored'] == 3 and result['namespace'] != cold['namespace']
    assert result['stats']['disk_hits'] == 0 and result['stats']['misses'] == 3 and result['encoded'] == [3]
    assert result['dtype'] == 'torch.bfloat16'


def test_failed_requested_backend_reuses_only_actual_torch_identity(tmp_path, snapshots, fp32_package):
    cold, cipher = fp32_package
    result = run_worker(tmp_path / 'runner-b', snapshot=snapshots[0], cache=str(tmp_path / 'runner-b' / 'cache'),
                        restore=str(cipher), bf16=True, backend='onnx_fp32')
    assert result['backend'] == 'torch' and result['namespace'] == cold['namespace']
    assert result['stats']['disk_hits'] == 3 and result['encoded'] == []
    assert result['score_digest'] == cold['score_digest']
