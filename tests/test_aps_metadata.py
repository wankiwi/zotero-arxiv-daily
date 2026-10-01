import json
from types import SimpleNamespace

import pytest

from zotero_arxiv_daily import aps_metadata as module
from tests.canned_responses import make_sample_paper

DOI = '10.1103/synthetic'
TITLE = 'A verified physics study'
TEXT = 'This full abstract describes a synthetic physics study with two coupled schemes.'


@pytest.mark.parametrize('doi,title,abstract,accepted', [
    (DOI,TITLE,TEXT,True), ('10.1103/wrong',TITLE,TEXT,False),
    (DOI,'Another study',TEXT,False), (DOI,TITLE,None,False), (DOI,TITLE,TEXT+'…',False)])
def test_scholar_requires_doi_title_full_text_and_labels_version(monkeypatch, doi,title,abstract,accepted):
    monkeypatch.setattr(module, 'fetch', lambda *a,**kw:json.dumps({
        'title':title,'abstract':abstract,'externalIds':{'DOI':doi,'ArXiv':'0000.00000'},
        'publicationDate':'2025-01-01'}).encode())
    paper = make_sample_paper(doi=DOI,title=TITLE)
    before = paper.published
    result = module.semantic_scholar(paper,object(),set())
    assert bool(result) == accepted and paper.published == before
    if accepted: assert 'version unverified' in result[1] and result[3]=='recovered_indexed_abstract'


@pytest.mark.parametrize('doi,identity,title,accepted', [
    (DOI,'http://arxiv.org/abs/2605.12345v2',TITLE,True),
    ('','http://arxiv.org/abs/2605.12345v1',TITLE,False),
    ('10.1103/wrong','http://arxiv.org/abs/2605.12345v2',TITLE,False),
    (DOI,'http://evil.test/abs/2605.12345v2',TITLE,False),
    (DOI,'http://arxiv.org/abs/2605.12345',TITLE,False),
    (DOI,'http://arxiv.org/abs/2605.12345v2','Different version title',False)])
def test_arxiv_candidate_search_never_substitutes_by_title_only(monkeypatch,doi,identity,title,accepted):
    xml=f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
    <entry><id>{identity}</id><title>{title}</title><summary>{TEXT}</summary><arxiv:doi>{doi}</arxiv:doi></entry></feed>'''
    def fetch(client,url,provider,blocked,params):
        assert params['max_results']==3 and params['search_query'].startswith('ti:')
        return xml.encode()
    monkeypatch.setattr(module,'fetch',fetch)
    result=module.arxiv_manuscript(make_sample_paper(doi=DOI,title=TITLE),object(),set())
    assert bool(result)==accepted
    if accepted:
        assert result[1]=='arXiv v2 (DOI-linked manuscript)' and result[2].endswith('2605.12345v2')


@pytest.mark.parametrize('status',[401,403,429])
def test_metadata_provider_blocks_are_not_retried(monkeypatch,status):
    from tests.test_publisher_abstracts import Response
    monkeypatch.setattr(module,'_LAST',{})
    calls=[]
    client=SimpleNamespace(get=lambda *a,**kw:(calls.append(a) or Response(status)))
    blocked=set()
    for _ in range(2): assert module.fetch(client,'https://example.test','arXiv',blocked) is None
    assert len(calls)==1 and 'arXiv' in blocked


def test_arxiv_rate_limit_is_three_seconds_and_one_connection(monkeypatch):
    from tests.test_publisher_abstracts import Response
    monkeypatch.setattr(module,'_LAST',{'arXiv':10.0})
    monkeypatch.setattr(module.time,'monotonic',lambda:11.0)
    sleeps=[];monkeypatch.setattr(module.time,'sleep',sleeps.append)
    client=SimpleNamespace(get=lambda *a,**kw:Response(404))
    assert module.fetch(client,'https://example.test','arXiv',set()) is None
    assert sleeps==[2.0]


def test_nonpublisher_abstract_labels_remain_honest():
    from zotero_arxiv_daily.construct_email import render_email,email_plain_text
    paper=make_sample_paper(abstract=TEXT,tldr=None,tldr_status='not_generated',
        abstract_source='arXiv v2 (DOI-linked manuscript)',abstract_recovery_status='recovered_doi_linked_manuscript')
    assert 'Manuscript abstract (arXiv v2' in email_plain_text(render_email([paper]))
    paper.abstract_recovery_status='recovered_indexed_abstract'
    assert 'Indexed abstract (Semantic Scholar; version unverified)' in email_plain_text(render_email([paper]))
