"""Read Zotero permission names and collection-match count inside Actions only."""
import hashlib
import json
import os
import re
import sys
import requests


class ReadOnlyError(Exception):
    pass


def inspect(client, key, user_id, collection_hash):
    if not re.fullmatch(r'\d+',user_id) or not re.fullmatch(r'[0-9a-f]{64}',collection_hash):
        raise ReadOnlyError('Invalid inspection parameters')
    headers={'Zotero-API-Key':key,'Zotero-API-Version':'3'}
    def get(path,params=None):
        # Fixed origin/path construction; credentials never enter URL or output.
        with client.get('https://api.zotero.org'+path,headers=headers,params=params,
                        timeout=(5,20),allow_redirects=False,stream=True) as response:
            if response.status_code!=200:
                raise ReadOnlyError('Zotero read failed HTTP '+str(response.status_code))
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>2_000_000:raise ReadOnlyError('Zotero response size exceeded')
                chunks.append(chunk)
            return json.loads(b''.join(chunks)),response.headers
    info,_=get('/keys/current')
    if str(info.get('userID'))!=user_id:raise ReadOnlyError('Configured user does not match key owner')
    access=(info.get('access') or {}).get('user') or {}
    # Only fixed permission names and boolean values leave the runner.
    permissions={k:bool(access.get(k,False)) for k in ('library','notes','write','files')}
    matches=0;seen=set()
    for page in range(20):
        records,headers=get('/users/'+user_id+'/collections',{'limit':100,'start':page*100,'format':'json'})
        if not isinstance(records,list):raise ReadOnlyError('Invalid collection response')
        for item in records:
            data=item.get('data') or {};name=data.get('name');identity=item.get('key')
            if not isinstance(name,str) or not identity or identity in seen:raise ReadOnlyError('Incomplete collection pagination')
            seen.add(identity)
            matches+=hashlib.sha256(name.encode()).hexdigest()==collection_hash
        total=headers.get('Total-Results')
        if total is not None:
            if len(seen)==int(total):break
            if len(seen)>int(total) or len(records)<100:
                raise ReadOnlyError('Incomplete collection pagination')
        elif len(records)<100:break
    else:raise ReadOnlyError('Collection pagination limit reached')
    return {'user_matches':True,'personal_library_permissions':permissions,'target_collection_matches':matches,
            'target_collection_unique':matches==1,'library_writes':0}


def main():
    try:
        with requests.Session() as client:
            result=inspect(client,os.environ['ZOTERO_KEY'],os.environ['ZOTERO_ID'],os.environ['TARGET_COLLECTION_SHA256'])
        print(json.dumps(result))
        return 0
    except ReadOnlyError as exc:
        print(json.dumps({'status':'inspection_failed','reason':str(exc)}))
    except Exception:
        # Never print exception/request reprs, response bodies, IDs, keys, or collection names.
        print(json.dumps({'status':'inspection_failed','reason':'request_or_metadata_error'}))
    return 1

if __name__=='__main__':sys.exit(main())
