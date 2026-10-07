import json
from datetime import datetime, timezone
import pytest
from omegaconf import OmegaConf
from zot2dailypaper.scores import minimum_score, SCORE_SCHEMA
from zot2dailypaper.state import State, paper_dict, load_paper
from zot2dailypaper.construct_email import render_email, email_plain_text
from tests.canned_responses import make_sample_paper


@pytest.mark.parametrize('old,new',[(-10,0),(-5,25),(0,50),(6.5,82.5),(10,100)])
def test_legacy_threshold_retains_original_eligibility(old,new):
    assert minimum_score({'min_score':old})==new
    assert minimum_score({'min_score':new,'min_score_scale':'0_100'})==new
    old_scores=[-10,-5,0,6,6.5,8,10]
    assert [s for s in old_scores if s>=old]==[s for s in old_scores if 5*s+50>=minimum_score({'min_score':old})]


@pytest.mark.parametrize('config',[{'min_score':True},{'min_score':float('nan')},{'min_score':11},
    {'min_score':-1,'min_score_scale':'0_100'},{'min_score':101,'min_score_scale':'0_100'},
    {'min_score':1,'min_score_scale':'unknown'}])
def test_threshold_rejects_invalid_or_ambiguous_scale(config):
    with pytest.raises(ValueError):minimum_score(config)


def test_legacy_pending_and_delivered_state_migrate_once_without_resending(tmp_path):
    old=paper_dict(make_sample_paper(score=6.4,raw_score=8,selection_score=-5,keyword_score=2,zotero_score=.5))
    old.pop('score_schema')
    other=dict(old,title='Delivered',doi='10.9999/delivered',url='https://example.org/delivered')
    timestamp=datetime.now(timezone.utc).isoformat()
    path=tmp_path/'state.json'
    path.write_text(json.dumps({'version':1,'records':{
        'old-key':{'added':timestamp,'paper':old,'channels':{'email':False}},
        'delivered-key':{'added':timestamp,'paper':other,'channels':{'email':True}}}}))
    original=path.read_bytes();state=State(path)
    assert path.read_bytes()==original  # Loading never writes remote/local history implicitly.
    assert len(state.records)==2 and len(state.pending('email'))==1
    paper=state.pending('email')[0]
    assert (paper.score,paper.raw_score,paper.selection_score,paper.keyword_score,paper.zotero_score)==(82,90,25,60,52.5)
    assert paper.score_schema==SCORE_SCHEMA
    assert all(r['added']==timestamp for r in state.records.values())
    state.save();again=State(path)
    assert again.pending('email')[0].score==82 and len(again.pending('email'))==1
    html=render_email([paper]);plain=email_plain_text(html)
    assert '82.0/100' in html and '82.0/100' in plain
    assert 'not a probability or accuracy estimate' in plain
    assert load_paper(paper_dict(paper)).score==82


def test_unknown_score_schema_preserves_file_and_fails_closed(tmp_path):
    data=paper_dict(make_sample_paper(score=70));data['score_schema']='future'
    with pytest.raises(ValueError,match='schema'):load_paper(data)


def test_quota_sum_is_authoritative_over_legacy_maximum():
    from zot2dailypaper.selection import quotas_for
    config=OmegaConf.create({'quotas':{'journals':25,'preprints':20,'random':5},'max_paper_num':45})
    assert sum(quotas_for(config).values())==50
