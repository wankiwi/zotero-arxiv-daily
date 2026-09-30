import numpy as np
import pytest
from zotero_arxiv_daily.reranker.embedding_cache import EmbeddingCache


def test_persistent_cache_exact_roundtrip_without_plaintext(tmp_path):
    namespace = {'model':'test','revision':'immutable','prompt':'document','dtype':'float32'}
    text = 'PRIVATE_ZOTERO_ABSTRACT_SENTINEL'
    vector = np.array([1.0, 2.0], dtype=np.float32)
    cache = EmbeddingCache(namespace, tmp_path)
    cache.put(text, vector)
    restored = EmbeddingCache(namespace, tmp_path)
    assert np.array_equal(restored.get(text), vector)
    assert restored.get(text + '!') is None
    for file in tmp_path.rglob('*.npz'):
        assert text.encode() not in file.read_bytes()
        assert file.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('change', [{'revision':'new'}, {'model':'other'}, {'prompt':'query'}, {'dtype':'float64'}, {'task':'other'}])
def test_namespace_changes_invalidate(tmp_path, change):
    ns = {'model':'m','revision':'r','prompt':'doc','dtype':'float32'}
    EmbeddingCache(ns, tmp_path).put('text', np.array([1.0]))
    assert EmbeddingCache(ns | change, tmp_path).get('text') is None


def test_corrupt_entry_recomputed(tmp_path):
    cache = EmbeddingCache({}, tmp_path)
    cache.put('text', np.array([1.0]))
    next(tmp_path.rglob('*.npz')).write_bytes(b'broken')
    restored = EmbeddingCache({}, tmp_path)
    assert restored.get('text') is None
    restored.put('text', np.array([2.0]))
    assert np.array_equal(EmbeddingCache({}, tmp_path).get('text'), [2.0])


@pytest.mark.parametrize('vector', [np.array([0.0]), np.array([np.nan]), np.array([[1.0]]), np.array([1])])
def test_invalid_vectors_rejected(vector):
    with pytest.raises(ValueError): EmbeddingCache({}).put('text', vector)


def test_disk_write_failure_retains_memory(tmp_path, monkeypatch):
    cache = EmbeddingCache({}, tmp_path)
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr('zotero_arxiv_daily.reranker.embedding_cache.os.replace', fail)
    cache.put('text', np.array([1.0]))
    assert np.array_equal(cache.get('text'), [1.0])
    assert not list(cache.directory.iterdir())


def test_unavailable_directory_uses_memory(tmp_path):
    file = tmp_path / 'file'
    file.write_text('not a directory')
    cache = EmbeddingCache({}, file)
    cache.put('text', np.array([1.0]))
    assert cache.directory is None and np.array_equal(cache.get('text'), [1.0])
