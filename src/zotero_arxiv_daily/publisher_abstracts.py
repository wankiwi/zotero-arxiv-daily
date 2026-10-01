"""Bounded public publisher abstracts; no login, challenge solving or full-text extraction."""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

import requests

from .identity import canonical_doi, paper_doi

MAX_BYTES = 2_000_000
HOSTS = {'www.nature.com', 'journals.aps.org'}


class AbstractPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.dois, self.sections, self.metadata = [], set(), [], []
        self.active = None
        self.skip = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key = attrs.get('name', attrs.get('property', '')).lower()
            if key in ('citation_doi', 'doi', 'prism.doi', 'dc.identifier'):
                doi = canonical_doi(attrs.get('content'))
                if doi:
                    self.dois.add(doi)
            if key == 'citation_abstract':
                self.metadata.append(attrs.get('content', ''))
        if tag in ('meta', 'link', 'img', 'br', 'hr', 'input', 'source', 'wbr'):
            if tag == 'br' and self.active is not None:
                self.active[1].append(' ')
            return
        self.stack.append(tag)
        depth = len(self.stack)
        if self.active is None and (attrs.get('id') == 'Abs1-content' or
                'abstract' in attrs.get('class', '').split() or attrs.get('id') == 'abstract'):
            self.active = (depth, [])
        if self.active is not None and self.skip is None:
            if tag in ('script', 'style', 'h2', 'h3'):
                self.skip = depth
            elif tag == 'math' and attrs.get('alttext'):
                self.active[1].append(attrs['alttext'])
                self.skip = depth
            elif tag in ('p', 'div'):
                self.active[1].append(' ')

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.active is not None and depth <= self.active[0]:
            self.sections.append(''.join(self.active[1]))
            self.active = None
        if self.skip is not None and depth <= self.skip:
            self.skip = None
        del self.stack[depth - 1:]

    def handle_data(self, data):
        if self.active is not None and self.skip is None:
            self.active[1].append(data)


def parse_abstract(page, doi):
    parser = AbstractPage()
    parser.feed(page)
    if parser.dois != {doi}:
        return '', 'publisher_doi_unverified'
    # Generic description/OG text may append a teaser, so never treat it as an abstract.
    from .abstracts import clean_abstract
    for value in parser.sections + parser.metadata:
        text = clean_abstract(value)
        if len(text) >= 40:
            return text, 'recovered'
    return '', 'publisher_abstract_absent'


def publisher_url(paper):
    doi = paper_doi(paper)
    if doi and re.fullmatch(r'10\.1038/[a-z0-9.-]+', doi):
        return 'https://www.nature.com/articles/' + doi.split('/', 1)[1]
    if doi and re.fullmatch(r'10\.1103/[a-z0-9.-]+', doi) and paper.journal == 'Physical Review Letters':
        return 'https://journals.aps.org/prl/accepted/' + doi
    return None


def publisher_session():
    client = requests.Session()  # Zero automatic retries, especially on 403/429.
    client.headers['User-Agent'] = 'zotero-arxiv-daily/1.0 (public abstract metadata)'
    return client


def recover_publisher(paper, client, blocked):
    url = publisher_url(paper)
    if not url:
        return '', None, 'publisher_not_supported'
    host = urlsplit(url).hostname
    if host in blocked:
        return '', None, 'publisher_access_blocked'
    for _ in range(4):  # Nature's public anonymous cookie flow uses three ordinary redirects.
        parts = urlsplit(url)
        anonymous_nature_redirect = parts.hostname == 'idp.nature.com' and parts.path in ('/authorize', '/transit')
        if parts.scheme != 'https' or not (parts.hostname in HOSTS or anonymous_nature_redirect) or parts.username or parts.password or parts.port not in (None, 443):
            return '', None, 'publisher_redirect_rejected'
        if parts.hostname in blocked:
            return '', None, 'publisher_access_blocked'
        with client.get(url, timeout=(5, 20), allow_redirects=False, stream=True) as response:
            if response.status_code in (401, 403, 429):
                blocked.add(parts.hostname)
                return '', None, 'publisher_access_blocked'
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get('Location', ''))
                continue
            if response.status_code == 404 and '/prl/accepted/' in url:
                url = url.replace('/prl/accepted/', '/prl/abstract/')
                continue
            response.raise_for_status()
            if parts.hostname not in HOSTS:
                return '', None, 'publisher_redirect_rejected'  # Never process an identity/login page.
            if 'html' not in response.headers.get('Content-Type', '').lower():
                return '', None, 'publisher_not_html'
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    return '', None, 'publisher_page_too_large'
                chunks.append(chunk)
            page = b''.join(chunks).decode('utf-8', errors='replace')
        if re.search(r'<title[^>]*>\s*(?:Just a moment|Access denied|Robot check)|cf-chl-|captcha', page, re.I):
            blocked.add(parts.hostname)
            return '', None, 'publisher_access_blocked'
        text, status = parse_abstract(page, paper_doi(paper))
        return text, url if text else None, status
    return '', None, 'publisher_redirect_limit'
