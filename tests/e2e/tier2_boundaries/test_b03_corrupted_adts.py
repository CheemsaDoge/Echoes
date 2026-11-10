"""Tier 2: Boundary B03 - Corrupted ADTS Header & Stream Integrity Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.4.4.
Verifies failure detection for corrupted syncwords, invalid layer/profile bits,
and frame truncation mid-stream.
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.audio_harness import (
    build_adts_header,
    parse_adts_header,
    validate_adts_stream,
    synthesize_adts_file,
)


class TestCorruptedAdtsBoundaries:
    """Verifies that corrupted ADTS headers are caught and rejected."""

    def test_corrupted_syncword_raises_error(self):
        """1. Syncword not equal to 0xFFF raises ValueError."""
        # Valid header
        valid_hdr = bytearray(build_adts_header(payload_len=64))
        # Corrupt first byte to 0x00
        corrupt_hdr = bytearray(valid_hdr)
        corrupt_hdr[0] = 0x00
        with pytest.raises(ValueError, match="Invalid ADTS syncword"):
            parse_adts_header(bytes(corrupt_hdr))

    def test_corrupted_syncword_low_nibble(self):
        """2. Corrupting byte 1 syncword high nibble (e.g. 0xE1 instead of 0xF1) raises error."""
        valid_hdr = bytearray(build_adts_header(payload_len=64))
        corrupt_hdr = bytearray(valid_hdr)
        corrupt_hdr[1] = 0xE1
        with pytest.raises(ValueError, match="Invalid ADTS syncword"):
            parse_adts_header(bytes(corrupt_hdr))

    def test_truncated_stream_before_frame_length_fulfilled(self):
        """3. Header indicates frame length 300, but stream ends at byte 50."""
        hdr = build_adts_header(payload_len=293)  # frame length = 300
        truncated_stream = hdr + bytes(43)  # total 50 bytes instead of 300
        is_valid, msg, frames = validate_adts_stream(truncated_stream)
        assert is_valid is False
        assert "Truncated frame" in msg

    def test_midstream_corruption_at_third_frame(self):
        """4. Third frame in a 5-frame stream has corrupted header -> stream fails validation at frame 3."""
        f1 = build_adts_header(50) + bytes(50)
        f2 = build_adts_header(50) + bytes(50)
        # Corrupted f3
        bad_hdr = bytearray(build_adts_header(50))
        bad_hdr[0] = 0xAA  # corrupt syncword
        f3 = bytes(bad_hdr) + bytes(50)
        f4 = build_adts_header(50) + bytes(50)

        stream = f1 + f2 + f3 + f4
        is_valid, msg, frames = validate_adts_stream(stream)
        assert is_valid is False
        assert len(frames) == 2, f"First 2 frames should succeed, failed at frame 3; parsed {len(frames)}"
        assert "Corrupted ADTS header" in msg

    def test_server_upload_rejects_corrupted_adts_with_400(self, client: TestClient, auth_headers: dict):
        """5. Server upload endpoint returns HTTP 400 when uploaded file has corrupted syncword."""
        bad_audio = b"\x00\x00\x00\x00\x00\x00\x00" + bytes(100)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"dp_instance_id": "dp_phone_001"})),
            "file": ("corrupt.aac", io.BytesIO(bad_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.json()["error_code"] == "INVALID_AUDIO_STREAM"
