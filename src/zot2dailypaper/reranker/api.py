from .base import BaseReranker, register_reranker
from openai import OpenAI
import numpy as np


@register_reranker('api')
class ApiReranker(BaseReranker):
    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        if not s1 or not s2:
            return np.empty((len(s1), len(s2)))
        if not hasattr(self, '_client'):
            self._client = OpenAI(api_key=self.config.reranker.api.key, base_url=self.config.reranker.api.base_url)
            self._embeddings = {}
        batch_size = int(self.config.reranker.api.get('batch_size') or 64)
        if batch_size < 1:
            raise ValueError('Embedding batch_size must be positive')
        texts = list(dict.fromkeys(t for t in s1 + s2 if t not in self._embeddings))
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = self._client.embeddings.create(input=batch, model=self.config.reranker.api.model)
            # Embedding response order is not guaranteed; respect the supplied index.
            ordered = sorted(response.data, key=lambda r: r.index)
            if [r.index for r in ordered] != list(range(len(batch))):
                raise ValueError('Embedding API returned incomplete or invalid indices')
            for text, result in zip(batch, ordered):
                vector = np.asarray(result.embedding, dtype=float)
                norm = np.linalg.norm(vector)
                if vector.ndim != 1 or not np.isfinite(vector).all() or not norm:
                    raise ValueError('Embedding API returned an invalid or zero vector')
                self._embeddings[text] = vector / norm
        left = np.stack([self._embeddings[t] for t in s1])
        right = np.stack([self._embeddings[t] for t in s2])
        return left @ right.T
