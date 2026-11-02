#!/usr/bin/env python3
"""Echoes Desktop Workbench: Web GUI for Chunk-level VAD & Speaker Labeling,
Waveform Inspection, and Multi-Day Comprehensive Model Selection.
Award-tier visual design standard, pure HTML5/Canvas/CSS, FastAPI backend.
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

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

from echoledger.models import VadConfig
from echoledger.vad import list_available_vads
from echoledger.speakers import list_available_speaker_models
from scripts.benchmark_vads import (
    read_wav_16k,
    run_comprehensive_evaluation,
)

def get_workbench_dir() -> Path:
    return Path(os.environ.get("WORKBENCH_DATA_DIR", "data/workbench"))

def get_dataset_file() -> Path:
    return get_workbench_dir() / "dataset.json"

WORKBENCH_DATA_DIR = Path("data/workbench")
DATASET_FILE = WORKBENCH_DATA_DIR / "dataset.json"

app = FastAPI(title="Echoes Audio Workbench", description="Desktop labeling and multi-model benchmarking")

# In-memory and persistent dataset storage
class ChunkRecord(BaseModel):
    id: str
    session_id: str = ""
    seq: int = 0
    file: str
    duration_s: float = 0.0
    recorded_at: str = ""
    rms_dbfs: float = -99.0
    scene: str = "谈话"
    has_speech: bool = True
    should_detect: bool = True
    speaker: str = ""
    speech_intervals: str = ""
    labeled: bool = False
    notes: str = ""

def load_dataset() -> dict:
    wb_dir = get_workbench_dir()
    wb_dir.mkdir(parents=True, exist_ok=True)
    ds_file = get_dataset_file()
    if ds_file.is_file():
        try:
            return json.loads(ds_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Initialize default structure
    default_data = {
        "scenes": ["对话", "喧闹", "安静", "讲课", "会议", "户外"],
        "speakers": ["我", "张老师", "未知路人"],
        "chunks": [],
    }
    save_dataset(default_data)
    return default_data

def save_dataset(data: dict) -> None:
    wb_dir = get_workbench_dir()
    wb_dir.mkdir(parents=True, exist_ok=True)
    get_dataset_file().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def import_zip_archive(zip_path: Path) -> int:
    dataset = load_dataset()
    wb_dir = get_workbench_dir()
    chunks_dir = wb_dir / "audio"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    imported_count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Try finding session.json for session metadata
        session_info = {}
        for n in zf.namelist():
            if n.endswith("session.json"):
                try:
                    session_info = json.loads(zf.read(n).decode("utf-8"))
                except Exception:
                    pass

        # Try finding scene_tag.txt
        default_scene = "对话"
        for n in zf.namelist():
            if n.endswith("scene_tag.txt"):
                try:
                    txt = zf.read(n).decode("utf-8").strip()
                    if txt: default_scene = txt
                except Exception:
                    pass

        wav_names = [n for n in zf.namelist() if n.lower().endswith(".wav") and not n.startswith("__MACOSX")]
        wav_names.sort(key=lambda x: [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", x)])

        start_time_base = session_info.get("started_at_ms", int(time.time() * 1000))

        for idx, wname in enumerate(wav_names):
            cid = f"chunk_{Path(wname).stem}_{uuid.uuid4().hex[:6]}"
            target_wav = chunks_dir / f"{cid}.wav"
            target_wav.write_bytes(zf.read(wname))

            samples = read_wav_16k(target_wav)
            dur = round(len(samples) / 16000.0, 2)
            f_samples = samples.astype(np.float64) / 32768.0
            rms = float(np.sqrt(np.mean(f_samples * f_samples)))
            rms_dbfs = round(20.0 * math.log10(max(rms, 1e-6)), 2)

            # Estimate chunk timestamp
            chunk_time_ms = start_time_base + int(idx * dur * 1000)
            iso_time = datetime.datetime.fromtimestamp(chunk_time_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")

            record = {
                "id": cid,
                "session_id": session_info.get("id", "imported_session"),
                "seq": idx,
                "file": str(target_wav.relative_to(wb_dir)),
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

    save_dataset(dataset)
    return imported_count

# --- API Endpoints ---

@app.get("/api/dataset")
def get_dataset():
    return load_dataset()

@app.get("/api/chunks")
def get_chunks():
    data = load_dataset()
    return {"chunks": data.get("chunks", []), "scenes": data.get("scenes", []), "speakers": data.get("speakers", [])}

@app.get("/api/audio/{chunk_id}")
def get_chunk_audio(chunk_id: str):
    data = load_dataset()
    chunk = next((c for c in data.get("chunks", []) if c["id"] == chunk_id), None)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    path = get_workbench_dir() / chunk["file"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Audio file not found: {path}")
    return StreamingResponse(open(path, "rb"), media_type="audio/wav")

@app.post("/api/chunk/{chunk_id}/label")
def update_chunk_label(chunk_id: str, label_data: dict):
    data = load_dataset()
    chunk = next((c for c in data.get("chunks", []) if c["id"] == chunk_id), None)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    for k in ["scene", "has_speech", "should_detect", "speaker", "speech_intervals", "notes"]:
        if k in label_data:
            chunk[k] = label_data[k]
    chunk["labeled"] = True

    # Persist newly introduced scenes or speakers into the dataset library
    sc = chunk.get("scene")
    if sc and sc not in data["scenes"]:
        data["scenes"].append(sc)
    spk = chunk.get("speaker")
    if spk and spk not in data["speakers"] and spk not in ("未知路人", "未知", "none"):
        data["speakers"].append(spk)

    save_dataset(data)
    return {"status": "ok", "chunk": chunk}

@app.post("/api/scene/add")
def add_scene(scene: str = Form(...)):
    s = scene.strip()
    if not s:
        raise HTTPException(status_code=400, detail="Scene name cannot be empty")
    data = load_dataset()
    if s not in data["scenes"]:
        data["scenes"].append(s)
        save_dataset(data)
    return {"status": "ok", "scenes": data["scenes"]}

@app.post("/api/speaker/add")
def add_speaker(speaker: str = Form(...)):
    spk = speaker.strip()
    if not spk:
        raise HTTPException(status_code=400, detail="Speaker name cannot be empty")
    data = load_dataset()
    if spk not in data["speakers"]:
        data["speakers"].append(spk)
        save_dataset(data)
    return {"status": "ok", "speakers": data["speakers"]}

@app.post("/api/upload_zip")
async def upload_zip(file: UploadFile = File(...)):
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files are supported")
    temp_zip = get_workbench_dir() / f"upload_{uuid.uuid4().hex[:8]}.zip"
    try:
        with open(temp_zip, "wb") as f:
            shutil.copyfileobj(file.file, f)
        count = import_zip_archive(temp_zip)
    finally:
        if temp_zip.exists():
            temp_zip.unlink()
    return {"status": "ok", "imported_chunks": count}

@app.post("/api/evaluate")
def run_evaluation(sweep: bool = False):
    data = load_dataset()
    chunks = data.get("chunks", [])
    if not chunks:
        raise HTTPException(status_code=400, detail="Dataset is empty. Import chunks first.")

    # Execute comprehensive evaluation
    report = run_comprehensive_evaluation(
        chunks,
        base_dir=get_workbench_dir(),
        speaker_rounds=20,
    )
    return report

# --- Single Page High Standard Web GUI HTML ---

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Echoes • 录音打标与模型选型工作台</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090B0E;
      --surface: #11141A;
      --surface-elevated: #161B22;
      --border: rgba(255, 255, 255, 0.08);
      --border-accent: rgba(20, 184, 166, 0.35);
      --text: #F0F6FC;
      --text-muted: #8B949E;
      --teal: #14B8A6;
      --teal-light: #2DD4BF;
      --teal-dim: rgba(20, 184, 166, 0.12);
      --rose: #F43F5E;
      --rose-dim: rgba(244, 63, 94, 0.12);
      --amber: #F59E0B;
      --radius: 12px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      height: 100vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    header {
      height: 56px;
      padding: 0 24px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 700;
      font-size: 16px;
      letter-spacing: -0.02em;
    }
    .brand-tag {
      background: var(--teal-dim);
      color: var(--teal-light);
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
    }
    .nav-tabs {
      display: flex;
      gap: 6px;
      background: var(--bg);
      padding: 4px;
      border-radius: 8px;
    }
    .nav-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 6px 14px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .nav-btn.active {
      background: var(--surface-elevated);
      color: var(--text);
    }
    .main-container {
      flex: 1;
      display: flex;
      overflow: hidden;
    }
    /* Sidebar */
    aside {
      width: 320px;
      background: var(--surface);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }
    .sidebar-header {
      padding: 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .sidebar-title {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      font-weight: 600;
    }
    .chunk-list {
      flex: 1;
      overflow-y: auto;
      padding: 8px;
    }
    .chunk-item {
      padding: 12px;
      border-radius: 8px;
      margin-bottom: 4px;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.12s ease;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .chunk-item:hover {
      background: var(--surface-elevated);
    }
    .chunk-item.active {
      background: var(--teal-dim);
      border-color: var(--border-accent);
    }
    .chunk-item-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .chunk-id {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 600;
      color: var(--text);
    }
    .chunk-badge {
      font-size: 10px;
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: 600;
    }
    .badge-labeled { background: rgba(20, 184, 166, 0.2); color: var(--teal-light); }
    .badge-pending { background: rgba(255, 255, 255, 0.06); color: var(--text-muted); }
    .chunk-meta {
      font-size: 11px;
      color: var(--text-muted);
      display: flex;
      gap: 8px;
    }
    /* Content Area */
    .workspace {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      padding: 28px 36px;
      gap: 24px;
    }
    .section-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    /* Waveform player */
    .player-card {
      background: var(--surface-elevated);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    canvas#waveform {
      width: 100%;
      height: 96px;
      background: rgba(0, 0, 0, 0.35);
      border-radius: 8px;
      cursor: pointer;
    }
    .player-controls {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .btn {
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 16px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .btn:hover { background: var(--surface-elevated); border-color: rgba(255, 255, 255, 0.2); }
    .btn-primary { background: var(--teal); color: #000; border-color: var(--teal-light); }
    .btn-primary:hover { background: var(--teal-light); }
    
    /* Dual Core Decision Switch */
    .decision-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    .toggle-card {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      transition: all 0.15s ease;
    }
    .toggle-card.active-teal {
      background: var(--teal-dim);
      border-color: var(--teal-light);
    }
    .toggle-card.active-rose {
      background: var(--rose-dim);
      border-color: var(--rose);
    }
    .toggle-title {
      font-size: 14px;
      font-weight: 600;
    }
    .toggle-sub {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 2px;
    }
    /* Chips */
    .chips-group {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .chip {
      padding: 6px 14px;
      border-radius: 20px;
      background: var(--bg);
      border: 1px solid var(--border);
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .chip:hover { color: var(--text); border-color: rgba(255, 255, 255, 0.2); }
    .chip.selected {
      background: var(--teal);
      color: #000;
      border-color: var(--teal-light);
    }
    .chip-input {
      background: transparent;
      border: 1px dashed var(--border);
      color: var(--text);
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 12px;
      outline: none;
      width: 110px;
    }
    .chip-input:focus { border-color: var(--teal); }

    /* Dashboard View */
    .dashboard-view {
      display: none;
      flex: 1;
      overflow-y: auto;
      padding: 28px 36px;
      gap: 24px;
      flex-direction: column;
    }
    .metrics-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
    }
    .metric-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
    }
    .metric-val {
      font-size: 28px;
      font-weight: 700;
      margin-top: 6px;
      color: var(--teal-light);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      padding: 12px 14px;
      text-align: left;
      border-bottom: 1px solid var(--border);
    }
    th { color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
    tr:hover td { background: var(--surface-elevated); }
    pre.env-box {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--teal-light);
      overflow-x: auto;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span>Echoes</span>
      <span class="brand-tag">Workbench</span>
    </div>
    <div class="nav-tabs">
      <button class="nav-btn active" id="tab-labeling" onclick="switchTab('labeling')">Chunk 打标工作台</button>
      <button class="nav-btn" id="tab-benchmark" onclick="switchTab('benchmark')">多日综合评测看板</button>
    </div>
    <div>
      <input type="file" id="zip-uploader" accept=".zip" style="display:none;" onchange="handleZipUpload(event)">
      <button class="btn btn-primary" onclick="document.getElementById('zip-uploader').click()">+ 导入会话分块包 (ZIP)</button>
    </div>
  </header>

  <div class="main-container">
    <!-- Sidebar: Chunks List -->
    <aside>
      <div class="sidebar-header">
        <span class="sidebar-title" id="chunks-count">Chunks (0)</span>
        <button class="btn" style="padding: 4px 8px; font-size:11px;" onclick="loadData()">刷新</button>
      </div>
      <div class="chunk-list" id="chunks-container"></div>
    </aside>

    <!-- Labeling Workspace -->
    <main class="workspace" id="labeling-view">
      <div class="player-card">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <h3 id="current-title" style="font-size:16px;">未选择切片</h3>
            <span id="current-meta" style="font-size:12px; color:var(--text-muted);">-</span>
          </div>
          <div style="display:flex; gap:8px;">
            <button class="btn" onclick="prevChunk()">◀ 上一块 (←)</button>
            <button class="btn btn-primary" id="play-btn" onclick="togglePlay()">播放 (Space)</button>
            <button class="btn" onclick="nextChunk()">下一块 (→) ▶</button>
          </div>
        </div>
        <canvas id="waveform"></canvas>
        <audio id="audio-player" style="display:none;" onended="onAudioEnded()"></audio>
      </div>

      <!-- Dual Core Decision Switch -->
      <div class="decision-grid">
        <div class="toggle-card" id="card-has-speech" onclick="toggleHasSpeech()">
          <div>
            <div class="toggle-title">1. 是否包含人声 (has_speech)</div>
            <div class="toggle-sub">防止 VAD 阈值过高漏掉真实说话声 (快捷键 V)</div>
          </div>
          <span id="badge-has-speech" class="chunk-badge badge-labeled">有人声</span>
        </div>
        <div class="toggle-card" id="card-should-detect" onclick="toggleShouldDetect()">
          <div>
            <div class="toggle-title">2. 是否该被 VAD 检出 (should_detect)</div>
            <div class="toggle-sub">防止 VAD 阈值过低保留环境杂音 (快捷键 D)</div>
          </div>
          <span id="badge-should-detect" class="chunk-badge badge-labeled">应检出</span>
        </div>
      </div>

      <!-- Scene Selection -->
      <div class="section-card">
        <h4 style="font-size:13px; text-transform:uppercase; color:var(--text-muted); letter-spacing:0.04em;">所属声学场景</h4>
        <div class="chips-group" id="scenes-chips"></div>
      </div>

      <!-- Speaker Selection -->
      <div class="section-card">
        <h4 style="font-size:13px; text-transform:uppercase; color:var(--text-muted); letter-spacing:0.04em;">声纹归属人物 (Speaker)</h4>
        <div class="chips-group" id="speakers-chips"></div>
      </div>

      <!-- Save & Proceed -->
      <div style="display:flex; justify-content:flex-end; gap:12px;">
        <button class="btn btn-primary" style="padding:10px 24px; font-size:14px;" onclick="saveAndNext()">保存并标记下一块 (Enter)</button>
      </div>
    </main>

    <!-- Benchmark Dashboard View -->
    <main class="dashboard-view" id="benchmark-view">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <h2>多日综合评测与模型黄金选型看板</h2>
          <p style="color:var(--text-muted); font-size:13px; margin-top:4px;">针对所有已打标 Chunks 运行全部 VAD 模型及声纹识别模型随机抽查</p>
        </div>
        <button class="btn btn-primary" id="run-eval-btn" onclick="triggerEvaluation()">⚡ 启动全量评测选型</button>
      </div>

      <div class="metrics-row" id="bench-metrics"></div>

      <div class="section-card">
        <h3>🏆 推荐生成配置 (.env)</h3>
        <pre class="env-box" id="env-output"># 运行评测后自动生成最优配置</pre>
      </div>

      <div class="section-card">
        <h3>📊 VAD 模型综合天梯排行榜</h3>
        <div style="overflow-x:auto;">
          <table id="vad-table">
            <thead>
              <tr><th>排名</th><th>模型</th><th>配置</th><th>准确率</th><th>人声召回率 (防漏检)</th><th>环境抑制率 (防误触)</th><th>F1得分</th><th>延迟</th><th>RTF</th></tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>

      <div class="section-card" id="speaker-card" style="display:none;">
        <h3>🎙️ 声纹识别模型评测 (Stratified Spot-Check 抽查)</h3>
        <div style="overflow-x:auto;">
          <table id="speaker-table">
            <thead>
              <tr><th>模型</th><th>维度</th><th>Top-1 准确率</th><th>已知识别率</th><th>路人拒识率</th><th>误认率 (FAR)</th><th>拒认率 (FRR)</th><th>区分度 Gap</th><th>延迟</th></tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </main>
  </div>

  <script>
    let state = {
      chunks: [],
      scenes: [],
      speakers: [],
      currentIndex: -1,
      currentChunk: null,
      audioCtx: null,
      audioBuffer: null,
    };

    async function loadData() {
      const resp = await fetch('/api/chunks');
      const data = await resp.json();
      state.chunks = data.chunks;
      state.scenes = data.scenes;
      state.speakers = data.speakers;
      document.getElementById('chunks-count').textContent = `Chunks (${state.chunks.length})`;
      renderSidebar();
      renderChips();
      if (state.chunks.length > 0 && state.currentIndex === -1) {
        selectChunk(0);
      }
    }

    function renderSidebar() {
      const container = document.getElementById('chunks-container');
      container.innerHTML = state.chunks.map((c, i) => `
        <div class="chunk-item ${i === state.currentIndex ? 'active' : ''}" onclick="selectChunk(${i})">
          <div class="chunk-item-row">
            <span class="chunk-id">${c.id.split('_').slice(0, 2).join('_')}</span>
            <span class="chunk-badge ${c.labeled ? 'badge-labeled' : 'badge-pending'}">${c.labeled ? '已标注' : '未标注'}</span>
          </div>
          <div class="chunk-meta">
            <span>⏱ ${c.duration_s}s</span>
            <span>📍 ${c.scene}</span>
            <span>${c.has_speech ? '🔊有人声' : '🔇静音'}</span>
          </div>
        </div>
      `).join('');
    }

    function renderChips() {
      const sContainer = document.getElementById('scenes-chips');
      sContainer.innerHTML = state.scenes.map(s => `
        <div class="chip ${state.currentChunk?.scene === s ? 'selected' : ''}" onclick="setField('scene', '${s}')">${s}</div>
      `).join('') + `<input class="chip-input" placeholder="+ 自定义场景" onkeydown="handleNewScene(event)">`;

      const spkContainer = document.getElementById('speakers-chips');
      spkContainer.innerHTML = state.speakers.map(spk => `
        <div class="chip ${state.currentChunk?.speaker === spk ? 'selected' : ''}" onclick="setField('speaker', '${spk}')">${spk}</div>
      `).join('') + `<input class="chip-input" placeholder="+ 新增人物" onkeydown="handleNewSpeaker(event)">`;
    }

    async function selectChunk(index) {
      if (index < 0 || index >= state.chunks.length) return;
      state.currentIndex = index;
      state.currentChunk = { ...state.chunks[index] };
      renderSidebar();
      renderChips();
      updateFormFields();

      // Audio setup
      const player = document.getElementById('audio-player');
      player.src = `/api/audio/${state.currentChunk.id}`;
      drawWaveformPlaceholder();
      loadAudioWaveform(player.src);
    }

    function updateFormFields() {
      const c = state.currentChunk;
      document.getElementById('current-title').textContent = `${c.id} (${c.recorded_at || '时间未记录'})`;
      document.getElementById('current-meta').textContent = `时长: ${c.duration_s}s | 能量: ${c.rms_dbfs} dBFS | 会话: ${c.session_id}`;

      // Update VAD decision switches
      const cardSpeech = document.getElementById('card-has-speech');
      const badgeSpeech = document.getElementById('badge-has-speech');
      if (c.has_speech) {
        cardSpeech.className = 'toggle-card active-teal';
        badgeSpeech.className = 'chunk-badge badge-labeled';
        badgeSpeech.textContent = '有人声';
      } else {
        cardSpeech.className = 'toggle-card';
        badgeSpeech.className = 'chunk-badge badge-pending';
        badgeSpeech.textContent = '无人声';
      }

      const cardDetect = document.getElementById('card-should-detect');
      const badgeDetect = document.getElementById('badge-should-detect');
      if (c.should_detect) {
        cardDetect.className = 'toggle-card active-teal';
        badgeDetect.className = 'chunk-badge badge-labeled';
        badgeDetect.textContent = '应被 VAD 检出';
      } else {
        cardDetect.className = 'toggle-card active-rose';
        badgeDetect.className = 'chunk-badge';
        badgeDetect.style.background = 'rgba(244,63,94,0.2)';
        badgeDetect.style.color = '#F43F5E';
        badgeDetect.textContent = '不应检出 (过滤)';
      }
    }

    function toggleHasSpeech() {
      if (!state.currentChunk) return;
      state.currentChunk.has_speech = !state.currentChunk.has_speech;
      if (!state.currentChunk.has_speech) {
        state.currentChunk.should_detect = false;
      }
      updateFormFields();
    }

    function toggleShouldDetect() {
      if (!state.currentChunk) return;
      state.currentChunk.should_detect = !state.currentChunk.should_detect;
      updateFormFields();
    }

    function setField(field, val) {
      if (!state.currentChunk) return;
      state.currentChunk[field] = val;
      renderChips();
    }

    async function handleNewScene(e) {
      if (e.key === 'Enter' && e.target.value.trim()) {
        const val = e.target.value.trim();
        const form = new FormData();
        form.append('scene', val);
        await fetch('/api/scene/add', { method: 'POST', body: form });
        state.scenes.push(val);
        setField('scene', val);
        e.target.value = '';
      }
    }

    async function handleNewSpeaker(e) {
      if (e.key === 'Enter' && e.target.value.trim()) {
        const val = e.target.value.trim();
        const form = new FormData();
        form.append('speaker', val);
        await fetch('/api/speaker/add', { method: 'POST', body: form });
        state.speakers.push(val);
        setField('speaker', val);
        e.target.value = '';
      }
    }

    async function saveAndNext() {
      if (!state.currentChunk) return;
      await fetch(`/api/chunk/${state.currentChunk.id}/label`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state.currentChunk),
      });
      state.chunks[state.currentIndex] = { ...state.currentChunk, labeled: true };
      renderSidebar();
      nextChunk();
    }

    function nextChunk() {
      if (state.currentIndex < state.chunks.length - 1) {
        selectChunk(state.currentIndex + 1);
      }
    }

    function prevChunk() {
      if (state.currentIndex > 0) {
        selectChunk(state.currentIndex - 1);
      }
    }

    function togglePlay() {
      const p = document.getElementById('audio-player');
      const btn = document.getElementById('play-btn');
      if (p.paused) {
        p.play();
        btn.textContent = '暂停 (Space)';
      } else {
        p.pause();
        btn.textContent = '播放 (Space)';
      }
    }

    function onAudioEnded() {
      document.getElementById('play-btn').textContent = '播放 (Space)';
    }

    // Canvas Waveform renderer
    async function loadAudioWaveform(url) {
      try {
        if (!state.audioCtx) state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const resp = await fetch(url);
        const arrayBuf = await resp.arrayBuffer();
        state.audioBuffer = await state.audioCtx.decodeAudioData(arrayBuf);
        drawWaveform();
      } catch (e) {
        console.error(e);
      }
    }

    function drawWaveformPlaceholder() {
      const cvs = document.getElementById('waveform');
      const ctx = cvs.getContext('2d');
      ctx.clearRect(0, 0, cvs.width, cvs.height);
    }

    function drawWaveform() {
      const cvs = document.getElementById('waveform');
      cvs.width = cvs.offsetWidth * 2;
      cvs.height = cvs.offsetHeight * 2;
      const ctx = cvs.getContext('2d');
      ctx.clearRect(0, 0, cvs.width, cvs.height);

      if (!state.audioBuffer) return;
      const data = state.audioBuffer.getChannelData(0);
      const step = Math.ceil(data.length / cvs.width);
      const amp = cvs.height / 2;

      ctx.fillStyle = '#14B8A6';
      for (let i = 0; i < cvs.width; i++) {
        let min = 1.0, max = -1.0;
        for (let j = 0; j < step; j++) {
          const datum = data[(i * step) + j];
          if (datum < min) min = datum;
          if (datum > max) max = datum;
        }
        ctx.fillRect(i, (1 + min) * amp, 1, Math.max(1, (max - min) * amp));
      }
    }

    // Keybindings
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT') return;
      if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
      else if (e.code === 'ArrowRight') { e.preventDefault(); nextChunk(); }
      else if (e.code === 'ArrowLeft') { e.preventDefault(); prevChunk(); }
      else if (e.code === 'KeyV') { e.preventDefault(); toggleHasSpeech(); }
      else if (e.code === 'KeyD') { e.preventDefault(); toggleShouldDetect(); }
      else if (e.code === 'Enter') { e.preventDefault(); saveAndNext(); }
    });

    async function handleZipUpload(e) {
      const file = e.target.files[0];
      if (!file) return;
      const formData = new FormData();
      formData.append('file', file);
      const resp = await fetch('/api/upload_zip', { method: 'POST', body: formData });
      const res = await resp.json();
      alert(`导入成功！共载入 ${res.imported_chunks} 个录音分块。`);
      loadData();
    }

    function switchTab(tab) {
      document.getElementById('tab-labeling').className = `nav-btn ${tab === 'labeling' ? 'active' : ''}`;
      document.getElementById('tab-benchmark').className = `nav-btn ${tab === 'benchmark' ? 'active' : ''}`;
      document.getElementById('labeling-view').style.display = tab === 'labeling' ? 'flex' : 'none';
      document.getElementById('benchmark-view').style.display = tab === 'benchmark' ? 'flex' : 'none';
      if (tab === 'benchmark') triggerEvaluation();
    }

    async function triggerEvaluation() {
      const btn = document.getElementById('run-eval-btn');
      btn.textContent = '正在全模型综合评测中...';
      btn.disabled = true;
      try {
        const resp = await fetch('/api/evaluate', { method: 'POST' });
        const report = await resp.json();
        renderEvaluation(report);
      } catch (e) {
        alert('评测失败: ' + e.message);
      } finally {
        btn.textContent = '⚡ 启动全量评测选型';
        btn.disabled = false;
      }
    }

    function renderEvaluation(report) {
      const best = report.best_vad;
      document.getElementById('bench-metrics').innerHTML = `
        <div class="metric-card">
          <div style="font-size:12px; color:var(--text-muted);">最优 VAD 模型</div>
          <div class="metric-val">${best ? best.model : '-'}</div>
          <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">${best ? best.config : ''}</div>
        </div>
        <div class="metric-card">
          <div style="font-size:12px; color:var(--text-muted);">Chunk 判定准确率</div>
          <div class="metric-val">${best ? (best.chunk_accuracy * 100).toFixed(1) + '%' : '-'}</div>
          <div style="font-size:11px; color:var(--teal-light); margin-top:4px;">F1: ${best ? best.f1_score.toFixed(4) : '-'}</div>
        </div>
        <div class="metric-card">
          <div style="font-size:12px; color:var(--text-muted);">人声检出召回率 (防漏检)</div>
          <div class="metric-val">${best ? (best.speech_recall * 100).toFixed(1) + '%' : '-'}</div>
          <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">漏检: ${best ? best.true_speech_missed : 0} 块</div>
        </div>
        <div class="metric-card">
          <div style="font-size:12px; color:var(--text-muted);">环境噪音抑制率 (防误触)</div>
          <div class="metric-val">${best ? (best.noise_rejection * 100).toFixed(1) + '%' : '-'}</div>
          <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">误触: ${best ? best.noise_false_alarms : 0} 块</div>
        </div>
      `;

      document.getElementById('env-output').textContent = report.recommended_env || '# 无可用配置';

      const tbody = document.querySelector('#vad-table tbody');
      tbody.innerHTML = (report.vad_rankings || []).map((r, i) => `
        <tr>
          <td>${i + 1}</td>
          <td><code>${r.model}</code></td>
          <td>${r.config}</td>
          <td><strong>${(r.chunk_accuracy * 100).toFixed(1)}%</strong></td>
          <td>${(r.speech_recall * 100).toFixed(1)}%</td>
          <td>${(r.noise_rejection * 100).toFixed(1)}%</td>
          <td>${r.f1_score.toFixed(4)}</td>
          <td>${r.latency_ms.toFixed(1)}ms</td>
          <td>${r.rtf.toFixed(4)}</td>
        </tr>
      `).join('');

      const spk = report.speaker_evaluation;
      const spkCard = document.getElementById('speaker-card');
      if (spk && spk.models) {
        spkCard.style.display = 'flex';
        const sbody = document.querySelector('#speaker-table tbody');
        sbody.innerHTML = spk.models.map(m => `
          <tr>
            <td><code>${m.model}</code></td>
            <td>${m.dim}</td>
            <td><strong>${(m.best_config.accuracy * 100).toFixed(1)}%</strong></td>
            <td>${(m.best_config.known_recall * 100).toFixed(1)}%</td>
            <td>${(m.best_config.unknown_rejection * 100).toFixed(1)}%</td>
            <td>${(m.best_config.far * 100).toFixed(1)}%</td>
            <td>${(m.best_config.frr * 100).toFixed(1)}%</td>
            <td>+${m.discrimination_gap.toFixed(4)}</td>
            <td>${m.latency_ms.toFixed(1)}ms</td>
          </tr>
        `).join('');
      } else {
        spkCard.style.display = 'none';
      }
    }

    loadData();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML_TEMPLATE)

def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Echoes Audio Workbench Web GUI")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    parser.add_argument("--import-zip", type=Path, help="Pre-import a session ZIP archive on start")
    args = parser.parse_args()

    if args.import_zip and args.import_zip.is_file():
        print(f"Importing {args.import_zip}...")
        cnt = import_zip_archive(args.import_zip)
        print(f"Imported {cnt} chunks.")

    print(f"\n=======================================================")
    print(f"  Echoes Audio Workbench Web GUI started!")
    print(f"  Open your browser: http://{args.host}:{args.port}")
    print(f"=======================================================\n")
    uvicorn.run(app, host=args.host, port=args.port)

if __name__ == "__main__":
    main()
