from abc import ABC, abstractmethod
from omegaconf import DictConfig
from ..protocol import Paper, CorpusPaper
import numpy as np
from typing import Type
from numbers import Real
import math
from ..interest_profile import interest_profile
def missing_abstract_factor(config):
    settings = config.get('reranker', {}) if config is not None else {}
    value = settings.get('missing_abstract_factor', 0.8)
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('reranker.missing_abstract_factor must be a finite number between 0 and 1')
    return float(value)


class BaseReranker(ABC):
    def __init__(self, config:DictConfig):
        self.config = config
        interest_profile(config)
        missing_abstract_factor(config)

    def rerank(self, candidates:list[Paper], corpus:list[CorpusPaper]) -> list[Paper]:
        if not candidates:
            return []
        factor = missing_abstract_factor(self.config)
        profile = interest_profile(self.config)
        keyword_weight, zotero_weight = profile.effective_weights(bool(corpus))
        corpus = sorted(corpus, key=lambda x: x.added_date, reverse=True) if zotero_weight else []
        keywords = list(profile.keywords) if keyword_weight else []
        references = [c.abstract.strip() or c.title for c in corpus] + keywords
        for candidate in candidates:
            candidate.scoring_basis = "abstract" if candidate.abstract.strip() else "title only"
        sim = self.get_similarity_score([c.abstract.strip() or c.title for c in candidates], references)
        if sim.shape != (len(candidates), len(references)) or not np.isfinite(sim).all():
            raise ValueError("Reranker returned invalid similarity scores")
        bounded_scores = keyword_weight or (factor < 1 and any(p.scoring_basis == "title only" for p in candidates))
        if bounded_scores and (np.abs(sim) > 1.00001).any():
            raise ValueError("Interest fusion and missing-abstract adjustment require cosine similarity in [-1, 1]")
        zotero_scores = np.zeros(len(candidates))
        keyword_scores = np.zeros(len(candidates))
        if corpus:
            decay = 1 / (1 + np.log10(np.arange(len(corpus)) + 1))
            zotero_scores = (sim[:, :len(corpus)] * (decay / decay.sum())).sum(axis=1)
        if keywords:
            keyword_scores = np.clip(sim[:, len(corpus):], -1, 1).mean(axis=1)
        scores = (keyword_weight * keyword_scores + zotero_weight * zotero_scores) * 10
        for score, keyword_score, zotero_score, candidate in zip(scores, keyword_scores, zotero_scores, candidates):
            candidate.missing_abstract_factor = factor if candidate.scoring_basis == 'title only' else 1.0
            candidate.raw_score = (float(np.clip(score, -10, 10)) if candidate.missing_abstract_factor < 1 else float(score))
            # Positive scores receive the configured multiplier. Negative scores
            # divide by it so missing evidence can never improve relevance.
            # Always start from fresh similarity, never a previously adjusted score.
            applied = candidate.missing_abstract_factor
            raw = candidate.raw_score
            if applied == 1:
                candidate.score = raw
            elif applied == 0:
                candidate.score = -10.0
            elif raw >= 0:
                candidate.score = raw * applied
            else:
                candidate.score = max(-10.0, raw / applied)
            candidate.keyword_score = float(keyword_score * 10) if keywords else None
            candidate.zotero_score = float(zotero_score * 10) if corpus else None
            candidate.interest_keyword_weight = keyword_weight
            candidate.interest_zotero_weight = zotero_weight
        candidates = sorted(candidates,key=lambda x: x.score,reverse=True)
        return candidates
    
    @abstractmethod
    def get_similarity_score(self, s1:list[str], s2:list[str]) -> np.ndarray:
        raise NotImplementedError

registered_rerankers = {}

def register_reranker(name:str):
    def decorator(cls):
        registered_rerankers[name] = cls
        return cls
    return decorator

def get_reranker_cls(name:str) -> Type[BaseReranker]:
    if name not in registered_rerankers:
        raise ValueError(f"Reranker {name} not found")
    return registered_rerankers[name]