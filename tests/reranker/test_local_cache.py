from types import SimpleNamespace
import numpy as np
import pytest
import torch
from zotero_arxiv_daily.reranker.local import LocalReranker


@pytest.fixture
def encoder(monkeypatch):
    class Encoder:
        def __init__(self, *args, **kwargs):
            self.calls=[]
            self.device=torch.device('cpu')
            self.prompts={'document':'Document: ', 'query':'Query: '}
            self.default_prompt_name=None
            self.max_seq_length=8192
            self.weight=torch.tensor([1.0],dtype=torch.bfloat16)
        def __getitem__(self, index):return SimpleNamespace(auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash='immutable')))
        def parameters(self):return iter([self.weight])
        def float(self):self.weight=self.weight.float();return self
        def tokenizer(self, texts, **kwargs):return {'length':[len(t) for t in texts]}
        def encode(self, texts, **kwargs):
            self.calls.append(list(texts))
            return np.array([[len(t),sum(map(ord,t))] for t in texts], dtype=np.float32)
        def similarity(self,left,right):return torch.from_numpy(left @ right.T)
    monkeypatch.setattr('sentence_transformers.SentenceTransformer',Encoder)
    return Encoder


def test_memory_and_local_persistent_hits_preserve_scores(config, encoder, tmp_path):
    config.reranker.local.cache_dir=str(tmp_path)
    ranker=LocalReranker(config)
    first=ranker.get_similarity_score(['a','b','a'],['a','c'])
    assert ranker._encoder.calls==[['a','b','c']]
    assert np.array_equal(first,ranker.get_similarity_score(['a','b','a'],['a','c']))
    assert len(ranker._encoder.calls)==1
    fresh=LocalReranker(config)
    assert np.array_equal(first,fresh.get_similarity_score(['a','b','a'],['a','c']))
    assert not fresh._encoder.calls
    fresh.get_similarity_score(['a','new'],['c'])
    assert fresh._encoder.calls==[['new']]


def test_prompt_change_invalidates(config, encoder):
    ranker=LocalReranker(config)
    ranker.get_similarity_score(['a'],['b'])
    config.reranker.local.encode_kwargs.prompt_name='query'
    ranker.get_similarity_score(['a'],['b'])
    assert len(ranker._encoder.calls)==2


def test_cpu_without_bf16_upcasts_same_encoder(config, encoder, monkeypatch):
    monkeypatch.setattr('zotero_arxiv_daily.reranker.local.cpu_has_bf16',lambda:False)
    ranker=LocalReranker(config)
    ranker.get_similarity_score(['a'],['b'])
    assert next(ranker._encoder.parameters()).dtype == torch.float32


def test_cpu_native_preserves_dtype(config, encoder, monkeypatch):
    config.reranker.local.cpu_dtype='native'
    monkeypatch.setattr('zotero_arxiv_daily.reranker.local.cpu_has_bf16',lambda:False)
    ranker=LocalReranker(config)
    ranker.get_similarity_score(['a'],['b'])
    assert next(ranker._encoder.parameters()).dtype == torch.bfloat16


def test_model_revision_change_reloads(config, encoder):
    ranker=LocalReranker(config)
    ranker.get_similarity_score(['a'],['b'])
    previous=ranker._encoder
    config.reranker.local.revision='new-revision'
    ranker.get_similarity_score(['a'],['b'])
    assert ranker._encoder is not previous


def test_cpu_thread_count_respects_quota(monkeypatch):
    from zotero_arxiv_daily.reranker.local import available_cpu_threads
    monkeypatch.setattr('os.sched_getaffinity',lambda pid:set(range(8)))
    monkeypatch.setattr('pathlib.Path.read_text',lambda self:'200000 100000')
    assert available_cpu_threads()==2


def test_zero_threads_rejected(config, encoder):
    config.reranker.local.cpu_threads=0
    with pytest.raises(ValueError, match='positive'):
        LocalReranker(config).get_similarity_score(['a'],['b'])


def test_prompt_definition_change_invalidates(config, encoder):
    ranker=LocalReranker(config)
    ranker.get_similarity_score(['a'],['b'])
    ranker._encoder.prompts['document']='New document role: '
    ranker.get_similarity_score(['a'],['b'])
    assert len(ranker._encoder.calls)==2
