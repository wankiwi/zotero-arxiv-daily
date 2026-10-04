"""Trusted-main-only cache bridge. This script never prints or writes the key."""
import argparse
import os
from pathlib import Path
import tempfile
from zotero_arxiv_daily.reranker.encrypted_cache import decode_key, seal, unseal, MAX_PACKAGE

BLOB = Path('.embedding-cache/private.enc')


def authorized(environ):
    return (environ.get('GITHUB_REPOSITORY') == 'wankiwi/zotero-arxiv-daily'
            and environ.get('GITHUB_REF') == 'refs/heads/main'
            and environ.get('GITHUB_EVENT_NAME') in ('schedule', 'workflow_dispatch'))


def key_for(environ):
    if not authorized(environ):
        return None
    try:
        return decode_key(environ.get('EMBEDDING_CACHE_KEY', ''))
    except ValueError:
        return None


def main(command, environ=os.environ):
    key = key_for(environ)
    if command == 'gate':
        if environ.get('GITHUB_OUTPUT'):
            with open(environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('enabled='+str(key is not None).lower()+'\n')
        print('Encrypted cross-run cache enabled' if key else 'Encrypted cross-run cache disabled: trusted main and valid dedicated key required')
        return
    if key is None:
        print('Encrypted cross-run cache disabled; continuing without remote persistence')
        return
    if command == 'restore':
        directory = Path(tempfile.mkdtemp(prefix='private-embeddings-', dir=environ['RUNNER_TEMP']))
        os.chmod(directory, 0o700)
        with open(environ['GITHUB_ENV'], 'a') as env:
            env.write('PRIVATE_EMBEDDING_CACHE_DIR='+str(directory)+'\n')
        try:
            if not BLOB.exists():
                print('No encrypted cache restored; starting empty')
            elif BLOB.stat().st_size > MAX_PACKAGE+36:
                print('Encrypted cache rejected: package size limit; starting empty')
            else:
                count = unseal(BLOB.read_bytes(), key, directory)
                print(f'Authenticated private cache restored: {count} vectors')
        except Exception:
            print('Encrypted cache authentication/format validation failed; recomputing missing vectors')
    else:
        # Never leave an old restored ciphertext to be published as a fresh save.
        BLOB.unlink(missing_ok=True)
        directory = environ.get('PRIVATE_EMBEDDING_CACHE_DIR')
        if not directory:
            print('No private cache directory to save')
            return
        try:
            blob = seal(directory, key)
            BLOB.parent.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=BLOB.parent, delete=False) as temporary:
                temporary.write(blob)
                path = Path(temporary.name)
            os.replace(path, BLOB)
            if environ.get('GITHUB_OUTPUT'):
                with open(environ['GITHUB_OUTPUT'], 'a') as output:
                    output.write('saved=true\n')
            print('Private cache sealed; only authenticated ciphertext is eligible for upload')
        except Exception:
            print('Private cache packaging failed; skipping cache upload')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['gate', 'restore', 'save'])
    main(parser.parse_args().command)
