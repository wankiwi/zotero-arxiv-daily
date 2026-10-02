"""Validated, opt-in semantic interests; no lexical query expansion."""
import math
import re
import unicodedata
from dataclasses import dataclass
from numbers import Real
from omegaconf import ListConfig


@dataclass(frozen=True)
class InterestProfile:
    keywords: tuple[str, ...] = ()
    keyword_weight: float = 0.6
    zotero_weight: float = 0.4

    def effective_weights(self, has_corpus):
        keyword = self.keyword_weight if self.keywords else 0.0
        zotero = self.zotero_weight if has_corpus else 0.0
        total = keyword + zotero
        if not total:
            raise ValueError("No available positively weighted interests: configure keywords or a nonempty Zotero corpus")
        return keyword / total, zotero / total


def interest_profile(config):
    raw = config.get('interest_profile', {}) if config is not None else {}
    if raw is None or not hasattr(raw, 'get'):
        raise ValueError('interest_profile must be a mapping')
    values = raw.get('keywords', [])
    if not isinstance(values, (list, tuple, ListConfig)) or len(values) > 100:
        raise ValueError('interest_profile.keywords must be a list of at most 100 phrases')
    keywords = []
    for value in values:
        if not isinstance(value, str) or len(value) > 250:
            raise ValueError('Each interest keyword must be a string of at most 250 characters')
        value = unicodedata.normalize('NFKC', value).casefold()
        value = re.sub(r'[-\u2010-\u2015\u2212]', ' ', value)
        value = ' '.join(value.split())
        if value and value not in keywords:
            keywords.append(value)
    weights = []
    for name, default in [('keyword_weight', 0.6), ('zotero_weight', 0.4)]:
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0:
            raise ValueError(f'interest_profile.{name} must be a finite nonnegative number')
        weights.append(float(value))
    if not math.isfinite(sum(weights)) or sum(weights) <= 0:
        raise ValueError('Interest weights must have a finite positive sum')
    return InterestProfile(tuple(keywords), *weights)
