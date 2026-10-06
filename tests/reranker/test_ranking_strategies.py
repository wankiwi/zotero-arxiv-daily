"""Strategy, support and provenance tests use synthetic cosine matrices only."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from omegaconf import OmegaConf

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.construct_email import render_email, email_plain_text
from zotero_arxiv_daily.protocol import CorpusPaper
from zotero_arxiv_daily.reranker.base import BaseReranker
from zotero_arxiv_daily.reranker.strategies import ProfileSettings, directional_scores, strategy_settings
from zotero_arxiv_daily.state import paper_dict, load_paper


def corpus(size):
    return [CorpusPaper(f'Private reference {i}', f'r{i}', datetime(2026, 1, 1) - timedelta(days=i), [])
            for i in range(size)]


class MatrixReranker(BaseReranker):
    def __init__(self, matrix, affinity=(), *, keywords=('water', 'sampling'), weights=(.4, .6), **reranker):
        super().__init__(OmegaConf.create({'interest_profile': {'keywords': list(keywords),
                       'keyword_weight': weights[0], 'zotero_weight': weights[1]}, 'reranker': reranker}))
        self.matrix, self.affinity = np.asarray(matrix), np.asarray(affinity)
        self.calls = []

    def get_similarity_score(self, left, right):
        self.calls.append((list(left), list(right)))
        if left and left[0].startswith('r'):
            return self.affinity[[int(text[1:]) for text in left]]
        return self.matrix


def test_default_takes_joint_max_instead_of_mean_and_legacy_rolls_back_exactly():
    matrix = np.array([[.8, 0., .9], [.5, .7, .7]])
    papers = [make_sample_paper(title='A', abstract='c0'), make_sample_paper(title='B', abstract='c1')]
    ranker = MatrixReranker(matrix, [[.9, .1]])
    ranked = ranker.rerank(papers, corpus(1))
    assert [p.title for p in ranked] == ['A', 'B']
    assert [p.score for p in ranked] == pytest.approx([92, 79])
    assert ranked[0].matched_interest == 'sampling'
    assert ranked[0].keyword_score == 95 and ranked[0].zotero_score == 90
    assert ranked[0].interest_direction_reliability == 0  # Only one reference: fallback.
    legacy = MatrixReranker(matrix, strategy='legacy_mean').rerank(papers, corpus(1))
    expected = (.4 * matrix[:, 1:].mean(1) + .6 * matrix[:, 0]) * 10 * 5 + 50
    assert np.array_equal([p.score for p in sorted(legacy, key=lambda p: p.title)], expected)
    assert all(p.matched_interest is None and p.ranking_strategy == 'legacy_mean' for p in legacy)


def test_eight_configured_directions_equal_independent_raw_centroid_calculation():
    keywords = np.eye(8)
    references = np.repeat(keywords, 5, axis=0)
    references[np.arange(40), (np.arange(40) // 5 + 1) % 8] += .1
    references /= np.linalg.norm(references, axis=1, keepdims=True)
    candidates = np.vstack([keywords[7], (keywords[0] + keywords[7]) / np.sqrt(2)])
    affinity, candidate_corpus = references @ keywords.T, candidates @ references.T
    candidate_keywords = candidates @ keywords.T
    options = ProfileSettings()
    score, _, _, winner, support = directional_scores(corpus(40), affinity, candidate_corpus,
                                                     candidate_keywords, (.4, .6), options)
    decay = 1 / (1 + np.log10(np.arange(40) + 1))
    decay /= decay.sum()
    global_centroid = decay @ references
    raw_profiles = []
    for direction in range(8):
        indices = list(range(direction * 5, direction * 5 + 5))
        weights = decay[indices] / decay[indices].sum()
        effective = 1 / (weights ** 2).sum()
        reliability = effective / (effective + 5)
        raw_profiles.append(reliability * (weights @ references[indices]) + (1 - reliability) * global_centroid)
        assert support[direction]['effective_support'] == pytest.approx(effective)
        assert support[direction]['reliability'] == pytest.approx(reliability)
    joint = .4 * candidate_keywords + .6 * (candidates @ np.array(raw_profiles).T)
    assert score == pytest.approx(joint.max(1))
    assert np.array_equal(winner, joint.argmax(1))
    assert winner[0] == 7 and all(s['profile_n'] == 5 for s in support)
    assert all(np.linalg.norm(p) < 1 for p in raw_profiles)  # Shrunk centroids must not be renormalized.


def test_direction_limit_newest_canonical_doi_and_unknowns_remain_distinct():
    refs = corpus(30)
    refs[0].doi, refs[1].doi = '10.21203/rs.123/v2', 'https://doi.org/10.21203/rs.123/v1'
    affinity = np.full((30, 1), .5)
    affinity[1] = .99  # Old duplicate cannot displace the newest reference.
    affinity[29] = .29  # Below assignment threshold.
    candidate_corpus = np.linspace(.1, .9, 30).reshape(1, -1)
    score, _, library, _, support = directional_scores(refs, affinity, candidate_corpus, np.array([[.6]]),
                                                      (0., 1.), ProfileSettings(shrinkage=0))
    decay = 1 / (1 + np.log10(np.arange(30) + 1))
    selected = [0] + list(range(2, 25))  # Same affinity: stable newest-first ties, maximum 24.
    expected = candidate_corpus[:, selected] @ (decay[selected] / decay[selected].sum())
    assert library == pytest.approx(expected) and score == pytest.approx(expected)
    assert support[0]['assigned_unique_eligible'] == 28 and support[0]['profile_n'] == 24


@pytest.mark.parametrize('options', [ProfileSettings(), ProfileSettings(min_references_per_direction=1,
                                                                        min_effective_support=10)])
def test_low_count_or_low_effective_support_falls_back_without_inventing_profile(options):
    values = np.array([[.2, .7, .8, .9]])
    _, _, library, _, support = directional_scores(corpus(4), np.ones((4, 1)), values, np.array([[.5]]),
                                                  (.4, .6), options)
    decay = 1 / (1 + np.log10(np.arange(4) + 1))
    assert library == pytest.approx((values * (decay / decay.sum())).sum(1))
    assert support[0]['fallback_global'] and support[0]['reliability'] == 0


@pytest.mark.parametrize('weights,reference_count,keywords,matrix,affinity,expected', [
    ((4, 6), 1, ('water', 'sampling'), [[.2, .8, .1]], [[.9, .1]], 72),
    ((.4, .6), 0, ('water', 'sampling'), [[.8, .1]], [], 90),
    ((1, 0), 1, ('water', 'sampling'), [[.8, .1]], [], 90),
    ((0, 1), 1, ('water', 'sampling'), [[.2, .8, .1]], [[.9, .1]], 60),
    ((.4, .6), 1, (), [[.2]], [], 60),
])
def test_available_components_dynamic_weights_and_empty_fallbacks(weights, reference_count, keywords,
                                                                  matrix, affinity, expected):
    ranker = MatrixReranker(matrix, affinity, weights=weights, keywords=keywords)
    paper = ranker.rerank([make_sample_paper(abstract='c0')], corpus(reference_count))[0]
    assert paper.score == pytest.approx(expected)
    assert paper.interest_keyword_weight + paper.interest_zotero_weight == 1
    if not weights[0] or not keywords:
        assert paper.keyword_score is None
    if not keywords:
        assert paper.matched_interest is None and ranker.direction_profile_support == []


def test_library_only_profiles_keep_direction_anchors_and_joint_winner_is_not_keyword_max():
    affinity = np.array([[.9, .1]] * 5 + [[.1, .9]] * 5)
    matrix = [[.1] * 5 + [.9] * 5 + [.99, .2]]
    ranker = MatrixReranker(matrix, affinity, weights=(0, 1))
    paper = ranker.rerank([make_sample_paper(abstract='c0')], corpus(10))[0]
    assert paper.matched_interest == 'sampling' and paper.keyword_score is None
    assert paper.interest_direction_support == 5 and paper.interest_direction_reliability > 0


def test_stable_candidate_and_direction_ties_and_bounded_affinity_requests(monkeypatch):
    monkeypatch.setattr('zotero_arxiv_daily.reranker.base.AFFINITY_ELEMENTS', 4)
    ranker = MatrixReranker([[.2] * 5 + [.8, .8]] * 2, [[.5, .5]] * 5)
    papers = [make_sample_paper(title=name, abstract=f'c{i}') for i, name in enumerate(['B', 'A'])]
    ranked = ranker.rerank(papers, list(reversed(corpus(5))))
    assert [p.title for p in ranked] == ['B', 'A']
    assert all(p.matched_interest == 'water' for p in ranked)
    assert [len(left) for left, _ in ranker.calls[1:]] == [2, 2, 1]
    assert ranker.direction_profile_support[0]['profile_n'] == 5


@pytest.mark.parametrize('bad', ['bad', None, 1])
def test_unknown_strategy_rejected(bad):
    with pytest.raises(ValueError, match='strategy'):
        MatrixReranker([[0]], strategy=bad)


@pytest.mark.parametrize('bad', [None, [], {'unknown': 1}, {'assignment_min_cosine': 1.1},
    {'assignment_min_cosine': True}, {'min_effective_support': 0}, {'min_effective_support': float('nan')},
    {'shrinkage': -1}, {'shrinkage': '5'}, {'shrinkage': float('inf')},
    {'max_references_per_direction': True}, {'max_references_per_direction': 2.5},
    {'min_references_per_direction': 0}, {'max_references_per_direction': 4}])
def test_invalid_profile_parameters_rejected(bad):
    with pytest.raises(ValueError):
        MatrixReranker([[0]], multi_interest_profile=bad)


@pytest.mark.parametrize('ablation', [{'keyword_prompt': 'query'}, {'text_mode': 'title_abstract'},
    {'deduplicate_corpus': True}, {'corpus_aggregation': 'top_k'}, {'keyword_aggregation': 'softmax'}, {'top_k': 2}])
def test_profile_rejects_conflicting_ablations_and_legacy_accepts_them(ablation):
    with pytest.raises(ValueError, match='legacy_mean'):
        MatrixReranker([[0]], experiments=ablation)
    MatrixReranker([[0]], experiments=ablation, strategy='legacy_mean')
    assert strategy_settings(None)[0] == 'multi_interest_profile'


@pytest.mark.parametrize('affinity', [[[float('nan'), .5]], [[1.1, .5]], [[.5]]])
def test_invalid_extra_affinity_fails_closed(affinity):
    with pytest.raises(ValueError):
        MatrixReranker([[.2, .8, .1]], affinity).rerank([make_sample_paper(abstract='c0')], corpus(1))


@pytest.mark.parametrize('raw', [-1., -.5, 0., .5, 1.])
@pytest.mark.parametrize('factor', [0., .8, 1.])
def test_profile_penalty_applied_once_with_boundary_and_state_provenance(raw, factor):
    ranker = MatrixReranker([[raw, raw]], keywords=('water', 'sampling'), missing_abstract_factor=factor)
    paper = make_sample_paper(title='c0', abstract='')
    ranker.rerank([paper], [])
    expected = -1 if factor == 0 else (raw * factor if raw >= 0 else max(-1, raw / factor))
    assert paper.score == pytest.approx(50 + 50 * expected)
    assert paper.raw_score == pytest.approx(50 + 50 * raw)
    original = paper.score
    ranker.rerank([paper], [])
    assert paper.score == original
    restored = load_paper(paper_dict(paper))
    assert restored.ranking_strategy == 'multi_interest_profile' and restored.matched_interest == 'water'
    historical = paper_dict(paper)
    for field in ('ranking_strategy', 'matched_interest', 'interest_direction_support', 'interest_direction_reliability'):
        historical.pop(field)
    assert load_paper(historical).ranking_strategy is None


def test_email_explains_profile_once_escapes_interest_and_preserves_mixed_pending_strategy():
    ranker = MatrixReranker([[.2, .8, .1]], [[.9, .1]], keywords=('<water>', 'sampling'))
    paper = ranker.rerank([make_sample_paper(title='Candidate', abstract='c0')], corpus(1))[0]
    html = render_email([paper])
    plain = email_plain_text(html)
    assert plain.count('Scored using') == 1 and 'strongest joint keyword/library direction' in plain
    assert 'Matched interest: &lt;water&gt;' in html and 'library support limited; global mean used' in plain
    assert 'Private reference' not in html and plain.index('Multi-interest') < plain.index('Candidate')
    historical = make_sample_paper(title='Old', ranking_strategy=None)
    plain = email_plain_text(render_email([paper, historical], ranking_strategy='multi_interest_profile'))
    assert plain.count('Scored using') == 1
    assert 'profiles for 1 papers and legacy mean aggregation for 1 papers' in plain
    assert 'stored scores retain their original strategy' in plain
    assert 'Multi-interest profile ranking' in render_email([], ranking_strategy='multi_interest_profile')


def test_api_affinity_uses_already_encoded_texts_without_extra_provider_requests(config, monkeypatch):
    from zotero_arxiv_daily.reranker.api import ApiReranker
    requested = []
    def create(*, input, model):
        requested.append(input)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[1., i + 1.]) for i in range(len(input))])
    monkeypatch.setattr('zotero_arxiv_daily.reranker.api.OpenAI',
                        lambda **_: SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    config.reranker.strategy = 'multi_interest_profile'
    config.interest_profile.keywords = ['water', 'sampling']
    ranker = ApiReranker(config)
    papers = [make_sample_paper(abstract='c0')]
    ranker.rerank(papers, corpus(10))
    assert len(requested) == 1 and len(requested[0]) == 13
    ranker.rerank(papers, corpus(10))
    assert len(requested) == 1
