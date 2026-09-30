"""Shared verified HTTP transport with connection pooling and bounded retries."""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def session(mailto: str | None = None) -> requests.Session:
    client = requests.Session()
    client.headers['User-Agent'] = 'zotero-arxiv-daily/1.0' + (f' (mailto:{mailto})' if mailto else '')
    retry = Retry(total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({'GET'}), respect_retry_after_header=True)
    client.mount('https://', HTTPAdapter(max_retries=retry))
    return client
