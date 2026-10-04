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


def test_interest_weights_reuse_vectors_new_phrase_encodes_only_new_text(config, encoder):
    from omegaconf import OmegaConf
    from tests.canned_responses import make_sample_paper, make_sample_corpus
    config.interest_profile = OmegaConf.create({'keywords':['water'], 'keyword_weight':0.6, 'zotero_weight':0.4})
    ranker = LocalReranker(config)
    papers, corpus = [make_sample_paper()], make_sample_corpus(1)
    ranker.rerank(papers, corpus)
    assert len(ranker._encoder.calls) == 1
    config.interest_profile.keyword_weight = 0.9
    ranker.rerank(papers, corpus)
    assert len(ranker._encoder.calls) == 1
    assert papers[0].interest_keyword_weight == pytest.approx(0.9/1.3)
    config.interest_profile.keywords = ['proton transfer']
    ranker.rerank(papers, corpus)
    assert ranker._encoder.calls[-1] == ['proton transfer']
    assert -10 <= papers[0].score <= 10


def test_penalty_edit_uses_existing_embeddings_without_llm(config,encoder,monkeypatch):
    from tests.canned_responses import make_sample_paper,make_sample_corpus
    from omegaconf import OmegaConf
    from zotero_arxiv_daily.protocol import Paper
    monkeypatch.setattr(Paper,'generate_tldr',lambda *a,**kw:pytest.fail('Ranking must not call LLM'))
    config.interest_profile=OmegaConf.create({'keywords':['water']})
    ranker=LocalReranker(config)
    paper=make_sample_paper(abstract='');corpus=make_sample_corpus(1)
    ranker.rerank([paper],corpus)
    raw=paper.raw_score;calls=len(ranker._encoder.calls)
    config.reranker.missing_abstract_factor=0.5
    ranker.rerank([paper],corpus)
    assert len(ranker._encoder.calls)==calls
    assert paper.raw_score==raw and paper.score==pytest.approx(raw*0.5)
    paper.abstract='A newly recovered original abstract.'
    ranker.rerank([paper],corpus)
    assert ranker._encoder.calls[-1]==[paper.abstract]
    assert paper.score==paper.raw_score and paper.missing_abstract_factor==1


def test_optional_onnx_load_failure_falls_back_and_never_mixes_cache(config,encoder,tmp_path,monkeypatch):
    from zotero_arxiv_daily.reranker.onnx_encoder import OnnxEncoder
    config.reranker.local.backend='onnx_fp32';config.reranker.local.cache_dir=str(tmp_path)
    def fail(*a,**k):raise OSError('synthetic unavailable')
    monkeypatch.setattr(OnnxEncoder,'__init__',fail)
    ranker=LocalReranker(config);score=ranker.get_similarity_score(['a'],['b'])
    assert ranker._backend=='torch' and np.isfinite(score).all()
    assert ranker._failed_onnx_identity==ranker._model_identity
    assert ranker._cache.directory.parent.name=='private'
    config.reranker.local.onnx_fallback=False
    with pytest.raises(OSError):LocalReranker(config).get_similarity_score(['a'],['b'])


def test_backend_namespace_and_inference_fallback(config,encoder,monkeypatch):
    from zotero_arxiv_daily.reranker import onnx_encoder
    class FakeOnnx(encoder):
        def __init__(self,*a,**kw):super().__init__();self.artifact_identity={'graph':'verified'}
    monkeypatch.setattr(onnx_encoder,'OnnxEncoder',FakeOnnx)
    config.reranker.local.backend='onnx_fp32'
    ranker=LocalReranker(config);ranker.get_similarity_score(['a'],['b']);namespace=ranker._cache_namespace
    def fail(*a,**k):raise RuntimeError('synthetic inference failure')
    ranker._encoder.encode=fail
    ranker.get_similarity_score(['new'],['b'])
    assert ranker._backend=='torch' and ranker._cache_namespace!=namespace
    assert ranker._encoder.calls==[['new','b']]
