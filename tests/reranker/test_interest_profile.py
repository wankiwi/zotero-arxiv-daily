import random
import numpy as np
import pytest
from omegaconf import OmegaConf
from zot2dailypaper.interest_profile import interest_profile
from zot2dailypaper.reranker.base import BaseReranker
from zot2dailypaper.construct_email import render_email
from zot2dailypaper.selection import select_papers
from tests.canned_responses import make_sample_paper, make_sample_corpus


class Controlled(BaseReranker):
    def __init__(self, matrix, **profile):
        super().__init__(OmegaConf.create({'interest_profile': profile, 'reranker': {'strategy': 'legacy_mean'}}))
        self.matrix = np.array(matrix)

    def get_similarity_score(self, left, right):
        self.references = right
        return self.matrix


def test_blend_uses_equal_keyword_mean_and_recent_library_weights():
    ranker = Controlled([[0.2, 0.8, 0.4], [0.7, 0.1, 0.3]], keywords=['water', 'sampling'])
    result = ranker.rerank([make_sample_paper(title='A'), make_sample_paper(title='B')], make_sample_corpus(1))
    assert [p.title for p in result] == ['A', 'B']
    assert [p.score for p in result] == pytest.approx([72, 70])
    assert result[0].keyword_score == pytest.approx(80)
    assert result[0].zotero_score == pytest.approx(60)
    html = render_email(result)
    assert 'keywords (60%) and your library (40%)' in html
    assert html.count('Scored using abstract similarity to keywords (60%) and your library (40%).') == 1
    assert html.index('Scored using') < html.index('1. A')


def test_keyword_normalization_does_not_repeat_weight():
    ranker = Controlled([[0.5]], keywords=[' Proton-transfer ', 'proton transfer', 'PROTON–TRANSFER', ''])
    result = ranker.rerank([make_sample_paper(abstract='')], [])
    assert ranker.references == ['proton transfer']
    assert result[0].raw_score == 75
    assert result[0].score == 70
    assert result[0].scoring_basis == 'title only'
    assert result[0].interest_keyword_weight == 1
    assert result[0].zotero_score is None


@pytest.mark.parametrize('kwargs', [
    {'keywords': 'water'}, {'keywords': [None]}, {'keyword_weight': -1},
    {'zotero_weight': True}, {'keyword_weight': float('nan')},
    {'keyword_weight': float('inf')}, {'keyword_weight': '0.6'},
    {'keyword_weight': 0, 'zotero_weight': 0}, {'keywords': ['x'*251]},
])
def test_invalid_profile_rejected(kwargs):
    with pytest.raises(ValueError):
        interest_profile({'interest_profile': kwargs})


def test_weight_normalization_empty_and_disabled_components():
    profile = interest_profile({'interest_profile': {'keywords':['water'], 'keyword_weight':6, 'zotero_weight':4}})
    assert profile.effective_weights(True) == (0.6, 0.4)
    assert profile.effective_weights(False) == (1, 0)
    assert interest_profile(None).effective_weights(True) == (0, 1)
    with pytest.raises(ValueError, match='No available'):
        interest_profile(None).effective_weights(False)
    with pytest.raises(ValueError, match='No available'):
        interest_profile({'interest_profile': {'zotero_weight':0}}).effective_weights(True)
    ranker = Controlled([[0.2]], keywords=['ignored'], keyword_weight=0)
    assert ranker.rerank([make_sample_paper()], make_sample_corpus(1))[0].score == 60
    assert ranker.references == ['Abstract for corpus paper 0.']


def test_legacy_score_exact_and_time_decay_preserved():
    matrix = np.array([[0.1, 0.7, 0.4], [0.9, 0.3, 0.1]])
    decay = 1 / (1 + np.log10(np.arange(3) + 1))
    expected = (matrix * (decay / decay.sum())).sum(axis=1)*10
    papers = [make_sample_paper(title=str(i)) for i in range(2)]
    result = Controlled(matrix).rerank(papers, make_sample_corpus(3))
    assert np.array_equal(np.array([p.score for p in sorted(result,key=lambda p:p.title)]), expected*5+50)


@pytest.mark.parametrize('matrix', [[[2]], [[float('nan')]], [[0.2,0.3]]])
def test_invalid_similarity_fails(matrix):
    with pytest.raises(ValueError):
        Controlled(matrix, keywords=['water']).rerank([make_sample_paper()], [])


def test_fused_ranking_retains_quota_then_disjoint_random_selection():
    papers = [make_sample_paper(title=str(i), source='journals' if i < 30 else 'arxiv') for i in range(55)]
    ranked = Controlled(np.linspace(0,1,55).reshape(-1,1),keywords=['water']).rerank(papers, [])
    selected = select_papers(ranked, {'journals':25,'preprints':15,'random':5}, rng=random.Random(7))
    assert len(selected) == len({p.title for p in selected}) == 45
    assert [p.title for p in selected if p.recommendation_group=='journals'] == [str(i) for i in range(29,4,-1)]
    assert len([p for p in selected if p.recommendation_group=='random']) == 5


def test_keyword_only_executor_still_filters_seen_duplicates_and_library(config, monkeypatch):
    from types import SimpleNamespace
    from zot2dailypaper.executor import Executor
    from zot2dailypaper.identity import title_key
    config.interest_profile.keywords = ['water']
    config.llm.enabled = False
    config.executor.quotas = None
    config.abstracts.enabled = False
    executor = Executor.__new__(Executor)
    executor.config = config
    executor.library_dois = set()
    executor.library_titles = {title_key('Already in library')}
    executor.library_titles_without_doi = executor.library_titles
    executor.filter_corpus = lambda items: items
    calls = []
    executor.fetch_zotero_corpus = lambda: calls.append('read') or []
    executor._enrich = lambda paper: paper
    fresh = make_sample_paper(title='Fresh', url='https://example.org/fresh')
    executor.retrievers = {'mock': SimpleNamespace(retrieve_papers=lambda: [fresh, fresh,
        make_sample_paper(title='Seen',url='https://example.org/seen'),
        make_sample_paper(title='Already in library',url='https://example.org/library')])}
    executor.reranker = Controlled([[0.8]], keywords=['water'])
    state = SimpleNamespace(has=lambda p: p.title=='Seen', pending=lambda channel: [])
    result = executor._recommend(state, [], 45, 1)
    assert calls == ['read']
    assert [p.title for p in result] == ['Fresh']
    executor.fetch_zotero_corpus = lambda: (_ for _ in ()).throw(RuntimeError('Zotero unavailable'))
    with pytest.raises(RuntimeError, match='Zotero unavailable'):
        executor._recommend(state, [], 45, 1)



def test_profile_survives_workflow_without_broadening_source_filters(tmp_path):
    import shutil
    from pathlib import Path
    from hydra import compose, initialize_config_dir
    from scripts.prepare_workflow import prepare
    from zot2dailypaper.preprint_interests import categories_for
    root = Path(__file__).resolve().parents[2]
    shutil.copytree(root / 'config', tmp_path / 'config')
    patch = OmegaConf.load(root / 'config/keyword_interests.yaml').interest_profile
    prepare(tmp_path, {'GITHUB_EVENT_NAME':'schedule',
                      'CUSTOM_CONFIG':OmegaConf.to_yaml(OmegaConf.create({'interest_profile':patch}))})
    with initialize_config_dir(config_dir=str(tmp_path / 'config'), version_base=None):
        config = compose(config_name='runtime')
    assert interest_profile(config).effective_weights(True) == (0.6, 0.4)
    assert len(interest_profile(config).keywords) == 6
    assert categories_for(config, 'arxiv') == ['physics.chem-ph','physics.comp-ph','cond-mat.mtrl-sci','cond-mat.soft','cs.LG','cs.AI']
    assert list(config.preprint_interests.openreview.keywords) == ['atomistic','molecular','chemistry','materials','molecular dynamics']
    assert dict(config.executor.quotas) == {'journals':25,'preprints':15,'random':5}


def test_weight_edits_recompute_scores_and_state_keeps_provenance():
    from zot2dailypaper.state import paper_dict, load_paper
    ranker = Controlled([[0.2, 0.8]], keywords=['water'])
    paper = make_sample_paper()
    ranker.rerank([paper], make_sample_corpus(1))
    assert paper.score == pytest.approx(78)
    ranker.config.interest_profile.keyword_weight = 0.4
    ranker.config.interest_profile.zotero_weight = 0.6
    ranker.rerank([paper], make_sample_corpus(1))
    assert paper.score == pytest.approx(72)
    restored = load_paper(paper_dict(paper))
    assert restored.interest_keyword_weight == 0.4
    assert restored.keyword_score == 90
    legacy = paper_dict(paper)
    for field in ('interest_keyword_weight','interest_zotero_weight','keyword_score','zotero_score'):
        legacy.pop(field)
    assert load_paper(legacy).interest_keyword_weight == 0
