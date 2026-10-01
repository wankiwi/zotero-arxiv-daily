"""Platform-specific preprint scope, independent of journal selection."""
import re
from collections.abc import Mapping
from omegaconf import DictConfig, ListConfig

PLATFORMS = ('arxiv', 'biorxiv', 'medrxiv', 'researchsquare', 'openreview')


def _terms(value, path, categories=False, platform=None):
    if not isinstance(value, (list, ListConfig)) or not value:
        raise ValueError(f'{path} must be a non-empty list; omit it to inherit, or use enabled: false to disable')
    result = []
    for term in value:
        if not isinstance(term, str) or not term.strip():
            raise ValueError(f'{path} requires non-empty strings')
        term = term.strip()
        if categories:
            if platform != 'arxiv':
                term = ' '.join(term.lower().replace('_', ' ').split())
            pattern = r'[a-z][a-z-]*(?:\.[A-Za-z][A-Za-z-]*)?' if platform == 'arxiv' else r'[a-z][a-z /-]*'
            if term != '*' and not re.fullmatch(pattern, term):
                raise ValueError(f'Invalid {platform} category: {term}')
        else:
            if term == '*' or not re.search(r'\w', term) or any(ord(c) < 32 for c in term):
                raise ValueError(f'{path} requires literal keyword phrases, not wildcards or control characters')
        if term not in result:
            result.append(term)
    if categories and '*' in result and len(result) != 1:
        raise ValueError(f'{path}: wildcard must be used alone')
    return result


def validate_interests(config):
    supplied = config.get('preprint_interests')
    if supplied is None:
        return {}
    if not isinstance(supplied, (Mapping, DictConfig)) or not supplied:
        raise ValueError('preprint_interests must be a non-empty platform mapping or null to inherit legacy settings')
    result = {}
    for name, values in supplied.items():
        if name not in PLATFORMS:
            raise ValueError(f'Unknown preprint platform: {name}')
        if not isinstance(values, (Mapping, DictConfig)) or not values:
            raise ValueError(f'preprint_interests.{name} must be a non-empty mapping')
        allowed = {'enabled', 'categories', 'keywords'}
        if name == 'openreview':
            allowed = {'enabled', 'venues', 'subject_areas', 'keywords'}
        if name == 'researchsquare':
            allowed |= {'backend', 'source_ids', 'type', 'subfield'}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f'Unknown preprint interest options for {name}: {sorted(unknown)}')
        spec = {}
        if 'enabled' in values:
            if not isinstance(values['enabled'], bool):
                raise ValueError(f'preprint_interests.{name}.enabled must be true or false')
            spec['enabled'] = values['enabled']
        if 'categories' in values and values['categories'] is not None:
            if name == 'researchsquare':
                raise ValueError('Research Square has no supported category filter here; use keywords')
            spec['categories'] = _terms(values['categories'], f'{name}.categories', True, name)
        if name == 'openreview':
            for key in ('venues', 'subject_areas'):
                if key in values:
                    spec[key] = _terms(values[key], f'{name}.{key}')
        if 'keywords' in values:
            spec['keywords'] = _terms(values['keywords'], f'{name}.keywords')
        if name == 'researchsquare':
            for key in ('backend', 'source_ids', 'type', 'subfield'):
                if key in values:
                    spec[key] = values[key]
        result[name] = spec
    return result


def enabled_sources(config):
    specs = validate_interests(config)
    return [name for name in config.executor.source if specs.get(name, {}).get('enabled', True)]


def categories_for(config, name):
    spec = validate_interests(config).get(name, {})
    value = spec.get('categories', config.source[name].get('category'))
    if value is None:
        raise ValueError(f'category must be specified for {name}')
    return _terms(value, f'source.{name}.category', True, name)


def keyword_text(value):
    return ' '.join(re.findall(r'\w+', value.casefold()))


def matches_keywords(paper, spec):
    phrases = spec.get('keywords')
    if not phrases:
        return True
    # Phrase OR, across title or abstract; never bridge the two fields.
    texts = [f' {keyword_text(text)} ' for text in (paper.title, paper.abstract)]
    return any(f' {keyword_text(phrase)} ' in text for phrase in phrases for text in texts)
