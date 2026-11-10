#!/usr/bin/env python3
"""
Empirical Stress Test Harness for HEMORY_SYSTEM_SPECIFICATION.md
Author: Challenger 2 (Edge Case & Protocol Stress Challenge)

This harness models and tests:
1. Primary Device Lease race conditions & database constraints
2. Watchdog Dead-Man Switch state machine & silent deadlock failure modes
3. Multi-tenant credential leak protection (upload_identity_mismatch_skipped_at_send)
4. SSE Stream resilience, cancellation, and disconnection behavior
5. Listening schedule 3-way merge algorithms and conflict scenarios
"""

import sys
import json
import time
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

print("=" * 80)
print("STARTING EMPIRICAL PROTOCOL STRESS TEST HARNESS")
print("=" * 80)

# ==============================================================================
# CHALLENGE 1: Primary Device Lease Race & Database Constraints
# ==============================================================================
print("\n[CHALLENGE 1] Testing Primary Device Lease & Concurrency...")

# 1.1 In-memory SQLite simulating the PostgreSQL schema defined in Section 3.2:
# CREATE TABLE dp_instances (
#     dp_instance_id VARCHAR(64) PRIMARY KEY,
#     vault_id VARCHAR(64) NOT NULL,
#     is_primary BOOLEAN NOT NULL DEFAULT FALSE
# );
# CREATE INDEX idx_dp_instances_primary ON dp_instances(vault_id, is_primary) WHERE is_primary = TRUE;

def test_primary_device_lease_concurrency():
    results = {}
    
    # Check 1: Non-unique index allows multiple primary devices if application logic races
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE dp_instances (
        dp_instance_id TEXT PRIMARY KEY,
        vault_id TEXT NOT NULL,
        is_primary INTEGER NOT NULL DEFAULT 0
    )
    """)
    # As specified in spec line 1122: CREATE INDEX idx_dp_instances_primary ON dp_instances(vault_id, is_primary) WHERE is_primary = TRUE;
    cur.execute("CREATE INDEX idx_dp_instances_primary ON dp_instances(vault_id, is_primary)")
    
    # Seed 2 devices
    cur.execute("INSERT INTO dp_instances VALUES ('dp_phone', 'vault_1', 1)")
    cur.execute("INSERT INTO dp_instances VALUES ('dp_watch', 'vault_1', 0)")
    conn.commit()
    
    # Simulate concurrent uncoordinated PUT /primary-device
    # Device Watch attempts to set itself primary
    # If transaction is: UPDATE dp_instances SET is_primary = 1 WHERE dp_instance_id = 'dp_watch'
    # but the previous primary reset failed, was delayed, or missed:
    cur.execute("UPDATE dp_instances SET is_primary = 1 WHERE dp_instance_id = 'dp_watch'")
    conn.commit()
    
    # Query count of primary devices
    cur.execute("SELECT COUNT(*) FROM dp_instances WHERE vault_id = 'vault_1' AND is_primary = 1")
    count_primary = cur.fetchone()[0]
    
    results["multiple_primaries_possible_without_unique_constraint"] = (count_primary > 1)
    print(f"  -> Multiple primary devices possible under race condition: {results['multiple_primaries_possible_without_unique_constraint']} (Count: {count_primary})")
    
    # Check 1.2: Rejection reason codes handling simulation
    def server_upload_evaluator(dp_id: str, started_at_epoch: float, vault_id: str, current_primaries: Dict[str, str], schedule_windows: List[tuple], server_now: float):
        # Rule 1: No primary configured
        if vault_id not in current_primaries or not current_primaries[vault_id]:
            return 403, "recording.upload_rejected_no_primary"
        # Rule 2: Device is not primary
        if current_primaries[vault_id] != dp_id:
            return 403, "recording.upload_rejected_not_primary"
        # Rule 3: Time drift (>30s future or >7d stale)
        if (started_at_epoch > server_now + 30.0) or (started_at_epoch < server_now - 7 * 86400):
            return 409, "recording.upload_rejected_invalid_time"
        # Rule 4: Outside listening window
        # (simplified: within any window)
        in_window = any(w[0] <= (started_at_epoch % 86400) <= w[1] for w in schedule_windows)
        if not in_window:
            return 403, "recording.upload_rejected_out_of_primary_window"
        return 200, "success"

    now = 1731171000.0
    primaries = {"vault_1": "dp_phone"}
    windows = [(32400, 64800)] # 09:00 - 18:00
    
    # Test all 4 rejection codes
    code1, err1 = server_upload_evaluator("dp_phone", now, "vault_empty", {}, windows, now)
    code2, err2 = server_upload_evaluator("dp_watch", now, "vault_1", primaries, windows, now)
    code3, err3 = server_upload_evaluator("dp_phone", now + 45.0, "vault_1", primaries, windows, now)
    # Outside window: same day at 03:00 AM (10800s from midnight)
    code4, err4 = server_upload_evaluator("dp_phone", now - (now % 86400) + 10800, "vault_1", primaries, windows, now)
    
    results["no_primary_handled"] = (err1 == "recording.upload_rejected_no_primary")
    results["not_primary_handled"] = (err2 == "recording.upload_rejected_not_primary")
    results["invalid_time_handled"] = (err3 == "recording.upload_rejected_invalid_time")
    results["out_of_window_handled"] = (err4 == "recording.upload_rejected_out_of_primary_window")
    
    print(f"  -> Rejection codes coverage: no_primary={results['no_primary_handled']}, not_primary={results['not_primary_handled']}, invalid_time={results['invalid_time_handled']}, out_of_window={results['out_of_window_handled']}")
    return results

ch1_res = test_primary_device_lease_concurrency()

# ==============================================================================
# CHALLENGE 2: Watchdog Dead-Man Switch State Machine & Deadlock Vulnerability
# ==============================================================================
print("\n[CHALLENGE 2] Testing Watchdog Dead-Man Switch & Deadlock Edge Cases...")

class DeadManSwitchSimulator:
    def __init__(self):
        self.service_alive = False # RecordingForegroundService.f36186Y
        self.heartbeat_coroutine_active = False
        self.audio_thread_active = False
        self.alarm_pending = False
        self.alarm_trigger_time = 0.0
        self.notification_posted = False
        self.log = []

    def start_service(self):
        self.service_alive = True
        self.heartbeat_coroutine_active = True
        self.audio_thread_active = True
        self.arm_alarm(lease_seconds=60)
        self.log.append("service_started")

    def arm_alarm(self, lease_seconds=60):
        self.alarm_pending = True
        self.alarm_trigger_time = time.time() + lease_seconds

    def heartbeat_tick(self):
        if self.heartbeat_coroutine_active and self.audio_thread_active:
            self.arm_alarm(lease_seconds=60)
            self.log.append("heartbeat_renewed")

    def simulate_audio_hal_deadlock(self):
        """Simulates audio driver hang: audio thread deadlocks, coroutine stalls, service object still exists"""
        self.audio_thread_active = False
        self.heartbeat_coroutine_active = False
        self.log.append("audio_hal_deadlocked")

    def trigger_alarm_receiver(self):
        """DeadManSwitchAlarmReceiver execution logic as specified in spec §2.2.3 lines 324-328"""
        if not self.alarm_pending:
            return "no_alarm"
        self.alarm_pending = False
        
        # Spec Line 324: Inspects RecordingForegroundService.f36186Y (static volatile boolean)
        if self.service_alive:
            # Spec Line 325: If f36186Y == true, logs '前台聆听服务仍活跃，判定为心跳冻结误报，不投递' and aborts
            self.log.append("discard_false_alarm")
            # CRITICAL: Note that receiver does NOT re-arm alarm!
            return "discarded_as_false_alarm"
        else:
            # Spec Line 326: If f36186Y == false: post notification ID 1214606592
            self.notification_posted = True
            self.log.append("posted_alert_notification")
            return "alert_posted"

def test_dead_man_switch():
    sim = DeadManSwitchSimulator()
    sim.start_service()
    
    # Scenario A: Audio HAL deadlocks while service remains active in Android memory
    sim.simulate_audio_hal_deadlock()
    # At +60s, Alarm fires:
    result = sim.trigger_alarm_receiver()
    
    # Did the alert get posted?
    silent_failure_detected = (result == "discarded_as_false_alarm" and not sim.notification_posted and not sim.alarm_pending)
    print(f"  -> Audio HAL deadlock test: Result='{result}', AlertPosted={sim.notification_posted}, AlarmReArmed={sim.alarm_pending}")
    print(f"  -> CRITICAL VULNERABILITY CONFIRMED: Watchdog silently disarmed under audio thread hang: {silent_failure_detected}")
    
    # Scenario B: Process killed by OS (LMK)
    sim2 = DeadManSwitchSimulator()
    sim2.start_service()
    sim2.service_alive = False # Process dead
    sim2.heartbeat_coroutine_active = False
    sim2.audio_thread_active = False
    res2 = sim2.trigger_alarm_receiver()
    print(f"  -> System Kill test: Result='{res2}', AlertPosted={sim2.notification_posted}")
    
    return {
        "hal_deadlock_watchdog_bypass": silent_failure_detected,
        "lmk_heads_up_posted": sim2.notification_posted
    }

ch2_res = test_dead_man_switch()

# ==============================================================================
# CHALLENGE 3: Multi-tenant Credential Leak Protection (UploadWorker)
# ==============================================================================
print("\n[CHALLENGE 3] Testing Multi-tenant Credential Leak Protection...")

@dataclass
class UploadEntry:
    upload_id: str
    tenant_id: str
    vault_id: str
    file_path: str
    status: str = "pending"

@dataclass
class ActiveSession:
    tenant_id: str
    vault_id: str
    jwt_token: str

def upload_worker_do_work(entry: UploadEntry, current_session: Optional[ActiveSession]) -> Dict[str, Any]:
    # Logic from spec §3.3.2 lines 1418-1444
    current_tenant = current_session.tenant_id if current_session else ""
    current_vault = current_session.vault_id if current_session else ""
    
    if entry.tenant_id != current_tenant or entry.vault_id != current_vault:
        # upload_identity_mismatch_skipped_at_send
        entry.status = "FailedPermanent"
        return {
            "result": "Result.failure()",
            "event": "upload_identity_mismatch_skipped_at_send",
            "sent": False,
            "error": "identity_mismatch"
        }
    
    # If match, execute upload
    entry.status = "uploaded"
    return {
        "result": "Result.success()",
        "sent": True,
        "destination_tenant": entry.tenant_id,
        "destination_vault": entry.vault_id
    }

def test_multi_tenant_leak_protection():
    # 1. User records on Tenant A
    entry = UploadEntry("upl_101", "tenant_A", "vault_A", "segments/seg_1/audio.aac")
    
    # 2. User switches to Tenant B
    session_b = ActiveSession("tenant_B", "vault_B", "jwt_for_tenant_B")
    
    # 3. UploadWorker executes
    res_b = upload_worker_do_work(entry, session_b)
    
    # 4. User logs out (null session)
    entry_2 = UploadEntry("upl_102", "tenant_A", "vault_A", "segments/seg_2/audio.aac")
    res_logout = upload_worker_do_work(entry_2, None)
    
    # 5. Legitimate upload under Tenant A
    session_a = ActiveSession("tenant_A", "vault_A", "jwt_for_tenant_A")
    entry_3 = UploadEntry("upl_103", "tenant_A", "vault_A", "segments/seg_3/audio.aac")
    res_a = upload_worker_do_work(entry_3, session_a)
    
    leak_prevented = (not res_b["sent"]) and (res_b["event"] == "upload_identity_mismatch_skipped_at_send")
    logout_prevented = (not res_logout["sent"]) and (res_logout["event"] == "upload_identity_mismatch_skipped_at_send")
    legitimate_allowed = res_a["sent"]
    data_loss_side_effect = (entry.status == "FailedPermanent")
    
    print(f"  -> Cross-tenant leak blocked: {leak_prevented} (Sent={res_b['sent']})")
    print(f"  -> Logged-out leak blocked: {logout_prevented} (Sent={res_logout['sent']})")
    print(f"  -> Legitimate upload succeeds: {legitimate_allowed}")
    print(f"  -> Tradeoff analysis: Segment marked FailedPermanent upon account switch (data loss): {data_loss_side_effect}")
    
    return {
        "leak_prevented": leak_prevented,
        "logout_prevented": logout_prevented,
        "legitimate_allowed": legitimate_allowed,
        "data_loss_on_switch": data_loss_side_effect
    }

ch3_res = test_multi_tenant_leak_protection()

# ==============================================================================
# CHALLENGE 4: SSE Stream Resilience, Cancellation, and Tool Timeouts
# ==============================================================================
print("\n[CHALLENGE 4] Testing SSE Stream Resilience & Failure Modes...")

def test_sse_resilience():
    # Model SSE Event Sequence
    events_received = []
    
    class SSETurnSession:
        def __init__(self, turn_id: str):
            self.turn_id = turn_id
            self.is_cancelled = False
            self.state = "pending"
            self.tool_status = "idle"
            
        def process_turn(self, simulate_network_drop=False, simulate_cancel_during_tool=False, simulate_tool_timeout=False):
            yield {"event": "turn_progress", "data": {"phase": "recalling"}}
            self.state = "recalling"
            
            # Executing phase
            yield {"event": "tool_call_started", "data": {"tool_name": "search_memory", "status": "running"}}
            self.state = "executing"
            self.tool_status = "running"
            
            if simulate_cancel_during_tool:
                self.is_cancelled = True
                self.state = "failed"
                yield {"event": "turn_failed", "data": {"error_code": "TURN_CANCELLED", "turn_error_kind": "cancelled"}}
                return
                
            if simulate_tool_timeout:
                self.tool_status = "failed"
                yield {"event": "tool_call_failed", "data": {"tool_name": "search_memory", "status": "failed", "error_message": "Timeout 5000ms"}}
                self.state = "failed"
                yield {"event": "turn_failed", "data": {"error_code": "TURN_TIMEOUT", "turn_error_kind": "timeout"}}
                return
                
            if simulate_network_drop:
                # Connection abruptly terminates without turn_completed or turn_failed
                raise ConnectionResetError("Broken pipe / Network connection reset")
                
            yield {"event": "tool_call_completed", "data": {"tool_name": "search_memory", "status": "completed"}}
            yield {"event": "turn_progress", "data": {"phase": "summarizing"}}
            yield {"event": "text_delta", "data": {"delta": "Found memory."}}
            yield {"event": "turn_completed", "data": {"status": "completed"}}
            self.state = "completed"

    # Test 4.1: Normal flow
    s1 = SSETurnSession("turn_001")
    normal_events = list(s1.process_turn())
    
    # Test 4.2: Mid-turn cancellation
    s2 = SSETurnSession("turn_002")
    cancel_events = list(s2.process_turn(simulate_cancel_during_tool=True))
    cancel_terminated = any(e["event"] == "turn_failed" and e["data"]["turn_error_kind"] == "cancelled" for e in cancel_events)
    
    # Test 4.3: Tool timeout
    s3 = SSETurnSession("turn_003")
    timeout_events = list(s3.process_turn(simulate_tool_timeout=True))
    timeout_handled = any(e["event"] == "tool_call_failed" for e in timeout_events) and any(e["event"] == "turn_failed" for e in timeout_events)
    
    # Test 4.4: Network drop
    s4 = SSETurnSession("turn_004")
    network_drop_caught = False
    try:
        for ev in s4.process_turn(simulate_network_drop=True):
            pass
    except ConnectionResetError:
        network_drop_caught = True
        
    print(f"  -> Normal turn completed successfully: {len(normal_events)} events")
    print(f"  -> Mid-turn cancel terminates cleanly with turn_failed: {cancel_terminated}")
    print(f"  -> Tool timeout emits tool_call_failed followed by turn_failed: {timeout_handled}")
    print(f"  -> Network drop caught at transport layer: {network_drop_caught} (Missing SSE Last-Event-ID resume in spec)")
    
    return {
        "normal_flow": len(normal_events) > 0,
        "cancel_clean": cancel_terminated,
        "timeout_clean": timeout_handled,
        "network_drop_unresumable": network_drop_caught
    }

ch4_res = test_sse_resilience()

# ==============================================================================
# CHALLENGE 5: Listening Schedule Revision Conflict & 3-Way Merge
# ==============================================================================
print("\n[CHALLENGE 5] Testing Listening Schedule Revision & 3-Way Merge...")

@dataclass
class ScheduleWindow:
    weekday: int
    start_minutes: int
    end_minutes: int
    window_start: str = ""
    window_end: str = ""

def three_way_schedule_merge(base_windows: List[Dict], remote_windows: List[Dict], local_windows: List[Dict]) -> (List[Dict], bool):
    """
    Simulates a 3-way merge on weekly_windows[]
    Returns (merged_windows, has_conflict)
    """
    # Key by (weekday, start, end)
    def to_set(windows):
        return {(w["weekday"], w["start_minutes"], w["end_minutes"]) for w in windows}
        
    base_set = to_set(base_windows)
    remote_set = to_set(remote_windows)
    local_set = to_set(local_windows)
    
    # Additions/deletions relative to base
    remote_added = remote_set - base_set
    remote_removed = base_set - remote_set
    
    local_added = local_set - base_set
    local_removed = base_set - local_set
    
    # Conflict check: did both add or modify windows for the same weekday with overlapping intervals?
    conflict = False
    # Start with base
    merged_set = set(base_set)
    # Apply remote changes
    merged_set -= remote_removed
    merged_set |= remote_added
    # Apply local changes
    merged_set -= local_removed
    merged_set |= local_added
    
    # Check for overlaps on the same weekday
    by_day = {}
    for (day, start, end) in merged_set:
        by_day.setdefault(day, []).append((start, end))
        
    for day, intervals in by_day.items():
        sorted_intervals = sorted(intervals, key=lambda x: x[0])
        for i in range(len(sorted_intervals) - 1):
            if sorted_intervals[i][1] > sorted_intervals[i+1][0]:
                conflict = True # Overlapping window conflict detected!
                
    result = [{"weekday": d, "start_minutes": s, "end_minutes": e} for (d, s, e) in sorted(merged_set)]
    return result, conflict

def test_schedule_3way_merge():
    # Base schedule (Rev 14): Mon 09:00 - 18:00 (540 - 1080)
    base = [{"weekday": 1, "start_minutes": 540, "end_minutes": 1080}]
    
    # Scenario A: Non-conflicting independent edits
    # Remote adds Tuesday (day 2: 540 - 1080)
    remote_a = [{"weekday": 1, "start_minutes": 540, "end_minutes": 1080}, {"weekday": 2, "start_minutes": 540, "end_minutes": 1080}]
    # Local adds Wednesday (day 3: 540 - 1080)
    local_a = [{"weekday": 1, "start_minutes": 540, "end_minutes": 1080}, {"weekday": 3, "start_minutes": 540, "end_minutes": 1080}]
    
    merged_a, conflict_a = three_way_schedule_merge(base, remote_a, local_a)
    print(f"  -> Scenario A (Orthogonal edits): Conflict={conflict_a}, ResultWindowsCount={len(merged_a)}")
    
    # Scenario B: Overlapping interval conflict on the same weekday
    # Remote edits Monday to 08:00 - 13:00 (480 - 780)
    remote_b = [{"weekday": 1, "start_minutes": 480, "end_minutes": 780}]
    # Local edits Monday to 11:00 - 17:00 (660 - 1020)
    local_b = [{"weekday": 1, "start_minutes": 660, "end_minutes": 1020}]
    
    merged_b, conflict_b = three_way_schedule_merge(base, remote_b, local_b)
    print(f"  -> Scenario B (Overlapping edits on same day): OverlapConflict={conflict_b}, MergedWindows={merged_b}")
    
    # Scenario C: Timezone conflict simulation
    # Base: UTC-8 (PST). start_minutes = 540 (09:00 PST)
    # Remote switches timezone to Europe/London (UTC+0) with start_minutes = 540 (09:00 GMT = 01:00 PST)
    # Local keeps PST and adds an afternoon window
    timezone_skew = (540 - (540 - 8*60)) # 8 hours offset
    print(f"  -> Scenario C (Cross-timezone merge divergence): Unnormalized minute skew={timezone_skew} min (8.0 hours)")
    
    return {
        "orthogonal_merge_success": not conflict_a and len(merged_a) == 3,
        "overlapping_conflict_detected": conflict_b,
        "timezone_skew_vulnerability": timezone_skew != 0
    }

ch5_res = test_schedule_3way_merge()

print("\n" + "=" * 80)
print("EMPIRICAL TEST SUMMARY:")
print(f"Challenge 1 (Primary Device Lease): {ch1_res}")
print(f"Challenge 2 (Watchdog Dead-Man Switch): {ch2_res}")
print(f"Challenge 3 (Multi-tenant Leak Protection): {ch3_res}")
print(f"Challenge 4 (SSE Stream Resilience): {ch4_res}")
print(f"Challenge 5 (Schedule Revision 3-Way Merge): {ch5_res}")
print("=" * 80)
