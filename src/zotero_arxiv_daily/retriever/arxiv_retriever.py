from .base import BaseRetriever, register_retriever
import arxiv
from datetime import datetime, timedelta, timezone
from arxiv import Result as ArxivResult
from ..protocol import Paper
from ..identity import normalize_doi
from ..preprint_interests import categories_for, keyword_text
from ..utils import extract_markdown_from_pdf, extract_tex_code_from_tar
from tempfile import TemporaryDirectory
import feedparser
from tqdm import tqdm
import multiprocessing
import os
from queue import Empty
from typing import Any, Callable, TypeVar
from loguru import logger
import requests

T = TypeVar("T")

DOWNLOAD_TIMEOUT = (10, 60)
PDF_EXTRACT_TIMEOUT = 180
TAR_EXTRACT_TIMEOUT = 180


def _download_file(url: str, path: str) -> None:
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        with open(path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def _run_in_subprocess(
    result_queue: Any,
    func: Callable[..., T | None],
    args: tuple[Any, ...],
) -> None:
    try:
        result_queue.put(("ok", func(*args)))
    except Exception as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _run_with_hard_timeout(
    func: Callable[..., T | None],
    args: tuple[Any, ...],
    *,
    timeout: float,
    operation: str,
    paper_title: str,
) -> T | None:
    start_methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("fork" if "fork" in start_methods else start_methods[0])
    result_queue = context.Queue()
    process = context.Process(target=_run_in_subprocess, args=(result_queue, func, args))
    process.start()

    try:
        status, payload = result_queue.get(timeout=timeout)
    except Empty:
        if process.is_alive():
            process.kill()
        process.join(5)
        result_queue.close()
        result_queue.join_thread()
        logger.warning(f"{operation} timed out for {paper_title} after {timeout} seconds")
        return None

    process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)
    result_queue.close()
    result_queue.join_thread()

    if status == "ok":
        return payload

    logger.warning(f"{operation} failed for {paper_title}: {payload}")
    return None


def _extract_text_from_pdf_worker(pdf_url: str) -> str:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.pdf")
        _download_file(pdf_url, path)
        return extract_markdown_from_pdf(path)


def _extract_text_from_html_worker(html_url: str) -> str | None:
    import trafilatura

    downloaded = trafilatura.fetch_url(html_url)
    if downloaded is None:
        raise ValueError(f"Failed to download HTML from {html_url}")
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    if not text:
        raise ValueError(f"No text extracted from {html_url}")
    return text


def _extract_text_from_tar_worker(source_url: str, paper_id: str) -> str | None:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.tar.gz")
        _download_file(source_url, path)
        file_contents = extract_tex_code_from_tar(path, paper_id)
        if not file_contents or "all" not in file_contents:
            raise ValueError("Main tex file not found.")
        return file_contents["all"]


@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        self._raw = {}
        self.categories = categories_for(config, self.name) if self.interests.get('enabled', True) else []

    def _retrieve_raw_papers(self) -> list[ArxivResult]:
        if not self.interests.get('enabled', True):
            return []
        categories = self.categories
        keywords = self.interests.get('keywords', [])
        days = self.retriever_config.get('window_days')
        if '*' in categories or days is not None or keywords:
            # Submission dates precede public announcements, especially over weekends.
            days = int(days if days is not None else 7)
            if not 1 <= days <= 90:
                raise ValueError('arxiv window_days must be between 1 and 90')
            until = datetime.now(timezone.utc)
            since = until - timedelta(days=days)
            query = f'submittedDate:[{since:%Y%m%d%H%M} TO {until:%Y%m%d%H%M}]'
            if '*' not in categories:
                query += ' AND (' + ' OR '.join(f'cat:{category}' for category in categories) + ')'
            if keywords:
                query += ' AND (' + ' OR '.join(f'{field}:"{keyword_text(term)}"'
                    for term in keywords for field in ('ti', 'abs')) + ')'
            search = arxiv.Search(query=query, max_results=10 if self.config.executor.debug else None,
                                  sort_by=arxiv.SortCriterion.SubmittedDate,
                                  sort_order=arxiv.SortOrder.Descending)
            client = arxiv.Client(page_size=500, num_retries=3, delay_seconds=3)
            results = list(client.results(search))
            if '*' not in categories and not self.retriever_config.get('include_cross_list', False):
                results = [paper for paper in results if paper.primary_category in categories]
            return results
        client = arxiv.Client(num_retries=3, delay_seconds=3)
        query = '+'.join(categories)
        include_cross_list = self.config.source.arxiv.get("include_cross_list", False)
        # Get the latest paper from arxiv rss feed
        feed = feedparser.parse(f"https://rss.arxiv.org/atom/{query}")
        if 'Feed error for query' in feed.feed.get('title', ''):
            raise Exception(f"Invalid ARXIV_QUERY: {query}.")
        raw_papers = []
        allowed_announce_types = {"new", "cross"} if include_cross_list else {"new"}
        all_paper_ids = [
            i.id.removeprefix("oai:arXiv.org:")
            for i in feed.entries
            if i.get("arxiv_announce_type", "new") in allowed_announce_types
        ]
        if self.config.executor.debug:
            all_paper_ids = all_paper_ids[:10]

        # Get full information of each paper from arxiv api
        bar = tqdm(total=len(all_paper_ids))
        for i in range(0, len(all_paper_ids), 20):
            search = arxiv.Search(id_list=all_paper_ids[i:i + 20])
            batch = list(client.results(search))
            bar.update(len(batch))
            raw_papers.extend(batch)
        bar.close()

        return raw_papers

    def convert_to_paper(self, raw_paper: ArxivResult) -> Paper:
        title = raw_paper.title
        authors = [a.name for a in raw_paper.authors]
        abstract = raw_paper.summary
        pdf_url = raw_paper.pdf_url
        self._raw[raw_paper.entry_id] = raw_paper
        full_text = None
        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=raw_paper.entry_id,
            pdf_url=pdf_url,
            full_text=full_text,
            doi=normalize_doi(getattr(raw_paper, "doi", None)),
            published=getattr(raw_paper, "published", None),
        )

    def enrich(self, paper: Paper) -> Paper:
        raw = self._raw.get(paper.url)
        if raw is not None and not paper.full_text:
            paper.full_text = extract_text_from_html(raw)
            if paper.full_text is None:
                paper.full_text = extract_text_from_pdf(raw)
            if paper.full_text is None:
                paper.full_text = extract_text_from_tar(raw)
        return paper


def extract_text_from_html(paper: ArxivResult) -> str | None:
    html_url = paper.entry_id.replace("/abs/", "/html/")
    try:
        return _run_with_hard_timeout(_extract_text_from_html_worker, (html_url,),
            timeout=PDF_EXTRACT_TIMEOUT, operation="HTML extraction", paper_title=paper.title)
    except Exception as exc:
        logger.warning(f"HTML extraction failed for {paper.title}: {exc}")
        return None


def extract_text_from_pdf(paper: ArxivResult) -> str | None:
    if paper.pdf_url is None:
        logger.warning(f"No PDF URL available for {paper.title}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_pdf_worker,
        (paper.pdf_url,),
        timeout=PDF_EXTRACT_TIMEOUT,
        operation="PDF extraction",
        paper_title=paper.title,
    )


def extract_text_from_tar(paper: ArxivResult) -> str | None:
    source_url = paper.source_url()
    if source_url is None:
        logger.warning(f"No source URL available for {paper.title}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_tar_worker,
        (source_url, paper.entry_id),
        timeout=TAR_EXTRACT_TIMEOUT,
        operation="Tar extraction",
        paper_title=paper.title,
    )
