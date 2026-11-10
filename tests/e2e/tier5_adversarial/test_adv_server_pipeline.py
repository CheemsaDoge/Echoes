"""Tier 5 Adversarial Test Suite: White-Box Server Subsystem Coverage Hardening.

Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md & ORIGINAL_REQUEST.md.
Covers deep white-box invariants, boundary conditions, race conditions,
error status codes, and multi-tenant isolation across:
  - backend/echoledger/lease.py
  - backend/echoledger/upload.py
  - backend/echoledger/voiceprint.py
  - backend/echoledger/speakers.py
  - backend/echoledger/sse.py
  - backend/echoledger/chat.py / gemini.py
  - backend/echoledger/db.py
  - backend/echoledger/api.py
"""

from __future__ import annotations

import asyncio
import datetime
import io
import json
import secrets
import sqlite3
import sys
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from starlette.testclient import TestClient

# Ensure backend and repo root are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database, sanitize_fts_tokens
from echoledger.lease import (
    LeaseErrorCode,
    LeaseRejectionException,
    PrimaryLeaseArbiter,
    UploadLeaseValidator,
)
from echoledger.models import PrimaryDeviceRequest, ClusterPatchRequest
from echoledger.speakers import (
    resolve_speaker_name,
    SessionSpeakerResolver,
)
from echoledger.sse import (
    BufferedEvent,
    TurnEventBuffer,
    TurnStreamManager,
    format_sse_frame,
    parse_last_event_id,
)
from echoledger.upload import (
    AdtsValidationError,
    AudioBitstreamException,
    UploadService,
    validate_and_parse_adts,
)
from echoledger.voiceprint import (
    calculate_clipping_ratio,
    calculate_snr,
    check_acoustic_qualification,
    cluster_segment,
    compute_centroid,
    cosine_similarity,
    generate_synthetic_aac,
    generate_synthetic_embedding,
    match_enrolled_speaker,
    pack_embedding,
    project_to_512,
    unit_normalize,
    unpack_embedding,
    update_centroid_ema,
    update_centroid_incremental,
    build_adts_header,
)
from echoledger.chat import (
    assemble_agent_system_instruction,
    execute_tool_call,
    SyntheticGeminiAgent,
)
from echoledger.storage import StorageService


ADMIN_TOKEN = "test-adv-admin-bearer-token-32chars"


@pytest.fixture
def real_server(tmp_path: Path):
    """Sets up an isolated, genuine EchoLedger backend instance with multi-tenant seeded data."""
    settings = Settings(
        data_dir=tmp_path,
        api_token=ADMIN_TOKEN,
        asr_provider="mock",
        vad_provider="energy_demo",
        run_worker=False,
    )
    app = create_app(settings)
    db: Database = app.state.db

    # Seed Tenant Alpha & Tenant Beta
    with db.connect(immediate=True) as c:
        # Tenant Alpha (Primary)
        c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_alpha', 'Tenant Alpha', 'alpha')")
        c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_alpha', 'ten_alpha', 'Alpha Vault', 1)")
        c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_alpha', 'ten_alpha', 'alpha@echo.local', 'Alice Alpha')")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_alpha_phone', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'Phone Alpha', 'phone', 1)")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_alpha_watch', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'Watch Alpha', 'watch', 0)")

        # Tenant Beta (Secondary / Isolated) - device_kind must satisfy chk_dp_kind ('phone', 'watch', 'official_cloud', 'agent')
        c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_beta', 'Tenant Beta', 'beta')")
        c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_beta', 'ten_beta', 'Beta Vault', 1)")
        c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_beta', 'ten_beta', 'beta@echo.local', 'Bob Beta')")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_beta_laptop', 'ten_beta', 'vlt_beta', 'usr_beta', 'Laptop Beta', 'phone', 1)")

    return app


@pytest.fixture
def real_client(real_server) -> TestClient:
    """TestClient configured with Admin Bearer Authorization header."""
    client = TestClient(real_server)
    client.headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"
    return client


# =============================================================================
# 1. Lease Arbitration & Clock Drift Adversarial Testing (`lease.py`)
# =============================================================================

class TestAdvLeaseArbitration:
    """White-box stress tests for lease arbitration, clock skew, and schedule constraints."""

    def test_clock_drift_future_exact_boundary(self):
        """Validates exact future clock drift boundary: <= 30.0s permitted, > 30.0s rejected with 409."""
        server_now = datetime.datetime(2026, 11, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

        # Boundary: exactly 30.0s future -> permitted
        t_30_0 = (server_now + datetime.timedelta(seconds=30.0)).isoformat()
        res = UploadLeaseValidator.validate_segment_time(t_30_0, server_now=server_now)
        assert res == server_now + datetime.timedelta(seconds=30.0)

        # Boundary violation: 30.001s future -> rejected with 409
        t_30_001 = (server_now + datetime.timedelta(seconds=30.001)).isoformat()
        with pytest.raises(LeaseRejectionException) as exc_info:
            UploadLeaseValidator.validate_segment_time(t_30_001, server_now=server_now)
        assert exc_info.value.status_code == 409
        assert exc_info.value.error_code == LeaseErrorCode.INVALID_TIME
        assert "Clock drift" in exc_info.value.error_message

    def test_clock_drift_stale_exact_boundary(self):
        """Validates exact stale drift boundary: <= 7 days permitted, > 7 days rejected with 409."""
        server_now = datetime.datetime(2026, 11, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

        # Boundary: exactly 7 days past (604800 seconds) -> permitted
        t_7d = (server_now - datetime.timedelta(days=7)).isoformat()
        res = UploadLeaseValidator.validate_segment_time(t_7d, server_now=server_now)
        assert res == server_now - datetime.timedelta(days=7)

        # Boundary violation: 7 days + 1 second -> rejected with 409
        t_7d_1s = (server_now - datetime.timedelta(days=7, seconds=1)).isoformat()
        with pytest.raises(LeaseRejectionException) as exc_info:
            UploadLeaseValidator.validate_segment_time(t_7d_1s, server_now=server_now)
        assert exc_info.value.status_code == 409
        assert exc_info.value.error_code == LeaseErrorCode.INVALID_TIME
        assert "stale" in exc_info.value.error_message

    def test_timestamp_timezone_offsets_and_malformed(self):
        """Validates timestamp normalization across timezones and rejection of malformed values."""
        server_now = datetime.datetime(2026, 11, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

        # Positive offset (+08:00): 20:00:00+08:00 is 12:00:00 UTC -> permitted
        t_cst = "2026-11-10T20:00:00+08:00"
        dt_cst = UploadLeaseValidator.validate_segment_time(t_cst, server_now=server_now)
        assert dt_cst == server_now

        # Negative offset (-05:00): 07:00:00-05:00 is 12:00:00 UTC -> permitted
        t_est = "2026-11-10T07:00:00-05:00"
        dt_est = UploadLeaseValidator.validate_segment_time(t_est, server_now=server_now)
        assert dt_est == server_now

        # Naive timestamp should be coerced to UTC
        t_naive = "2026-11-10T12:00:00"
        dt_naive = UploadLeaseValidator.validate_segment_time(t_naive, server_now=server_now)
        assert dt_naive.tzinfo == datetime.timezone.utc

        # Malformed strings must raise 409 INVALID_TIME
        for bad_ts in ["", "not-a-timestamp", "2026-02-31T12:00:00Z", "null"]:
            with pytest.raises(LeaseRejectionException) as exc_info:
                UploadLeaseValidator.validate_segment_time(bad_ts, server_now=server_now)
            assert exc_info.value.status_code == 409
            assert exc_info.value.error_code == LeaseErrorCode.INVALID_TIME

    def test_lease_preemption_and_no_primary_rejection(self, real_server):
        """Tests device preemption and missing primary device rejection."""
        db: Database = real_server.state.db

        with db.connect(immediate=True) as conn:
            # 1. Device Alpha Phone is initial primary
            primary = UploadLeaseValidator.validate_primary_device_lease(
                conn, "ten_alpha", "vlt_alpha", uploading_dp_id="dp_alpha_phone"
            )
            assert primary == "dp_alpha_phone"

            # 2. Upload from secondary device (Watch) rejected with 409 NOT_PRIMARY
            with pytest.raises(LeaseRejectionException) as exc_info:
                UploadLeaseValidator.validate_primary_device_lease(
                    conn, "ten_alpha", "vlt_alpha", uploading_dp_id="dp_alpha_watch"
                )
            assert exc_info.value.status_code == 409
            assert exc_info.value.error_code == LeaseErrorCode.NOT_PRIMARY
            assert exc_info.value.extra.get("primary_dp_instance_id") == "dp_alpha_phone"

            # 3. Preemption: Promote Watch to primary
            PrimaryLeaseArbiter.acquire_primary_device(
                conn=conn,
                dp_instance_id="dp_alpha_watch",
                vault_id="vlt_alpha",
                tenant_id="ten_alpha",
            )

            # 4. Now Watch succeeds, and Phone is rejected with 409
            assert UploadLeaseValidator.validate_primary_device_lease(
                conn, "ten_alpha", "vlt_alpha", uploading_dp_id="dp_alpha_watch"
            ) == "dp_alpha_watch"

            with pytest.raises(LeaseRejectionException) as exc_info:
                UploadLeaseValidator.validate_primary_device_lease(
                    conn, "ten_alpha", "vlt_alpha", uploading_dp_id="dp_alpha_phone"
                )
            assert exc_info.value.status_code == 409
            assert exc_info.value.error_code == LeaseErrorCode.NOT_PRIMARY

            # 5. Clear all primaries in vlt_alpha to test 403 NO_PRIMARY
            conn.execute("UPDATE dp_instances SET is_primary = 0 WHERE vault_id = 'vlt_alpha'")
            with pytest.raises(LeaseRejectionException) as exc_info:
                UploadLeaseValidator.validate_primary_device_lease(
                    conn, "ten_alpha", "vlt_alpha", uploading_dp_id="dp_alpha_watch"
                )
            assert exc_info.value.status_code == 403
            assert exc_info.value.error_code == LeaseErrorCode.NO_PRIMARY

    def test_listening_schedule_window_exact_minute_boundaries(self, real_server):
        """Tests weekly listening schedule minute-level boundary enforcement."""
        db: Database = real_server.state.db

        with db.connect(immediate=True) as conn:
            # Seed schedule: Monday (iso weekday 1), 09:00 - 17:00 (540 to 1020 minutes)
            windows = [{"weekday": 1, "start_minutes": 540, "end_minutes": 1020}]
            conn.execute(
                """INSERT OR REPLACE INTO listening_schedules
                   (schedule_id, dp_instance_id, tenant_id, vault_id, user_id, weekly_windows, is_enabled, revision)
                   VALUES ('sch_alpha', 'dp_alpha_phone', 'ten_alpha', 'vlt_alpha', 'usr_alpha', ?, 1, 1)""",
                (json.dumps(windows),),
            )

            # Monday 2026-11-09 at 08:59 UTC -> outside window (rejected 403)
            dt_0859 = datetime.datetime(2026, 11, 9, 8, 59, tzinfo=datetime.timezone.utc)
            with pytest.raises(LeaseRejectionException) as exc_info:
                UploadLeaseValidator.validate_listening_schedule_window(
                    conn, "ten_alpha", "vlt_alpha", "dp_alpha_phone", dt_0859
                )
            assert exc_info.value.status_code == 403
            assert exc_info.value.error_code == LeaseErrorCode.OUT_OF_PRIMARY_WINDOW

            # Monday at 09:00 UTC (exact start boundary) -> permitted
            dt_0900 = datetime.datetime(2026, 11, 9, 9, 0, tzinfo=datetime.timezone.utc)
            UploadLeaseValidator.validate_listening_schedule_window(
                conn, "ten_alpha", "vlt_alpha", "dp_alpha_phone", dt_0900
            )

            # Monday at 17:00 UTC (exact end boundary) -> permitted
            dt_1700 = datetime.datetime(2026, 11, 9, 17, 0, tzinfo=datetime.timezone.utc)
            UploadLeaseValidator.validate_listening_schedule_window(
                conn, "ten_alpha", "vlt_alpha", "dp_alpha_phone", dt_1700
            )

            # Monday at 17:01 UTC -> outside window (rejected 403)
            dt_1701 = datetime.datetime(2026, 11, 9, 17, 1, tzinfo=datetime.timezone.utc)
            with pytest.raises(LeaseRejectionException) as exc_info:
                UploadLeaseValidator.validate_listening_schedule_window(
                    conn, "ten_alpha", "vlt_alpha", "dp_alpha_phone", dt_1701
                )
            assert exc_info.value.status_code == 403

            # Disabled schedule -> 24/7 permitted even outside window
            conn.execute("UPDATE listening_schedules SET is_enabled = 0 WHERE schedule_id = 'sch_alpha'")
            UploadLeaseValidator.validate_listening_schedule_window(
                conn, "ten_alpha", "vlt_alpha", "dp_alpha_phone", dt_1701
            )

    def test_concurrent_primary_lease_arbitration_stress(self, real_server):
        """Stress-tests atomic primary lease election under heavy multi-threaded contention."""
        db: Database = real_server.state.db
        devices = ["dp_alpha_phone", "dp_alpha_watch"]
        errors = []

        def switch_lease(target_dp: str):
            try:
                with db.connect(immediate=True) as conn:
                    PrimaryLeaseArbiter.acquire_primary_device(
                        conn=conn,
                        dp_instance_id=target_dp,
                        vault_id="vlt_alpha",
                        tenant_id="ten_alpha",
                    )
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(16):
            target = devices[i % len(devices)]
            t = threading.Thread(target=switch_lease, args=(target,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Encountered concurrent lease errors: {errors}"

        # Invariant: exactly ONE primary device must exist in the vault
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT dp_instance_id FROM dp_instances WHERE vault_id = 'vlt_alpha' AND is_primary = 1"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["dp_instance_id"] in devices


# =============================================================================
# 2. Upload Ingestion & ADTS Bitstream Verification (`upload.py`)
# =============================================================================

class TestAdvUploadAndAdtsVerification:
    """White-box stress tests for 6-field multipart ingestion and 7-byte ADTS AAC verification."""

    def test_adts_header_boundary_corruptions(self):
        """Verifies bit-level ADTS header corruption rejection across all header fields."""
        valid_hdr = build_adts_header(32, sample_rate=16000, channels=1, profile=2)
        payload = b"\x00" * 32
        valid_frame = valid_hdr + payload

        # 1. Truncated stream (< 7 bytes)
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(b"\xFF\xF1\x50")
        assert "too short" in str(exc.value)

        # 2. Corrupted syncword
        bad_sync = bytearray(valid_frame)
        bad_sync[0] = 0xAA
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(bytes(bad_sync))
        assert "syncword" in str(exc.value)

        # 3. Invalid MPEG ID (bit 3 set to 1, indicating MPEG-2)
        bad_mpeg_id = bytearray(valid_frame)
        bad_mpeg_id[1] |= 0x08
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(bytes(bad_mpeg_id))
        assert "MPEG ID" in str(exc.value)

        # 4. Invalid layer bits (bits 1-2 != 00)
        bad_layer = bytearray(valid_frame)
        bad_layer[1] |= 0x02
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(bytes(bad_layer))
        assert "layer" in str(exc.value)

        # 5. Protection absent == 0 (CRC present, 9-byte header not allowed by spec)
        bad_crc = bytearray(valid_frame)
        bad_crc[1] &= ~0x01
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(bytes(bad_crc))
        assert "Protection absent" in str(exc.value)

        # 6. Invalid Profile (profile != 1 [AAC-LC])
        bad_prof = bytearray(valid_frame)
        bad_prof[2] = (bad_prof[2] & 0x3F) | (0x02 << 6)  # Profile SSR
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(bytes(bad_prof))
        assert "profile" in str(exc.value)

        # 7. Invalid Sample Rate Index (!= 8 [16000 Hz])
        bad_sr = bytearray(valid_frame)
        bad_sr[2] = (bad_sr[2] & 0xC3) | (0x04 << 2)  # Index 4 (44.1kHz)
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(bytes(bad_sr))
        assert "sample rate index" in str(exc.value)

        # 8. Invalid Channel Configuration (!= 1 [Mono])
        bad_ch = bytearray(valid_frame)
        bad_ch[2] = (bad_ch[2] & ~0x01) | 0x00
        bad_ch[3] = (bad_ch[3] & 0x3F) | (0x02 << 6)  # Channel config 2 (Stereo)
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(bytes(bad_ch))
        assert "channel configuration" in str(exc.value)

        # 9. Invalid Frame Length < 7
        bad_flen = bytearray(valid_frame)
        bad_flen[3] &= 0xFC
        bad_flen[4] = 0x00
        bad_flen[5] = (bad_flen[5] & 0x1F) | (0x05 << 5)  # Frame length = 5 (< 7)
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(bytes(bad_flen))
        assert "frame length" in str(exc.value)

        # 10. Frame length exceeds stream buffer
        truncated_flen = bytearray(valid_frame)
        truncated_flen[3] |= 0x01
        truncated_flen[4] = 0xFF  # Frame length > 2000 bytes
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(bytes(truncated_flen))
        assert "Truncated frame" in str(exc.value)

    def test_adts_corrupted_frame_in_multiframe_stream(self):
        """Verifies multi-frame stream parses frame 0, but fails exactly on corrupted frame 1."""
        frame_0 = build_adts_header(32) + b"\x00" * 32
        frame_1_corrupt = bytearray(build_adts_header(32) + b"\x00" * 32)
        frame_1_corrupt[0] = 0x00  # Corrupt syncword on frame 1

        multi_frame = frame_0 + bytes(frame_1_corrupt)
        with pytest.raises(AdtsValidationError) as exc:
            validate_and_parse_adts(multi_frame)
        assert "frame 1" in str(exc.value)

    def test_multipart_form_validation_and_status_codes(self, real_client):
        """Tests strict 6-field multipart validation and 422 HTTP status codes."""
        valid_aac = build_adts_header(32) + b"\x00" * 32
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 1. Invalid format != 'aac'
        res = real_client.post(
            "/tenants/ten_alpha/vaults/vlt_alpha/recordings/upload",
            data={
                "started_at": now_iso,
                "format": "wav",
                "source": "phone",
                "capture_metadata": json.dumps({"segment_index": 0}),
            },
            files={"file": ("test.wav", valid_aac, "audio/wav")},
        )
        assert res.status_code == 422
        assert "Invalid format" in res.text

        # 2. Invalid source enum
        res = real_client.post(
            "/tenants/ten_alpha/vaults/vlt_alpha/recordings/upload",
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "satellite_dish",
                "capture_metadata": json.dumps({"segment_index": 0}),
            },
            files={"file": ("test.aac", valid_aac, "audio/aac")},
        )
        assert res.status_code == 422
        assert "Invalid source" in res.text

        # 3. Malformed JSON capture_metadata
        res = real_client.post(
            "/tenants/ten_alpha/vaults/vlt_alpha/recordings/upload",
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": "{not_valid_json",
            },
            files={"file": ("test.aac", valid_aac, "audio/aac")},
        )
        assert res.status_code == 422
        assert "must be valid JSON" in res.text

        # 4. Invalid segment_index (float, boolean, string-with-alpha, negative)
        for bad_idx in [1.5, True, False, "first_chunk", -5, 9223372036854775808]:
            res = real_client.post(
                "/tenants/ten_alpha/vaults/vlt_alpha/recordings/upload",
                data={
                    "started_at": now_iso,
                    "format": "aac",
                    "source": "phone",
                    "capture_metadata": json.dumps({"segment_index": bad_idx}),
                },
                files={"file": ("test.aac", valid_aac, "audio/aac")},
            )
            assert res.status_code == 422
            assert "segment_index" in res.text

    def test_upload_size_boundaries(self, real_client):
        """Tests zero-byte audio rejection (400) and oversized upload limit (413)."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 1. Zero-byte audio file -> 400 empty_audio_file (with valid primary device header)
        res = real_client.post(
            "/tenants/ten_alpha/vaults/vlt_alpha/recordings/upload",
            headers={"x-dp-instance-id": "dp_alpha_phone"},
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({"segment_index": 0}),
            },
            files={"file": ("empty.aac", b"", "audio/aac")},
        )
        assert res.status_code == 400
        assert res.json().get("error") == "empty_audio_file"

        # 2. Oversized audio file (> 15MB limit) -> 413
        oversized_data = b"\x00" * (15_000_000 + 10)
        res = real_client.post(
            "/tenants/ten_alpha/vaults/vlt_alpha/recordings/upload",
            headers={"x-dp-instance-id": "dp_alpha_phone"},
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({"segment_index": 0}),
            },
            files={"file": ("huge.aac", oversized_data, "audio/aac")},
        )
        assert res.status_code == 413

    def test_storage_vault_physical_partitioning_and_isolation(self, real_client, real_server):
        """Verifies physical disk isolation and partition scoping by vault_id."""
        valid_aac = build_adts_header(32) + b"\x00" * 32
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        storage: StorageService = real_server.state.storage

        # Upload to Vault Alpha
        res_a = real_client.post(
            "/tenants/ten_alpha/vaults/vlt_alpha/recordings/upload",
            headers={"x-dp-instance-id": "dp_alpha_phone"},
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({"segment_index": 0, "session_id": "sess_alpha_1"}),
            },
            files={"file": ("test.aac", valid_aac, "audio/aac")},
        )
        assert res_a.status_code == 201
        data_a = res_a.json()
        assert "vlt_alpha" in data_a["storage_key"]
        file_path_a = storage.resolve_path(data_a["storage_key"])
        assert file_path_a.is_file()

        # Upload to Vault Beta
        res_b = real_client.post(
            "/tenants/ten_beta/vaults/vlt_beta/recordings/upload",
            headers={"x-dp-instance-id": "dp_beta_laptop"},
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({"segment_index": 0, "session_id": "sess_beta_1"}),
            },
            files={"file": ("test.aac", valid_aac, "audio/aac")},
        )
        assert res_b.status_code == 201
        data_b = res_b.json()
        assert "vlt_beta" in data_b["storage_key"]
        file_path_b = storage.resolve_path(data_b["storage_key"])
        assert file_path_b.is_file()

        # Invariant: storage keys must belong strictly to their vault path prefix
        assert "vlt_alpha" in str(file_path_a) and "vlt_beta" not in str(file_path_a)
        assert "vlt_beta" in str(file_path_b) and "vlt_alpha" not in str(file_path_b)

    def test_upload_idempotency_and_cross_tenant_session_hijacking(self, real_client):
        """Verifies upload idempotency and prevents cross-tenant session hijacking."""
        valid_aac = build_adts_header(32) + b"\x00" * 32
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 1. First upload for sess_idempotent_001
        res1 = real_client.post(
            "/tenants/ten_alpha/vaults/vlt_alpha/recordings/upload",
            headers={"x-dp-instance-id": "dp_alpha_phone"},
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({"segment_index": 0, "session_id": "sess_idempotent_001"}),
            },
            files={"file": ("test.aac", valid_aac, "audio/aac")},
        )
        assert res1.status_code == 201
        res1_json = res1.json()

        # 2. Idempotent re-upload of the exact same segment
        res2 = real_client.post(
            "/tenants/ten_alpha/vaults/vlt_alpha/recordings/upload",
            headers={"x-dp-instance-id": "dp_alpha_phone"},
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({"segment_index": 0, "session_id": "sess_idempotent_001"}),
            },
            files={"file": ("test.aac", valid_aac, "audio/aac")},
        )
        assert res2.status_code == 201
        res2_json = res2.json()
        assert res1_json["asset_id"] == res2_json["asset_id"]
        assert res1_json["storage_key"] == res2_json["storage_key"]

        # 3. Cross-tenant hijacking attempt: Tenant Beta attempts to upload to sess_idempotent_001
        res_hijack = real_client.post(
            "/tenants/ten_beta/vaults/vlt_beta/recordings/upload",
            headers={"x-dp-instance-id": "dp_beta_laptop"},
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({"segment_index": 1, "session_id": "sess_idempotent_001"}),
            },
            files={"file": ("test.aac", valid_aac, "audio/aac")},
        )
        assert res_hijack.status_code == 403
        assert "belongs to a different tenant" in res_hijack.text


# =============================================================================
# 3. Voiceprint & Dual-Threshold Clustering Engine (`voiceprint.py`)
# =============================================================================

class TestAdvVoiceprintAndClustering:
    """White-box mathematical and algorithmic verification of voiceprint embedding operations."""

    def test_unit_normalize_edge_cases(self):
        """Tests vector unit normalization under extreme values, zeroes, and non-finite floats."""
        # 1. Normal vector
        v = np.array([3.0, 4.0] + [0.0] * 510, dtype=np.float32)
        norm_v = unit_normalize(v)
        assert np.isclose(np.linalg.norm(norm_v), 1.0, atol=1e-6)
        assert np.isclose(norm_v[0], 0.6)
        assert np.isclose(norm_v[1], 0.8)

        # 2. Zero vector -> ValueError
        with pytest.raises(ValueError) as exc:
            unit_normalize([0.0] * 512)
        assert "Zero vector cannot be normalized" in str(exc.value)

        # 3. Tiny sub-epsilon vector -> ValueError
        with pytest.raises(ValueError):
            unit_normalize([1e-15] * 512)

        # 4. Non-finite values (NaN / Inf) -> ValueError
        with pytest.raises(ValueError) as exc:
            unit_normalize([np.nan] + [1.0] * 511)
        assert "non-finite" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            unit_normalize([np.inf] + [1.0] * 511)
        assert "non-finite" in str(exc.value)

        # 5. Invalid dimensions (2D matrix) -> ValueError
        with pytest.raises(ValueError) as exc:
            unit_normalize(np.ones((2, 512)))
        assert "1-dimensional" in str(exc.value)

    def test_pack_unpack_embedding_integrity(self):
        """Verifies binary packing to IEEE-754 float32 bytes (2048 bytes for 512-dim)."""
        vec = generate_synthetic_embedding("speaker_test_seed")
        assert len(vec) == 512

        # Pack produces exactly 512 * 4 = 2048 bytes
        blob = pack_embedding(vec)
        assert len(blob) == 2048

        # Unpack restores original vector with machine precision
        unpacked = unpack_embedding(blob)
        assert len(unpacked) == 512
        assert np.allclose(vec, unpacked, atol=1e-6)

        # Unpacking invalid blob size (not multiple of 4) raises ValueError
        with pytest.raises(ValueError):
            unpack_embedding(blob[:2047])

        with pytest.raises(ValueError):
            unpack_embedding(b"")

    def test_cosine_similarity_and_dimension_mismatch(self):
        """Verifies cosine similarity properties and dimension mismatch validation."""
        v1 = unit_normalize(np.array([1.0, 0.0] + [0.0] * 510))
        v2 = unit_normalize(np.array([0.0, 1.0] + [0.0] * 510))
        v3 = unit_normalize(np.array([-1.0, 0.0] + [0.0] * 510))

        # Orthogonal: similarity ~ 0.0
        assert np.isclose(cosine_similarity(v1, v2), 0.0, atol=1e-6)

        # Parallel: similarity ~ 1.0
        assert np.isclose(cosine_similarity(v1, v1), 1.0, atol=1e-6)

        # Opposite: similarity ~ -1.0
        assert np.isclose(cosine_similarity(v1, v3), -1.0, atol=1e-6)

        # Dimension mismatch raises ValueError
        with pytest.raises(ValueError) as exc:
            cosine_similarity(v1, [1.0] * 256)
        assert "dimension mismatch" in str(exc.value)

    def test_centroid_singularity_and_cancellation(self):
        """Verifies that vector cancellation singularities in centroid calculations handle gracefully."""
        v = unit_normalize(np.ones(512))
        opp_v = -v

        # Perfectly opposite vectors (+v, -v) sum to zero vector
        # compute_centroid handles this singularity by safely falling back to stacked[0]
        c = compute_centroid([v, opp_v])
        assert len(c) == 512
        assert np.isclose(np.linalg.norm(c), 1.0, atol=1e-6)

        # Incremental update with cancellation: count=1, c_current=v, new_vec=opp_v
        inc_c = update_centroid_incremental(v, 1, opp_v)
        assert len(inc_c) == 512
        assert np.isclose(np.linalg.norm(inc_c), 1.0, atol=1e-6)

        # Incremental update with count <= 0 returns new vector
        inc_zero = update_centroid_incremental(v, 0, v)
        assert np.allclose(inc_zero, v, atol=1e-6)

        # EMA update with alpha bounds
        ema_c = update_centroid_ema(v, opp_v, alpha=1.5)  # Clamped to 1.0
        assert np.allclose(ema_c, v, atol=1e-6)

    def test_acoustic_qualification_boundaries(self):
        """Tests speech duration (<3s), SNR (<12dB), and clipping (>=0.5%) thresholds."""
        # 1. Duration < 3.0s (e.g. 2.0s = 32000 samples @ 16kHz) -> Rejected
        short_audio = (np.sin(np.linspace(0, 440 * 2 * np.pi * 2, 32000)) * 10000).astype(np.int16)
        passed, reason = check_acoustic_qualification(short_audio)
        assert not passed
        assert "Speech duration" in reason

        # 2. Low energy stationary noise (amplitude=20, energy=400 < 1000 threshold) -> SNR 0.0dB -> Rejected
        low_noise_audio = np.full(48000, 20, dtype=np.int16)
        passed_noise, reason_noise = check_acoustic_qualification(low_noise_audio)
        assert not passed_noise
        assert "SNR" in reason_noise

        # 3. Excessive clipping ratio >= 0.005 (0.5%) -> Rejected
        clipped_audio = np.zeros(48000, dtype=np.int16)
        clipped_audio[:500] = 32767  # 500 / 48000 = 1.04% clipping
        passed_clip, reason_clip = check_acoustic_qualification(clipped_audio)
        assert not passed_clip
        assert "Clipping ratio" in reason_clip

    def test_dual_threshold_matching_and_clustering(self):
        """Tests online identification (tau=0.78, margin=0.05) and clustering (tau=0.72, floor=0.60)."""
        base_alice = generate_synthetic_embedding("speaker_alice")
        alice_sample_near = generate_synthetic_embedding("speaker_alice", sample_id="take_1")
        base_bob = generate_synthetic_embedding("speaker_bob")

        enrolled = [
            {"speaker_id": "alice", "centroid": pack_embedding(base_alice)},
            {"speaker_id": "bob", "centroid": pack_embedding(base_bob)},
        ]

        # 1. Alice match: similarity > 0.90, margin > 0.80 -> Accepted
        match_res = match_enrolled_speaker(alice_sample_near, enrolled)
        assert match_res["accepted"] is True
        assert match_res["speaker_id"] == "alice"
        assert match_res["similarity"] >= 0.78

        # 2. Ambiguous match: runner-up margin < 0.05
        # Artificially construct runner up with similarity within 0.02
        v_query = unit_normalize(np.ones(512))
        v_cand1 = unit_normalize(np.ones(512))
        # Rotate slightly for 0.98 similarity
        v_cand2 = unit_normalize(0.98 * v_cand1 + 0.02 * generate_synthetic_embedding("noise"))
        ambiguous_enrolled = [
            {"speaker_id": "spk_1", "centroid": pack_embedding(v_cand1)},
            {"speaker_id": "spk_2", "centroid": pack_embedding(v_cand2)},
        ]
        amb_match = match_enrolled_speaker(v_query, ambiguous_enrolled, threshold=0.78, margin=0.05)
        assert amb_match["accepted"] is False  # Rejected due to runner-up margin

        # 3. Clustering engine actions
        clusters = [
            {"cluster_id": "cls_001", "embedding_blob": pack_embedding(base_alice)},
        ]

        # High similarity (>= 0.72) -> action="merge"
        c_merge = cluster_segment(alice_sample_near, clusters)
        assert c_merge["action"] == "merge"
        assert c_merge["cluster_id"] == "cls_001"

        # Low similarity (< 0.60) -> action="reject"
        c_reject = cluster_segment(base_bob, clusters)
        # Inter-speaker similarity is ~0.04
        assert c_reject["action"] == "reject"


# =============================================================================
# 4. Speakers, Mutations & Contract C6 Fallback (`speakers.py`)
# =============================================================================

class TestAdvSpeakersAndContractC6Fallback:
    """White-box verification of Contract C6 3-tier fallback resolution and cluster mutation APIs."""

    def test_contract_c6_three_tier_fallback_matrix(self):
        """Verifies full 3-tier dynamic speaker fallback hierarchy per Contract C6."""
        # Tier 1: Confirmed named profile
        ctx_t1 = {"speaker_profiles": {"spk_1": {"name": "Dr. Reed"}}}
        assert resolve_speaker_name("spk_1", ctx_t1) == "Dr. Reed"

        # Tier 1 Edge: Profile name is whitespace -> fallback to Tier 2
        ctx_t1_ws = {
            "speaker_profiles": {"spk_1": {"name": "   "}},
            "active_roster": {"spk_1": {"suspected_name": "Suspect David"}},
        }
        assert resolve_speaker_name("spk_1", ctx_t1_ws) == "Suspect David"

        # Tier 2: Active roster suspect
        ctx_t2 = {"active_roster": {"spk_2": {"suspected_name": "Consultant Sarah"}}}
        assert resolve_speaker_name("spk_2", ctx_t2) == "Consultant Sarah"

        # Tier 2 Edge: Suspect name is whitespace -> fallback to Tier 3
        ctx_t2_ws = {
            "active_roster": {"spk_2": {"suspected_name": "  "}},
            "unknown_speaker_index": 4,
        }
        assert resolve_speaker_name("spk_2", ctx_t2_ws) == "Speaker 4"

        # Tier 3: Sequential Unknown Speaker Index
        ctx_t3 = {"unknown_speaker_index": 2}
        assert resolve_speaker_name("spk_unassigned", ctx_t3) == "Speaker 2"

        # Generic label on utterance ("Unknown", "Speaker 3") does not override Tier 1 or Tier 2
        ctx_generic = {
            "speaker_profiles": {"spk_1": {"name": "Alice"}},
            "speaker_name": "Speaker 99",
        }
        assert resolve_speaker_name("spk_1", ctx_generic) == "Alice"

    def test_session_speaker_resolver_deterministic_indexing(self):
        """Verifies SessionSpeakerResolver maintains deterministic sequential indexing across a session."""
        resolver = SessionSpeakerResolver()

        # Unassigned speaker A gets "Speaker 1"
        assert resolver.resolve(speaker_id="spk_unknown_a") == "Speaker 1"

        # Unassigned speaker B gets "Speaker 2"
        assert resolver.resolve(speaker_id="spk_unknown_b") == "Speaker 2"

        # Unassigned speaker A resolved again gets the same "Speaker 1"
        assert resolver.resolve(speaker_id="spk_unknown_a") == "Speaker 1"

        # Unassigned cluster C gets "Speaker 3"
        assert resolver.resolve(cluster_id="cls_unknown_c") == "Speaker 3"

    def test_cluster_patch_mark_new_workflow(self, real_client, real_server):
        """Tests PATCH /clusters/{id} with action='mark_new' creates profile and updates messages."""
        db: Database = real_server.state.db

        with db.connect(immediate=True) as c:
            # Seed session, section, pending cluster and linked section message
            c.execute(
                """INSERT OR REPLACE INTO recording_sessions
                   (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at)
                   VALUES ('sess_adv_1', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'dp_alpha_phone', '2026-11-10', '2026-11-10T10:00:00Z')"""
            )
            c.execute(
                """INSERT OR REPLACE INTO voiceprint_clusters
                   (cluster_id, tenant_id, vault_id, status, confidence_score)
                   VALUES ('cls_adv_1', 'ten_alpha', 'vlt_alpha', 'pending', 0.88)"""
            )
            c.execute(
                """INSERT OR REPLACE INTO sections
                   (section_id, live_session_id, tenant_id, vault_id, section_index, start_ms, end_ms, title)
                   VALUES ('sec_adv_1', 'sess_adv_1', 'ten_alpha', 'vlt_alpha', 0, 0, 5000, 'Test Meeting')"""
            )
            c.execute(
                """INSERT OR REPLACE INTO section_messages
                   (message_id, section_id, cluster_id, speaker_name, content, start_ms, end_ms)
                   VALUES ('msg_adv_1', 'sec_adv_1', 'cls_adv_1', 'Unknown', 'We need to deploy this tomorrow.', 0, 5000)"""
            )

        # 1. Validation error: mark_new with empty name returns 422
        res_empty = real_client.patch(
            "/clusters/cls_adv_1",
            headers={"x-vault-id": "vlt_alpha"},
            json={"action": "mark_new", "name": "   "},
        )
        assert res_empty.status_code == 422

        # 2. Valid mark_new creates speaker profile and marks cluster enrolled
        res_ok = real_client.patch(
            "/clusters/cls_adv_1",
            headers={"x-vault-id": "vlt_alpha"},
            json={"action": "mark_new", "name": "Dr. Evelyn Reed", "role_label": "Lead Scientist"},
        )
        assert res_ok.status_code == 200
        patch_data = res_ok.json()
        assert patch_data["status"] == "enrolled"
        new_spk_id = patch_data["speaker_id"]
        assert new_spk_id.startswith("spk_")

        # 3. Verify section_messages retroactively updated
        row = db.one("SELECT speaker_id, speaker_name FROM section_messages WHERE message_id = 'msg_adv_1'")
        assert row["speaker_id"] == new_spk_id
        assert row["speaker_name"] == "Dr. Evelyn Reed"

    def test_cluster_patch_mark_existing_merge(self, real_client, real_server):
        """Tests PATCH /clusters/{id} with action='mark_existing' merges into existing profile."""
        db: Database = real_server.state.db

        with db.connect(immediate=True) as c:
            c.execute(
                """INSERT OR REPLACE INTO speaker_profiles
                   (speaker_id, tenant_id, vault_id, user_id, name)
                   VALUES ('spk_alice_exist', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'Alice Existing')"""
            )
            c.execute(
                """INSERT OR REPLACE INTO voiceprint_clusters
                   (cluster_id, tenant_id, vault_id, status)
                   VALUES ('cls_adv_merge', 'ten_alpha', 'vlt_alpha', 'pending')"""
            )

        # 1. Missing speaker_id -> 422
        res_miss = real_client.patch(
            "/clusters/cls_adv_merge",
            headers={"x-vault-id": "vlt_alpha"},
            json={"action": "mark_existing"},
        )
        assert res_miss.status_code == 422

        # 2. Non-existent speaker_id -> 404
        res_404 = real_client.patch(
            "/clusters/cls_adv_merge",
            headers={"x-vault-id": "vlt_alpha"},
            json={"action": "mark_existing", "speaker_id": "spk_nonexistent_xyz"},
        )
        assert res_404.status_code == 404

        # 3. Valid merge -> 200 with status='merged'
        res_merge = real_client.patch(
            "/clusters/cls_adv_merge",
            headers={"x-vault-id": "vlt_alpha"},
            json={"action": "mark_existing", "speaker_id": "spk_alice_exist"},
        )
        assert res_merge.status_code == 200
        assert res_merge.json()["status"] == "merged"

    def test_cluster_patch_dismiss_and_cross_vault_isolation(self, real_client, real_server):
        """Tests cluster dismissal and cross-vault mutation isolation (HTTP 404)."""
        db: Database = real_server.state.db

        with db.connect(immediate=True) as c:
            c.execute(
                """INSERT OR REPLACE INTO voiceprint_clusters
                   (cluster_id, tenant_id, vault_id, status)
                   VALUES ('cls_beta_isolate', 'ten_beta', 'vlt_beta', 'pending')"""
            )

        # Attempting to mutate Beta cluster using Alpha client -> 404 Not Found
        res_cross = real_client.patch(
            "/clusters/cls_beta_isolate",
            headers={"x-vault-id": "vlt_alpha"},
            json={"action": "dismiss"},
        )
        assert res_cross.status_code == 404


# =============================================================================
# 5. Server-Sent Events (SSE) Streaming Engine (`sse.py`)
# =============================================================================

class TestAdvSseProtocolAndBufferEviction:
    """White-box stress tests for SSE protocol framing, circular buffer eviction, and replay."""

    def test_w3c_sse_framing_and_headers(self):
        """Verifies exact W3C SSE frame formatting and double-newline wire termination."""
        frame = format_sse_frame("turn_001_001", "text_delta", {"delta": "Hello world"})
        assert frame == 'id: turn_001_001\nevent: text_delta\ndata: {"delta": "Hello world"}\n\n'
        assert frame.endswith("\n\n")

    def test_last_event_id_parsing_and_resilience(self):
        """Tests Last-Event-ID header parsing across valid, non-matching, and corrupt tokens."""
        # Valid matching header
        assert parse_last_event_id("turn_alpha_005", "turn_alpha") == 5

        # Matching with leading zeroes
        assert parse_last_event_id("turn_alpha_020", "turn_alpha") == 20

        # Non-matching turn prefix -> defaults to 0
        assert parse_last_event_id("turn_beta_005", "turn_alpha") == 0

        # Malformed non-numeric suffix -> defaults to 0
        assert parse_last_event_id("turn_alpha_corrupt", "turn_alpha") == 0

        # Absent or empty header -> defaults to 0
        assert parse_last_event_id(None, "turn_alpha") == 0
        assert parse_last_event_id("", "turn_alpha") == 0

    def test_turn_event_buffer_eviction_under_pressure(self):
        """Verifies TurnEventBuffer maxlen eviction bounds memory and keeps newest events."""
        buf = TurnEventBuffer(turn_id="turn_stress", thread_id="th_stress", max_events=256)

        # Append 300 events (exceeds 256 capacity)
        for i in range(1, 301):
            buf.append_event("text_delta", {"delta": f"chunk_{i}"})

        # Length is capped strictly at 256
        assert len(buf.events) == 256
        assert buf.events[0].seq == 45  # Oldest remaining event
        assert buf.events[-1].seq == 300  # Newest event

        # Replay from 0 yields all 256 available events
        events_from_0 = buf.get_events_after(0)
        assert len(events_from_0) == 256
        assert events_from_0[0].seq == 45

        # Replay from 200 yields exactly remaining 100 events
        events_from_200 = buf.get_events_after(200)
        assert len(events_from_200) == 100
        assert events_from_200[0].seq == 201

    def test_turn_event_buffer_post_completion_rejection(self):
        """Verifies that appending events after turn completion is strictly rejected."""
        buf = TurnEventBuffer(turn_id="turn_done", thread_id="th_done")
        buf.append_event("turn_progress", {"phase": "recalling"})
        buf.append_event("turn_completed", {"status": "completed"})

        assert buf.is_completed is True

        # Trailing events after completion return None and are not recorded
        rejected = buf.append_event("text_delta", {"delta": "phantom delta"})
        assert rejected is None
        assert len(buf.events) == 2

    def test_turn_stream_manager_expiration_and_410_gone(self):
        """Verifies TurnStreamManager purges expired buffers and marks them 410 Gone."""
        manager = TurnStreamManager(retention_seconds=0.01)
        buf = manager.get_or_create("turn_expiring", "th_expiring")
        buf.append_event("turn_completed", {"status": "completed"})

        # Wait for retention expiration
        time.sleep(0.02)
        assert buf.is_expired() is True

        # Cleanup purges buffer
        manager.cleanup()
        assert manager.get("turn_expiring") is None
        assert manager.is_expired_turn("turn_expiring") is True

    @pytest.mark.anyio
    async def test_subscriber_multiplexing_and_disconnection(self):
        """Verifies concurrent subscriber queue fanout and clean termination upon completion."""
        buf = TurnEventBuffer(turn_id="turn_fanout", thread_id="th_fanout")
        q1: asyncio.Queue = asyncio.Queue()
        q2: asyncio.Queue = asyncio.Queue()
        buf._subscribers.add(q1)
        buf._subscribers.add(q2)

        # Append event -> delivered to both subscribers
        buf.append_event("text_delta", {"delta": "hello"})
        ev1 = q1.get_nowait()
        ev2 = q2.get_nowait()
        assert ev1 is not None and ev1.data["delta"] == "hello"
        assert ev2 is not None and ev2.data["delta"] == "hello"

        # Complete turn -> None sentinel delivered to both subscribers
        buf.append_event("turn_completed", {"status": "completed"})
        sentinel1 = q1.get_nowait()
        sentinel2 = q2.get_nowait()
        assert sentinel1 is not None and sentinel1.event_type == "turn_completed"
        # Second item is the None termination sentinel
        assert q1.get_nowait() is None
        assert q2.get_nowait() is None


# =============================================================================
# 6. Gemini Conversational Agent & Tool Resilience (`chat.py` / `gemini.py`)
# =============================================================================

class TestAdvGeminiReasoningAndChat:
    """White-box verification of cognitive tool execution, FTS injection safety, and turn lifecycles."""

    def test_episodic_memory_tool_execution_resilience(self, real_server):
        """Verifies execute_tool_call handles SQL injection, FTS operators, and control characters safely."""
        db: Database = real_server.state.db

        # 1. SQL Injection attempt in query parameter
        res_sqli = execute_tool_call(
            db=db,
            vault_id="vlt_alpha",
            tool_name="search_memory",
            arguments={"query": "'; DROP TABLE users; --"},
        )
        assert res_sqli["status"] == "completed"
        assert isinstance(res_sqli["result"]["memories"], list)
        # Ensure tables were NOT dropped
        assert db.one("SELECT count(*) FROM users") is not None

        # 2. FTS5 syntax operator attack ('NEAR', '*', 'OR', 'NOT', unmatched quotes)
        res_fts = execute_tool_call(
            db=db,
            vault_id="vlt_alpha",
            tool_name="search_memory",
            arguments={"query": '"*"" NEAR(foo bar) OR NOT AND ***'},
        )
        assert res_fts["status"] == "completed"

        # 3. Unicode control characters and emojis
        res_ctrl = execute_tool_call(
            db=db,
            vault_id="vlt_alpha",
            tool_name="search_sections",
            arguments={"query": "\x00\x01\x1f \U0001F525 \U0001F4A1 architectural discussion"},
        )
        assert res_ctrl["status"] == "completed"
        assert isinstance(res_ctrl["result"]["sections"], list)

        # 4. Extreme 10,000 character query parameter
        res_huge = execute_tool_call(
            db=db,
            vault_id="vlt_alpha",
            tool_name="search_memory",
            arguments={"query": "meeting " * 1000},
        )
        assert res_huge["status"] == "completed"

    def test_search_sections_scoped_filters(self, real_server):
        """Verifies search_sections filter parameters (session_id, speaker_id) execute safely."""
        db: Database = real_server.state.db

        # Non-existent filters return 0 matches without throwing
        res = execute_tool_call(
            db=db,
            vault_id="vlt_alpha",
            tool_name="search_sections",
            arguments={
                "query": "budget",
                "session_id": "sess_nonexistent_999",
                "speaker_id": "spk_nonexistent_999",
            },
        )
        assert res["status"] == "completed"
        assert res["result"]["matches_count"] == 0

    @pytest.mark.anyio
    async def test_chat_turn_lifecycle_and_dual_key_compatibility(self, real_server):
        """Verifies full turn phase progression and Contract C4 dual-key compatibility."""
        db: Database = real_server.state.db
        buf = TurnEventBuffer(turn_id="turn_lifecycle_test", thread_id="th_lifecycle")
        agent = SyntheticGeminiAgent(db)

        await agent.execute_turn(
            buffer=buf,
            prompt="What did the team say about the deployment date?",
            vault_id="vlt_alpha",
            user_id="usr_alpha",
        )

        event_types = [ev.event_type for ev in buf.events]
        assert "turn_progress" in event_types
        assert "tool_call_started" in event_types
        assert "tool_call_completed" in event_types
        assert "text_delta" in event_types
        assert "turn_completed" in event_types

        # Contract C4 dual-key verification
        tc_start = next(ev for ev in buf.events if ev.event_type == "tool_call_started")
        assert "tool" in tc_start.data and "tool_name" in tc_start.data
        assert "call_id" in tc_start.data and "tool_call_id" in tc_start.data
        assert "args" in tc_start.data and "arguments" in tc_start.data

        tc_done = next(ev for ev in buf.events if ev.event_type == "tool_call_completed")
        assert "tool" in tc_done.data and "tool_name" in tc_done.data
        assert "result" in tc_done.data
        assert "elapsed_ms" in tc_done.data

    def test_in_flight_chat_turn_cancellation(self, real_client, real_server):
        """Verifies client turn cancellation endpoint halts stream and registers CANCELLED status."""
        # 1. Create a chat thread
        res_th = real_client.post(
            "/chat/threads",
            headers={"x-vault-id": "vlt_alpha"},
            json={"title": "Cancellation Test Thread"},
        )
        assert res_th.status_code == 201
        thread_id = res_th.json()["thread_id"]

        turn_id = f"turn_cancel_{secrets.token_hex(4)}"

        # 2. Cancel turn via API endpoint
        res_cancel = real_client.post(
            f"/chat/threads/{thread_id}/turns/{turn_id}/cancel",
            headers={"x-vault-id": "vlt_alpha"},
        )
        assert res_cancel.status_code == 200
        assert res_cancel.json()["status"] == "cancelled"

        # 3. Requesting SSE stream for this pre-cancelled turn immediately emits turn_failed CANCELLED
        res_stream = real_client.post(
            f"/chat/threads/{thread_id}/turns",
            headers={"x-vault-id": "vlt_alpha"},
            json={"message": "Test cancelled prompt", "client_turn_id": turn_id},
        )
        assert res_stream.status_code == 200
        body_text = res_stream.text
        assert "turn_failed" in body_text
        assert "CANCELLED" in body_text


# =============================================================================
# 7. Database Constraints, FTS5 & Transaction Locks (`db.py`)
# =============================================================================

class TestAdvDatabaseAndSchemaConstraints:
    """White-box verification of 14-table relational integrity, FTS tokenization, and locks."""

    def test_14_table_foreign_key_enforcement(self, real_server):
        """Verifies PRAGMA foreign_keys=ON rejects orphaned relational rows."""
        db: Database = real_server.state.db

        with db.connect() as c:
            # Foreign key violation: non-existent tenant_id in recording_sessions
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(
                    """INSERT INTO recording_sessions
                       (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at)
                       VALUES ('sess_orphan', 'ten_nonexistent', 'vlt_alpha', 'usr_alpha', 'dp_alpha_phone', '2026-11-10', '2026-11-10T10:00:00Z')"""
                )

            # Foreign key violation: non-existent section_id in section_messages
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(
                    """INSERT INTO section_messages
                       (message_id, section_id, content)
                       VALUES ('msg_orphan', 'sec_nonexistent', 'Orphaned speech')"""
                )

    def test_schema_check_constraints(self, real_server):
        """Verifies SQL CHECK constraints on cluster status and voiceprint consent."""
        db: Database = real_server.state.db

        with db.connect() as c:
            # Invalid cluster status violates chk_clusters_status
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(
                    """INSERT INTO voiceprint_clusters
                       (cluster_id, tenant_id, vault_id, status)
                       VALUES ('cls_bad_status', 'ten_alpha', 'vlt_alpha', 'bogus_state')"""
                )

            # Voiceprint consent must be 1 (consent=1 check constraint)
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(
                    """INSERT INTO voiceprints
                       (id, speaker_id, model_id, embedding, evidence_segment_id, consent, created_ms)
                       VALUES ('vp_no_consent', 'spk_any', 'model_1', '[]', 'seg_any', 0, 1000)"""
                )

    def test_fts5_token_sanitization(self):
        """Verifies sanitize_fts_tokens strips unsafe FTS5 operators and punctuation."""
        # Clean alphanumeric and unicode tokens
        tokens = sanitize_fts_tokens("meeting AND OR NOT * NEAR(5) 'architecture' ; --")
        assert "AND" in tokens
        assert "OR" in tokens
        assert "NOT" in tokens
        assert "architecture" in tokens
        assert "*" not in tokens
        assert ";" not in tokens
        assert "--" not in tokens

        # Control bytes and empty strings
        assert sanitize_fts_tokens("\x00\x01\x1f") == []
        assert sanitize_fts_tokens("   ") == []

    def test_concurrent_database_write_locks(self, real_server):
        """Verifies database handles concurrent write transactions without database lock errors."""
        db: Database = real_server.state.db
        errors = []

        def insert_mutation(idx: int):
            try:
                db.append_recording_mutation(
                    tenant_id="ten_alpha",
                    vault_id="vlt_alpha",
                    entity_type="segment",
                    entity_id=f"seg_concurrent_{idx}",
                    mutation_type="segment_uploaded",
                    payload_json={"segment_index": idx},
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=insert_mutation, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Encountered database lock errors: {errors}"
        rows, next_cursor, has_more = db.list_recording_mutations("vlt_alpha", 0)
        assert len(rows) >= 16


# =============================================================================
# 8. API Endpoints, Parameters & Status Codes (`api.py`)
# =============================================================================

class TestAdvApiEndpointsAndParameters:
    """White-box verification of REST endpoints, HTTP status codes, and query boundaries."""

    def test_speakers_crud_status_codes(self, real_client):
        """Tests /speakers endpoints: GET 200, POST 201, invalid POST 422, GET 404."""
        # 1. GET /speakers -> 200 OK
        res_list = real_client.get("/speakers", headers={"x-vault-id": "vlt_alpha"})
        assert res_list.status_code == 200
        assert "speakers" in res_list.json()

        # 2. POST /speakers with missing name -> 422 Unprocessable Content
        res_invalid = real_client.post(
            "/speakers",
            headers={"x-vault-id": "vlt_alpha"},
            json={"notes": "missing name field"},
        )
        assert res_invalid.status_code == 422

        # 3. POST /speakers valid -> 201 Created
        res_create = real_client.post(
            "/speakers",
            headers={"x-vault-id": "vlt_alpha"},
            json={"name": "Alice Innovator", "role_label": "Founder"},
        )
        assert res_create.status_code == 201
        spk_id = res_create.json()["speaker_id"]

        # 4. GET /speakers/{id} -> 200 OK
        res_get = real_client.get(f"/speakers/{spk_id}", headers={"x-vault-id": "vlt_alpha"})
        assert res_get.status_code == 200
        assert res_get.json()["name"] == "Alice Innovator"

        # 5. GET /speakers/nonexistent -> 404 Not Found
        res_404 = real_client.get("/speakers/spk_nonexistent_000", headers={"x-vault-id": "vlt_alpha"})
        assert res_404.status_code == 404

    def test_clusters_and_preview_endpoints(self, real_client, real_server):
        """Tests /clusters, /clusters/{id}, and /clusters/{id}/audio_preview endpoints."""
        db: Database = real_server.state.db

        with db.connect(immediate=True) as c:
            c.execute(
                """INSERT OR REPLACE INTO voiceprint_clusters
                   (cluster_id, tenant_id, vault_id, status)
                   VALUES ('cls_preview_test', 'ten_alpha', 'vlt_alpha', 'pending')"""
            )

        # 1. GET /clusters -> 200 OK
        res = real_client.get("/clusters", headers={"x-vault-id": "vlt_alpha"})
        assert res.status_code == 200
        assert "clusters" in res.json()

        # 2. GET /clusters/{id} -> 200 OK
        res_detail = real_client.get("/clusters/cls_preview_test", headers={"x-vault-id": "vlt_alpha"})
        assert res_detail.status_code == 200
        assert res_detail.json()["cluster_id"] == "cls_preview_test"

        # 3. GET /clusters/{id}/audio_preview -> 200 audio/aac
        res_audio = real_client.get("/clusters/cls_preview_test/audio_preview", headers={"x-vault-id": "vlt_alpha"})
        assert res_audio.status_code == 200
        assert res_audio.headers["content-type"] == "audio/aac"
        assert len(res_audio.content) > 50

        # 4. Non-existent cluster preview -> 404
        res_nonexist = real_client.get("/clusters/cls_nonexistent_xyz/audio_preview", headers={"x-vault-id": "vlt_alpha"})
        assert res_nonexist.status_code == 404

    def test_processing_chains_endpoint(self, real_client, real_server):
        """Tests GET /processing-chains and GET /processing-chains/{id} (Feature F4.4)."""
        db: Database = real_server.state.db

        # current_step must satisfy chk_chain_step: ('slicing', 'vad', 'transcribing', 'diarizing', 'summarizing')
        db.create_processing_chain(
            chain_id="chn_test_001",
            tenant_id="ten_alpha",
            vault_id="vlt_alpha",
            recording_id="rec_test_001",
            live_session_id=None,
            current_step="diarizing",
            progress_pct=50.0,
            status="running",
        )

        # 1. List processing chains
        res_list = real_client.get("/processing-chains", headers={"x-vault-id": "vlt_alpha"})
        assert res_list.status_code == 200
        chains = res_list.json()["chains"]
        assert any(c["chain_id"] == "chn_test_001" for c in chains)

        # 2. Get single processing chain
        res_get = real_client.get("/processing-chains/chn_test_001", headers={"x-vault-id": "vlt_alpha"})
        assert res_get.status_code == 200
        assert res_get.json()["current_step"] == "diarizing"

        # 3. Non-existent chain -> 404
        res_404 = real_client.get("/processing-chains/chn_nonexistent", headers={"x-vault-id": "vlt_alpha"})
        assert res_404.status_code == 404

    def test_recording_mutations_sync(self, real_client, real_server):
        """Tests GET and POST /recording-mutations for incremental cursor synchronization."""
        db: Database = real_server.state.db

        db.append_recording_mutation(
            tenant_id="ten_alpha",
            vault_id="vlt_alpha",
            entity_type="session",
            entity_id="sess_sync_001",
            mutation_type="session_created",
            payload_json={"title": "Sync Test"},
        )

        # GET mutations
        res_get = real_client.get("/recording-mutations?cursor=0", headers={"x-vault-id": "vlt_alpha"})
        assert res_get.status_code == 200
        muts_data = res_get.json()
        assert "mutations" in muts_data
        assert "next_cursor" in muts_data

        # POST client mutations
        res_post = real_client.post(
            "/recording-mutations",
            headers={"x-vault-id": "vlt_alpha"},
            json={
                "mutations": [
                    {
                        "mutation_id": "mut_client_001",
                        "entity_type": "segment",
                        "entity_id": "seg_client_001",
                        "mutation_type": "status_changed",
                        "payload": {"status": "synced"},
                    }
                ]
            },
        )
        assert res_post.status_code == 200
        assert res_post.json()["status"] == "synced"
        assert res_post.json()["processed_count"] == 1

    def test_healthz_endpoint(self, real_client):
        """Tests system liveness /healthz returns 200 OK and version."""
        res = real_client.get("/healthz")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "version" in data
