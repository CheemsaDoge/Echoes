#!/usr/bin/env python3
"""Comprehensive benchmark and evaluation runner for multiple 16kHz VAD models.
Evaluates accuracy (speech recall, silence/noise removal rate, false retained rate),
latency, and RTF overall and grouped by acoustic scene (喧闹, 谈话, 安静, 谈话者声音小).
Outputs Markdown tables and JSON reports.
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
import time
import wave
import numpy as np

from echoledger.models import VadConfig
from echoledger.vad import (
    FRAME,
    make_scorer,
    list_available_vads,
    speech_intervals,
    score_intervals,
)
from echoledger.audio import merge_intervals

def parse_intervals(interval_str: str, sr: int = 16000) -> list[tuple[int, int]]:
    s = interval_str.strip()
    if not s:
        return []
    res = []
    for item in re.split(r"[;,]", s):
        item = item.strip()
        if not item:
            continue
        m = re.match(r"^([\d.]+)\s*-\s*([\d.]+)$", item)
        if m:
            start_s = float(m.group(1))
            end_s = float(m.group(2))
            res.append((int(round(start_s * sr)), int(round(end_s * sr))))
    return res

def read_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        w = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if w != 2:
        raise ValueError(f"Expected 16-bit PCM in {path}")
    samples = np.frombuffer(frames, dtype=np.int16)
    if ch > 1:
        samples = samples.reshape(-1, ch)[:, 0]
    if sr != 16000:
        target_len = int(len(samples) * 16000 / sr)
        orig_indices = np.linspace(0, len(samples) - 1, target_len)
        samples = np.interp(orig_indices, np.arange(len(samples)), samples).astype(np.int16)
    return samples

def run_benchmark_on_dataset(
    slices: list[dict],
    models: list[str],
    configs: list[tuple[str, VadConfig]],
    base_dir: Path,
) -> dict:
    # Preload audio into memory
    audio_cache = {}
    total_audio_sec = 0.0
    for item in slices:
        wav_path = base_dir / item["file"]
        if not wav_path.is_file():
            # Try relative to cwd or item['file'] directly
            if Path(item["file"]).is_file():
                wav_path = Path(item["file"])
            else:
                raise FileNotFoundError(f"Slice audio file not found: {wav_path}")
        audio_data = read_wav_16k(wav_path)
        audio_cache[item["id"]] = audio_data
        total_audio_sec += len(audio_data) / 16000.0

    results = []

    for model_name in models:
        try:
            scorer = make_scorer(model_name)
        except Exception as e:
            print(f"Skipping model '{model_name}': initialization failed ({e})")
            continue

        # Compute probabilities and benchmark inference latency
        model_scores = {}
        total_inf_time = 0.0

        for item in slices:
            samples = audio_cache[item["id"]]
            audio_sec = len(samples) / 16000.0

            t0 = time.perf_counter()
            probs = scorer.probabilities(samples)
            inf_dur = time.perf_counter() - t0

            total_inf_time += inf_dur
            model_scores[item["id"]] = (probs, inf_dur)

        avg_latency_ms = (total_inf_time * 1000.0) / max(1, len(slices))
        rtf = total_inf_time / max(1e-6, total_audio_sec)

        for cfg_name, vad_cfg in configs:
            # Overall counters
            tot_n = 0
            tot_gold = 0
            tot_pred = 0
            tot_tp = 0

            # Scene-wise counters: scene -> dict
            scenes = {}

            for item in slices:
                samples = audio_cache[item["id"]]
                n = len(samples)
                probs, _ = model_scores[item["id"]]
                truth = parse_intervals(item.get("speech_intervals", ""))

                pred = speech_intervals(probs, n, vad_cfg)
                pred_merged = merge_intervals(pred, n)
                gold_merged = merge_intervals(truth, n)

                pred_n = sum(b - a for a, b in pred_merged)
                gold_n = sum(b - a for a, b in gold_merged)
                tp = sum(max(0, min(b, d) - max(a, c)) for a, b in pred_merged for c, d in gold_merged)

                tot_n += n
                tot_gold += gold_n
                tot_pred += pred_n
                tot_tp += tp

                sc = item.get("scene", "未归类")
                if sc not in scenes:
                    scenes[sc] = {"n": 0, "gold": 0, "pred": 0, "tp": 0}
                scenes[sc]["n"] += n
                scenes[sc]["gold"] += gold_n
                scenes[sc]["pred"] += pred_n
                scenes[sc]["tp"] += tp

            tot_negative = tot_n - tot_gold
            tot_fn = tot_gold - tot_tp
            tot_fp = tot_pred - tot_tp

            recall = tot_tp / tot_gold if tot_gold > 0 else (1.0 if tot_pred == 0 else 0.0)
            removed_frac = 1.0 - (tot_pred / tot_n) if tot_n > 0 else 0.0
            non_speech_retained = tot_fp / tot_negative if tot_negative > 0 else 0.0

            scene_results = {}
            for sc_name, sc_data in scenes.items():
                s_neg = sc_data["n"] - sc_data["gold"]
                s_fp = sc_data["pred"] - sc_data["tp"]
                s_rec = sc_data["tp"] / sc_data["gold"] if sc_data["gold"] > 0 else 1.0
                s_rem = 1.0 - (sc_data["pred"] / sc_data["n"]) if sc_data["n"] > 0 else 0.0
                s_ns_ret = s_fp / s_neg if s_neg > 0 else 0.0
                scene_results[sc_name] = {
                    "recall": s_rec,
                    "removed_fraction": s_rem,
                    "non_speech_retained_rate": s_ns_ret,
                    "slices_count": sum(1 for s in slices if s.get("scene") == sc_name),
                }

            results.append({
                "model": model_name,
                "config": cfg_name,
                "vad_config": {
                    "threshold": vad_cfg.threshold,
                    "min_silence_ms": vad_cfg.min_silence_ms,
                    "pad_ms": vad_cfg.pad_ms,
                    "retain_uncertain": vad_cfg.retain_uncertain,
                },
                "speech_recall": recall,
                "removed_fraction": removed_frac,
                "non_speech_retained_rate": non_speech_retained,
                "missed_speech_ms": tot_fn * 1000.0 / 16000.0,
                "false_retained_ms": tot_fp * 1000.0 / 16000.0,
                "latency_ms": round(avg_latency_ms, 2),
                "rtf": round(rtf, 5),
                "scenes": scene_results,
            })

    return {
        "slices_evaluated": len(slices),
        "total_audio_seconds": round(total_audio_sec, 2),
        "results": results,
    }

def format_markdown_report(report_data: dict) -> str:
    lines = []
    lines.append("# VAD Models Benchmark & Evaluation Report\n")
    lines.append(f"- **Evaluated slices**: {report_data['slices_evaluated']}")
    lines.append(f"- **Total audio duration**: {report_data['total_audio_seconds']}s")
    lines.append(f"- **Generated at**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    lines.append("## 1. Overall Performance Comparison\n")
    lines.append("| Model | Config | Speech Recall | Removed Frac | Non-Speech Retained | Latency/slice | RTF |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: |")

    for r in report_data["results"]:
        rec_str = f"{r['speech_recall']*100:.1f}%"
        rem_str = f"{r['removed_fraction']*100:.1f}%"
        ns_ret_str = f"{r['non_speech_retained_rate']*100:.1f}%"
        lines.append(f"| `{r['model']}` | {r['config']} | **{rec_str}** | {rem_str} | {ns_ret_str} | {r['latency_ms']:.1f}ms | {r['rtf']:.4f} |")

    # Group by scene
    all_scenes = set()
    for r in report_data["results"]:
        all_scenes.update(r["scenes"].keys())

    if all_scenes:
        lines.append("\n## 2. Performance Breakdown by Scene\n")
        for sc in sorted(all_scenes):
            lines.append(f"### Scene: `{sc}`\n")
            lines.append("| Model | Config | Scene Speech Recall | Scene Removed Frac | False Retain Rate |")
            lines.append("| :--- | :--- | :---: | :---: | :---: |")
            for r in report_data["results"]:
                sc_res = r["scenes"].get(sc)
                if sc_res:
                    s_rec = f"{sc_res['recall']*100:.1f}%"
                    s_rem = f"{sc_res['removed_fraction']*100:.1f}%"
                    s_ns = f"{sc_res['non_speech_retained_rate']*100:.1f}%"
                    lines.append(f"| `{r['model']}` | {r['config']} | {s_rec} | {s_rem} | {s_ns} |")
            lines.append("")

    return "\n".join(lines)

def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark multiple VAD models against ground truth dataset")
    parser.add_argument("--labels", type=Path, help="Path to labels.json")
    parser.add_argument("--slices-dir", type=Path, help="Path to slices directory containing labels.json")
    parser.add_argument("--all-slices", action="store_true", help="Evaluate all slices, including unverified ones")
    parser.add_argument("--wav", type=Path, help="Quick test: single WAV file")
    parser.add_argument("--intervals", type=str, default="", help="Quick test: speech intervals for --wav (e.g. '0.5-3.2; 4.0-7.5')")
    parser.add_argument("--scene", type=str, default="谈话", help="Quick test: scene name for --wav")
    parser.add_argument("--models", type=str, default="", help="Comma-separated models to evaluate (default: all available)")
    parser.add_argument("--sweep", action="store_true", help="Run hyperparameter grid sweep across thresholds and padding")
    parser.add_argument("--output", "-o", type=Path, help="Output path for Markdown report (e.g. report.md)")
    parser.add_argument("--json", type=Path, help="Output path for JSON report (e.g. report.json)")
    args = parser.parse_args()

    # Determine slice list
    slices = []
    base_dir = Path(".")
    if args.wav:
        if not args.wav.is_file():
            raise FileNotFoundError(f"WAV file not found: {args.wav}")
        samples = read_wav_16k(args.wav)
        dur = len(samples) / 16000.0
        slices.append({
            "id": args.wav.stem,
            "file": str(args.wav),
            "duration_s": round(dur, 2),
            "scene": args.scene,
            "speech_intervals": args.intervals,
            "verified": True,
        })
        base_dir = args.wav.parent
    else:
        labels_file = args.labels
        if not labels_file and args.slices_dir:
            labels_file = args.slices_dir / "labels.json"
        if not labels_file or not labels_file.is_file():
            raise FileNotFoundError(f"labels.json not found. Pass --labels or --slices-dir.")
        base_dir = labels_file.parent
        data = json.loads(labels_file.read_text())
        all_s = data.get("slices", [])
        if args.all_slices:
            slices = all_s
        else:
            slices = [s for s in all_s if s.get("verified")]
            if not slices:
                print("Warning: No verified slices found. Falling back to all slices (--all-slices).")
                slices = all_s

    if not slices:
        raise ValueError("No slices available to evaluate.")

    # Determine models
    if args.models:
        model_names = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        avail = list_available_vads()
        model_names = [v["id"] for v in avail if v["ready"] and v["id"] not in ("passthrough", "energy_demo")]

    # Determine configs
    configs = []
    if args.sweep:
        for th in [0.35, 0.50, 0.65]:
            for pad in [32, 64, 128]:
                for ret in [True, False]:
                    tag = f"th={th}_pad={pad}_ret={int(ret)}"
                    configs.append((tag, VadConfig(threshold=th, pad_ms=pad, retain_uncertain=ret)))
    else:
        configs.append(("default", VadConfig(threshold=0.5, pad_ms=64, retain_uncertain=True)))
        configs.append(("conservative", VadConfig(threshold=0.35, pad_ms=96, retain_uncertain=True)))
        configs.append(("aggressive", VadConfig(threshold=0.65, pad_ms=32, retain_uncertain=False)))

    print(f"Starting benchmark on {len(slices)} slices with {len(model_names)} models ({len(configs)} configs each)...")
    report = run_benchmark_on_dataset(slices, model_names, configs, base_dir)

    md_output = format_markdown_report(report)
    print("\n" + md_output)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md_output, encoding="utf-8")
        print(f"Markdown report written to: {args.output}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON report written to: {args.json}")

if __name__ == "__main__":
    main()
