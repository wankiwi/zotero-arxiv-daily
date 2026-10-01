"""Public DOI metadata recovery; never invent an abstract or scrape a paywall."""
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import quote
from loguru import logger
from .http import session
from .identity import paper_doi, canonical_doi

class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag in ('p', 'br', 'div', 'jats:p', 'jats:title'): self.parts.append(' ')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden - 1)
        self.parts.append(' ')
    def handle_data(self, text):
        if not self.hidden: self.parts.append(text)

def clean_abstract(value):
    if not isinstance(value, str): return ''
    parser = _Text()
    parser.feed(unescape(value))
    text = ' '.join(''.join(parser.parts).split())
    if text.casefold().strip(' .') in {'', 'no abstract', 'no abstract available', 'abstract unavailable', 'n/a'}:
        return ''
    return text

def inverted_abstract(index):
    if not isinstance(index, dict): return ''
    words = {}
    for word, positions in index.items():
        if isinstance(word, str) and isinstance(positions, list):
            for pos in positions:
                if type(pos) is int and 0 <= pos < 100000: words.setdefault(pos, word)
    return clean_abstract(' '.join(words[pos] for pos in sorted(words)))

def recover_abstracts(papers, config):
    if not config.get('enabled', False): return
    limit = config.get('max_papers', 50)
    if type(limit) is not int or limit < 0: raise ValueError('abstracts.max_papers must be a nonnegative integer')
    missing = [p for p in papers if not p.abstract and paper_doi(p)]
    if len(missing) > limit:
        logger.warning(f'Abstract recovery limited to {limit}/{len(missing)} missing DOI abstracts')
    with session(config.get('mailto')) as client:
        blocked = set()
        for paper in missing[:limit]:
            doi = paper_doi(paper)
            for provider, url in [('Crossref', 'https://api.crossref.org/works/' + quote(doi, safe='')),
                                  ('OpenAlex', 'https://api.openalex.org/works/https://doi.org/' + quote(doi, safe=''))]:
                if provider in blocked: continue
                try:
                    response = client.get(url, timeout=(10, 30))
                    if response.status_code == 404: continue
                    if response.status_code in (401, 403, 429):
                        blocked.add(provider)
                        logger.warning(f'{provider} abstract lookup blocked HTTP {response.status_code}; no paid fallback')
                        continue
                    response.raise_for_status()
                    data = response.json()
                    if provider == 'Crossref':
                        data = data.get('message', {})
                        abstract = clean_abstract(data.get('abstract')) if canonical_doi(data.get('DOI')) == doi else ''
                    else:
                        abstract = inverted_abstract(data.get('abstract_inverted_index')) if canonical_doi(data.get('doi')) == doi else ''
                    if abstract:
                        paper.abstract, paper.abstract_source = abstract, provider
                        break
                except Exception as exc:
                    logger.warning(f'{provider} abstract lookup failed ({type(exc).__name__}); retaining missing-abstract status')
