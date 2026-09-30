from abc import ABC, abstractmethod
from omegaconf import DictConfig
from ..protocol import Paper, RawPaperItem
from tqdm import tqdm
from typing import Type
from time import sleep
from loguru import logger
from ..preprint_interests import validate_interests, matches_keywords


class BaseRetriever(ABC):
    name: str
    def __init__(self, config:DictConfig):
        self.config = config
        self.retriever_config = getattr(config.source,self.name)
        self.interests = validate_interests(config).get(self.name, {})

    @abstractmethod
    def _retrieve_raw_papers(self) -> list[RawPaperItem]:
        pass

    @abstractmethod
    def convert_to_paper(self, raw_paper:RawPaperItem) -> Paper | None:
        pass

    def enrich(self, paper: Paper) -> Paper:
        return paper

    def retrieve_papers(self) -> list[Paper]:
        if not self.interests.get('enabled', True):
            return []
        raw_papers = self._retrieve_raw_papers()
        logger.info("Processing papers...")
        papers = []
        for raw_paper in tqdm(raw_papers, total=len(raw_papers), desc="Converting papers"):
            try:
                paper = self.convert_to_paper(raw_paper)
            except Exception as exc:
                logger.warning(f"Skipping paper {getattr(raw_paper, 'title', raw_paper)}: {exc}")
                continue
            if paper is not None and matches_keywords(paper, self.interests):
                papers.append(paper)
            delay = self.retriever_config.get("conversion_delay", 0)
            if delay:
                sleep(delay)
        return papers

registered_retrievers = {}

def register_retriever(name:str):
    def decorator(cls):
        registered_retrievers[name] = cls
        cls.name = name
        return cls
    return decorator

def get_retriever_cls(name:str) -> Type[BaseRetriever]:
    if name not in registered_retrievers:
        raise ValueError(f"Retriever {name} not found")
    return registered_retrievers[name]
