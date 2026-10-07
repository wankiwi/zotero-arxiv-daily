"""Offline, real-model subprocess fixture. Only synthetic texts and a fixed test key."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
MODEL = 'synthetic/tiny-cache-model'
KEY = b'\x01' * 32


def forbid_network(*args, **kwargs):
    raise AssertionError('Offline model-load test attempted a network connection')


def offline(root):
    os.environ.update({
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
        'HF_HOME': str(root / 'hf'), 'HF_TOKEN_PATH': str(root / 'absent-test-token'),
        'HF_TOKEN': '', 'HUGGING_FACE_HUB_TOKEN': '', 'HF_HUB_DISABLE_TELEMETRY': '1',
        'TOKENIZERS_PARALLELISM': 'false',
    })
    socket.socket.connect = forbid_network
    socket.socket.connect_ex = forbid_network
    socket.socket.sendto = forbid_network


def snapshot_revision(root):
    content = hashlib.sha1()
    for path in sorted(root.rglob('*')):
        if path.is_file():
            payload = path.read_bytes()
            if path.suffix == '.json':
                metadata = json.loads(payload)
                if isinstance(metadata, dict):
                    metadata.pop('_commit_hash', None)
                payload = json.dumps(metadata, sort_keys=True).encode()
            content.update(path.relative_to(root).as_posix().encode())
            content.update(payload)
    return content.hexdigest()


def build(root):
    import torch
    from sentence_transformers import SentenceTransformer
    from tokenizers import Tokenizer, models, pre_tokenizers, processors
    from transformers import BertConfig, BertModel, PreTrainedTokenizerFast

    torch.set_num_threads(1)
    snapshots = []
    for seed in (17, 23):
        torch.manual_seed(seed)
        base = root / f'base-{seed}'
        config = BertConfig(vocab_size=16, hidden_size=8, num_hidden_layers=1,
                            num_attention_heads=2, intermediate_size=16, max_position_embeddings=32,
                            hidden_dropout_prob=0, attention_probs_dropout_prob=0, dtype='bfloat16')
        BertModel(config).to(torch.bfloat16).save_pretrained(base)
        words = ['[PAD]', '[UNK]', '[CLS]', '[SEP]', '[MASK]', 'Document', 'Query', ':',
                 'alpha', 'beta', 'gamma', 'delta', 'water', 'new', 'text', '!']
        tokenizer = Tokenizer(models.WordPiece({word: i for i, word in enumerate(words)}, unk_token='[UNK]'))
        tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer.post_processor = processors.TemplateProcessing(
            single='[CLS] $A [SEP]', pair='[CLS] $A [SEP] $B:1 [SEP]:1',
            special_tokens=[('[CLS]', 2), ('[SEP]', 3)])
        PreTrainedTokenizerFast(tokenizer_object=tokenizer, unk_token='[UNK]', pad_token='[PAD]',
                               cls_token='[CLS]', sep_token='[SEP]', mask_token='[MASK]',
                               model_max_length=32).save_pretrained(base)
        encoder = SentenceTransformer(str(base), device='cpu', local_files_only=True, trust_remote_code=False,
                                      model_kwargs={'dtype': torch.bfloat16},
                                      prompts={'document': 'Document: ', 'query': 'Query: '})
        stage = root / f'stage-{seed}'
        encoder.save(str(stage))
        revision = snapshot_revision(stage)
        snapshot = root / 'models--synthetic--tiny-cache-model' / 'snapshots' / revision
        shutil.copytree(stage, snapshot)
        # A synthetic immutable snapshot's metadata, consumed by the real HF
        # config loader. The two revisions have different real model weights.
        config_path = snapshot / 'config.json'
        metadata = json.loads(config_path.read_text(encoding='utf-8'))
        metadata['_commit_hash'] = revision
        config_path.write_text(json.dumps(metadata), encoding='utf-8')
        snapshots.append({'path': str(snapshot), 'revision': revision})
    return {'snapshots': snapshots}


def evaluate(options):
    import torch
    import sentence_transformers
    from omegaconf import OmegaConf
    from zot2dailypaper.reranker import local
    from zot2dailypaper.reranker.encrypted_cache import seal, unseal

    cache = Path(options['cache'])
    os.environ['PRIVATE_EMBEDDING_CACHE_DIR'] = str(cache)
    revision = options['snapshot']['revision']
    source = Path(options['snapshot']['path'])
    assert snapshot_revision(source) == revision
    snapshot = Path(options['root']) / 'model-cache' / 'models--synthetic--tiny-cache-model' / 'snapshots' / revision
    shutil.copytree(source, snapshot)
    if options.get('restore'):
        restored = unseal(Path(options['restore']).read_bytes(), KEY, cache)
    else:
        restored = 0
    actual_loader = sentence_transformers.SentenceTransformer
    encoded = []
    loaded = []

    def load(model, **kwargs):
        assert model == MODEL and kwargs['revision'] == options['snapshot']['revision']
        encoder = actual_loader(str(snapshot), device='cpu', local_files_only=True, trust_remote_code=False,
                                model_kwargs={'dtype': torch.bfloat16})
        resolved = encoder[0].auto_model.config._commit_hash
        assert resolved == options['snapshot']['revision']
        loaded.append({'revision': resolved, 'dtype': str(next(encoder.parameters()).dtype)})
        if options.get('prompt_definition'):
            encoder.prompts['document'] = options['prompt_definition']
        actual_encode = encoder.encode

        def encode(texts, **kwargs):
            encoded.append(len(texts))
            return actual_encode(texts, **kwargs)

        encoder.encode = encode
        return encoder

    sentence_transformers.SentenceTransformer = load
    local.cpu_has_bf16 = lambda: options.get('bf16', False)
    kwargs = {'prompt_name': options.get('prompt_name', 'document'), 'batch_size': 2,
              'normalize_embeddings': options.get('normalize', True)}
    config = OmegaConf.create({'executor': {'debug': True}, 'reranker': {'local': {
        'model': MODEL, 'revision': options['snapshot']['revision'], 'cpu_dtype': options.get('dtype', 'auto'),
        'cpu_threads': 1, 'backend': options.get('backend', 'torch'), 'onnx_fallback': True,
        'onnx_directory': None, 'encode_kwargs': kwargs,
    }}})
    ranker = local.LocalReranker(config)
    candidates = ['alpha beta', 'gamma delta']
    if options.get('new_text'):
        candidates.append('new water text')
    scores = ranker.get_similarity_score(candidates, ['alpha gamma'])
    result = {'restored': restored, 'loaded': loaded, 'dtype': str(next(ranker._encoder.parameters()).dtype),
              'backend': ranker._backend, 'namespace': ranker._cache_namespace,
              'stats': ranker._cache.stats, 'encoded': encoded,
              'score_digest': hashlib.sha256(scores.tobytes()).hexdigest()}
    if options.get('seal'):
        Path(options['seal']).write_bytes(seal(cache, KEY))
    return result


if __name__ == '__main__':
    options = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    offline(Path(options['root']))
    result = build(Path(options['root'])) if options['action'] == 'build' else evaluate(options)
    print(json.dumps(result, sort_keys=True))
