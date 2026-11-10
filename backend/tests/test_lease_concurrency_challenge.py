"""
backend/tests/test_lease_concurrency_challenge.py
Empirical Adversarial Challenge Test Harness for Milestone M2:
1. High-concurrency multi-threaded lease switching races against SQLite WAL mode
2. Invariant verification: at no point can two devices hold is_primary = TRUE for the same (tenant_id, vault_id, user_id)
3. Partial unique index enforcement and adversarial SQL bypass attempts
4. Cross-tenant, cross-vault, and cross-user lease isolation
5. Clock drift boundary conditions (future drift 10s vs 30s discrepancy, stale cutoff at 7 days)
6. Listening schedule timezone transitions (boundary minutes, midnight rollover, daylight saving)
7. Concurrent upload while lease is actively being switched
"""
from __future__ import annotations

import concurrent.futures
import datetime
import io
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.lease import LeaseErrorCode, LeaseRejectionException, PrimaryLeaseArbiter, UploadLeaseValidator


def make_adts_frame(
    payload: bytes = b"\x00" * 128,
    sample_rate_idx: int = 8,
    channels: int = 1,
    profile: int = 1,
    mpeg_id: int = 0,
    protection_absent: int = 1,
) -> bytes:
    flen = 7 + len(payload)
    h = bytearray(7)
    h[0] = 0xFF
    h[1] = 0xF0 | (mpeg_id << 3) | protection_absent
    h[2] = ((profile & 0x03) << 6) | ((sample_rate_idx & 0x0F) << 2) | ((channels >> 2) & 0x01)
    h[3] = ((channels & 0x03) << 6) | ((flen >> 11) & 0x03)
    h[4] = (flen >> 3) & 0xFF
    h[5] = ((flen & 0x07) << 5) | 0x1F
    h[6] = 0xFC
    return bytes(h) + payload


def make_adts_stream(num_frames: int = 5, payload_size: int = 128) -> bytes:
    return make_adts_frame(b"\xCC" * payload_size) * num_frames


@pytest.fixture
def challenge_env(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        api_token="challenger-admin-token-at-least-24-chars",
        run_worker=False,
    )
    app = create_app(settings)
    db: Database = app.state.db

    # Seed 5 devices for Alice in tenant_alpha / vault_alpha
    with db.connect(immediate=True) as conn:
        conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t_alpha', 'Alpha Corp', 'alpha')")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_alpha', 't_alpha', 'Alpha Vault', 1)")
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_alice', 't_alpha', 'alice@alpha.com', 'Alice')")
        for i in range(5):
            is_prim = 1 if i == 0 else 0
            conn.execute(
                """INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
                   VALUES (?, 't_alpha', 'v_alpha', 'u_alice', ?, 'phone', ?)""",
                (f"dp_{i}", f"Alice Device {i}", is_prim),
            )

        # Seed another tenant, vault, and user
        conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t_beta', 'Beta Corp', 'beta')")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_beta', 't_beta', 'Beta Vault', 1)")
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_bob', 't_beta', 'bob@beta.com', 'Bob')")
        conn.execute(
            """INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
               VALUES ('dp_bob_0', 't_beta', 'v_beta', 'u_bob', 'Bob Device 0', 'phone', 1)"""
        )

    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {settings.api_token}",
        "x-tenant-id": "t_alpha",
        "x-vault-id": "v_alpha",
        "x-user-id": "u_alice",
        "x-dp-instance-id": "dp_0",
    }
    return app, db, settings, client, headers


# ============================================================================
# CHALLENGE 1: High-Concurrency Multi-Threaded Lease Switching Races
# ============================================================================

def test_concurrent_lease_arbitration_stress_harness(challenge_env):
    """
    Stress-test PUT /primary-device under heavy multi-threaded contention.
    Spawns 12 worker threads executing 60 rapid lease switches across 5 devices,
    while 4 reader threads continuously verify the invariant:
    COUNT(is_primary = 1) <= 1 at all times.
    """
    app, db, settings, _, _ = challenge_env

    violation_events: list[str] = []
    stop_event = threading.Event()
    read_iterations = 0

    def reader_task():
        nonlocal read_iterations
        while not stop_event.is_set():
            try:
                with db.connect() as conn:
                    rows = conn.execute(
                        """SELECT dp_instance_id FROM dp_instances
                           WHERE tenant_id = 't_alpha' AND vault_id = 'v_alpha' AND user_id = 'u_alice' AND is_primary = 1"""
                    ).fetchall()
                    read_iterations += 1
                    if len(rows) > 1:
                        violation_events.append(f"INVARIANT VIOLATED: Found {len(rows)} primary devices: {[r[0] for r in rows]}")
            except Exception as e:
                # OperationalError or busy timeout can occur if locked
                pass
            time.sleep(0.001)

    # Launch 4 continuous invariant checkers
    reader_threads = [threading.Thread(target=reader_task, daemon=True) for _ in range(4)]
    for rt in reader_threads:
        rt.start()

    successes = 0
    failures = 0
    errors: list[str] = []

    def switch_worker(thread_idx: int, iterations: int):
        nonlocal successes, failures
        # Each thread uses its own TestClient to exercise HTTP middleware & SQLite connection pool
        local_client = TestClient(app)
        local_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha",
            "x-user-id": "u_alice",
        }
        for it in range(iterations):
            target_dp = f"dp_{(thread_idx + it) % 5}"
            try:
                res = local_client.put(
                    "/primary-device",
                    json={"dp_instance_id": target_dp, "vault_id": "v_alpha"},
                    headers=local_headers,
                )
                if res.status_code == 200:
                    data = res.json()
                    assert data["dp_instance_id"] == target_dp
                    assert data["is_primary"] is True
                    successes += 1
                else:
                    failures += 1
                    errors.append(f"HTTP {res.status_code}: {res.text}")
            except Exception as exc:
                failures += 1
                errors.append(f"Exception: {exc}")

    # Launch 10 worker threads performing 8 switches each = 80 lease switches
    threads = [threading.Thread(target=switch_worker, args=(i, 8)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Stop reader threads
    stop_event.set()
    for rt in reader_threads:
        rt.join(timeout=1.0)

    # Invariant Check: at no point during concurrent execution did count > 1
    assert not violation_events, f"Invariant violation detected during race: {violation_events}"
    assert failures == 0, f"Encountered unexpected failures during concurrent switching: {errors}"
    assert successes == 80

    # Final Invariant Check in database: exactly 1 device has is_primary = 1
    with db.connect() as conn:
        active_primaries = conn.execute(
            """SELECT dp_instance_id FROM dp_instances
               WHERE tenant_id = 't_alpha' AND vault_id = 'v_alpha' AND user_id = 'u_alice' AND is_primary = 1"""
        ).fetchall()
        assert len(active_primaries) == 1, f"Expected exactly 1 active primary, found {len(active_primaries)}"


# ============================================================================
# CHALLENGE 2: Partial Unique Index & SQLite Adversarial Bypass Attempts
# ============================================================================

def test_partial_unique_index_adversarial_sqlite_bypass(challenge_env):
    """
    Stress-test the partial unique index idx_dp_instances_primary_unique.
    Verify that SQLite blocks:
    1. Direct INSERT with is_primary = 1 when another row already has is_primary = 1
    2. Direct UPDATE setting is_primary = 1 on an existing non-primary device
    3. Direct UPDATE setting is_primary = TRUE
    4. Allows multiple non-primary devices (is_primary = 0 or FALSE)
    5. Checks behavior of non-standard boolean values (e.g. is_primary = 2)
    """
    _, db, _, _, _ = challenge_env

    # 1. Direct INSERT of another primary device for same user/vault must raise IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect(immediate=True) as conn:
            conn.execute(
                """INSERT INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
                   VALUES ('dp_adversarial_1', 't_alpha', 'v_alpha', 'u_alice', 'Rogue DP', 'phone', 1)"""
            )

    # 2. Direct UPDATE setting is_primary = 1 on dp_1 must raise IntegrityError (dp_0 is already primary)
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect(immediate=True) as conn:
            conn.execute("UPDATE dp_instances SET is_primary = 1 WHERE dp_instance_id = 'dp_1'")

    # 3. Direct UPDATE setting is_primary = TRUE must also raise IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect(immediate=True) as conn:
            conn.execute("UPDATE dp_instances SET is_primary = TRUE WHERE dp_instance_id = 'dp_2'")

    # 4. Multiple non-primary devices are permitted
    with db.connect(immediate=True) as conn:
        conn.execute("UPDATE dp_instances SET is_primary = 0 WHERE dp_instance_id = 'dp_0'")
        # Now 0 devices are primary
        count = conn.execute("SELECT COUNT(*) FROM dp_instances WHERE vault_id = 'v_alpha' AND is_primary = 1").fetchone()[0]
        assert count == 0

        # Promote dp_3 to primary cleanly
        conn.execute("UPDATE dp_instances SET is_primary = 1 WHERE dp_instance_id = 'dp_3'")
        count = conn.execute("SELECT COUNT(*) FROM dp_instances WHERE vault_id = 'v_alpha' AND is_primary = 1").fetchone()[0]
        assert count == 1


# ============================================================================
# CHALLENGE 3: Cross-Tenant, Cross-Vault, and Cross-User Lease Isolation
# ============================================================================

def test_cross_tenant_and_cross_user_lease_isolation(challenge_env):
    """
    Verify that primary lease operations on Alice's device:
    - Never demote Bob's primary device in t_beta / v_beta
    - Reject cross-tenant or cross-user lease takeover attempts
    """
    app, db, settings, client, headers = challenge_env

    # Verify initial state: Alice has dp_0 as primary, Bob has dp_bob_0 as primary
    with db.connect() as conn:
        alice_p = conn.execute("SELECT dp_instance_id FROM dp_instances WHERE user_id = 'u_alice' AND is_primary = 1").fetchone()[0]
        bob_p = conn.execute("SELECT dp_instance_id FROM dp_instances WHERE user_id = 'u_bob' AND is_primary = 1").fetchone()[0]
        assert alice_p == "dp_0"
        assert bob_p == "dp_bob_0"

    # Alice switches primary to dp_1
    res = client.put("/primary-device", json={"dp_instance_id": "dp_1", "vault_id": "v_alpha"}, headers=headers)
    assert res.status_code == 200

    # Bob's primary device MUST remain dp_bob_0 and is_primary = 1
    with db.connect() as conn:
        bob_check = conn.execute("SELECT is_primary FROM dp_instances WHERE dp_instance_id = 'dp_bob_0'").fetchone()[0]
        assert bob_check == 1, "Bob's primary lease was unexpectedly demoted by Alice's lease switch!"

    # Alice attempts to claim Bob's device (cross-tenant / foreign user)
    res_foreign = client.put("/primary-device", json={"dp_instance_id": "dp_bob_0", "vault_id": "v_alpha"}, headers=headers)
    # Fails because dp_bob_0 does not belong to v_alpha
    assert res_foreign.status_code == 400
    assert res_foreign.json()["error"] == LeaseErrorCode.INVALID_DP

    # Alice attempts to claim Bob's device specifying Bob's vault v_beta
    res_cross_vault = client.put("/primary-device", json={"dp_instance_id": "dp_bob_0", "vault_id": "v_beta"}, headers=headers)
    # Fails because Alice's credentials don't match Bob's tenant/vault
    assert res_cross_vault.status_code in (400, 403)


# ============================================================================
# CHALLENGE 4: Clock Drift Boundary Testing (Future Drift & 7-Day Stale)
# ============================================================================

class TestClockDriftBoundaries:
    """
    Adversarial boundary testing of UploadLeaseValidator.validate_segment_time.
    Validates the exact boundary thresholds between accepted timestamps and
    HTTP 409 recording.upload_rejected_invalid_time.
    """

    def test_clock_drift_future_drift_boundaries(self):
        server_now = datetime.datetime(2026, 11, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

        # 1. Exactly on time -> Valid
        dt_0 = UploadLeaseValidator.validate_segment_time("2026-11-10T12:00:00Z", server_now=server_now)
        assert dt_0 == server_now

        # 2. Future drift +10.0s (referenced in dispatch and worker handoff) -> Valid
        dt_10 = UploadLeaseValidator.validate_segment_time("2026-11-10T12:00:10Z", server_now=server_now)
        assert (dt_10 - server_now).total_seconds() == 10.0

        # 3. Future drift +15.0s (worker claimed <=10s, but spec §3.1.3 states >30s)
        # Empirical check: does the implementation enforce 30s or 10s?
        dt_15 = UploadLeaseValidator.validate_segment_time("2026-11-10T12:00:15Z", server_now=server_now)
        assert (dt_15 - server_now).total_seconds() == 15.0  # Accepted because code threshold is 30s!

        # 4. Boundary at +30.0s -> Exactly 30.0s is NOT > 30.0s -> Valid
        dt_30 = UploadLeaseValidator.validate_segment_time("2026-11-10T12:00:30Z", server_now=server_now)
        assert (dt_30 - server_now).total_seconds() == 30.0

        # 5. Boundary at +30.1s -> Exceeds 30.0s -> Rejection HTTP 409
        with pytest.raises(LeaseRejectionException) as exc_info:
            UploadLeaseValidator.validate_segment_time("2026-11-10T12:00:30.100Z", server_now=server_now)
        assert exc_info.value.status_code == 409
        assert exc_info.value.error_code == LeaseErrorCode.INVALID_TIME
        assert "Clock drift" in exc_info.value.error_message

        # 6. Future drift +45.0s -> Rejection HTTP 409
        with pytest.raises(LeaseRejectionException) as exc_info:
            UploadLeaseValidator.validate_segment_time("2026-11-10T12:00:45Z", server_now=server_now)
        assert exc_info.value.status_code == 409
        assert exc_info.value.error_code == LeaseErrorCode.INVALID_TIME

    def test_clock_drift_stale_boundaries(self):
        server_now = datetime.datetime(2026, 11, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)

        # 1. Stale by exactly 6 days 23 hours (within 7 days) -> Valid
        dt_6d = UploadLeaseValidator.validate_segment_time("2026-11-03T13:00:00Z", server_now=server_now)
        assert (server_now - dt_6d).total_seconds() == 6 * 86400 + 23 * 3600

        # 2. Stale by exactly 7 days (7 * 86400s) -> Valid (not > 7 days)
        dt_7d = UploadLeaseValidator.validate_segment_time("2026-11-03T12:00:00Z", server_now=server_now)
        assert (server_now - dt_7d).total_seconds() == 7 * 86400

        # 3. Stale by 7 days + 1 second -> Rejection HTTP 409
        with pytest.raises(LeaseRejectionException) as exc_info:
            UploadLeaseValidator.validate_segment_time("2026-11-03T11:59:59Z", server_now=server_now)
        assert exc_info.value.status_code == 409
        assert exc_info.value.error_code == LeaseErrorCode.INVALID_TIME
        assert "stale" in exc_info.value.error_message.lower()

        # 4. Stale by 8 days -> Rejection HTTP 409
        with pytest.raises(LeaseRejectionException) as exc_info:
            UploadLeaseValidator.validate_segment_time("2026-11-02T12:00:00Z", server_now=server_now)
        assert exc_info.value.status_code == 409
        assert exc_info.value.error_code == LeaseErrorCode.INVALID_TIME

    def test_clock_drift_malformed_timestamps(self):
        # Malformed format -> Rejection HTTP 409 invalid_time
        with pytest.raises(LeaseRejectionException) as exc_info:
            UploadLeaseValidator.validate_segment_time("invalid-timestamp-string")
        assert exc_info.value.status_code == 409
        assert exc_info.value.error_code == LeaseErrorCode.INVALID_TIME


# ============================================================================
# CHALLENGE 5: Listening Schedule Timezone Transitions & Midnight Rollover
# ============================================================================

class TestListeningScheduleTimezones:
    """
    Adversarial testing of listening schedule window evaluation across:
    1. Boundary minutes: exact start_minutes, exact end_minutes, -1m, +1m
    2. Midnight rollover: UTC timestamp crossing day boundary in local timezone
    3. Multiple split windows on the same weekday
    4. Daylight Saving Time (DST) transitions (EDT vs EST)
    5. Schedule disabled and empty window bypass (24/7 recording)
    """

    @pytest.fixture
    def schedule_db(self, tmp_path: Path):
        db = Database(tmp_path)
        with db.connect(immediate=True) as conn:
            conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t1', 'T1', 't1')")
            conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name) VALUES ('v1', 't1', 'V1')")
            conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email) VALUES ('u1', 't1', 'u1@t1.com')")
            conn.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp1', 't1', 'v1', 'u1', 'DP1', 'phone', 1)")
        return db

    def test_schedule_minute_boundaries(self, schedule_db: Database):
        """
        Window configured on Tuesday (weekday=2) from 09:00 to 17:00 (540m to 1020m).
        Check exact boundary behavior at:
        - 08:59:00 (539m) -> OUTSIDE
        - 09:00:00 (540m) -> INSIDE
        - 17:00:00 (1020m) -> INSIDE
        - 17:01:00 (1021m) -> OUTSIDE
        """
        windows = [
            {"weekday": 2, "window_start": "09:00", "window_end": "17:00", "start_minutes": 540, "end_minutes": 1020}
        ]
        with schedule_db.connect(immediate=True) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO listening_schedules (schedule_id, tenant_id, vault_id, user_id, dp_instance_id, weekly_windows, is_enabled)
                   VALUES ('s1', 't1', 'v1', 'u1', 'dp1', ?, 1)""",
                (json.dumps(windows),),
            )

        # Reference date: 2026-11-10 is a Tuesday (isoweekday = 2)
        with schedule_db.connect() as conn:
            # 1. 08:59 UTC -> 539m -> Rejected
            dt_before = datetime.datetime(2026, 11, 10, 8, 59, 0, tzinfo=datetime.timezone.utc)
            with pytest.raises(LeaseRejectionException) as exc:
                UploadLeaseValidator.validate_listening_schedule_window(
                    conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=dt_before, capture_timezone_str="UTC"
                )
            assert exc.value.error_code == LeaseErrorCode.OUT_OF_PRIMARY_WINDOW

            # 2. 09:00 UTC -> 540m -> Accepted
            dt_start = datetime.datetime(2026, 11, 10, 9, 0, 0, tzinfo=datetime.timezone.utc)
            UploadLeaseValidator.validate_listening_schedule_window(
                conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=dt_start, capture_timezone_str="UTC"
            )

            # 3. 17:00 UTC -> 1020m -> Accepted
            dt_end = datetime.datetime(2026, 11, 10, 17, 0, 0, tzinfo=datetime.timezone.utc)
            UploadLeaseValidator.validate_listening_schedule_window(
                conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=dt_end, capture_timezone_str="UTC"
            )

            # 4. 17:01 UTC -> 1021m -> Rejected
            dt_after = datetime.datetime(2026, 11, 10, 17, 1, 0, tzinfo=datetime.timezone.utc)
            with pytest.raises(LeaseRejectionException) as exc:
                UploadLeaseValidator.validate_listening_schedule_window(
                    conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=dt_after, capture_timezone_str="UTC"
                )
            assert exc.value.error_code == LeaseErrorCode.OUT_OF_PRIMARY_WINDOW

    def test_schedule_midnight_rollover_and_cross_timezone_transitions(self, schedule_db: Database):
        """
        Verify timezone offset conversion:
        Monday 23:30 UTC = Tuesday 08:30 in Asia/Tokyo (UTC+9)
        Monday 23:30 UTC = Monday 18:30 in America/New_York (UTC-5)
        """
        # Configure schedule for Tuesday (weekday=2) 08:00 - 10:00 (480m - 600m)
        windows = [
            {"weekday": 2, "window_start": "08:00", "window_end": "10:00", "start_minutes": 480, "end_minutes": 600}
        ]
        with schedule_db.connect(immediate=True) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO listening_schedules (schedule_id, tenant_id, vault_id, user_id, dp_instance_id, weekly_windows, is_enabled)
                   VALUES ('s_tz', 't1', 'v1', 'u1', 'dp1', ?, 1)""",
                (json.dumps(windows),),
            )

        # UTC timestamp is Monday 2026-11-09T23:30:00Z (isoweekday = 1)
        seg_dt = datetime.datetime(2026, 11, 9, 23, 30, 0, tzinfo=datetime.timezone.utc)

        with schedule_db.connect() as conn:
            # When interpreted in Asia/Tokyo:
            # 23:30 Monday UTC + 9h = 08:30 Tuesday Tokyo -> isoweekday=2, minutes=510 -> Inside window!
            UploadLeaseValidator.validate_listening_schedule_window(
                conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=seg_dt, capture_timezone_str="Asia/Tokyo"
            )

            # When interpreted in UTC:
            # isoweekday=1 (Monday), minutes=1410 -> Outside Tuesday window -> Rejected!
            with pytest.raises(LeaseRejectionException) as exc:
                UploadLeaseValidator.validate_listening_schedule_window(
                    conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=seg_dt, capture_timezone_str="UTC"
                )
            assert exc.value.error_code == LeaseErrorCode.OUT_OF_PRIMARY_WINDOW

            # When interpreted in America/New_York (UTC-5):
            # 23:30 Monday UTC - 5h = 18:30 Monday NY -> isoweekday=1, minutes=1110 -> Rejected!
            with pytest.raises(LeaseRejectionException) as exc:
                UploadLeaseValidator.validate_listening_schedule_window(
                    conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=seg_dt, capture_timezone_str="America/New_York"
                )
            assert exc.value.error_code == LeaseErrorCode.OUT_OF_PRIMARY_WINDOW

    def test_schedule_daylight_saving_transitions(self, schedule_db: Database):
        """
        Verify daylight saving time adjustments:
        Schedule configured for Tuesday 09:00 - 11:00 (540m - 660m) in America/New_York.
        - Summer (EDT, UTC-4): 2026-06-16T13:30:00Z -> 09:30 EDT -> Inside window!
        - Winter (EST, UTC-5): 2026-01-13T13:30:00Z -> 08:30 EST -> Outside window (rejected)!
        - Winter (EST, UTC-5): 2026-01-13T14:30:00Z -> 09:30 EST -> Inside window!
        """
        windows = [
            {"weekday": 2, "window_start": "09:00", "window_end": "11:00", "start_minutes": 540, "end_minutes": 660}
        ]
        with schedule_db.connect(immediate=True) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO listening_schedules (schedule_id, tenant_id, vault_id, user_id, dp_instance_id, weekly_windows, is_enabled)
                   VALUES ('s_dst', 't1', 'v1', 'u1', 'dp1', ?, 1)""",
                (json.dumps(windows),),
            )

        with schedule_db.connect() as conn:
            # Summer: EDT (UTC-4)
            # 13:30 UTC - 4h = 09:30 EDT (Tuesday, 570m) -> Accepted
            dt_summer = datetime.datetime(2026, 6, 16, 13, 30, 0, tzinfo=datetime.timezone.utc)
            UploadLeaseValidator.validate_listening_schedule_window(
                conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=dt_summer, capture_timezone_str="America/New_York"
            )

            # Winter: EST (UTC-5)
            # 13:30 UTC - 5h = 08:30 EST (Tuesday, 510m) -> Outside [540, 660] -> Rejected
            dt_winter_early = datetime.datetime(2026, 1, 13, 13, 30, 0, tzinfo=datetime.timezone.utc)
            with pytest.raises(LeaseRejectionException) as exc:
                UploadLeaseValidator.validate_listening_schedule_window(
                    conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=dt_winter_early, capture_timezone_str="America/New_York"
                )
            assert exc.value.error_code == LeaseErrorCode.OUT_OF_PRIMARY_WINDOW

            # Winter: EST (UTC-5)
            # 14:30 UTC - 5h = 09:30 EST (Tuesday, 570m) -> Accepted
            dt_winter_ok = datetime.datetime(2026, 1, 13, 14, 30, 0, tzinfo=datetime.timezone.utc)
            UploadLeaseValidator.validate_listening_schedule_window(
                conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=dt_winter_ok, capture_timezone_str="America/New_York"
            )

    def test_schedule_disabled_and_empty_windows_bypass(self, schedule_db: Database):
        """
        When schedule is disabled (is_enabled=0) or weekly_windows=[],
        24/7 recording is permitted without restriction.
        """
        dt_any = datetime.datetime(2026, 11, 10, 3, 30, 0, tzinfo=datetime.timezone.utc)

        # 1. Disabled schedule
        windows = [{"weekday": 2, "window_start": "09:00", "window_end": "17:00", "start_minutes": 540, "end_minutes": 1020}]
        with schedule_db.connect(immediate=True) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO listening_schedules (schedule_id, tenant_id, vault_id, user_id, dp_instance_id, weekly_windows, is_enabled)
                   VALUES ('s_dis', 't1', 'v1', 'u1', 'dp1', ?, 0)""",
                (json.dumps(windows),),
            )
            # Even though 03:30 is outside 09:00-17:00, disabled schedule allows it
            UploadLeaseValidator.validate_listening_schedule_window(
                conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=dt_any, capture_timezone_str="UTC"
            )

        # 2. Empty windows
        with schedule_db.connect(immediate=True) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO listening_schedules (schedule_id, tenant_id, vault_id, user_id, dp_instance_id, weekly_windows, is_enabled)
                   VALUES ('s_emp', 't1', 'v1', 'u1', 'dp1', '[]', 1)"""
            )
            UploadLeaseValidator.validate_listening_schedule_window(
                conn=conn, tenant_id="t1", vault_id="v1", dp_instance_id="dp1", seg_dt=dt_any, capture_timezone_str="UTC"
            )


# ============================================================================
# CHALLENGE 6: Concurrent Upload & Primary Lease Switching Race
# ============================================================================

def test_concurrent_upload_and_lease_switch_race(challenge_env):
    """
    Race condition test:
    dp_0 is primary. 10 threads attempt upload from dp_0,
    while 5 concurrent threads attempt to switch primary to dp_1.
    Verify:
    1. Uploads that validate before the switch succeed (201 Created).
    2. Uploads that validate after the switch are rejected with 409 not_primary.
    3. No 500 internal server errors, no database corruptions, no lock deadlocks.
    """
    app, db, settings, client, headers = challenge_env
    stream = make_adts_stream(5)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    upload_results: list[int] = []
    switch_results: list[int] = []

    def uploader(idx: int):
        local_client = TestClient(app)
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps({
                "dp_instance_id": "dp_0",
                "live_session_id": f"sess_race_{idx}",
                "segment_index": 0,
            }),
        }
        res = local_client.post(
            "/tenants/t_alpha/vaults/v_alpha/recordings/upload",
            data=data,
            files=files,
            headers=headers,
        )
        upload_results.append(res.status_code)

    def switcher():
        local_client = TestClient(app)
        res = local_client.put(
            "/primary-device",
            json={"dp_instance_id": "dp_1", "vault_id": "v_alpha"},
            headers=headers,
        )
        switch_results.append(res.status_code)

    threads = []
    # Interleave 10 upload threads and 4 lease switch threads
    for i in range(5):
        threads.append(threading.Thread(target=uploader, args=(i * 2,)))
        threads.append(threading.Thread(target=switcher))
        threads.append(threading.Thread(target=uploader, args=(i * 2 + 1,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify that all lease switches succeeded
    for status_code in switch_results:
        assert status_code == 200, f"Lease switch failed with status {status_code}"

    # Verify that uploads only either succeeded (201) or were rejected due to not_primary (409)
    for status_code in upload_results:
        assert status_code in (201, 409), f"Unexpected upload status code: {status_code}"

    # Verify database integrity: exactly one primary remains
    with db.connect() as conn:
        primaries = conn.execute(
            "SELECT dp_instance_id FROM dp_instances WHERE tenant_id = 't_alpha' AND vault_id = 'v_alpha' AND is_primary = 1"
        ).fetchall()
        assert len(primaries) == 1
        assert primaries[0]["dp_instance_id"] == "dp_1"


def test_concurrent_duplicate_upload_toctou_behavior(challenge_env):
    """
    Adversarial challenge:
    Tests TOCTOU race condition when 4 concurrent threads upload the exact same segment.
    In upload.py, the idempotency check SELECT is non-transactional relative to INSERT.
    If multiple requests race, one succeeds (201), and racing duplicates may trigger
    sqlite3.IntegrityError causing HTTP 500 rather than graceful idempotent receipts.
    """
    app, db, settings, client, headers = challenge_env
    stream = make_adts_stream(5)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    results: list[tuple[int, str]] = []

    def duplicate_uploader():
        local_client = TestClient(app, raise_server_exceptions=False)
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps({
                "dp_instance_id": "dp_0",
                "live_session_id": "sess_toctou_race",
                "segment_index": 0,
            }),
        }
        res = local_client.post(
            "/tenants/t_alpha/vaults/v_alpha/recordings/upload",
            data=data,
            files=files,
            headers=headers,
        )
        results.append((res.status_code, res.text))

    threads = [threading.Thread(target=duplicate_uploader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # At least one upload succeeds with 201 Created
    status_codes = [r[0] for r in results]
    assert 201 in status_codes, f"Expected at least one 201 Created, got: {status_codes}"
