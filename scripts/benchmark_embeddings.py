"""Offline synthetic backend comparison. No downloads, SMTP, Zotero or LLM access."""
import os
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['CUDA_VISIBLE_DEVICES'] = ''
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from time import perf_counter
import numpy as np
from omegaconf import OmegaConf
from zot2dailypaper.reranker.local import LocalReranker
from zot2dailypaper.reranker.onnx_encoder import MODEL, REVISION


def worker(backend, directory):
    texts = [f'Study {i}: '+topic+' '+('We compare molecular simulations and measured structure to assess mechanisms and uncertainty. '*8)
             for i, topic in enumerate(['Molecular dynamics of interfacial water.', 'Electric double layer response.',
             'Machine learning force fields for chemical reactions.', 'Proton transfer in hydrogen bond networks.',
             'Enhanced sampling of free energy barriers.', 'Charged droplet stability.']*8)]
    with tempfile.TemporaryDirectory(prefix='embedding-benchmark-') as cache:
        cfg = OmegaConf.create({'executor': {'debug': False},
            'interest_profile': {'keywords': ['water'], 'keyword_weight': .4, 'zotero_weight': .6},
            'reranker': {'local': {'model': MODEL, 'revision': REVISION, 'backend': backend,
            'onnx_directory': directory, 'onnx_fallback': False, 'cpu_dtype': 'float32', 'cpu_threads': 4,
            'cache_dir': cache, 'encode_kwargs': {'task': 'retrieval', 'prompt_name': 'document', 'batch_size': 16}}}})
        ranker = LocalReranker(cfg)
        started = perf_counter()
        score = ranker.get_similarity_score(texts[:36], texts[36:])
        cold = perf_counter()-started
        started = perf_counter()
        warm = ranker.get_similarity_score(texts[:36], texts[36:])
        memory = perf_counter()-started
        ranker._cache.memory.clear()
        started = perf_counter()
        disk = ranker.get_similarity_score(texts[:36], texts[36:])
        disk_time = perf_counter()-started
        return {'backend': ranker._backend, 'cold_seconds': cold, 'warm_memory_seconds': memory,
                'warm_disk_seconds': disk_time, 'cache_exact': bool(np.array_equal(score, warm) and np.array_equal(score, disk)),
                'scores': score.tolist()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--onnx-directory', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', choices=['torch', 'onnx_fp32', 'onnx_int8'])
    args = parser.parse_args()
    if args.worker:
        args.output.write_text(json.dumps(worker(args.worker, args.onnx_directory)))
        return
    reports = {}
    with tempfile.TemporaryDirectory(prefix='backend-comparison-') as td:
        reference = None
        for backend in ('torch', 'onnx_fp32', 'onnx_int8'):
            path = Path(td)/(backend+'.json')
            subprocess.run([sys.executable, __file__, '--worker', backend, '--onnx-directory', args.onnx_directory,
                            '--output', str(path)], check=True, timeout=300)
            report = json.loads(path.read_text())
            score = np.array(report.pop('scores'))
            if reference is None:
                reference = score
            else:
                delta = np.abs(score-reference)
                report.update(max_cosine_difference=float(delta.max()), mean_cosine_difference=float(delta.mean()),
                              top10_overlap=len(set(np.argsort(score.mean(1))[-10:]) & set(np.argsort(reference.mean(1))[-10:]))/10)
            reports[backend] = report
    result = {'revision': REVISION, 'synthetic_texts': 48, 'cpu_threads': 4, 'separate_worker_processes': True,
              'quality_labels': False, 'results': reports}
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
