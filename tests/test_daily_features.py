from datetime import datetime, timedelta, timezone
import random
from types import SimpleNamespace
import pytest
from omegaconf import OmegaConf
from zotero_arxiv_daily.protocol import Paper
from tests.canned_responses import make_budget_guard, make_chat_response
from zotero_arxiv_daily.selection import select_papers, pending_batch, quotas_for, paper_group
from zotero_arxiv_daily.abstracts import clean_abstract, recover_abstracts
from zotero_arxiv_daily.construct_email import render_email, email_plain_text
from zotero_arxiv_daily.retriever.openreview_retriever import OpenReviewRetriever

def paper(i, source='journals', **kwargs):
    return Paper(source=source,title=f'Paper {i}',abstract='A molecular materials study.',authors=[],url=f'https://example.org/{i}',score=10-i/100,**kwargs)

def test_quotas_and_unbiased_remaining_sample():
    pool=[paper(i) for i in range(40)]+[paper(i,'openreview') for i in range(40,70)]
    quotas={'journals':25,'preprints':15,'random':5}
    selected=select_papers(pool,quotas,rng=random.Random(42))
    assert [sum(paper_group(p)==g for p in selected) for g in quotas]==[25,15,5]
    assert len({p.url for p in selected})==45
    assert selected[:25]==pool[:25]
    assert selected[25:40]==pool[40:55]
    assert all(p in pool[25:40]+pool[55:] for p in selected[40:])

def test_shortage_pending_reservation_and_persistence(tmp_path):
    from zotero_arxiv_daily.state import State
    quotas={'journals':25,'preprints':15,'random':5}
    old=[paper(200,recommendation_group='random')]
    selected=select_papers([paper(1),paper(2,'openreview')],quotas,old)
    assert len(selected)==2
    state=State(tmp_path/'history.json');state.add(old+selected);state.mark([selected[0]],'email');state.save()
    restored=State(tmp_path/'history.json')
    assert restored.has(selected[0])
    assert {p.url for p in restored.pending('email')}=={old[0].url,selected[1].url}
    assert paper_group(restored.pending('email')[0])=='random'
    assert len(pending_batch([paper(i) for i in range(30)],quotas,45))==25

@pytest.mark.parametrize('value',[{'journals':-1,'preprints':15,'random':5},{'journals':True,'preprints':15,'random':5},{'journals':25}, {'journals':0,'preprints':0,'random':0}])
def test_invalid_quotas(value):
    with pytest.raises(ValueError):quotas_for(OmegaConf.create({'quotas':value,'max_paper_num':20}))

def test_clean_abstract():
    assert clean_abstract('<jats:p>A &amp; B</jats:p><script>bad()</script><p>C</p>')=='A & B C'
    assert clean_abstract('&lt;p&gt;Readable&lt;/p&gt;')=='Readable'
    assert clean_abstract('No abstract available')==''
    assert clean_abstract(None)==''

def test_recovery_validates_doi_and_falls_back(monkeypatch):
    import zotero_arxiv_daily.abstracts as module
    calls=[]
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):
            calls.append(url)
            data={'message':{'DOI':'10.1000/wrong','abstract':'Wrong paper'}} if 'crossref' in url else {'doi':'https://doi.org/10.1000/right','abstract_inverted_index':{'Molecular':[0],'study':[1]}}
            return SimpleNamespace(status_code=200,raise_for_status=lambda:None,json=lambda:data)
    monkeypatch.setattr(module,'session',lambda *a:Client())
    p=paper(1,doi='10.1000/right');p.abstract=''
    recover_abstracts([p],{'enabled':True,'max_papers':1})
    assert p.abstract=='Molecular study' and p.abstract_source=='OpenAlex' and len(calls)==2

def test_recovery_access_denied_visible_and_bounded(monkeypatch, capsys):
    import zotero_arxiv_daily.abstracts as module
    calls=[]
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):calls.append(url);return SimpleNamespace(status_code=403)
    monkeypatch.setattr(module,'session',lambda *a:Client())
    papers=[paper(i,doi=f'10.1000/{i}') for i in range(5)]
    for p in papers:p.abstract=''
    recover_abstracts(papers,{'enabled':True,'max_papers':3})
    assert len(calls)==2 and all(not p.abstract for p in papers)

def retriever(config):
    r=OpenReviewRetriever(config)
    r.until=datetime.now(timezone.utc);r.since=r.until-timedelta(days=7)
    return r

def note(r,**content):
    fields={'title':'A molecular study','abstract':'Experiments','authors':['A'],'primary_area':['ai_4_physical_sciences']}
    fields.update(content)
    return ('NeurIPS','NeurIPS.cc/2026/Conference',{'id':'test', 'odate':int((r.until-timedelta(days=1)).timestamp()*1000),
        'cdate':int((r.until-timedelta(days=200)).timestamp()*1000),'content':{k:{'value':v} for k,v in fields.items()}})

def test_openreview_array_schema_old_creation_new_public(config):
    r=retriever(config);p=r.convert_to_paper(note(r))
    assert p and p.source=='openreview' and p.subject_match_reason.startswith('subject field')

def test_openreview_keyword_gate_excludes_subject_labels(config):
    r=retriever(config)
    assert r.convert_to_paper(note(r,title='General optimization',abstract='A study',primary_area='applications to physical sciences (physics, chemistry, biology, etc.)')) is None
    assert r.convert_to_paper(note(r,title='General optimization',abstract='New molecular dynamics methods',primary_area=None)) is not None
    assert r.convert_to_paper(note(r,title='General optimization',abstract='A study',primary_area=None,keywords=['atomistic'])) is not None

def test_openreview_explicit_subject_fallback_and_reject_unknown(config):
    r=retriever(config)
    p=r.convert_to_paper(note(r,primary_area=None))
    assert 'fallback' in p.subject_match_reason
    config.source.openreview.subject_areas=['Unsupported']
    with pytest.raises(ValueError,match='subject'):OpenReviewRetriever(config)

def test_openreview_timestamp_window_and_private_notes(config):
    r=retriever(config);raw=note(r);raw[2]['odate']=int((r.until-timedelta(days=20)).timestamp()*1000)
    assert r.convert_to_paper(raw) is None
    raw=note(r);raw[2]['readers']=['private'];assert r.convert_to_paper(raw) is None

def test_openreview_access_error_is_not_empty_success(config):
    r=retriever(config)
    client=SimpleNamespace(get=lambda *a,**kw:SimpleNamespace(status_code=403))
    with pytest.raises(RuntimeError,match='access blocked HTTP 403'):r._get(client,'/notes',{})

def test_openreview_pagination_deduplicates_and_reports_partial(config,monkeypatch):
    import zotero_arxiv_daily.retriever.openreview_retriever as module
    config.source.openreview.venues=['TMLR'];r=retriever(config)
    records=[note(r)[2]]
    pages=iter([{'notes':records,'count':3},{'notes':records+[dict(records[0],id='second')],'count':3}])
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(module,'session',lambda:Client())
    monkeypatch.setattr(r,'_groups',lambda *a:[('TMLR','TMLR/-/Submission')])
    monkeypatch.setattr(r,'_get',lambda *a:next(pages))
    assert len(r._retrieve_raw_papers())==2 and not r.failures
    monkeypatch.setattr(r,'_get',lambda *a:(_ for _ in ()).throw(RuntimeError('blocked')))
    assert r._retrieve_raw_papers()==[] and r.failures

def test_three_numbered_groups_plain_and_html():
    papers=[paper(1),paper(2,'openreview'),paper(3,recommendation_group='random')]
    papers[2].abstract=''
    html=render_email(papers);plain=email_plain_text(html)
    for title in ('1. Paper 1','1. Paper 2','1. Paper 3'):assert title in html and title in plain
    assert html.count('class="digest-section"')==3
    assert 'max-width:760px' in html and '33.33%' not in html
    assert 'No abstract available' in html
    assert plain.index('Journals')<plain.index('Preprints')<plain.index('Random')
    assert 'No new recommendations in this group' in render_email([])

def test_chinese_prompt_without_live_llm():
    requests=[]
    def create(**kwargs):
        requests.append(kwargs)
        return make_chat_response('该研究提出了分子模拟方法。', model=kwargs.get('model'))
    client=SimpleNamespace(max_retries=0,chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p=paper(1);p.generate_tldr(client,{'language':'Chinese','generation_kwargs':{'model':'test'}},make_budget_guard())
    assert p.tldr_status=='generated' and len(requests)==1
    assert 'exactly one sentence in Chinese' in requests[0]['messages'][0]['content']

@pytest.mark.parametrize('group',['journals','preprints','random'])
@pytest.mark.parametrize('status',['generated','fallback','not_generated'])
@pytest.mark.parametrize('abstract',['Original abstract ' * 100, ''])
def test_original_abstract_kept_in_data_and_only_shown_without_summary(group,status,abstract):
    p=paper(1,recommendation_group=group);p.abstract=abstract
    p.tldr='该研究提出新方法。' if status=='generated' else abstract
    p.tldr_status=status
    html=render_email([p]);plain=email_plain_text(html)
    assert p.abstract == abstract
    if status=='generated':
        assert '该研究提出新方法。' in html and '该研究提出新方法。' in plain
        assert 'Original abstract' not in plain and 'No abstract available' not in plain
        if abstract: assert abstract not in html and abstract.strip() not in plain
    elif abstract:
        assert abstract in html and abstract.strip() in plain
    else:
        assert 'No abstract available' in html and 'No abstract available' in plain

@pytest.mark.parametrize('mode,full_text,expected,reason',[
    ('abstract','Entire full text','abstract',None),
    ('full_text','Entire full text','full_text',None),
    ('full_text',None,'abstract','full_text_unavailable'),
    ('full_text','Long text '*10000,'abstract','full_text_exceeds_budget_input_bound'),
])
def test_summary_input_selection_and_no_fulltext_prefix(mode,full_text,expected,reason):
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        return make_chat_response('该研究提出新方法。', model=kwargs.get('model'))
    client=SimpleNamespace(max_retries=0,chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p=paper(1,full_text=full_text)
    p.generate_tldr(client,{'input_mode':mode,'generation_kwargs':{'model':'test'}},make_budget_guard())
    assert p.summary_input_source==expected and p.summary_input_fallback==reason
    assert len(calls)==1
    prompt=calls[0]['messages'][1]['content']
    if expected=='full_text':assert full_text in prompt and 'Abstract:' not in prompt
    else:assert p.abstract in prompt and 'Full text:' not in prompt
    assert p.abstract not in render_email([p])

def test_full_text_budget_fallback_is_one_call(config):
    from decimal import Decimal
    from zotero_arxiv_daily.budget import BudgetRequests, utc_day
    config.llm.budget.enabled=True;config.llm.input_mode='full_text'
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        return make_chat_response('该研究提出新方法。', model=kwargs.get('model'))
    client=SimpleNamespace(max_retries=0,chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p=paper(1,full_text='Full paper '*10000)
    p.generate_tldr(client,config.llm,BudgetRequests(Decimal('.20'),Decimal('.004608'),utc_day()))
    assert len(calls)==1 and p.summary_input_source=='abstract'
    assert p.summary_input_fallback=='full_text_exceeds_budget_input_bound'
    assert p.summary_input_fallback in render_email([p])

def test_abstract_mode_never_fetches_fulltext(config):
    from zotero_arxiv_daily.executor import Executor
    from zotero_arxiv_daily.llm import ModelRequests
    executor=Executor.__new__(Executor);executor.config=config;executor.openai_client=None
    executor.model_requests=ModelRequests()
    executor.retrievers={'journals':SimpleNamespace(enrich=lambda p:pytest.fail('abstract mode fetched full text'))}
    executor._enrich(paper(1))

@pytest.mark.parametrize('fields',[
    {'primary_area':['unrelated'], 'secondary_area':['ai_4_physical_sciences']},
    {'primary_area':'applications->chemistry_physics_and_earth_sciences'},
    {'primary_area':'applications to physical sciences (physics, chemistry, biology, etc.)'},
])
def test_venue_schema_subject_aliases(config,fields):
    r=retriever(config)
    assert r.convert_to_paper(note(r,**fields)).subject_match_reason.startswith('subject field')

def test_corl_author_keyword_gate(config):
    r=retriever(config)
    raw=note(r,title='Robot planning',abstract='Experiments',primary_area=None,free_keyword_1='molecular dynamics')
    assert r.convert_to_paper(raw)

def test_next_year_iclr_group_discovery(config):
    r=retriever(config);seen=[]
    def get(client,path,params):
        identity=params['id'];seen.append(identity)
        return {'groups':[{'id':identity,'content':{'submission_id':{'value':identity+'/-/Submission'}}}]}
    r._get=get
    groups=list(r._groups(None,'ICLR',2026))
    assert 'ICLR.cc/2027/Conference' in seen
    assert all('/Workshop' not in group for group,inv in groups)

def test_invalid_summary_mode_fails_before_source_or_api_setup(config):
    from zotero_arxiv_daily.executor import Executor
    config.llm.input_mode='prefix'
    with pytest.raises(ValueError,match='input_mode'):Executor(config)

@pytest.mark.parametrize('text,expected',[
    ('We analyze T<Tc and predict ordering.','We analyze T<Tc and predict ordering.'),
    ('We analyze T&lt;Tc and predict ordering.','We analyze T<Tc and predict ordering.'),
    ('We analyze x<y and y>z for stability.','We analyze x<y and y>z for stability.'),
    ('We analyze x&lt;y and y&gt;z for stability.','We analyze x<y and y>z for stability.'),
    ('<jats:p>For T&lt;Tc, <italic>x</italic> increases.</jats:p>','For T<Tc, x increases.'),
    ('Compare p<q and q>r.','Compare p<q and q>r.'),
])
def test_abstract_cleaner_preserves_scientific_inequalities(text,expected):
    assert clean_abstract(text)==expected

def test_dedup_adopts_abstract_provenance_without_overwriting():
    from zotero_arxiv_daily.identity import deduplicate
    a=paper(1,doi='10.1000/same');a.abstract=''
    b=paper(1,'openreview',doi='10.1000/same',abstract_source='OpenReview')
    merged=deduplicate([a,b])[0]
    assert merged.abstract==b.abstract and merged.abstract_source=='OpenReview'
    c=paper(1,doi='10.1000/same',abstract_source='Crossref');c.abstract='Published abstract'
    merged=deduplicate([c,b])[0]
    assert merged.abstract=='Published abstract' and merged.abstract_source=='Crossref'

def test_openreview_malformed_content_does_not_drop_valid_neighbors(config,monkeypatch):
    r=retriever(config)
    good=note(r);bad=('TMLR','TMLR',dict(good[2],id='bad',content=None))
    good2=('TMLR','TMLR',dict(good[2],id='second'))
    monkeypatch.setattr(r,'_retrieve_raw_papers',lambda:[good,bad,good2])
    assert len(r.retrieve_papers())==2
    assert any('malformed submission' in error for error in r.failures)

@pytest.mark.parametrize('venue,status,pdate,kind,group',[
    ('TMLR','TMLR',True,'journal','journals'),
    ('TMLR','TMLR/Under_Review',False,'preprint','preprints'),
    ('TMLR','TMLR/Decision_Pending',False,'preprint','preprints'),
    ('TMLR','TMLR',False,'preprint','preprints'),
    ('ICLR','ICLR.cc/2026/Conference',True,'conference','preprints'),
    ('TMLR','unknown',True,'preprint','preprints'),
])
def test_verified_publication_classification(config,venue,status,pdate,kind,group):
    from zotero_arxiv_daily.selection import publication_group
    r=retriever(config);identity='TMLR' if venue=='TMLR' else 'ICLR.cc/2026/Conference'
    r.venue_ids[identity]={'accepted':identity,'under_review':identity+'/Under_Review','decision_pending':identity+'/Decision_Pending'}
    raw=note(r,venueid=status);raw=(venue,identity,raw[2])
    if pdate:raw[2]['pdate']=raw[2]['odate']
    p=r.convert_to_paper(raw)
    assert p.publication_kind==kind and publication_group(p)==group
    assert f'Type: {kind}' in render_email([p])

def test_newly_published_tmlr_uses_publication_date(config):
    r=retriever(config)
    r.venue_ids['TMLR']={'accepted':'TMLR'}
    raw=note(r,venueid='TMLR');raw=('TMLR','TMLR',raw[2])
    raw[2]['pdate']=raw[2]['odate']
    raw[2]['odate']=raw[2]['cdate']
    p=r.convert_to_paper(raw)
    assert p and p.publication_kind=='journal'
    assert p.published.timestamp()==raw[2]['pdate']/1000


@pytest.mark.parametrize('readers',[None,[],['private'],'everyone'])
def test_authenticated_openreview_rejects_nonpublic_notes(config,readers):
    r=retriever(config);r.authenticated=True;raw=note(r)
    if readers is not None:raw[2]['readers']=readers
    assert r.convert_to_paper(raw) is None


def test_authenticated_public_note_excludes_private_fields(config):
    r=retriever(config);r.authenticated=True;raw=note(r)
    raw[2]['readers']=['everyone']
    for key in ['abstract','authors','primary_area','pdf','venue','venueid','keywords']:
        raw[2]['content'][key]={'value':'CONFIDENTIAL','readers':['private']}
    p=r.convert_to_paper(raw)
    assert p and not p.abstract and not p.authors and not p.pdf_url
    assert 'CONFIDENTIAL' not in repr(p)
    raw[2]['content']['title']['readers']=['private']
    assert r.convert_to_paper(raw) is None


def test_authenticated_public_note_is_retained(config):
    r=retriever(config);r.authenticated=True;raw=note(r);raw[2]['readers']=['everyone']
    raw[2]['content']['abstract']['readers']=['everyone']
    assert r.convert_to_paper(raw).abstract=='Experiments'


def test_openreview_official_login(config,monkeypatch):
    r=retriever(config);calls=[]
    monkeypatch.setenv('OPENREVIEW_USERNAME','synthetic-user');monkeypatch.setenv('OPENREVIEW_PASSWORD','synthetic-password')
    def post(url,**kwargs):
        calls.append((url,kwargs));return SimpleNamespace(status_code=200,json=lambda:{'token':'synthetic-token'})
    client=SimpleNamespace(headers={},post=post)
    r._authenticate(client)
    assert r.authenticated and client.headers['Authorization']=='Bearer synthetic-token'
    url,kwargs=calls[0]
    assert url=='https://api2.openreview.net/login' and kwargs['json']['id']=='synthetic-user'
    assert kwargs['json']['password']=='synthetic-password' and kwargs['allow_redirects'] is False


@pytest.mark.parametrize('result',[{'mfaPending':True,'mfaPendingToken':'SENSITIVE'}, {}, {'token':'bad token'}, 'SENSITIVE'])
def test_openreview_login_invalid_or_mfa_is_sanitized(config,monkeypatch,result):
    r=retriever(config)
    monkeypatch.setenv('OPENREVIEW_USERNAME','synthetic-user');monkeypatch.setenv('OPENREVIEW_PASSWORD','synthetic-password')
    client=SimpleNamespace(headers={},post=lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:result))
    with pytest.raises(RuntimeError) as error:r._authenticate(client)
    assert 'SENSITIVE' not in str(error.value) and not r.authenticated and not client.headers
    if isinstance(result,dict) and result.get('mfaPending'):assert 'MFA' in str(error.value)


def test_openreview_login_transport_error_sanitized(config,monkeypatch):
    r=retriever(config)
    monkeypatch.setenv('OPENREVIEW_USERNAME','synthetic-user');monkeypatch.setenv('OPENREVIEW_PASSWORD','synthetic-password')
    def post(*a,**kw):raise RuntimeError('SENSITIVE response body and password')
    with pytest.raises(RuntimeError,match='response withheld') as error:r._authenticate(SimpleNamespace(post=post))
    assert 'SENSITIVE' not in str(error.value)


def test_openreview_credentials_optional_but_pair_required(config,monkeypatch):
    r=retriever(config)
    monkeypatch.delenv('OPENREVIEW_USERNAME',raising=False);monkeypatch.delenv('OPENREVIEW_PASSWORD',raising=False)
    r._authenticate(SimpleNamespace());assert not r.authenticated
    monkeypatch.setenv('OPENREVIEW_USERNAME','synthetic-user')
    with pytest.raises(RuntimeError,match='both OPENREVIEW'):r._authenticate(SimpleNamespace())


@pytest.mark.parametrize('exclusions',[['excluded-group'],None,'',{},False,0,['everyone']])
def test_authenticated_openreview_rejects_note_exclusions(config,exclusions):
    r=retriever(config);r.authenticated=True;raw=note(r)
    raw[2].update(readers=['everyone'],nonreaders=exclusions)
    # No Paper is created, so neither digest rendering nor LLM sees the note.
    assert r.convert_to_paper(raw) is None


@pytest.mark.parametrize('exclusions',[['excluded-group'],None,'',{},False,0,['everyone']])
def test_openreview_excluded_fields_never_reach_paper(config,exclusions):
    r=retriever(config);r.authenticated=True;raw=note(r)
    raw[2].update(readers=['everyone'],nonreaders=[])
    raw[2]['content']['abstract']={'value':'CONFIDENTIAL','readers':['everyone'],'nonreaders':exclusions}
    raw[2]['content']['authors']={'value':['CONFIDENTIAL'],'nonreaders':exclusions}
    p=r.convert_to_paper(raw)
    assert p and not p.abstract and not p.authors and 'CONFIDENTIAL' not in repr(p)
    raw[2]['content']['title']['nonreaders']=exclusions
    assert r.convert_to_paper(raw) is None


@pytest.mark.parametrize('explicit_empty',[False,True])
def test_openreview_public_note_without_exclusions_is_retained(config,explicit_empty):
    r=retriever(config);r.authenticated=True;raw=note(r);raw[2]['readers']=['everyone']
    if explicit_empty:
        raw[2]['nonreaders']=[]
        raw[2]['content']['abstract']['nonreaders']=[]
    assert r.convert_to_paper(raw).abstract=='Experiments'


def test_openreview_pagination_uses_supported_queries_and_local_date_filter(config,monkeypatch):
    import zotero_arxiv_daily.retriever.openreview_retriever as module
    config.source.openreview.venues=['TMLR'];r=retriever(config);queries=[]
    public=note(r)[2];public['readers']=['everyone']
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(module,'session',lambda:Client())
    monkeypatch.setattr(r,'_groups',lambda *a:[('TMLR','TMLR/-/Submission')])
    def get(client,path,params):
        queries.append(params);return {'notes':[public],'count':1}
    monkeypatch.setattr(r,'_get',get)
    result=r.retrieve_papers()
    assert len(result)==1  # cdate is old but first-public odate is within window.
    assert 'mintmdate' not in queries[0] and 'mintcdate' not in queries[0]
    assert queries[0]['count']=='true' and queries[0]['sort']=='tmdate:desc'


def test_openreview_filter_stage_counts(config):
    r=retriever(config);stats={}
    assert r.convert_to_paper(note(r),diagnostics=stats)
    assert all(stats[key]==1 for key in ['examined','public_acl','valid_date','within_window','keyword_match','subject_match'])
    old=note(r);old[2]['odate']=int((r.since-timedelta(days=1)).timestamp()*1000)
    assert r.convert_to_paper(old,diagnostics=stats) is None
    assert stats['older_than_window']==1 and stats['keyword_match']==1
