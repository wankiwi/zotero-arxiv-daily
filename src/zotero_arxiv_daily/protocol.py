from dataclasses import dataclass, field
from typing import Optional, TypeVar
from datetime import datetime
from functools import lru_cache
import re
import tiktoken
from openai import OpenAI
from loguru import logger
from .llm import model_unavailable
import json
RawPaperItem = TypeVar('RawPaperItem')

@lru_cache(maxsize=1)
def _tokenizer():
    try:
        return tiktoken.encoding_for_model("gpt-4o")
    except Exception as exc:
        logger.warning(f"Tokenizer unavailable; using conservative UTF-8 byte limit: {exc}")
        return None


def truncate_prompt(prompt: str, limit: int) -> str:
    enc = _tokenizer()
    if enc is None:
        return prompt.encode('utf-8')[:limit].decode('utf-8', errors='ignore')
    return enc.decode(enc.encode(prompt, disallowed_special=())[:limit])


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
    doi: Optional[str] = None
    journal: Optional[str] = None
    issns: list[str] = field(default_factory=list)
    published: Optional[datetime] = None
    scoring_basis: str = "abstract"
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


    def _generate_tldr_with_llm(self, openai_client:OpenAI,llm_params:dict) -> str:
        lang = llm_params.get('language', 'English')
        prompt = f"Given the following information of a paper, generate a one-sentence TLDR summary in {lang}:\n\n"
        if self.title:
            prompt += f"Title:\n {self.title}\n\n"

        if self.abstract:
            prompt += f"Abstract: {self.abstract}\n\n"

        if self.full_text:
            prompt += f"Preview of main content:\n {self.full_text}\n\n"

        if not self.full_text and not self.abstract:
            logger.warning(f"Neither full text nor abstract is provided for {self.url}")
            return "Failed to generate TLDR. Neither full text nor abstract is provided"
        
        # use gpt-4o tokenizer for estimation
        prompt = truncate_prompt(prompt, 4000)
        
        response = openai_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": f"You are an assistant who perfectly summarizes scientific paper, and gives the core idea of the paper to the user. Your answer should be in {lang}.",
                },
                {"role": "user", "content": prompt},
            ],
            **llm_params.get('generation_kwargs', {})
        )
        tldr = response.choices[0].message.content
        if not isinstance(tldr, str) or not tldr.strip():
            raise ValueError('Empty summary response')
        return tldr
    
    def generate_tldr(self, openai_client:OpenAI,llm_params:dict, requests=None) -> str:
        self.tldr_error = None
        if not self.abstract and not self.full_text:
            self.tldr, self.tldr_status = '', 'not_generated'
            return self.tldr
        try:
            operation = lambda: self._generate_tldr_with_llm(openai_client,llm_params)
            tldr = requests.call(operation) if requests is not None else operation()
            self.tldr = tldr
            self.tldr_status = 'generated'
            return tldr
        except Exception as e:
            self.tldr_error = 'model_unavailable' if model_unavailable(e) else 'request_failed'
            # Do not log provider response bodies, account IDs or request payloads.
            if requests is None:
                logger.warning(f'AI summary unavailable ({self.tldr_error}); using original abstract when available')
            tldr = self.abstract
            self.tldr = tldr
            self.tldr_status = 'fallback' if self.abstract else 'not_generated'
            return tldr

    def _generate_affiliations_with_llm(self, openai_client:OpenAI,llm_params:dict) -> Optional[list[str]]:
        if self.full_text is not None:
            prompt = f"Given the beginning of a paper, extract the affiliations of the authors in a python list format, which is sorted by the author order. If there is no affiliation found, return an empty list '[]':\n\n{self.full_text}"
            # use gpt-4o tokenizer for estimation
            prompt = truncate_prompt(prompt, 2000)
            affiliations = openai_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "You are an assistant who perfectly extracts affiliations of authors from a paper. You should return a python list of affiliations sorted by the author order, like [\"TsingHua University\",\"Peking University\"]. If an affiliation is consisted of multi-level affiliations, like 'Department of Computer Science, TsingHua University', you should return the top-level affiliation 'TsingHua University' only. Do not contain duplicated affiliations. If there is no affiliation found, you should return an empty list [ ]. You should only return the final list of affiliations, and do not return any intermediate results.",
                    },
                    {"role": "user", "content": prompt},
                ],
                **llm_params.get('generation_kwargs', {})
            )
            affiliations = affiliations.choices[0].message.content

            affiliations = re.search(r'\[.*?\]', affiliations, flags=re.DOTALL).group(0)
            affiliations = json.loads(affiliations)
            if not isinstance(affiliations, list):
                raise ValueError("Affiliations must be a JSON list")
            affiliations = list(dict.fromkeys(str(a) for a in affiliations))

            return affiliations
    
    def generate_affiliations(self, openai_client:OpenAI,llm_params:dict, requests=None) -> Optional[list[str]]:
        try:
            operation = lambda: self._generate_affiliations_with_llm(openai_client,llm_params)
            affiliations = requests.call(operation) if requests is not None else operation()
            self.affiliations = affiliations
            return affiliations
        except Exception as e:
            if requests is None or not model_unavailable(e):
                logger.warning('AI affiliation extraction unavailable; preserving unknown affiliation')
            self.affiliations = None
            return None
@dataclass
class CorpusPaper:
    title: str
    abstract: str
    added_date: datetime
    paths: list[str]
    doi: Optional[str] = None
