"""Explicit, default-baseline ranking ablations; no delivery or remote services."""
from dataclasses import dataclass
import math
from numbers import Real
import numpy as np
from ..identity import normalize_doi


@dataclass(frozen=True)
class Experiments:
    keyword_prompt: str = 'document'
    text_mode: str = 'abstract'
    deduplicate_corpus: bool = False
    corpus_aggregation: str = 'mean'
    keyword_aggregation: str = 'mean'
    top_k: int = 5
    temperature: float = 0.1


def settings(config):
    raw = config.get('reranker', {}).get('experiments', {}) if config is not None else {}
    options = Experiments(**dict(raw or {}))
    if options.keyword_prompt not in ('document', 'query') or options.text_mode not in ('abstract', 'title_abstract'):
        raise ValueError('Invalid experimental prompt or text mode')
    if type(options.deduplicate_corpus) is not bool:
        raise ValueError('deduplicate_corpus must be boolean')
    if any(mode not in ('mean', 'top_k', 'softmax') for mode in (options.corpus_aggregation, options.keyword_aggregation)):
        raise ValueError('Experimental aggregation must be mean, top_k or softmax')
    if type(options.top_k) is not int or options.top_k < 1:
        raise ValueError('top_k must be a positive integer')
    t = options.temperature
    if isinstance(t, bool) or not isinstance(t, Real) or not math.isfinite(t) or t < 1e-6:
        raise ValueError('temperature must be finite and >= 0.000001')
    return options


def paper_text(paper, mode):
    abstract = paper.abstract.strip()
    if mode == 'title_abstract' and abstract:
        return paper.title.strip()+'\n\n'+abstract
    return abstract or paper.title


def unique_corpus(corpus):
    """Keep the newest exact normalized DOI; unknown/different DOIs stay separate."""
    result, seen = [], set()
    for paper in sorted(corpus, key=lambda p: p.added_date, reverse=True):
        doi = normalize_doi(paper.doi)
        if doi and doi in seen:
            continue
        if doi:
            seen.add(doi)
        result.append(paper)
    return result


def aggregate(similarity, weights, mode, top_k, temperature):
    """Top-k/softmax retain the baseline recency prior, renormalized per row."""
    if mode == 'mean':
        return (similarity * weights).sum(axis=1)
    if mode == 'top_k':
        indices = np.argsort(-similarity, axis=1, kind='stable')[:, :min(top_k, similarity.shape[1])]
        selected = np.take_along_axis(similarity, indices, axis=1)
        prior = weights[indices]
        return (selected * (prior/prior.sum(axis=1, keepdims=True))).sum(axis=1)
    logits = (similarity-similarity.max(axis=1, keepdims=True))/temperature
    prior = np.exp(logits) * weights
    return (similarity*(prior/prior.sum(axis=1, keepdims=True))).sum(axis=1)
