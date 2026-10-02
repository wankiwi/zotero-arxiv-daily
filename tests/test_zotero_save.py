import hashlib
import time
import json
from types import SimpleNamespace
import pytest
from zotero_arxiv_daily.zotero_save import SaveService, SaveError, VerifiedSession, ZoteroWriter
from zotero_arxiv_daily.identity import paper_id
from tests.canned_responses import make_sample_paper


def service(tmp_path,writer=None):
    paper=make_sample_paper(doi='10.1000/synthetic',url='https://example.org/paper',abstract='Original abstract')
    writer=writer or SimpleNamespace(user_id='123',collection_key='TARGET01',ensure=lambda *a,**kw:'ITEM0001')
    svc=SaveService(writer,tmp_path/'saves.sqlite','https://papers.example.org','owner',[paper])
    session=VerifiedSession('owner','random-session-csrf-token',time.time()+300)
    args={'method':'POST','origin':svc.origin,'session':session,'csrf_token':session.csrf_token,
          'identifier':hashlib.sha256(paper_id(paper).encode()).hexdigest()}
    return svc,args,paper

@pytest.mark.parametrize('change',[{'method':'GET'},{'method':'HEAD'},{'origin':'https://evil.example'},
 {'csrf_token':'wrong'},{'session':None},{'session':VerifiedSession('other','x',9999999999)},
 {'session':VerifiedSession('owner','x',0)},{'identifier':'unknown'}])
def test_unauthorized_or_scanner_requests_cannot_write(tmp_path,change):
    writer=SimpleNamespace(user_id='123',collection_key='TARGET01',ensure=lambda *a,**kw:pytest.fail('unexpected remote call'))
    svc,args,_=service(tmp_path,writer)
    with pytest.raises(SaveError):svc.save(**(args|change))


def test_preview_is_read_only_and_duplicate_post_is_durable(tmp_path):
    calls=[]
    writer=SimpleNamespace(user_id='123',collection_key='TARGET01',ensure=lambda p,**kw:calls.append(kw) or 'ITEM0001')
    svc,args,paper=service(tmp_path,writer)
    assert svc.preview(args['session'],args['identifier'])['title']==paper.title
    assert calls==[]
    assert svc.save(**args)=='ITEM0001'
    reopened,_,_=service(tmp_path,writer)
    assert reopened.save(**args)=='ITEM0001' and len(calls)==1
    assert calls[0]['allow_write'] and len(calls[0]['token'])==32


def test_timeout_never_blindly_repeats_write(tmp_path):
    calls=[]
    def ensure(p,**kwargs):
        calls.append(kwargs)
        if kwargs['allow_write']:raise TimeoutError('secret must never escape')
        return 'ITEM0001'  # Reconciled as already present remotely.
    svc,args,_=service(tmp_path,SimpleNamespace(user_id='123',collection_key='TARGET01',ensure=ensure))
    with pytest.raises(SaveError,match='reconciliation') as exc:svc.save(**args)
    assert 'secret' not in str(exc.value)
    assert svc.save(**args)=='ITEM0001'
    assert [c['allow_write'] for c in calls]==[True,False]

class Response:
    def __init__(self,data,status=200,headers=None):self.data=data;self.status_code=status;self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,n):
        if self.data is not None:yield json.dumps(self.data).encode()

class Client:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def request(self,method,url,**kwargs):
        assert 'secret-key' not in url and url.startswith('https://api.zotero.org/')
        assert kwargs['allow_redirects'] is False
        self.calls.append((method,url,kwargs))
        return next(self.responses)


def item(collections):
    return {'key':'ITEM0001','version':4,'data':{'itemType':'preprint','DOI':'10.1000/synthetic','collections':collections}}

@pytest.mark.parametrize('collections,expected_methods',[(['TARGET01'],['GET']),(['OTHER001'],['GET','PATCH'])])
def test_existing_item_memberships_preserved(collections,expected_methods):
    client=Client([Response([item(collections)],headers={'Last-Modified-Version':'8','Total-Results':'1'}),Response(None,204)])
    writer=ZoteroWriter('secret-key','123','TARGET01',client)
    p=make_sample_paper(doi='10.1000/synthetic')
    assert writer.ensure(p,'x'*32,True)=='ITEM0001'
    assert [c[0] for c in client.calls]==expected_methods
    if len(client.calls)>1:
        assert client.calls[1][2]['json']=={'collections':['OTHER001','TARGET01']}
        assert client.calls[1][2]['headers']['If-Unmodified-Since-Version']=='4'


def test_create_citation_original_url_only_with_conflict_guard():
    template={'itemType':'preprint','title':'','url':'','abstractNote':'','DOI':'','date':'','creators':[],'collections':[]}
    client=Client([Response([],headers={'Last-Modified-Version':'8','Total-Results':'0'}),Response(template),
                   Response({'successful':{'0':{'key':'ITEM0001'}},'failed':{}})])
    writer=ZoteroWriter('secret-key','123','TARGET01',client)
    p=make_sample_paper(doi='10.1000/synthetic',journal=None,pdf_url='https://example.org/paper.pdf')
    assert writer.ensure(p,'x'*32,True)=='ITEM0001'
    assert [c[0] for c in client.calls]==['GET','GET','POST']
    post=client.calls[-1][2]
    assert post['headers']['If-Unmodified-Since-Version']=='8'
    assert post['headers']['Zotero-Write-Token']=='x'*32
    assert post['json'][0]['url']==p.url and post['json'][0]['collections']==['TARGET01']
    assert p.pdf_url not in json.dumps(client.calls)


def test_ambiguous_existing_matches_never_write():
    other=item([]);other['key']='ITEM0002'
    client=Client([Response([item([]),other],headers={'Last-Modified-Version':'8','Total-Results':'2'})])
    writer=ZoteroWriter('secret-key','123','TARGET01',client)
    with pytest.raises(SaveError,match='Multiple'):writer.ensure(make_sample_paper(doi='10.1000/synthetic'),'x'*32,True)
    assert len(client.calls)==1


def test_uncertain_no_match_refuses_second_creation():
    client=Client([Response([],headers={'Last-Modified-Version':'8','Total-Results':'0'})])
    writer=ZoteroWriter('secret-key','123','TARGET01',client)
    with pytest.raises(SaveError,match='uncertain'):writer.ensure(make_sample_paper(doi='10.1000/synthetic'),'x'*32,False)
    assert len(client.calls)==1

@pytest.mark.parametrize('status',[301,401,403,409,412,429,500])
def test_http_failures_do_not_retry_or_leak(status):
    client=Client([Response({'secret':'do not expose'},status)])
    writer=ZoteroWriter('secret-key','123','TARGET01',client)
    with pytest.raises(SaveError) as exc:writer.request('POST','/users/123/items',data=[])
    assert len(client.calls)==1 and 'secret' not in str(exc.value)
