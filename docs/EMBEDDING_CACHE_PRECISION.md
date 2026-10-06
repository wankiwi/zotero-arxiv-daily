# Cross-run embedding cache precision

## Observed cause

Run [37239640651](https://github.com/wankiwi/zotero-arxiv-daily/actions/runs/37239640651)
saved 3,102 vectors after PyTorch CPU inference in FP32. Run
[37396029673](https://github.com/wankiwi/zotero-arxiv-daily/actions/runs/37396029673)
authenticated and restored those 3,102 vectors, then loaded the same configured
model on a runner whose automatic CPU precision was BF16:

| Latest run stage | Memory hits | Disk hits | Misses | Encoding time |
| --- | ---: | ---: | ---: | ---: |
| First ranking | 0 | 0 | 3,097 | 648.98 s |
| Second ranking | 3,086 | 0 | 11 | 2.94 s |

The cache namespace includes the **actual parameter dtype**, as well as resolved
model revision, backend, artifacts, prompts, encode options, dimension, sequence
length, device, threads and library versions. FP32 and BF16 therefore necessarily
have different namespaces. Successful decryption restores files; it does not make
vectors from a different inference precision interchangeable. The dtype difference
alone is sufficient to explain the misses. The old logs did not record namespace
fingerprints, so they cannot rule out additional identity differences.

## Repair

For `cpu_dtype: auto`, the first model load checks whether the current BF16 pool
contains a validated vector for any requested text. If it does, native BF16 remains
selected. Otherwise, if an **exact FP32 namespace** contains a validated matching
vector, the actual model is upcast to FP32 before lookup and inference. Missing
texts are encoded in that same FP32 precision. This retains the existing namespace
and previously computed scores without treating BF16 as FP32.

The choice stays fixed for that encoder load, including document/query roles.
AVX2-only CPUs still use FP32; there is no BF16 emulation. Explicit `native` and
`float32` settings keep their existing meaning. There is no configuration, model,
ranking-weight, prompt, normalization, encryption or serialized-vector change.
Namespace schema 2 remains compatible with already stored FP32 caches.

The existing checksum, finite/nonzero vector, dimension, float32 output, allocation
and authenticated-package validations apply to both the selection probe and final
lookup. A mismatched revision, actual backend, prompt or encode option still misses;
a corrupt FP32 entry cannot select automatic precision. An unresolved revision
still disables disk persistence. New logs include the namespace fingerprint and
actual dtype/backend, without texts, vector keys, vectors, prompts or secret values.

## Regression coverage

`tests/reranker/test_local_cache_process.py` uses actual SentenceTransformer,
Transformers and PyTorch loads of a tiny, locally generated BF16 BERT model. Each
process gets a different physical model/cache directory. Synthetic immutable
snapshot identifiers are derived from model files and verified against their
contents; the second revision has different real weights. Network connections are
forbidden, and no production model weights, private corpus, keys or APIs are used.

A fixed synthetic AES-GCM test key transports the cache to fresh processes. Tests
cover cold misses, native BF16 disk hits, FP32 disk hits on a BF16-capable runner, unchanged score digests,
encoding only a new text, and invalidation for actual revision, effective prompt,
named prompt, explicit native dtype and normalization changes. Requested ONNX load
failure must reuse the *actual* PyTorch namespace. Existing backend-isolation and
ONNX inference-fallback tests continue to cover distinct effective backend pools.

Unit tests additionally isolate actual backend identity even when the remaining
metadata is identical, and exercise native-pool preference, precision consistency
across document/query roles and corrupt-cache rejection. No production workflow
dispatch or mail delivery is needed to run this coverage.
