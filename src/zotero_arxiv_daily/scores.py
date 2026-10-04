"""Versioned relevance units; scores are not probabilities or accuracy estimates."""
import math
from numbers import Real

SCORE_SCHEMA = 'relevance_0_100_v1'
LEGACY_SCHEMA = 'relevance_signed_10_v1'
SCORE_FIELDS = ('score', 'raw_score', 'selection_score', 'keyword_score', 'zotero_score')


def to_display(value):
    return 5 * max(-10.0, min(10.0, float(value))) + 50


def upgrade_scores(data):
    data = dict(data)
    schema = data.get('score_schema', LEGACY_SCHEMA)
    if schema not in (LEGACY_SCHEMA, SCORE_SCHEMA):
        raise ValueError('Unsupported relevance score schema; preserve history and investigate')
    for name in SCORE_FIELDS:
        value = data.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            raise ValueError('Invalid stored relevance score')
        if schema == LEGACY_SCHEMA:
            data[name] = to_display(value)
        elif not 0 <= value <= 100:
            raise ValueError('Stored relevance score outside 0-100')
    data['score_schema'] = SCORE_SCHEMA
    return data


def minimum_score(executor):
    scale = executor.get('min_score_scale', 'legacy')
    value = executor.get('min_score', -10 if scale == 'legacy' else 0)
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError('executor.min_score must be a finite number')
    if scale == 'legacy':
        if not -10 <= value <= 10:
            raise ValueError('Legacy min_score must be in [-10,10]; use min_score_scale: 0_100 for new units')
        return to_display(value)
    if scale != '0_100' or not 0 <= value <= 100:
        raise ValueError('min_score_scale must be legacy or 0_100, with a threshold in its declared range')
    return float(value)
