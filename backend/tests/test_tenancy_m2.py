"""
backend/tests/test_tenancy_m2.py
Comprehensive unit and integration test suite for Milestone M2:
- 14-Table Relational Schema & SQLite Referential Integrity
- Partial Unique Index idx_dp_instances_primary_unique
- Single Primary Lease Arbitration (PUT /primary-device)
- 4 Canonical Upload Lease Rejection Codes (no_primary, not_primary, invalid_time, out_of_primary_window)
- 6-Field Multipart Audio Ingestion & Contract C2 Response
- 7-Byte MPEG-4 ADTS Bitstream Verification Engine
- Physical Partitioned Storage Persistence & Companion .meta.json
"""
from __future__ import annotations

import datetime
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.lease import LeaseErrorCode
from echoledger.upload import validate_and_parse_adts, AdtsValidationError


# --- Synthetic ADTS Frame Generator Helper ---
def make_adts_frame(
    payload: bytes = b"\x00" * 128,
    sample_rate_idx: int = 8,  # 8 = 16000 Hz
    channels: int = 1,         # 1 = Mono
    profile: int = 1,          # 1 = AAC-LC (MPEG-4 profile 2 -> ADTS profile-1 = 1)
    mpeg_id: int = 0,          # 0 = MPEG-4 Audio
    protection_absent: int = 1 # 1 = No CRC
) -> bytes:
    flen = 7 + len(payload)
    h = bytearray(7)
    h[0] = 0xFF
    h[1] = 0xF0 | (mpeg_id << 3) | protection_absent
    h[2] = ((profile & 0x03) << 6) | ((sample_rate_idx & 0x0F) << 2) | ((channels >> 2) & 0x01)
    h[3] = ((channels & 0x03) << 6) | ((flen >> 11) & 0x03)
    h[4] = (flen >> 3) & 0xFF
    h[5] = ((flen & 0x07) << 5) | 0x1F
    h[6] = 0xFC
    return bytes(h) + payload


def make_adts_stream(num_frames: int = 10, payload_size: int = 128) -> bytes:
    frame = make_adts_frame(b"\xAB" * payload_size)
    return frame * num_frames


@pytest.fixture
def m2_env(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        api_token="owner-token-for-test-at-least-24-chars",
        run_worker=False,
    )
    app = create_app(settings)
    db: Database = app.state.db

    # Seed 4-tier tenancy topology
    with db.connect(immediate=True) as conn:
        conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t_alpha', 'Alpha Tenant', 'alpha')")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_alpha_1', 't_alpha', 'Vault 1', 1)")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_alpha_2', 't_alpha', 'Vault 2', 0)")
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_alice', 't_alpha', 'alice@alpha.com', 'Alice')")
        conn.execute("""
            INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
            VALUES ('dp_phone', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Phone', 'phone', 1),
                   ('dp_watch', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Watch', 'watch', 0)
        """)

    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {settings.api_token}",
        "x-tenant-id": "t_alpha",
        "x-vault-id": "v_alpha_1",
        "x-user-id": "u_alice",
        "x-dp-instance-id": "dp_phone",
    }
    return app, db, settings, client, headers


# ============================================================================
# 1. 14-Table DDL Schema & Referential Integrity
# ============================================================================

def test_14_table_schema_and_cascading_integrity(m2_env):
    _, db, _, _, _ = m2_env
    expected_tables = {
        "tenants", "vaults", "users", "dp_instances", "device_bindings",
        "recording_sessions", "recording_segments", "sections", "section_messages",
        "summaries", "speaker_profiles", "voiceprint_clusters", "listening_schedules",
        "mcp_api_keys"
    }
    with db.connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = expected_tables - tables
        assert not missing, f"Missing multi-tenant tables: {missing}"

        # Test cascading deletion from tenant to child tables
        conn.execute("INSERT INTO tenants (tenant_id, name, slug) VALUES ('t_del', 'Del Tenant', 'del')")
        conn.execute("INSERT INTO vaults (vault_id, tenant_id, name) VALUES ('v_del', 't_del', 'Del Vault')")
        conn.execute("INSERT INTO users (user_id, tenant_id, email) VALUES ('u_del', 't_del', 'del@del.com')")
        conn.execute("INSERT INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind) VALUES ('dp_del', 't_del', 'v_del', 'u_del', 'Del DP', 'phone')")

        # Delete tenant
        conn.execute("DELETE FROM tenants WHERE tenant_id = 't_del'")
        assert conn.execute("SELECT COUNT(*) FROM vaults WHERE vault_id = 'v_del'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM users WHERE user_id = 'u_del'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM dp_instances WHERE dp_instance_id = 'dp_del'").fetchone()[0] == 0


# ============================================================================
# 2. Partial Unique Index Constraint (idx_dp_instances_primary_unique)
# ============================================================================

def test_partial_unique_index_single_primary_only(m2_env):
    _, db, _, _, _ = m2_env
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect(immediate=True) as conn:
            # Attempt to set second device as primary in same vault for same user
            conn.execute("UPDATE dp_instances SET is_primary = 1 WHERE dp_instance_id = 'dp_watch'")

    # Multiple non-primary devices are permitted
    with db.connect(immediate=True) as conn:
        conn.execute("UPDATE dp_instances SET is_primary = 0 WHERE dp_instance_id = 'dp_phone'")
        conn.execute("UPDATE dp_instances SET is_primary = 0 WHERE dp_instance_id = 'dp_watch'")
        p_count = conn.execute("SELECT COUNT(*) FROM dp_instances WHERE vault_id = 'v_alpha_1' AND is_primary = 1").fetchone()[0]
        assert p_count == 0


# ============================================================================
# 3. Single Primary Lease Arbitration (PUT /primary-device)
# ============================================================================

def test_primary_device_lease_switch_atomic(m2_env):
    _, db, _, client, headers = m2_env

    # Initial state: dp_phone is primary
    with db.connect() as conn:
        assert conn.execute("SELECT is_primary FROM dp_instances WHERE dp_instance_id = 'dp_phone'").fetchone()[0] == 1
        assert conn.execute("SELECT is_primary FROM dp_instances WHERE dp_instance_id = 'dp_watch'").fetchone()[0] == 0

    # Elect dp_watch as primary
    res = client.put("/primary-device", json={"dp_instance_id": "dp_watch", "vault_id": "v_alpha_1"}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["dp_instance_id"] == "dp_watch"
    assert body["is_primary"] is True
    assert "effective_from" in body

    # Verify atomic demotion and promotion in database
    with db.connect() as conn:
        assert conn.execute("SELECT is_primary FROM dp_instances WHERE dp_instance_id = 'dp_phone'").fetchone()[0] == 0
        assert conn.execute("SELECT is_primary FROM dp_instances WHERE dp_instance_id = 'dp_watch'").fetchone()[0] == 1


def test_primary_device_lease_invalid_dp(m2_env):
    _, _, _, client, headers = m2_env
    res = client.put("/primary-device", json={"dp_instance_id": "non_existent_dp", "vault_id": "v_alpha_1"}, headers=headers)
    assert res.status_code == 400
    assert res.json()["error"] == LeaseErrorCode.INVALID_DP


def test_primary_device_lease_mismatched_vault(m2_env):
    _, _, _, client, headers = m2_env
    # dp_phone belongs to v_alpha_1, but we pass v_alpha_2
    res = client.put("/primary-device", json={"dp_instance_id": "dp_phone", "vault_id": "v_alpha_2"}, headers=headers)
    assert res.status_code == 400
    assert res.json()["error"] == LeaseErrorCode.INVALID_DP


# ============================================================================
# 4. Canonical Upload Lease Rejection Codes
# ============================================================================

def test_upload_rejection_no_primary(m2_env):
    _, db, _, client, headers = m2_env
    # Demote all primary devices in vault
    with db.connect(immediate=True) as conn:
        conn.execute("UPDATE dp_instances SET is_primary = 0 WHERE vault_id = 'v_alpha_1'")

    stream = make_adts_stream(5)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    files = {"file": ("chunk.aac", stream, "audio/aac")}
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "ambient",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 403
    assert res.json()["error"] == LeaseErrorCode.NO_PRIMARY
    assert res.json()["error_code"] == LeaseErrorCode.NO_PRIMARY


def test_upload_rejection_not_primary(m2_env):
    _, _, _, client, headers = m2_env
    # dp_phone is primary; attempt upload claiming dp_watch
    stream = make_adts_stream(5)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    files = {"file": ("chunk.aac", stream, "audio/aac")}
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "watch",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_watch"}),
    }
    watch_headers = {**headers, "x-dp-instance-id": "dp_watch"}
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=watch_headers)
    assert res.status_code == 409
    body = res.json()
    assert body["error"] == LeaseErrorCode.NOT_PRIMARY
    assert body["error_code"] == LeaseErrorCode.NOT_PRIMARY
    assert body["primary_dp_instance_id"] == "dp_phone"


def test_upload_rejection_invalid_time_future(m2_env):
    _, _, _, client, headers = m2_env
    # 45 seconds ahead (> 30s drift)
    future_iso = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=45)).isoformat().replace("+00:00", "Z")
    stream = make_adts_stream(5)
    files = {"file": ("chunk.aac", stream, "audio/aac")}
    data = {
        "started_at": future_iso,
        "format": "aac",
        "source": "phone",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 409
    assert res.json()["error"] == LeaseErrorCode.INVALID_TIME


def test_upload_rejection_invalid_time_stale(m2_env):
    _, _, _, client, headers = m2_env
    # 8 days in past (> 7 days stale)
    stale_iso = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)).isoformat().replace("+00:00", "Z")
    stream = make_adts_stream(5)
    files = {"file": ("chunk.aac", stream, "audio/aac")}
    data = {
        "started_at": stale_iso,
        "format": "aac",
        "source": "phone",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 409
    assert res.json()["error"] == LeaseErrorCode.INVALID_TIME


def test_upload_rejection_out_of_primary_window(m2_env):
    _, db, _, client, headers = m2_env
    now = datetime.datetime.now(datetime.timezone.utc)
    # Configure schedule strictly for tomorrow
    tomorrow_weekday = (now.isoweekday() % 7) + 1
    windows = [{"weekday": tomorrow_weekday, "start_minutes": 540, "end_minutes": 1020, "window_start": "09:00", "window_end": "17:00"}]
    with db.connect(immediate=True) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO listening_schedules (schedule_id, tenant_id, vault_id, user_id, dp_instance_id, weekly_windows, is_enabled)
            VALUES ('sched_alice', 't_alpha', 'v_alpha_1', 'u_alice', 'dp_phone', ?, 1)
        """, (json.dumps(windows),))

    now_iso = now.isoformat().replace("+00:00", "Z")
    stream = make_adts_stream(5)
    files = {"file": ("chunk.aac", stream, "audio/aac")}
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 403
    assert res.json()["error"] == LeaseErrorCode.OUT_OF_PRIMARY_WINDOW


# ============================================================================
# 5. 6-Field Multipart Ingestion & Contract C2 Response
# ============================================================================

def test_upload_valid_aac_contract_c2_response(m2_env):
    _, db, settings, client, headers = m2_env
    stream = make_adts_stream(15)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    metadata = {
        "recording_id": "rec_01JCG9Y91B3K4P0W8A",
        "dp_instance_id": "dp_phone",
        "sample_rate": 16000,
        "channels": 1,
        "bitrate_kbps": 48,
        "split_reason": "silence_window",
        "battery_level": 0.85,
        "is_charging": False,
        "live_session_id": "sess_20261110_01",
        "segment_index": 2,
    }
    files = {"file": ("segment_002.aac", stream, "audio/aac")}
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "ambient",
        "capture_timezone": "Asia/Shanghai",
        "capture_metadata": json.dumps(metadata),
    }

    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 201
    body = res.json()
    assert body["recording_id"] == "rec_01JCG9Y91B3K4P0W8A"
    assert "asset_id" in body
    assert "artifact_id" in body
    assert body["status"] == "queued_for_processing"

    storage_key = body["storage_key"]
    assert "t_alpha" in storage_key
    assert "v_alpha_1" in storage_key
    assert storage_key.endswith(".aac")

    # Verify physical file existence and content
    audio_path = settings.data_dir / storage_key.replace("data/", "")
    assert audio_path.exists()
    assert audio_path.read_bytes() == stream

    # Verify companion metadata file
    meta_path = audio_path.with_name(audio_path.name.replace(".aac", ".meta.json"))
    assert meta_path.exists()
    saved_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert saved_meta["sample_rate"] == 16000
    assert saved_meta["channels"] == 1
    assert saved_meta["frame_count"] == 15
    assert saved_meta["duration_ms"] == 15 * 64

    # Verify database segment insertion
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM recording_segments WHERE live_session_id = 'sess_20261110_01'").fetchone()
        assert row is not None
        assert row["segment_index"] == 2
        assert row["duration_ms"] == 15 * 64
        assert row["upload_status"] == "uploaded"


def test_upload_missing_fields_rejected_422(m2_env):
    _, _, _, client, headers = m2_env
    stream = make_adts_stream(5)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    # Missing started_at
    files = {"file": ("chunk.aac", stream, "audio/aac")}
    data = {
        "format": "aac",
        "source": "phone",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 422


def test_upload_invalid_format_rejected_422(m2_env):
    _, _, _, client, headers = m2_env
    stream = make_adts_stream(5)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    files = {"file": ("chunk.mp3", stream, "audio/mpeg")}
    data = {
        "started_at": now_iso,
        "format": "mp3",  # Must be 'aac'
        "source": "phone",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 422
    assert "format" in res.json()["detail"]


def test_upload_invalid_metadata_rejected_422(m2_env):
    _, _, _, client, headers = m2_env
    stream = make_adts_stream(5)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    files = {"file": ("chunk.aac", stream, "audio/aac")}
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_metadata": "{invalid_json_here",
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 422


# ============================================================================
# 6. 7-Byte MPEG-4 ADTS Bitstream Verification Engine
# ============================================================================

def test_adts_bitstream_corrupted_syncword(m2_env):
    _, _, _, client, headers = m2_env
    # Corrupt syncword from 0xFF to 0xFE
    bad_frame = bytearray(make_adts_frame())
    bad_frame[0] = 0xFE
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    files = {"file": ("bad.aac", bytes(bad_frame), "audio/aac")}
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 400
    assert res.json()["error"] == "INVALID_AUDIO_STREAM"


def test_adts_bitstream_wrong_sample_rate(m2_env):
    _, _, _, client, headers = m2_env
    # Sample rate index 4 (44100 Hz instead of 8 for 16000 Hz)
    wrong_sr_frame = make_adts_frame(sample_rate_idx=4)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    files = {"file": ("wrong_sr.aac", wrong_sr_frame, "audio/aac")}
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 400
    assert res.json()["error"] == "INVALID_AUDIO_STREAM"


def test_adts_bitstream_stereo_rejected(m2_env):
    _, _, _, client, headers = m2_env
    # 2 channels (Stereo instead of Mono 1)
    stereo_frame = make_adts_frame(channels=2)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    files = {"file": ("stereo.aac", stereo_frame, "audio/aac")}
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 400
    assert res.json()["error"] == "INVALID_AUDIO_STREAM"


def test_adts_bitstream_truncated_rejected(m2_env):
    _, _, _, client, headers = m2_env
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    files = {"file": ("trunc.aac", b"\xFF\xF1\x60", "audio/aac")}  # Only 3 bytes
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
    }
    res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res.status_code == 400
    assert res.json()["error"] == "INVALID_AUDIO_STREAM"


# ============================================================================
# 7. Tenancy Micro-Isolation Endpoints
# ============================================================================

def test_tenancy_endpoints_authorization(m2_env):
    _, _, _, client, headers = m2_env

    # 1. Access own tenant and vault
    res_t = client.get("/tenants/t_alpha", headers=headers)
    assert res_t.status_code == 200
    assert res_t.json()["name"] == "Alpha Tenant"

    res_v = client.get("/tenants/t_alpha/vaults/v_alpha_1", headers=headers)
    assert res_v.status_code == 200
    assert res_v.json()["name"] == "Vault 1"

    # 2. Cross-tenant access forbidden
    foreign_headers = {**headers, "x-tenant-id": "t_foreign", "Authorization": "Bearer foreign_user_token"}
    res_ct = client.get("/tenants/t_alpha", headers=foreign_headers)
    assert res_ct.status_code == 403

    # 3. Unauthenticated access forbidden (401)
    res_unauth = client.get("/tenants/t_alpha")
    assert res_unauth.status_code == 401


# ============================================================================
# 8. Hardening & Edge Cases (Remediations)
# ============================================================================

def test_session_cross_tenant_upsert_rejection(m2_env):
    _, db, _, _, _ = m2_env
    # Create legitimate session for u_alice
    db.upsert_recording_session(
        live_session_id="sess_hijack_target",
        tenant_id="t_alpha",
        vault_id="v_alpha_1",
        user_id="u_alice",
        dp_instance_id="dp_phone",
        local_day_key="2026-11-10",
        started_at="2026-11-10T00:00:00Z",
    )

    # Malicious actor from another tenant attempts to upsert session
    with pytest.raises(PermissionError, match="belongs to a different tenant"):
        db.upsert_recording_session(
            live_session_id="sess_hijack_target",
            tenant_id="t_beta",
            vault_id="v_beta_1",
            user_id="u_mallory",
            dp_instance_id="dp_mallory",
            local_day_key="2026-11-10",
            started_at="2026-11-10T00:00:00Z",
        )


def test_upload_idempotency_returns_consistent_receipt(m2_env):
    _, _, _, client, headers = m2_env
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    audio = make_adts_stream(5)
    files = {"file": ("chunk.aac", audio, "audio/aac")}
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_metadata": json.dumps({
            "live_session_id": "sess_idempotent_test",
            "segment_index": 0,
            "dp_instance_id": "dp_phone",
        }),
    }

    # First upload
    res1 = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
    assert res1.status_code == 201
    body1 = res1.json()

    # Second upload (retry) with fresh files handle
    files_retry = {"file": ("chunk.aac", audio, "audio/aac")}
    res2 = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files_retry, headers=headers)
    assert res2.status_code == 201
    body2 = res2.json()

    assert body1["storage_key"] == body2["storage_key"]
    assert body1["asset_id"] == body2["asset_id"]


def test_identity_spoofing_prevented_for_non_admin(m2_env):
    _, db, _, client, _ = m2_env
    # Seed a non-admin user and device binding
    with db.connect(immediate=True) as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email) VALUES ('u_bob', 't_alpha', 'bob@alpha.com')")
        conn.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind) VALUES ('dp_bob', 't_alpha', 'v_alpha_1', 'u_bob', 'Bob Phone', 'phone')")
        conn.execute(
            """INSERT INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
               VALUES ('bind_bob', 't_alpha', 'u_bob', 'dp_bob', 'bob_uuid_123', 'android', 'token_bob_secret', 'bound')"""
        )

    # Legitimate non-admin token with matched headers succeeds
    legit_headers = {
        "Authorization": "Bearer token_bob_secret",
        "x-tenant-id": "t_alpha",
        "x-user-id": "u_bob",
    }
    res_ok = client.get("/tenants/t_alpha", headers=legit_headers)
    assert res_ok.status_code == 200

    # Spoofing attack: non-admin token with spoofed x-user-id header gets 403
    spoofed_headers = {
        "Authorization": "Bearer token_bob_secret",
        "x-tenant-id": "t_alpha",
        "x-user-id": "u_alice",  # Attempting to impersonate Alice
    }
    res_spoof = client.get("/tenants/t_alpha", headers=spoofed_headers)
    assert res_spoof.status_code == 403

