"""
backend/tests/test_m2_adversarial_empirical.py
Empirical Adversarial Challenge Test Harness for Milestone M2.
Authored by empirical_challenger (challenger_m2_2).

Evaluates:
1. 7-byte MPEG-4 ADTS bitstream validator (corrupted syncword, sample rates, MPEG ID/layer/profile,
   truncated frames at all boundaries, buffer fullness bit independence).
2. Oversized uploads exceeding max_upload_bytes (HTTP 413 & bounded memory stream).
3. Storage partitioning & directory traversal defense (tenant_id, vault_id, session_id, started_at).
4. Empty/corrupted metadata & zero-length payload handling.
5. Idempotency & concurrent deduplication stress testing.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import io
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.lease import LeaseErrorCode
from echoledger.storage import StorageService
from echoledger.upload import AdtsValidationError, validate_and_parse_adts


# --- Synthetic ADTS Packet Synthesis Helper ---
def make_adts_frame(
    payload: bytes = b"\x00" * 128,
    sample_rate_idx: int = 8,   # 8 = 16000 Hz
    channels: int = 1,          # 1 = Mono
    profile: int = 1,           # 1 = AAC-LC (MPEG-4 profile 2 -> ADTS profile-1 = 1)
    mpeg_id: int = 0,           # 0 = MPEG-4 Audio
    protection_absent: int = 1, # 1 = No CRC
    buffer_fullness: int = 0x7FF,
) -> bytes:
    flen = 7 + len(payload)
    h = bytearray(7)
    h[0] = 0xFF
    h[1] = 0xF0 | (mpeg_id << 3) | protection_absent
    h[2] = ((profile & 0x03) << 6) | ((sample_rate_idx & 0x0F) << 2) | ((channels >> 2) & 0x01)
    h[3] = ((channels & 0x03) << 6) | ((flen >> 11) & 0x03)
    h[4] = (flen >> 3) & 0xFF
    h[5] = ((flen & 0x07) << 5) | ((buffer_fullness >> 6) & 0x1F)
    h[6] = ((buffer_fullness & 0x3F) << 2) | 0x00
    return bytes(h) + payload


def make_adts_stream(num_frames: int = 5, payload_size: int = 128) -> bytes:
    frame = make_adts_frame(payload=b"\xAB" * payload_size)
    return frame * num_frames


@pytest.fixture
def env(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        api_token="owner-token-for-test-at-least-24-chars",
        run_worker=False,
    )
    app = create_app(settings)
    db: Database = app.state.db

    with db.connect(immediate=True) as conn:
        conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t_alpha', 'Alpha Tenant', 'alpha')")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_alpha_1', 't_alpha', 'Vault 1', 1)")
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_alice', 't_alpha', 'alice@alpha.com', 'Alice')")
        conn.execute("""
            INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
            VALUES ('dp_phone', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Phone', 'phone', 1)
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
# Section 1: ADTS Bitstream Verification Engine Stress & Fuzzing
# ============================================================================

class TestAdtsBitstreamAdversarial:

    def test_adts_empty_payload_rejected(self):
        """Zero-length buffer fails immediately."""
        with pytest.raises(AdtsValidationError, match="too short"):
            validate_and_parse_adts(b"")

    @pytest.mark.parametrize("length", [1, 2, 3, 4, 5, 6])
    def test_adts_sub_header_truncation_rejected(self, length: int):
        """Buffers smaller than 7 bytes fail header parsing."""
        with pytest.raises(AdtsValidationError, match="too short"):
            validate_and_parse_adts(b"\xFF" * length)

    def test_adts_syncword_all_bit_flips_rejected(self):
        """Every single bit-flip in the 12-bit syncword (b0, b1[7..4]) must be rejected."""
        base = bytearray(make_adts_frame())

        # Byte 0 (8 bits)
        for bit in range(8):
            corrupt = bytearray(base)
            corrupt[0] ^= (1 << bit)
            with pytest.raises(AdtsValidationError, match="Invalid ADTS syncword"):
                validate_and_parse_adts(bytes(corrupt))

        # Byte 1 high nibble (4 bits)
        for bit in range(4, 8):
            corrupt = bytearray(base)
            corrupt[1] ^= (1 << bit)
            with pytest.raises(AdtsValidationError, match="Invalid ADTS syncword"):
                validate_and_parse_adts(bytes(corrupt))

    def test_adts_mpeg_id_bit_flip_rejected(self):
        """MPEG-2 AAC bit (b1 bit 3) must be rejected."""
        corrupt = bytearray(make_adts_frame())
        corrupt[1] ^= (1 << 3)
        with pytest.raises(AdtsValidationError, match="Invalid MPEG ID"):
            validate_and_parse_adts(bytes(corrupt))

    @pytest.mark.parametrize("layer_bits", [0x02, 0x04, 0x06])
    def test_adts_layer_bits_rejected(self, layer_bits: int):
        """Non-zero layer bits in b1[2..1] must be rejected."""
        corrupt = bytearray(make_adts_frame())
        corrupt[1] |= layer_bits
        with pytest.raises(AdtsValidationError, match="Invalid layer"):
            validate_and_parse_adts(bytes(corrupt))

    def test_adts_protection_absent_crc_rejected(self):
        """Protection absent = 0 (CRC present, 9-byte header) must be rejected."""
        corrupt = bytearray(make_adts_frame(protection_absent=0))
        with pytest.raises(AdtsValidationError, match="Protection absent must be 1"):
            validate_and_parse_adts(bytes(corrupt))

    @pytest.mark.parametrize("bad_profile", [0, 2, 3])
    def test_adts_profile_variants_rejected(self, bad_profile: int):
        """Profiles other than AAC-LC (1) must be rejected."""
        corrupt = bytearray(make_adts_frame(profile=bad_profile))
        with pytest.raises(AdtsValidationError, match="Invalid profile"):
            validate_and_parse_adts(bytes(corrupt))

    @pytest.mark.parametrize("bad_sr_idx", [0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15])
    def test_adts_sample_rates_rejected(self, bad_sr_idx: int):
        """Any sample rate other than index 8 (16000 Hz) must be rejected."""
        corrupt = bytearray(make_adts_frame(sample_rate_idx=bad_sr_idx))
        with pytest.raises(AdtsValidationError, match="Invalid sample rate index"):
            validate_and_parse_adts(bytes(corrupt))

    @pytest.mark.parametrize("bad_channel", [0, 2, 3, 4, 5, 6, 7])
    def test_adts_channel_configs_rejected(self, bad_channel: int):
        """Any channel configuration other than 1 (Mono) must be rejected."""
        corrupt = bytearray(make_adts_frame(channels=bad_channel))
        with pytest.raises(AdtsValidationError, match="Invalid channel configuration"):
            validate_and_parse_adts(bytes(corrupt))

    @pytest.mark.parametrize("bad_flen", [0, 1, 5, 6])
    def test_adts_frame_length_under_minimum_rejected(self, bad_flen: int):
        """Frame lengths < 7 bytes must be rejected and not cause infinite loops."""
        frame = bytearray(make_adts_frame(payload=b""))
        # Force frame length in b3, b4, b5
        frame[3] = (frame[3] & 0xFC) | ((bad_flen >> 11) & 0x03)
        frame[4] = (bad_flen >> 3) & 0xFF
        frame[5] = ((bad_flen & 0x07) << 5) | (frame[5] & 0x1F)
        with pytest.raises(AdtsValidationError, match="Invalid frame length"):
            validate_and_parse_adts(bytes(frame))

    def test_adts_frame_payload_truncation_rejected(self):
        """Header claims 135 bytes, but buffer has only 70 bytes."""
        frame = make_adts_frame(payload=b"\x00" * 128)
        truncated = frame[:70]
        with pytest.raises(AdtsValidationError, match="Truncated frame at index 0"):
            validate_and_parse_adts(truncated)

    @pytest.mark.parametrize("trailing_len", [1, 2, 3, 4, 5, 6])
    def test_adts_trailing_bytes_after_valid_frames_rejected(self, trailing_len: int):
        """Valid frames followed by 1..6 trailing garbage bytes must fail."""
        stream = make_adts_stream(3) + (b"\x99" * trailing_len)
        with pytest.raises(AdtsValidationError, match="Truncated ADTS header at offset"):
            validate_and_parse_adts(stream)

    def test_adts_mid_stream_corrupt_syncword_detected(self):
        """Corruption at frame 4 of 5-frame stream is accurately caught."""
        f1 = make_adts_frame(b"1" * 64)
        f2 = make_adts_frame(b"2" * 64)
        f3 = make_adts_frame(b"3" * 64)
        bad_f4 = bytearray(make_adts_frame(b"4" * 64))
        bad_f4[0] = 0xAA  # corrupt syncword
        f5 = make_adts_frame(b"5" * 64)

        stream = f1 + f2 + f3 + bytes(bad_f4) + f5
        with pytest.raises(AdtsValidationError, match="Invalid ADTS syncword .* at frame 3"):
            validate_and_parse_adts(stream)

    def test_adts_buffer_fullness_does_not_corrupt_framing(self):
        """Varying buffer fullness across CBR/VBR values does not corrupt frame parsing."""
        for bf in [0x000, 0x123, 0x555, 0x7FE, 0x7FF]:
            f = make_adts_frame(payload=b"\x01" * 100, buffer_fullness=bf)
            meta = validate_and_parse_adts(f)
            assert meta.frame_count == 1
            assert meta.duration_ms == 64
            assert meta.file_size_bytes == 107


# ============================================================================
# Section 2: Oversized Uploads & Memory Exhaustion Resistance
# ============================================================================

class TestOversizedUploads:

    def test_upload_exceeding_max_bytes_returns_413(self, env):
        """Files exceeding max_upload_bytes return HTTP 413 without reading unbounded streams."""
        app, _, _, client, headers = env
        app.state.upload_service.max_upload_bytes = 1000

        oversized_data = make_adts_stream(num_frames=15, payload_size=100) # 15 * 107 = 1605 bytes > 1000
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        files = {"file": ("oversized.aac", oversized_data, "audio/aac")}
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
        }
        res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
        assert res.status_code == 413
        assert "exceeds maximum size limit" in res.json()["detail"]

    def test_upload_within_limit_succeeds(self, env):
        """Files within max_upload_bytes succeed."""
        app, _, _, client, headers = env
        app.state.upload_service.max_upload_bytes = 2000

        data_stream = make_adts_stream(num_frames=10, payload_size=100) # 1070 bytes <= 2000
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        files = {"file": ("chunk.aac", data_stream, "audio/aac")}
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
        }
        res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
        assert res.status_code == 201


# ============================================================================
# Section 3: Storage Partitioning & Directory Traversal Attacks
# ============================================================================

class TestStorageDirectoryTraversal:

    @pytest.mark.parametrize("traversal_input", [
        "../../etc/passwd",
        "....//....//shadow",
        "..\\..\\windows\\system32",
        "t_alpha/../../../var",
        "../",
        "..",
        ".",
        "...",
    ])
    def test_storage_service_neutralizes_traversal_segments(self, traversal_input: str, tmp_path: Path):
        """StorageService neutralizes traversal attempts and enforces path containment."""
        storage = StorageService(data_dir=tmp_path)
        key, meta_key = storage.save_recording(
            tenant_id=traversal_input,
            vault_id=traversal_input,
            session_id=traversal_input,
            segment_index=0,
            started_at=traversal_input,
            audio_bytes=b"dummy",
            meta_dict={"safe": True},
        )

        audio_path = storage.resolve_path(key)
        meta_path = storage.resolve_path(meta_key)

        assert audio_path.resolve().is_relative_to(tmp_path.resolve())
        assert meta_path.resolve().is_relative_to(tmp_path.resolve())
        assert ".." not in str(audio_path)
        assert audio_path.exists()
        assert meta_path.exists()

    def test_traversal_in_upload_metadata_session_id(self, env):
        """Client attempting path traversal via session_id in capture_metadata is sanitized."""
        _, _, settings, client, headers = env
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        meta = {
            "live_session_id": "../../../../../etc/malicious_session",
            "dp_instance_id": "dp_phone",
            "segment_index": 0,
        }
        stream = make_adts_stream(3)
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_metadata": json.dumps(meta),
        }

        res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
        assert res.status_code == 201
        storage_key = res.json()["storage_key"]
        assert ".." not in storage_key
        assert "malicious_session" in storage_key
        # Verify file is inside data_dir
        audio_file = settings.data_dir / storage_key.replace("data/", "")
        assert audio_file.resolve().is_relative_to(settings.data_dir.resolve())
        assert audio_file.exists()


# ============================================================================
# Section 4: Empty & Corrupted Metadata & Payload Forms
# ============================================================================

class TestMalformedUploadInputs:

    def test_empty_audio_payload_rejected_400(self, env):
        """Zero-byte audio upload returns HTTP 400 empty_audio_file."""
        _, _, _, client, headers = env
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        files = {"file": ("empty.aac", b"", "audio/aac")}
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
        }
        res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
        assert res.status_code == 400
        assert res.json()["error_code"] == "empty_audio_file"

    @pytest.mark.parametrize("corrupt_meta", [
        "",
        "{broken json",
        "null",
        "[]",
        "123",
        "\"string_only\"",
    ])
    def test_corrupt_capture_metadata_rejected_422(self, corrupt_meta: str, env):
        """Malformed or non-dict capture_metadata returns HTTP 422."""
        _, _, _, client, headers = env
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(3)
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_metadata": corrupt_meta,
        }
        res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
        assert res.status_code == 422

    def test_clock_drift_future_rejected_409(self, env):
        """Timestamp >30s in future returns HTTP 409 recording.upload_rejected_invalid_time."""
        _, _, _, client, headers = env
        future_iso = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=45)).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(3)
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        data = {
            "started_at": future_iso,
            "format": "aac",
            "source": "phone",
            "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
        }
        res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
        assert res.status_code == 409
        assert res.json()["error_code"] == LeaseErrorCode.INVALID_TIME

    def test_clock_drift_stale_rejected_409(self, env):
        """Timestamp >7 days stale returns HTTP 409 recording.upload_rejected_invalid_time."""
        _, _, _, client, headers = env
        stale_iso = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(3)
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        data = {
            "started_at": stale_iso,
            "format": "aac",
            "source": "phone",
            "capture_metadata": json.dumps({"dp_instance_id": "dp_phone"}),
        }
        res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
        assert res.status_code == 409
        assert res.json()["error_code"] == LeaseErrorCode.INVALID_TIME


# ============================================================================
# Section 5: Idempotency & Concurrency Stress (Defect Exposure)
# ============================================================================

class TestIdempotencyAndConcurrencyDefects:

    def test_sequential_idempotent_retry_succeeds(self, env):
        """Consecutive identical uploads return consistent receipts without error."""
        _, _, _, client, headers = env
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(5)
        meta = {
            "live_session_id": "sess_idemp_seq",
            "segment_index": 0,
            "dp_instance_id": "dp_phone",
        }
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_metadata": json.dumps(meta),
        }

        # First upload
        files1 = {"file": ("chunk.aac", stream, "audio/aac")}
        res1 = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files1, headers=headers)
        assert res1.status_code == 201
        body1 = res1.json()

        # Second upload
        files2 = {"file": ("chunk.aac", stream, "audio/aac")}
        res2 = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files2, headers=headers)
        assert res2.status_code == 201
        body2 = res2.json()

        assert body1["storage_key"] == body2["storage_key"]
        assert body1["asset_id"] == body2["asset_id"]

    def test_concurrent_identical_uploads_resolved_idempotent(self, env):
        """
        REMEDIATED BEHAVIOR:
        Concurrent uploads of the identical (live_session_id, segment_index) all succeed
        with HTTP 201 and return the identical idempotent receipt without crashing.
        """
        _, _, _, client, headers = env
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(5)
        meta = {
            "live_session_id": "sess_concurrent_remediated",
            "segment_index": 0,
            "dp_instance_id": "dp_phone",
        }
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_metadata": json.dumps(meta),
        }

        def upload_call():
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            return client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)

        # Launch 5 concurrent threads attempting upload
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(upload_call) for _ in range(5)]
            results = [f.result() for f in futures]
            for res in results:
                assert res.status_code == 201, f"Expected 201, got {res.status_code}: {res.text}"
            asset_ids = {r.json()["asset_id"] for r in results}
            assert len(asset_ids) == 1, "All concurrent uploads must resolve to identical asset receipt"

    def test_non_integer_segment_index_returns_422(self, env):
        """
        REMEDIATED BEHAVIOR:
        capture_metadata with non-integer segment_index returns HTTP 422 Unprocessable Content.
        """
        _, _, _, client, headers = env
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(3)
        meta = {
            "live_session_id": "sess_bad_idx",
            "segment_index": "not_an_int",
            "dp_instance_id": "dp_phone",
        }
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_metadata": json.dumps(meta),
        }
        files = {"file": ("chunk.aac", stream, "audio/aac")}

        res = client.post("/tenants/t_alpha/vaults/v_alpha_1/recordings/upload", data=data, files=files, headers=headers)
        assert res.status_code == 422
        assert "Invalid segment_index" in res.json().get("detail", "")
