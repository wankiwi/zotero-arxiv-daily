"""Optional local-only vector cache. Never included in workflow state/artifacts."""
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from loguru import logger


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def valid_vector(vector):
    return isinstance(vector, np.ndarray) and vector.ndim == 1 and vector.size > 0 and np.issubdtype(vector.dtype, np.floating) and np.isfinite(vector).all() and bool(np.linalg.norm(vector))


class EmbeddingCache:
    def __init__(self, namespace, directory=None):
        self.namespace = digest(namespace)
        self.memory = {}
        self.directory = Path(directory).expanduser() / self.namespace if directory else None
        if self.directory:
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError:
                logger.warning('Local embedding cache directory unavailable; using memory only')
                self.directory = None

    def key(self, text):
        return digest([self.namespace, text])

    def get(self, text):
        key = self.key(text)
        if key in self.memory:
            return self.memory[key]
        if self.directory:
            path = self.directory / (key + '.npz')
            if path.exists():
                try:
                    with np.load(path, allow_pickle=False) as stored:
                        vector = stored['vector']
                        checksum = str(stored['checksum'].item())
                    if not valid_vector(vector) or hashlib.sha256(vector.tobytes()).hexdigest() != checksum:
                        raise ValueError('Invalid cached embedding')
                    self.memory[key] = vector
                    return vector
                except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
                    logger.warning('Ignoring corrupt local embedding cache entry; recomputing')
        return None

    def put(self, text, vector):
        vector = np.asarray(vector)
        if not valid_vector(vector):
            raise ValueError('Local encoder returned an invalid embedding')
        key = self.key(text)
        self.memory[key] = vector.copy()
        if self.directory:
            buffer = io.BytesIO()
            np.savez(buffer, vector=vector, checksum=hashlib.sha256(vector.tobytes()).hexdigest())
            temporary = None
            try:
                with NamedTemporaryFile(dir=self.directory, delete=False) as file:
                    temporary = Path(file.name)
                    file.write(buffer.getvalue())
                os.replace(temporary, self.directory / (key + '.npz'))
            except OSError:
                logger.warning('Unable to persist local embedding cache; retaining in-memory result')
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
