from contextlib import nullcontext
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import requests
import pytest
from hydra import initialize_config_dir,compose
from zot2dailypaper.retriever import chemrxiv_retriever as module
from zot2dailypaper.identity import canonical_doi,deduplicate,crossref_equivalent_dois
from zot2dailypaper.state import State
from zot2dailypaper.selection import publication_group
from tests.canned_responses import make_sample_paper

TEXT='A complete original abstract describing molecular dynamics and chemistry in interfacial water.'
DOI='10.26434/chemrxiv.15000001/v1'


def item(doi=DOI,**overrides):
    result={'DOI':doi,'prefix':'10.26434','member':'316','publisher':'American Chemical Society (ACS)','type':'posted-content','subtype':'preprint','title':['Chemical study'],
            'abstract':'<jats:p>'+TEXT+'</jats:p>','posted':{'date-parts':[[2026,10,2]]},
            'resource':{'primary':{'URL':'https://chemrxiv.org/doi/full/'+doi}},
            'author':[{'given':'A','family':'Scientist','affiliation':[{'name':'Institute'}]}]}
    result.update(overrides);return result


def ranker(config):
    config.preprint_interests={'chemrxiv':{'enabled':True}}
    retriever=module.ChemRxivRetriever(config)
    retriever.since=datetime(2026,10,2,tzinfo=timezone.utc)
    retriever.until=datetime(2026,10,3,tzinfo=timezone.utc)
    return retriever


def transport(monkeypatch,pages):
    calls=[]
    def get(url,**kwargs):
        calls.append((url,kwargs));data=pages[len(calls)-1]
        r=requests.Response();r.url=url;r.status_code=data if isinstance(data,int) else 200
        r._content=json.dumps(data).encode();r._content_consumed=True;return r
    from types import SimpleNamespace
    monkeypatch.setattr(module,'abstract_session',lambda *a:nullcontext(SimpleNamespace(get=get)))
    return calls


def test_source_page_pagination_and_newest_version(config,monkeypatch):
    r=ranker(config);r.page_size=2
    newer=item(DOI.replace('/v1','/v2'))
    other=item('10.26434/chemrxiv.15000002/v1')
    calls=transport(monkeypatch,[{'message':{'items':[item(),newer],'total-results':3,'next-cursor':'next'}},
                                 {'message':{'items':[other],'total-results':3,'next-cursor':'last'}}])
    papers=r.retrieve_papers()
    assert len(papers)==2 and papers[0].doi.endswith('/v2')
    assert all(p.abstract==TEXT and publication_group(p)=='preprints' for p in papers)
    assert all(p.abstract_source=='Crossref (ChemRxiv deposit)' for p in papers)
    assert len(calls)==2 and calls[1][1]['params']['cursor']=='next'
    assert 'from-posted-date:2026-10-02' in calls[0][1]['params']['filter']
    assert calls[0][1]['allow_redirects'] is False and calls[0][1]['stream'] is True


@pytest.mark.parametrize('changes',[{'type':'journal-article'},{'subtype':'other'},
    {'DOI':'10.26434/other.123/v1'}, {'publisher':'Other'}, {'member':'999'}, {'prefix':'10.99999'}, {'resource':{'primary':{'URL':'https://evil.test/paper'}}},
    {'posted':{'date-parts':[[2026,9,1]]}}, {'posted':{'date-parts':[[2026,10]]}},
    {'posted':{},'created':{'date-parts':[[2026,10,2]]}}])
def test_identity_and_precise_posted_date_required(config,monkeypatch,changes):
    r=ranker(config)
    transport(monkeypatch,[{'message':{'items':[item(**changes)],'total-results':1}}])
    assert r.retrieve_papers()==[]


@pytest.mark.parametrize('status',[401,403,429,302])
def test_access_refusal_and_redirect_stop_without_retry(config,monkeypatch,status):
    r=ranker(config);calls=transport(monkeypatch,[status])
    with pytest.raises(RuntimeError):r.retrieve_papers()
    assert len(calls)==1


@pytest.mark.parametrize('second',[{'items':[],'total-results':3},
    {'items':[item()],'total-results':3,'next-cursor':'next'}])
def test_incomplete_and_repeated_pagination_is_visible(config,monkeypatch,second):
    r=ranker(config);r.page_size=1
    transport(monkeypatch,[{'message':{'items':[item()],'total-results':3,'next-cursor':'next'}},{'message':second}])
    with pytest.raises(RuntimeError):r.retrieve_papers()


def test_max_pages_does_not_silently_truncate(config,monkeypatch):
    r=ranker(config);r.max_pages=1
    transport(monkeypatch,[{'message':{'items':[item()],'total-results':2,'next-cursor':'next'}}])
    with pytest.raises(RuntimeError,match='max_pages'):r.retrieve_papers()


@pytest.mark.parametrize('key,value',[('window_days',0),('max_pages',0),('page_size',101),('page_size',True)])
def test_options_validated(config,key,value):
    config.source.chemrxiv[key]=value
    with pytest.raises(ValueError):ranker(config)


def test_categories_not_silently_ignored_and_keyword_scope_independent(config,monkeypatch):
    config.preprint_interests={'chemrxiv':{'categories':['physical chemistry']}}
    with pytest.raises(ValueError,match='taxonomy'):module.ChemRxivRetriever(config)
    r=ranker(config);r.interests['keywords']=['interfacial water']
    transport(monkeypatch,[{'message':{'items':[item(),item('10.26434/chemrxiv.15000002/v1',abstract='Unrelated topic')],'total-results':2}}])
    assert len(r.retrieve_papers())==1


@pytest.mark.parametrize('doi,root',[(DOI,'10.26434/chemrxiv.15000001'),
    ('10.26434/chemrxiv-2025-abcde-v2','10.26434/chemrxiv-2025-abcde'),
    ('10.26434/chemrxiv.123456.v3','10.26434/chemrxiv.123456')])
def test_version_families_and_history(config,doi,root,tmp_path):
    assert canonical_doi(doi)==root
    state=State(tmp_path/'history.json')
    state.add([make_sample_paper(doi=doi,source='chemrxiv')])
    assert state.has(make_sample_paper(doi=root+'/v7',title='Changed title'))


def test_published_relations_deduplicate_without_transplanting_manuscript_text(tmp_path):
    pre=make_sample_paper(source='chemrxiv',doi=DOI,abstract=TEXT,related_dois=['10.1000/published'])
    published=make_sample_paper(source='journals',doi='10.1000/published',journal='Journal',abstract='',title='Published title')
    merged=deduplicate([pre,published])
    assert len(merged)==1 and merged[0].doi=='10.1000/published' and merged[0].abstract==''
    state=State(tmp_path/'history.json');state.add(merged);state.mark(merged,'email');state.save()
    restored=State(state.path)
    assert restored.has(make_sample_paper(doi=DOI,title='Changed title'))


def test_publication_relation_bridges_two_preprint_families():
    first=make_sample_paper(source='chemrxiv',doi=DOI,title='First')
    second=make_sample_paper(source='chemrxiv',doi='10.26434/chemrxiv.15000002/v1',title='Second')
    published=make_sample_paper(source='journals',doi='10.1000/published',journal='Journal',
        related_dois=[first.doi,second.doi],title='Published')
    assert len(deduplicate([first,second,published]))==1


def test_latest_version_never_uses_an_older_abstract():
    old=make_sample_paper(source='chemrxiv',doi=DOI,abstract=TEXT)
    new=make_sample_paper(source='chemrxiv',doi=DOI.replace('/v1','/v2'),abstract='')
    result=deduplicate([old,new])
    assert len(result)==1 and result[0].doi.endswith('/v2') and not result[0].abstract


def test_only_explicit_preprint_relations_are_equivalent():
    record=item(relation={'is-preprint-of':[{'id':'10.1000/published','id-type':'doi'}],
                          'is-correction-of':[{'id':'10.1000/unrelated','id-type':'doi'}]})
    assert crossref_equivalent_dois(record)==['10.1000/published']


@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('keywords',['[molecular]','[]'])
def test_schedule_explicit_opt_in_preserves_other_source_filters(tmp_path,enabled,keywords):
    from scripts.prepare_workflow import prepare
    root=Path(__file__).resolve().parents[1]
    shutil.copytree(root/'config',tmp_path/'config',ignore=shutil.ignore_patterns('runtime.yaml','private.yaml'))
    custom='preprint_interests:\n  chemrxiv:\n    enabled: '+str(enabled).lower()+'\n    categories: ["*"]\n    keywords: '+keywords+'\n'
    prepare(tmp_path,{'GITHUB_EVENT_NAME':'schedule','CUSTOM_CONFIG':custom})
    with initialize_config_dir(config_dir=str(tmp_path/'config'),version_base=None):cfg=compose(config_name='runtime')
    assert ('chemrxiv' in cfg.executor.source)==enabled
    assert list(cfg.preprint_interests.chemrxiv.keywords)==(['molecular'] if keywords=='[molecular]' else [])
    assert list(cfg.preprint_interests.openreview.venues)==['ICLR','NeurIPS','ICML','TMLR','CoRL']
    assert dict(cfg.executor.quotas)=={'journals':25,'preprints':15,'random':5}
    assert not cfg.output.rss.enabled and cfg.llm.budget.daily_cny==0.3


def test_indexed_cursor_does_not_stop_on_out_of_window_page(config,monkeypatch):
    r=ranker(config);r.page_size=1
    calls=transport(monkeypatch,[{'message':{'items':[item(posted={'date-parts':[[2026,9,1]]})],'total-results':3,'next-cursor':'same'}},
        {'message':{'items':[item('10.26434/chemrxiv.15000002/v1')],'total-results':3,'next-cursor':'same'}},
        {'message':{'items':[item('10.26434/chemrxiv.15000003/v1')],'total-results':3,'next-cursor':'same'}}])
    assert len(r.retrieve_papers())==2 and len(calls)==3
    assert all(c[1]['params']['sort']=='indexed' for c in calls)
    assert all('prefix:10.26434' in c[1]['params']['filter'] for c in calls)


@pytest.mark.parametrize('found_version',['v1','v2'])
def test_abstract_recovery_uses_exact_chemrxiv_version(monkeypatch,found_version):
    from types import SimpleNamespace
    from zot2dailypaper import abstracts
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        if 'openalex' in url:return SimpleNamespace(status_code=404)
        return SimpleNamespace(status_code=200,raise_for_status=lambda:None,
            json=lambda:{'message':{'DOI':DOI.replace('v1',found_version),'title':['Target'],'abstract':TEXT}})
    monkeypatch.setattr(abstracts,'session',lambda *a:nullcontext(SimpleNamespace(get=get)))
    monkeypatch.setattr(abstracts,'publisher_session',lambda:nullcontext(object()))
    paper=make_sample_paper(source='chemrxiv',doi=DOI.replace('v1','v2'),title='Target',abstract='')
    abstracts.recover_abstracts([paper],{'enabled':True,'publisher_fallback':False})
    assert calls and all('%2Fv2' in url for url in calls)
    assert bool(paper.abstract)==(found_version=='v2')


def test_publication_alias_matches_older_preprint_history(tmp_path):
    state=State(tmp_path/'history.json')
    state.add([make_sample_paper(source='chemrxiv',doi=DOI,title='Old title')])
    assert state.has(make_sample_paper(source='journals',doi='10.1000/published',title='Different title',related_dois=[DOI]))
    assert not state.has(make_sample_paper(doi='10.1000/unrelated',title='Old title'))


@pytest.mark.parametrize('relation',[None, [], {'has-preprint':None}, {'has-preprint':[None,42]}, {'has-preprint':'invalid'}])
def test_malformed_optional_relations_do_not_invent_identities(relation):
    assert crossref_equivalent_dois({'DOI':DOI,'relation':relation})==[]


def test_explicit_empty_keyword_list_means_all_chemistry(config,monkeypatch):
    config.preprint_interests={'chemrxiv':{'enabled':True,'keywords':[],'categories':['*']}}
    r=module.ChemRxivRetriever(config)
    r.since=datetime(2026,10,2,tzinfo=timezone.utc);r.until=datetime(2026,10,3,tzinfo=timezone.utc)
    transport(monkeypatch,[{'message':{'items':[item()],'total-results':1}}])
    assert len(r.retrieve_papers())==1
