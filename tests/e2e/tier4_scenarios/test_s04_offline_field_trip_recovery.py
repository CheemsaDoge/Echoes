"""Tier 4: Scenario S04 - Remote Field Trip Offline Recording, Intermittent Network & Recovery.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.3.2 & Section 2.4.5.
Simulates:
1. Long off-grid expedition with 5 accumulated rollover segments.
2. Intermittent transient network failures (503 Service Unavailable).
3. Exponential backoff retry resolution.
4. Retention audit and queue cleanup.
"""

from __future__ import annotations
import os
import time
import pytest
from tests.e2e.harness.queue_harness import MockUploadQueueStorage
from tests.e2e.harness.audio_harness import synthesize_adts_file, evaluate_rollover


class TestFieldTripOfflineRecovery:
    """Verifies queue survival across multiple offline slices, transient network errors, and recovery."""

    def test_field_trip_offline_accumulation_and_eventual_drain(
        self, queue_storage: MockUploadQueueStorage
    ):
        # 1. Device is offline during off-grid trip
        queue_storage.is_network_connected = False
        recording_id = "rec_expedition_01"

        # 5 audio segments captured over the expedition
        num_segments = 5
        items = []
        for i in range(num_segments):
            dur = 28 * 60 * 1000  # 28m
            cut, reason = evaluate_rollover(dur, silence_duration_ms=1000, dbfs=-45.0)
            assert cut is True

            audio = synthesize_adts_file(num_frames=6, payload_size=100)
            meta = {
                "recording_id": recording_id,
                "segment_index": i,
                "split_reason": reason,
                "tenant_id": "tenant_alpha",
            }
            item = queue_storage.enqueue(f"upl_trip_{i}", recording_id, i, audio, meta)
            items.append(item)

        assert len(queue_storage.load_from_disk()) == 5
        assert all(it.status == "QUEUED" for it in items)

        # 2. Network connectivity returns, but first upload batch hits transient server error (503)
        queue_storage.is_network_connected = True
        server_state = {"healthy": False}

        def unstable_server_upload(item, meta):
            if not server_state["healthy"]:
                return False, 503
            return True, 201

        now_ms = 1_000_000
        # Tick 1: Server down -> marks FAILED_RETRYABLE
        res_t1 = queue_storage.process_worker_tick(
            server_upload_fn=unstable_server_upload,
            current_time_ms=now_ms,
        )
        assert all(r["action"] == "retryable_failure" for r in res_t1)
        assert all(it.status == "FAILED_RETRYABLE" for it in items)
        assert all(it.attempts == 1 for it in items)

        # 3. Server recovers; Worker waits for 30s exponential backoff window to elapse
        server_state["healthy"] = True
        # Immediate tick before backoff window elapsed should wait
        res_t2 = queue_storage.process_worker_tick(
            server_upload_fn=unstable_server_upload,
            current_time_ms=now_ms + 5_000,  # Only 5s later, backoff is 30s
        )
        assert all(r["action"] == "backoff_waiting" for r in res_t2)

        # 4. Advance time past 30s backoff window -> All segments successfully uploaded
        res_t3 = queue_storage.process_worker_tick(
            server_upload_fn=unstable_server_upload,
            current_time_ms=now_ms + 35_000,
        )
        assert all(r["action"] == "success" for r in res_t3)
        assert all(it.status == "COMPLETED" for it in items)

        # 5. Run retention pruning: current items were completed today, so none should be pruned
        pruned = queue_storage.prune_retention(current_time_ms=now_ms + 35_000)
        assert pruned == 0
        assert len(queue_storage.items) == 5

        # 6. Simulate passing of 8 days: retention prunes all 5 segments
        pruned_8d = queue_storage.prune_retention(current_time_ms=now_ms + 35_000 + (8 * 86400 * 1000))
        assert pruned_8d == 5
        assert len(queue_storage.items) == 0
