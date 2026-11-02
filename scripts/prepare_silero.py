#!/usr/bin/env python3
"""Copy a user-selected ONNX model or one from the installed silero-vad wheel.
Does not import/execute silero-vad or download model code. Verify publisher and
license before installing that external wheel. Records SHA-256 for provenance.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path)
parser.add_argument('--destination', type=Path, default=Path('data/models/silero_vad.onnx'))
parser.add_argument('--sha256', help='Expected SHA-256, supplied independently by the operator')
args = parser.parse_args()
source, version = args.source, 'user-supplied'
if source is None:
    try:
        dist = importlib.metadata.distribution('silero-vad')
        source = Path(dist.locate_file('silero_vad/data/silero_vad.onnx'))
        version = dist.version
    except importlib.metadata.PackageNotFoundError:
        raise SystemExit('Install a reviewed silero-vad wheel with --no-deps, or pass --source MODEL.onnx.')
if not source.is_file():
    raise SystemExit('Model not found. Pass an explicit --source path to a compatible model.')
digest = hashlib.sha256(source.read_bytes()).hexdigest()
if args.sha256 and args.sha256.lower() != digest:
    raise SystemExit('SHA-256 verification failed; model was not copied.')
args.destination.parent.mkdir(parents=True, exist_ok=True)
if source.resolve() != args.destination.resolve():
    shutil.copyfile(source, args.destination)
manifest = {'source_package_version': version, 'sha256': digest, 'model': str(args.destination),
            'expected_sample_rate': 16000, 'expected_inputs': ['input', 'state', 'sr'],
            'live_inference_verified': False}
args.destination.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2))
print(json.dumps(manifest, indent=2))
