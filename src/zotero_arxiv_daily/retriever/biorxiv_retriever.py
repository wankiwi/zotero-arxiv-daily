import requests
from datetime import datetime, timedelta, timezone
from ..identity import normalize_doi
from ..http import session
from .base import BaseRetriever, register_retriever
from ..protocol import Paper
from loguru import logger
from typing import Any
from time import sleep

@register_retriever("biorxiv")
class BiorxivRetriever(BaseRetriever):
    server = "biorxiv"

    def __init__(self, config):
        super().__init__(config)
        if self.retriever_config.category is None:
            raise ValueError(f"category must be specified for {self.name}")

    def _retrieve_raw_papers(self) -> list[dict[str, Any]]:
        days = self.retriever_config.get('window_days')
        if days is not None or '*' in self.retriever_config.category:
            return self._retrieve_window(int(days if days is not None else 1))
        api_url = f"https://api.biorxiv.org/details/{self.server}/2d"
        retry_num = 10
        delay_time = 10
        for i in range(retry_num):
            try:
                response = requests.get(api_url, timeout=(10, 30))
                response.raise_for_status()
                break
            except Exception as e:
                if i == retry_num - 1:
                    raise e
                else:
                    logger.warning(f"Failed to retrieve papers: {str(e)}. Retry in {delay_time} seconds.")
                    sleep(delay_time)
        result = response.json()
        collection = result['collection']
        if len(collection) == 0:
            logger.warning(f"No paper found. API Message: {result['messages']}")
            return []
        all_dates = set(c['date'] for c in collection)
        latest_date = sorted(all_dates)[-1]
        collection = [c for c in collection if c['date'] == latest_date]
        categories = [c.lower() for c in self.retriever_config.category]
        collection = [c for c in collection if c['category'].lower() in categories]
        if self.config.executor.debug:
            collection = collection[:10]
        return collection


    def _retrieve_window(self, days):
        max_pages = int(self.retriever_config.get('max_pages', 100))
        if not 1 <= days <= 90 or max_pages < 1:
            raise ValueError(f'{self.name} requires window_days 1–90 and positive max_pages')
        until = datetime.now(timezone.utc).date()
        since = until - timedelta(days=days)
        categories = {category.lower() for category in self.retriever_config.category}
        records, cursor = [], 0
        with session() as client:
            for _ in range(max_pages):
                response = client.get(f'https://api.biorxiv.org/details/{self.server}/{since}/{until}/{cursor}',
                                      timeout=(10, 30))
                response.raise_for_status()
                result = response.json()
                messages = result.get('messages', [])
                if messages and messages[0].get('status') not in (None, 'ok'):
                    raise RuntimeError(f'{self.name} API returned: {messages}')
                collection = result['collection']
                for item in collection:
                    if since.isoformat() <= item.get('date', '') <= until.isoformat():
                        if '*' in categories or item.get('category', '').lower() in categories:
                            records.append(item)
                cursor += len(collection)
                total = messages[0].get('total') if messages else None
                if total is not None and cursor >= int(total):
                    break
                if not collection:
                    if total is not None:
                        raise RuntimeError(f'{self.name} API pagination returned an incomplete collection')
                    break
            else:
                raise RuntimeError(f'{self.name} max_pages reached; narrow window_days or increase max_pages')
        # bioRxiv and medRxiv use the same DOI for all versions.
        newest = {}
        for item in records:
            doi = normalize_doi(item.get('doi'))
            if doi and (doi not in newest or int(item.get('version', 0)) > int(newest[doi].get('version', 0))):
                newest[doi] = item
        records = list(newest.values())
        return records[:10] if self.config.executor.debug else records

    def convert_to_paper(self, raw_paper:dict[str, Any]) -> Paper | None:
        title = raw_paper['title']
        authors = [a.strip() for a in raw_paper['authors'].split(';')]
        abstract = raw_paper['abstract']
        pdf_url = f"https://www.{self.server}.org/content/{raw_paper['doi']}v{raw_paper['version']}.full.pdf"
        full_text = None # biorxiv forbids scraping its pdf
        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=pdf_url,
            pdf_url=pdf_url,
            full_text=full_text,
            doi=normalize_doi(raw_paper.get('doi')),
            published=datetime.fromisoformat(raw_paper['date']).replace(tzinfo=timezone.utc) if raw_paper.get('date') else None,
        )