import pytest

from tests.canned_responses import make_sample_paper
from zot2dailypaper.selection import is_cover_title, select_papers, pending_batch


@pytest.mark.parametrize('title', [
    'Outside Front Cover: Molecular materials', 'Inside Back Cover (Volume 10)',
    'Front Cover', 'Back Cover: Image', 'Inside\u00a0Front Cover：Materials',
    'Cover Image: An electrolyte', 'Cover Picture (Issue 10)', 'Frontispiece: Catalysis',
    'Front Cover – Molecular transport',
    'Inside Cover: Molecules', 'Supplementary Cover Art: Catalysts', 'Cover Feature: Material',
    'Cover Profile: Interview', 'Cover Art (Issue 3)',
])
def test_precise_publisher_cover_variants(title):
    assert is_cover_title(title)


@pytest.mark.parametrize('title', [
    'Front cover surfaces control transport', 'Covers and surfaces in molecular science',
    'Cover time for random walks', 'Cover: a theory of geometric sets',
    'Inside front cover materials studied by microscopy', 'A cover image classification method',
])
def test_research_about_covers_remains_eligible(title):
    assert not is_cover_title(title)


@pytest.mark.parametrize('source', ['journals','arxiv','biorxiv','medrxiv','researchsquare','openreview'])
def test_all_sources_and_pending_exclude_cover_before_quotas(source):
    cover = make_sample_paper(source=source, title='Outside Front Cover: Chemistry', score=100)
    journal = make_sample_paper(source='journals', title='Front cover surfaces control transport', score=3)
    preprint = make_sample_paper(source='arxiv', title='Research paper', score=2)
    random = make_sample_paper(source='journals', title='Another research paper', score=1)
    quotas = {'journals':1,'preprints':1,'random':1}
    selected = select_papers([cover,journal,preprint,random], quotas, pending=[cover])
    assert selected == [journal,preprint,random]
    assert pending_batch([cover,*selected],quotas,3) == selected
    assert select_papers([cover,journal],None) == [journal]
    assert pending_batch([cover,journal],None,1) == [journal]
