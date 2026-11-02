#!/usr/bin/env python3
"""Comprehensive benchmark and model selection runner for 16kHz VAD and Speaker Recognition.
Supports chunk-level dual ground-truth ('has_speech' and 'should_detect'), multi-scene evaluation,
and Stratified Random Spot-Check for speaker identification models.
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
import sys
import time
import wave
import numpy as np

# Ensure repository root and backend directory are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from echoledger.models import VadConfig
from echoledger.vad import (
    FRAME,
    make_scorer,
    list_available_vads,
    speech_intervals,
    score_intervals,
)
from echoledger.audio import merge_intervals
from echoledger.speakers import (
    list_available_speaker_models,
    spot_check_speaker_models,
)

def parse_intervals(interval_str: str, sr: int = 16000) -> list[tuple[int, int]]:
    s = str(interval_str or "").strip()
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

def read_wav_16k(path: Path | str) -> np.ndarray:
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

def run_comprehensive_evaluation(
    dataset_records: list[dict],
    vad_models: list[str] | None = None,
    configs: list[tuple[str, VadConfig]] | None = None,
    base_dir: Path = Path("."),
    speaker_rounds: int = 25,
) -> dict:
    """Core evaluation engine analyzing both VAD models and Speaker Embedding models across chunks.
    
    Each record in dataset_records can contain:
      - 'id' / 'chunk_id': str
      - 'file' / 'wav_path': str
      - 'samples': np.ndarray (optional, if preloaded)
      - 'scene': str (e.g. '喧闹', '安静', '对话', '讲课')
      - 'has_speech': bool | None (whether chunk actually contains human speech)
      - 'should_detect': bool | None (whether VAD should trigger detection on this chunk)
      - 'speaker': str | None (person label, or '未知路人' / '')
      - 'speech_intervals': str (fine-grained intervals)
    """
    # 1. Preload audio into memory
    loaded_records = []
    total_audio_sec = 0.0

    for item in dataset_records:
        rec = dict(item)
        if "samples" in rec and rec["samples"] is not None:
            samples = rec["samples"]
        else:
            rel_file = rec.get("file") or rec.get("wav_path") or ""
            wav_path = base_dir / rel_file
            if not wav_path.is_file():
                if Path(rel_file).is_file():
                    wav_path = Path(rel_file)
                else:
                    continue
            samples = read_wav_16k(wav_path)
        rec["samples"] = samples
        total_audio_sec += len(samples) / 16000.0
        loaded_records.append(rec)

    if not loaded_records:
        return {"error": "No valid chunk audio files found in dataset"}

    # 2. Setup VAD models and configurations
    if vad_models is None:
        avail_vads = list_available_vads()
        vad_models = [v["id"] for v in avail_vads if v.get("ready") and v["id"] not in ("passthrough", "energy_demo")]

    if configs is None:
        configs = [
            ("default", VadConfig(threshold=0.5, pad_ms=64, retain_uncertain=True)),
            ("conservative", VadConfig(threshold=0.35, pad_ms=96, retain_uncertain=True)),
            ("aggressive", VadConfig(threshold=0.65, pad_ms=32, retain_uncertain=False)),
        ]

    # 3. Benchmark VAD models
    vad_results = []
    model_scores = {}

    for model_name in vad_models:
        try:
            scorer = make_scorer(model_name)
        except Exception:
            continue

        total_inf_time = 0.0
        chunk_probs = {}

        for rec in loaded_records:
            samples = rec["samples"]
            t0 = time.perf_counter()
            probs = scorer.probabilities(samples)
            inf_dur = time.perf_counter() - t0
            total_inf_time += inf_dur
            chunk_probs[rec.get("id") or rec.get("chunk_id")] = probs

        avg_latency_ms = (total_inf_time * 1000.0) / len(loaded_records)
        rtf = total_inf_time / max(1e-6, total_audio_sec)
        model_scores[model_name] = (chunk_probs, avg_latency_ms, rtf)

        for cfg_name, vad_cfg in configs:
            # Chunk-level confusion metrics
            # Condition Positive = should_detect is True (or has speech_intervals / has_speech)
            total_chunks = len(loaded_records)
            chunk_tp, chunk_fp, chunk_fn, chunk_tn = 0, 0, 0, 0
            
            # Sub-metrics: Speech missed count & Ambient noise false alarm count
            true_speech_missed = 0
            noise_false_alarm = 0
            unwanted_speech_suppressed = 0

            # Fine-grained sample-level metrics (when intervals are available)
            tot_samples = 0
            tot_gold_samples = 0
            tot_pred_samples = 0
            tot_tp_samples = 0

            # Scene-wise stats: scene_name -> dict
            scene_breakdown: dict[str, dict] = {}

            for rec in loaded_records:
                cid = rec.get("id") or rec.get("chunk_id")
                samples = rec["samples"]
                n = len(samples)
                probs = chunk_probs[cid]

                pred_intervals = speech_intervals(probs, n, vad_cfg)
                pred_samples_len = sum(b - a for a, b in pred_intervals)
                vad_detected = (pred_samples_len > 0)

                # Ground truth resolution:
                # 1. Direct explicit should_detect
                should_detect = rec.get("should_detect")
                has_speech = rec.get("has_speech")
                raw_intervals = rec.get("speech_intervals", "")

                if should_detect is None:
                    if "speech_intervals" in rec:
                        should_detect = bool(raw_intervals)
                    elif has_speech is not None:
                        should_detect = has_speech
                    else:
                        should_detect = False

                if has_speech is None:
                    if "speech_intervals" in rec:
                        has_speech = bool(raw_intervals)
                    else:
                        has_speech = should_detect

                # Chunk-level decision
                if should_detect:
                    if vad_detected:
                        chunk_tp += 1
                    else:
                        chunk_fn += 1
                        if has_speech:
                            true_speech_missed += 1
                else:
                    if vad_detected:
                        chunk_fp += 1
                        if not has_speech:
                            noise_false_alarm += 1
                    else:
                        chunk_tn += 1
                        if has_speech:
                            unwanted_speech_suppressed += 1

                # Sample-level tracking
                gold_intervals = parse_intervals(raw_intervals)
                if gold_intervals:
                    pred_m = merge_intervals(pred_intervals, n)
                    gold_m = merge_intervals(gold_intervals, n)
                    p_n = sum(b - a for a, b in pred_m)
                    g_n = sum(b - a for a, b in gold_m)
                    tp_s = sum(max(0, min(b, d) - max(a, c)) for a, b in pred_m for c, d in gold_m)
                    tot_samples += n
                    tot_gold_samples += g_n
                    tot_pred_samples += p_n
                    tot_tp_samples += tp_s

                # Scene tracking
                scene = rec.get("scene") or "未分类"
                sc_dict = scene_breakdown.setdefault(scene, {
                    "total": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0, "pred_samples": 0, "tot_samples": 0
                })
                sc_dict["total"] += 1
                sc_dict["tot_samples"] += n
                sc_dict["pred_samples"] += pred_samples_len
                if should_detect:
                    if vad_detected: sc_dict["tp"] += 1
                    else: sc_dict["fn"] += 1
                else:
                    if vad_detected: sc_dict["fp"] += 1
                    else: sc_dict["tn"] += 1

            # Compute rates
            condition_pos = chunk_tp + chunk_fn
            condition_neg = chunk_tn + chunk_fp
            chunk_accuracy = (chunk_tp + chunk_tn) / total_chunks if total_chunks else 0.0
            chunk_recall = chunk_tp / condition_pos if condition_pos else 1.0
            chunk_specificity = chunk_tn / condition_neg if condition_neg else 1.0
            
            precision = chunk_tp / (chunk_tp + chunk_fp) if (chunk_tp + chunk_fp) else 0.0
            f1_score = (2 * precision * chunk_recall) / (precision + chunk_recall) if (precision + chunk_recall) else 0.0

            tot_samples_all = sum(sc["tot_samples"] for sc in scene_breakdown.values())
            tot_pred_samples_all = sum(sc["pred_samples"] for sc in scene_breakdown.values())
            overall_rem = 1.0 - (tot_pred_samples_all / tot_samples_all) if tot_samples_all else 0.0

            # Scene-level computed summaries
            scenes_out = {}
            for sc_name, sc_data in scene_breakdown.items():
                sc_p = sc_data["tp"] + sc_data["fn"]
                sc_n = sc_data["tn"] + sc_data["fp"]
                sc_rec = sc_data["tp"] / sc_p if sc_p else 1.0
                sc_spec = sc_data["tn"] / sc_n if sc_n else 1.0
                sc_acc = (sc_data["tp"] + sc_data["tn"]) / sc_data["total"] if sc_data["total"] else 0.0
                sc_rem = 1.0 - (sc_data["pred_samples"] / sc_data["tot_samples"]) if sc_data["tot_samples"] else 0.0
                scenes_out[sc_name] = {
                    "chunks_count": sc_data["total"],
                    "accuracy": round(sc_acc, 4),
                    "speech_recall": round(sc_rec, 4),
                    "noise_rejection": round(sc_spec, 4),
                    "removed_fraction": round(sc_rem, 4),
                }

            vad_results.append({
                "model": model_name,
                "config": cfg_name,
                "vad_config": {
                    "threshold": vad_cfg.threshold,
                    "min_silence_ms": vad_cfg.min_silence_ms,
                    "pad_ms": vad_cfg.pad_ms,
                    "retain_uncertain": vad_cfg.retain_uncertain,
                },
                "chunk_accuracy": round(chunk_accuracy, 4),
                "speech_recall": round(chunk_recall, 4),
                "noise_rejection": round(chunk_specificity, 4),
                "removed_fraction": round(overall_rem, 4),
                "f1_score": round(f1_score, 4),
                "true_speech_missed": true_speech_missed,
                "noise_false_alarms": noise_false_alarm,
                "latency_ms": round(avg_latency_ms, 2),
                "rtf": round(rtf, 5),
                "scenes": scenes_out,
            })

    # Rank VAD models by F1-score, then accuracy, then latency
    vad_results.sort(key=lambda x: (x["f1_score"], x["chunk_accuracy"], -x["latency_ms"]), reverse=True)
    best_vad = vad_results[0] if vad_results else None

    # Scene-specific best VAD recommendations
    all_scenes = set()
    for r in vad_results:
        all_scenes.update(r["scenes"].keys())

    scene_recommendations = {}
    for sc in sorted(all_scenes):
        best_sc_entry = max(vad_results, key=lambda r: (
            r["scenes"].get(sc, {}).get("accuracy", 0),
            r["scenes"].get(sc, {}).get("speech_recall", 0),
            -r["latency_ms"]
        ))
        scene_recommendations[sc] = {
            "recommended_model": best_sc_entry["model"],
            "config": best_sc_entry["config"],
            "stats": best_sc_entry["scenes"].get(sc, {}),
        }

    # 4. Speaker Embedding Model Spot-Check (if any speaker labels exist)
    speaker_eval_report = None
    chunks_with_speaker = [c for c in loaded_records if c.get("speaker")]
    if len(chunks_with_speaker) >= 3:
        speaker_eval_report = spot_check_speaker_models(
            chunks_with_speaker,
            rounds=speaker_rounds,
        )

    # 5. Recommended .env configuration generator
    env_snippet = []
    if best_vad:
        env_snippet.append(f"# Recommended VAD Provider (Overall F1={best_vad['f1_score']:.1%})")
        env_snippet.append(f"VAD_PROVIDER={best_vad['model']}")
        cfg_obj = best_vad["vad_config"]
        env_snippet.append(f"# Calibrated VAD Profile parameters (configured via API / DB profile):")
        env_snippet.append(f"# threshold={cfg_obj['threshold']}, pad_ms={cfg_obj['pad_ms']}, retain_uncertain={str(cfg_obj['retain_uncertain']).lower()}")
    if speaker_eval_report and speaker_eval_report.get("winner"):
        rc = speaker_eval_report.get("recommended_config", {})
        acc_str = f" Top-1 Acc={rc.get('accuracy', 1.0):.1%}" if rc else ""
        env_snippet.append(f"\n# Recommended Speaker Embedding Model ({speaker_eval_report['winner']}{acc_str})")
        if rc:
            env_snippet.append(f"# Calibrated matching parameters: threshold={rc.get('threshold')}, margin={rc.get('margin')}")

    return {
        "timestamp": datetime.datetime.now().isoformat(),
        "chunks_evaluated": len(loaded_records),
        "slices_evaluated": len(loaded_records),
        "total_audio_seconds": round(total_audio_sec, 2),
        "best_vad": best_vad,
        "scene_recommendations": scene_recommendations,
        "vad_rankings": vad_results,
        "results": vad_results,
        "speaker_evaluation": speaker_eval_report,
        "recommended_env": "\n".join(env_snippet),
    }

def format_comprehensive_markdown(report: dict) -> str:
    lines = []
    lines.append("# Echoes Multi-Day VAD & Speaker Recognition Comprehensive Benchmark\n")
    lines.append(f"- **Total Chunks Evaluated**: {report['chunks_evaluated']}")
    lines.append(f"- **Total Audio Duration**: {report['total_audio_seconds']}s")
    lines.append(f"- **Timestamp**: {report['timestamp']}\n")

    # 1. Best VAD Winner Banner
    best = report.get("best_vad")
    if best:
        lines.append("## 🏆 Recommended VAD Model & Settings\n")
        lines.append(f"> **Optimal Provider**: `{best['model']}` ({best['config']})\n")
        lines.append(f"> - **Chunk Accuracy**: **{best['chunk_accuracy']*100:.1f}%**")
        lines.append(f"> - **Speech Recall (Anti-Leak)**: **{best['speech_recall']*100:.1f}%** (Missed: {best['true_speech_missed']} chunks)")
        lines.append(f"> - **Noise Rejection (Anti-False-Alarm)**: **{best['noise_rejection']*100:.1f}%** (False alarms: {best['noise_false_alarms']} chunks)")
        lines.append(f"> - **F1-Score**: **{best['f1_score']:.4f}** | Latency: **{best['latency_ms']:.1f}ms** (RTF: {best['rtf']:.4f})\n")

    # 2. VAD Rankings Table
    lines.append("## 📊 VAD Model Comprehensive Leaderboard\n")
    lines.append("| Rank | Model | Config | Accuracy | Recall (防漏检) | Rejection (防误触) | F1-Score | Latency | RTF |")
    lines.append("| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |")

    for i, r in enumerate(report.get("vad_rankings", [])[:15], 1):
        lines.append(
            f"| {i} | `{r['model']}` | {r['config']} | "
            f"**{r['chunk_accuracy']*100:.1f}%** | {r['speech_recall']*100:.1f}% | "
            f"{r['noise_rejection']*100:.1f}% | {r['f1_score']:.4f} | "
            f"{r['latency_ms']:.1f}ms | {r['rtf']:.4f} |"
        )

    # 3. Scene Breakdown
    lines.append("\n## 🏙️ Performance Breakdown by Scene\n")
    scene_recs = report.get("scene_recommendations", {})
    for sc, info in scene_recs.items():
        st = info["stats"]
        lines.append(f"### Scene: `{sc}` ({st.get('chunks_count', 0)} chunks)")
        lines.append(f"- **Top Model for this scene**: `{info['recommended_model']}` ({info['config']})")
        lines.append(f"- Scene Accuracy: **{st.get('accuracy', 0)*100:.1f}%** | Recall: **{st.get('speech_recall', 0)*100:.1f}%** | Noise Rejection: **{st.get('noise_rejection', 0)*100:.1f}%**\n")

    # 4. Speaker Recognition Spot-Check
    spk_rep = report.get("speaker_evaluation")
    if spk_rep and "models" in spk_rep:
        lines.append("## 🎙️ Speaker Embedding Models (Stratified Random Spot-Check)\n")
        lines.append(f"> **Optimal Speaker Model**: `{spk_rep.get('winner')}`\n")
        lines.append("| Model | Dim | Top-1 Accuracy | Known Recall | Unknown Rejection | FAR | FRR | Discrim Gap | Latency |")
        lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        for m in spk_rep["models"]:
            bc = m["best_config"]
            lines.append(
                f"| `{m['model']}` | {m['dim']} | **{bc['accuracy']*100:.1f}%** | "
                f"{bc['known_recall']*100:.1f}% | {bc['unknown_rejection']*100:.1f}% | "
                f"{bc['far']*100:.1f}% | {bc['frr']*100:.1f}% | "
                f"+{m['discrimination_gap']:.4f} | {m['latency_ms']:.1f}ms |"
            )

    # 5. Environment Config Snippet
    if report.get("recommended_env"):
        lines.append("\n## ⚙️ Recommended `.env` Snippet\n")
        lines.append("```ini")
        lines.append(report["recommended_env"])
        lines.append("```\n")

    return "\n".join(lines)

def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Day VAD & Speaker Recognition Comprehensive Benchmark")
    parser.add_argument("--labels", type=Path, help="Path to labels.json or dataset.json")
    parser.add_argument("--slices-dir", type=Path, help="Path to slices directory containing labels.json")
    parser.add_argument("--all-slices", action="store_true", help="Evaluate all slices, including unverified ones")
    parser.add_argument("--wav", type=Path, help="Quick test: single WAV file")
    parser.add_argument("--intervals", type=str, default="", help="Quick test: speech intervals for --wav")
    parser.add_argument("--scene", type=str, default="谈话", help="Quick test: scene name for --wav")
    parser.add_argument("--models", type=str, default="", help="Comma-separated VAD models to evaluate")
    parser.add_argument("--sweep", action="store_true", help="Run full hyperparameter grid sweep")
    parser.add_argument("--output", "-o", type=Path, help="Output path for Markdown report (e.g. report.md)")
    parser.add_argument("--json", type=Path, help="Output path for JSON report (e.g. report.json)")
    args = parser.parse_args()

    records = []
    base_dir = Path(".")

    if args.wav:
        if not args.wav.is_file():
            raise FileNotFoundError(f"WAV file not found: {args.wav}")
        samples = read_wav_16k(args.wav)
        dur = len(samples) / 16000.0
        records.append({
            "id": args.wav.stem,
            "file": str(args.wav),
            "samples": samples,
            "duration_s": round(dur, 2),
            "scene": args.scene,
            "has_speech": bool(args.intervals),
            "should_detect": bool(args.intervals),
            "speech_intervals": args.intervals,
            "verified": True,
        })
        base_dir = args.wav.parent
    else:
        labels_file = args.labels
        if not labels_file and args.slices_dir:
            labels_file = args.slices_dir / "labels.json"
        if not labels_file or not labels_file.is_file():
            raise FileNotFoundError("labels.json not found. Pass --labels or --slices-dir.")
        base_dir = labels_file.parent
        data = json.loads(labels_file.read_text())
        all_s = data.get("slices") or data.get("chunks") or []
        if args.all_slices:
            records = all_s
        else:
            records = [s for s in all_s if s.get("verified") or s.get("labeled")]
            if not records:
                records = all_s

    if not records:
        raise ValueError("No records available to evaluate.")

    # Parse models
    vad_models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None

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

    print(f"Running comprehensive evaluation on {len(records)} chunks...")
    report = run_comprehensive_evaluation(records, vad_models=vad_models, configs=configs, base_dir=base_dir)

    md_text = format_comprehensive_markdown(report)
    print("\n" + md_text)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md_text, encoding="utf-8")
        print(f"Markdown report written to: {args.output}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON report written to: {args.json}")

if __name__ == "__main__":
    main()
