from loguru import logger
from pyzotero import zotero
from omegaconf import DictConfig, ListConfig
from .utils import glob_match
from .retriever import get_retriever_cls
from .protocol import CorpusPaper
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from .identity import canonical_doi, paper_doi, title_key, deduplicate
from .state import State
from .output import write_rss
from .reranker import get_reranker_cls
from .construct_email import render_email
from .utils import send_email
from openai import OpenAI
from .llm import ModelRequests
from .budget import prepare_budget, BudgetUnavailable
from .preprint_interests import enabled_sources
from .selection import quotas_for, select_papers, pending_batch
from .abstracts import clean_abstract, recover_abstracts


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


class Executor:
    def __init__(self, config: DictConfig):
        self.config = config
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        self.retrievers = {source: get_retriever_cls(source)(config) for source in enabled_sources(config)}
        if not self.retrievers:
            raise ValueError('No enabled sources remain after preprint interest configuration')
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = None
        self.llm_blocked_reason = None
        self.model_requests = ModelRequests()
        self.library_dois, self.library_titles, self.library_titles_without_doi = set(), set(), set()

    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        collections = {c['key']: c['data'] for c in zot.everything(zot.collections())}
        items = zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint'))
        self.library_dois, self.library_titles, self.library_titles_without_doi = set(), set(), set()
        paths = {}

        def collection_path(key, visiting=frozenset()):
            if key in paths:
                return paths[key]
            if key in visiting:
                raise ValueError("Cycle in Zotero collection hierarchy")
            data = collections.get(key)
            if not data:
                logger.warning(f"Zotero collection {key} no longer exists; omitting path")
                return ''
            parent = data.get('parentCollection')
            prefix = collection_path(parent, visiting | {key}) if parent else ''
            paths[key] = '/'.join(filter(None, [prefix, data.get('name', '')]))
            return paths[key]

        corpus = []
        for item in items:
            data = item.get('data', {})
            title = data.get('title', '')
            doi = canonical_doi(data.get('DOI')) or canonical_doi(data.get('url'))
            if doi:
                self.library_dois.add(doi)
            if title_key(title):
                self.library_titles.add(title_key(title))
                if not doi:
                    self.library_titles_without_doi.add(title_key(title))
            abstract = clean_abstract(data.get('abstractNote'))
            if not abstract.strip():
                continue
            try:
                added = datetime.fromisoformat(data['dateAdded'].replace('Z', '+00:00')).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError):
                logger.warning(f"Skipping Zotero item with invalid dateAdded: {item.get('key', 'unknown')}")
                continue
            item_paths = [collection_path(key) for key in data.get('collections', [])]
            corpus.append(CorpusPaper(title=title, abstract=abstract, added_date=added,
                                      paths=[p for p in item_paths if p], doi=doi))
        logger.info(f"Fetched {len(corpus)} Zotero papers with abstracts")
        return corpus

    def filter_corpus(self, corpus: list[CorpusPaper]) -> list[CorpusPaper]:
        if self.include_path_patterns:
            corpus = [c for c in corpus if any(glob_match(path, pattern) for path in c.paths for pattern in self.include_path_patterns)]
        if self.ignore_path_patterns:
            corpus = [c for c in corpus if not any(glob_match(path, pattern) for path in c.paths for pattern in self.ignore_path_patterns)]
        logger.info(f"Selected {len(corpus)} Zotero papers for the interest profile")
        return corpus

    def _enrich(self, paper):
        if self.config.executor.get('fetch_full_text', True):
            retriever = self.retrievers.get(paper.source)
            if retriever:
                try:
                    paper = retriever.enrich(paper)
                except Exception as exc:
                    logger.warning(f"Full text unavailable for {paper.url}: {exc}")
        if self.openai_client:
            if not paper.tldr:
                paper.generate_tldr(self.openai_client, self.config.llm, self.model_requests)
            if paper.affiliations is None and paper.full_text and not self.config.llm.get('budget', {}).get('enabled', False):
                paper.generate_affiliations(self.openai_client, self.config.llm, self.model_requests)
        elif not paper.tldr:
            paper.tldr_status, paper.tldr_error = 'not_generated', None
            if getattr(self, 'llm_blocked_reason', None):
                paper.tldr = paper.abstract
                paper.tldr_status = 'fallback' if paper.abstract else 'not_generated'
                paper.tldr_error = 'budget_unavailable'
        return paper

    def run(self):
        output = self.config.get('output', {})
        email_cfg, rss_cfg = output.get('email', {}), output.get('rss', {})
        email_enabled, rss_enabled = email_cfg.get('enabled', True), rss_cfg.get('enabled', False)
        # Deliberately exclude recipient, model credentials, endpoints and custom YAML.
        logger.info(f'Effective configuration: sources={list(self.retrievers)}, '
                    f'llm_enabled={bool(self.config.llm.get("enabled", True))}, '
                    f'email_enabled={bool(email_enabled)}, rss_enabled={bool(rss_enabled)}')
        if not email_enabled and not rss_enabled:
            raise ValueError('Enable at least one output channel')
        state_cfg = self.config.get('state', {})
        state = State(state_cfg.get('path', 'data/recommendations.json'),
                      enabled=state_cfg.get('enabled', False) or rss_enabled,
                      retention_days=int(state_cfg.get('retention_days', 90)))
        maximum = int(self.config.executor.max_paper_num)
        workers = int(self.config.executor.get('enrichment_workers', 1))
        if maximum < 1 or not 1 <= workers <= 8:
            raise ValueError('max_paper_num must be positive and enrichment_workers must be between 1 and 8')
        quotas = quotas_for(self.config.executor)
        errors = []
        self.model_requests = ModelRequests()
        try:
            ranked = self._recommend(state, errors, maximum, workers)
        except Exception as exc:
            logger.error(f'Cannot prepare new recommendations: {exc}')
            errors.append(f'recommendations: {exc}')
            ranked = []
        finally:
            if self.openai_client is not None and hasattr(self.openai_client, 'close'):
                try:
                    self.openai_client.close()
                except Exception as exc:
                    logger.warning(f'Cannot close LLM client: {exc}')
                self.openai_client = None
        state.add(ranked)
        state.save()  # Preserve pending deliveries before contacting transports.
        if email_enabled:
            pending = pending_batch(state.pending('email'), quotas, maximum)
            if pending or (self.config.executor.send_empty and not errors):
                try:
                    send_email(self.config, render_email(pending))
                    logger.info(f'SMTP accepted {len(pending)} recommendations')
                    state.mark(pending, 'email')
                    state.save()
                except Exception as exc:
                    errors.append(f'email: {exc}')
        if rss_enabled:
            try:
                path, emitted = write_rss(state, rss_cfg)
                logger.info(f'RSS wrote {len(emitted)} items to {path}')
                state.mark(emitted, 'rss')
                state.save()
            except Exception as exc:
                errors.append(f'rss: {exc}')
        if errors:
            raise RuntimeError('Pipeline completed with failures: ' + '; '.join(errors))
        logger.info('Recommendation outputs completed')

    def _recommend(self, state, errors, maximum, workers):
        corpus = self.filter_corpus(self.fetch_zotero_corpus())
        if not corpus:
            raise ValueError('No Zotero papers with abstracts matched the configured interest profile')
        candidates = []
        for source, retriever in self.retrievers.items():
            try:
                candidates.extend(retriever.retrieve_papers())
                if getattr(retriever, 'failures', []):
                    errors.append(f"{source}: incomplete retrieval for {', '.join(retriever.failures)}")
            except Exception as exc:
                logger.error(f'Retrieval failed for {source}: {exc}')
                errors.append(f'{source}: {exc}')
        for paper in candidates:
            paper.abstract = clean_abstract(paper.abstract)
        unique = deduplicate(candidates)
        if self.config.executor.get('exclude_existing', True):
            unique = [p for p in unique if paper_doi(p) not in self.library_dois
                      and title_key(p.title) not in (self.library_titles_without_doi if paper_doi(p) else self.library_titles)]
        unique = [p for p in unique if not state.has(p)]
        logger.info(f'{len(candidates)} candidates, {len(unique)} new papers after deduplication')
        recover_abstracts(unique, self.config.get('abstracts', {}))
        ranked = self.reranker.rerank(unique, corpus) if unique else []
        minimum = float(self.config.executor.get('min_score', -10))
        ranked = [p for p in ranked if p.score >= minimum]
        quotas = quotas_for(self.config.executor)
        pending = state.pending('email') if self.config.get('output', {}).get('email', {}).get('enabled', True) else []
        ranked = select_papers(ranked, quotas, pending)[:maximum]
        if ranked and self.config.llm.get('enabled', True):
            try:
                if self.config.llm.get('budget', {}).get('enabled', False):
                    self.model_requests = prepare_budget(self.config.llm)
                    self.openai_client = OpenAI(api_key=self.config.llm.api.key, base_url=self.config.llm.api.base_url, max_retries=0)
                else:
                    self.openai_client = OpenAI(api_key=self.config.llm.api.key, base_url=self.config.llm.api.base_url)
            except BudgetUnavailable as exc:
                self.llm_blocked_reason = str(exc)
                logger.warning(f'LLM budget guard: {exc}')
        # Fork-based PDF extraction must run outside worker threads.
        if workers > 1 and not self.config.executor.get('fetch_full_text', True):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                ranked = list(pool.map(self._enrich, ranked))
        else:
            ranked = [self._enrich(p) for p in ranked]
        failed = sum(bool(p.tldr_error) for p in ranked)
        generated = sum(p.tldr_status == 'generated' for p in ranked)
        not_generated = sum(p.tldr_status == 'not_generated' and not p.tldr_error for p in ranked)
        if failed:
            logger.warning(f'AI summary degradation: {failed}/{len(ranked)} unavailable; '
                           f'{generated} generated; original abstracts retained when available. '
                           f'Model requests stopped={self.model_requests.unavailable}')
        else:
            logger.info(f'AI summaries: {generated} generated; {not_generated} not generated')
        return ranked
