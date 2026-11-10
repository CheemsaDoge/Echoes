"""Tier 1: Feature F2.2 & F2.3 - Single Primary Recorder Lease Arbitration Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 3.1.3 & Contract C3 & ORIGINAL_REQUEST.md R3.
Rejection codes:
- no_primary (403)
- not_primary (409)
- out_of_primary_window (403)
- invalid_time (409)
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.audio_harness import synthesize_adts_file


class TestPrimaryLeaseArbitration:
    """Verifies single primary recorder lease arbitration and rejection codes."""

    def test_single_primary_election(self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str]):
        """1. Electing a new primary device sets is_primary=True and returns effective_from."""
        # Add a secondary device (e.g. smartwatch)
        store.dp_instances["dp_watch_002"] = {
            "dp_instance_id": "dp_watch_002",
            "tenant_id": "tenant_alpha",
            "vault_id": "vault_primary",
            "user_id": "user_001",
            "is_primary": False,
            "device_name": "Pixel Watch 2",
        }

        res = client.put(
            "/primary-device",
            json={"dp_instance_id": "dp_watch_002", "vault_id": "vault_primary"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["dp_instance_id"] == "dp_watch_002"
        assert data["is_primary"] is True
        assert "effective_from" in data

        # Phone should now be demoted to non-primary
        assert store.dp_instances["dp_phone_001"]["is_primary"] is False
        assert store.dp_instances["dp_watch_002"]["is_primary"] is True

    def test_upload_rejected_no_primary(self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str], sample_adts_audio: bytes):
        """2. Upload returns HTTP 403 recording.upload_rejected_no_primary when no device is primary."""
        # Demote all devices in the vault
        for d in store.dp_instances.values():
            d["is_primary"] = False

        meta = {"recording_id": "rec_test_01", "dp_instance_id": "dp_phone_001"}
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "ambient"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta)),
            "file": ("test.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 403
        data = res.json()
        assert data["error_code"] == "recording.upload_rejected_no_primary"

    def test_upload_rejected_not_primary(self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str], sample_adts_audio: bytes):
        """3. Upload by a non-primary secondary device returns HTTP 409 recording.upload_rejected_not_primary."""
        # Ensure dp_phone_001 is primary, and create dp_watch_002 as secondary
        store.dp_instances["dp_phone_001"]["is_primary"] = True
        store.dp_instances["dp_watch_002"] = {
            "dp_instance_id": "dp_watch_002",
            "tenant_id": "tenant_alpha",
            "vault_id": "vault_primary",
            "user_id": "user_001",
            "is_primary": False,
            "device_name": "Pixel Watch 2",
        }

        # Attempt upload from dp_watch_002
        meta = {"recording_id": "rec_watch_01", "dp_instance_id": "dp_watch_002"}
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = {
            "started_at": (None, now_iso),
            "format": (None, "aac"),
            "source": (None, "watch"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta)),
            "file": ("watch.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 409
        data = res.json()
        assert data["error_code"] == "recording.upload_rejected_not_primary"
        assert data["primary_dp_instance_id"] == "dp_phone_001"

    def test_upload_rejected_invalid_time_future_drift(self, client: TestClient, auth_headers: dict[str, str], sample_adts_audio: bytes):
        """4. Upload timestamp >30s in the future returns HTTP 409 recording.upload_rejected_invalid_time."""
        future_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=45)
        meta = {"recording_id": "rec_future", "dp_instance_id": "dp_phone_001"}
        files = {
            "started_at": (None, future_time.isoformat()),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta)),
            "file": ("future.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 409
        data = res.json()
        assert data["error_code"] == "recording.upload_rejected_invalid_time"

    def test_upload_rejected_invalid_time_stale_stamp(self, client: TestClient, auth_headers: dict[str, str], sample_adts_audio: bytes):
        """5. Upload timestamp older than 7 days returns HTTP 409 recording.upload_rejected_invalid_time."""
        stale_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)
        meta = {"recording_id": "rec_stale", "dp_instance_id": "dp_phone_001"}
        files = {
            "started_at": (None, stale_time.isoformat()),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta)),
            "file": ("stale.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 409
        data = res.json()
        assert data["error_code"] == "recording.upload_rejected_invalid_time"

    def test_upload_rejected_out_of_primary_window(self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str], sample_adts_audio: bytes):
        """6. Upload timestamp outside listening schedule returns HTTP 403 recording.upload_rejected_out_of_primary_window."""
        now = datetime.datetime.now(datetime.timezone.utc)
        out_time = now - datetime.timedelta(minutes=10)
        # Configure weekly windows for tomorrow only, so today is completely outside the schedule
        tomorrow_weekday = (now.isoweekday() % 7) + 1
        user_id = "user_001"
        store.listening_schedules[user_id] = {
            "user_id": user_id,
            "revision": 2,
            "timezone": "UTC",
            "weekly_windows": [
                {"weekday": tomorrow_weekday, "window_start": "09:00", "window_end": "17:00", "start_minutes": 540, "end_minutes": 1020}
            ],
        }

        meta = {"recording_id": "rec_off_hours", "dp_instance_id": "dp_phone_001"}
        files = {
            "started_at": (None, out_time.isoformat()),
            "format": (None, "aac"),
            "source": (None, "phone"),
            "capture_timezone": (None, "UTC"),
            "capture_metadata": (None, json.dumps(meta)),
            "file": ("off_hours.aac", io.BytesIO(sample_adts_audio), "audio/aac"),
        }
        res = client.post(
            "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
            files=files,
            headers=auth_headers,
        )
        assert res.status_code == 403
        data = res.json()
        assert data["error_code"] == "recording.upload_rejected_out_of_primary_window"
