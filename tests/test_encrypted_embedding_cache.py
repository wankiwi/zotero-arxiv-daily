"""Only fixed synthetic test keys; never real secrets or network storage."""
import base64
import hashlib
import io
import json
import zipfile
import numpy as np
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from zotero_arxiv_daily.reranker.embedding_cache import EmbeddingCache
from zotero_arxiv_daily.reranker import encrypted_cache as module
from scripts import embedding_cache_state as bridge

KEY = b'\x01'*32


def test_authenticated_roundtrip_has_no_plaintext_names_or_vectors(tmp_path):
    source=tmp_path/'source';cache=EmbeddingCache({'model':'test'},source,dimension=3,dtype='float32')
    vector=np.array([1.25,2.5,3.75],dtype=np.float32);cache.put('PRIVATE-TEXT',vector)
    blob=module.seal(source,KEY)
    assert b'PRIVATE-TEXT' not in blob and b'manifest.json' not in blob and b'.npz' not in blob
    assert vector.tobytes() not in blob
    assert blob!=module.seal(source,KEY) # Fresh nonce, no production key generation.
    target=tmp_path/'target'
    assert module.unseal(blob,KEY,target)==1
    assert np.array_equal(EmbeddingCache({'model':'test'},target,dimension=3,dtype='float32').get('PRIVATE-TEXT'),vector)


@pytest.mark.parametrize('kind',['wrong_key','tamper','oversize'])
def test_rejected_package_creates_no_files(tmp_path,kind,monkeypatch):
    blob=module.seal(tmp_path/'empty',KEY);key=KEY
    if kind=='wrong_key':key=b'\x02'*32
    elif kind=='tamper':blob=blob[:-1]+bytes([blob[-1]^1])
    else:monkeypatch.setattr(module,'MAX_PACKAGE',1)
    with pytest.raises(Exception):module.unseal(blob,key,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_authenticated_malicious_path_rejected_before_write(tmp_path):
    payload=b'bad';manifest={'version':1,'files':[{'path':'../escape','size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}]}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        archive.writestr('manifest.json',json.dumps(manifest));archive.writestr('../escape',payload)
    nonce=b'\x03'*12;blob=module.MAGIC+nonce+AESGCM(KEY).encrypt(nonce,buffer.getvalue(),module.AAD)
    with pytest.raises(ValueError):module.unseal(blob,KEY,tmp_path/'out')
    assert not (tmp_path/'out').exists() and not (tmp_path/'escape').exists()


@pytest.mark.parametrize('value',['','abc',base64.b64encode(b'x'*16).decode(),'!!!!'])
def test_key_format_rejects_invalid_values(value):
    with pytest.raises(ValueError):module.decode_key(value)


def test_only_trusted_main_can_use_key(tmp_path,capsys):
    env={'GITHUB_REPOSITORY':'wankiwi/zotero-arxiv-daily','GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'schedule',
         'EMBEDDING_CACHE_KEY':base64.b64encode(KEY).decode(),'GITHUB_OUTPUT':str(tmp_path/'output')}
    assert bridge.key_for(env)==KEY
    for change in [{'GITHUB_REF':'refs/heads/feature'},{'GITHUB_EVENT_NAME':'pull_request'},{'GITHUB_REPOSITORY':'fork/repo'}]:
        assert bridge.key_for(env|change) is None
    bridge.main('gate',env|{'EMBEDDING_CACHE_KEY':''})
    assert 'enabled=false' in (tmp_path/'output').read_text()
    assert env['EMBEDDING_CACHE_KEY'] not in capsys.readouterr().out


def test_two_simulated_runs_restore_disk_hit_without_secret_output(tmp_path,monkeypatch,capsys):
    env={'GITHUB_REPOSITORY':'wankiwi/zotero-arxiv-daily','GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'schedule',
         'EMBEDDING_CACHE_KEY':base64.b64encode(KEY).decode(),'RUNNER_TEMP':str(tmp_path),'GITHUB_ENV':str(tmp_path/'env')}
    monkeypatch.setattr(bridge,'BLOB',tmp_path/'cipher'/'private.enc')
    bridge.main('restore',env)
    directory=(tmp_path/'env').read_text().strip().split('=',1)[1]
    EmbeddingCache({'model':'same'},directory,dimension=2,dtype='float32').put('private',np.ones(2,dtype=np.float32))
    bridge.main('save',env|{'PRIVATE_EMBEDDING_CACHE_DIR':directory})
    (tmp_path/'env').write_text('');bridge.main('restore',env)
    restored=(tmp_path/'env').read_text().strip().split('=',1)[1]
    cache=EmbeddingCache({'model':'same'},restored,dimension=2,dtype='float32')
    assert cache.get('private') is not None and cache.stats['disk_hits']==1
    assert env['EMBEDDING_CACHE_KEY'] not in capsys.readouterr().out

def test_array_header_cannot_request_unbounded_allocation(tmp_path):
    from zotero_arxiv_daily.reranker.embedding_cache import validate_array_archive
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(header, {'descr': '<f4', 'fortran_order': False, 'shape': (2**40,)})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('vector.npy', header.getvalue())
        checksum = io.BytesIO(); np.save(checksum, 'invalid')
        archive.writestr('checksum.npy', checksum.getvalue())
    with pytest.raises(ValueError, match='allocation'):
        validate_array_archive(io.BytesIO(buffer.getvalue()), 1024*1024)

def test_bounded_snapshot_keeps_recent_hits_and_preserves_private_timestamps(tmp_path,monkeypatch):
    import os
    source=tmp_path/'source'
    cache=EmbeddingCache({'model':'retention'},source,dimension=2,dtype='float32')
    cache.put('older',np.array([1,2],dtype=np.float32))
    cache.put('recent',np.array([2,3],dtype=np.float32))
    older=cache.directory/(cache.key('older')+'.npz')
    recent=cache.directory/(cache.key('recent')+'.npz')
    os.utime(older,ns=(100,100));os.utime(recent,ns=(200,200))
    monkeypatch.setattr(module,'MAX_FILES',1)
    target=tmp_path/'target'
    assert module.unseal(module.seal(source,KEY),KEY,target)==1
    restored=EmbeddingCache({'model':'retention'},target,dimension=2,dtype='float32')
    assert not (restored.directory/(cache.key('older')+'.npz')).exists()
    restored_recent=restored.directory/(cache.key('recent')+'.npz')
    assert restored_recent.stat().st_mtime_ns==200
    assert restored.get('recent') is not None
    assert restored_recent.stat().st_mtime_ns>200
    assert older.exists() and recent.exists()  # Snapshot retention never deletes source cache.
