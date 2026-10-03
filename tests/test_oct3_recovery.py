from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from zotero_arxiv_daily import abstracts
from zotero_arxiv_daily.construct_email import email_summary
from zotero_arxiv_daily.publisher_abstracts import parse_abstract, publisher_url
from tests.canned_responses import make_sample_paper

TEXT = 'A complete verified original abstract about interfacial molecular transport and its underlying physical mechanism.'


def unavailable_metadata(monkeypatch):
    client = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(status_code=404))
    monkeypatch.setattr(abstracts, 'session', lambda *a: nullcontext(client))
    monkeypatch.setattr(abstracts, 'publisher_session', lambda: nullcontext(object()))


def test_blocked_aps_does_not_exhaust_nature_publisher_allowance(monkeypatch):
    unavailable_metadata(monkeypatch)
    calls = []
    def recover(paper, client, blocked):
        calls.append(paper.doi)
        if paper.doi.startswith('10.1103/'):
            blocked.add('journals.aps.org')
            return '', None, 'publisher_access_blocked'
        return TEXT, publisher_url(paper), 'recovered'
    monkeypatch.setattr(abstracts, 'recover_publisher', recover)
    papers = [make_sample_paper(doi=f'10.1103/test-{i}',journal='Physical Review Letters',abstract='') for i in range(9)]
    papers += [make_sample_paper(doi='10.1038/test-nature',abstract='')]
    abstracts.recover_abstracts(papers, {'enabled':True,'publisher_max_papers':2,'aps_metadata_max_papers':0})
    assert calls == ['10.1103/test-0','10.1038/test-nature']
    assert papers[-1].abstract == TEXT
    assert all(p.abstract_recovery_status=='publisher_access_blocked' for p in papers[:-1])


def test_shared_context_stops_retries_and_preserves_remaining_budget(monkeypatch):
    unavailable_metadata(monkeypatch)
    context = abstracts.RecoveryContext()
    calls=[]
    monkeypatch.setattr(abstracts,'recover_publisher',lambda p,*a: calls.append(p.doi) or ('',None,'publisher_abstract_absent'))
    first=make_sample_paper(doi='10.1038/first',abstract='')
    second=make_sample_paper(doi='10.1038/second',abstract='')
    config={'enabled':True,'max_papers':2,'publisher_max_papers':2}
    abstracts.recover_abstracts([first],config,context)
    abstracts.recover_abstracts([first,second],config,context)
    assert calls==['10.1038/first','10.1038/second']
    assert len(context.attempted)==2


def test_deadline_and_metadata_cap_are_visible_without_hiding_papers(monkeypatch):
    unavailable_metadata(monkeypatch)
    paper=make_sample_paper(doi='10.1038/example',abstract='')
    monkeypatch.setattr(abstracts,'monotonic',lambda:100)
    context=abstracts.RecoveryContext(deadline=99)
    abstracts.recover_abstracts([paper],{'enabled':True},context)
    assert paper.abstract_recovery_status=='pre_rank_time_limit' and not context.attempted
    abstracts.recover_abstracts([paper],{'enabled':True,'max_papers':0})
    assert paper.abstract_recovery_status=='metadata_lookup_limit'
    assert 'allowance exhausted' in email_summary(paper)[0]


def test_deadline_client_caps_timeout_and_never_starts_late(monkeypatch):
    calls=[]
    client=SimpleNamespace(get=lambda url,**kw:calls.append(kw))
    monkeypatch.setattr(abstracts,'monotonic',lambda:100)
    wrapped=abstracts.DeadlineClient(client,abstracts.RecoveryContext(deadline=102))
    wrapped.get('https://example.org',timeout=(5,20))
    assert calls==[{'timeout':(2,2)}]
    wrapped.context.deadline=99
    with pytest.raises(TimeoutError):wrapped.get('https://example.org')
    assert len(calls)==1


def test_source_attempt_reasons_survive_state_and_email(monkeypatch):
    from zotero_arxiv_daily.state import load_paper,paper_dict
    unavailable_metadata(monkeypatch)
    paper=make_sample_paper(doi='10.5555/example',abstract='')
    abstracts.recover_abstracts([paper],{'enabled':True})
    restored=load_paper(paper_dict(paper))
    text,label=email_summary(restored)
    assert 'Crossref: metadata not found' in text and 'OpenAlex: metadata not found' in text
    assert label=='Abstract unavailable'
    legacy=paper_dict(paper);legacy.pop('abstract_recovery_attempts')
    assert load_paper(legacy).abstract_recovery_attempts==[]


def test_shortlist_preserves_category_order_without_changing_quotas():
    papers=[make_sample_paper(source='journals',doi=f'10.5555/j{i}',abstract='') for i in range(5)]
    papers += [make_sample_paper(source='arxiv',doi=f'10.5555/p{i}',abstract='') for i in range(5)]
    assert [p.doi for p in abstracts.recovery_shortlist(papers,4)]==['10.5555/j0','10.5555/p0','10.5555/j1','10.5555/p1']


def test_public_acs_abstract_requires_exact_doi_and_complete_section():
    doi='10.1021/synthetic.123'
    assert publisher_url(make_sample_paper(doi=doi))=='https://pubs.acs.org/doi/'+doi
    page='<meta name="citation_doi" content="'+doi+'"><div class="hlFld-Abstract">'+TEXT+'</div>'
    assert parse_abstract(page,doi)==(TEXT,'recovered')
    assert not parse_abstract(page,'10.1021/other')[0]
    assert not parse_abstract(page.replace(TEXT,TEXT+'...'),doi)[0]


def test_metadata_recovery_transport_never_retries_429():
    from zotero_arxiv_daily.http import abstract_session
    with abstract_session() as client:
        for url in ('https://api.crossref.org/works/example','https://api.openalex.org/works/example'):
            assert client.get_adapter(url).max_retries.total==0


@pytest.mark.parametrize('doi,title,text,reason', [
    ('10.5555/wrong','Target',TEXT,'doi_version_mismatch'),
    ('10.5555/right','Another study',TEXT,'title_mismatch'),
    ('10.5555/right','Target',TEXT+'...','abstract_incomplete'),
    ('10.5555/right','Target','','abstract_absent'),
])
def test_public_metadata_rejection_reasons_are_specific(monkeypatch,doi,title,text,reason):
    def get(url,**kwargs):
        if 'openalex' in url:return SimpleNamespace(status_code=404)
        return SimpleNamespace(status_code=200,raise_for_status=lambda:None,
                               json=lambda:{'message':{'DOI':doi,'title':[title],'abstract':text}})
    monkeypatch.setattr(abstracts,'session',lambda *a:nullcontext(SimpleNamespace(get=get)))
    monkeypatch.setattr(abstracts,'publisher_session',lambda:nullcontext(object()))
    paper=make_sample_paper(doi='10.5555/right',title='Target',abstract='')
    abstracts.recover_abstracts([paper],{'enabled':True})
    assert not paper.abstract
    assert {'provider':'Crossref','status':reason} in paper.abstract_recovery_attempts


def test_correction_metadata_absence_is_not_presented_as_request_failure(monkeypatch):
    paper=make_sample_paper(doi='10.5555/correction',title='Correction to a study',abstract='')
    def get(url,**kwargs):
        if 'crossref' in url:return SimpleNamespace(status_code=404)
        return SimpleNamespace(status_code=200,raise_for_status=lambda:None,json=lambda:{
            'doi':paper.doi,'title':paper.title,'type':'erratum','abstract_inverted_index':None})
    monkeypatch.setattr(abstracts,'session',lambda *a:nullcontext(SimpleNamespace(get=get)))
    monkeypatch.setattr(abstracts,'publisher_session',lambda:nullcontext(object()))
    abstracts.recover_abstracts([paper],{'enabled':True})
    text,_=email_summary(paper)
    assert 'correction/erratum; checked metadata provides no standalone abstract' in text
    assert not paper.abstract and 'lookup failed' not in text


def test_verified_page_without_abstract_is_distinct_from_access_failure():
    paper=make_sample_paper(abstract='',abstract_recovery_status='publisher_abstract_absent')
    text,_=email_summary(paper)
    assert 'provides no standalone abstract (not a request failure)' in text
    paper.abstract_recovery_status='publisher_access_blocked'
    assert 'access blocked' in email_summary(paper)[0]
    assert 'provides no standalone abstract' not in email_summary(paper)[0]
