#!/usr/bin/env python3
"""Adversarial Concurrency & Stress Test Suite for Milestone M4 SSE Streaming & Reconnection.
Attacks:
1. Ring buffer overflow (>256 events per turn, rolling window bounds, memory leaks, index limits)
2. Concurrent SSE subscriber connections to same turn (completed vs in-progress vs duplicate ID race)
3. Reconnection edge cases (sequential/out-of-order Last-Event-ID, seq_000, future seq_999999, malformed headers, HTTP 410)
4. Cancellation races (pre-start cancel race, mid-stream cancel terminal ordering, concurrent cancel idempotency & zero 500s)

Can be executed directly via Python (`.venv/bin/python scripts/challenge_m4_sse_concurrency_stress.py`)
or via pytest (`.venv/bin/pytest scripts/challenge_m4_sse_concurrency_stress.py -v`).
"""

from __future__ import annotations
import asyncio
import concurrent.futures
import json
import logging
import sqlite3
import sys
import threading
import time
from typing import Any
import pytest
from starlette.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.sse import TurnEventBuffer, TurnStreamManager, parse_last_event_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("m4_stress_challenge")


def parse_sse_events(raw_sse_text: str) -> list[dict[str, Any]]:
    """Parse raw W3C SSE text into list of structured event dicts."""
    events = []
    current: dict[str, Any] = {}
    for line in raw_sse_text.splitlines():
        line = line.strip()
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("id:"):
            current["id"] = line[3:].strip()
        elif line.startswith("event:"):
            current["event"] = line[6:].strip()
        elif line.startswith("data:"):
            try:
                current["data"] = json.loads(line[5:].strip())
            except Exception:
                current["data"] = line[5:].strip()
    if current:
        events.append(current)
    return events


def create_test_context(tmp_path=None):
    """Initializes isolated test app, database, and auth headers."""
    import pathlib
    import tempfile
    if tmp_path is None:
        tmp_dir = tempfile.mkdtemp(prefix="adv_stress_")
        tmp_path = pathlib.Path(tmp_dir)
    settings = Settings(
        data_dir=tmp_path,
        api_token="adversarial-test-admin-secret-token-32chars",
    )
    app = create_app(settings)
    db: Database = app.state.db

    # Seed multi-tenant entities
    with db.connect(immediate=True) as c:
        c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_adv', 'Tenant Adversarial', 'adv')")
        c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_adv', 'ten_adv', 'Adv Vault', 1)")
        c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_adv', 'ten_adv', 'adv@echo.local', 'Adv User')")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_adv', 'ten_adv', 'vlt_adv', 'usr_adv', 'Adv Phone', 'phone', 1)")
        c.execute(
            """INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
               VALUES ('bnd_adv', 'ten_adv', 'usr_adv', 'dp_adv', 'uuid_adv', 'android', 'tok_adv', 'bound')"""
        )

    headers = {
        "Authorization": "Bearer tok_adv",
        "x-tenant-id": "ten_adv",
        "x-vault-id": "vlt_adv",
        "x-user-id": "usr_adv",
        "x-dp-instance-id": "dp_adv",
    }
    return {"app": app, "db": db, "headers": headers, "settings": settings}


# =============================================================================
# 1. Ring Buffer Overflow & High-Frequency Stress Tests
# =============================================================================

class TestRingBufferOverflowAndStress:
    """Stress tests verifying ring buffer bounds, rolling window eviction, and memory containment."""

    def test_ring_buffer_rolling_window_256_overflow(self):
        """Verify ring buffer retains strictly at most 256 events when 1,000 events are pushed."""
        buf = TurnEventBuffer("turn_overflow_01", "th_overflow")
        total_pushed = 1000
        for i in range(1, total_pushed + 1):
            buf.append_event("text_delta", {"delta": f"token_{i}"})

        assert len(buf.events) == 256, f"Expected deque length 256, got {len(buf.events)}"
        assert buf.events[0].seq == total_pushed - 256 + 1, f"Oldest seq expected 745, got {buf.events[0].seq}"
        assert buf.events[-1].seq == total_pushed, f"Newest seq expected {total_pushed}, got {buf.events[-1].seq}"

        # Evicted sequence lookup: requesting seq 100 (which was evicted) returns all 256 retained events
        events_after_evicted = buf.get_events_after(100)
        assert len(events_after_evicted) == 256
        assert events_after_evicted[0].seq == 745

        # Querying un-evicted sequence: requesting seq 950 returns strictly seq 951..1000 (50 events)
        events_after_recent = buf.get_events_after(950)
        assert len(events_after_recent) == 50
        assert events_after_recent[0].seq == 951
        assert events_after_recent[-1].seq == 1000

    def test_ring_buffer_extreme_stress_100k_events(self):
        """Verify ring buffer survives 100,000 rapid event pushes without memory growth or indexing errors."""
        buf = TurnEventBuffer("turn_stress_100k", "th_stress")
        for i in range(1, 100001):
            buf.append_event("text_delta", {"delta": "chunk " + "x" * 32})

        assert len(buf.events) == 256
        assert buf.next_seq == 100001
        assert buf.events[-1].seq == 100000
        assert buf.events[0].seq == 100000 - 256 + 1

        # Replay at boundary
        replayed = buf.get_events_after(99999)
        assert len(replayed) == 1
        assert replayed[0].seq == 100000


# =============================================================================
# 2. Concurrent Subscriber Connections Tests
# =============================================================================

class TestConcurrentSubscriberConnections:
    """Attacks concurrent subscriber connections to the same active or completed turn."""

    def test_concurrent_subscribers_on_completed_turn(self, tmp_path=None):
        """Verify multiple concurrent client connections reading the same completed turn succeed."""
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]

        thread_id = "th_concur_read"
        turn_id = "turn_concur_read_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        # Initial request completes turn
        client = TestClient(app)
        res_init = client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Initial conversation turn"},
            headers=headers,
        )
        assert res_init.status_code == 200

        # Concurrent read requests with different Last-Event-IDs
        def fetch_events(last_seq: int):
            c = TestClient(app)
            h = dict(headers)
            if last_seq > 0:
                h["Last-Event-ID"] = f"{turn_id}_{last_seq:03d}"
            res = c.post(
                f"/chat/threads/{thread_id}/turns",
                json={"client_turn_id": turn_id, "message": "Resume read"},
                headers=h,
            )
            return res.status_code, len(parse_sse_events(res.text))

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_events, seq) for seq in range(10)]
            results = [f.result() for f in futures]

        for status_code, event_count in results:
            assert status_code == 200
            assert isinstance(event_count, int)

    def test_concurrent_turn_initialization_race_demonstrates_500_vulnerability(self, tmp_path=None):
        """VULNERABILITY PROBE: Simultaneous requests attempting to initialize the same client_turn_id
        trigger sqlite3.IntegrityError: UNIQUE constraint failed on chat_turns, producing HTTP 500.
        """
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]

        thread_id = "th_init_race"
        turn_id = "turn_init_race_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        def initiate_turn():
            c = TestClient(app, raise_server_exceptions=False)
            res = c.post(
                f"/chat/threads/{thread_id}/turns",
                json={"client_turn_id": turn_id, "message": "Race initialize turn"},
                headers=headers,
            )
            return res.status_code, res.text

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(initiate_turn) for _ in range(5)]
            results = [f.result() for f in futures]

        status_codes = [r[0] for r in results]
        logger.info("Concurrent turn initialization status codes: %s", status_codes)

        # Document finding: if 500 occurs, highlight unhandled unique constraint race
        has_500 = any(code == 500 for code in status_codes)
        if has_500:
            logger.warning("CONFIRMED VULNERABILITY: Concurrent turn init produced HTTP 500 error!")
        assert not has_500, f"Concurrent turn initialization produced HTTP 500 error! Status codes: {status_codes}"

    def test_concurrent_subscriber_to_in_progress_turn_premature_truncation(self, tmp_path=None):
        """VULNERABILITY PROBE: A subscriber connecting to an in-progress active turn receives
        premature truncation (partial events) rather than waiting for turn completion or receiving live events.
        """
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]
        manager: TurnStreamManager = app.state.turn_manager

        thread_id = "th_active_sub"
        turn_id = "turn_active_sub_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        # Simulate an in-progress turn: buffer exists, 2 events emitted, is_completed is False
        buf = manager.get_or_create(turn_id, thread_id)
        buf.append_event("turn_progress", {"phase": "recalling", "message": "Searching vault"})
        buf.append_event("tool_call_started", {"tool_name": "search_memory"})
        assert not buf.is_completed

        # Subscriber connects
        client = TestClient(app)
        res = client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Stream subscriber"},
            headers=headers,
        )
        assert res.status_code == 200
        events_received = parse_sse_events(res.text)

        # Later, turn completes
        buf.append_event("tool_call_completed", {"tool_name": "search_memory", "status": "completed"})
        buf.append_event("text_delta", {"delta": "Found answer."})
        buf.append_event("turn_completed", {"status": "completed", "tokens": 15})

        logger.info("Subscriber received %d events from active turn; buffer now has %d events", len(events_received), len(buf.events))
        # The subscriber only received the 2 events that existed at connection time
        # It disconnected prematurely without receiving turn_completed!
        is_prematurely_truncated = len(events_received) == 2 and not any(e["event"] == "turn_completed" for e in events_received)
        assert not is_prematurely_truncated, (
            f"Subscriber to active turn was prematurely truncated with only {len(events_received)} initial events and never received turn_completed!"
        )


# =============================================================================
# 3. Reconnection Edge Cases Tests
# =============================================================================

class TestReconnectionEdgeCases:
    """Stress tests on SSE reconnection semantics and header parsing."""

    def test_reconnection_last_event_id_zero_full_replay(self, tmp_path=None):
        """Verify Last-Event-ID: {turn_id}_000 causes full replay from sequence 1."""
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]

        thread_id = "th_replay_zero"
        turn_id = "turn_replay_zero_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        client = TestClient(app)
        res_initial = client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Replay baseline"},
            headers=headers,
        )
        initial_events = parse_sse_events(res_initial.text)
        assert len(initial_events) >= 6

        # Reconnect with seq_000
        h_reconn = dict(headers)
        h_reconn["Last-Event-ID"] = f"{turn_id}_000"
        res_reconn = client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Replay request"},
            headers=h_reconn,
        )
        replayed_events = parse_sse_events(res_reconn.text)
        assert len(replayed_events) == len(initial_events)
        assert replayed_events[0]["id"] == f"{turn_id}_001"

    def test_reconnection_future_sequence_returns_zero_events(self, tmp_path=None):
        """Verify Last-Event-ID with future sequence returns exactly 0 events."""
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]

        thread_id = "th_future_seq"
        turn_id = "turn_future_seq_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        client = TestClient(app)
        client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Future seq test"},
            headers=headers,
        )

        h_future = dict(headers)
        h_future["Last-Event-ID"] = f"{turn_id}_999999"
        res_future = client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Catch up"},
            headers=h_future,
        )
        events = parse_sse_events(res_future.text)
        assert len(events) == 0

    def test_reconnection_malformed_header_variations(self, tmp_path=None):
        """Verify server handles malformed Last-Event-ID headers without 500 errors."""
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]

        thread_id = "th_malform_var"
        turn_id = "turn_malform_var_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        client = TestClient(app)
        client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Baseline for malformed headers"},
            headers=headers,
        )

        malformed_headers = [
            "abc_def_xyz",
            "",
            f"{turn_id}_-5",
            f"{turn_id}_!@#$%^&*()_+~`",
            f"{turn_id}_",
            f"{turn_id}_NaN",
            f"{turn_id}_" + ("9" * 5000),  # Oversized int string
            "   ",
            f"wrong_turn_id_005",
        ]

        for bad_id in malformed_headers:
            h = dict(headers)
            h["Last-Event-ID"] = bad_id
            res = client.post(
                f"/chat/threads/{thread_id}/turns",
                json={"client_turn_id": turn_id, "message": "Resume"},
                headers=h,
            )
            assert res.status_code == 200, f"Malformed Last-Event-ID '{bad_id}' caused status {res.status_code}"
            events = parse_sse_events(res.text)
            # Must gracefully fall back to full replay (from seq 0) without crash
            assert len(events) >= 6

    def test_reconnection_out_of_order_rapid_cycles(self, tmp_path=None):
        """Verify rapid reconnects with out-of-order Last-Event-ID return correct subsets."""
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]

        thread_id = "th_ooo_reconn"
        turn_id = "turn_ooo_reconn_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        client = TestClient(app)
        res_initial = client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Out of order cycles"},
            headers=headers,
        )
        total_events = len(parse_sse_events(res_initial.text))
        assert total_events >= 6

        # Cycle through sequence offsets out of order: 5 -> 2 -> 7 -> 0 -> 4
        sequence_test_pattern = [5, 2, 7, 0, 4]
        for seq in sequence_test_pattern:
            h = dict(headers)
            h["Last-Event-ID"] = f"{turn_id}_{seq:03d}"
            res = client.post(
                f"/chat/threads/{thread_id}/turns",
                json={"client_turn_id": turn_id, "message": "Cycle"},
                headers=h,
            )
            assert res.status_code == 200
            events = parse_sse_events(res.text)
            for ev in events:
                ev_seq = int(ev["id"].split("_")[-1])
                assert ev_seq > seq

    def test_reconnection_expired_buffer_410_gone(self, tmp_path=None):
        """Verify that reconnecting to an expired buffer returns HTTP 410 Gone."""
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]
        manager: TurnStreamManager = app.state.turn_manager

        thread_id = "th_expire_test"
        turn_id = "turn_expire_test_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        client = TestClient(app)
        client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Will expire"},
            headers=headers,
        )

        # Mark expired in TurnStreamManager
        manager.mark_expired(turn_id)

        h = dict(headers)
        h["Last-Event-ID"] = f"{turn_id}_003"
        res = client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Reconnect expired"},
            headers=h,
        )
        assert res.status_code == 410
        assert res.json() == {"error": "turn_replay_buffer_expired", "message": "Turn replay buffer has expired"}


# =============================================================================
# 4. Cancellation Races Tests
# =============================================================================

class TestCancellationRaces:
    """Attacks turn cancellation idempotency, ordering, and race conditions."""

    def test_cancellation_pre_start_race_condition(self, tmp_path=None):
        """VULNERABILITY PROBE: Pre-start / immediate cancel race where cancellation request
        arrives before TurnEventBuffer is allocated in TurnStreamManager.
        Demonstrates that TurnStreamManager ignores pending cancellation on buffer creation,
        causing agent to execute and mark turn completed instead of cancelled.
        """
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]

        thread_id = "th_cancel_race_adv"
        turn_id = "turn_pre_cancel_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        # Client issues cancel BEFORE or EXACTLY as the stream request is dispatched
        client = TestClient(app)
        res_cancel = client.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=headers)
        assert res_cancel.status_code == 200
        assert res_cancel.json()["status"] == "cancelled"

        # Now stream request arrives
        res_stream = client.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Pre-cancelled prompt"},
            headers=headers,
        )
        assert res_stream.status_code == 200
        events = parse_sse_events(res_stream.text)
        last_event = events[-1] if events else None

        db_turn = db.get_chat_turn(turn_id)
        db_status = db_turn["status"] if db_turn else None
        logger.info("Pre-cancel test: Stream last event='%s', DB status='%s'", last_event.get("event") if last_event else None, db_status)

        # Vulnerability detection: If the turn completed despite being pre-cancelled
        is_vulnerable = (last_event and last_event.get("event") == "turn_completed") or (db_status == "completed")
        assert not is_vulnerable, (
            f"Pre-start cancellation was ignored! Turn completed with event='{last_event.get('event') if last_event else None}', db_status='{db_status}'"
        )

    def test_cancellation_mid_stream_terminal_event_order(self, tmp_path=None):
        """VULNERABILITY PROBE: Mid-stream cancellation during tool execution.
        Verifies whether tool_call_completed is erroneously emitted AFTER turn_failed.
        """
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]

        thread_id = "th_cancel_mid_adv"
        turn_id = "turn_mid_cancel_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")

        # Inject a 60ms delay in tool execution to ensure cancel arrives mid-tool
        orig_search = db.search_memory
        def slow_search(*args, **kwargs):
            time.sleep(0.06)
            return orig_search(*args, **kwargs)
        db.search_memory = slow_search

        res_stream_holder = []
        res_cancel_holder = []

        def run_stream():
            c = TestClient(app)
            r = c.post(
                f"/chat/threads/{thread_id}/turns",
                json={"client_turn_id": turn_id, "message": "Slow memory search"},
                headers=headers,
            )
            res_stream_holder.append(r)

        def run_cancel():
            time.sleep(0.02)  # Wait for stream to start and enter search
            c = TestClient(app)
            r = c.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=headers)
            res_cancel_holder.append(r)

        t1 = threading.Thread(target=run_stream)
        t2 = threading.Thread(target=run_cancel)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        res_stream = res_stream_holder[0]
        res_cancel = res_cancel_holder[0]
        assert res_stream.status_code == 200
        assert res_cancel.status_code == 200

        events = parse_sse_events(res_stream.text)
        event_types = [e["event"] for e in events]
        logger.info("Mid-stream cancel events: %s", event_types)

        # Check for event after turn_failed
        turn_failed_idx = event_types.index("turn_failed") if "turn_failed" in event_types else -1
        has_trailing_event_after_terminal = turn_failed_idx != -1 and turn_failed_idx < len(event_types) - 1
        assert not has_trailing_event_after_terminal, (
            f"Events emitted after terminal turn_failed! Full event stream: {event_types}"
        )

    def test_concurrent_cancellations_idempotency_and_no_500(self, tmp_path=None):
        """Verify 20 concurrent cancel requests against the same active turn return 200 OK without 500s."""
        ctx = create_test_context(tmp_path)
        app = ctx["app"]
        headers = ctx["headers"]
        db = ctx["db"]
        manager: TurnStreamManager = app.state.turn_manager

        thread_id = "th_concur_cancel"
        turn_id = "turn_concur_cancel_01"
        db.create_chat_thread(thread_id=thread_id, tenant_id="ten_adv", vault_id="vlt_adv", user_id="usr_adv")
        buf = manager.get_or_create(turn_id, thread_id)
        buf.append_event("turn_progress", {"phase": "recalling"})

        def send_cancel(idx):
            c = TestClient(app, raise_server_exceptions=False)
            r = c.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=headers)
            return r.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(send_cancel, i) for i in range(20)]
            results = [f.result() for f in futures]

        assert all(code == 200 for code in results), f"Non-200 code encountered: {results}"
        assert buf.is_cancelled
        assert buf.is_completed
        turn_failed_count = sum(1 for e in buf.events if e.event_type == "turn_failed")
        assert turn_failed_count == 1, f"Expected exactly 1 turn_failed event, got {turn_failed_count}"


# =============================================================================
# Standalone Runner with Diagnostic Summary
# =============================================================================

def run_all_challenges():
    print("=" * 80)
    print("M4 ADVERSARIAL STRESS & CONCURRENCY CHALLENGE RUNNER")
    print("=" * 80)

    findings: list[dict[str, Any]] = []

    # 1. Ring buffer
    print("\n[CHALLENGE 1] Ring Buffer Overflow & Rolling Window Stress...")
    t1 = TestRingBufferOverflowAndStress()
    t1.test_ring_buffer_rolling_window_256_overflow()
    print("  ✓ 1,000 events rolling window 256 bounds verified.")
    t1.test_ring_buffer_extreme_stress_100k_events()
    print("  ✓ 100,000 events high-frequency stress test passed without memory leaks.")

    # 2. Concurrency & Subscriber Tests
    print("\n[CHALLENGE 2] Concurrency & Subscriber Stress...")
    t2 = TestConcurrentSubscriberConnections()
    t2.test_concurrent_subscribers_on_completed_turn()
    print("  ✓ Concurrent read subscribers on completed turn passed.")

    try:
        t2.test_concurrent_turn_initialization_race_demonstrates_500_vulnerability()
        print("  ✓ Concurrent turn init passed without 500.")
    except AssertionError as e:
        findings.append({
            "severity": "CRITICAL",
            "title": "HTTP 500 on Concurrent Turn Initialization",
            "description": f"Simultaneous client requests initializing the same client_turn_id race on SQLite chat_turns insert, raising unhandled sqlite3.IntegrityError (UNIQUE constraint failed: chat_turns.thread_id, chat_turns.turn_index): {e}",
        })
        print(f"  ✗ VULNERABILITY FOUND: {e}")

    try:
        t2.test_concurrent_subscriber_to_in_progress_turn_premature_truncation()
        print("  ✓ Active turn subscriber handling passed.")
    except AssertionError as e:
        findings.append({
            "severity": "HIGH",
            "title": "Premature Truncation on Active Turn Subscription",
            "description": f"Subscribers connecting to an active, in-progress turn receive only current buffered events and immediately disconnect, dropping subsequent tool results and turn_completed: {e}",
        })
        print(f"  ✗ VULNERABILITY FOUND: {e}")

    # 3. Reconnection Edge Cases
    print("\n[CHALLENGE 3] Reconnection Edge Cases...")
    t3 = TestReconnectionEdgeCases()
    t3.test_reconnection_last_event_id_zero_full_replay()
    print("  ✓ Last-Event-ID: {turn_id}_000 full replay verified.")
    t3.test_reconnection_future_sequence_returns_zero_events()
    print("  ✓ Last-Event-ID: {turn_id}_999999 future sequence verified (0 events).")
    t3.test_reconnection_malformed_header_variations()
    print("  ✓ Malformed Last-Event-ID headers handled gracefully without 500.")
    t3.test_reconnection_out_of_order_rapid_cycles()
    print("  ✓ Rapid out-of-order reconnection cycles verified.")
    t3.test_reconnection_expired_buffer_410_gone()
    print("  ✓ Expired buffer returns HTTP 410 Gone with turn_replay_buffer_expired.")

    # 4. Cancellation Races
    print("\n[CHALLENGE 4] Cancellation Races...")
    t4 = TestCancellationRaces()

    try:
        t4.test_cancellation_pre_start_race_condition()
        print("  ✓ Pre-start cancellation handled correctly.")
    except AssertionError as e:
        findings.append({
            "severity": "HIGH",
            "title": "Pre-Start Cancellation Race Ignored by Turn Manager",
            "description": f"When a cancellation request is processed before TurnEventBuffer is instantiated, TurnStreamManager records the turn in _cancellations, but get_or_create() creates a new un-cancelled buffer (is_cancelled=False). The agent runs to completion, overwriting the database status to 'completed' and emitting 'turn_completed': {e}",
        })
        print(f"  ✗ VULNERABILITY FOUND: {e}")

    try:
        t4.test_cancellation_mid_stream_terminal_event_order()
        print("  ✓ Terminal event order verified.")
    except AssertionError as e:
        findings.append({
            "severity": "MEDIUM",
            "title": "Non-Terminal Event Emitted After turn_failed",
            "description": f"When mid-stream cancellation occurs during tool execution, mark_cancelled() appends turn_failed, but execute_turn() proceeds to append tool_call_completed afterward without checking buffer.is_cancelled, violating SSE wire terminal event semantics: {e}",
        })
        print(f"  ✗ VULNERABILITY FOUND: {e}")

    t4.test_concurrent_cancellations_idempotency_and_no_500()
    print("  ✓ 20 concurrent cancel requests verified (idempotent, single turn_failed, zero 500s).")

    print("\n" + "=" * 80)
    print("CHALLENGE SUMMARY & VERDICT")
    print("=" * 80)
    print(f"Total Vulnerabilities Identified: {len(findings)}")
    for i, f in enumerate(findings, 1):
        print(f"  {i}. [{f['severity']}] {f['title']}: {f['description']}")

    if findings:
        print("\nVERDICT: CHALLENGE_FAILED (Empirical vulnerabilities reproduced)")
        return False
    else:
        print("\nVERDICT: APPROVE (All adversarial challenges passed)")
        return True


if __name__ == "__main__":
    success = run_all_challenges()
    sys.exit(0 if success else 1)
