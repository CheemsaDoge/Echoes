#!/usr/bin/env python3
"""Echoes Desktop Workbench: Interactive Terminal UI (TUI) for Chunk-level VAD & Speaker Labeling,
Acoustic Metrics Inspection, and Comprehensive Multi-Model Benchmarking.
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
import shutil
import sys
import time
import uuid
import wave
import zipfile
import numpy as np

# Ensure repository root and backend directory are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from scripts.benchmark_vads import (
        read_wav_16k,
        run_comprehensive_evaluation,
        format_comprehensive_markdown,
    )
except ImportError:
    from benchmark_vads import (
        read_wav_16k,
        run_comprehensive_evaluation,
        format_comprehensive_markdown,
    )

DEFAULT_WORKBENCH_DIR = Path("data/workbench")
DEFAULT_DATASET_FILE = DEFAULT_WORKBENCH_DIR / "dataset.json"

def load_tui_dataset(dataset_path: Path) -> dict:
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    if dataset_path.is_file():
        try:
            return json.loads(dataset_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Warning: Failed to parse {dataset_path}: {e}")
    default_data = {
        "scenes": ["对话", "喧闹", "安静", "讲课", "会议", "户外", "谈话者声音小"],
        "speakers": ["我", "张老师", "未知路人"],
        "chunks": [],
    }
    save_tui_dataset(default_data, dataset_path)
    return default_data

def save_tui_dataset(data: dict, dataset_path: Path) -> None:
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def import_zip_to_dataset(zip_path: Path, dataset_path: Path) -> int:
    dataset = load_tui_dataset(dataset_path)
    audio_dir = dataset_path.parent / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    imported_count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        session_info = {}
        for n in zf.namelist():
            if n.endswith("session.json"):
                try:
                    session_info = json.loads(zf.read(n).decode("utf-8"))
                except Exception:
                    pass

        default_scene = "对话"
        for n in zf.namelist():
            if n.endswith("scene_tag.txt"):
                try:
                    txt = zf.read(n).decode("utf-8").strip()
                    if txt:
                        default_scene = txt
                except Exception:
                    pass

        wav_names = [n for n in zf.namelist() if n.lower().endswith(".wav") and not n.startswith("__MACOSX")]
        wav_names.sort(key=lambda x: [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", x)])

        start_time_base = session_info.get("started_at_ms", int(time.time() * 1000))

        for idx, wname in enumerate(wav_names):
            cid = f"chunk_{Path(wname).stem}_{uuid.uuid4().hex[:6]}"
            target_wav = audio_dir / f"{cid}.wav"
            target_wav.write_bytes(zf.read(wname))

            samples = read_wav_16k(target_wav)
            dur = round(len(samples) / 16000.0, 2)
            f_samples = samples.astype(np.float64) / 32768.0
            rms = float(np.sqrt(np.mean(f_samples * f_samples))) if len(f_samples) > 0 else 0.0
            rms_dbfs = round(20.0 * math.log10(max(rms, 1e-6)), 2)

            chunk_time_ms = start_time_base + int(idx * dur * 1000)
            iso_time = datetime.datetime.fromtimestamp(chunk_time_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")

            # Determine relative path from dataset_path.parent
            try:
                rel_file = str(target_wav.relative_to(dataset_path.parent))
            except ValueError:
                rel_file = str(target_wav)

            record = {
                "id": cid,
                "session_id": session_info.get("id", "imported_session"),
                "seq": idx,
                "file": rel_file,
                "duration_s": dur,
                "recorded_at": iso_time,
                "rms_dbfs": rms_dbfs,
                "scene": default_scene,
                "has_speech": True if rms_dbfs > -42.0 else False,
                "should_detect": True if rms_dbfs > -38.0 else False,
                "speaker": "",
                "speech_intervals": "",
                "labeled": False,
                "notes": "",
            }
            dataset["chunks"].append(record)
            imported_count += 1

    if default_scene not in dataset["scenes"]:
        dataset["scenes"].append(default_scene)

    save_tui_dataset(dataset, dataset_path)
    return imported_count

def run_tui_benchmark(dataset: dict, base_dir: Path, sweep: bool = False, output_path: Path | None = None) -> dict:
    chunks = dataset.get("chunks", [])
    if not chunks:
        print("❌ 数据集为空，无法执行评测。")
        return {}
    print(f"\n🚀 开始执行多模型综合评测 (共 {len(chunks)} 个音频块)...")
    report = run_comprehensive_evaluation(chunks, base_dir=base_dir, speaker_rounds=20)
    md_text = format_comprehensive_markdown(report)
    print("\n" + md_text)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md_text, encoding="utf-8")
        print(f"✅ 评测报告已输出至: {output_path}")
    return report

def interactive_loop(dataset_path: Path) -> None:
    dataset = load_tui_dataset(dataset_path)
    base_dir = dataset_path.parent
    idx = 0

    while True:
        chunks = dataset.get("chunks", [])
        total = len(chunks)

        print("\n" + "=" * 78)
        if total == 0:
            print("🎙️  Echoes 录音打标与模型选型终端工作台 (TUI)")
            print("=" * 78)
            print("当前数据集为空。\n")
            print("操作选项:")
            print("  [i] 导入手机端录音无损分块 ZIP 归档包")
            print("  [q] 退出")
            cmd = input("\n请选择操作 > ").strip().lower()
            if cmd == "q":
                break
            elif cmd == "i":
                zip_str = input("输入 ZIP 文件绝对或相对路径: ").strip()
                zp = Path(zip_str)
                if zp.is_file():
                    cnt = import_zip_to_dataset(zp, dataset_path)
                    print(f"✅ 成功导入 {cnt} 个音频块！")
                    dataset = load_tui_dataset(dataset_path)
                    idx = 0
                else:
                    print(f"❌ 文件不存在: {zp}")
            continue

        idx = max(0, min(idx, total - 1))
        c = chunks[idx]

        status_tag = "✅ 已标注" if c.get("labeled") else "⏳ 待标注"
        has_speech_str = "🟢 YES (是)" if c.get("has_speech") else "⚪ NO  (否)"
        should_detect_str = "🟢 YES (是)" if c.get("should_detect") else "⚪ NO  (否)"

        print(f"🎙️  Echoes 音频打标工作台  [{idx + 1} / {total}]  [{status_tag}]")
        print("=" * 78)
        print(f"  • Chunk ID    : {c.get('id', '')} (Session: {c.get('session_id', 'local')})")
        print(f"  • 文件路径    : {c.get('file', '')}")
        print(f"  • 录音时间    : {c.get('recorded_at', '')}  |  时长: {c.get('duration_s', 0.0)}s  |  RMS能量: {c.get('rms_dbfs', -99.0)} dBFS")
        print("-" * 78)
        print(f"  [1] has_speech    (声学人声真值 - 是否含有人说话声)   : {has_speech_str}")
        print(f"  [2] should_detect (业务检出真值 - 是否期望检出触发ASR): {should_detect_str}")
        print(f"  [3] scene         (录音场景分类)                      : [{c.get('scene', '未分类')}]")
        print(f"  [4] speaker       (发言人归属)                        : [{c.get('speaker', '未指定') or '未指定'}]")
        print(f"  [5] intervals     (语音区间, 如 0.2-1.5)              : [{c.get('speech_intervals', '')}]")
        print(f"  [6] notes         (标注备忘)                          : [{c.get('notes', '')}]")
        print("-" * 78)
        print("快捷操作:")
        print("  [1] 切换has_speech    [2] 切换should_detect   [3] 修改场景      [4] 修改发言人")
        print("  [5] 标注语音区间      [6] 添加备注            [n] 下一块 (Next) [p] 上一块 (Prev)")
        print("  [j] 跳转序号          [b] 运行全模型综合评测  [s] 保存数据集    [q] 保存并退出")
        print("=" * 78)

        try:
            cmd = input("命令 (按 Enter 直接跳下一块, 或输入快捷键) > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n正在保存数据集并退出...")
            save_tui_dataset(dataset, dataset_path)
            break

        if cmd in ("", "n", "next"):
            if idx < total - 1:
                idx += 1
            else:
                print("ℹ️ 已是最后一块。")
        elif cmd in ("p", "prev"):
            if idx > 0:
                idx -= 1
            else:
                print("ℹ️ 已是第一块。")
        elif cmd == "1":
            c["has_speech"] = not c.get("has_speech", False)
            c["labeled"] = True
            save_tui_dataset(dataset, dataset_path)
            print(f"👉 has_speech 切换为: {c['has_speech']}")
        elif cmd == "2":
            c["should_detect"] = not c.get("should_detect", False)
            c["labeled"] = True
            save_tui_dataset(dataset, dataset_path)
            print(f"👉 should_detect 切换为: {c['should_detect']}")
        elif cmd == "3":
            scenes = dataset.get("scenes", [])
            print("\n现有场景列表:")
            for si, sc in enumerate(scenes, 1):
                cur = " (当前)" if sc == c.get("scene") else ""
                print(f"  {si}. {sc}{cur}")
            print("  0. [+] 新增自定义场景")
            choice = input("选择场景编号或输入新场景名称: ").strip()
            if choice == "0":
                new_sc = input("输入新场景名称: ").strip()
                if new_sc:
                    if new_sc not in scenes:
                        scenes.append(new_sc)
                    c["scene"] = new_sc
                    c["labeled"] = True
            elif choice.isdigit() and 1 <= int(choice) <= len(scenes):
                c["scene"] = scenes[int(choice) - 1]
                c["labeled"] = True
            elif choice:
                if choice not in scenes:
                    scenes.append(choice)
                c["scene"] = choice
                c["labeled"] = True
            save_tui_dataset(dataset, dataset_path)
        elif cmd == "4":
            speakers = dataset.get("speakers", [])
            print("\n现有发言人列表:")
            for spi, spk in enumerate(speakers, 1):
                cur = " (当前)" if spk == c.get("speaker") else ""
                print(f"  {spi}. {spk}{cur}")
            print("  0. [+] 新增发言人")
            choice = input("选择发言人编号或直接输入名称: ").strip()
            if choice == "0":
                new_spk = input("输入新发言人名称: ").strip()
                if new_spk:
                    if new_spk not in speakers:
                        speakers.append(new_spk)
                    c["speaker"] = new_spk
                    c["labeled"] = True
            elif choice.isdigit() and 1 <= int(choice) <= len(speakers):
                c["speaker"] = speakers[int(choice) - 1]
                c["labeled"] = True
            elif choice:
                if choice not in speakers and choice not in ("未知", "路人", "none"):
                    speakers.append(choice)
                c["speaker"] = choice
                c["labeled"] = True
            save_tui_dataset(dataset, dataset_path)
        elif cmd == "5":
            cur_iv = c.get("speech_intervals", "")
            iv_inp = input(f"输入语音区间 (当前: '{cur_iv}', 如 '0.2-1.5,2.0-3.0'): ").strip()
            c["speech_intervals"] = iv_inp
            c["labeled"] = True
            save_tui_dataset(dataset, dataset_path)
        elif cmd == "6":
            cur_note = c.get("notes", "")
            n_inp = input(f"输入备注 (当前: '{cur_note}'): ").strip()
            c["notes"] = n_inp
            c["labeled"] = True
            save_tui_dataset(dataset, dataset_path)
        elif cmd.startswith("j"):
            parts = cmd.split()
            target_str = parts[1] if len(parts) > 1 else input(f"输入跳转块编号 (1-{total}): ").strip()
            if target_str.isdigit():
                t_idx = int(target_str) - 1
                if 0 <= t_idx < total:
                    idx = t_idx
                else:
                    print(f"❌ 序号超出范围: 1~{total}")
        elif cmd == "b":
            save_tui_dataset(dataset, dataset_path)
            run_tui_benchmark(dataset, base_dir=base_dir)
            input("\n按 Enter 键返回标注界面...")
        elif cmd == "s":
            save_tui_dataset(dataset, dataset_path)
            print("💾 数据集已保存！")
        elif cmd in ("q", "quit", "exit"):
            save_tui_dataset(dataset, dataset_path)
            print("💾 数据集已保存，退出工作台。")
            break
        else:
            print(f"❓ 未知指令: '{cmd}'，请输入 1-6, n, p, j, b, s 或 q。")

def main() -> None:
    parser = argparse.ArgumentParser(description="Echoes Audio Workbench Terminal UI & Benchmarking")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_FILE, help="Path to dataset.json")
    parser.add_argument("--import-zip", type=Path, help="Import a session audio ZIP file into the workbench")
    parser.add_argument("--benchmark", action="store_true", help="Run multi-model comprehensive evaluation directly")
    parser.add_argument("--output", "-o", type=Path, help="Markdown report output path when --benchmark is active")
    args = parser.parse_args()

    dataset_path = args.dataset
    if args.import_zip:
        if not args.import_zip.is_file():
            print(f"❌ ZIP 文件不存在: {args.import_zip}")
            sys.exit(1)
        cnt = import_zip_to_dataset(args.import_zip, dataset_path)
        print(f"✅ 成功从 {args.import_zip} 导入 {cnt} 个音频块至 {dataset_path}")

    if args.benchmark:
        dataset = load_tui_dataset(dataset_path)
        run_tui_benchmark(dataset, base_dir=dataset_path.parent, output_path=args.output)
        sys.exit(0)

    # Interactive loop
    interactive_loop(dataset_path)

if __name__ == "__main__":
    main()
