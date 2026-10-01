"""Quota selection shared by new recommendations and durable pending deliveries."""
import random
from collections.abc import Mapping
from loguru import logger
from omegaconf import DictConfig

GROUPS = ('journals', 'preprints', 'random')


def quotas_for(config):
    quotas = config.get('quotas')
    if quotas is None:
        return None
    if not isinstance(quotas, (Mapping, DictConfig)) or set(quotas) != set(GROUPS):
        raise ValueError('executor.quotas requires journals, preprints, and random')
    if any(type(v) is not int or v < 0 for v in quotas.values()) or not sum(quotas.values()):
        raise ValueError('executor.quotas must be nonnegative integers with a positive total')
    if sum(quotas.values()) > int(config.max_paper_num):
        raise ValueError('max_paper_num must be at least the sum of executor.quotas')
    return dict(quotas)


def publication_group(paper):
    return 'journals' if paper.source == 'journals' or paper.publication_kind == 'journal' else 'preprints'


def paper_group(paper):
    return paper.recommendation_group or publication_group(paper)


def select_papers(ranked, quotas, pending=(), rng=None):
    """Reserve quota slots for pending deliveries; sample remaining candidates once."""
    if quotas is None:
        return list(ranked)
    slots = {group: max(0, count - sum(paper_group(p) == group for p in pending))
             for group, count in quotas.items()}
    selected, remaining = [], []
    for paper in ranked:
        group = publication_group(paper)
        if slots[group]:
            paper.recommendation_group = group
            selected.append(paper)
            slots[group] -= 1
        else:
            remaining.append(paper)
    random_papers = (rng or random.SystemRandom()).sample(remaining, min(slots['random'], len(remaining)))
    for paper in random_papers:
        paper.recommendation_group = 'random'
    selected.extend(random_papers)
    for group, target in quotas.items():
        count = sum(paper_group(p) == group for p in [*pending, *selected])
        if count < target:
            logger.warning(f'{group}: {count}/{target} eligible recommendations; leaving shortage unfilled')
    return selected


def pending_batch(papers, quotas, maximum):
    if quotas is None:
        return papers[:maximum]
    return [p for group, limit in quotas.items() for p in [p for p in papers if paper_group(p) == group][:limit]]
