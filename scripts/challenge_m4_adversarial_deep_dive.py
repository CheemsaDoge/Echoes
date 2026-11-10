#!/usr/bin/env python3
"""Deep-Dive Concurrency & Boundary Stress Test Suite for Milestone M4 SSE Streaming & Reconnection.

Adversarial Vectors:
Vector A: Massive Subscriber Fanout (50 concurrent subscribers to an active, slowly generating turn)
Vector B: Abrupt Client Disconnection Mid-Stream (Dead queue cleanup & background task survival)
Vector C: Extreme Turn Creation Concurrency (30 concurrent distinct turns in same thread, write-lock & index stress)
Vector D: Fuzzing & Malformed Last-Event-ID Headers (negative, huge, float, null bytes, special chars)
Vector E: Cross-Tenant & Cross-Vault SSE Replay and Cancellation Attacks (zero leakage, strict 404)
Vector F: High-Frequency Cancellation Race During Active Streaming (zero trailing events, strict termination)
Vector G: Time-Based Buffer Retention & Expiration Lifecycle (HTTP 410 on expiration boundary)

Can be executed directly via Python (`.venv/bin/python scripts/challenge_m4_adversarial_deep_dive.py`).
"""

from __future__ import annotations
import asyncio
import concurrent.futures
import json
import logging
import os
import pathlib
import sys
import tempfile
import threading
import time
from typing import Any

from starlette.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.sse import TurnEventBuffer, TurnStreamManager, parse_last_event_id

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("m4_deep_dive_challenge")


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


def create_env():
    """Create test environment with isolated DB and 2 distinct tenants for cross-tenant tests."""
    tmp_dir = tempfile.mkdtemp(prefix="adv_deep_dive_")
    tmp_path = pathlib.Path(tmp_dir)
    settings = Settings(
        data_dir=tmp_path,
        api_token="test-secret-token-32chars-adversarial",
    )
    app = create_app(settings)
    db: Database = app.state.db

    with db.connect(immediate=True) as c:
        # Tenant 1 (Alpha)
        c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_alpha', 'Tenant Alpha', 'alpha')")
        c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_alpha', 'ten_alpha', 'Alpha Vault', 1)")
        c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_alpha', 'ten_alpha', 'alpha@echo.local', 'Alpha User')")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_alpha', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'Alpha Device', 'phone', 1)")
        c.execute("INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status) VALUES ('bnd_alpha', 'ten_alpha', 'usr_alpha', 'dp_alpha', 'uuid_alpha', 'android', 'tok_alpha', 'bound')")

        # Tenant 2 (Beta)
        c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_beta', 'Tenant Beta', 'beta')")
        c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_beta', 'ten_beta', 'Beta Vault', 1)")
        c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_beta', 'ten_beta', 'beta@echo.local', 'Beta User')")
        c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_beta', 'ten_beta', 'vlt_beta', 'usr_beta', 'Beta Device', 'phone', 1)")
        c.execute("INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status) VALUES ('bnd_beta', 'ten_beta', 'usr_beta', 'dp_beta', 'uuid_beta', 'android', 'tok_beta', 'bound')")

    headers_alpha = {
        "Authorization": "Bearer tok_alpha",
        "x-tenant-id": "ten_alpha",
        "x-vault-id": "vlt_alpha",
        "x-user-id": "usr_alpha",
        "x-dp-instance-id": "dp_alpha",
    }
    headers_beta = {
        "Authorization": "Bearer tok_beta",
        "x-tenant-id": "ten_beta",
        "x-vault-id": "vlt_beta",
        "x-user-id": "usr_beta",
        "x-dp-instance-id": "dp_beta",
    }

    return {
        "app": app,
        "db": db,
        "settings": settings,
        "headers_alpha": headers_alpha,
        "headers_beta": headers_beta,
        "tmp_dir": tmp_dir,
    }


# =============================================================================
# Vector A: Massive Subscriber Fanout (50 concurrent subscribers)
# =============================================================================
def test_vector_a_massive_subscriber_fanout():
    """50 concurrent subscribers connect to an active turn as it emits events."""
    env = create_env()
    app = env["app"]
    headers = env["headers_alpha"]
    db: Database = env["db"]
    manager: TurnStreamManager = app.state.turn_manager

    thread_id = "th_fanout_50"
    turn_id = "turn_fanout_50_01"
    db.create_chat_thread(thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", user_id="usr_alpha")

    # Manually drive an active turn to control event timing precisely
    buf = manager.get_or_create(turn_id, thread_id)
    buf.append_event("turn_progress", {"phase": "recalling"})

    results: list[dict[str, Any]] = []

    def subscriber_worker(sub_id: int):
        # Stagger subscriber connections slightly
        time.sleep(0.005 * (sub_id % 10))
        c = TestClient(app)
        res = c.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Subscriber listener"},
            headers=headers,
        )
        events = parse_sse_events(res.text)
        return {
            "sub_id": sub_id,
            "status_code": res.status_code,
            "events": events,
            "event_count": len(events),
        }

    # Start 50 subscribers in thread pool
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=50)
    futures = [executor.submit(subscriber_worker, i) for i in range(50)]

    # Slowly push events from background thread
    time.sleep(0.05)
    for i in range(1, 15):
        buf.append_event("text_delta", {"delta": f"token_{i} "})
        time.sleep(0.01)

    buf.append_event("turn_completed", {"status": "completed", "tokens": 42})

    for f in futures:
        results.append(f.result())
    executor.shutdown()

    # Assertions
    assert len(results) == 50, f"Expected 50 subscriber results, got {len(results)}"
    for r in results:
        assert r["status_code"] == 200, f"Subscriber {r['sub_id']} received status {r['status_code']}"
        events = r["events"]
        assert len(events) > 0, f"Subscriber {r['sub_id']} received 0 events"
        # Must have received turn_completed as the final event
        assert events[-1]["event"] == "turn_completed", f"Subscriber {r['sub_id']} did not terminate with turn_completed: {events[-1]}"
        # Sequence numbers must be strictly monotonically increasing
        seqs = [int(ev["id"].split("_")[-1]) for ev in events]
        for s1, s2 in zip(seqs[:-1], seqs[1:]):
            assert s2 > s1, f"Subscriber {r['sub_id']} received non-monotonic seqs: {seqs}"

    # Subscriber queues cleanup check
    assert len(buf._subscribers) == 0, f"Expected 0 active subscriber queues remaining, got {len(buf._subscribers)}"
    return True


# =============================================================================
# Vector B: Abrupt Client Disconnection Mid-Stream
# =============================================================================
def test_vector_b_abrupt_client_disconnect():
    """Client disconnects abruptly mid-stream; server task finishes and subsequent client can catch up."""
    env = create_env()
    app = env["app"]
    headers = env["headers_alpha"]
    db: Database = env["db"]
    manager: TurnStreamManager = app.state.turn_manager

    thread_id = "th_abrupt_disc"
    turn_id = "turn_abrupt_disc_01"
    db.create_chat_thread(thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", user_id="usr_alpha")

    # Connect with streaming client, read first chunk and close immediately
    c = TestClient(app)
    with c.stream(
        "POST",
        f"/chat/threads/{thread_id}/turns",
        json={"client_turn_id": turn_id, "message": "Long generation message"},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        # Read only a little bit then exit with-block (abrupt drop)
        for line in response.iter_lines():
            if line and "turn_progress" in line:
                break

    # Client reconnected with Last-Event-ID
    c_reconn = TestClient(app)
    h = dict(headers)
    h["Last-Event-ID"] = f"{turn_id}_001"
    res_reconn = c_reconn.post(
        f"/chat/threads/{thread_id}/turns",
        json={"client_turn_id": turn_id, "message": "Reconnect after drop"},
        headers=h,
    )
    assert res_reconn.status_code == 200
    events = parse_sse_events(res_reconn.text)
    assert len(events) >= 5, f"Expected at least 5 remaining events on replay, got {len(events)}"
    assert events[-1]["event"] == "turn_completed"

    buf = manager.get(turn_id)
    assert buf is not None
    assert buf.is_completed, "Turn was not completed after reconnection"
    assert len(buf._subscribers) == 0
    return True


# =============================================================================
# Vector C: Extreme Turn Creation Concurrency (30 distinct turns in same thread)
# =============================================================================
def test_vector_c_extreme_turn_creation_concurrency():
    """30 simultaneous requests creating distinct turns in the SAME thread.
    Probes SQLite write lock contention and turn_index sequence ordering.
    """
    env = create_env()
    app = env["app"]
    headers = env["headers_alpha"]
    db: Database = env["db"]

    thread_id = "th_concur_distinct_turns"
    db.create_chat_thread(thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", user_id="usr_alpha")

    def create_turn(idx: int):
        c = TestClient(app, raise_server_exceptions=False)
        turn_id = f"turn_dist_{idx:03d}"
        res = c.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": f"Turn payload {idx}"},
            headers=headers,
        )
        return idx, res.status_code, turn_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(create_turn, i) for i in range(30)]
        results = [f.result() for f in futures]

    status_codes = [r[1] for r in results]
    assert all(code == 200 for code in status_codes), f"Non-200 status codes in concurrent turn creation: {status_codes}"

    # Verify database turn indices
    turns = db.all("SELECT * FROM chat_turns WHERE thread_id = ?", (thread_id,))
    assert len(turns) == 30, f"Expected 30 turns in thread, got {len(turns)}"
    turn_indices = [t["turn_index"] for t in turns]
    # All indices must be unique and form range 1..30
    assert sorted(turn_indices) == list(range(1, 31)), f"Turn indices were corrupted or duplicated: {sorted(turn_indices)}"
    return True


# =============================================================================
# Vector D: Fuzzing & Malformed Last-Event-ID Headers
# =============================================================================
def test_vector_d_fuzzing_last_event_id_headers():
    """Fuzz Last-Event-ID with extreme inputs and boundary conditions."""
    env = create_env()
    app = env["app"]
    headers = env["headers_alpha"]
    db: Database = env["db"]

    thread_id = "th_fuzz_hdr"
    turn_id = "turn_fuzz_hdr_01"
    db.create_chat_thread(thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", user_id="usr_alpha")

    # Baseline turn creation
    c = TestClient(app)
    res_base = c.post(
        f"/chat/threads/{thread_id}/turns",
        json={"client_turn_id": turn_id, "message": "Baseline for fuzzing"},
        headers=headers,
    )
    assert res_base.status_code == 200
    base_events = parse_sse_events(res_base.text)
    total_base = len(base_events)
    assert total_base >= 5

    fuzz_cases = [
        ("", total_base),                          # Empty -> full replay
        (f"{turn_id}_-1", total_base),             # Negative seq -> 0 -> full replay
        (f"{turn_id}_0", total_base),              # Zero -> full replay
        (f"{turn_id}_000", total_base),            # Padded zero -> full replay
        (f"{turn_id}_001", total_base - 1),        # Sequence 1 -> replay remaining
        (f"{turn_id}_999999999", 0),               # Out of bounds -> 0 events
        (f"{turn_id}_1.5", total_base),            # Float string -> ValueError -> full replay
        (f"{turn_id}_\x00", total_base),           # Null byte -> ValueError -> full replay
        (f"{turn_id}_\r\n", total_base),           # CRLF -> ValueError -> full replay
        (f"{turn_id}_ 5 ", total_base - 5 if total_base > 5 else 0), # Whitespace inside suffix -> int(" 5 ") is 5 in Python!
        ("completely_different_turn_005", total_base), # Wrong prefix -> full replay
        (";;;DROP TABLE chat_turns;--", total_base),  # SQL injection probe -> full replay
    ]

    for bad_header, expected_min_or_exact in fuzz_cases:
        h = dict(headers)
        if bad_header != "":
            h["Last-Event-ID"] = bad_header
        res = c.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Fuzz"},
            headers=h,
        )
        assert res.status_code == 200, f"Header '{bad_header}' produced status {res.status_code}"
        evs = parse_sse_events(res.text)
        if expected_min_or_exact == 0:
            assert len(evs) == 0, f"Expected 0 events for future seq, got {len(evs)}"
        else:
            assert len(evs) >= 1, f"Expected events for header '{bad_header}', got 0"
    return True


# =============================================================================
# Vector E: Cross-Tenant & Cross-Vault SSE Replay and Cancellation Attacks
# =============================================================================
def test_vector_e_cross_tenant_isolation_attacks():
    """Tenant Beta attempts to access, replay, or cancel Tenant Alpha's turn."""
    env = create_env()
    app = env["app"]
    h_alpha = env["headers_alpha"]
    h_beta = env["headers_beta"]
    db: Database = env["db"]

    thread_alpha = "th_alpha_secret"
    turn_alpha = "turn_alpha_secret_01"
    db.create_chat_thread(thread_id=thread_alpha, tenant_id="ten_alpha", vault_id="vlt_alpha", user_id="usr_alpha")

    # Alpha creates a turn
    c_alpha = TestClient(app)
    res_alpha = c_alpha.post(
        f"/chat/threads/{thread_alpha}/turns",
        json={"client_turn_id": turn_alpha, "message": "Confidential Alpha memory"},
        headers=h_alpha,
    )
    assert res_alpha.status_code == 200

    c_beta = TestClient(app)

    # Attack 1: Beta attempts to read Alpha's thread
    res_b1 = c_beta.post(
        f"/chat/threads/{thread_alpha}/turns",
        json={"client_turn_id": "turn_beta_probe", "message": "Probe"},
        headers=h_beta,
    )
    assert res_b1.status_code == 404, f"Cross-tenant thread access returned {res_b1.status_code} instead of 404"

    # Attack 2: Beta attempts to cancel Alpha's turn in Alpha's thread
    res_b2 = c_beta.post(
        f"/chat/threads/{thread_alpha}/turns/{turn_alpha}/cancel",
        headers=h_beta,
    )
    assert res_b2.status_code == 404, f"Cross-tenant cancellation returned {res_b2.status_code} instead of 404"

    # Attack 3: Beta creates a thread in Beta's vault and attempts to steal turn_alpha by referencing turn_alpha ID
    thread_beta = "th_beta_attack"
    db.create_chat_thread(thread_id=thread_beta, tenant_id="ten_beta", vault_id="vlt_beta", user_id="usr_beta")
    res_b3 = c_beta.post(
        f"/chat/threads/{thread_beta}/turns",
        json={"client_turn_id": turn_alpha, "message": "Steal turn"},
        headers=h_beta,
    )
    assert res_b3.status_code == 404, f"Colliding foreign vault turn ID allowed with status {res_b3.status_code} instead of 404"

    # Attack 4: Beta attempts to cancel turn_alpha through Beta's thread
    res_b4 = c_beta.post(
        f"/chat/threads/{thread_beta}/turns/{turn_alpha}/cancel",
        headers=h_beta,
    )
    assert res_b4.status_code == 404, f"Cross-tenant turn cancel via local thread returned {res_b4.status_code} instead of 404"
    return True


# =============================================================================
# Vector F: High-Frequency Cancellation Race During Active Streaming
# =============================================================================
def test_vector_f_high_frequency_cancellation_race():
    """20 threads race to cancel an active turn at microsecond intervals during generation."""
    env = create_env()
    app = env["app"]
    headers = env["headers_alpha"]
    db: Database = env["db"]

    thread_id = "th_race_cancel_burst"
    turn_id = "turn_race_cancel_burst_01"
    db.create_chat_thread(thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", user_id="usr_alpha")

    # Add small delay in tool search to guarantee turn stays active for ~80ms
    orig_search = db.search_memory
    def slow_search(*args, **kwargs):
        time.sleep(0.08)
        return orig_search(*args, **kwargs)
    db.search_memory = slow_search

    stream_res = []

    def start_streaming():
        c = TestClient(app)
        r = c.post(
            f"/chat/threads/{thread_id}/turns",
            json={"client_turn_id": turn_id, "message": "Slow search turn to cancel"},
            headers=headers,
        )
        stream_res.append(r)

    def send_cancel(delay_s: float):
        time.sleep(delay_s)
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(f"/chat/threads/{thread_id}/turns/{turn_id}/cancel", headers=headers)
        return r.status_code

    t_stream = threading.Thread(target=start_streaming)
    t_stream.start()

    # Fire 20 cancel requests spaced over 5ms to 60ms
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        cancel_futures = [executor.submit(send_cancel, 0.005 + (i * 0.002)) for i in range(20)]
        cancel_results = [f.result() for f in cancel_futures]

    t_stream.join()

    assert all(code == 200 for code in cancel_results), f"Cancel status codes had non-200: {cancel_results}"
    r_stream = stream_res[0]
    assert r_stream.status_code == 200
    events = parse_sse_events(r_stream.text)
    event_types = [e["event"] for e in events]

    # Verify terminal guarantee: turn_failed MUST be the absolute last event
    assert "turn_failed" in event_types, f"turn_failed missing from events: {event_types}"
    idx = event_types.index("turn_failed")
    assert idx == len(event_types) - 1, f"Trailing events emitted after turn_failed: {event_types[idx:]}"

    # Verify DB status
    db_turn = db.get_chat_turn(turn_id)
    assert db_turn is not None
    assert db_turn["status"] == "cancelled", f"Expected DB status 'cancelled', got '{db_turn['status']}'"
    return True


# =============================================================================
# Vector G: Time-Based Buffer Retention & Expiration Lifecycle
# =============================================================================
def test_vector_g_buffer_retention_and_expiration_lifecycle():
    """Verify buffer expiration triggers HTTP 410 Gone with exact error payload."""
    env = create_env()
    app = env["app"]
    headers = env["headers_alpha"]
    db: Database = env["db"]
    manager: TurnStreamManager = app.state.turn_manager

    # Override retention_seconds to 0.25 seconds for fast deterministic lifecycle testing
    manager.retention_seconds = 0.25

    thread_id = "th_lifecycle_expire"
    turn_id = "turn_lifecycle_expire_01"
    db.create_chat_thread(thread_id=thread_id, tenant_id="ten_alpha", vault_id="vlt_alpha", user_id="usr_alpha")

    c = TestClient(app)
    res_init = c.post(
        f"/chat/threads/{thread_id}/turns",
        json={"client_turn_id": turn_id, "message": "Expiring turn"},
        headers=headers,
    )
    assert res_init.status_code == 200

    # Immediate reconnect while buffer is fresh (< 0.25s) succeeds
    h = dict(headers)
    h["Last-Event-ID"] = f"{turn_id}_001"
    res_fresh = c.post(
        f"/chat/threads/{thread_id}/turns",
        json={"client_turn_id": turn_id, "message": "Fresh reconnect"},
        headers=h,
    )
    assert res_fresh.status_code == 200

    # Wait 0.35s to exceed retention_seconds (0.25s)
    time.sleep(0.35)

    # Reconnect attempt with Last-Event-ID must return HTTP 410
    res_expired = c.post(
        f"/chat/threads/{thread_id}/turns",
        json={"client_turn_id": turn_id, "message": "Expired reconnect"},
        headers=h,
    )
    assert res_expired.status_code == 410, f"Expected 410 Gone on expired buffer, got {res_expired.status_code}"
    payload = res_expired.json()
    assert payload["error"] == "turn_replay_buffer_expired"

    # Subsequent request on terminal turn without Last-Event-ID must also return 410
    res_term_without_lid = c.post(
        f"/chat/threads/{thread_id}/turns",
        json={"client_turn_id": turn_id, "message": "Terminal turn repeat"},
        headers=headers,
    )
    assert res_term_without_lid.status_code == 410
    assert res_term_without_lid.json()["error"] == "turn_replay_buffer_expired"
    return True


# =============================================================================
# Runner
# =============================================================================
def run_all_deep_dive_challenges():
    print("=" * 80)
    print("M4 DEEP-DIVE CONCURRENCY & ADVERSARIAL STRESS HARNESS")
    print("=" * 80)

    tests = [
        ("Vector A: Massive Subscriber Fanout (50 concurrent subscribers)", test_vector_a_massive_subscriber_fanout),
        ("Vector B: Abrupt Client Disconnection Mid-Stream (Cleanup & Resume)", test_vector_b_abrupt_client_disconnect),
        ("Vector C: Extreme Turn Creation Concurrency (30 in same thread)", test_vector_c_extreme_turn_creation_concurrency),
        ("Vector D: Fuzzing & Malformed Last-Event-ID Headers", test_vector_d_fuzzing_last_event_id_headers),
        ("Vector E: Cross-Tenant & Cross-Vault SSE Replay & Cancellation", test_vector_e_cross_tenant_isolation_attacks),
        ("Vector F: High-Frequency Cancellation Race During Streaming", test_vector_f_high_frequency_cancellation_race),
        ("Vector G: Time-Based Buffer Retention & Expiration Lifecycle (410)", test_vector_g_buffer_retention_and_expiration_lifecycle),
    ]

    failed = []
    for name, fn in tests:
        print(f"\n[EXEC] Running {name}...")
        t0 = time.time()
        try:
            fn()
            elapsed = time.time() - t0
            print(f"  ✓ PASSED in {elapsed:.2f}s")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  ✗ FAILED in {elapsed:.2f}s: {e}")
            import traceback
            traceback.print_exc()
            failed.append((name, str(e)))

    print("\n" + "=" * 80)
    print("DEEP-DIVE CHALLENGE SUMMARY")
    print("=" * 80)
    print(f"Total Tests Executed: {len(tests)}")
    print(f"Passed:               {len(tests) - len(failed)}")
    print(f"Failed:               {len(failed)}")

    if failed:
        print("\nVERDICT: CHALLENGE_FAILED")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return False
    else:
        print("\nVERDICT: APPROVE (All deep-dive adversarial stress vectors passed!)")
        return True


if __name__ == "__main__":
    success = run_all_deep_dive_challenges()
    sys.exit(0 if success else 1)
