"""Authenticated whole-package private embedding cache; never uploads plaintext."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile
import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .embedding_cache import valid_vector, validate_array_archive

MAGIC = b'ZPDEMB1\0'
# Durable v1 format identity: keep the original repository slug after renaming.
# Changing these authenticated bytes would invalidate existing encrypted caches.
AAD = b'wankiwi/zotero-arxiv-daily:private-embedding-cache:v1'
MAX_PACKAGE = 128 * 1024 * 1024
MAX_MEMBER = 1024 * 1024
MAX_FILES = 20000
MAX_MANIFEST = 8 * 1024 * 1024
ENTRY = re.compile(r'private/[0-9a-f]{64}/[0-9a-f]{64}\.npz')


def decode_key(value):
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except Exception:
        raise ValueError('Invalid dedicated cache key encoding') from None
    if len(key) != 32:
        raise ValueError('Dedicated cache key must decode to 32 bytes')
    return key


def validate_payload(payload):
    if len(payload) > MAX_MEMBER:
        raise ValueError('Oversized cache member')
    validate_array_archive(io.BytesIO(payload), MAX_MEMBER)
    with np.load(io.BytesIO(payload), allow_pickle=False) as data:
        if set(data.files) != {'vector', 'checksum'}:
            raise ValueError('Unexpected vector fields')
        vector = data['vector']
        if not valid_vector(vector) or vector.dtype != np.float32 or vector.size > 65536:
            raise ValueError('Invalid private vector')
        if str(data['checksum'].item()) != hashlib.sha256(vector.tobytes()).hexdigest():
            raise ValueError('Corrupt private vector')


def seal(root, key):
    root = Path(root).resolve()
    entries = []
    total = 0
    paths = sorted((root/'private').glob('*/*.npz'), key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if not ENTRY.fullmatch(relative) or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unsafe cache entry')
        if path.stat().st_size > MAX_MEMBER:
            raise ValueError('Oversized cache entry')
        payload = path.read_bytes()
        validate_payload(payload)
        if len(entries) >= MAX_FILES or total + len(payload) > MAX_PACKAGE - 16*1024*1024:
            continue  # Bounded snapshot; old vectors may be recomputed, never delete source files.
        total += len(payload)
        entries.append((relative, payload, path.stat().st_mtime_ns))
    manifest = {'version': 1, 'files': [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                                      'last_used_ns': used} for name, data, used in entries]}
    manifest_bytes = json.dumps(manifest).encode()
    if len(manifest_bytes) > MAX_MANIFEST:
        raise ValueError('Cache manifest limit exceeded')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('manifest.json', manifest_bytes)
        for name, data, _ in entries:
            archive.writestr(name, data)
    plaintext = buffer.getvalue()
    if len(plaintext) > MAX_PACKAGE:
        raise ValueError('Cache package limit exceeded')
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, AAD)


def unseal(blob, key, destination):
    if len(blob) > MAX_PACKAGE+36 or not blob.startswith(MAGIC):
        raise ValueError('Invalid encrypted package')
    nonce = blob[len(MAGIC):len(MAGIC)+12]
    plaintext = AESGCM(key).decrypt(nonce, blob[len(MAGIC)+12:], AAD)
    # Authenticate first, then validate every member before creating any file.
    with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(infos) > MAX_FILES+1 or len(names) != len(set(names)) or 'manifest.json' not in names:
            raise ValueError('Invalid archive members')
        if any(i.compress_type != zipfile.ZIP_STORED or i.file_size > (MAX_MANIFEST if i.filename=='manifest.json' else MAX_MEMBER) for i in infos):
            raise ValueError('Invalid archive sizes or compression')
        if sum(i.file_size for i in infos) > MAX_PACKAGE:
            raise ValueError('Expanded package limit exceeded')
        manifest = json.loads(archive.read('manifest.json'))
        records = manifest.get('files')
        if manifest.get('version') != 1 or not isinstance(records, list) or len(records) != len(infos)-1:
            raise ValueError('Invalid private manifest')
        entries = []
        seen = set()
        for record in records:
            name = record['path']
            if not ENTRY.fullmatch(name) or name in seen:
                raise ValueError('Unsafe private cache path')
            seen.add(name)
            payload = archive.read(name)
            if len(payload) != record['size'] or hashlib.sha256(payload).hexdigest() != record['sha256']:
                raise ValueError('Private cache manifest mismatch')
            validate_payload(payload)
            used = record.get('last_used_ns', 0)
            if type(used) is not int or not 0 <= used < 2**63:
                raise ValueError('Invalid cache retention metadata')
            entries.append((name, payload, used))
    destination = Path(destination).resolve()
    for name, payload, used in entries:
        target = destination/name
        if not target.parent.resolve().is_relative_to(destination) or target.is_symlink():
            raise ValueError('Unsafe destination')
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary.write(payload)
            temp_path = Path(temporary.name)
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
        os.utime(target, ns=(used, used))
    return len(entries)
