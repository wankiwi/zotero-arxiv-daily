import numpy as np
import pytest
from omegaconf import OmegaConf
from zot2dailypaper.reranker.base import BaseReranker
from zot2dailypaper.state import paper_dict, load_paper
from zot2dailypaper.construct_email import render_email
from tests.canned_responses import make_sample_paper, make_sample_corpus


class Controlled(BaseReranker):
    def __init__(self, values, factor=0.8, keywords=()):
        super().__init__(OmegaConf.create({'reranker':{'missing_abstract_factor':factor, 'strategy':'legacy_mean'},
                                          'interest_profile':{'keywords':list(keywords)}}))
        self.values=np.asarray(values)
    def get_similarity_score(self,left,right):return self.values


@pytest.mark.parametrize('raw',[-10,-5,0,5,10])
@pytest.mark.parametrize('factor',[0,0.8,1])
def test_signed_adjustment_never_increases_negative_or_zero_scores(raw,factor):
    paper=make_sample_paper(abstract='  ')
    Controlled([[raw/10]],factor).rerank([paper],make_sample_corpus(1))
    assert paper.raw_score==5*raw+50
    expected = -10 if factor == 0 else (raw*factor if raw >= 0 else max(-10,raw/factor))
    assert paper.score==pytest.approx(5*expected+50)
    assert 0<=paper.score<=5*raw+50
    assert paper.missing_abstract_factor==factor
    assert paper.scoring_basis=='title only'


@pytest.mark.parametrize('factor',[-0.1,1.1,True,False,'0.8',None,float('nan'),float('inf')])
def test_invalid_factor_rejected(factor):
    with pytest.raises(ValueError,match='missing_abstract_factor'):
        Controlled([[0.8]],factor)


def test_abstract_not_llm_summary_controls_penalty_and_recovery_resets_it():
    paper=make_sample_paper(abstract='',tldr='A summary does not replace missing source text')
    ranker=Controlled([[0.8]])
    ranker.rerank([paper],make_sample_corpus(1))
    assert paper.score==pytest.approx(82)
    ranker.rerank([paper],make_sample_corpus(1))
    assert paper.score==pytest.approx(82)  # Never compound.
    paper.abstract='Complete recovered original abstract.'
    paper.tldr=None;paper.tldr_status='not_generated'
    ranker.rerank([paper],make_sample_corpus(1))
    assert paper.score==90 and paper.raw_score==90 and paper.missing_abstract_factor==1
    assert paper.scoring_basis=='abstract'


def test_fusion_penalized_once_and_keyword_only_empty_corpus_supported():
    paper=make_sample_paper(abstract='')
    Controlled([[0.2,0.8]],keywords=['water']).rerank([paper],make_sample_corpus(1))
    assert paper.raw_score==pytest.approx(78)
    assert paper.score==pytest.approx(72.4)
    assert paper.keyword_score==90 and paper.zotero_score==60
    Controlled([[0.8]],keywords=['water']).rerank([paper],[])
    assert paper.raw_score==90 and paper.score==pytest.approx(82)


def test_threshold_keeps_its_existing_scale_and_rank_order_changes():
    missing=make_sample_paper(title='Missing',abstract='')
    complete=make_sample_paper(title='Complete',abstract='Full source abstract',tldr=None)
    ranked=Controlled([[0.8],[0.7]]).rerank([missing,complete],make_sample_corpus(1))
    assert [p.title for p in ranked]==['Complete','Missing']
    assert [p.title for p in ranked if p.score>=82.5]==['Complete']
    assert len([p for p in ranked if p.score>=0])==2


def test_history_provenance_and_legacy_state_are_compatible():
    paper=make_sample_paper(abstract='')
    Controlled([[0.8]]).rerank([paper],make_sample_corpus(1))
    paper.selection_score=paper.score
    restored=load_paper(paper_dict(paper))
    assert restored.raw_score==90 and restored.missing_abstract_factor==0.8
    html=render_email([restored])
    assert 'Missing-abstract factor: 0.8; unadjusted relevance: 90.0/100' in html
    assert 'positive values multiply' in html and 'negative values divide' in html
    assert 'factor of zero yields 0/100' in html
    legacy=paper_dict(paper)
    for key in ('raw_score','selection_score','missing_abstract_factor'):legacy.pop(key)
    old=load_paper(legacy)
    assert old.raw_score is None and old.missing_abstract_factor==1
    old.score=-99
    Controlled([[0.8]]).rerank([old],make_sample_corpus(1))
    assert old.score==pytest.approx(82)


def test_tiny_cosine_roundoff_at_negative_boundary_is_normalized_before_discount():
    paper=make_sample_paper(abstract='')
    Controlled([[-1.0000001]]).rerank([paper],make_sample_corpus(1))
    assert paper.raw_score==0 and paper.score==0
