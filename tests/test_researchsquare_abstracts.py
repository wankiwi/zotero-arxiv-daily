import pytest
from zot2dailypaper import abstracts, aps_metadata
from zot2dailypaper.identity import paper_id
from tests.canned_responses import make_sample_paper
import json

DOI='10.21203/rs.3.rs-123456/v2'
TITLE='Synthetic chemistry study'
TEXT='A sufficiently long complete abstract describing a synthetic molecular chemistry study.'

@pytest.mark.parametrize('found_doi, title, text, accepted', [
 (DOI,TITLE,TEXT,True), (DOI.replace('/v2','/v1'),TITLE,TEXT,False),
 (DOI.rsplit('/',1)[0],TITLE,TEXT,False), (DOI,'Different paper',TEXT,False),
 (DOI,TITLE,None,False), (DOI,TITLE,TEXT+'...',False)])
def test_exact_version_title_and_full_abstract_required(monkeypatch,found_doi,title,text,accepted):
    calls=[]
    def fetch(client,url,provider,blocked):
        calls.append(url)
        assert url.endswith('10.21203%2Frs.3.rs-123456%2Fv2')
        if provider=='OpenAlex':return None
        return json.dumps({'message':{'DOI':found_doi,'title':[title],'abstract':text}}).encode()
    monkeypatch.setattr(aps_metadata,'fetch',fetch)
    p=make_sample_paper(source='researchsquare',doi=DOI,title=TITLE,abstract='')
    identity=paper_id(p)
    abstracts.recover_abstracts([p], {'enabled':True})
    assert bool(p.abstract)==accepted
    assert paper_id(p)==identity=='doi:10.21203/rs.3.rs-123456'
    if accepted:
        assert p.abstract==TEXT and p.abstract_source=='Crossref'
        assert p.abstract_source_url==calls[0] and p.abstract_recovery_status=='recovered'


def test_openalex_fallback_preserves_version(monkeypatch):
    def fetch(client,url,provider,blocked):
        if provider=='Crossref':return None
        return json.dumps({'doi':'https://doi.org/'+DOI,'title':TITLE,
                           'abstract_inverted_index':{w:[i] for i,w in enumerate(TEXT.split())}}).encode()
    monkeypatch.setattr(aps_metadata,'fetch',fetch)
    p=make_sample_paper(doi=DOI,title=TITLE,abstract='')
    abstracts.recover_abstracts([p],{'enabled':True})
    assert p.abstract==TEXT and p.abstract_source=='OpenAlex'


def test_unknown_researchsquare_version_is_not_guessed(monkeypatch):
    monkeypatch.setattr(aps_metadata,'fetch',lambda *a:pytest.fail('Unknown version must not be fetched'))
    p=make_sample_paper(doi=DOI.rsplit('/',1)[0],abstract='')
    abstracts.recover_abstracts([p],{'enabled':True})
    assert not p.abstract and p.abstract_recovery_status=='researchsquare_version_unverified'


def test_selection_lookup_limit_still_applies(monkeypatch):
    calls=[]
    monkeypatch.setattr(abstracts,'recover_researchsquare',lambda paper,*args:calls.append(paper))
    papers=[make_sample_paper(doi=DOI,abstract='') for _ in range(3)]
    abstracts.recover_abstracts(papers,{'enabled':True,'max_papers':1})
    assert calls==papers[:1]
