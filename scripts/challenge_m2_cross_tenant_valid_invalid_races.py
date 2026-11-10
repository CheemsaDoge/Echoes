#!/usr/bin/env python3
"""
scripts/challenge_m2_cross_tenant_valid_invalid_races.py
Empirical Challenger Test Harness for Cross-Tenant Collision Races with Valid and Invalid segment_index.
Author: teamwork_challenger (challenger_m2_iter3_2)

Evaluates:
1. Simultaneous cross-tenant uncreated session collision race with mixed valid and invalid segment indices (30 threads: Alpha vs Beta).
2. Simultaneous cross-tenant race on an existing session with mixed valid and invalid segment indices (40 threads: Alpha owner vs Beta attacker).
3. 60-thread massive interleaved chaos race across 3 actors (Alpha Alice vs Beta Bob vs Alpha Charlie) with mixed valid and invalid segment indices.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi.testclient import TestClient
from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from tests.test_tenancy_m2 import make_adts_stream


INVALID_SEGMENT_INDEX_CASES = [
    ("negative_float", -0.5),
    ("positive_float", 12.34),
    ("small_float", 0.1),
    ("pi_float", 3.14159),
    ("bool_true", True),
    ("bool_false", False),
    ("negative_int", -1),
    ("negative_int_99", -99),
    ("negative_str", "-1"),
    ("special_str_inf", "Infinity"),
    ("special_str_ninf", "-Infinity"),
    ("special_str_nan", "NaN"),
    ("empty_str", ""),
    ("whitespace_str", "   "),
    ("hex_str", "0x10"),
    ("text_str", "abc"),
    ("scientific_str", "1e2"),
    ("overflow_float", 1e1000),
    ("huge_int_10_30", 10**30),
    ("int_2_63", 2**63),
    ("out_of_bounds_int", 9223372036854775808),
]

VALID_SEGMENT_INDEX_CASES = [
    ("zero_int", 0),
    ("one_int", 1),
    ("two_int", 2),
    ("three_str", "3"),
    ("four_int", 4),
    ("zero_str", "0"),
    ("max_int_signed_64", 9223372036854775807),
    ("large_valid_int", 1000),
]


def setup_rig(data_dir: Path):
    settings = Settings(
        data_dir=data_dir,
        api_token="alpha-admin-token-at-least-24-chars-long",
        run_worker=False,
    )
    app = create_app(settings)
    db: Database = app.state.db

    with db.connect(immediate=True) as conn:
        conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t_alpha', 'Alpha Corp', 'alpha'), ('t_beta', 'Beta Inc', 'beta')")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_alpha_1', 't_alpha', 'Vault Alpha 1', 1), ('v_alpha_2', 't_alpha', 'Vault Alpha 2', 0), ('v_beta_1', 't_beta', 'Vault Beta 1', 1)")
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_alice', 't_alpha', 'alice@alpha.com', 'Alice'), ('u_bob', 't_beta', 'bob@beta.com', 'Bob'), ('u_charlie', 't_alpha', 'charlie@alpha.com', 'Charlie')")
        conn.execute("""
            INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
            VALUES ('dp_alice_phone', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Phone', 'phone', 1),
                   ('dp_bob_phone', 't_beta', 'v_beta_1', 'u_bob', 'Bob Phone', 'phone', 1),
                   ('dp_charlie_phone', 't_alpha', 'v_alpha_1', 'u_charlie', 'Charlie Phone', 'phone', 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
            VALUES ('bind_alice_phone', 't_alpha', 'u_alice', 'dp_alice_phone', 'uuid_alice_phone', 'android', 'token_alice_phone', 'bound'),
                   ('bind_bob_phone', 't_beta', 'u_bob', 'dp_bob_phone', 'uuid_bob_phone', 'android', 'token_bob_phone', 'bound'),
                   ('bind_charlie_phone', 't_alpha', 'u_charlie', 'dp_charlie_phone', 'uuid_charlie_phone', 'android', 'token_charlie_phone', 'bound')
        """)

    client = TestClient(app, raise_server_exceptions=False)
    return app, db, settings, client


def run_scenario_1_uncreated_session_race(client: TestClient, db: Database, settings: Settings, stream: bytes, now_iso: str) -> bool:
    print("\n" + "=" * 80)
    print("SCENARIO 1: Uncreated Session Collision Race with Mixed Valid/Invalid segment_index")
    print("=" * 80)
    session_id = f"sess_uncreated_race_{datetime.datetime.now().strftime('%H%M%S_%f')}"

    # Alpha: 8 valid, 7 invalid
    alpha_tasks = []
    for i in range(8):
        case_name, val = VALID_SEGMENT_INDEX_CASES[i % len(VALID_SEGMENT_INDEX_CASES)]
        alpha_tasks.append(("alpha", "valid", case_name, val, "t_alpha", "v_alpha_1", "u_alice", "dp_alice_phone"))
    for i in range(7):
        case_name, val = INVALID_SEGMENT_INDEX_CASES[i % len(INVALID_SEGMENT_INDEX_CASES)]
        alpha_tasks.append(("alpha", "invalid", case_name, val, "t_alpha", "v_alpha_1", "u_alice", "dp_alice_phone"))

    # Beta: 8 valid, 7 invalid
    beta_tasks = []
    for i in range(8):
        case_name, val = VALID_SEGMENT_INDEX_CASES[(i + 2) % len(VALID_SEGMENT_INDEX_CASES)]
        beta_tasks.append(("beta", "valid", case_name, val, "t_beta", "v_beta_1", "u_bob", "dp_bob_phone"))
    for i in range(7):
        case_name, val = INVALID_SEGMENT_INDEX_CASES[(i + 3) % len(INVALID_SEGMENT_INDEX_CASES)]
        beta_tasks.append(("beta", "invalid", case_name, val, "t_beta", "v_beta_1", "u_bob", "dp_bob_phone"))

    all_tasks = []
    for a, b in zip(alpha_tasks, beta_tasks):
        all_tasks.append(a)
        all_tasks.append(b)

    def execute_upload(task):
        tenant_tag, validity, case_name, seg_idx, tenant_id, vault_id, user_id, dp_id = task
        headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": tenant_id,
            "x-vault-id": vault_id,
            "x-user-id": user_id,
            "x-dp-instance-id": dp_id,
        }
        meta = {
            "live_session_id": session_id,
            "segment_index": seg_idx,
            "dp_instance_id": dp_id,
        }
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps(meta),
        }
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        res = client.post(
            f"/tenants/{tenant_id}/vaults/{vault_id}/recordings/upload",
            data=data,
            files=files,
            headers=headers,
        )
        return task, res

    print(f"Dispatching {len(all_tasks)} concurrent threads simultaneously on {session_id}...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(all_tasks)) as pool:
        futures = [pool.submit(execute_upload, t) for t in all_tasks]
        results = [f.result() for f in futures]

    # Verify no 500s
    crashes = [(task, res.status_code, res.text) for task, res in results if res.status_code == 500]
    if crashes:
        print(f"[FAIL] HTTP 500 crashes detected: {crashes}")
        return False

    # Check invalid requests: MUST BE 100% 422
    invalid_results = [(task, res) for task, res in results if task[1] == "invalid"]
    invalid_failures = []
    for task, res in invalid_results:
        if res.status_code != 422:
            invalid_failures.append((task, res.status_code, res.text[:80]))
    if invalid_failures:
        print(f"[FAIL] Invalid segment_index did not return 422: {invalid_failures}")
        return False
    print(f"[PASS] All {len(invalid_results)} invalid requests returned HTTP 422 correctly.")

    # Determine winner tenant
    with db.connect() as conn:
        row = conn.execute("SELECT tenant_id, user_id FROM recording_sessions WHERE live_session_id=?", (session_id,)).fetchone()
        assert row is not None, "A session row must have been created by a valid request"
        winning_tenant, winning_user = row[0], row[1]
    print(f"[INFO] Winning tenant established in DB: {winning_tenant} ({winning_user})")

    # Check valid requests
    valid_results = [(task, res) for task, res in results if task[1] == "valid"]
    valid_failures = []
    for task, res in valid_results:
        tenant_tag = task[0]
        expected_tenant = "t_alpha" if tenant_tag == "alpha" else "t_beta"
        if expected_tenant == winning_tenant:
            if res.status_code != 201:
                valid_failures.append((task, "Expected 201 for winner", res.status_code, res.text[:80]))
        else:
            if res.status_code != 403:
                valid_failures.append((task, "Expected 403 for loser", res.status_code, res.text[:80]))
    if valid_failures:
        print(f"[FAIL] Valid requests status code mismatch: {valid_failures}")
        return False
    print(f"[PASS] All {len(valid_results)} valid requests conformed: winner got 201, loser got 403.")

    # Check DB integrity: zero segments for losing tenant or invalid indices
    with db.connect() as conn:
        segs = conn.execute("SELECT segment_id, tenant_id, segment_index FROM recording_segments WHERE live_session_id=?", (session_id,)).fetchall()
        for seg_id, t_id, s_idx in segs:
            assert t_id == winning_tenant, f"Found segment owned by losing tenant: {t_id}"
            assert isinstance(s_idx, int), f"Segment index in DB not int: {s_idx}"
            assert s_idx >= 0, f"Negative segment index in DB: {s_idx}"
    print(f"[PASS] DB verified: {len(segs)} valid segments persisted, 100% owned by {winning_tenant}.")
    return True


def run_scenario_2_existing_session_race(client: TestClient, db: Database, settings: Settings, stream: bytes, now_iso: str) -> bool:
    print("\n" + "=" * 80)
    print("SCENARIO 2: Existing Session Race with Mixed Valid/Invalid segment_index")
    print("=" * 80)
    session_id = f"sess_existing_race_{datetime.datetime.now().strftime('%H%M%S_%f')}"

    # Pre-seed session owned by Alpha
    with db.connect(immediate=True) as conn:
        conn.execute("""
            INSERT INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at)
            VALUES (?, 't_alpha', 'v_alpha_1', 'u_alice', 'dp_alice_phone', '2026-11-10', ?)
        """, (session_id, now_iso))

    tasks = []
    # 10 valid Alpha (5 segment 0 idempotent, 5 new segment 1)
    for i in range(5):
        tasks.append(("alpha", "valid", "seg0_idempotent", 0, "t_alpha", "v_alpha_1", "u_alice", "dp_alice_phone"))
        tasks.append(("alpha", "valid", "seg1_new", 1, "t_alpha", "v_alpha_1", "u_alice", "dp_alice_phone"))
    # 10 invalid Alpha
    for i in range(10):
        cname, val = INVALID_SEGMENT_INDEX_CASES[i % len(INVALID_SEGMENT_INDEX_CASES)]
        tasks.append(("alpha", "invalid", cname, val, "t_alpha", "v_alpha_1", "u_alice", "dp_alice_phone"))

    # 10 valid Beta (attacker trying to inject segments 0 and 1)
    for i in range(5):
        tasks.append(("beta", "valid", "seg0_attack", 0, "t_beta", "v_beta_1", "u_bob", "dp_bob_phone"))
        tasks.append(("beta", "valid", "seg1_attack", 1, "t_beta", "v_beta_1", "u_bob", "dp_bob_phone"))
    # 10 invalid Beta
    for i in range(10):
        cname, val = INVALID_SEGMENT_INDEX_CASES[(i + 5) % len(INVALID_SEGMENT_INDEX_CASES)]
        tasks.append(("beta", "invalid", cname, val, "t_beta", "v_beta_1", "u_bob", "dp_bob_phone"))

    def execute_upload(task):
        tenant_tag, validity, case_name, seg_idx, tenant_id, vault_id, user_id, dp_id = task
        headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": tenant_id,
            "x-vault-id": vault_id,
            "x-user-id": user_id,
            "x-dp-instance-id": dp_id,
        }
        meta = {
            "live_session_id": session_id,
            "segment_index": seg_idx,
            "dp_instance_id": dp_id,
        }
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps(meta),
        }
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        res = client.post(
            f"/tenants/{tenant_id}/vaults/{vault_id}/recordings/upload",
            data=data,
            files=files,
            headers=headers,
        )
        return task, res

    print(f"Dispatching 40 concurrent threads on existing session {session_id}...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as pool:
        futures = [pool.submit(execute_upload, t) for t in tasks]
        results = [f.result() for f in futures]

    # Verify no 500s
    crashes = [r.text for _, r in results if r.status_code == 500]
    if crashes:
        print(f"[FAIL] HTTP 500 crashes detected: {crashes}")
        return False

    # All invalid requests must return 422
    for task, res in results:
        if task[1] == "invalid":
            if res.status_code != 422:
                print(f"[FAIL] Invalid request {task[0]}:{task[2]} got {res.status_code} instead of 422")
                return False
    print("[PASS] 100% of invalid requests returned HTTP 422.")

    # All valid Alpha requests must return 201
    for task, res in results:
        if task[0] == "alpha" and task[1] == "valid":
            if res.status_code != 201:
                print(f"[FAIL] Valid Alpha request {task[2]} got {res.status_code} instead of 201: {res.text}")
                return False
    print("[PASS] 100% of valid Alpha requests returned HTTP 201.")

    # All valid Beta requests must return 403
    for task, res in results:
        if task[0] == "beta" and task[1] == "valid":
            if res.status_code != 403:
                print(f"[FAIL] Valid Beta request {task[2]} got {res.status_code} instead of 403: {res.text}")
                return False
            # Verify no data leakage in 403 body
            body_txt = res.text
            assert "ast_" not in body_txt and "art_" not in body_txt and ".aac" not in body_txt
    print("[PASS] 100% of valid Beta requests returned HTTP 403 with zero information leakage.")
    return True


def run_scenario_3_massive_60_chaos_race(client: TestClient, db: Database, settings: Settings, stream: bytes, now_iso: str) -> bool:
    print("\n" + "=" * 80)
    print("SCENARIO 3: 60-Thread Massive Interleaved Chaos Race (3 Actors, Valid + Invalid)")
    print("=" * 80)
    session_id = f"sess_chaos_60_{datetime.datetime.now().strftime('%H%M%S_%f')}"

    # 3 Actors:
    # Actor 1: Alice (t_alpha, v_alpha_1, u_alice)
    # Actor 2: Bob (t_beta, v_beta_1, u_bob)
    # Actor 3: Charlie (t_alpha, v_alpha_1, u_charlie - same tenant/vault, different user)
    actors = [
        ("alice", "t_alpha", "v_alpha_1", "u_alice", "dp_alice_phone"),
        ("bob", "t_beta", "v_beta_1", "u_bob", "dp_bob_phone"),
        ("charlie", "t_alpha", "v_alpha_1", "u_charlie", "dp_charlie_phone"),
    ]

    tasks = []
    # 20 tasks per actor: 10 valid, 10 invalid
    for actor_tag, tenant_id, vault_id, user_id, dp_id in actors:
        for i in range(10):
            cname, val = VALID_SEGMENT_INDEX_CASES[i % len(VALID_SEGMENT_INDEX_CASES)]
            tasks.append((actor_tag, "valid", cname, val, tenant_id, vault_id, user_id, dp_id))
        for i in range(10):
            cname, val = INVALID_SEGMENT_INDEX_CASES[i % len(INVALID_SEGMENT_INDEX_CASES)]
            tasks.append((actor_tag, "invalid", cname, val, tenant_id, vault_id, user_id, dp_id))

    def execute_chaos(task):
        actor_tag, validity, case_name, seg_idx, tenant_id, vault_id, user_id, dp_id = task
        headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": tenant_id,
            "x-vault-id": vault_id,
            "x-user-id": user_id,
            "x-dp-instance-id": dp_id,
        }
        meta = {
            "live_session_id": session_id,
            "segment_index": seg_idx,
            "dp_instance_id": dp_id,
        }
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps(meta),
        }
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        res = client.post(
            f"/tenants/{tenant_id}/vaults/{vault_id}/recordings/upload",
            data=data,
            files=files,
            headers=headers,
        )
        return task, res

    print(f"Dispatching 60 concurrent threads simultaneously on {session_id}...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=60) as pool:
        futures = [pool.submit(execute_chaos, t) for t in tasks]
        results = [f.result() for f in futures]

    crashes = [r.text for _, r in results if r.status_code == 500]
    if crashes:
        print(f"[FAIL] HTTP 500 crashes detected: {crashes}")
        return False

    # 1. Check all 30 invalid tasks: 100% 422
    for task, res in results:
        if task[1] == "invalid":
            if res.status_code != 422:
                print(f"[FAIL] Invalid task {task[0]}:{task[2]} got {res.status_code} instead of 422")
                return False
    print("[PASS] All 30 invalid requests across 3 actors returned HTTP 422.")

    # 2. Check winning actor in DB
    with db.connect() as conn:
        row = conn.execute("SELECT tenant_id, vault_id, user_id FROM recording_sessions WHERE live_session_id=?", (session_id,)).fetchone()
        assert row is not None, "A session row must have been created by a valid request"
        winning_tenant, winning_vault, winning_user = row[0], row[1], row[2]
    print(f"[INFO] Winning actor established in DB: tenant={winning_tenant}, user={winning_user}")

    # Map to actor tag
    if winning_user == "u_alice":
        winning_tag = "alice"
    elif winning_user == "u_bob":
        winning_tag = "bob"
    else:
        winning_tag = "charlie"

    # 3. Check valid requests: winning actor gets 201, other 2 actors get 403
    for task, res in results:
        if task[1] == "valid":
            actor_tag = task[0]
            if actor_tag == winning_tag:
                if res.status_code != 201:
                    print(f"[FAIL] Winner {actor_tag} got {res.status_code} instead of 201: {res.text}")
                    return False
            else:
                if res.status_code != 403:
                    print(f"[FAIL] Unauthorized actor {actor_tag} got {res.status_code} instead of 403: {res.text}")
                    return False
    print(f"[PASS] All valid requests conformed: {winning_tag} got 201, unauthorized actors got 403.")
    return True


def main():
    with tempfile.TemporaryDirectory() as td:
        app, db, settings, client = setup_rig(Path(td))
        stream = make_adts_stream(3)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        s1_ok = run_scenario_1_uncreated_session_race(client, db, settings, stream, now_iso)
        s2_ok = run_scenario_2_existing_session_race(client, db, settings, stream, now_iso)
        s3_ok = run_scenario_3_massive_60_chaos_race(client, db, settings, stream, now_iso)

        print("\n" + "=" * 80)
        print("EMPIRICAL CHALLENGER REGRESSION SUMMARY")
        print("=" * 80)
        print(f"Scenario 1 (Uncreated Race, 30 threads):  {'PASS' if s1_ok else 'FAIL'}")
        print(f"Scenario 2 (Existing Race, 40 threads):   {'PASS' if s2_ok else 'FAIL'}")
        print(f"Scenario 3 (60 Chaos Race, 60 threads):   {'PASS' if s3_ok else 'FAIL'}")

        if s1_ok and s2_ok and s3_ok:
            print("\n>>> DEFINITIVE VERDICT: APPROVE <<<")
            sys.exit(0)
        else:
            print("\n>>> DEFINITIVE VERDICT: CHALLENGE_FAILED <<<")
            sys.exit(1)


if __name__ == "__main__":
    main()
