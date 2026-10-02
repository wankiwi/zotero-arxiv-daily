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
        if local.get('revision'):
            load_options['revision'] = local.revision
        identity = (local.model, local.get('revision'), local.get('cpu_dtype', 'auto'))
        if getattr(self, '_model_identity', None) != identity:
            self._encoder = SentenceTransformer(local.model, **load_options)
            self._model_identity = identity
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
        if kwargs.get('precision', 'float32') != 'float32' or kwargs.get('output_value', 'sentence_embedding') != 'sentence_embedding':
            raise ValueError('Local ranking requires floating sentence embeddings')
        kwargs['convert_to_numpy'] = True
        kwargs['convert_to_tensor'] = False
        resolved = getattr(encoder[0].auto_model.config, '_commit_hash', None)
        namespace = {'schema': 1, 'model': local.model, 'revision': resolved,
                     'encode': kwargs, 'prompts': encoder.prompts, 'default_prompt': encoder.default_prompt_name,
                     'max_seq_length': encoder.max_seq_length, 'dtype': str(next(encoder.parameters()).dtype),
                     'device': str(encoder.device), 'threads': torch.get_num_threads(),
                     'libraries': {name: version(name) for name in ('sentence-transformers', 'transformers', 'torch', 'tokenizers')}}
        directory = local.get('cache_dir')
        if directory and not resolved:
            logger.warning('Persistent embedding cache disabled: model has no resolved immutable revision')
            directory = None
        namespace_key = digest(namespace)
        if getattr(self, '_cache_namespace', None) != namespace_key or getattr(self, '_cache_directory', None) != directory:
            self._cache = EmbeddingCache(namespace, directory)
            self._cache_namespace, self._cache_directory = namespace_key, directory
        texts = list(dict.fromkeys(s1 + s2))
        lookup = {text: self._cache.get(text) for text in texts}
        missing = [text for text in texts if lookup[text] is None]
        logger.info(f'Local embeddings: device={encoder.device}, dtype={namespace["dtype"]}, threads={torch.get_num_threads()}, '
                    f'unique={len(texts)}, cached={len(texts)-len(missing)}, pending={len(missing)}, batch_size={kwargs.get("batch_size", 32)}')
        if missing:
            lengths = encoder.tokenizer(missing, padding=False, truncation=False, return_length=True)['length']
            logger.info(f'Embedding token lengths: median={int(np.median(lengths))}, p95={int(np.percentile(lengths, 95))}, '
                        f'max={max(lengths)}, model_limit={encoder.max_seq_length}')
            started = perf_counter()
            features = encoder.encode(missing, **kwargs, show_progress_bar=True)
            if len(features) != len(missing):
                raise ValueError('Local encoder returned incomplete embeddings')
            for text, vector in zip(missing, features):
                self._cache.put(text, vector)
                lookup[text] = vector
            logger.info(f'Embedded {len(missing)} texts in {perf_counter()-started:.2f}s')
        left, right = np.stack([lookup[t] for t in s1]), np.stack([lookup[t] for t in s2])
        profile = interest_profile(self.config)
        if profile.keywords and profile.keyword_weight:
            # Both components use cosine, even if a model config selects dot product.
            left = left / np.linalg.norm(left, axis=1, keepdims=True)
            right = right / np.linalg.norm(right, axis=1, keepdims=True)
            return left @ right.T
        sim = encoder.similarity(left, right)
        return sim.cpu().numpy()
