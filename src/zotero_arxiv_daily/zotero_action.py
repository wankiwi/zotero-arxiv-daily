"""Non-authorizing email navigation for a future authenticated confirmation service."""
import hashlib
import re
from urllib.parse import urlsplit
from .identity import paper_id


def confirmation_origin(value):
    if value is None or value == '':
        return None
    if not isinstance(value,str):
        raise ValueError('Zotero confirmation origin must be an HTTPS origin')
    parsed=urlsplit(value)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None,443) or parsed.path not in ('','/') or parsed.query or parsed.fragment
            or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',parsed.hostname)
            or '.' not in parsed.hostname):
        raise ValueError('Zotero confirmation origin must contain only an HTTPS host; no tokens, query or path')
    return 'https://' + parsed.hostname


def confirmation_link(paper, origin=None):
    origin=confirmation_origin(origin)
    if origin is None:
        return None
    # Public deterministic identifier, never proof of authorization or a bearer capability.
    identifier=hashlib.sha256(paper_id(paper).encode()).hexdigest()
    return origin+'/zotero/confirm?paper='+identifier
