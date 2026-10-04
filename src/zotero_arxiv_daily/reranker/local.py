from ..interest_profile import interest_profile
from .base import BaseReranker, register_reranker
from .embedding_cache import EmbeddingCache, digest
import logging
import os
from pathlib import Path
from time import perf_counter
import warnings
from importlib.metadata import version

from loguru import logger
from omegaconf import OmegaConf
import numpy as np


def available_cpu_threads():
    count = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
    try:
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota != 'max':
            count = min(count, max(1, int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    return count


def cpu_has_bf16():
    try:
        flags = Path('/proc/cpuinfo').read_text().split()
        return 'avx512_bf16' in flags or 'amx_bf16' in flags
    except OSError:
        return False


@register_reranker('local')
class LocalReranker(BaseReranker):
    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        if not s1 or not s2:
            return np.empty((len(s1), len(s2)))
        vectors = self._encode_texts(s1+s2)
        return self._similarity(vectors[:len(s1)], vectors[len(s1):])

    def get_rank_similarity(self, s1, s2, corpus_count, keyword_prompt):
        if keyword_prompt == 'document' or len(s2) == corpus_count:
            return self.get_similarity_score(s1, s2)
        if not s1 or not s2:
            return np.empty((len(s1), len(s2)))
        documents = self._encode_texts(s1+s2[:corpus_count], prompt_name='document')
        backend = self._backend
        queries = self._encode_texts(s2[corpus_count:], prompt_name='query')
        if self._backend != backend:
            # Query inference may fall back: never mix document/query backends.
            documents = self._encode_texts(s1+s2[:corpus_count], prompt_name='document')
        references = np.concatenate([documents[len(s1):], queries])
        return self._similarity(documents[:len(s1)], references)

    def _encode_texts(self, texts, prompt_name=None):
        import torch
        from sentence_transformers import SentenceTransformer
        local = self.config.reranker.local
        if not self.config.executor.debug:
            from transformers.utils import logging as transformers_logging
            from huggingface_hub.utils import logging as hf_logging
            transformers_logging.set_verbosity_error()
            hf_logging.set_verbosity_error()
            for name in ('sentence_transformers', 'transformers', 'huggingface_hub'):
                logging.getLogger(name).setLevel(logging.ERROR)
            warnings.filterwarnings('ignore', category=FutureWarning)
        load_options = {'trust_remote_code': True}
        from .onnx_encoder import MODEL, REVISION
        revision = local.get('revision') or (REVISION if local.model == MODEL else None)
        if revision:
            load_options['revision'] = revision
        backend = local.get('backend', 'torch')
        if backend not in ('torch', 'onnx_fp32', 'onnx_int8'):
            raise ValueError('local.backend must be torch, onnx_fp32 or onnx_int8')
        if type(local.get('onnx_fallback', True)) is not bool:
            raise ValueError('local.onnx_fallback must be boolean')
        identity = (local.model, revision, local.get('cpu_dtype', 'auto'), backend,
                    local.get('onnx_directory'), local.get('cpu_threads'))
        if getattr(self, '_model_identity', None) != identity:
            started = perf_counter()
            self._backend = 'torch'
            if backend != 'torch' and getattr(self, '_failed_onnx_identity', None) != identity:
                try:
                    from .onnx_encoder import OnnxEncoder
                    threads = int(local.get('cpu_threads') or available_cpu_threads())
                    if threads < 1:
                        raise ValueError('local.cpu_threads must be positive')
                    self._encoder = OnnxEncoder(local.model, revision, backend, local.get('onnx_directory'), threads)
                    self._backend = backend
                except Exception as exc:
                    if not local.get('onnx_fallback', True):
                        raise
                    self._failed_onnx_identity = identity
                    logger.warning(f'Optional ONNX load failed ({type(exc).__name__}); using PyTorch')
            if self._backend == 'torch':
                self._encoder = SentenceTransformer(local.model, **load_options)
            self._model_identity = identity
            logger.info(f'Embedding model prepared: backend={self._backend}, seconds={perf_counter()-started:.3f}')
        encoder = self._encoder
        mode = local.get('cpu_dtype', 'auto')
        if mode not in ('auto', 'native', 'float32'):
            raise ValueError('local.cpu_dtype must be auto, native or float32')
        if encoder.device.type == 'cpu':
            configured_threads = local.get('cpu_threads')
            threads = int(configured_threads if configured_threads is not None else available_cpu_threads())
            if threads < 1:
                raise ValueError('local.cpu_threads must be positive')
            torch.set_num_threads(threads)
            # BF16 is very slow when emulated on AVX2-only hosted runners.
            # Upcast the same weights; do not quantize, truncate or change model.
            if mode == 'float32' or (mode == 'auto' and not cpu_has_bf16()):
                encoder.float()
        kwargs = OmegaConf.to_container(local.encode_kwargs, resolve=True) if local.encode_kwargs else {}
        kwargs = dict(kwargs)
        kwargs.pop('show_progress_bar', None)
        if prompt_name is not None:
            if prompt_name not in encoder.prompts:
                raise ValueError('Experimental keyword query requires named document/query prompts')
            if kwargs.get('prompt') is not None:
                raise ValueError('Experimental named prompts cannot override an explicit prompt')
            kwargs['prompt_name'] = prompt_name
        if kwargs.get('precision', 'float32') != 'float32' or kwargs.get('output_value', 'sentence_embedding') != 'sentence_embedding':
            raise ValueError('Local ranking requires floating sentence embeddings')
        kwargs['convert_to_numpy'] = True
        kwargs['convert_to_tensor'] = False
        resolved = getattr(encoder[0].auto_model.config, '_commit_hash', None)
        dimension = kwargs.get('truncate_dim') or (encoder.get_sentence_embedding_dimension() if hasattr(encoder, 'get_sentence_embedding_dimension') else None)
        namespace = {'schema': 2, 'privacy': 'private', 'backend': self._backend,
                     'artifacts': getattr(encoder, 'artifact_identity', None), 'dimension': dimension, 'output_dtype': 'float32', 'model': local.model, 'revision': resolved,
                     'encode': kwargs, 'prompts': encoder.prompts, 'default_prompt': encoder.default_prompt_name,
                     'max_seq_length': encoder.max_seq_length, 'dtype': str(next(encoder.parameters()).dtype),
                     'device': str(encoder.device), 'threads': torch.get_num_threads(),
                     'libraries': {name: version(name) for name in ('sentence-transformers', 'transformers', 'torch', 'tokenizers')}}
        directory = os.environ.get('PRIVATE_EMBEDDING_CACHE_DIR') or local.get('cache_dir')
        if directory and not resolved:
            logger.warning('Persistent embedding cache disabled: model has no resolved immutable revision')
            directory = None
        namespace_key = digest(namespace)
        if not hasattr(self, '_role_caches'):
            self._role_caches = {}
        cache_key = (namespace_key, directory)
        if cache_key not in self._role_caches:
            self._role_caches[cache_key] = EmbeddingCache(namespace, directory, dimension=dimension, dtype='float32')
        self._cache = self._role_caches[cache_key]
        self._cache_namespace, self._cache_directory = namespace_key, directory
        # This pool is PRIVATE: even public-paper membership reflects user filtering.
        # Never export this directory as a public candidate cache.
        requested = texts
        texts = list(dict.fromkeys(texts))
        lookup_started = perf_counter()
        stats_before = dict(self._cache.stats)
        lookup = {text: self._cache.get(text) for text in texts}
        missing = [text for text in texts if lookup[text] is None]
        delta = {key: self._cache.stats[key]-stats_before[key] for key in stats_before}
        logger.info(f'Private embedding cache: memory_hits={delta["memory_hits"]}, disk_hits={delta["disk_hits"]}, '
                    f'misses={delta["misses"]}, corrupt={delta["corrupt"]}, lookup_seconds={perf_counter()-lookup_started:.3f}')
        logger.info(f'Local embeddings: device={encoder.device}, dtype={namespace["dtype"]}, threads={torch.get_num_threads()}, '
                    f'unique={len(texts)}, cached={len(texts)-len(missing)}, pending={len(missing)}, batch_size={kwargs.get("batch_size", 32)}')
        if missing:
            lengths = encoder.tokenizer(missing, padding=False, truncation=False, return_length=True)['length']
            logger.info(f'Embedding token lengths: median={int(np.median(lengths))}, p95={int(np.percentile(lengths, 95))}, '
                        f'max={max(lengths)}, model_limit={encoder.max_seq_length}')
            started = perf_counter()
            try:
                features = encoder.encode(missing, **kwargs, show_progress_bar=True)
            except Exception as exc:
                if self._backend == 'torch' or not local.get('onnx_fallback', True):
                    raise
                logger.warning(f'Optional ONNX inference failed ({type(exc).__name__}); recomputing with PyTorch')
                self._failed_onnx_identity = identity
                self._model_identity = None
                return self._encode_texts(requested, prompt_name)
            if len(features) != len(missing):
                raise ValueError('Local encoder returned incomplete embeddings')
            for text, vector in zip(missing, features):
                self._cache.put(text, vector)
                lookup[text] = vector
            logger.info(f'Embedded {len(missing)} texts in {perf_counter()-started:.2f}s')
        return np.stack([lookup[t] for t in requested])

    def _similarity(self, left, right):
        similarity_started = perf_counter()
        profile = interest_profile(self.config)
        if profile.keywords and profile.keyword_weight:
            left = left / np.linalg.norm(left, axis=1, keepdims=True)
            right = right / np.linalg.norm(right, axis=1, keepdims=True)
            result = left @ right.T
        else:
            result = self._encoder.similarity(left, right).cpu().numpy()
        logger.info(f'Embedding similarity: seconds={perf_counter()-similarity_started:.3f}, candidates={len(left)}, references={len(right)}')
        return result
