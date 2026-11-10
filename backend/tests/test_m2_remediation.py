"""
backend/tests/test_m2_remediation.py
Regression test suite for Milestone M2 Iteration 2 Remediation.
Verifies all 5 security and robustness remediations:
1. Cross-tenant information leak in upload deduplication & session hijack (HTTP 403)
2. Non-admin primary recorder lease bypass via x-dp-instance-id header or metadata spoofing (HTTP 403)
3. Unhandled PermissionError on session hijack returns HTTP 403
4. Non-integer / negative segment_index returns HTTP 422
5. Concurrent upload TOCTOU race condition returns HTTP 201 idempotent receipt
"""
import concurrent.futures
import datetime
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.lease import LeaseErrorCode
from tests.test_tenancy_m2 import make_adts_stream


@pytest.fixture
def remediation_env(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        api_token="owner-admin-token-at-least-24-chars-long",
        run_worker=False,
    )
    app = create_app(settings)
    db: Database = app.state.db

    # Seed 2 distinct tenants with users and devices
    with db.connect(immediate=True) as conn:
        # Tenant 1 (Alpha)
        conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t_alpha', 'Alpha Corp', 'alpha')")
        conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_alpha_1', 't_alpha', 'Vault Alpha 1', 1)")
        conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_alice', 't_alpha', 'alice@alpha.com', 'Alice')")
        conn.execute("""
            INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
            VALUES ('dp_alice_phone', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Phone', 'phone', 1),
                   ('dp_alice_watch', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Watch', 'watch', 0)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
            VALUES ('bind_alice_watch', 't_alpha', 'u_alice', 'dp_alice_watch', 'uuid_watch', 'android', 'token_alice_watch', 'bound')
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


# ============================================================================
# 1. Cross-Tenant Deduplication Information Leak & Hijack Protection
# ============================================================================

def test_cross_tenant_upload_isolation_and_hijack_defense(remediation_env):
    _, db, settings, client = remediation_env
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    stream = make_adts_stream(5)

    # 1. Tenant Alpha uploads segment 0 with session 'sess_shared_123'
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
        "source": "ambient",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({
            "live_session_id": "sess_shared_123",
            "segment_index": 0,
            "dp_instance_id": "dp_alice_phone",
        }),
    }
    res_alpha = client.post(
        "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
        data=alpha_data,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=alpha_headers,
    )
    assert res_alpha.status_code == 201
    alpha_body = res_alpha.json()
    assert "t_alpha" in alpha_body["storage_key"]

    # 2. Tenant Beta attempts to upload specifying the exact same session_id 'sess_shared_123'
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
        "source": "ambient",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({
            "live_session_id": "sess_shared_123",
            "segment_index": 0,
            "dp_instance_id": "dp_bob_phone",
        }),
    }
    res_beta = client.post(
        "/tenants/t_beta/vaults/v_beta_1/recordings/upload",
        data=beta_data,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=beta_headers,
    )
    # MUST be rejected with HTTP 403 Forbidden
    assert res_beta.status_code == 403
    assert "belongs to a different tenant/vault/user" in res_beta.json().get("detail", "")

    # Also test with different segment_index=1 on hijacked session
    beta_data["capture_metadata"] = json.dumps({
        "live_session_id": "sess_shared_123",
        "segment_index": 1,
        "dp_instance_id": "dp_bob_phone",
    })
    res_beta_seg1 = client.post(
        "/tenants/t_beta/vaults/v_beta_1/recordings/upload",
        data=beta_data,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=beta_headers,
    )
    assert res_beta_seg1.status_code == 403
    assert "belongs to a different tenant/vault/user" in res_beta_seg1.json().get("detail", "")


# ============================================================================
# 2. Non-Admin Device Token Header & Metadata Spoofing Protection
# ============================================================================

def test_non_admin_header_spoofing_rejected(remediation_env):
    _, db, settings, client = remediation_env
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    stream = make_adts_stream(5)

    # Device token 'token_alice_watch' is bound to 'dp_alice_watch' (non-primary)
    # dp_alice_phone is primary.
    # Attempt 1: Non-admin token supplies header x-dp-instance-id: dp_alice_phone
    spoofed_headers = {
        "Authorization": "Bearer token_alice_watch",
        "x-tenant-id": "t_alpha",
        "x-vault-id": "v_alpha_1",
        "x-user-id": "u_alice",
        "x-dp-instance-id": "dp_alice_phone",  # Spoofed!
    }
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_phone"}),
    }
    res = client.post(
        "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
        data=data,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=spoofed_headers,
    )
    assert res.status_code == 403
    assert "Header x-dp-instance-id does not match authenticated binding" in res.json().get("detail", "")

    # Attempt 2: Non-admin token does not send x-dp-instance-id header, but injects dp_alice_phone in capture_metadata
    meta_spoofed_headers = {
        "Authorization": "Bearer token_alice_watch",
        "x-tenant-id": "t_alpha",
        "x-vault-id": "v_alpha_1",
        "x-user-id": "u_alice",
    }
    data_meta_spoof = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_phone"}),  # Metadata spoof!
    }
    res2 = client.post(
        "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
        data=data_meta_spoof,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=meta_spoofed_headers,
    )
    assert res2.status_code == 403
    assert "capture_metadata device ID does not match authenticated binding" in res2.json().get("detail", "")

    # Attempt 3: Non-admin token with matching dp_alice_watch is correctly evaluated against lease (returns 409 NOT_PRIMARY)
    legit_headers = {
        "Authorization": "Bearer token_alice_watch",
        "x-tenant-id": "t_alpha",
        "x-vault-id": "v_alpha_1",
        "x-user-id": "u_alice",
        "x-dp-instance-id": "dp_alice_watch",
    }
    data_legit = {
        "started_at": now_iso,
        "format": "aac",
        "source": "watch",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_watch"}),
    }
    res3 = client.post(
        "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
        data=data_legit,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=legit_headers,
    )
    assert res3.status_code == 409
    assert res3.json()["error_code"] == LeaseErrorCode.NOT_PRIMARY


# ============================================================================
# 3. Handled PermissionError on Session Hijack
# ============================================================================

def test_unhandled_permission_error_caught_as_403(remediation_env):
    _, db, settings, client = remediation_env
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    stream = make_adts_stream(5)

    # Insert an existing session directly owned by Alice
    with db.connect(immediate=True) as conn:
        conn.execute("""
            INSERT INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at)
            VALUES ('sess_alice_private', 't_alpha', 'v_alpha_1', 'u_alice', 'dp_alice_phone', '2026-11-10', '2026-11-10T00:00:00Z')
        """)

    # Bob attempts upload with live_session_id='sess_alice_private'
    bob_headers = {
        "Authorization": "Bearer token_bob_phone",
        "x-tenant-id": "t_beta",
        "x-vault-id": "v_beta_1",
        "x-user-id": "u_bob",
        "x-dp-instance-id": "dp_bob_phone",
    }
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "phone",
        "capture_timezone": "UTC",
        "capture_metadata": json.dumps({
            "live_session_id": "sess_alice_private",
            "segment_index": 0,
            "dp_instance_id": "dp_bob_phone",
        }),
    }
    res = client.post(
        "/tenants/t_beta/vaults/v_beta_1/recordings/upload",
        data=data,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=bob_headers,
    )
    assert res.status_code == 403
    assert "belongs to a different tenant/vault/user" in res.json().get("detail", "")


# ============================================================================
# 4. Safe Parsing of segment_index (HTTP 422)
# ============================================================================

@pytest.mark.parametrize(
    "bad_idx",
    [
        "not_an_int",
        "12.34",
        "NaN",
        None,
        -1,
        -99,
        [1, 2],
        {"idx": 1},
        # Raw floats (previously truncated or bypassed negative check)
        12.34,
        0.1,
        0.9,
        3.14159,
        1e2,
        -0.5,
        -1.5,
        float("inf"),
        float("-inf"),
        float("nan"),
        # Booleans (previously coerced to 1 / 0)
        True,
        False,
        # SQLite 64-bit signed integer bounds
        10**30,
        2**63,
        -9223372036854775809,
        # Special strings
        "",
        "   ",
        "-",
        "+",
        "0x10",
        "Infinity",
        "-Infinity",
    ],
)
def test_invalid_segment_index_returns_422(remediation_env, bad_idx):
    _, _, settings, client = remediation_env
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
        "live_session_id": f"sess_idx_test_{abs(hash(str(bad_idx)))}",
        "segment_index": bad_idx,
        "dp_instance_id": "dp_alice_phone",
    }
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "ambient",
        "capture_metadata": json.dumps(meta),
    }
    res = client.post(
        "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
        data=data,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=headers,
    )
    assert res.status_code == 422, f"Expected 422 for bad_idx={bad_idx}, got {res.status_code}: {res.text}"


@pytest.mark.parametrize(
    "raw_json_payload",
    [
        '{"dp_instance_id": "dp_alice_phone", "segment_index": 1e1000}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": Infinity}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": -Infinity}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": NaN}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": true}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": false}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": -0.5}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": 12.34}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": 0.1}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": ' + str(10**30) + '}',
        '{"dp_instance_id": "dp_alice_phone", "segment_index": ' + str(2**63) + '}',
        '{"dp_instance_id": "dp_alice_phone", broken}',
        '[{"segment_index": 0}]',
    ],
)
def test_raw_json_fuzzing_segment_index_returns_422(remediation_env, raw_json_payload):
    _, _, settings, client = remediation_env
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    stream = make_adts_stream(3)

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
        "capture_metadata": raw_json_payload,
    }
    res = client.post(
        "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
        data=data,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=headers,
    )
    assert res.status_code == 422, f"Expected 422 for raw_json_payload={raw_json_payload}, got {res.status_code}: {res.text}"


@pytest.mark.parametrize(
    "valid_idx",
    [0, 1, 42, "42", 9223372036854775807, "0"],
)
def test_valid_segment_index_accepted(remediation_env, valid_idx):
    _, _, settings, client = remediation_env
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
        "live_session_id": f"sess_valid_idx_{valid_idx}",
        "segment_index": valid_idx,
        "dp_instance_id": "dp_alice_phone",
    }
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "ambient",
        "capture_metadata": json.dumps(meta),
    }
    res = client.post(
        "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
        data=data,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=headers,
    )
    assert res.status_code == 201, f"Expected 201 for valid_idx={valid_idx}, got {res.status_code}: {res.text}"


def test_omitted_segment_index_defaults_to_zero(remediation_env):
    _, _, settings, client = remediation_env
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
        "live_session_id": "sess_idx_omitted",
        "dp_instance_id": "dp_alice_phone",
    }
    data = {
        "started_at": now_iso,
        "format": "aac",
        "source": "ambient",
        "capture_metadata": json.dumps(meta),
    }
    res = client.post(
        "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
        data=data,
        files={"file": ("chunk.aac", stream, "audio/aac")},
        headers=headers,
    )
    assert res.status_code == 201


# ============================================================================
# 5. Concurrent Upload TOCTOU Race Condition Idempotency
# ============================================================================

def test_concurrent_upload_idempotent_resolution(remediation_env):
    _, db, settings, client = remediation_env
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    stream = make_adts_stream(5)

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
            "live_session_id": "sess_race_10_threads",
            "segment_index": 0,
            "dp_instance_id": "dp_alice_phone",
        }),
    }

    def do_upload():
        files = {"file": ("chunk.aac", stream, "audio/aac")}
        return client.post(
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            data=data,
            files=files,
            headers=headers,
        )

    # Fire 10 concurrent threads simultaneously
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(do_upload) for _ in range(10)]
        responses = [f.result() for f in futures]

    # Every single request must return 201 Created
    for idx, r in enumerate(responses):
        assert r.status_code == 201, f"Thread {idx} failed with {r.status_code}: {r.text}"

    # Every request must receive the exact same asset_id and storage_key
    first_asset = responses[0].json()["asset_id"]
    first_storage_key = responses[0].json()["storage_key"]
    for r in responses[1:]:
        assert r.json()["asset_id"] == first_asset
        assert r.json()["storage_key"] == first_storage_key

    # DB must contain exactly 1 row for this segment
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM recording_segments WHERE live_session_id='sess_race_10_threads' AND segment_index=0"
        ).fetchone()[0]
        assert count == 1, f"Expected 1 segment in DB, found {count}"
