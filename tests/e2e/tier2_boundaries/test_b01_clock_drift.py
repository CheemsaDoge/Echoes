"""Tier 2: Boundary B01 - Clock Drift Boundary & Corner Case Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 3.1.3 & 2.5.5.
Rules:
- started_at > server_time + 30s -> REJECTED (recording.upload_rejected_invalid_time)
- started_at < server_time - 7 days -> REJECTED (recording.upload_rejected_invalid_time)
- Boundaries tested: 29s vs 31s, 6d23h vs 7d1h, timezone offsets.
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient


class TestClockDriftBoundaries:
    """Verifies clock drift threshold boundaries on audio segment uploads."""

    def _upload_with_timestamp(self, client: TestClient, auth_headers: dict, audio: bytes, started_at_iso: str):
        meta = {"recording_id": "rec_drift_test", "dp_instance_id": "dp_phone_001"}
        files = {
            "started_at": (None, started_at_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta)),
            "file": ("drift.aac", io.BytesIO(audio), "audio/aac"),
        }
        return client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )

    def test_future_drift_acceptable_at_29s(self, client: TestClient, auth_headers: dict, sample_adts_audio: bytes):
        """1. Boundary: 29 seconds ahead of server clock is within <=30s tolerance -> HTTP 201."""
        t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=29)
        res = self._upload_with_timestamp(client, auth_headers, sample_adts_audio, t.isoformat())
        assert res.status_code == 201, f"29s future drift should be accepted, got {res.status_code}: {res.text}"

    def test_future_drift_rejected_at_31s(self, client: TestClient, auth_headers: dict, sample_adts_audio: bytes):
        """2. Boundary: 31 seconds ahead of server clock exceeds >30s threshold -> HTTP 409."""
        t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=31)
        res = self._upload_with_timestamp(client, auth_headers, sample_adts_audio, t.isoformat())
        assert res.status_code == 409
        assert res.json()["error_code"] == "recording.upload_rejected_invalid_time"

    def test_stale_timestamp_acceptable_at_6d23h(self, client: TestClient, auth_headers: dict, sample_adts_audio: bytes):
        """3. Boundary: 6 days 23 hours in the past is within <=7 days tolerance -> HTTP 201."""
        t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=6, hours=23)
        res = self._upload_with_timestamp(client, auth_headers, sample_adts_audio, t.isoformat())
        assert res.status_code == 201, f"6d23h stale timestamp should be accepted, got {res.status_code}: {res.text}"

    def test_stale_timestamp_rejected_at_7d1h(self, client: TestClient, auth_headers: dict, sample_adts_audio: bytes):
        """4. Boundary: 7 days 1 hour in the past exceeds >7 days stale threshold -> HTTP 409."""
        t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7, hours=1)
        res = self._upload_with_timestamp(client, auth_headers, sample_adts_audio, t.isoformat())
        assert res.status_code == 409
        assert res.json()["error_code"] == "recording.upload_rejected_invalid_time"

    def test_timezone_offset_iso_string_parsed_correctly(self, client: TestClient, auth_headers: dict, sample_adts_audio: bytes):
        """5. Non-UTC ISO string (e.g. +08:00) at current local time is parsed correctly without false future drift."""
        tz_cst = datetime.timezone(datetime.timedelta(hours=8))
        now_cst = datetime.datetime.now(tz_cst)
        res = self._upload_with_timestamp(client, auth_headers, sample_adts_audio, now_cst.isoformat())
        assert res.status_code == 201

    def test_invalid_malformed_timestamp_string_returns_422(self, client: TestClient, auth_headers: dict, sample_adts_audio: bytes):
        """6. Non-date garbage string in started_at returns HTTP 422 Unprocessable."""
        res = self._upload_with_timestamp(client, auth_headers, sample_adts_audio, "invalid-date-not-an-iso-string")
        assert res.status_code == 422
