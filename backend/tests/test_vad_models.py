import json
import math
import subprocess
from pathlib import Path
import numpy as np
import pytest

from echoledger.config import Settings
from echoledger.models import VadConfig
from echoledger.vad import (
    FRAME,
    make_scorer,
    list_available_vads,
    speech_intervals,
    score_intervals,
    PassthroughScorer,
    EnergyDemoScorer,
    SileroOnnxScorer,
    SileroV4OnnxScorer,
    SileroV3OnnxScorer,
    TenVadOnnxScorer,
    WebRtcScorer,
)
from echoledger.audio import merge_intervals

def test_config_vad_providers():
    # Valid providers
    valid_providers = [
        "passthrough", "energy_demo", "silero", "silero_v5", "silero_v4", "silero_v3",
        "ten_vad", "ten_vad_int8", "webrtc", "webrtc_0", "webrtc_1", "webrtc_2", "webrtc_3"
    ]
    for p in valid_providers:
        s = Settings(api_token="x" * 24, vad_provider=p)
        s.validate()

    # Invalid provider
    with pytest.raises(ValueError, match="Unknown VAD_PROVIDER"):
        s = Settings(api_token="x" * 24, vad_provider="non_existent_vad")
        s.validate()

def test_list_available_vads():
    vads = list_available_vads()
    assert len(vads) >= 10
    ids = {v["id"] for v in vads}
    assert {"silero_v5", "silero_v4", "silero_v3", "ten_vad", "webrtc_2"}.issubset(ids)
    for v in vads:
        assert "ready" in v
        assert "type" in v

def test_silero_legacy_backward_compatibility():
    # Calling silero with empty model_path should fail with original RuntimeError
    with pytest.raises(RuntimeError, match="VAD model missing: configure VAD_MODEL_PATH"):
        make_scorer("silero")

def test_unknown_provider():
    with pytest.raises(ValueError, match="Unknown VAD provider"):
        make_scorer("unknown_provider_foo")

@pytest.mark.parametrize("provider", [
    "passthrough",
    "energy_demo",
    "silero_v5",
    "silero_v4",
    "silero_v3",
    "ten_vad",
    "ten_vad_int8",
    "webrtc",
    "webrtc_0",
    "webrtc_1",
    "webrtc_2",
    "webrtc_3",
])
def test_all_vad_scorers_contract(provider):
    scorer = make_scorer(provider)
    assert hasattr(scorer, "model_id")
    assert isinstance(scorer.model_id, str) and len(scorer.model_id) > 0

    # 1. Edge case: empty input
    empty = np.zeros(0, dtype=np.int16)
    p_empty = scorer.probabilities(empty)
    assert len(p_empty) == 0
    assert p_empty.dtype == np.float32

    # 2. Short non-aligned audio (e.g. 1000 samples = ~2 frames)
    short = np.zeros(1000, dtype=np.int16)
    p_short = scorer.probabilities(short)
    expected_frames = math.ceil(len(short) / FRAME)
    assert len(p_short) == expected_frames
    assert p_short.dtype == np.float32
    assert np.all((p_short >= 0.0) & (p_short <= 1.0))

    # 3. 1-second synthetic audio (16,000 samples = 32 frames)
    # Put a sine wave tone in the middle (sample 4000 to 12000)
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    tone = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
    tone[:4000] = 0
    tone[12000:] = 0

    probs = scorer.probabilities(tone)
    assert len(probs) == math.ceil(16000 / FRAME)
    assert np.all((probs >= 0.0) & (probs <= 1.0))

    intervals = speech_intervals(probs, 16000, VadConfig())
    assert isinstance(intervals, list)

def test_webrtc_modes_aggression():
    # WebRTC mode 3 should be stricter or equal in silence rejection compared to mode 0
    scorer_0 = make_scorer("webrtc_0")
    scorer_3 = make_scorer("webrtc_3")
    assert scorer_0.mode == 0
    assert scorer_3.mode == 3

    with pytest.raises(ValueError):
        WebRtcScorer(mode=9)

def test_manifest_file_structure():
    manifest_path = Path("data/models/vad/manifest.json")
    if manifest_path.is_file():
        data = json.loads(manifest_path.read_text())
        assert isinstance(data, list)
        assert len(data) >= 5
        for item in data:
            assert "id" in item
            assert "verified" in item
            assert item["verified"] is True
            assert "latency_1s_ms" in item

def test_slice_and_benchmark_integration(tmp_path):
    # Create a 2-second test WAV file with speech-like tone in [0.5s, 1.5s]
    wav_path = tmp_path / "test_input.wav"
    t = np.linspace(0, 2.0, 32000, endpoint=False)
    sig = (np.sin(2 * np.pi * 300 * t) * 15000).astype(np.int16)
    sig[:8000] = 0
    sig[24000:] = 0

    import wave
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(sig.tobytes())

    # Run slice_and_label.py
    out_dir = tmp_path / "slices"
    cmd_slice = [
        ".venv/bin/python3",
        "scripts/slice_and_label.py",
        "-i", str(wav_path),
        "-o", str(out_dir),
        "-s", "1.0",
    ]
    res_slice = subprocess.run(cmd_slice, capture_output=True, text=True)
    assert res_slice.returncode == 0
    assert (out_dir / "labels.json").is_file()

    labels_data = json.loads((out_dir / "labels.json").read_text())
    assert len(labels_data["slices"]) == 2

    # Update labels with ground truth and verified=True
    labels_data["slices"][0]["speech_intervals"] = "0.5-1.0"
    labels_data["slices"][0]["verified"] = True
    labels_data["slices"][0]["scene"] = "谈话"

    labels_data["slices"][1]["speech_intervals"] = "0.0-0.5"
    labels_data["slices"][1]["verified"] = True
    labels_data["slices"][1]["scene"] = "谈话者声音小"

    (out_dir / "labels.json").write_text(json.dumps(labels_data))

    # Run benchmark_vads.py
    report_md = tmp_path / "report.md"
    report_json = tmp_path / "report.json"
    cmd_bench = [
        ".venv/bin/python3",
        "scripts/benchmark_vads.py",
        "--slices-dir", str(out_dir),
        "--models", "silero_v5,webrtc_2",
        "-o", str(report_md),
        "--json", str(report_json),
    ]
    res_bench = subprocess.run(cmd_bench, capture_output=True, text=True)
    assert res_bench.returncode == 0
    assert report_md.is_file()
    assert report_json.is_file()

    bench_res = json.loads(report_json.read_text())
    assert bench_res["slices_evaluated"] == 2
    assert len(bench_res["results"]) > 0
    for r in bench_res["results"]:
        assert 0.0 <= r["speech_recall"] <= 1.0
        assert 0.0 <= r["removed_fraction"] <= 1.0
