"""Opt-in, local-file-only official Jina ONNX CPU encoder."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import numpy as np

MODEL = 'jinaai/jina-embeddings-v5-text-nano-retrieval'
REVISION = 'ac5d898c8d382b17167c33e5c8af644a3519b47d'
ARTIFACTS = {
    'onnx_fp32': ('model.onnx', 'ad8244eea593ae8488cfd821a7d573aa51a350bc817702d6da99f3bccab24f49',
                 '7eeca7c5e63a9047991df400cdd57c3cf05fabc0168700a4ecee42d1eba8093d'),
    'onnx_int8': ('model_quantized.onnx', 'ac93a7417c216e5076e37da2b3599f7ef16513934098a477680440c09f735a08',
                 'ee7870eb143a7353be08b33f79992a51de3e32b41f684ccd82953a710c2f2f9c'),
}


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class OnnxEncoder:
    def __init__(self, model, revision, backend, directory, threads):
        import onnxruntime as ort
        import torch
        from transformers import AutoTokenizer
        if model != MODEL or revision != REVISION:
            raise ValueError('ONNX requires the verified Jina model and immutable revision')
        if not directory:
            raise ValueError('ONNX requires an explicitly provisioned local artifact directory')
        name, graph_sha, data_sha = ARTIFACTS[backend]
        graph = Path(directory).expanduser() / name
        for path, expected in ((graph, graph_sha), (Path(str(graph) + '_data'), data_sha)):
            if file_digest(path) != expected:
                raise ValueError('Official ONNX artifact checksum mismatch')
        self.artifact_identity = {'graph_sha256': graph_sha, 'data_sha256': data_sha, 'runtime': ort.__version__}
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(graph), sess_options=options, providers=['CPUExecutionProvider'])
        self.tokenizer = AutoTokenizer.from_pretrained(model, revision=revision, local_files_only=True)
        self.prompts = {'query': 'Query: ', 'document': 'Document: '}
        self.default_prompt_name = None
        self.max_seq_length = 8192
        self.device = torch.device('cpu')
        self.weight = torch.zeros(1, dtype=torch.float32)
        self.revision = revision

    def __getitem__(self, index):
        return SimpleNamespace(auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash=self.revision)))

    def parameters(self):
        return iter([self.weight])

    def float(self):
        return self

    def get_sentence_embedding_dimension(self):
        return 768

    def encode(self, texts, **kwargs):
        allowed = {'task', 'prompt_name', 'prompt', 'batch_size', 'normalize_embeddings', 'truncate_dim',
                   'convert_to_numpy', 'convert_to_tensor', 'show_progress_bar', 'precision', 'output_value'}
        if set(kwargs) - allowed or kwargs.get('task', 'retrieval') != 'retrieval':
            raise ValueError('Unsupported ONNX encoding options; use PyTorch for this configuration')
        prompt = kwargs.get('prompt')
        if prompt is None:
            name = kwargs.get('prompt_name')
            if name is not None and name not in self.prompts:
                raise ValueError('Unknown ONNX prompt')
            prompt = self.prompts.get(name, '')
        batch_size = kwargs.get('batch_size', 32)
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError('batch_size must be positive')
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]), reverse=True)
        output = np.empty((len(texts), 768), dtype=np.float32)
        for start in range(0, len(order), batch_size):
            indices = order[start:start+batch_size]
            tokens = self.tokenizer([(prompt+t).strip() for t in (texts[i] for i in indices)], padding=True,
                                    truncation=True, max_length=self.max_seq_length, return_tensors='np')
            inputs = {v.name: np.asarray(tokens[v.name], dtype=np.int64) for v in self.session.get_inputs()}
            batch = self.session.run(['sentence_embedding'], inputs)[0]
            if batch.shape != (len(indices), 768) or not np.isfinite(batch).all():
                raise ValueError('ONNX returned invalid sentence embeddings')
            output[indices] = batch
        dimension = kwargs.get('truncate_dim')
        if dimension is not None:
            if type(dimension) is not int or not 1 <= dimension <= 768:
                raise ValueError('truncate_dim must be between 1 and 768')
            output = output[:, :dimension]
        if kwargs.get('normalize_embeddings', False):
            output /= np.linalg.norm(output, axis=1, keepdims=True)
        return output

    def similarity(self, left, right):
        import torch
        left = left / np.linalg.norm(left, axis=1, keepdims=True)
        right = right / np.linalg.norm(right, axis=1, keepdims=True)
        return torch.from_numpy(left @ right.T)
