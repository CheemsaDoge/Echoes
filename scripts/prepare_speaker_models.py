#!/usr/bin/env python3
"""Download, verify, and benchmark 16kHz speaker embedding ONNX models.
Generates data/models/speakers/manifest.json with model metadata, dimension,
SHA-256 hash, and 1-second extraction latency.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import urllib.request
import numpy as np
import sherpa_onnx

SPEAKER_MODELS = [
    {
        "id": "campplus_cn",
        "name": "Alibaba 3D-Speaker Cam++ (Chinese)",
        "file": "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
        "description": "State-of-the-art fast 192-dim Cam++ embedding extractor from 3D-Speaker",
    },
    {
        "id": "eres2net_cn",
        "name": "Alibaba 3D-Speaker ERes2Net (Chinese)",
        "file": "3dspeaker_speech_eres2net_sv_zh-cn_16k-common.onnx",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_sv_zh-cn_16k-common.onnx",
        "description": "High-accuracy 192-dim ERes2Net embedding extractor from 3D-Speaker",
    },
    {
        "id": "wespeaker_resnet34",
        "name": "WeSpeaker CNCeleb ResNet34",
        "file": "wespeaker_zh_cnceleb_resnet34.onnx",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_zh_cnceleb_resnet34.onnx",
        "description": "256-dim ResNet34 embedding extractor trained on CNCeleb dataset",
    },
    {
        "id": "nemo_speakernet",
        "name": "NeMo SpeakerNet (English/Multilingual)",
        "file": "nemo_en_speakerverification_speakernet.onnx",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_speakerverification_speakernet.onnx",
        "description": "192-dim lightweight SpeakerNet model from NVIDIA NeMo",
    },
]

TEST_AUDIOS = [
    ("fangjun-sr-1.wav", "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/fangjun-sr-1.wav"),
    ("fangjun-test-sr-1.wav", "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/fangjun-test-sr-1.wav"),
    ("leijun-sr-1.wav", "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/leijun-sr-1.wav"),
    ("leijun-test-sr-1.wav", "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/leijun-test-sr-1.wav"),
    ("liudehua-sr-1.wav", "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/liudehua-sr-1.wav"),
    ("liudehua-test-sr-1.wav", "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/liudehua-test-sr-1.wav"),
]

def download_file(url: str, dest: Path) -> None:
    if dest.is_file() and dest.stat().st_size > 1024:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    temp_dest = dest.with_suffix(".download.tmp")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(temp_dest, "wb") as out:
            shutil.copyfileobj(resp, out)
        if temp_dest.stat().st_size > 1024:
            temp_dest.replace(dest)
    finally:
        if temp_dest.exists():
            temp_dest.unlink()

def benchmark_speaker_model(model_path: Path) -> dict:
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(model_path),
        num_threads=1,
        provider="cpu"
    )
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    dim = extractor.dim

    # Benchmark on 1-second dummy audio
    dummy_1s = np.zeros(16000, dtype=np.float32)
    t0 = time.perf_counter()
    stream = extractor.create_stream()
    stream.accept_waveform(16000, dummy_1s)
    stream.input_finished()
    emb = extractor.compute(stream)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    return {
        "dim": dim,
        "latency_1s_ms": round(elapsed_ms, 2),
        "verified": len(emb) == dim,
    }

def main() -> None:
    parser = argparse.ArgumentParser(description="Download and verify speaker embedding models")
    parser.add_argument("--models-dir", type=Path, default=Path("data/models/speakers"))
    args = parser.parse_args()

    models_dir = args.models_dir
    models_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries = []

    for entry in SPEAKER_MODELS:
        file_path = models_dir / entry["file"]
        print(f"Preparing {entry['name']} -> {file_path}")
        download_file(entry["url"], file_path)

        data = file_path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)

        bench_info = benchmark_speaker_model(file_path)
        print(f"  Verified! Dim: {bench_info['dim']}, Latency for 1s audio: {bench_info['latency_1s_ms']} ms")

        record = {
            "id": entry["id"],
            "name": entry["name"],
            "description": entry["description"],
            "file": entry["file"],
            "relative_path": str(file_path),
            "size_bytes": size_bytes,
            "sha256": sha256,
            "dim": bench_info["dim"],
            "latency_1s_ms": bench_info["latency_1s_ms"],
            "verified": bench_info["verified"],
        }
        manifest_entries.append(record)

    # Download verification test audios
    print("\nDownloading sample multi-speaker test WAVs...")
    for filename, url in TEST_AUDIOS:
        dest = models_dir / filename
        download_file(url, dest)
        print(f"  {filename}: {dest.stat().st_size} bytes")

    manifest_path = models_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_entries, indent=2, ensure_ascii=False))
    print(f"\nManifest successfully written to {manifest_path}")

if __name__ == "__main__":
    main()
