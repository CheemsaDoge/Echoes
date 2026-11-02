#!/usr/bin/env python3
"""Audio slicing and labeling dataset generator for Echoes VAD benchmarking.
Supports single WAVs, directories of WAVs, and mobile session chunk archives (00000000.wav...).
Slices audio, extracts acoustic metrics (RMS, peak dBFS, activity), and generates
a structured labels.json template for ground truth annotation.
"""
from __future__ import annotations
import argparse
import datetime
import io
import json
import math
import os
from pathlib import Path
import re
import wave
import zipfile
import numpy as np

def read_wav_mono_16k(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        w = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if w != 2:
        raise ValueError(f"Unsupported sample width: {w} bytes (expected 16-bit PCM)")
    samples = np.frombuffer(frames, dtype=np.int16)
    if ch > 1:
        # Take left channel or average
        samples = samples.reshape(-1, ch)[:, 0]
    if sr != 16000:
        # Simple integer resample or linear interpolation if not 16k
        target_len = int(len(samples) * 16000 / sr)
        orig_indices = np.linspace(0, len(samples) - 1, target_len)
        samples = np.interp(orig_indices, np.arange(len(samples)), samples).astype(np.int16)
    return samples

def write_wav(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.astype(np.int16).tobytes())

def collect_audio_from_source(src: Path) -> np.ndarray:
    if src.is_file():
        if src.suffix.lower() == ".zip":
            chunks = []
            with zipfile.ZipFile(src, "r") as zf:
                wav_names = [n for n in zf.namelist() if n.lower().endswith(".wav") and not n.startswith("__MACOSX")]
                # Sort naturally: 00000000.wav, 00000001.wav, etc.
                wav_names.sort(key=lambda x: [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", x)])
                for name in wav_names:
                    chunks.append(read_wav_mono_16k(zf.read(name)))
            if not chunks:
                raise ValueError(f"No WAV files found inside zip: {src}")
            return np.concatenate(chunks)
        else:
            return read_wav_mono_16k(src.read_bytes())
    elif src.is_dir():
        wav_files = sorted(src.glob("*.wav"), key=lambda x: [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", x.name)])
        if not wav_files:
            raise ValueError(f"No WAV files found in directory: {src}")
        chunks = [read_wav_mono_16k(f.read_bytes()) for f in wav_files]
        return np.concatenate(chunks)
    else:
        raise FileNotFoundError(f"Input path not found: {src}")

def compute_acoustic_features(samples: np.ndarray) -> dict:
    if len(samples) == 0:
        return {"duration_s": 0.0, "rms_dbfs": -99.0, "peak_dbfs": -99.0, "active_ratio": 0.0, "scene_guess": "安静"}
    duration_s = round(len(samples) / 16000.0, 3)
    f = samples.astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(f * f)))
    peak = float(np.max(np.abs(f)))
    rms_dbfs = round(20.0 * math.log10(max(rms, 1e-6)), 2)
    peak_dbfs = round(20.0 * math.log10(max(peak, 1e-6)), 2)

    # Frame-wise energy activity (> -42 dBFS)
    frame_len = 512
    active_count = 0
    total_frames = max(1, math.ceil(len(samples) / frame_len))
    for i in range(0, len(samples), frame_len):
        chunk = f[i:i+frame_len]
        c_rms = np.sqrt(np.mean(chunk * chunk))
        if c_rms > 0.008: # ~ -42 dBFS
            active_count += 1
    active_ratio = round(active_count / total_frames, 3)

    # Scene heuristic for initial draft template
    if rms_dbfs < -45.0:
        scene = "安静"
    elif rms_dbfs < -32.0:
        scene = "谈话者声音小"
    elif rms_dbfs < -18.0:
        scene = "谈话"
    else:
        scene = "喧闹"

    return {
        "duration_s": duration_s,
        "rms_dbfs": rms_dbfs,
        "peak_dbfs": peak_dbfs,
        "active_ratio": active_ratio,
        "scene_guess": scene,
    }

def main() -> None:
    parser = argparse.ArgumentParser(description="Slice audio and create VAD ground truth labeling template")
    parser.add_argument("--input", "-i", type=Path, required=True, help="Input WAV file, directory, or session ZIP archive")
    parser.add_argument("--output-dir", "-o", type=Path, default=Path("data/eval_slices/batch_01"), help="Target directory for slices and labels.json")
    parser.add_argument("--slice-seconds", "-s", type=float, default=15.0, help="Slice duration in seconds (default: 15.0)")
    parser.add_argument("--batch-name", type=str, default="", help="Batch identifier (default: directory name)")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_name = args.batch_name or output_dir.name

    print(f"Loading audio from {args.input}...")
    audio = collect_audio_from_source(args.input)
    total_sec = len(audio) / 16000.0
    print(f"Total audio loaded: {len(audio)} samples ({total_sec:.2f} seconds)")

    slice_samples = int(args.slice_seconds * 16000)
    num_slices = math.ceil(len(audio) / slice_samples)
    print(f"Slicing into {num_slices} chunks of ~{args.slice_seconds}s each...")

    slice_records = []
    for idx in range(num_slices):
        start_samp = idx * slice_samples
        end_samp = min(len(audio), start_samp + slice_samples)
        chunk = audio[start_samp:end_samp]
        slice_id = f"slice_{idx+1:04d}"
        filename = f"{slice_id}.wav"
        file_path = output_dir / filename
        write_wav(file_path, chunk, 16000)

        feats = compute_acoustic_features(chunk)
        record = {
            "id": slice_id,
            "file": filename,
            "duration_s": feats["duration_s"],
            "rms_dbfs": feats["rms_dbfs"],
            "peak_dbfs": feats["peak_dbfs"],
            "active_ratio": feats["active_ratio"],
            "scene": feats["scene_guess"],
            "speech_intervals": "",  # To be annotated, e.g. "0.0-15.0" or "2.3-5.6; 8.1-12.0"
            "verified": False,
            "notes": "",
        }
        slice_records.append(record)

    manifest = {
        "batch": batch_name,
        "source": str(args.input),
        "created_at": datetime.datetime.now().isoformat(),
        "total_audio_seconds": round(total_sec, 2),
        "slice_seconds": args.slice_seconds,
        "slices_count": len(slice_records),
        "slices": slice_records,
    }

    labels_path = output_dir / "labels.json"
    labels_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"Successfully created {len(slice_records)} slices in {output_dir}")
    print(f"Annotation template generated at {labels_path}")
    print("\nNext step: edit labels.json to fill in speech_intervals and set verified: true.")

if __name__ == "__main__":
    main()
