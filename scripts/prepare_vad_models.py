#!/usr/bin/env python3
"""Download, verify, and benchmark 16kHz VAD models into data/models/vad/.
Generates data/models/vad/manifest.json with provenance, file hashes, input/output
signatures, and measured 1-second inference latency.
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

MODELS = [
    {
        "id": "silero_v5",
        "name": "Silero VAD v5 ONNX",
        "file": "silero_vad_v5.onnx",
        "urls": [
            "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx",
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad_v5.onnx",
        ],
        "type": "onnx",
        "copy_to": "data/models/silero_vad.onnx",
        "description": "Silero VAD v5 recurrent model (input, sr, state)",
    },
    {
        "id": "silero_v4",
        "name": "Silero VAD v4 ONNX",
        "file": "silero_vad_v4.onnx",
        "urls": [
            "https://github.com/snakers4/silero-vad/raw/v4.0/files/silero_vad.onnx",
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad_v4.onnx",
        ],
        "type": "onnx",
        "description": "Silero VAD v4 recurrent model (input, sr, h, c)",
    },
    {
        "id": "silero_v3",
        "name": "Silero VAD v3 ONNX",
        "file": "silero_vad_v3.onnx",
        "urls": [
            "https://github.com/snakers4/silero-vad/raw/v3.1/files/silero_vad.onnx",
        ],
        "type": "onnx",
        "description": "Silero VAD v3 recurrent model (input, h0, c0)",
    },
    {
        "id": "ten_vad",
        "name": "TEN-framework VAD ONNX",
        "file": "ten_vad.onnx",
        "urls": [
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/ten-vad.onnx",
            "https://raw.githubusercontent.com/TEN-framework/ten-vad/main/src/onnx_model/ten-vad.onnx",
        ],
        "type": "onnx",
        "description": "TEN-framework neural VAD model (16kHz, 256 hop)",
    },
    {
        "id": "ten_vad_int8",
        "name": "TEN-framework VAD INT8 ONNX",
        "file": "ten_vad_int8.onnx",
        "urls": [
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/ten-vad.int8.onnx",
        ],
        "type": "onnx",
        "description": "TEN-framework quantized INT8 VAD model",
    },
]

def download_file(urls: list[str], dest: Path) -> None:
    if dest.is_file() and dest.stat().st_size > 1024:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as out:
                shutil.copyfileobj(resp, out)
            if dest.stat().st_size > 1024:
                return
        except Exception as e:
            last_err = e
            if dest.exists():
                dest.unlink()
    raise RuntimeError(f"Failed to download {dest.name} from {urls}: {last_err}")

def inspect_and_benchmark_onnx(model_path: Path, model_id: str) -> dict:
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    
    inputs = [{"name": i.name, "shape": list(i.shape), "type": i.type} for i in session.get_inputs()]
    outputs = [{"name": o.name, "shape": list(o.shape), "type": o.type} for o in session.get_outputs()]

    # Benchmark on 1s dummy audio (16,000 samples)
    dummy_1s = np.zeros(16000, dtype=np.int16)
    
    t0 = time.perf_counter()
    if model_id == "silero_v5":
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, 64), dtype=np.float32)
        audio = dummy_1s.astype(np.float32) / 32768.0
        for i in range(0, len(audio), 512):
            frame = audio[i:i+512]
            if len(frame) < 512:
                frame = np.pad(frame, (0, 512 - len(frame)))
            inp = np.concatenate((context, frame.reshape(1, -1)), axis=1)
            prob, state = session.run(None, {"input": inp, "sr": np.array(16000, dtype=np.int64), "state": state})
            context = inp[:, -64:]
    elif model_id == "silero_v4":
        h = np.zeros((2, 1, 64), dtype=np.float32)
        c = np.zeros((2, 1, 64), dtype=np.float32)
        audio = dummy_1s.astype(np.float32) / 32768.0
        for i in range(0, len(audio), 512):
            frame = audio[i:i+512]
            if len(frame) < 512:
                frame = np.pad(frame, (0, 512 - len(frame)))
            prob, h, c = session.run(None, {"input": frame.reshape(1, -1), "sr": np.array(16000, dtype=np.int64), "h": h, "c": c})
    elif model_id == "silero_v3":
        h0 = np.zeros((2, 1, 64), dtype=np.float32)
        c0 = np.zeros((2, 1, 64), dtype=np.float32)
        audio = dummy_1s.astype(np.float32) / 32768.0
        for i in range(0, len(audio), 512):
            frame = audio[i:i+512]
            if len(frame) < 512:
                frame = np.pad(frame, (0, 512 - len(frame)))
            out, h0, c0 = session.run(None, {"input": frame.reshape(1, -1), "h0": h0, "c0": c0})
    elif "ten_vad" in model_id:
        import sherpa_onnx
        config = sherpa_onnx.VadModelConfig()
        config.sample_rate = 16000
        config.num_threads = 1
        config.provider = "cpu"
        config.ten_vad.model = str(model_path)
        config.ten_vad.threshold = 0.5
        config.ten_vad.min_silence_duration = 0.1
        config.ten_vad.min_speech_duration = 0.1
        config.ten_vad.window_size = 256
        vad = sherpa_onnx.VadModel.create(config)
        audio = dummy_1s.astype(np.float32) / 32768.0
        for i in range(0, len(audio), 256):
            chunk = audio[i:i+256]
            if len(chunk) < 256:
                chunk = np.pad(chunk, (0, 256 - len(chunk)))
            vad.is_speech(chunk)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    return {
        "inputs": inputs,
        "outputs": outputs,
        "latency_1s_ms": round(elapsed_ms, 2),
        "verified": True,
    }

def benchmark_webrtc() -> dict:
    import webrtcvad
    vad = webrtcvad.Vad(2)
    dummy_1s = b"\x00" * 32000
    t0 = time.perf_counter()
    for i in range(0, len(dummy_1s), 320): # 10ms frame is 320 bytes
        frame = dummy_1s[i:i+320]
        vad.is_speech(frame, 16000)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "modes_supported": [0, 1, 2, 3],
        "latency_1s_ms": round(elapsed_ms, 2),
        "verified": True,
    }

def main() -> None:
    parser = argparse.ArgumentParser(description="Download and verify VAD models")
    parser.add_argument("--models-dir", type=Path, default=Path("data/models/vad"))
    args = parser.parse_args()

    models_dir = args.models_dir
    models_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries = []

    for entry in MODELS:
        file_path = models_dir / entry["file"]
        print(f"Preparing {entry['name']} -> {file_path}")
        download_file(entry["urls"], file_path)

        data = file_path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)

        if "copy_to" in entry:
            copy_path = Path(entry["copy_to"])
            copy_path.parent.mkdir(parents=True, exist_ok=True)
            if not copy_path.exists() or copy_path.stat().st_size != size_bytes:
                shutil.copyfile(file_path, copy_path)
                print(f"  Copied to compatibility path: {copy_path}")

        bench_info = inspect_and_benchmark_onnx(file_path, entry["id"])

        record = {
            "id": entry["id"],
            "name": entry["name"],
            "description": entry["description"],
            "file": entry["file"],
            "relative_path": str(file_path),
            "size_bytes": size_bytes,
            "sha256": sha256,
            "inputs": bench_info["inputs"],
            "outputs": bench_info["outputs"],
            "latency_1s_ms": bench_info["latency_1s_ms"],
            "verified": bench_info["verified"],
        }
        manifest_entries.append(record)
        print(f"  Verified! Latency for 1s audio: {bench_info['latency_1s_ms']} ms")

    # Add WebRTC entry
    webrtc_info = benchmark_webrtc()
    manifest_entries.append({
        "id": "webrtc",
        "name": "Google WebRTC VAD",
        "description": "GMM-based subband VAD supporting aggressiveness modes 0, 1, 2, 3",
        "file": "embedded:webrtcvad-wheels",
        "relative_path": "builtin",
        "size_bytes": 0,
        "sha256": "builtin-c-extension",
        "inputs": [{"name": "audio_frame_10ms_or_20ms_or_30ms", "type": "int16_pcm", "sample_rate": 16000}],
        "outputs": [{"name": "is_speech", "type": "bool"}],
        "latency_1s_ms": webrtc_info["latency_1s_ms"],
        "verified": webrtc_info["verified"],
    })
    print(f"  WebRTC VAD verified! Latency for 1s audio: {webrtc_info['latency_1s_ms']} ms")

    manifest_path = models_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_entries, indent=2, ensure_ascii=False))
    print(f"\nManifest successfully written to {manifest_path}")

if __name__ == "__main__":
    main()
