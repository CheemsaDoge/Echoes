"""Tier 2: Boundary B02 - Zero-Byte Files & Empty Speech Segments Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.4.4 & 2.5.4.
Verifies robust rejection of zero-byte, truncated, or empty audio files and zero-speech intervals.
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.audio_harness import (
    build_adts_header,
    validate_adts_stream,
    evaluate_rollover,
)


class TestZeroByteAndEmptyAudioBoundaries:
    """Verifies handling of zero-byte, sub-header, and empty speech segments."""

    def _post_audio_bytes(self, client: TestClient, auth_headers: dict, audio_bytes: bytes):
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        meta = {"recording_id": "rec_edge", "dp_instance_id": "dp_phone_001"}
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta)),
            "file": ("edge.aac", io.BytesIO(audio_bytes), "audio/aac"),
        }
        return client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )

    def test_zero_byte_audio_upload_rejected_400(self, client: TestClient, auth_headers: dict):
        """1. Zero-byte file returns HTTP 400 Bad Request with INVALID_AUDIO_STREAM."""
        res = self._post_audio_bytes(client, auth_headers, b"")
        assert res.status_code == 400
        assert res.json()["error_code"] == "INVALID_AUDIO_STREAM"

    def test_sub_header_truncation_less_than_7_bytes_rejected(self, client: TestClient, auth_headers: dict):
        """2. Audio file of 1 to 6 bytes (truncated header) returns HTTP 400 Bad Request."""
        for byte_count in [1, 3, 6]:
            partial = b"\xFF\xF1\x60\x40\x10\xFF"[:byte_count]
            res = self._post_audio_bytes(client, auth_headers, partial)
            assert res.status_code == 400
            assert res.json()["error_code"] == "INVALID_AUDIO_STREAM"

    def test_adts_stream_validator_reports_empty_stream(self):
        """3. Audio validator utility flags 0-byte stream explicitly."""
        is_valid, msg, frames = validate_adts_stream(b"")
        assert is_valid is False
        assert "empty" in msg.lower()
        assert frames == []

    def test_header_with_zero_payload_validates_frame_length_7(self):
        """4. Frame with 0 payload length (frame length = 7) is parsed with payload_length=0."""
        hdr = build_adts_header(payload_len=0)
        is_valid, msg, frames = validate_adts_stream(hdr)
        assert is_valid is True
        assert len(frames) == 1
        assert frames[0]["payload_len"] == 0

    def test_zero_duration_rollover_decision(self):
        """5. Duration of 0ms or silence of 0ms never triggers slicing."""
        should_cut, reason = evaluate_rollover(duration_ms=0, silence_duration_ms=0, dbfs=-60.0)
        assert should_cut is False
        assert reason is None

    def test_empty_metadata_string_rejected_422(self, client: TestClient, auth_headers: dict, sample_adts_audio: bytes):
        """6. Empty string in capture_metadata returns HTTP 422 Unprocessable."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, ""),
            "file": ("edge.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 422
