"""End-to-end unit & integration tests for Hemory AAC Segment Pipeline & Day Timeline."""

from __future__ import annotations

import datetime as dt
import io
import json
import math
import subprocess
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.hemory_pipeline import encode_pcm16_to_aac
from echoledger.models import AsrSegment
from echoledger.providers import Transcript


def _make_speech_like_pcm(duration_sec: float = 4.0, freq: float = 220.0, amp: float = 9000.0) -> np.ndarray:
    """Synthesize high-SNR modulated harmonic audio with background floor."""
    sr = 16000
    n = int(sr * duration_sec)
    t = np.arange(n, dtype=np.float64) / sr
    # Speech-like envelope: 20% quiet pause at start/end so top-20% vs bottom-20% SNR >> 12dB
    env = np.ones(n, dtype=np.float64)
    quiet = max(1, int(0.22 * n))
    env[:quiet] = 0.002
    env[-quiet:] = 0.002
    signal = amp * env * (0.65 * np.sin(2 * math.pi * freq * t) + 0.35 * np.sin(2 * math.pi * (freq * 1.9) * t))
    return np.clip(signal, -30000, 30000).astype(np.int16)


def _make_silent_pcm(duration_sec: float = 3.0) -> np.ndarray:
    sr = 16000
    n = int(sr * duration_sec)
    rng = np.random.default_rng(7)
    return rng.integers(-15, 16, size=n, dtype=np.int16)


def _pcm_to_adts_aac(samples: np.ndarray) -> bytes:
    aac = encode_pcm16_to_aac(samples)
    assert len(aac) > 32, "ffmpeg ADTS AAC encoding failed in test fixture"
    return aac


class AlwaysSpeechScorer:
    model_id = "test-speech-vad"

    def probabilities(self, samples: np.ndarray) -> np.ndarray:
        n_frames = max(1, (len(samples) + 511) // 512)
        probs = np.zeros(n_frames, dtype=np.float32)
        for i in range(n_frames):
            chunk = samples[i * 512 : (i + 1) * 512]
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))) if len(chunk) else 0.0
            probs[i] = 0.92 if rms >= 150.0 else 0.02
        return probs


class ScriptedTranscriber:
    def __init__(self, script: list[list[AsrSegment]], is_demo: bool = False):
        self.script = list(script)
        self.calls = 0
        self.is_demo = is_demo

    def transcribe(self, samples: np.ndarray, language: str = "zh") -> Transcript:
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return Transcript(self.script[idx], "scripted-asr", self.is_demo)


def _upload_aac(
    client: TestClient,
    token: str,
    aac_bytes: bytes,
    started_at_iso: str,
    session_id: str,
    segment_index: int,
    tz: str = "Asia/Shanghai",
):
    meta = {
        "live_session_id": session_id,
        "segment_index": segment_index,
        "dp_instance_id": "dp_default",
        "split_reason": "window",
        "sample_rate": 16000,
        "bitrate_kbps": 48,
        "channels": 1,
    }
    return client.post(
        "/tenants/ten_default/vaults/vlt_default/recordings/upload",
        headers={
            "Authorization": f"Bearer {token}",
            "x-dp-instance-id": "dp_default",
        },
        data={
            "started_at": started_at_iso,
            "format": "aac",
            "source": "phone",
            "capture_timezone": tz,
            "capture_metadata": json.dumps(meta),
        },
        files={"file": (f"seg_{segment_index}.aac", io.BytesIO(aac_bytes), "audio/aac")},
    )


TEST_TOKEN = "hemory-test-secret-token-0123456789"


def test_silent_and_hallucinated_segments_produce_zero_junk_cards(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        api_token=TEST_TOKEN,
        asr_provider="mock",
        run_worker=False,
    )
    app = create_app(settings)
    transcriber = ScriptedTranscriber(
        [
            [AsrSegment(start_ms=0, end_ms=1200, text="System.", speaker_label="spk_0")],
            [AsrSegment(start_ms=0, end_ms=1500, text="Uh, what would I like to tell you?", speaker_label="spk_0")],
        ],
        is_demo=False,
    )
    app.state.pipeline._scorer = AlwaysSpeechScorer()
    app.state.pipeline.transcriber = transcriber
    app.state.pipeline._explicit_transcriber = True

    now_utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    t0 = (now_utc - dt.timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    t1 = (now_utc - dt.timedelta(minutes=18)).isoformat().replace("+00:00", "Z")
    t2 = (now_utc - dt.timedelta(minutes=16)).isoformat().replace("+00:00", "Z")

    with TestClient(app) as client:
        # 1. Pure silence AAC -> gated before ASR even runs
        r0 = _upload_aac(client, TEST_TOKEN, _pcm_to_adts_aac(_make_silent_pcm(3.0)), t0, "sess_clean", 0)
        assert r0.status_code == 201
        assert app.state.pipeline.tick() is True
        assert transcriber.calls == 0

        # 2. Speech energy present, but ASR outputs "System." hallucination -> filtered out
        r1 = _upload_aac(client, TEST_TOKEN, _pcm_to_adts_aac(_make_speech_like_pcm(3.5)), t1, "sess_clean", 1)
        assert r1.status_code == 201
        assert app.state.pipeline.tick() is True
        assert transcriber.calls == 1

        # 3. Speech energy present, ASR outputs "Uh, what would I like to tell you?" -> filtered out
        r2 = _upload_aac(client, TEST_TOKEN, _pcm_to_adts_aac(_make_speech_like_pcm(3.5)), t2, "sess_clean", 2)
        assert r2.status_code == 201
        assert app.state.pipeline.tick() is True
        assert transcriber.calls == 2

        local_day = now_utc.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d")
        day_resp = client.get(
            f"/timeline/day?date={local_day}&tz=Asia/Shanghai",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert day_resp.status_code == 200
        payload = day_resp.json()
        assert payload["chains"] == []
        assert payload["activities"] == []
        assert payload["scattered_chats"] == []

        clusters = client.get("/clusters?status=pending", headers={"Authorization": f"Bearer {TEST_TOKEN}"}).json()
        assert clusters["clusters"] == []


def test_scattered_chat_folding_vs_activity_aggregation_ascending_order_and_voiceprint(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        api_token=TEST_TOKEN,
        asr_provider="mock",
        run_worker=False,
    )
    app = create_app(settings)

    transcriber = ScriptedTranscriber(
        [
            # Segment 0 (earlier): Short isolated 1-liner -> should stay P3 ScatteredChat!
            [
                AsrSegment(
                    start_ms=200,
                    end_ms=2800,
                    text="大姐，打火机放哪儿了？",
                    speaker_label="spk_a",
                )
            ],
            # Segment 1 (15 min later): Multi-turn project discussion -> should become P2 Activity!
            [
                AsrSegment(
                    start_ms=100,
                    end_ms=2600,
                    text="我们今天把移动端时间线重构一下，改成从早到晚正序排列。",
                    speaker_label="spk_a",
                ),
                AsrSegment(
                    start_ms=2800,
                    end_ms=5400,
                    text="同意，零散的短句折叠成对话卡，真正的长对话再生成结构化摘要。",
                    speaker_label="spk_b",
                ),
                AsrSegment(
                    start_ms=5600,
                    end_ms=8200,
                    text="好，那接口联调完下午直接发测试包验收。",
                    speaker_label="spk_a",
                ),
            ],
        ],
        is_demo=False,
    )
    app.state.pipeline._scorer = AlwaysSpeechScorer()
    app.state.pipeline.transcriber = transcriber
    app.state.pipeline._explicit_transcriber = True

    # Pin timestamps inside the same Asia/Shanghai local day so ordering is deterministic
    shanghai = dt.timezone(dt.timedelta(hours=8))
    now_local = dt.datetime.now(shanghai).replace(second=0, microsecond=0)
    # Use 30 mins ago and 10 mins ago (ensuring same local date unless right after midnight)
    if now_local.hour == 0 and now_local.minute < 35:
        base_local = now_local.replace(hour=0, minute=2)
        t_early_dt = base_local
        t_late_dt = base_local + dt.timedelta(minutes=16)
    else:
        t_early_dt = now_local - dt.timedelta(minutes=30)
        t_late_dt = now_local - dt.timedelta(minutes=12)

    t_early_iso = t_early_dt.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    t_late_iso = t_late_dt.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    local_day = t_early_dt.strftime("%Y-%m-%d")

    speech_short = _pcm_to_adts_aac(_make_speech_like_pcm(4.0, freq=210.0))
    speech_long = _pcm_to_adts_aac(_make_speech_like_pcm(9.5, freq=210.0))

    with TestClient(app) as client:
        # Upload late segment FIRST and early segment SECOND to prove timeline sorts strictly ascending!
        u_late = _upload_aac(client, TEST_TOKEN, speech_long, t_late_iso, "sess_b", 0)
        u_early = _upload_aac(client, TEST_TOKEN, speech_short, t_early_iso, "sess_a", 0)
        assert u_late.status_code == 201
        assert u_early.status_code == 201

        assert app.state.pipeline.tick() is True
        assert app.state.pipeline.tick() is True
        assert app.state.pipeline.tick() is False

        day_resp = client.get(
            f"/timeline/day?date={local_day}&tz=Asia/Shanghai",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert day_resp.status_code == 200
        day = day_resp.json()

        # Verify P3 ScatteredChat has ONLY the short isolated remark
        assert len(day["scattered_chats"]) == 1
        scat = day["scattered_chats"][0]
        assert len(scat["snippets"]) == 1
        assert "打火机" in scat["snippets"][0]["text"]
        assert scat["snippets"][0]["audio_url"].startswith("/recordings/segments/seg_")

        # Verify P2 Activity has the multi-turn discussion, clean category (never UNKNOWN), and title != summary
        assert len(day["activities"]) == 1
        act = day["activities"][0]
        assert act["category"] != "unknown"
        assert act["title"] != act["summary"]
        assert act["utterance_count"] == 3
        assert scat["start_epoch_ms"] < act["start_epoch_ms"]
        assert scat["start_min"] <= act["start_min"]

        # Verify real audio streaming works on the recorded segment
        audio_resp = client.get(
            scat["snippets"][0]["audio_url"],
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert audio_resp.status_code == 200
        assert audio_resp.headers["content-type"].startswith("audio/aac")
        assert audio_resp.content[:2] in (b"\xff\xf1", b"\xff\xf9")

        # Verify pending voiceprint cluster has REAL disk audio preview
        clusters_resp = client.get("/clusters?status=pending", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        assert clusters_resp.status_code == 200
        pending = clusters_resp.json()["clusters"]
        assert len(pending) >= 1
        cid = pending[0]["cluster_id"]
        assert (settings.data_dir / "clusters" / f"{cid}.aac").is_file()

        prev_resp = client.get(f"/clusters/{cid}/audio_preview", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        assert prev_resp.status_code == 200
        assert len(prev_resp.content) > 100

        # Confirm cluster as "老张" and verify retroactive update across Event Detail & Day Timeline!
        patch_resp = client.patch(
            f"/clusters/{cid}",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            json={"action": "mark_new", "name": "老张"},
        )
        assert patch_resp.status_code == 200

        detail_resp = client.get(
            f"/memory-events/{act['event_id']}",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert len(detail["utterances"]) == 3
        assert any(u["speaker_name"] == "老张" for u in detail["utterances"]) or "老张" in (
            client.get(
                f"/timeline/day?date={local_day}&tz=Asia/Shanghai",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            ).json()["scattered_chats"][0]["snippets"][0]["speaker"]
        )

        # Verify FTS5 memory recall finds the transcribed utterances for SSE Chat!
        recalled = app.state.db.search_sections("vlt_default", query="时间线")
        assert len(recalled) >= 1
        assert "时间线" in recalled[0]["content"]
