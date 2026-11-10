"""Tier 1: Feature F2.4 - 6-Field Multipart Audio Ingestion Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.5.2 & Contract C2 & ORIGINAL_REQUEST.md R3.
Multipart fields:
1. started_at (ISO 8601 UTC)
2. format ('aac')
3. source ('ambient' | 'phone' | 'watch' | 'app_default')
4. capture_timezone (IANA string)
5. capture_metadata (JSON string)
6. file (binary ADTS AAC)
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.audio_harness import synthesize_adts_file


class TestMultipartAudioUpload:
    """Verifies the 6-field multipart audio upload API."""

    def test_successful_multipart_upload_201_response(
        self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str], sample_adts_audio: bytes
    ):
        """1. Full valid 6-field upload returns HTTP 201 Created with canonical keys."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        metadata = {
            "recording_id": "rec_01JCG9Y91B3K4P0W8A",
            "dp_instance_id": "dp_phone_001",
            "sample_rate": 16000,
            "channels": 1,
            "bitrate_kbps": 48,
            "split_reason": "silence_window",
            "battery_level": 0.85,
            "is_charging": False,
        }
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "ambient"),
            "capture_timezone": (None, "Asia/Shanghai"),
            "capture_metadata": (None, json.dumps(metadata)),
            "file": ("seg_01JCG9Y91B.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }

        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 201, f"Expected 201 Created, got {res.status_code}: {res.text}"
        data = res.json()
        assert data["recording_id"] == "rec_01JCG9Y91B3K4P0W8A"
        assert "asset_id" in data
        assert "artifact_id" in data
        assert "storage_key" in data
        assert data["status"] == "queued_for_processing"

    def test_missing_started_at_field_rejected(
        self, client: TestClient, auth_headers: dict[str, str], sample_adts_audio: bytes
    ):
        """2. Missing started_at field returns HTTP 422 Unprocessable."""
        files = {
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"dp_instance_id": "dp_phone_001"})),
            "file": ("audio.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 422
        assert "started_at" in res.json()["detail"]

    def test_invalid_format_field_rejected(
        self, client: TestClient, auth_headers: dict[str, str], sample_adts_audio: bytes
    ):
        """3. Unsupported format (e.g. 'wav' or 'mp3') returns HTTP 422."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "mp3"),  # Must be 'aac'
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"dp_instance_id": "dp_phone_001"})),
            "file": ("audio.mp3", io.BytesIO(sample_adts_audio), "audio/mpeg"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 422
        assert "format" in res.json()["detail"]

    def test_corrupted_capture_metadata_json_rejected(
        self, client: TestClient, auth_headers: dict[str, str], sample_adts_audio: bytes
    ):
        """4. Malformed JSON string in capture_metadata returns HTTP 422."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, "{broken_json: true,"),
            "file": ("audio.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 422

    def test_storage_partitioning_by_vault(
        self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str], sample_adts_audio: bytes
    ):
        """5. Successful upload partitions storage key by vault_id."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "ambient"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps({"dp_instance_id": "dp_phone_001"})),
            "file": ("test.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 201
        key = res.json()["storage_key"]
        assert key.startswith("recordings/vault_primary/")
