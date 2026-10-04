from .scores import SCORE_SCHEMA
from dataclasses import dataclass, field
from typing import Optional, TypeVar
from datetime import datetime
from openai import OpenAI
from loguru import logger
from .llm import model_unavailable
from .budget import BudgetUnavailable, BudgetRequests, PROMPT_BYTES, SYSTEM_BYTES, MAX_OUTPUT_TOKENS, audit_response, no_retry_client
RawPaperItem = TypeVar('RawPaperItem')

@dataclass
class Paper:
    source: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    pdf_url: Optional[str] = None
    full_text: Optional[str] = None
    tldr: Optional[str] = None
    affiliations: Optional[list[str]] = None
    score: Optional[float] = None
    score_schema: str = SCORE_SCHEMA
    raw_score: Optional[float] = None
    selection_score: Optional[float] = None
    missing_abstract_factor: float = 1.0
    doi: Optional[str] = None
    related_dois: list[str] = field(default_factory=list)
    journal: Optional[str] = None
    issns: list[str] = field(default_factory=list)
    published: Optional[datetime] = None
    publication_kind: Optional[str] = None
    publication_status: Optional[str] = None
    publication_venue: Optional[str] = None
    recommendation_group: Optional[str] = None
    abstract_source: Optional[str] = None
    abstract_source_url: Optional[str] = None
    abstract_recovery_status: Optional[str] = None
    abstract_recovery_attempts: list[dict[str, str]] = field(default_factory=list)
    subject_match_reason: Optional[str] = None
    scoring_basis: str = "abstract"
    keyword_score: Optional[float] = None
    zotero_score: Optional[float] = None
    interest_keyword_weight: float = 0.0
    interest_zotero_weight: float = 1.0
    summary_input_source: Optional[str] = None
    summary_input_fallback: Optional[str] = None
    tldr_status: Optional[str] = None
    tldr_error: Optional[str] = None

    @property
    def summary_label(self):
        if self.tldr_status == 'generated':
            return 'AI summary'
        if self.tldr_status == 'fallback':
            return 'Original abstract (AI summary unavailable)'
        if self.tldr_status == 'legacy' or (self.tldr_status is None and self.tldr):
            return 'Summary (legacy; origin unknown)'
        return 'Original abstract (AI summary not generated)' if self.abstract else 'AI summary not generated'

    @property
    def summary_text(self):
        return self.tldr or self.abstract or 'No abstract available'


    def _generate_tldr_with_llm(self, openai_client:OpenAI,llm_params:dict, requests=None) -> str:
        if not isinstance(requests, BudgetRequests) or llm_params.get("budget", {}).get("enabled", True) is not True:
            raise BudgetUnavailable("A mandatory daily budget reservation is required")
        lang = llm_params.get('language', 'Chinese')
        mode = llm_params.get('input_mode', 'abstract')
        if mode not in ('abstract', 'full_text'):
            raise ValueError('llm.input_mode must be abstract or full_text')
        self.summary_input_source, self.summary_input_fallback = 'abstract', None
        title = self.title.encode('utf-8')[:128].decode('utf-8', errors='ignore')
        full_prompt = f'Title: {title}\nFull text:\n{self.full_text or ""}'
        if mode == 'full_text':
            if not self.full_text:
                self.summary_input_fallback = 'full_text_unavailable'
            elif len(full_prompt.encode('utf-8')) > PROMPT_BYTES:
                self.summary_input_fallback = 'full_text_exceeds_budget_input_bound'
            else:
                self.summary_input_source = 'full_text'
        if self.summary_input_source == 'full_text':
            prompt = full_prompt  # Entire retrieved text, never a prefix labeled as full text.
        else:
            if self.summary_input_fallback:
                logger.warning(f'Summary input fallback to abstract: {self.summary_input_fallback}')
            if not self.abstract:
                raise ValueError('No usable summary input after full-text fallback')
            prompt = f'Title: {title}\nAbstract: {self.abstract}'
            prompt = prompt.encode('utf-8')[:PROMPT_BYTES].decode('utf-8', errors='ignore')
        system = f'Return exactly one sentence in {lang} summarizing the scientific evidence. No heading, list, or invented claims.'
        kwargs = dict(llm_params.get('generation_kwargs', {}))
        if len(system.encode('utf-8')) > SYSTEM_BYTES:
            raise BudgetUnavailable('System prompt exceeds verified input bound')
        kwargs = {'model': kwargs['model'], 'max_tokens': MAX_OUTPUT_TOKENS, 'n': 1,
                  'extra_body': {'enable_thinking': False}}
        
        def operation():
            request_client = no_retry_client(openai_client)
            response = request_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": system,
                    },
                    {"role": "user", "content": prompt},
                ],
                **kwargs
            )
            audit_response(response, kwargs['model'])
            if getattr(response.choices[0], 'finish_reason', None) != 'stop':
                raise ValueError('Summary did not finish normally; retain original abstract')
            tldr = response.choices[0].message.content
            if not isinstance(tldr, str) or not tldr.strip():
                raise ValueError('Empty summary response')
            return tldr.strip()
        return requests.call(operation)

    def generate_tldr(self, openai_client:OpenAI,llm_params:dict, requests=None) -> str:
        self.tldr_error = None
        if not self.abstract and (llm_params.get('input_mode', 'abstract') != 'full_text' or not self.full_text):
            self.tldr, self.tldr_status = '', 'not_generated'
            return self.tldr
        try:
            tldr = self._generate_tldr_with_llm(openai_client,llm_params,requests)
            self.tldr = tldr
            self.tldr_status = 'generated'
            return tldr.strip()
        except Exception as e:
            self.tldr_error = 'budget_unavailable' if isinstance(e, BudgetUnavailable) else 'model_unavailable' if model_unavailable(e) else 'request_failed'
            # Do not log provider response bodies, account IDs or request payloads.
            if requests is None:
                logger.warning(f'AI summary unavailable ({self.tldr_error}); using original abstract when available')
            tldr = self.abstract
            self.tldr = tldr
            self.tldr_status = 'fallback' if self.abstract else 'not_generated'
            return tldr.strip()

    def generate_affiliations(self, openai_client:OpenAI, llm_params:dict, requests=None) -> Optional[list[str]]:
        """Compatibility API: retain metadata; optional paid extraction is disabled."""
        return self.affiliations

@dataclass
class CorpusPaper:
    title: str
    abstract: str
    added_date: datetime
    paths: list[str]
    doi: Optional[str] = None
