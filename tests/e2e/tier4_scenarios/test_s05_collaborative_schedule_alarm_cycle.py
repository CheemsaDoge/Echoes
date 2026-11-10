"""Tier 4: Scenario S05 - Collaborative Schedule Edit, AlarmManager Cycle & Auto-Recording.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.1.1, 4.4 & ORIGINAL_REQUEST.md.
Simulates:
1. Collaborative edit between web and mobile triggering 3-way schedule merge.
2. Native AlarmManager schedule arming (ACTION_PRECHECK, ACTION_FIRE, ACTION_STOP).
3. Automatic transition into recording and orderly shutdown at window end.
"""

from __future__ import annotations
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.audio_harness import build_adts_header, validate_adts_stream
from tests.e2e.tier3_pairwise.test_p03_schedule_sync_3way_merge import three_way_schedule_merge


class MockNativeAlarmScheduler:
    """Simulates Android AlarmManager scheduling mechanics (AlarmSchedulerHelper / j8.w)."""

    def __init__(self):
        self.armed_alarms: list[dict] = []
        self.service_state: str = "IDLE"  # IDLE, RECORDING, STOPPED

    def arm_schedule(self, weekly_windows: list[dict], current_time_minutes: int):
        self.armed_alarms.clear()
        for w in weekly_windows:
            start_m = w["start_minutes"]
            end_m = w["end_minutes"]
            # 10m precheck lead
            self.armed_alarms.append({
                "action": "ACTION_PRECHECK",
                "trigger_minute": max(0, start_m - 10),
                "tag": f"precheck_day_{w['weekday']}",
            })
            self.armed_alarms.append({
                "action": "ACTION_FIRE",
                "trigger_minute": start_m,
                "tag": f"fire_day_{w['weekday']}",
            })
            self.armed_alarms.append({
                "action": "ACTION_STOP",
                "trigger_minute": end_m,
                "tag": f"stop_day_{w['weekday']}",
            })

    def trigger_intent(self, action: str):
        if action == "ACTION_FIRE":
            self.service_state = "RECORDING"
        elif action == "ACTION_STOP":
            self.service_state = "STOPPED"


class TestCollaborativeScheduleAndAlarmCycle:
    """Verifies end-to-end schedule synchronization, conflict self-healing, and native alarm triggering."""

    def test_collaborative_schedule_merge_and_alarm_firing_cycle(
        self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str]
    ):
        user_id = "user_001"
        scheduler = MockNativeAlarmScheduler()

        # 1. Base schedule rev 1
        base_windows = [
            {"weekday": 1, "window_start": "09:00", "window_end": "17:00", "start_minutes": 540, "end_minutes": 1020},
        ]
        store.listening_schedules[user_id] = {
            "user_id": user_id,
            "revision": 1,
            "timezone": "UTC",
            "weekly_windows": base_windows,
        }

        # 2. Remote update advances server to rev 2 (adds Tuesday 10:00-14:00)
        remote_windows = list(base_windows) + [
            {"weekday": 2, "window_start": "10:00", "window_end": "14:00", "start_minutes": 600, "end_minutes": 840},
        ]
        store.listening_schedules[user_id]["revision"] = 2
        store.listening_schedules[user_id]["weekly_windows"] = remote_windows

        # 3. Mobile client concurrently makes local edit based on rev 1 (adds Thursday 13:00-16:00)
        local_windows = list(base_windows) + [
            {"weekday": 4, "window_start": "13:00", "window_end": "16:00", "start_minutes": 780, "end_minutes": 960},
        ]

        # 4. Mobile attempts PUT with outdated rev 1 -> 409
        h1 = dict(auth_headers)
        h1["x-hemory-listening-schedule-revision"] = "1"
        res_conflict = client.put(
            "/users/me/listening-schedule",
            json={"revision": 1, "timezone": "UTC", "weekly_windows": local_windows},
            headers=h1,
        )
        assert res_conflict.status_code == 409

        # 5. Interceptor fetches rev 2, executes 3-way merge, resubmits with rev 2
        fetch_res = client.get("/users/me/listening-schedule", headers=auth_headers)
        assert fetch_res.status_code == 200
        remote_data = fetch_res.json()

        merged = three_way_schedule_merge(base_windows, remote_data["weekly_windows"], local_windows)
        assert len(merged) == 3  # Mon, Tue, Thu

        h2 = dict(auth_headers)
        h2["x-hemory-listening-schedule-revision"] = "2"
        res_ok = client.put(
            "/users/me/listening-schedule",
            json={"revision": 2, "timezone": "UTC", "weekly_windows": merged},
            headers=h2,
        )
        assert res_ok.status_code == 200
        assert res_ok.json()["revision"] == 3

        # 6. Native AlarmManager arms alarms based on merged schedule
        scheduler.arm_schedule(merged, current_time_minutes=500)
        # 3 windows * 3 alarms (PRECHECK, FIRE, STOP) = 9 alarms
        assert len(scheduler.armed_alarms) == 9

        # 7. Simulate alarm timeline progression
        # PRECHECK fires at 530
        precheck_alarm = next(a for a in scheduler.armed_alarms if a["action"] == "ACTION_PRECHECK" and a["trigger_minute"] == 530)
        assert precheck_alarm is not None

        # FIRE fires at 540 -> starts RecordingForegroundService
        scheduler.trigger_intent("ACTION_FIRE")
        assert scheduler.service_state == "RECORDING"

        # Recording writes ADTS chunks while active
        test_chunk = build_adts_header(100) + bytes(100)
        is_valid, _, _ = validate_adts_stream(test_chunk)
        assert is_valid is True

        # STOP fires at 1020 -> stops RecordingForegroundService gracefully
        scheduler.trigger_intent("ACTION_STOP")
        assert scheduler.service_state == "STOPPED"
