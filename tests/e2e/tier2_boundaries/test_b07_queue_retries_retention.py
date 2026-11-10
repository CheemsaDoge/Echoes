"""Tier 2: Boundary B07 - Queue Retry Attempts & Retention Pruning Boundary Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.3.2.
Verifies exact 5th-attempt permanent failure boundary and exact 7-day retention cutoff boundary.
"""

from __future__ import annotations
import os
import time
import pytest
from tests.e2e.harness.queue_harness import MockUploadQueueStorage
from tests.e2e.harness.audio_harness import synthesize_adts_file


class TestQueueRetriesAndRetentionBoundaries:
    """Verifies queue max retry transitions and 7-day cutoff boundaries."""

    def test_attempt_4_vs_attempt_5_failure_transition(self, queue_storage: MockUploadQueueStorage):
        """1. Attempt 4 remains FAILED_RETRYABLE; Attempt 5 transitions to FAILED_PERMANENT."""
        audio = synthesize_adts_file(num_frames=2)
        item = queue_storage.enqueue("upl_bound", "rec_b", 0, audio, {"tenant_id": "tenant_alpha"})

        def server_fail(it, m):
            return False, 503

        # Step up to attempt 4
        item.attempts = 3
        item.last_attempt_at_ms = 1_000_000
        backoff_3 = 30_000 * (2 ** (3 - 1))  # 120,000 ms
        time_attempt_4 = item.last_attempt_at_ms + backoff_3 + 1000
        queue_storage.process_worker_tick(server_upload_fn=server_fail, current_time_ms=time_attempt_4)
        assert item.attempts == 4
        assert item.status == "FAILED_RETRYABLE"

        # Advance beyond backoff for attempt 5
        backoff_4 = 30_000 * (2 ** (4 - 1))  # 240,000 ms
        time_attempt_5 = item.last_attempt_at_ms + backoff_4 + 1000
        queue_storage.process_worker_tick(server_upload_fn=server_fail, current_time_ms=time_attempt_5)
        assert item.attempts == 5
        assert item.status == "FAILED_PERMANENT"
        assert item.upload_block_reason == "max_attempts_exceeded"

    def test_retention_boundary_exactly_around_7_days(self, queue_storage: MockUploadQueueStorage):
        """2. Completed item 6d 23h 59m ago is retained; completed item 7d 00h 01m ago is pruned."""
        audio = synthesize_adts_file(num_frames=1)
        now_ms = 100_000_000
        seven_days_ms = 7 * 24 * 3600 * 1000

        # Item A: 1 minute before 7-day cutoff (younger, should survive)
        item_a = queue_storage.enqueue("upl_young", "rec_a", 0, audio, {"tenant_id": "tenant_alpha"})
        item_a.status = "COMPLETED"
        item_a.last_attempt_at_ms = now_ms - (seven_days_ms - 60_000)

        # Item B: 1 minute past 7-day cutoff (older, should be pruned)
        item_b = queue_storage.enqueue("upl_old", "rec_b", 0, audio, {"tenant_id": "tenant_alpha"})
        item_b.status = "COMPLETED"
        item_b.last_attempt_at_ms = now_ms - (seven_days_ms + 60_000)

        queue_storage.persist_atomic()

        pruned = queue_storage.prune_retention(current_time_ms=now_ms)
        assert pruned == 1
        assert "upl_young" in queue_storage.items
        assert "upl_old" not in queue_storage.items

    def test_failed_permanent_items_never_pruned_by_retention(self, queue_storage: MockUploadQueueStorage):
        """3. Items with FAILED_PERMANENT status are kept for forensic audit, never pruned."""
        audio = synthesize_adts_file(num_frames=1)
        now_ms = 100_000_000
        eight_days_ms = 8 * 24 * 3600 * 1000

        item = queue_storage.enqueue("upl_perm", "rec_c", 0, audio, {"tenant_id": "tenant_alpha"})
        item.status = "FAILED_PERMANENT"
        item.last_attempt_at_ms = now_ms - eight_days_ms
        queue_storage.persist_atomic()

        pruned = queue_storage.prune_retention(current_time_ms=now_ms)
        assert pruned == 0
        assert "upl_perm" in queue_storage.items
        assert os.path.exists(item.file_path)

    def test_queued_and_inflight_items_never_pruned(self, queue_storage: MockUploadQueueStorage):
        """4. Items with QUEUED or IN_FLIGHT status are never pruned regardless of age."""
        audio = synthesize_adts_file(num_frames=1)
        now_ms = 100_000_000
        ten_days_ms = 10 * 24 * 3600 * 1000

        item = queue_storage.enqueue("upl_stalled", "rec_d", 0, audio, {"tenant_id": "tenant_alpha"})
        item.status = "QUEUED"
        item.last_attempt_at_ms = now_ms - ten_days_ms
        queue_storage.persist_atomic()

        pruned = queue_storage.prune_retention(current_time_ms=now_ms)
        assert pruned == 0
        assert "upl_stalled" in queue_storage.items

    def test_atomic_persistence_preserves_valid_file_on_tmp_orphan(self, queue_storage: MockUploadQueueStorage):
        """5. Stray .tmp file left by interrupted crash does not corrupt main queue file."""
        audio = synthesize_adts_file(num_frames=1)
        queue_storage.enqueue("upl_safe", "rec_e", 0, audio, {"tenant_id": "tenant_alpha"})

        # Create orphan tmp file
        tmp_file = queue_storage.upload_dir / "upload_queue.json.tmp"
        with open(tmp_file, "w") as f:
            f.write("{broken_incomplete_json")

        # Main queue reads valid content
        loaded = queue_storage.load_from_disk()
        assert len(loaded) == 1
        assert loaded[0].upload_id == "upl_safe"
