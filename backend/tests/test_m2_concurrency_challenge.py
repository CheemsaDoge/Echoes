"""
backend/tests/test_m2_concurrency_challenge.py
Adversarial Empirical Concurrency & Idempotency Stress Test Suite for Milestone M2.
Authored by empirical_challenger (challenger_m2_recheck_1).

Evaluates under multi-threaded concurrency (TOCTOU race handling):
1. Massive Concurrency Idempotency (30+ simultaneous threads on same session & segment index).
2. Simultaneous Cross-Tenant Session Collision Race (Tenant A vs Tenant B racing on uncreated session).
3. Concurrent Multi-Segment Pipeline Race (multiple segments racing concurrently with duplicate retransmissions).
4. Cross-Tenant Concurrent Infiltration on Existing Session (Tenant B racing on Tenant A's existing session).
5. Metadata Variance Race (concurrent uploads with differing client recording_ids on same segment).
6. High Concurrency SQLite Lock Contention & Busy Timeout Stress (40-50 threads).
7. Cross-Vault Simultaneous Collision Race (Vault 1 vs Vault 2 within same Tenant).
8. Cross-User Simultaneous Collision Race (User Alice vs User Charlie within same Tenant & Vault).
9. Concurrent Divergent Timestamps Race (Threads uploading same session & segment with different started_at).
10. Massive Interleaved Multi-Tenant Chaos Race (60 concurrent threads across 3 actors).
"""
from __future__ import annotations

import concurrent.futures
import datetime
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.lease import LeaseErrorCode
from tests.test_tenancy_m2 import make_adts_stream


@pytest.fixture
def test_rig(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        api_token="alpha-admin-token-at-least-24-chars-long",
        run_worker=False,
    )
    app = create_app(settings)
    db: Database = app.state.db

    # Seed 2 distinct tenants with users, vaults, and devices
    with db.connect(immediate=True) as conn:
        # Tenant 1 (Alpha)
        conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t_alpha', 'Alpha Corp', 'alpha')")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_alpha_1', 't_alpha', 'Vault Alpha 1', 1)")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_alpha_2', 't_alpha', 'Vault Alpha 2', 0)")
        
        # User Alice in Vault Alpha 1
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_alice', 't_alpha', 'alice@alpha.com', 'Alice')")
        conn.execute("""
            INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
            VALUES ('dp_alice_phone', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Phone', 'phone', 1),
                   ('dp_alice_v2_phone', 't_alpha', 'v_alpha_2', 'u_alice', 'Alice V2 Phone', 'phone', 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
            VALUES ('bind_alice_phone', 't_alpha', 'u_alice', 'dp_alice_phone', 'uuid_alice_phone', 'android', 'token_alice_phone', 'bound'),
                   ('bind_alice_v2_phone', 't_alpha', 'u_alice', 'dp_alice_v2_phone', 'uuid_alice_v2_phone', 'android', 'token_alice_v2_phone', 'bound')
        """)

        # User Charlie in Vault Alpha 1 (Same tenant & vault, different user)
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_charlie', 't_alpha', 'charlie@alpha.com', 'Charlie')")
        conn.execute("""
            INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
            VALUES ('dp_charlie_phone', 't_alpha', 'v_alpha_1', 'u_charlie', 'Charlie Phone', 'phone', 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
            VALUES ('bind_charlie_phone', 't_alpha', 'u_charlie', 'dp_charlie_phone', 'uuid_charlie_phone', 'android', 'token_charlie_phone', 'bound')
        """)

        # Tenant 2 (Beta)
        conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t_beta', 'Beta Inc', 'beta')")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_beta_1', 't_beta', 'Vault Beta 1', 1)")
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_bob', 't_beta', 'bob@beta.com', 'Bob')")
        conn.execute("""
            INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
            VALUES ('dp_bob_phone', 't_beta', 'v_beta_1', 'u_bob', 'Bob Phone', 'phone', 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
            VALUES ('bind_bob_phone', 't_beta', 'u_bob', 'dp_bob_phone', 'uuid_bob_phone', 'android', 'token_bob_phone', 'bound')
        """)

    client = TestClient(app)
    return app, db, settings, client


class TestAdversarialConcurrencyM2:
    """Adversarial stress testing against TOCTOU races and concurrency hazards."""

    def test_massive_concurrent_identical_uploads(self, test_rig):
        """
        Challenge 1: 30 concurrent threads uploading the EXACT same session and segment index.
        Verifies:
        - 100% of threads receive HTTP 201 Created.
        - 100% of threads receive IDENTICAL receipts (asset_id, storage_key, status).
        - Exactly 1 segment row inserted into recording_segments.
        - Exactly 1 session row inserted into recording_sessions.
        - Zero unhandled IntegrityError or 500 status codes.
        """
        _, db, settings, client = test_rig
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(5)
        headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }
        meta = {
            "live_session_id": "sess_massive_concurrency_30",
            "segment_index": 0,
            "dp_instance_id": "dp_alice_phone",
        }
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps(meta),
        }

        thread_count = 30

        def send_upload():
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            return client.post(
                "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
                data=data,
                files=files,
                headers=headers,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as pool:
            futures = [pool.submit(send_upload) for _ in range(thread_count)]
            responses = [f.result() for f in futures]

        status_codes = [r.status_code for r in responses]
        assert all(code == 201 for code in status_codes), f"Status codes mismatch: {status_codes}"

        receipts = [r.json() for r in responses]
        asset_ids = {r["asset_id"] for r in receipts}
        storage_keys = {r["storage_key"] for r in receipts}
        statuses = {r["status"] for r in receipts}

        assert len(asset_ids) == 1, f"Expected exactly 1 distinct asset_id, got: {asset_ids}"
        assert len(storage_keys) == 1, f"Expected exactly 1 distinct storage_key, got: {storage_keys}"
        assert statuses == {"queued_for_processing"}

        # Database verification
        with db.connect() as conn:
            seg_rows = conn.execute(
                "SELECT * FROM recording_segments WHERE live_session_id='sess_massive_concurrency_30' AND segment_index=0"
            ).fetchall()
            assert len(seg_rows) == 1, f"Expected exactly 1 segment row, found {len(seg_rows)}"

            sess_rows = conn.execute(
                "SELECT * FROM recording_sessions WHERE live_session_id='sess_massive_concurrency_30'"
            ).fetchall()
            assert len(sess_rows) == 1, f"Expected exactly 1 session row, found {len(sess_rows)}"

    def test_simultaneous_cross_tenant_collision_race(self, test_rig):
        """
        Challenge 2: Simultaneous Cross-Tenant Session Collision Race.
        Tenant Alpha and Tenant Beta race simultaneously to upload with the SAME collided session ID
        'sess_cross_race_collision' which does not exist prior to this test.
        20 threads total: 10 threads from Alpha, 10 threads from Beta.

        Verifies:
        - Exactly ONE tenant wins the session ID.
        - ALL winning tenant's threads get HTTP 201 Created with identical receipts.
        - ALL losing tenant's threads get HTTP 403 Forbidden with clean detail message.
        - Zero 500 errors, zero unhandled sqlite3.IntegrityError.
        - The losing tenant's responses NEVER contain the winning tenant's storage_key or asset_id.
        """
        _, db, settings, client = test_rig
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream_alpha = make_adts_stream(4)
        stream_beta = make_adts_stream(6)
        session_id = "sess_cross_race_collision"

        alpha_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }
        alpha_data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps({
                "live_session_id": session_id,
                "segment_index": 0,
                "dp_instance_id": "dp_alice_phone",
            }),
        }

        beta_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_beta",
            "x-vault-id": "v_beta_1",
            "x-user-id": "u_bob",
            "x-dp-instance-id": "dp_bob_phone",
        }
        beta_data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps({
                "live_session_id": session_id,
                "segment_index": 0,
                "dp_instance_id": "dp_bob_phone",
            }),
        }

        # Build 10 Alpha tasks and 10 Beta tasks, interleaved
        tasks = []
        for i in range(10):
            tasks.append(("alpha", alpha_data, alpha_headers, stream_alpha))
            tasks.append(("beta", beta_data, beta_headers, stream_beta))

        def execute_task(task):
            tenant_name, data, headers, stream = task
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            tenant_id = headers["x-tenant-id"]
            vault_id = headers["x-vault-id"]
            res = client.post(
                f"/tenants/{tenant_id}/vaults/{vault_id}/recordings/upload",
                data=data,
                files=files,
                headers=headers,
            )
            return tenant_name, res

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(execute_task, t) for t in tasks]
            results = [f.result() for f in futures]

        alpha_results = [r for t, r in results if t == "alpha"]
        beta_results = [r for t, r in results if t == "beta"]

        # Check for any 500 errors
        all_statuses = [r.status_code for _, r in results]
        assert 500 not in all_statuses, f"Encountered HTTP 500 in concurrent race: {[r.text for _, r in results if r.status_code == 500]}"

        # Identify winning tenant from DB session row
        with db.connect() as conn:
            sess_row = conn.execute(
                "SELECT tenant_id FROM recording_sessions WHERE live_session_id=?",
                (session_id,),
            ).fetchone()
            assert sess_row is not None, "A session row must have been created"
            winner_tenant = sess_row[0]

        if winner_tenant == "t_alpha":
            winner_results = alpha_results
            loser_results = beta_results
        else:
            winner_results = beta_results
            loser_results = alpha_results

        # Winner must have 201 for all its requests
        for idx, res in enumerate(winner_results):
            assert res.status_code == 201, f"Winner request {idx} got {res.status_code}: {res.text}"

        # Winner receipts must all be identical
        winner_receipts = [r.json() for r in winner_results]
        winner_asset_ids = {r["asset_id"] for r in winner_receipts}
        winner_storage_keys = {r["storage_key"] for r in winner_receipts}
        assert len(winner_asset_ids) == 1, f"Winner received multiple asset IDs: {winner_asset_ids}"
        assert len(winner_storage_keys) == 1, f"Winner received multiple storage keys: {winner_storage_keys}"

        winning_storage_key = list(winner_storage_keys)[0]
        winning_asset_id = list(winner_asset_ids)[0]

        # Loser must have 403 for all its requests
        for idx, res in enumerate(loser_results):
            assert res.status_code == 403, f"Loser request {idx} got {res.status_code}: {res.text}"
            body_text = res.text
            # Strict isolation check: no info leak of winner's storage key or asset ID
            assert winning_storage_key not in body_text, "Information leak: winning storage key leaked to losing tenant!"
            assert winning_asset_id not in body_text, "Information leak: winning asset ID leaked to losing tenant!"
            assert "different tenant/vault/user" in res.json().get("detail", "")

    def test_cross_vault_simultaneous_collision_race(self, test_rig):
        """
        Challenge 7: Cross-Vault Simultaneous Collision Race within the same tenant.
        Tenant Alpha has Vault 1 and Vault 2. Both attempt concurrent upload using the
        same uncreated session ID 'sess_vault_collision_race'.
        10 threads Vault 1 vs 10 threads Vault 2.
        Verifies:
        - Exactly one vault wins the session.
        - Losing vault gets HTTP 403 Forbidden with zero leaks.
        - Zero 500 errors.
        """
        _, db, settings, client = test_rig
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(5)
        session_id = "sess_vault_collision_race"

        v1_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }
        v1_data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_metadata": json.dumps({
                "live_session_id": session_id,
                "segment_index": 0,
                "dp_instance_id": "dp_alice_phone",
            }),
        }

        v2_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_2",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_v2_phone",
        }
        v2_data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps({
                "live_session_id": session_id,
                "segment_index": 0,
                "dp_instance_id": "dp_alice_v2_phone",
            }),
        }

        tasks = []
        for _ in range(10):
            tasks.append(("v1", v1_data, v1_headers))
            tasks.append(("v2", v2_data, v2_headers))

        def execute_vault_task(task):
            label, data, headers = task
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            res = client.post(
                f"/tenants/{headers['x-tenant-id']}/vaults/{headers['x-vault-id']}/recordings/upload",
                data=data,
                files=files,
                headers=headers,
            )
            return label, res

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(execute_vault_task, t) for t in tasks]
            results = [f.result() for f in futures]

        all_statuses = [r.status_code for _, r in results]
        assert 500 not in all_statuses, f"Found 500 in cross-vault race: {[r.text for _, r in results if r.status_code == 500]}"

        with db.connect() as conn:
            winner_vault = conn.execute(
                "SELECT vault_id FROM recording_sessions WHERE live_session_id=?",
                (session_id,),
            ).fetchone()[0]

        winning_label = "v1" if winner_vault == "v_alpha_1" else "v2"
        losing_label = "v2" if winning_label == "v1" else "v1"

        winner_results = [r for l, r in results if l == winning_label]
        loser_results = [r for l, r in results if l == losing_label]

        for r in winner_results:
            assert r.status_code == 201
        for r in loser_results:
            assert r.status_code == 403
            assert "different tenant/vault/user" in r.json().get("detail", "")

    def test_cross_user_simultaneous_collision_race(self, test_rig):
        """
        Challenge 8: Cross-User Simultaneous Collision Race within same tenant and vault.
        User Alice vs User Charlie both racing to create session 'sess_user_collision_race'.
        10 threads Alice vs 10 threads Charlie.
        Verifies:
        - Exactly one user wins the session.
        - Losing user is rejected with HTTP 403 Forbidden.
        - Zero 500 errors.
        """
        _, db, settings, client = test_rig
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(5)
        session_id = "sess_user_collision_race"

        alice_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }
        alice_data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_metadata": json.dumps({
                "live_session_id": session_id,
                "segment_index": 0,
                "dp_instance_id": "dp_alice_phone",
            }),
        }

        charlie_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_charlie",
            "x-dp-instance-id": "dp_charlie_phone",
        }
        charlie_data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_metadata": json.dumps({
                "live_session_id": session_id,
                "segment_index": 0,
                "dp_instance_id": "dp_charlie_phone",
            }),
        }

        tasks = []
        for _ in range(10):
            tasks.append(("alice", alice_data, alice_headers))
            tasks.append(("charlie", charlie_data, charlie_headers))

        def execute_user_task(task):
            label, data, headers = task
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            res = client.post(
                "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
                data=data,
                files=files,
                headers=headers,
            )
            return label, res

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(execute_user_task, t) for t in tasks]
            results = [f.result() for f in futures]

        all_statuses = [r.status_code for _, r in results]
        assert 500 not in all_statuses

        with db.connect() as conn:
            winner_user = conn.execute(
                "SELECT user_id FROM recording_sessions WHERE live_session_id=?",
                (session_id,),
            ).fetchone()[0]

        winning_label = "alice" if winner_user == "u_alice" else "charlie"
        losing_label = "charlie" if winning_label == "alice" else "alice"

        winner_results = [r for l, r in results if l == winning_label]
        loser_results = [r for l, r in results if l == losing_label]

        for r in winner_results:
            assert r.status_code == 201
        for r in loser_results:
            assert r.status_code == 403
            assert "different tenant/vault/user" in r.json().get("detail", "")

    def test_concurrent_divergent_timestamps_on_same_segment(self, test_rig):
        """
        Challenge 9: Concurrent uploads on same session & segment index with slightly different started_at.
        Verifies:
        - All 15 concurrent threads succeed with HTTP 201.
        - Persisted segment receipt is completely deterministic.
        - Zero unhandled IntegrityErrors.
        """
        _, db, settings, client = test_rig
        stream = make_adts_stream(5)
        headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }
        session_id = "sess_divergent_ts"

        thread_count = 15
        base_now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5)

        def fire_ts(idx):
            # Vary started_at by idx fractions of a second (within valid clock window)
            ts = (base_now + datetime.timedelta(milliseconds=idx * 100)).isoformat().replace("+00:00", "Z")
            data = {
                "started_at": ts,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({
                    "live_session_id": session_id,
                    "segment_index": 0,
                    "dp_instance_id": "dp_alice_phone",
                }),
            }
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            return client.post(
                "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
                data=data,
                files=files,
                headers=headers,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as pool:
            futures = [pool.submit(fire_ts, i) for i in range(thread_count)]
            responses = [f.result() for f in futures]

        for i, r in enumerate(responses):
            assert r.status_code == 201, f"Thread {i} failed: {r.status_code} - {r.text}"

        asset_ids = {r.json()["asset_id"] for r in responses}
        storage_keys = {r.json()["storage_key"] for r in responses}
        assert len(asset_ids) == 1, f"Asset IDs diverged: {asset_ids}"
        assert len(storage_keys) == 1, f"Storage keys diverged: {storage_keys}"

    def test_cross_tenant_concurrent_requests_on_existing_session(self, test_rig):
        """
        Challenge 3: Cross-tenant concurrent infiltration on an already existing session.
        Tenant Alpha creates session 'sess_alpha_pre_existing'.
        15 threads of Tenant Beta concurrently try to upload to that session with different segment indices (0..14).
        Verifies:
        - All 15 Beta threads are rejected with HTTP 403 Forbidden.
        - Zero 500 errors.
        - Zero rows created for Beta in recording_segments.
        """
        _, db, settings, client = test_rig
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(5)

        # Alpha establishes session with segment 0
        alpha_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }
        res_init = client.post(
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "ambient",
                "capture_metadata": json.dumps({
                    "live_session_id": "sess_alpha_pre_existing",
                    "segment_index": 0,
                    "dp_instance_id": "dp_alice_phone",
                }),
            },
            files={"file": ("chunk.aac", stream, "audio/aac")},
            headers=alpha_headers,
        )
        assert res_init.status_code == 201

        # Beta launches 15 concurrent threads with indices 0..14
        beta_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_beta",
            "x-vault-id": "v_beta_1",
            "x-user-id": "u_bob",
            "x-dp-instance-id": "dp_bob_phone",
        }

        def send_beta(idx):
            data = {
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({
                    "live_session_id": "sess_alpha_pre_existing",
                    "segment_index": idx,
                    "dp_instance_id": "dp_bob_phone",
                }),
            }
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            return client.post(
                "/tenants/t_beta/vaults/v_beta_1/recordings/upload",
                data=data,
                files=files,
                headers=beta_headers,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as pool:
            futures = [pool.submit(send_beta, i) for i in range(15)]
            responses = [f.result() for f in futures]

        for idx, r in enumerate(responses):
            assert r.status_code == 403, f"Beta thread {idx} expected 403, got {r.status_code}: {r.text}"
            assert "belongs to a different tenant/vault/user" in r.json().get("detail", "")

        # Verify DB segment count remains 1 (Alpha's initial segment)
        with db.connect() as conn:
            seg_count = conn.execute(
                "SELECT COUNT(*) FROM recording_segments WHERE live_session_id='sess_alpha_pre_existing'"
            ).fetchone()[0]
            assert seg_count == 1

    def test_concurrent_multi_segment_pipeline_race(self, test_rig):
        """
        Challenge 4: Multi-segment pipeline race.
        Simulates offline recovery where 4 segments (0, 1, 2, 3) are uploaded concurrently,
        each with 5 duplicate retransmissions (20 concurrent threads in flight).
        Verifies:
        - All 20 requests succeed with HTTP 201 Created.
        - For each segment index k in (0, 1, 2, 3), all 5 duplicate retransmissions receive the identical receipt.
        - Exactly 4 segment rows in DB, one per segment_index.
        - Exactly 1 session row in DB.
        """
        _, db, settings, client = test_rig
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(5)
        headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }

        # 4 segments x 5 copies = 20 tasks
        tasks = []
        for seg_idx in range(4):
            for copy_num in range(5):
                tasks.append((seg_idx, copy_num))

        def upload_segment_copy(item):
            seg_idx, copy_num = item
            data = {
                "started_at": now_iso,
                "format": "aac",
                "source": "ambient",
                "capture_metadata": json.dumps({
                    "live_session_id": "sess_multi_seg_pipeline",
                    "segment_index": seg_idx,
                    "dp_instance_id": "dp_alice_phone",
                }),
            }
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            res = client.post(
                "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
                data=data,
                files=files,
                headers=headers,
            )
            return seg_idx, copy_num, res

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(upload_segment_copy, t) for t in tasks]
            results = [f.result() for f in futures]

        for seg_idx, copy_num, res in results:
            assert res.status_code == 201, f"Seg {seg_idx} copy {copy_num} failed with {res.status_code}: {res.text}"

        # Group by seg_idx and verify receipt consistency
        for target_seg in range(4):
            seg_receipts = [r.json() for seg_idx, _, r in results if seg_idx == target_seg]
            assert len(seg_receipts) == 5
            asset_ids = {r["asset_id"] for r in seg_receipts}
            storage_keys = {r["storage_key"] for r in seg_receipts}
            assert len(asset_ids) == 1, f"Segment {target_seg} asset IDs inconsistent: {asset_ids}"
            assert len(storage_keys) == 1, f"Segment {target_seg} storage keys inconsistent: {storage_keys}"

        # Verify DB integrity
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT segment_index, COUNT(*) FROM recording_segments WHERE live_session_id='sess_multi_seg_pipeline' GROUP BY segment_index"
            ).fetchall()
            assert len(rows) == 4
            for seg_idx, cnt in rows:
                assert cnt == 1, f"Expected 1 entry for segment {seg_idx}, got {cnt}"

    def test_metadata_variance_race(self, test_rig):
        """
        Challenge 5: Metadata variance race.
        Multiple threads upload the same (session_id, segment_index), but pass different client
        `recording_id`s in `capture_metadata` ("rec_client_thread_1", "rec_client_thread_2", ...).
        Verifies:
        - All threads return 201 Created.
        - Persisted asset_id and storage_key are identical across all threads.
        - The server returns the client's requested recording_id or persisted recording_id without crashing.
        - Zero unhandled exceptions.
        """
        _, db, settings, client = test_rig
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(5)
        headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }

        thread_count = 10

        def send_upload_with_unique_rec_id(thread_id):
            meta = {
                "live_session_id": "sess_meta_variance",
                "segment_index": 0,
                "recording_id": f"rec_custom_thread_{thread_id}",
                "dp_instance_id": "dp_alice_phone",
            }
            data = {
                "started_at": now_iso,
                "format": "aac",
                "source": "ambient",
                "capture_metadata": json.dumps(meta),
            }
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            return client.post(
                "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
                data=data,
                files=files,
                headers=headers,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as pool:
            futures = [pool.submit(send_upload_with_unique_rec_id, i) for i in range(thread_count)]
            responses = [f.result() for f in futures]

        for idx, res in enumerate(responses):
            assert res.status_code == 201, f"Thread {idx} got {res.status_code}: {res.text}"

        asset_ids = {r.json()["asset_id"] for r in responses}
        storage_keys = {r.json()["storage_key"] for r in responses}
        assert len(asset_ids) == 1, f"Asset IDs diverged: {asset_ids}"
        assert len(storage_keys) == 1, f"Storage keys diverged: {storage_keys}"

    def test_high_concurrency_sqlite_busy_stress_50(self, test_rig):
        """
        Challenge 6: High Concurrency SQLite Lock Stress (50 concurrent threads).
        Spams 50 threads hitting the upload endpoint simultaneously on the same session & segment index.
        Verifies:
        - SQLite WAL mode and busy_timeout handle 50 simultaneous lock acquisitions.
        - Zero `sqlite3.OperationalError: database is locked`.
        - Zero HTTP 500 errors.
        - Exactly 50 HTTP 201 responses.
        - Deterministic receipt resolution.
        """
        _, db, settings, client = test_rig
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(3)
        headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }
        meta = {
            "live_session_id": "sess_busy_stress_50",
            "segment_index": 0,
            "dp_instance_id": "dp_alice_phone",
        }
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "phone",
            "capture_metadata": json.dumps(meta),
        }

        concurrency = 50

        def fire_request():
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            return client.post(
                "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
                data=data,
                files=files,
                headers=headers,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(fire_request) for _ in range(concurrency)]
            responses = [f.result() for f in futures]

        for i, r in enumerate(responses):
            assert r.status_code == 201, f"Request {i} failed: {r.status_code} - {r.text}"

        asset_ids = {r.json()["asset_id"] for r in responses}
        assert len(asset_ids) == 1, f"Asset IDs diverged: {asset_ids}"

        with db.connect() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM recording_segments WHERE live_session_id='sess_busy_stress_50'"
            ).fetchone()[0]
            assert cnt == 1

    def test_interleaved_multi_tenant_chaos_race(self, test_rig):
        """
        Challenge 10: Massive Interleaved Multi-Tenant Chaos Race (60 concurrent threads).
        Actors:
        - 20 requests: Alice (Alpha, V1) targeting session 'sess_chaos_shared' (segment 0..4)
        - 20 requests: Bob (Beta, V1) targeting session 'sess_chaos_shared' (collided attempt)
        - 20 requests: Charlie (Alpha, V1, different user) targeting session 'sess_chaos_shared' (collided attempt)
        All 60 dispatched simultaneously via 60 worker threads.
        Verifies:
        - Exactly one actor owns 'sess_chaos_shared'.
        - Owner's valid uploads get 201.
        - All unauthorized actors get 403 Forbidden.
        - Zero 500 errors across all 60 requests.
        - Zero unhandled IntegrityErrors.
        """
        _, db, settings, client = test_rig
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(4)
        session_id = "sess_chaos_shared"

        alice_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }
        bob_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_beta",
            "x-vault-id": "v_beta_1",
            "x-user-id": "u_bob",
            "x-dp-instance-id": "dp_bob_phone",
        }
        charlie_headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_charlie",
            "x-dp-instance-id": "dp_charlie_phone",
        }

        tasks = []
        for i in range(20):
            seg_idx = i % 5
            tasks.append(("alice", "t_alpha", "v_alpha_1", alice_headers, "dp_alice_phone", seg_idx))
            tasks.append(("bob", "t_beta", "v_beta_1", bob_headers, "dp_bob_phone", seg_idx))
            tasks.append(("charlie", "t_alpha", "v_alpha_1", charlie_headers, "dp_charlie_phone", seg_idx))

        def execute_chaos_task(task):
            actor, tenant_id, vault_id, headers, dp_id, seg_idx = task
            data = {
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_metadata": json.dumps({
                    "live_session_id": session_id,
                    "segment_index": seg_idx,
                    "dp_instance_id": dp_id,
                }),
            }
            files = {"file": ("chunk.aac", stream, "audio/aac")}
            res = client.post(
                f"/tenants/{tenant_id}/vaults/{vault_id}/recordings/upload",
                data=data,
                files=files,
                headers=headers,
            )
            return actor, res

        with concurrent.futures.ThreadPoolExecutor(max_workers=60) as pool:
            futures = [pool.submit(execute_chaos_task, t) for t in tasks]
            results = [f.result() for f in futures]

        status_codes = [r.status_code for _, r in results]
        assert 500 not in status_codes, f"Found 500 in chaos race: {[r.text for _, r in results if r.status_code == 500]}"

        with db.connect() as conn:
            sess_row = conn.execute(
                "SELECT tenant_id, vault_id, user_id FROM recording_sessions WHERE live_session_id=?",
                (session_id,),
            ).fetchone()
            assert sess_row is not None
            owner_user = sess_row[2]

        if owner_user == "u_alice":
            owner_actor = "alice"
        elif owner_user == "u_bob":
            owner_actor = "bob"
        else:
            owner_actor = "charlie"

        owner_results = [r for a, r in results if a == owner_actor]
        unauthorized_results = [r for a, r in results if a != owner_actor]

        # All owner requests must be 201
        for r in owner_results:
            assert r.status_code == 201, f"Owner got {r.status_code}: {r.text}"

        # All unauthorized actor requests must be 403 Forbidden
        for r in unauthorized_results:
            assert r.status_code == 403, f"Unauthorized got {r.status_code}: {r.text}"
            assert "different tenant/vault/user" in r.json().get("detail", "")

    def test_live_socket_uvicorn_concurrent_uploads(self, test_rig):
        """
        Challenge 11: Real TCP Socket Concurrency under Uvicorn.
        Spawns an actual Uvicorn server on a live loopback port.
        Uses 20 concurrent threads dispatching real HTTP POST multipart requests over TCP sockets.
        Verifies:
        - Real network socket buffering and multi-threaded event loop concurrency.
        - All 20 requests return HTTP 201 Created.
        - Identical asset_id and storage_key across all 20 receipts.
        - Exactly 1 segment row in SQLite database.
        - Zero 500 errors, zero dropped connections.
        """
        import socket
        import threading
        import httpx
        import uvicorn

        app, db, settings, _ = test_rig

        # Allocate free loopback port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config=config)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()

        # Wait for server to become responsive
        url = f"http://127.0.0.1:{port}"
        base_client = httpx.Client(base_url=url, timeout=10.0)
        ready = False
        for _ in range(50):
            try:
                res = base_client.get("/healthz")
                if res.status_code in (200, 404):  # server responded
                    ready = True
                    break
            except Exception:
                time.sleep(0.05)
        assert ready, "Uvicorn server failed to boot on loopback port"

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        stream = make_adts_stream(5)
        upload_url = f"{url}/tenants/t_alpha/vaults/v_alpha_1/recordings/upload"
        headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "x-tenant-id": "t_alpha",
            "x-vault-id": "v_alpha_1",
            "x-user-id": "u_alice",
            "x-dp-instance-id": "dp_alice_phone",
        }
        data = {
            "started_at": now_iso,
            "format": "aac",
            "source": "ambient",
            "capture_timezone": "UTC",
            "capture_metadata": json.dumps({
                "live_session_id": "sess_live_uvicorn_race",
                "segment_index": 0,
                "dp_instance_id": "dp_alice_phone",
            }),
        }

        concurrency = 20

        def send_socket_request():
            with httpx.Client(timeout=15.0) as client:
                files = {"file": ("chunk.aac", stream, "audio/aac")}
                return client.post(upload_url, data=data, files=files, headers=headers)

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(send_socket_request) for _ in range(concurrency)]
            responses = [f.result() for f in futures]

        server.should_exit = True

        for i, r in enumerate(responses):
            assert r.status_code == 201, f"Socket request {i} failed: {r.status_code} - {r.text}"

        receipts = [r.json() for r in responses]
        asset_ids = {r["asset_id"] for r in receipts}
        storage_keys = {r["storage_key"] for r in receipts}

        assert len(asset_ids) == 1, f"Asset IDs diverged across sockets: {asset_ids}"
        assert len(storage_keys) == 1, f"Storage keys diverged across sockets: {storage_keys}"

        with db.connect() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM recording_segments WHERE live_session_id='sess_live_uvicorn_race'"
            ).fetchone()[0]
            assert cnt == 1, f"Expected 1 segment in DB, found {cnt}"

