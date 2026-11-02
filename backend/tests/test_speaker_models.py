import io
import json
import math
from pathlib import Path
import sys
import wave
import zipfile
import numpy as np
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from echoledger.speakers import (
    normalize,
    match_speaker,
    list_available_speaker_models,
    make_speaker_embedder,
    spot_check_speaker_models,
    SPEAKER_MODEL_FILES,
)
from scripts.benchmark_vads import read_wav_16k
from scripts.workbench_gui import app, load_dataset, save_dataset, get_workbench_dir
from scripts.workbench_tui import (
    load_tui_dataset,
    save_tui_dataset,
    import_zip_to_dataset,
    run_tui_benchmark,
)

def create_synthetic_wav_bytes(duration_s: float = 1.0, freq: float = 440.0) -> bytes:
    n_samples = int(duration_s * 16000)
    t = np.linspace(0, duration_s, n_samples, endpoint=False)
    sig = (np.sin(2 * np.pi * freq * t) * 16000).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(sig.tobytes())
    return buf.getvalue()

def test_list_available_speaker_models():
    models = list_available_speaker_models()
    assert len(models) == 4
    model_ids = {m["id"] for m in models}
    assert model_ids == {"campplus_cn", "eres2net_cn", "wespeaker_resnet34", "nemo_speakernet"}

    expected_dims = {
        "campplus_cn": 192,
        "eres2net_cn": 192,
        "wespeaker_resnet34": 256,
        "nemo_speakernet": 256,
    }

    for m in models:
        assert m["dim"] == expected_dims[m["id"]]
        assert "name" in m
        assert "description" in m
        assert "model_path" in m
        assert "ready" in m
        assert m["ready"] is True  # Pre-installed in data/models/speakers/

def test_make_speaker_embedder_unknown_model():
    with pytest.raises(ValueError, match="Unknown speaker model"):
        make_speaker_embedder("non_existent_model_id")

    with pytest.raises(RuntimeError, match="Speaker.*model missing"):
        make_speaker_embedder("campplus_cn", model_path="non/existent/path.onnx")

@pytest.mark.parametrize("model_name,expected_dim", [
    ("campplus_cn", 192),
    ("wespeaker_resnet34", 256),
    ("nemo_speakernet", 256),
    ("eres2net_cn", 192),
])
def test_all_speaker_embedders_contract(model_name, expected_dim):
    embedder = make_speaker_embedder(model_name)
    assert embedder.model_id == model_name
    assert embedder.dim == expected_dim

    # 1. Edge case: empty input
    empty = np.zeros(0, dtype=np.int16)
    v_empty = embedder.extract(empty)
    assert len(v_empty) == expected_dim
    assert all(x == 0.0 for x in v_empty)

    # 2. Synthetic 1-second sine wave
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    sig = (np.sin(2 * np.pi * 300 * t) * 15000).astype(np.int16)
    vec = embedder.extract(sig)
    assert len(vec) == expected_dim
    assert isinstance(vec, list)
    assert all(isinstance(x, float) for x in vec)

    # 3. Normalization contract
    norm_vec = normalize(vec)
    assert isinstance(norm_vec, np.ndarray)
    assert norm_vec.shape == (expected_dim,)
    assert math.isclose(float(np.linalg.norm(norm_vec)), 1.0, rel_tol=1e-5)

def test_speaker_spot_check_real_audio():
    spk_dir = Path("data/models/speakers")
    chunks = [
        {"id": "leijun_enroll", "file": str(spk_dir / "leijun-sr-1.wav"), "speaker": "雷军"},
        {"id": "leijun_test", "file": str(spk_dir / "leijun-test-sr-1.wav"), "speaker": "雷军"},
        {"id": "liudehua_enroll", "file": str(spk_dir / "liudehua-sr-1.wav"), "speaker": "刘德华"},
        {"id": "liudehua_test", "file": str(spk_dir / "liudehua-test-sr-1.wav"), "speaker": "刘德华"},
        {"id": "fangjun_enroll", "file": str(spk_dir / "fangjun-sr-1.wav"), "speaker": "方俊"},
        {"id": "fangjun_test", "file": str(spk_dir / "fangjun-test-sr-1.wav"), "speaker": "方俊"},
    ]
    for c in chunks:
        c["samples"] = read_wav_16k(Path(c["file"]))

    # Test spot-check across fast models (Cam++, WeSpeaker, NeMo)
    eval_models = ["campplus_cn", "wespeaker_resnet34", "nemo_speakernet"]
    report = spot_check_speaker_models(chunks, models=eval_models, rounds=10, seed=123)

    assert "winner" in report
    assert report["winner"] in eval_models
    assert report["chunks_evaluated"] == 6
    assert set(report["known_speakers"]) == {"雷军", "刘德华", "方俊"}
    assert len(report["models"]) == len(eval_models)

    for m in report["models"]:
        assert m["dim"] in (192, 256)
        assert m["best_config"]["accuracy"] >= 0.90
        assert m["discrimination_gap"] > 0.30
        assert m["latency_ms"] > 0.0

def test_speaker_spot_check_edge_cases():
    # Empty chunks
    assert "error" in spot_check_speaker_models([])

    # < 3 valid chunks
    short_chunks = [
        {"id": "c1", "speaker": "A", "samples": np.zeros(16000, dtype=np.int16)},
        {"id": "c2", "speaker": "A", "samples": np.zeros(16000, dtype=np.int16)},
    ]
    assert "error" in spot_check_speaker_models(short_chunks)

    # Chunks without known speaker
    unknown_chunks = [
        {"id": "c1", "speaker": "未知", "samples": np.zeros(16000, dtype=np.int16)},
        {"id": "c2", "speaker": "路人", "samples": np.zeros(16000, dtype=np.int16)},
        {"id": "c3", "speaker": "", "samples": np.zeros(16000, dtype=np.int16)},
    ]
    assert "error" in spot_check_speaker_models(unknown_chunks)

def test_workbench_gui_api_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    # 1. Root HTML
    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "Echoes" in r_root.text

    # 2. Get empty chunks
    r_chunks = client.get("/api/chunks")
    assert r_chunks.status_code == 200
    data = r_chunks.json()
    assert data["chunks"] == []
    assert "对话" in data["scenes"]

    # 3. Add scene & add speaker
    r_sc = client.post("/api/scene/add", data={"scene": "安静书房"})
    assert r_sc.status_code == 200
    assert "安静书房" in r_sc.json()["scenes"]

    r_sc_bad = client.post("/api/scene/add", data={"scene": "   "})
    assert r_sc_bad.status_code == 400

    r_spk = client.post("/api/speaker/add", data={"speaker": "雷军"})
    assert r_spk.status_code == 200
    assert "雷军" in r_spk.json()["speakers"]

    r_spk_bad = client.post("/api/speaker/add", data={"speaker": "   "})
    assert r_spk_bad.status_code == 400

    # 4. Upload zip archive with 2 chunks
    wav1 = create_synthetic_wav_bytes(duration_s=1.0, freq=300.0)
    wav2 = create_synthetic_wav_bytes(duration_s=1.2, freq=600.0)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("session.json", json.dumps({"id": "sess_test", "started_at_ms": 1700000000000}))
        zf.writestr("scene_tag.txt", "安静书房")
        zf.writestr("00000000.wav", wav1)
        zf.writestr("00000001.wav", wav2)
    zip_bytes = zip_buf.getvalue()

    r_up = client.post(
        "/api/upload_zip",
        files={"file": ("session_test.zip", zip_bytes, "application/zip")},
    )
    assert r_up.status_code == 200
    assert r_up.json()["imported_chunks"] == 2

    # 5. Get chunks and verify fields
    r_chunks_after = client.get("/api/chunks")
    chunks = r_chunks_after.json()["chunks"]
    assert len(chunks) == 2
    c0 = chunks[0]
    assert c0["scene"] == "安静书房"
    assert c0["duration_s"] == 1.0

    # 6. Stream chunk audio
    r_audio = client.get(f"/api/audio/{c0['id']}")
    assert r_audio.status_code == 200
    assert len(r_audio.content) == len(wav1)

    r_audio_404 = client.get("/api/audio/non_existent_id")
    assert r_audio_404.status_code == 404

    # 7. Update chunk label
    r_lbl = client.post(f"/api/chunk/{c0['id']}/label", json={
        "scene": "深度研讨",
        "has_speech": True,
        "should_detect": True,
        "speaker": "雷军",
        "notes": "音质清晰",
    })
    assert r_lbl.status_code == 200
    updated = r_lbl.json()["chunk"]
    assert updated["labeled"] is True
    assert updated["scene"] == "深度研讨"
    assert updated["speaker"] == "雷军"

    # 8. Evaluate populated dataset
    r_eval = client.post("/api/evaluate")
    assert r_eval.status_code == 200
    eval_rep = r_eval.json()
    assert eval_rep["chunks_evaluated"] == 2
    assert "best_vad" in eval_rep
    assert "vad_rankings" in eval_rep

def test_workbench_tui_functions(tmp_path):
    dataset_file = tmp_path / "tui_data" / "dataset.json"

    # Test load & save default dataset
    ds = load_tui_dataset(dataset_file)
    assert ds["chunks"] == []
    assert "对话" in ds["scenes"]

    ds["scenes"].append("测试自定义场景")
    save_tui_dataset(ds, dataset_file)
    reloaded = load_tui_dataset(dataset_file)
    assert "测试自定义场景" in reloaded["scenes"]

    # Test zip import
    wav_bytes = create_synthetic_wav_bytes(duration_s=1.0)
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("00000000.wav", wav_bytes)
        zf.writestr("00000001.wav", wav_bytes)

    imported_cnt = import_zip_to_dataset(zip_path, dataset_file)
    assert imported_cnt == 2
    ds_imported = load_tui_dataset(dataset_file)
    assert len(ds_imported["chunks"]) == 2

    # Test TUI benchmark execution
    rep_md = tmp_path / "report.md"
    rep = run_tui_benchmark(ds_imported, base_dir=dataset_file.parent, output_path=rep_md)
    assert rep["chunks_evaluated"] == 2
    assert rep_md.is_file()
    assert "# Echoes Multi-Day VAD" in rep_md.read_text(encoding="utf-8")
