import hashlib
import json
import pytest
from scripts.inspect_zotero_access import inspect, ReadOnlyError

class Response:
    def __init__(self,data,status=200,headers=None):
        self.data=data;self.status_code=status;self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,size):yield json.dumps(self.data).encode()

class Client:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def get(self,url,**kwargs):
        assert 'secret-key' not in url and url.startswith('https://api.zotero.org/')
        assert kwargs['headers']['Zotero-API-Key']=='secret-key'
        assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
        self.calls.append(url)
        return next(self.responses)

TARGET=hashlib.sha256(b'Review inbox').hexdigest()

def test_inspection_outputs_only_permission_flags_and_match_counts():
    client=Client([Response({'userID':123,'key':'secret-key','username':'private-user','access':{'user':{'library':True,'write':False}}}),
                   Response([{'key':'PRIVATEKEY','data':{'name':'Review inbox'}}])])
    result=inspect(client,'secret-key','123',TARGET)
    assert result['target_collection_matches']==1 and result['target_collection_unique']
    assert result['personal_library_permissions']=={'library':True,'notes':False,'write':False,'files':False}
    assert not any(x in json.dumps(result) for x in ['secret-key','private-user','PRIVATEKEY','Review inbox','123'])
    assert result['library_writes']==0 and client.calls[0].endswith('/keys/current')

@pytest.mark.parametrize('status',[301,401,403,429])
def test_denied_or_redirected_requests_stop_without_retry(status):
    client=Client([Response({},status)])
    with pytest.raises(ReadOnlyError):inspect(client,'secret-key','123',TARGET)
    assert len(client.calls)==1


def test_duplicate_target_names_are_not_silently_selected():
    client=Client([Response({'userID':123}),Response([{'key':k,'data':{'name':'Review inbox'}} for k in ['A','B']])])
    result=inspect(client,'secret-key','123',TARGET)
    assert result['target_collection_matches']==2 and not result['target_collection_unique']


def test_wrong_owner_does_not_list_library():
    client=Client([Response({'userID':456})])
    with pytest.raises(ReadOnlyError):inspect(client,'secret-key','123',TARGET)
    assert len(client.calls)==1


def test_inspection_job_has_no_write_permission_or_delivery_keys():
    import yaml
    from pathlib import Path
    data=yaml.load(Path('.github/workflows/test.yml').read_text(),Loader=yaml.BaseLoader)
    job=data['jobs']['zotero-readonly-inspection']
    assert job['permissions']=={'contents':'read'}
    assert job['steps'][0]['with']['persist-credentials']=='false'
    env=job['steps'][-1]['env']
    assert set(env)=={'ZOTERO_KEY','ZOTERO_ID','TARGET_COLLECTION_SHA256'}
