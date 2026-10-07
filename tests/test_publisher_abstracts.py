from types import SimpleNamespace

import pytest

from tests.canned_responses import make_sample_paper
from zot2dailypaper import publisher_abstracts as module


DOI = '10.1038/synthetic-test'
ABSTRACT = 'A verified scientific abstract with T<Tc and x>0, preserved without promotional text.'


def page(doi=DOI, section=''):
    return f'<meta name="citation_doi" content="{doi}">{section}'


def test_precise_nature_section_ignores_teaser_and_full_text():
    html = page(section=f'<meta name="description" content="DO_NOT_USE">'
                f'<div id="Abs1-content"><p>{ABSTRACT.replace("<", "&lt;").replace(">", "&gt;")}</p></div>'
                '<p>Full article and references must not be extracted.</p>')
    assert module.parse_abstract(html, DOI) == (ABSTRACT, 'recovered')


def test_aps_section_preserves_math_alttext_and_skips_script():
    html = page(section='<div class="abstract"><h2>Abstract</h2><p>We describe the time-dependent '
                '<math alttext="|Psi(R,t)|^2"><mi>IGNORE_DUPLICATE</mi></math>'
                ' with two coupled schemes.<script>BAD</script></p></div>')
    text, status = module.parse_abstract(html, DOI)
    assert status == 'recovered' and '|Psi(R,t)|^2' in text and 'two coupled' in text
    assert 'IGNORE_DUPLICATE' not in text and 'BAD' not in text and 'Abstract' not in text


@pytest.mark.parametrize('html', [page('10.1038/wrong'), '<a href="https://doi.org/' + DOI + '">Reference</a>',
                                 page() + '<meta name="DOI" content="10.1038/conflicting">'])
def test_identity_mismatch_and_reference_only_doi_are_rejected(html):
    html += '<div id="Abs1-content">' + ABSTRACT + '</div>'
    assert module.parse_abstract(html, DOI)[1] == 'publisher_doi_unverified'


def test_generic_metadata_is_not_fabricated_into_an_abstract():
    assert module.parse_abstract(page(section='<meta name="description" content="' + ABSTRACT + '">'), DOI) == ('', 'publisher_abstract_absent')
    assert module.parse_abstract(page(section='<meta name="citation_abstract" content="' + ABSTRACT.replace('<', '&lt;') + '">'), DOI)[1] == 'recovered'


class Response:
    def __init__(self, status=200, text='', headers=None):
        self.status_code, self.text = status, text
        self.headers = headers if headers is not None else {'Content-Type': 'text/html'}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError('HTTP failure')
    def iter_content(self, size): yield self.text.encode()


def client_for(responses):
    calls = []
    def get(url, **kwargs):
        assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
        calls.append(url)
        return responses.pop(0)
    return SimpleNamespace(get=get), calls


def test_redirect_to_public_abstract_and_provenance():
    paper = make_sample_paper(doi='10.1103/test', journal='Physical Review Letters')
    client, calls = client_for([Response(302, headers={'Location': '/prl/abstract/10.1103/test'}),
        Response(text=page('10.1103/test', '<div class="abstract">' + ABSTRACT.replace('<', '&lt;') + '</div>'))])
    text, url, status = module.recover_publisher(paper, client, set())
    assert text == ABSTRACT and status == 'recovered' and url.endswith('/abstract/10.1103/test')
    assert len(calls) == 2


@pytest.mark.parametrize('target', ['https://127.0.0.1/private', 'http://www.nature.com/test',
    'https://www.nature.com.evil.test/', 'https://user:pass@www.nature.com/', 'https://www.nature.com:444/',
    'https://idp.nature.com/login'])
def test_redirect_destination_cannot_escape_public_allowlist(target):
    client, calls = client_for([Response(302, headers={'Location': target})])
    result = module.recover_publisher(make_sample_paper(doi=DOI), client, set())
    assert result[2] == 'publisher_redirect_rejected' and len(calls) == 1


@pytest.mark.parametrize('status', [401, 403, 429])
def test_access_control_stops_host_without_retry_or_second_path(status):
    client, calls = client_for([Response(status)])
    blocked = set()
    paper = make_sample_paper(doi='10.1103/test', journal='Physical Review Letters')
    for _ in range(2):
        assert module.recover_publisher(paper, client, blocked)[2] == 'publisher_access_blocked'
    assert len(calls) == 1 and blocked == {'journals.aps.org'}


@pytest.mark.parametrize('body,expected', [('<title>Just a moment</title>', 'publisher_access_blocked'),
                                          ('x' * (module.MAX_BYTES + 1), 'publisher_page_too_large')])
def test_challenge_and_oversized_page_are_not_extracted(body, expected):
    client, calls = client_for([Response(text=body)])
    assert module.recover_publisher(make_sample_paper(doi=DOI), client, set())[2] == expected
    assert len(calls) == 1


def test_redirects_and_unsupported_dois_are_bounded():
    client, calls = client_for([Response(302, headers={'Location': '/articles/synthetic-test'}) for _ in range(4)])
    assert module.recover_publisher(make_sample_paper(doi=DOI), client, set())[2] == 'publisher_redirect_limit'
    assert len(calls) == 4
    assert module.publisher_url(make_sample_paper(doi='10.1038/../../private')) is None
    assert module.publisher_url(make_sample_paper(doi='10.5555/other')) is None


def test_nature_ordinary_anonymous_redirects_are_bounded_without_login():
    client, calls = client_for([
        Response(303, headers={'Location':'https://idp.nature.com/authorize?public=1'}),
        Response(302, headers={'Location':'https://idp.nature.com/transit?public=1'}),
        Response(302, headers={'Location':'https://www.nature.com/articles/synthetic-test'}),
        Response(text=page(section='<div id="Abs1-content">'+ABSTRACT.replace('<','&lt;')+'</div>'))])
    assert module.recover_publisher(make_sample_paper(doi=DOI), client, set())[0] == ABSTRACT
    assert len(calls) == 4


def test_publisher_transport_has_no_automatic_retries():
    with module.publisher_session() as client:
        assert client.get_adapter('https://journals.aps.org').max_retries.total == 0


@pytest.mark.parametrize('guarded', [True, False])
def test_real_requests_redirect_preparation_never_reads_oversized_body(guarded):
    import io
    import requests
    from requests.adapters import BaseAdapter
    from urllib3.response import HTTPResponse

    class CountingBody(io.BytesIO):
        consumed = 0
        def read(self, size=-1):
            result = super().read(size)
            self.consumed += len(result)
            return result
    oversized = CountingBody(b'x' * (module.MAX_BYTES + 1_000_000))
    final = CountingBody(page(section='<div id="Abs1-content">' + ABSTRACT.replace('<','&lt;') + '</div>').encode())
    class Adapter(BaseAdapter):
        calls = 0
        def send(self, request, **kwargs):
            self.calls += 1
            response = requests.Response()
            response.request, response.url = request, request.url
            response.status_code = 302 if self.calls == 1 else 200
            response.headers = requests.structures.CaseInsensitiveDict(
                {'Location':'https://www.nature.com/articles/synthetic-test'} if self.calls == 1 else {'Content-Type':'text/html'})
            assert response.is_redirect == (self.calls == 1)
            response.raw = HTTPResponse(body=oversized if self.calls == 1 else final, preload_content=False)
            return response
        def close(self): pass
    adapter = Adapter()
    with (module.publisher_session() if guarded else requests.Session()) as client:
        client.mount('https://', adapter)
        result = module.recover_publisher(make_sample_paper(doi=DOI), client, set())
    assert result[0] == ABSTRACT and adapter.calls == 2
    # The unguarded control reproduces the original eager read, so this fixture
    # cannot pass merely because Requests failed to recognize the redirect.
    assert oversized.consumed == (0 if guarded else module.MAX_BYTES + 1_000_000)
    assert oversized.closed
    assert final.consumed < module.MAX_BYTES


def test_recovery_pipeline_bounds_publisher_calls_and_records_provenance(monkeypatch):
    from contextlib import nullcontext
    from zot2dailypaper import abstracts
    metadata = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(status_code=404))
    monkeypatch.setattr(abstracts, 'session', lambda *a:nullcontext(metadata))
    monkeypatch.setattr(abstracts, 'publisher_session', lambda:nullcontext(object()))
    calls = []
    def recover(paper, client, blocked):
        calls.append(paper.doi)
        return ABSTRACT, 'https://www.nature.com/articles/synthetic-test', 'recovered'
    monkeypatch.setattr(abstracts, 'recover_publisher', recover)
    papers = [make_sample_paper(doi=f'10.1038/synthetic-{i}', abstract='') for i in range(3)]
    abstracts.recover_abstracts(papers, {'enabled':True, 'max_papers':3, 'publisher_max_papers':1})
    assert len(calls) == 1 and papers[0].abstract == ABSTRACT
    assert papers[0].abstract_source == 'Publisher' and papers[0].abstract_source_url.endswith('synthetic-test')
    assert papers[1].abstract_recovery_status == 'publisher_lookup_limit'
    assert papers[2].abstract == ''
