"""Tier 3: Pairwise P01 - Primary Lease Change While Recording Is In-Flight.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 3.1.3 & Section 2.5.5.
Cross-feature interaction between:
1. Recording Foreground Service & UploadWorker
2. Single Primary Lease Arbitration (/primary-device)
3. Multipart Upload Rejection & IPC Shutdown
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.queue_harness import MockUploadQueueStorage
from tests.e2e.harness.audio_harness import synthesize_adts_file


class TestPrimaryLeaseInflightInteraction:
    """Verifies behavior when primary lease changes while a secondary device has in-flight recording."""

    def test_lease_preemption_causes_upload_rejection_and_worker_quarantine(
        self, client: TestClient, store: MemoryStore, queue_storage: MockUploadQueueStorage, sample_adts_audio: bytes
    ):
        """Device A records while primary -> Device B is elected primary -> Device A upload gets 409 not_primary."""
        # 1. Setup: Device A (phone) is primary
        store.dp_instances["dp_phone_001"]["is_primary"] = True
        store.dp_instances["dp_watch_002"] = {
            "dp_instance_id": "dp_watch_002",
            "tenant_id": "tenant_alpha",
            "vault_id": "vault_primary",
            "user_id": "user_001",
            "is_primary": False,
            "device_name": "Pixel Watch 2",
        }
        token = "token_alpha_user_001"
        auth_headers = {"Authorization": f"Bearer {token}"}

        # Device A enqueues an audio segment while recording
        item_a = queue_storage.enqueue(
            upload_id="upl_inflight_a",
            recording_id="rec_phone_sess",
            segment_index=0,
            audio_bytes=sample_adts_audio,
            meta_dict={
                "recording_id": "rec_phone_sess",
                "dp_instance_id": "dp_phone_001",
                "tenant_id": "tenant_alpha",
            },
        )

        # 2. In-flight lease switch: User switches primary device to watch
        lease_res = client.put(
            "/primary-device",
            json={"dp_instance_id": "dp_watch_002", "vault_id": "vault_primary"},
            headers=auth_headers,
        )
        assert lease_res.status_code == 200
        assert store.dp_instances["dp_phone_001"]["is_primary"] is False
        assert store.dp_instances["dp_watch_002"]["is_primary"] is True

        # 3. Device A's WorkManager attempts to upload the queued segment
        def server_upload(item, meta):
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            files = {
                "started_at": (None, now_iso),
                "format": (None, "aac"),
                "source": (None, "phone"),
                "capture_timezone": (None, "UTC"),
                "capture_metadata": (None, json.dumps(meta)),
                "file": ("inflight.aac", open(item.file_path, "rb"), "audio/aac"),
            }
            res = client.post(
                "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
                files=files,
                headers=auth_headers,
            )
            return res.status_code == 201, res.status_code

        results = queue_storage.process_worker_tick(server_upload_fn=server_upload)

        # 4. Verification: Server returned 409, WorkManager quarantined the segment without retrying
        assert len(results) == 1
        assert results[0]["action"] == "permanent_failure"
        assert results[0]["code"] == 409
        assert item_a.status == "FAILED_PERMANENT"
        assert item_a.upload_block_reason == "http_409"

    def test_new_primary_device_upload_succeeds_immediately(
        self, client: TestClient, store: MemoryStore, queue_storage: MockUploadQueueStorage, sample_adts_audio: bytes
    ):
        """After lease transfer, newly elected primary device uploads successfully."""
        store.dp_instances["dp_phone_001"]["is_primary"] = False
        store.dp_instances["dp_watch_002"] = {
            "dp_instance_id": "dp_watch_002",
            "tenant_id": "tenant_alpha",
            "vault_id": "vault_primary",
            "user_id": "user_001",
            "is_primary": True,  # Watch is now primary
            "device_name": "Pixel Watch 2",
        }
        token = "token_alpha_user_001"
        auth_headers = {"Authorization": f"Bearer {token}"}

        item_watch = queue_storage.enqueue(
            upload_id="upl_watch_primary",
            recording_id="rec_watch_sess",
            segment_index=0,
            audio_bytes=sample_adts_audio,
            meta_dict={
                "recording_id": "rec_watch_sess",
                "dp_instance_id": "dp_watch_002",
                "tenant_id": "tenant_alpha",
            },
        )

        def server_upload(item, meta):
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            files = {
                "started_at": (None, now_iso),
                "format": (None, "aac"),
                "source": (None, "watch"),
                "capture_timezone": (None, "UTC"),
                "capture_metadata": (None, json.dumps(meta)),
                "file": ("watch.aac", open(item.file_path, "rb"), "audio/aac"),
            }
            res = client.post(
                "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
                files=files,
                headers=auth_headers,
            )
            return res.status_code == 201, res.status_code

        results = queue_storage.process_worker_tick(server_upload_fn=server_upload)
        assert results[0]["action"] == "success"
        assert item_watch.status == "COMPLETED"
