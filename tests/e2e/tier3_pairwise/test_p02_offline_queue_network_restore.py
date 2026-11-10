"""Tier 3: Pairwise P02 - Offline Queue Accumulation Across Multiple Rollovers & Network Restore.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.3.2 & Section 2.4.5.
Cross-feature interaction between:
1. Elastic Rollover Slicer (3 consecutive chunks)
2. Atomic upload_queue.json disk accumulation while offline
3. WorkManager connectivity trigger & drain
4. Ingestion API HTTP 201 receipt & retention state transition
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.queue_harness import MockUploadQueueStorage
from tests.e2e.harness.audio_harness import synthesize_adts_file, evaluate_rollover


class TestOfflineQueueAndNetworkRestore:
    """Verifies queue accumulation of multiple rollover segments during offline mode and subsequent flush."""

    def test_offline_rollover_accumulation_and_batch_upload_flush(
        self, client: TestClient, store: MemoryStore, queue_storage: MockUploadQueueStorage
    ):
        token = "token_alpha_user_001"
        auth_headers = {"Authorization": f"Bearer {token}"}

        # 1. Device is offline
        queue_storage.is_network_connected = False

        # 2. Slicer generates 3 sequential segments:
        # Segment 0: 27m silence_window cut
        # Segment 1: 30m hard_limit cut
        # Segment 2: 15m manual_stop cut
        rollover_events = [
            (27 * 60 * 1000, 900, -45.0, False, False),
            (30 * 60 * 1000, 0, -20.0, False, False),
            (15 * 60 * 1000, 0, -20.0, False, True),
        ]

        segment_items = []
        for idx, (dur, sil, dbfs, is_int, is_stop) in enumerate(rollover_events):
            should_cut, split_reason = evaluate_rollover(dur, sil, dbfs, is_int, is_stop)
            assert should_cut is True

            audio = synthesize_adts_file(num_frames=10, payload_size=100)
            meta = {
                "recording_id": "rec_offline_session",
                "segment_index": idx,
                "split_reason": split_reason,
                "dp_instance_id": "dp_phone_001",
                "tenant_id": "tenant_alpha",
            }
            item = queue_storage.enqueue(
                upload_id=f"upl_offline_{idx}",
                recording_id="rec_offline_session",
                segment_index=idx,
                audio_bytes=audio,
                meta_dict=meta,
            )
            segment_items.append(item)

        # 3. Check queue state while offline
        assert len(queue_storage.items) == 3
        assert all(it.status == "QUEUED" for it in queue_storage.items.values())

        # Tick while offline does nothing
        results_offline = queue_storage.process_worker_tick()
        assert results_offline[0]["action"] == "skip"
        assert all(it.status == "QUEUED" for it in queue_storage.items.values())

        # 4. Network restored
        queue_storage.is_network_connected = True

        def server_upload(item, meta):
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            files = {
                "started_at": (None, now_iso),
                "format": (None, "aac"),
                "source": (None, "phone"),
                "capture_timezone": (None, "UTC"),
                "capture_metadata": (None, json.dumps(meta)),
                "file": (f"seg_{item.segment_index}.aac", open(item.file_path, "rb"), "audio/aac"),
            }
            res = client.post(
                "/tenants/tenant_alpha/vaults/vault_primary/recordings/upload",
                files=files,
                headers=auth_headers,
            )
            return res.status_code == 201, res.status_code

        # 5. WorkManager tick drains the entire queue
        results_online = queue_storage.process_worker_tick(server_upload_fn=server_upload)

        assert len(results_online) == 3
        assert all(r["action"] == "success" for r in results_online)
        assert all(it.status == "COMPLETED" for it in queue_storage.items.values())

        # Check that server persisted all 3 segments
        vault_segments = [
            s for s in store.recording_segments.values() if s["recording_id"] == "rec_offline_session"
        ]
        assert len(vault_segments) == 3
