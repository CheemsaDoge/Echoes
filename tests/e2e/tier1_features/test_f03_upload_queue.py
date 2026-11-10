"""Tier 1: Feature F1.6 & F1.7 - Local Atomic Upload Queue & WorkManager Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.3.2 & ORIGINAL_REQUEST.md R2.
- files/upload/upload_queue.json atomic persistence.
- WorkManager retry backoff, multi-tenant guard, 7-day retention pruning.
"""

from __future__ import annotations
import json
import os
import time
import pytest
from tests.e2e.harness.queue_harness import MockUploadQueueStorage
from tests.e2e.harness.audio_harness import synthesize_adts_file


class TestUploadQueueAndWorkManager:
    """Verifies local upload queue persistence and WorkManager worker behavior."""

    def test_atomic_queue_json_persistence(self, queue_storage: MockUploadQueueStorage):
        """1. Verify files/upload/upload_queue.json is created atomically and matches schema."""
        audio = synthesize_adts_file(num_frames=5)
        meta = {"tenant_id": "tenant_alpha", "split_reason": "silence_window"}

        item = queue_storage.enqueue(
            upload_id="upl_01",
            recording_id="rec_01",
            segment_index=0,
            audio_bytes=audio,
            meta_dict=meta,
        )

        assert queue_storage.queue_file.exists(), "upload_queue.json must exist on disk"
        disk_items = queue_storage.load_from_disk()
        assert len(disk_items) == 1
        assert disk_items[0].upload_id == "upl_01"
        assert disk_items[0].byte_size == len(audio)
        assert disk_items[0].status == "QUEUED"
        assert disk_items[0].max_attempts == 5

    def test_network_disconnected_skips_worker_run(self, queue_storage: MockUploadQueueStorage):
        """2. When network is disconnected, WorkManager worker skips upload attempt."""
        audio = synthesize_adts_file(num_frames=3)
        queue_storage.enqueue("upl_02", "rec_02", 0, audio, {"tenant_id": "tenant_alpha"})

        queue_storage.is_network_connected = False
        results = queue_storage.process_worker_tick()

        assert len(results) == 1
        assert results[0]["action"] == "skip"
        assert results[0]["reason"] == "no_network"

        # Item status remains QUEUED and attempts unchanged
        assert queue_storage.items["upl_02"].status == "QUEUED"
        assert queue_storage.items["upl_02"].attempts == 0

    def test_multitenant_identity_mismatch_blocks_upload(self, queue_storage: MockUploadQueueStorage):
        """3. When current credentials tenant_id != segment tenant_id, worker blocks upload."""
        audio = synthesize_adts_file(num_frames=4)
        # Segment created under tenant_beta, but active app credentials are tenant_alpha
        queue_storage.active_auth_context = {"tenant_id": "tenant_alpha"}
        queue_storage.enqueue(
            upload_id="upl_mismatch",
            recording_id="rec_foreign",
            segment_index=0,
            audio_bytes=audio,
            meta_dict={"tenant_id": "tenant_beta"},  # Mismatched!
        )

        results = queue_storage.process_worker_tick()
        assert any(r["action"] == "blocked_identity_mismatch" for r in results)

        item = queue_storage.items["upl_mismatch"]
        assert item.status == "FAILED_PERMANENT"
        assert item.upload_block_reason == "upload_identity_mismatch_skipped_at_send"
        # The file must NOT be deleted yet (retained for safety inspection)
        assert os.path.exists(item.file_path)

    def test_exponential_backoff_and_max_attempts(self, queue_storage: MockUploadQueueStorage):
        """4. Verify exponential backoff and transition to FAILED_PERMANENT upon reaching max_attempts (5)."""
        audio = synthesize_adts_file(num_frames=2)
        queue_storage.enqueue("upl_retry", "rec_03", 0, audio, {"tenant_id": "tenant_alpha"})

        # Mock server that returns transient 500 error
        def failing_server(item, meta):
            return False, 500

        now_ms = 1_000_000
        for attempt in range(1, 6):
            # Advance time beyond backoff
            results = queue_storage.process_worker_tick(
                server_upload_fn=failing_server,
                current_time_ms=now_ms,
            )
            item = queue_storage.items["upl_retry"]
            assert item.attempts == attempt
            if attempt < 5:
                assert item.status == "FAILED_RETRYABLE"
                # Backoff for next attempt: 30s * 2^(attempt-1)
                backoff_ms = (30 * 1000) * (2 ** (attempt - 1))
                now_ms += backoff_ms + 1000
            else:
                assert item.status == "FAILED_PERMANENT"
                assert item.upload_block_reason == "max_attempts_exceeded"

    def test_7day_retention_pruning(self, queue_storage: MockUploadQueueStorage):
        """5. Verify COMPLETED uploads older than 7 days are pruned from disk and queue."""
        audio = synthesize_adts_file(num_frames=2)
        item = queue_storage.enqueue("upl_old", "rec_04", 0, audio, {"tenant_id": "tenant_alpha"})

        # Mark as completed 8 days ago
        now_ms = int(time.time() * 1000)
        eight_days_ms = 8 * 24 * 3600 * 1000
        item.status = "COMPLETED"
        item.last_attempt_at_ms = now_ms - eight_days_ms
        queue_storage.persist_atomic()

        assert os.path.exists(item.file_path)
        assert os.path.exists(item.meta_path)

        pruned = queue_storage.prune_retention(current_time_ms=now_ms)
        assert pruned == 1
        assert "upl_old" not in queue_storage.items
        assert not os.path.exists(item.file_path)
        assert not os.path.exists(item.meta_path)

    def test_retention_preserves_recent_completed_records(self, queue_storage: MockUploadQueueStorage):
        """6. Completed uploads younger than 7 days (e.g. 2 days) are strictly preserved."""
        audio = synthesize_adts_file(num_frames=2)
        item = queue_storage.enqueue("upl_recent", "rec_05", 0, audio, {"tenant_id": "tenant_alpha"})
        now_ms = int(time.time() * 1000)
        two_days_ms = 2 * 24 * 3600 * 1000
        item.status = "COMPLETED"
        item.last_attempt_at_ms = now_ms - two_days_ms
        queue_storage.persist_atomic()

        pruned = queue_storage.prune_retention(current_time_ms=now_ms)
        assert pruned == 0
        assert "upl_recent" in queue_storage.items
        assert os.path.exists(item.file_path)
