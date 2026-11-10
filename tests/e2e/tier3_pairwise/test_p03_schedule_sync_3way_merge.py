"""Tier 3: Pairwise P03 - Concurrent Schedule Sync Outdated Revision & 3-Way Merge Self-Healing.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 4.4 & Section 4.4.3.
Cross-feature interaction between:
1. Optimistic Concurrency Header (x-hemory-listening-schedule-revision)
2. HTTP 409 Conflict Rejection & Auto-Recovery Interceptor
3. 3-Way Window Merge Algorithm (Base vs Remote vs Local Edits)
4. Successful Resubmission & Alarm Schedule Re-Arming
"""

from __future__ import annotations
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore


def three_way_schedule_merge(
    base_windows: list[dict],
    remote_windows: list[dict],
    local_windows: list[dict],
) -> list[dict]:
    """
    Simulates Dart ListeningScheduleRevisionInterceptor 3-way window merge:
    - Retains windows added in Remote
    - Retains windows added in Local
    - De-duplicates by (weekday, start_minutes, end_minutes)
    """
    merged_dict = {}

    def _key(w):
        return (w["weekday"], w["start_minutes"], w["end_minutes"])

    for w in remote_windows:
        merged_dict[_key(w)] = w
    for w in local_windows:
        merged_dict[_key(w)] = w

    return sorted(merged_dict.values(), key=lambda x: (x["weekday"], x["start_minutes"]))


class TestScheduleSync3WayMerge:
    """Verifies concurrent schedule sync conflict resolution via 3-way merge."""

    def test_schedule_concurrent_conflict_and_3way_merge_recovery(
        self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str]
    ):
        user_id = "user_001"

        # 1. Base schedule at revision 14
        base_windows = [
            {"weekday": 1, "window_start": "09:00", "window_end": "17:00", "start_minutes": 540, "end_minutes": 1020},
            {"weekday": 2, "window_start": "09:00", "window_end": "17:00", "start_minutes": 540, "end_minutes": 1020},
        ]
        store.listening_schedules[user_id] = {
            "user_id": user_id,
            "revision": 14,
            "timezone": "America/Los_Angeles",
            "weekly_windows": base_windows,
        }

        # 2. Remote update (e.g. from Web App) advances server to revision 15 (adds Wednesday window)
        remote_windows = list(base_windows) + [
            {"weekday": 3, "window_start": "10:00", "window_end": "18:00", "start_minutes": 600, "end_minutes": 1080},
        ]
        store.listening_schedules[user_id]["revision"] = 15
        store.listening_schedules[user_id]["weekly_windows"] = remote_windows

        # 3. Local Client (Phone) had offline edit based on revision 14 (adds Friday window)
        local_windows = list(base_windows) + [
            {"weekday": 5, "window_start": "09:00", "window_end": "15:00", "start_minutes": 540, "end_minutes": 900},
        ]

        # 4. Client attempts PUT with outdated revision header: 14
        headers_14 = dict(auth_headers)
        headers_14["x-hemory-listening-schedule-revision"] = "14"
        put_body = {
            "revision": 14,
            "timezone": "America/Los_Angeles",
            "weekly_windows": local_windows,
        }

        res_conflict = client.put("/users/me/listening-schedule", json=put_body, headers=headers_14)
        assert res_conflict.status_code == 409
        conflict_data = res_conflict.json()
        assert conflict_data["error_code"] == "SCHEDULE_REVISION_CONFLICT"
        assert conflict_data["server_revision"] == 15

        # 5. Client Self-Healing Interceptor:
        # Step A: Fetch remote schedule
        res_fetch = client.get("/users/me/listening-schedule", headers=auth_headers)
        assert res_fetch.status_code == 200
        latest_remote = res_fetch.json()
        assert latest_remote["revision"] == 15

        # Step B: Perform 3-way merge
        merged_windows = three_way_schedule_merge(
            base_windows=base_windows,
            remote_windows=latest_remote["weekly_windows"],
            local_windows=local_windows,
        )
        # Should now have Monday (1), Tuesday (2), Wednesday (3 from remote), Friday (5 from local)
        weekdays = [w["weekday"] for w in merged_windows]
        assert weekdays == [1, 2, 3, 5]

        # Step C: Resubmit with updated revision header: 15
        headers_15 = dict(auth_headers)
        headers_15["x-hemory-listening-schedule-revision"] = "15"
        resubmit_body = {
            "revision": 15,
            "timezone": "America/Los_Angeles",
            "weekly_windows": merged_windows,
        }
        res_success = client.put("/users/me/listening-schedule", json=resubmit_body, headers=headers_15)
        assert res_success.status_code == 200
        updated_data = res_success.json()
        assert updated_data["revision"] == 16
        assert len(updated_data["weekly_windows"]) == 4
