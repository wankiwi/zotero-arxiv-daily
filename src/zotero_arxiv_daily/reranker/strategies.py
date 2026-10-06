"""Named ranking strategies; profile centroids deliberately remain unnormalized."""
from dataclasses import dataclass
import math
from numbers import Real

import numpy as np

from ..identity import canonical_doi
from .experiments import Experiments, settings

DEFAULT_STRATEGY = 'multi_interest_profile'
STRATEGIES = (DEFAULT_STRATEGY, 'legacy_mean')
AFFINITY_ELEMENTS = 65536  # Bound the extra reference-by-direction matrix per request.


@dataclass(frozen=True)
class ProfileSettings:
    assignment_min_cosine: float = 0.30
    max_references_per_direction: int = 24
    min_references_per_direction: int = 5
    min_effective_support: float = 3.0
    shrinkage: float = 5.0


def strategy_settings(config):
    raw = config.get('reranker', {}) if config is not None else {}
    name = raw.get('strategy', DEFAULT_STRATEGY)
    if name not in STRATEGIES:
        raise ValueError('reranker.strategy must be multi_interest_profile or legacy_mean')
    supplied = raw.get('multi_interest_profile', {})
    if supplied is None or not hasattr(supplied, 'items'):
        raise ValueError('reranker.multi_interest_profile must be a mapping')
    try:
        options = ProfileSettings(**dict(supplied))
    except TypeError as exc:
        raise ValueError('Unknown multi_interest_profile parameter') from exc
    for key in ('assignment_min_cosine', 'min_effective_support', 'shrinkage'):
        value = getattr(options, key)
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            raise ValueError(f'multi_interest_profile.{key} must be a finite number')
    if not -1 <= options.assignment_min_cosine <= 1:
        raise ValueError('assignment_min_cosine must be between -1 and 1')
    if options.min_effective_support <= 0 or options.shrinkage < 0:
        raise ValueError('min_effective_support must be positive and shrinkage nonnegative')
    for key in ('max_references_per_direction', 'min_references_per_direction'):
        if type(getattr(options, key)) is not int or getattr(options, key) < 1:
            raise ValueError(f'multi_interest_profile.{key} must be a positive integer')
    if options.min_references_per_direction > options.max_references_per_direction:
        raise ValueError('min_references_per_direction cannot exceed the maximum')
    if name == DEFAULT_STRATEGY and settings(config) != Experiments():
        raise ValueError('multi_interest_profile requires default experiments; select legacy_mean for ablations')
    return name, options


def directional_scores(corpus, affinity, candidate_corpus, candidate_keywords, weights, options):
    """Equivalent to dotting unit candidate vectors with support-shrunk raw centroids.

    Corpus is already sorted newest first. Deduplication affects profiles only;
    the global mean still includes every reference, matching the evaluated model.
    No candidate-by-reference-by-direction tensor or reference self-matrix.
    """
    n, directions = candidate_keywords.shape
    prior = 1 / (1 + np.log10(np.arange(len(corpus)) + 1))
    prior /= prior.sum() if len(prior) else 1
    global_mean = (candidate_corpus * prior).sum(axis=1)
    library = np.repeat(global_mean[:, None], directions, axis=1)
    assignment = affinity.argmax(axis=1) if len(corpus) else np.empty(0, dtype=int)
    unique, seen = [], set()
    for i, paper in enumerate(corpus):
        doi = canonical_doi(paper.doi)
        if doi and doi in seen:
            continue
        if doi:
            seen.add(doi)
        unique.append(i)
    eligible = [i for i in unique if affinity[i, assignment[i]] >= options.assignment_min_cosine]
    support = []
    for direction in range(directions):
        members = [i for i in eligible if assignment[i] == direction]
        selected = sorted(members, key=lambda i: (-float(affinity[i, direction]), i))[:options.max_references_per_direction]
        effective, reliability = 0.0, 0.0
        if selected:
            weights_in_profile = prior[selected] / prior[selected].sum()
            effective = float(1 / np.square(weights_in_profile).sum())
            if len(selected) >= options.min_references_per_direction and effective >= options.min_effective_support:
                reliability = effective / (effective + options.shrinkage)
                profile_mean = (candidate_corpus[:, selected] * weights_in_profile).sum(axis=1)
                library[:, direction] = reliability * profile_mean + (1 - reliability) * global_mean
        support.append({'direction_index': direction, 'assigned_unique_eligible': len(members),
                        'profile_n': len(selected), 'effective_support': effective,
                        'reliability': reliability, 'fallback_global': reliability == 0.0})
    keyword_weight, library_weight = weights
    joint = keyword_weight * candidate_keywords + library_weight * library
    winning = joint.argmax(axis=1)  # Stable ties use the configured keyword order.
    row = np.arange(n)
    return joint[row, winning], candidate_keywords[row, winning], library[row, winning], winning, support
