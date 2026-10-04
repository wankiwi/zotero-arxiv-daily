from datetime import datetime, timedelta
import numpy as np
import pytest
from omegaconf import OmegaConf
from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.protocol import CorpusPaper
from zotero_arxiv_daily.reranker.experiments import settings, paper_text, unique_corpus, aggregate
from zotero_arxiv_daily.reranker.base import BaseReranker


@pytest.mark.parametrize('bad', [{'top_k':0}, {'top_k':True}, {'temperature':float('nan')},
    {'temperature':0}, {'temperature':True}, {'keyword_prompt':'unsupported'}, {'text_mode':'title'},
    {'deduplicate_corpus':1}, {'corpus_aggregation':'max'}, {'keyword_aggregation':'max'}])
def test_invalid_experiment_rejected(bad):
    with pytest.raises(ValueError):settings(OmegaConf.create({'reranker':{'experiments':bad}}))


def test_reliable_doi_dedup_keeps_newest_and_unknowns_separate():
    now=datetime(2026,1,1)
    papers=[CorpusPaper('same','a',now,[],doi='10.1234/A'),
            CorpusPaper('same','new',now+timedelta(days=1),[],doi='https://doi.org/10.1234/a'),
            CorpusPaper('same','b',now,[],doi='10.1234/b'),
            CorpusPaper('same','unknown',now,[]),CorpusPaper('same','unknown2',now,[])]
    result=unique_corpus(papers)
    assert len(result)==4 and result[0].abstract=='new'
    assert len(papers)==5


def test_top_k_and_softmax_are_bounded_and_keep_recency_prior():
    values=np.array([[.9,.2,-.3],[.2,.2,.2]])
    prior=np.array([.5,.3,.2])
    assert np.array_equal(aggregate(values,prior,'mean',2,.1),(values*prior).sum(1))
    assert aggregate(values,prior,'top_k',1,.1)==pytest.approx([.9,.2])
    assert aggregate(values,prior,'top_k',9,.1)==pytest.approx((values*prior).sum(1))
    soft=aggregate(values,prior,'softmax',2,1e-6)
    assert np.isfinite(soft).all() and soft==pytest.approx([.9,.2])
    assert aggregate(values,prior,'top_k',2,.1)[0]==pytest.approx((.9*.5+.2*.3)/.8)


def test_text_mode_missing_abstract_and_api_query_rejection():
    paper=make_sample_paper(title='Title',abstract=' Abstract ')
    assert paper_text(paper,'abstract')=='Abstract'
    assert paper_text(paper,'title_abstract')=='Title\n\nAbstract'
    paper.abstract=' '
    assert paper_text(paper,'title_abstract')=='Title'
    class Stub(BaseReranker):
        def get_similarity_score(self,a,b):return np.zeros((len(a),len(b)))
    ranker=Stub(None)
    with pytest.raises(ValueError,match='local encoder'):
        ranker.get_rank_similarity(['doc'],['keyword'],0,'query')
