from abc import ABC, abstractmethod
from omegaconf import DictConfig
from ..protocol import Paper, CorpusPaper
import numpy as np
from typing import Type
from numbers import Real
import math
from ..interest_profile import interest_profile
from ..scores import to_display, SCORE_SCHEMA
from .experiments import settings, paper_text, unique_corpus, aggregate
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
        settings(config)

    def rerank(self, candidates:list[Paper], corpus:list[CorpusPaper]) -> list[Paper]:
        if not candidates:
            return []
        factor = missing_abstract_factor(self.config)
        options = settings(self.config)
        if options.deduplicate_corpus:
            corpus = unique_corpus(corpus)
        profile = interest_profile(self.config)
        keyword_weight, zotero_weight = profile.effective_weights(bool(corpus))
        corpus = sorted(corpus, key=lambda x: x.added_date, reverse=True) if zotero_weight else []
        keywords = list(profile.keywords) if keyword_weight else []
        references = [paper_text(c, options.text_mode) for c in corpus] + keywords
        for candidate in candidates:
            candidate.scoring_basis = "abstract" if candidate.abstract.strip() else "title only"
        sim = self.get_rank_similarity([paper_text(c, options.text_mode) for c in candidates], references,
                                       len(corpus), options.keyword_prompt)
        if sim.shape != (len(candidates), len(references)) or not np.isfinite(sim).all():
            raise ValueError("Reranker returned invalid similarity scores")
        if (np.abs(sim) > 1.00001).any():
            raise ValueError("Relevance scores require cosine similarity in [-1, 1]")
        zotero_scores = np.zeros(len(candidates))
        keyword_scores = np.zeros(len(candidates))
        if corpus:
            decay = 1 / (1 + np.log10(np.arange(len(corpus)) + 1))
            zotero_scores = aggregate(sim[:, :len(corpus)], decay/decay.sum(), options.corpus_aggregation,
                                      options.top_k, options.temperature)
        if keywords:
            values = np.clip(sim[:, len(corpus):], -1, 1)
            # Keep the baseline operation order bit-for-bit when disabled.
            keyword_scores = values.mean(axis=1) if options.keyword_aggregation == 'mean' else aggregate(
                values, np.ones(len(keywords))/len(keywords), options.keyword_aggregation, options.top_k, options.temperature)
        scores = (keyword_weight * keyword_scores + zotero_weight * zotero_scores) * 10
        for score, keyword_score, zotero_score, candidate in zip(scores, keyword_scores, zotero_scores, candidates):
            candidate.missing_abstract_factor = factor if candidate.scoring_basis == 'title only' else 1.0
            raw = float(np.clip(score, -10, 10))
            candidate.raw_score = to_display(raw)
            candidate.score_schema = SCORE_SCHEMA
            # Positive scores receive the configured multiplier. Negative scores
            # divide by it so missing evidence can never improve relevance.
            # Always start from fresh similarity, never a previously adjusted score.
            applied = candidate.missing_abstract_factor
            if applied == 1:
                candidate.score = raw
            elif applied == 0:
                candidate.score = -10.0
            elif raw >= 0:
                candidate.score = raw * applied
            else:
                candidate.score = max(-10.0, raw / applied)
            candidate.score = to_display(candidate.score)
            candidate.keyword_score = to_display(keyword_score * 10) if keywords else None
            candidate.zotero_score = to_display(zotero_score * 10) if corpus else None
            candidate.interest_keyword_weight = keyword_weight
            candidate.interest_zotero_weight = zotero_weight
        candidates = sorted(candidates,key=lambda x: x.score,reverse=True)
        return candidates
    
    def get_rank_similarity(self, s1, s2, corpus_count, keyword_prompt):
        if keyword_prompt != 'document' and len(s2) > corpus_count:
            raise ValueError('keyword_prompt=query requires a local encoder with named prompts')
        return self.get_similarity_score(s1, s2)

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
