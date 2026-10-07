"""Explicit download of two verified official artifacts, never called by workflows."""
import argparse
import shutil
from pathlib import Path
from huggingface_hub import hf_hub_download
from zot2dailypaper.reranker.onnx_encoder import MODEL, REVISION, ARTIFACTS, file_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['onnx_fp32', 'onnx_int8'], required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    needed = 900_000_000 if args.backend == 'onnx_fp32' else 300_000_000
    if shutil.disk_usage(args.directory).free < needed + 1024**3:
        raise SystemExit('Insufficient free disk space (artifact size plus 1 GiB margin required)')
    name, graph_sha, data_sha = ARTIFACTS[args.backend]
    for filename, expected in ((name, graph_sha), (name+'_data', data_sha)):
        path = hf_hub_download(MODEL, 'onnx/'+filename, revision=REVISION, local_dir=args.directory)
        if file_digest(path) != expected:
            raise SystemExit('Official artifact checksum mismatch; do not enable this backend')
        print(f'Verified {filename}')
    print('Set onnx_directory to the onnx subdirectory. Private cache remains local-only.')


if __name__ == '__main__':
    main()
