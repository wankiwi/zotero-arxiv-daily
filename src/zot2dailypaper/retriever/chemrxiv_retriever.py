"""ChemRxiv-deposited Crossref metadata; no ChemRxiv page/API scraping."""
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit
from loguru import logger

from .base import BaseRetriever, register_retriever
from ..abstracts import clean_abstract
from ..http import abstract_session
from ..identity import canonical_doi, normalize_doi, crossref_equivalent_dois
from ..preprint_interests import categories_for
from ..protocol import Paper

ENDPOINT = 'https://api.crossref.org/works'
MAX_BYTES = 2_000_000
DOI_PATTERN = re.compile(r'10\.26434/chemrxiv(?:[.-][a-z0-9]+)+(?:/v[1-9]\d*)?')


def chemrxiv_date(item):
    parts = item.get('posted', {}).get('date-parts', [])
    try:
        if len(parts) != 1 or len(parts[0]) != 3:
            return None
        return datetime(*parts[0], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def verified_item(item):
    doi = normalize_doi(item.get('DOI'))
    resource = (item.get('resource') or {}).get('primary') or {}
    try:
        url = urlsplit(resource.get('URL') or '')
        source_ok = url.scheme in ('http','https') and url.hostname in ('chemrxiv.org','www.chemrxiv.org') and not url.username and not url.password and url.port in (None,80,443)
    except ValueError:
        source_ok = False
    return bool(doi and DOI_PATTERN.fullmatch(doi) and source_ok and
                item.get('prefix') == '10.26434' and str(item.get('member')) == '316' and
                item.get('publisher') == 'American Chemical Society (ACS)' and
                item.get('type') == 'posted-content' and item.get('subtype') == 'preprint')


@register_retriever('chemrxiv')
class ChemRxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        self.failures = []
        if categories_for(config, self.name) != ['*']:
            raise ValueError('ChemRxiv Crossref backend supports category ["*"] only; official taxonomy is unavailable. Use independent keywords.')
        for key, default, low, high in [('window_days',1,1,90),('page_size',50,1,100),('max_pages',20,1,100)]:
            value = self.retriever_config.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'ChemRxiv {key} must be an integer from {low} to {high}')
            setattr(self,key,value)
        self.until = datetime.now(timezone.utc)
        self.since = self.until - timedelta(days=self.window_days)

    def _retrieve_raw_papers(self):
        if not self.interests.get('enabled',True):
            return []
        cursor, seen_pages, results = '*', set(), []
        filters = f'prefix:10.26434,type:posted-content,from-posted-date:{self.since.date()},until-posted-date:{self.until.date()}'
        with abstract_session(self.retriever_config.get('mailto')) as client:
            for _ in range(self.max_pages):
                with client.get(ENDPOINT,params={'filter':filters,'rows':self.page_size,'cursor':cursor,
                                'sort':'indexed','order':'desc'},timeout=(5,20),allow_redirects=False,stream=True) as response:
                    if response.status_code in (401,403,429):
                        raise RuntimeError(f'ChemRxiv Crossref access blocked HTTP {response.status_code}; no retries')
                    if 300 <= response.status_code < 400:
                        raise RuntimeError('ChemRxiv metadata redirect rejected')
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_content(65536):
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise ValueError('ChemRxiv metadata page exceeds 2 MB; reduce page_size')
                message = json.loads(body)['message']
                items = message.get('items')
                if not isinstance(items,list) or any(not isinstance(i,dict) for i in items):
                    raise ValueError('ChemRxiv metadata returned malformed items')
                fingerprint = tuple(str(i.get('DOI')) for i in items)
                if items and fingerprint in seen_pages:
                    raise RuntimeError('ChemRxiv pagination repeated a page')
                seen_pages.add(fingerprint)
                results.extend(items)
                total = message.get('total-results')
                if total is not None and (type(total) is not int or total < 0):
                    raise ValueError('ChemRxiv invalid total-results')
                if total is not None and len(results) >= total:
                    break
                if not items:
                    if total is not None:
                        raise RuntimeError('ChemRxiv pagination ended before total-results')
                    break
                if total is None and len(items) < self.page_size:
                    break
                following = message.get('next-cursor')
                if not isinstance(following,str) or not following:
                    raise RuntimeError('ChemRxiv pagination cursor did not advance')
                # Crossref may reuse a cursor token; page identity detects stalls.
                cursor = following
            else:
                raise RuntimeError('ChemRxiv incomplete retrieval: max_pages reached; partial records discarded; narrow window_days or increase max_pages')
        valid = [i for i in results if verified_item(i) and (date := chemrxiv_date(i)) is not None
                 and self.since.date() <= date.date() <= self.until.date()]
        if len(valid) != len(results):
            logger.warning(f'ChemRxiv: excluded {len(results)-len(valid)} records failing source identity or precise posted-date/window validation')
        def version(item):
            match = re.search(r'[./-]v([1-9]\d*)$',normalize_doi(item['DOI']))
            return int(match[1]) if match else 0
        valid.sort(key=lambda i:(version(i),chemrxiv_date(i)),reverse=True)
        newest = {}
        for item in valid:newest.setdefault(canonical_doi(item['DOI']),item)
        logger.info(f'ChemRxiv: {len(results)} deposited records, {len(newest)} source/date-verified version families')
        return list(newest.values())

    def convert_to_paper(self, item):
        if not verified_item(item):
            return None
        doi=normalize_doi(item['DOI']);title=clean_abstract(' '.join(item.get('title') or []))
        if not title or re.match(r'^(?:withdrawn|retracted|withdrawal|retraction)\s*[:：]|^\[(?:withdrawn|retracted)\]',title,re.I):
            return None
        abstract=clean_abstract(item.get('abstract'))
        if abstract.rstrip().endswith(('…','...')):abstract=''
        authors=[' '.join(filter(None,(a.get('given'),a.get('family')))) or a.get('name','') for a in item.get('author',[])]
        affiliations=list(dict.fromkeys(clean_abstract(a.get('name')) for person in item.get('author',[]) for a in person.get('affiliation',[]) if a.get('name')))
        return Paper(source=self.name,title=title,authors=[a for a in authors if a],abstract=abstract,
                     url='https://doi.org/'+doi,doi=doi,published=chemrxiv_date(item),affiliations=affiliations or None,
                     publication_kind='preprint',publication_status='preprint',publication_venue='ChemRxiv',
                     abstract_source='Crossref (ChemRxiv deposit)' if abstract else None,
                     abstract_source_url='https://api.crossref.org/works/'+doi if abstract else None,
                     related_dois=crossref_equivalent_dois(item))
