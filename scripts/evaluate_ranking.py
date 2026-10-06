"""Private-local, offline ranking ablations and blinded human review. No delivery."""
import os
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['CUDA_VISIBLE_DEVICES'] = ''
import argparse
import copy
import csv
from dataclasses import fields
from datetime import datetime
import json
from pathlib import Path
import random
from time import perf_counter
import numpy as np
from omegaconf import OmegaConf
from zotero_arxiv_daily.identity import paper_id
from zotero_arxiv_daily.scores import SCORE_SCHEMA
from zotero_arxiv_daily.protocol import Paper, CorpusPaper
from zotero_arxiv_daily.reranker.local import LocalReranker
from zotero_arxiv_daily.reranker.onnx_encoder import MODEL, REVISION

VARIANTS = {
    'baseline': {}, 'keyword_query': {'keyword_prompt':'query'},
    'title_abstract': {'text_mode':'title_abstract'}, 'doi_dedup': {'deduplicate_corpus':True},
    'corpus_top_k': {'corpus_aggregation':'top_k'}, 'corpus_softmax': {'corpus_aggregation':'softmax'},
    'keyword_top_k': {'keyword_aggregation':'top_k'}, 'keyword_softmax': {'keyword_aggregation':'softmax'},
}


def private_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation avoids clobbering a human's existing labels/results.
    with path.open('x', encoding='utf-8', newline='') as out:
        os.chmod(path, 0o600)
        out.write(text)


def load_sample(path):
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    allowed = {f.name for f in fields(Paper)}
    papers = [Paper(**{k:v for k,v in item.items() if k in allowed}) for item in raw['candidates']]
    if not 200 <= len(papers) <= 300:
        raise ValueError('Blind evaluation requires a fixed 200-300 candidate sample')
    identities = [paper_id(p) for p in papers]
    if len(set(identities)) != len(papers):
        raise ValueError('Candidates must have unique stable identities')
    corpus = [CorpusPaper(title=p['title'], abstract=p.get('abstract',''),
        added_date=datetime.fromisoformat(p['added_date']), paths=[], doi=p.get('doi')) for p in raw['corpus']]
    if not corpus or not raw.get('keywords'):
        raise ValueError('Supply corpus and keywords; never infer private library contents')
    return raw, papers, corpus


def safe_cell(value):
    value = str(value or '')
    return "'"+value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value


def prepare(sample, output, seed):
    raw, papers, _ = load_sample(sample)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any((output/name).exists() for name in ('review.csv','private-index.json')):
        raise FileExistsError('Review already exists; use a new private directory')
    order = list(range(len(papers)));random.Random(seed).shuffle(order)
    rows, index = [], {}
    for number, position in enumerate(order, 1):
        paper = papers[position]; review_id=f'R{number:03d}'
        index[review_id] = paper_id(paper)
        rows.append([review_id, safe_cell(paper.title), safe_cell(paper.abstract), safe_cell(paper.url), '', ''])
    import io
    buffer=io.StringIO(newline='');writer=csv.writer(buffer)
    writer.writerow(['review_id','title','abstract','url','relevance_0_to_3','notes'])
    writer.writerows(rows)
    private_write(output/'review.csv', buffer.getvalue())
    private_write(output/'private-index.json', json.dumps(index, ensure_ascii=False, indent=2))
    print(f'Prepared {len(papers)} blinded rows locally; scores and methods are hidden')


def compare(sample, output):
    raw,papers,corpus=load_sample(sample)
    if (output/'rankings.json').exists():raise FileExistsError('Comparison already exists')
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    cfg=OmegaConf.create({'executor':{'debug':False},
        'interest_profile':{'keywords':raw['keywords'],'keyword_weight':.4,'zotero_weight':.6},
        'reranker':{'missing_abstract_factor':.8,'experiments':{},'local':{
            'model':MODEL,'revision':REVISION,'backend':'torch','cpu_dtype':'float32','cpu_threads':4,
            'cache_dir':str(output/'vectors'),'encode_kwargs':{'task':'retrieval','prompt_name':'document','batch_size':16}}}})
    cfg.reranker.strategy='legacy_mean' # This script compares the original mean-based ablations.
    ranker=LocalReranker(cfg);reports={};rankings={};baseline=None
    # One untimed baseline prepares the model/vectors; report this cold cost separately.
    started=perf_counter();ranker.rerank(copy.deepcopy(papers),corpus);cold=perf_counter()-started
    for name,options in VARIANTS.items():
        cfg.reranker.experiments=OmegaConf.create(options)
        started=perf_counter();ranked=ranker.rerank(copy.deepcopy(papers),corpus);elapsed=perf_counter()-started
        scores={paper_id(p):p.score for p in ranked};order=list(scores)
        rankings[name]={'order':order,'scores':scores}
        if baseline is None:baseline=order;baseline_scores=scores
        positions={key:i for i,key in enumerate(order)}
        reports[name]={'seconds_after_baseline_warmup':elapsed,
            'top40_overlap':len(set(baseline[:40]) & set(order[:40]))/40,
            'mean_absolute_rank_shift':float(np.mean([abs(i-positions[key]) for i,key in enumerate(baseline)])),
            'mean_absolute_score_delta':float(np.mean([abs(scores[key]-baseline_scores[key]) for key in baseline]))}
    groups={paper_id(p):'journals' if p.publication_kind=='journal' or p.source=='journals' else 'preprints' for p in papers}
    report={'score_schema':SCORE_SCHEMA,'model_revision':REVISION,'candidate_count':len(papers),'corpus_count':len(corpus),
        'corpus_role':raw.get('corpus_role','provided local corpus'), 'cold_baseline_seconds':cold,
        'weights':{'keyword':.4,'zotero':.6},'quality_labels':False,
        'warning':'Rank changes and timings do not establish recommendation quality; no winner selected.',
        'variants':reports}
    private_write(output/'rankings.json',json.dumps({'variants':rankings,'groups':groups},indent=2))
    private_write(output/'comparison.json',json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


def evaluate(output):
    index=json.loads((output/'private-index.json').read_text())
    rankings=json.loads((output/'rankings.json').read_text())
    labels={};seen=set()
    with (output/'review.csv').open(newline='',encoding='utf-8-sig') as source:
        for row in csv.DictReader(source):
            review_id=row['review_id'];value=row['relevance_0_to_3'].strip()
            if review_id not in index or review_id in seen:raise ValueError('Unknown or duplicate review ID')
            seen.add(review_id)
            if value not in ('0','1','2','3'):raise ValueError('Complete all 0-3 labels before comparing methods')
            labels[index[review_id]]=int(value)
    if seen!=set(index):raise ValueError('Missing review rows')
    result={}
    for name,variant in rankings['variants'].items():
        if set(variant['order']) != set(labels):raise ValueError('Review and ranking samples differ')
        result[name]={}
        for group,quota in [('journals',25),('preprints',15)]:
            order=[key for key in variant['order'] if rankings['groups'][key]==group]
            k=min(quota,len(order))
            if not k:result[name][group]={'available':0};continue
            gains=np.array([labels[key] for key in order])
            discount=np.log2(np.arange(k)+2)
            dcg=float(((2.**gains[:k]-1)/discount).sum())
            ideal=float(((2.**np.sort(gains)[::-1][:k]-1)/discount).sum())
            result[name][group]={'evaluated':k,'quota':quota,'precision':float((gains[:k]>=2).mean()),
                                  'ndcg':dcg/ideal if ideal else None}
    private_write(output/'labelled-results.json',json.dumps(result,indent=2))
    print('Saved local labelled metrics; random five are excluded from relevance-rank evaluation')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','compare','evaluate'])
    parser.add_argument('--sample',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seed',type=int,default=20261004)
    args=parser.parse_args()
    if args.command!='evaluate' and not args.sample:parser.error('--sample is required')
    if args.command=='prepare':prepare(args.sample,args.output,args.seed)
    elif args.command=='compare':compare(args.sample,args.output)
    else:evaluate(args.output)


if __name__=='__main__':main()
