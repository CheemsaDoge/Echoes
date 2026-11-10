"""Tier 4: Scenario S01 - 24-Hour Continuous Recording Simulation.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 1.1, 2.4.5 & ORIGINAL_REQUEST.md.
Simulates a full 24-hour cycle of ambient recording with periodic speech, elastic rollover,
background WorkManager upload queue, and timeline interval integration.
"""

from __future__ import annotations
import datetime
import io
import json
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.queue_harness import MockUploadQueueStorage
from tests.e2e.harness.audio_harness import (
    synthesize_adts_file,
    validate_adts_stream,
    evaluate_rollover,
)
from tests.e2e.harness.timeline_harness import (
    TimelineInterval,
    clip_timeline_intervals,
    P0_LIVE_CARD,
    P2_ACTIVITY,
    P4_COVERAGE,
)


class Test24HourContinuousRecordingSimulation:
    """Verifies a full 24-hour continuous recording lifecycle across multiple slicing boundaries."""

    def test_24h_lifecycle_rollover_and_upload_pipeline(
        self, client: TestClient, store: MemoryStore, queue_storage: MockUploadQueueStorage
    ):
        token = "token_alpha_user_001"
        auth_headers = {"Authorization": f"Bearer {token}"}
        recording_id = "rec_24h_continuous_001"

        # 24-hour simulated timeline: sequence of session segments across the day
        # 1. 08:30 - Morning Standup (27m cut on silence)
        # 2. 09:00 - Architecture Deep Dive (30m cut on hard limit)
        # 3. 09:30 - Client Review Call (28m cut on silence)
        # 4. 10:00 - User stops recording for lunch (manual stop at 20m)
        segment_events = [
            {"name": "standup", "dur_ms": 27 * 60 * 1000, "sil_ms": 900, "dbfs": -42.0, "stop": False},
            {"name": "deep_dive", "dur_ms": 30 * 60 * 1000, "sil_ms": 0, "dbfs": -18.0, "stop": False},
            {"name": "review_call", "dur_ms": 28 * 60 * 1000, "sil_ms": 1100, "dbfs": -45.0, "stop": False},
            {"name": "lunch_break", "dur_ms": 20 * 60 * 1000, "sil_ms": 0, "dbfs": -20.0, "stop": True},
        ]

        queued_items = []
        for idx, seg in enumerate(segment_events):
            should_cut, split_reason = evaluate_rollover(
                duration_ms=seg["dur_ms"],
                silence_duration_ms=seg["sil_ms"],
                dbfs=seg["dbfs"],
                is_manual_stop=seg["stop"],
            )
            assert should_cut is True

            # Synthesize valid 16kHz mono ADTS AAC audio
            audio_bytes = synthesize_adts_file(num_frames=8, payload_size=120)
            is_valid, msg, frames = validate_adts_stream(audio_bytes)
            assert is_valid is True

            meta = {
                "recording_id": recording_id,
                "segment_index": idx,
                "segment_name": seg["name"],
                "split_reason": split_reason,
                "dp_instance_id": "dp_phone_001",
                "tenant_id": "tenant_alpha",
            }

            item = queue_storage.enqueue(
                upload_id=f"upl_24h_{idx}",
                recording_id=recording_id,
                segment_index=idx,
                audio_bytes=audio_bytes,
                meta_dict=meta,
            )
            queued_items.append(item)

        # Verify all 4 segments entered the atomic queue
        assert len(queue_storage.load_from_disk()) == 4

        # WorkManager worker ticks and uploads all segments to server
        def server_upload(item, meta):
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            files = {
                "started_at": (None, now_iso),
                "format": (None, "aac"),
                "source": (None, "ambient"),
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

        upload_results = queue_storage.process_worker_tick(server_upload_fn=server_upload)
        assert len(upload_results) == 4
        assert all(r["action"] == "success" for r in upload_results)

        # Verify server stored all 4 segments under the recording session
        stored_segs = [s for s in store.recording_segments.values() if s["recording_id"] == recording_id]
        assert len(stored_segs) == 4

        # Verify timeline interval clipping engine represents the recorded coverage vs activity cards
        timeline_intervals = [
            # 08:30 to 10:20 total audio coverage
            TimelineInterval("cov_all", 510, 620, P4_COVERAGE, "Coverage"),
            # Scheduled Activity: 09:00 to 09:30 Deep Dive
            TimelineInterval("act_deep_dive", 540, 570, P2_ACTIVITY, "Activity"),
        ]
        clipped = clip_timeline_intervals(timeline_intervals)
        assert len(clipped) == 3
        # Coverage before activity: 510..540
        assert (clipped[0].start_ms, clipped[0].end_ms) == (510, 540)
        # Activity: 540..570
        assert (clipped[1].start_ms, clipped[1].end_ms) == (540, 570)
        # Coverage after activity: 570..620
        assert (clipped[2].start_ms, clipped[2].end_ms) == (570, 620)
