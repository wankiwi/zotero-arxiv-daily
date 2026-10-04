"""Synthetic cross-runner cache probe; no application, model, or delivery calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

import numpy as np

from zotero_arxiv_daily.reranker.embedding_cache import EmbeddingCache
from zotero_arxiv_daily.reranker.encrypted_cache import MAGIC, decode_key, seal, unseal


NAMESPACE = {'validation': 'synthetic-only-v1', 'dimension': 4, 'dtype': 'float32'}
TEXTS = ('synthetic-vector-a', 'synthetic-vector-b', 'synthetic-vector-c')
VECTORS = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0.5, -0.25, 0.75, 1]], dtype=np.float32)


def run(phase, package, key):
    """Use a fresh directory and fresh cache object for each independent phase."""
    package = Path(package)
    with tempfile.TemporaryDirectory(prefix='synthetic-cache-validation-') as directory:
        if phase == 'save':
            cache = EmbeddingCache(NAMESPACE, directory, dimension=4, dtype='float32')
            for text, vector in zip(TEXTS, VECTORS):
                assert cache.get(text) is None, 'Save phase must start cold'
                cache.put(text, vector)
            assert cache.stats == dict(memory_hits=0, disk_hits=0, misses=3, corrupt=0, writes=3)
            blob = seal(directory, key)
            assert blob.startswith(MAGIC) and not blob.startswith(b'PK')
            package.parent.mkdir(parents=True, exist_ok=True)
            with package.open('xb') as output:
                output.write(blob)
            os.chmod(package, 0o600)
        elif phase == 'restore':
            blob = package.read_bytes()
            assert unseal(blob, key, directory) == 3, 'Expected exactly three vectors'
            cache = EmbeddingCache(NAMESPACE, directory, dimension=4, dtype='float32')
            for text, vector in zip(TEXTS, VECTORS):
                actual = cache.get(text)
                assert actual is not None and np.array_equal(actual, vector), 'Restored vector mismatch'
            assert cache.stats == dict(memory_hits=0, disk_hits=3, misses=0, corrupt=0, writes=0)
        else:
            raise ValueError('Unsupported phase')
        return {'phase': phase, 'synthetic_vectors': 3, 'ciphertext_sha256': hashlib.sha256(blob).hexdigest(),
                'stats': cache.stats, 'vectors_equal': True if phase == 'restore' else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['save', 'restore'])
    parser.add_argument('--package', type=Path, required=True)
    args = parser.parse_args()
    if (os.environ.get('GITHUB_REPOSITORY') != 'wankiwi/zotero-arxiv-daily'
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'):
        raise SystemExit('Synthetic cache validation requires trusted main workflow_dispatch')
    key = decode_key(os.environ.get('EMBEDDING_CACHE_KEY', ''))
    print(json.dumps(run(args.phase, args.package, key), sort_keys=True))


if __name__ == '__main__':
    main()
