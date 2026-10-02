"""Public DOI metadata recovery; never invent an abstract or scrape a paywall."""
from html import unescape
import re
from urllib.parse import quote
from loguru import logger
from .http import session
from .identity import paper_doi, canonical_doi, normalize_doi, title_key
from .publisher_abstracts import publisher_session, publisher_url, recover_publisher
from .aps_metadata import semantic_scholar, arxiv_manuscript

# Strip only known formatting tags. Unknown '<Tc' and '<y ...>' sequences
# are scientific plaintext, not HTML. Decode entities once, never reparse them
# as arbitrary tags; mathematical inequalities must survive cleaning.
_MARKUP = re.compile(r"</?(?:jats:)?(?:p|title|abstract|sec|div|span|br|i|b|em|strong|italic|bold|sup|sub|a|ul|ol|li|h[1-6]|table|tr|td|th|tbody|thead|xref|ext-link)(?:\s+[\w:-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s<>]+))*\s*/?>", re.I)


def clean_abstract(value):
    if not isinstance(value, str):
        return ''
    text = unescape(value)
    text = re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>', '', text, flags=re.I | re.S)
    text = ' '.join(_MARKUP.sub(' ', text).split())
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

def recover_researchsquare(paper, client, blocked):
    """Use the cited version for metadata; recommendation deduplication stays versionless."""
    import json
    from .aps_metadata import fetch
    doi = normalize_doi(paper.doi) or normalize_doi(paper.url)
    if not doi or not re.fullmatch(r'10\.21203/rs\.\d+\.rs-\d+/v[1-9]\d*', doi):
        paper.abstract_recovery_status = 'researchsquare_version_unverified'
        return
    paper.abstract_recovery_status = 'researchsquare_abstract_unavailable'
    for provider, base in [('Crossref', 'https://api.crossref.org/works/'),
                           ('OpenAlex', 'https://api.openalex.org/works/https://doi.org/')]:
        url = base + quote(doi, safe='')
        try:
            raw = fetch(client, url, provider, blocked)
            if raw is None:
                continue
            data = json.loads(raw)
            if provider == 'Crossref':
                data = data.get('message', {})
                found_doi, titles = data.get('DOI'), data.get('title') or []
                found_title = titles[0] if isinstance(titles, list) and titles else ''
                abstract = clean_abstract(data.get('abstract'))
            else:
                found_doi, found_title = data.get('doi'), data.get('title') or data.get('display_name') or ''
                abstract = inverted_abstract(data.get('abstract_inverted_index'))
            if normalize_doi(found_doi) != doi or title_key(found_title) != title_key(paper.title):
                logger.warning(f'{provider} Research Square DOI/version/title mismatch; metadata rejected')
                continue
            if len(abstract) >= 40 and not abstract.rstrip().endswith(('…', '...')):
                paper.abstract, paper.abstract_source = abstract, provider
                paper.abstract_source_url, paper.abstract_recovery_status = url, 'recovered'
                return
        except Exception as exc:
            logger.warning(f'{provider} Research Square abstract lookup failed ({type(exc).__name__})')
    logger.warning('Research Square exact-version abstract unavailable; retaining missing-abstract status')


def recover_abstracts(papers, config):
    if not config.get('enabled', False): return
    limit = config.get('max_papers', 50)
    if type(limit) is not int or limit < 0: raise ValueError('abstracts.max_papers must be a nonnegative integer')
    publisher_limit = config.get('publisher_max_papers', 10)
    if type(publisher_limit) is not int or not 0 <= publisher_limit <= 50:
        raise ValueError('abstracts.publisher_max_papers must be an integer from 0 to 50')
    aps_limit = config.get('aps_metadata_max_papers', 10)
    if type(aps_limit) is not int or not 0 <= aps_limit <= 50:
        raise ValueError('abstracts.aps_metadata_max_papers must be an integer from 0 to 50')
    missing = [p for p in papers if not p.abstract and paper_doi(p)]
    if len(missing) > limit:
        logger.warning(f'Abstract recovery limited to {limit}/{len(missing)} missing DOI abstracts')
    with session(config.get('mailto')) as client, publisher_session() as publisher:
        blocked = set()
        publisher_blocked, publisher_attempts = set(), 0
        metadata_blocked, aps_attempts = set(), 0
        for paper in missing[:limit]:
            doi = paper_doi(paper)
            if doi.startswith('10.21203/rs.'):
                recover_researchsquare(paper, publisher, blocked)
                continue
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
                        paper.abstract_source_url, paper.abstract_recovery_status = url, 'recovered'
                        break
                except Exception as exc:
                    logger.warning(f'{provider} abstract lookup failed ({type(exc).__name__}); retaining missing-abstract status')
            if not paper.abstract and config.get('publisher_fallback', True) and publisher_url(paper):
                if publisher_attempts >= publisher_limit:
                    paper.abstract_recovery_status = 'publisher_lookup_limit'
                else:
                    publisher_attempts += 1
                    try:
                        abstract, url, status = recover_publisher(paper, publisher, publisher_blocked)
                        paper.abstract_recovery_status = status
                        if abstract:
                            paper.abstract, paper.abstract_source, paper.abstract_source_url = abstract, 'Publisher', url
                        else:
                            logger.warning(f'Publisher abstract lookup: {status}; retaining missing-abstract status')
                    except Exception as exc:
                        paper.abstract_recovery_status = 'publisher_request_failed'
                        logger.warning(f'Publisher abstract lookup failed ({type(exc).__name__}); retaining missing-abstract status')
            if not paper.abstract and doi.startswith('10.1103/') and aps_attempts < aps_limit:
                aps_attempts += 1
                for provider, recover in [('Semantic Scholar', semantic_scholar), ('arXiv', arxiv_manuscript)]:
                    try:
                        result = recover(paper, publisher, metadata_blocked)
                        if result:
                            paper.abstract, paper.abstract_source, paper.abstract_source_url, paper.abstract_recovery_status = result
                            break
                        if provider in metadata_blocked:
                            logger.warning(f'{provider} metadata access blocked; no retries or identity substitution')
                    except Exception as exc:
                        logger.warning(f'{provider} metadata lookup failed ({type(exc).__name__}); no unverified substitute')
