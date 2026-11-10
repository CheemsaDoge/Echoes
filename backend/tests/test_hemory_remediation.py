from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.models import AsrSegment
from echoledger.providers import ProviderError, Transcript
from echoledger.voiceprint import build_adts_header, decode_audio_to_pcm16, generate_synthetic_aac


def _build_test_app(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        api_token="test-admin-secret-token-32chars",
        asr_provider="mock",
    )
    app = create_app(settings)
    return app, settings


def test_adts_decode_raises_when_ffmpeg_missing():
    adts_bytes = build_adts_header(16, sample_rate=16000, channels=1) + (b"\x11\x22\x33\x44" * 4)
    with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
        with pytest.raises(RuntimeError, match="ffmpeg is required"):
            decode_audio_to_pcm16(adts_bytes)


def test_retryable_provider_error_requeues_then_succeeds(tmp_path: Path):
    app, settings = _build_test_app(tmp_path)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {settings.api_token}"}

    # Ensure primary lease on dp_default
    r_ensure = client.post(
        "/dp-instances/ensure",
        headers=headers,
        json={"dp_instance_id": "dp_default", "vault_id": "vlt_default", "make_primary": True},
    )
    assert r_ensure.status_code == 200

    aac_bytes = generate_synthetic_aac(duration_sec=1.5)
    meta = {
        "dp_instance_id": "dp_default",
        "live_session_id": "ls_retry_01",
        "segment_index": 0,
        "split_reason": "silence_window",
    }
    up = client.post(
        "/tenants/ten_default/vaults/vlt_default/recordings/upload",
        headers={"Authorization": f"Bearer {settings.api_token}", "x-dp-instance-id": "dp_default"},
        files={
            "started_at": (None, "2026-11-10T12:00:00Z"),
            "format": (None, "aac"),
            "source": (None, "ambient"),
            "capture_timezone": (None, "Asia/Shanghai"),
            "capture_metadata": (None, json.dumps(meta)),
            "file": ("seg0.aac", io.BytesIO(aac_bytes), "audio/aac"),
        },
    )
    assert up.status_code == 201
    recording_id = up.json()["recording_id"]
    chain_id = app.state.db.one("SELECT chain_id FROM processing_chains WHERE recording_id = ?", (recording_id,))["chain_id"]

    proc = app.state.pipeline.hemory_processor
    proc.retry_backoff_sec = 0.0

    calls = {"count": 0}

    def flaky_transcribe(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ProviderError("HTTP_429_RATE_LIMIT", retryable=True)
        return Transcript([AsrSegment(start_ms=0, end_ms=1200, text="明天上午十点评审后端架构方案")], "mock", False)

    speech_pcm = (np.sin(np.linspace(0, 200 * np.pi, 16000 * 2)) * 12000).astype(np.int16)
    with (
        patch("echoledger.hemory_pipeline.decode_audio_to_pcm16", return_value=speech_pcm),
        patch.object(app.state.pipeline.scorer, "probabilities", return_value=np.ones(100, dtype=np.float32)),
        patch("echoledger.hemory_pipeline.speech_intervals", return_value=[(0, len(speech_pcm))]),
        patch.object(app.state.pipeline.transcriber, "transcribe", side_effect=flaky_transcribe),
    ):
        assert proc.tick() is True
        chain_after_fail = app.state.db.one("SELECT * FROM processing_chains WHERE chain_id = ?", (chain_id,))
        assert chain_after_fail["status"] == "pending"
        assert chain_after_fail["error_code"] == "RETRYABLE_1"

        assert proc.tick() is True
        chain_after_retry = app.state.db.one("SELECT * FROM processing_chains WHERE chain_id = ?", (chain_id,))
        assert chain_after_retry["status"] == "completed"
        assert chain_after_retry["error_code"] is None


def test_authenticate_tenancy_rejects_unknown_token_and_guards_dp_ensure(tmp_path: Path):
    app, settings = _build_test_app(tmp_path)
    client = TestClient(app)

    # Unknown token must be rejected with 401 even when x-tenant-id is supplied
    bad = client.get(
        "/timeline/day",
        headers={
            "Authorization": "Bearer unknown_attacker_token",
            "x-tenant-id": "ten_default",
            "x-vault-id": "vlt_default",
        },
    )
    assert bad.status_code == 401

    # Admin calling /dp-instances/ensure with nonexistent vault returns 404 instead of 500
    missing_vlt = client.post(
        "/dp-instances/ensure",
        headers={"Authorization": f"Bearer {settings.api_token}", "x-tenant-id": "ten_default"},
        json={"dp_instance_id": "dp_ghost", "vault_id": "vlt_nonexistent", "make_primary": True},
    )
    assert missing_vlt.status_code == 404

    # Mutation endpoint exposes latest_cursor and UTC offset timezones work on /timeline/day
    app.state.db.append_recording_mutation(
        tenant_id="ten_default",
        vault_id="vlt_default",
        entity_type="event",
        entity_id="evt_1",
        mutation_type="event_created",
        payload_json={},
    )
    mut = client.get(
        "/recording-mutations?after=0&limit=1",
        headers={"Authorization": f"Bearer {settings.api_token}"},
    )
    assert mut.status_code == 200
    assert mut.json()["latest_cursor"] >= 1

    day = client.get(
        "/timeline/day?date=2026-11-10&tz=UTC%2B08:00",
        headers={"Authorization": f"Bearer {settings.api_token}"},
    )
    assert day.status_code == 200
    assert day.json()["timezone"] == "UTC+08:00"

    # Reassigning an already-enrolled cluster via POST /speakers decrements previous speaker sample_count
    with app.state.db.connect(immediate=True) as c:
        c.execute(
            """INSERT INTO voiceprint_clusters(
                cluster_id, tenant_id, vault_id, speaker_id, status, confidence_score,
                occurrences_count, created_at, updated_at
            ) VALUES ('cls_re_1', 'ten_default', 'vlt_default', NULL, 'pending', 0.9, 3, '2026-11-10T10:00:00Z', '2026-11-10T10:00:00Z')"""
        )
    s1 = client.post(
        "/speakers",
        headers={"Authorization": f"Bearer {settings.api_token}"},
        json={"name": "Alice", "cluster_id": "cls_re_1"},
    )
    assert s1.status_code == 201
    spk1_id = s1.json()["speaker_id"]
    spk1_before = app.state.db.one("SELECT sample_count FROM speaker_profiles WHERE speaker_id = ?", (spk1_id,))
    assert spk1_before["sample_count"] == 3

    s2 = client.post(
        "/speakers",
        headers={"Authorization": f"Bearer {settings.api_token}"},
        json={"name": "Bob", "cluster_id": "cls_re_1"},
    )
    assert s2.status_code == 201
    spk2_id = s2.json()["speaker_id"]
    spk2_after = app.state.db.one("SELECT sample_count FROM speaker_profiles WHERE speaker_id = ?", (spk2_id,))
    assert spk2_after["sample_count"] == 3
    old_spk = app.state.db.one("SELECT sample_count FROM speaker_profiles WHERE speaker_id = ?", (spk1_id,))
    assert old_spk["sample_count"] == 0
