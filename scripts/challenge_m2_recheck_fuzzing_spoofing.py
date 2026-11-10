#!/usr/bin/env python3
"""
scripts/challenge_m2_recheck_fuzzing_spoofing.py
Empirical Challenger Test Harness for Milestone M2 Remediation Re-check:
Author: teamwork_challenger (challenger_m2_recheck_2)

Evaluates:
Vector 1: Non-admin token header spoofing across x-dp-instance-id, x-user-id, x-tenant-id, and capture_metadata.
Vector 2: Adversarial fuzzing of segment_index (null, negative numbers, floats, special strings, invalid JSON, booleans, large numbers).
"""
import sys
import json
import tempfile
import datetime
from pathlib import Path
from typing import List, Tuple, Dict, Any

from fastapi.testclient import TestClient
from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.lease import LeaseErrorCode
from tests.test_tenancy_m2 import make_adts_stream


def run_vector_1_tests(client: TestClient, settings: Settings, stream: bytes, now_iso: str) -> Tuple[int, int, List[str]]:
    print("=" * 80)
    print("VECTOR 1: NON-ADMIN TOKEN HEADER & METADATA SPOOFING TESTS")
    print("=" * 80)

    # Token 'token_alice_watch' is bound to: tenant='t_alpha', user='u_alice', dp='dp_alice_watch' (non-primary)
    # Alice's primary device is 'dp_alice_phone'.
    # Bob is in tenant 't_beta', user 'u_bob', dp 'dp_bob_phone'.

    passed = 0
    failed = 0
    failures = []

    cases = [
        (
            "1.1 Header x-dp-instance-id spoofing (watch claims phone)",
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_timezone": "UTC", "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_watch"})},
            {"Authorization": "Bearer token_alice_watch", "x-tenant-id": "t_alpha", "x-user-id": "u_alice", "x-dp-instance-id": "dp_alice_phone"},
            403,
            "Header x-dp-instance-id does not match authenticated binding"
        ),
        (
            "1.2 Header x-dp-instance-id cross-tenant spoofing (watch claims Bob phone)",
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_timezone": "UTC", "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_watch"})},
            {"Authorization": "Bearer token_alice_watch", "x-tenant-id": "t_alpha", "x-user-id": "u_alice", "x-dp-instance-id": "dp_bob_phone"},
            403,
            "Header x-dp-instance-id does not match authenticated binding"
        ),
        (
            "1.3 Header x-user-id spoofing (watch claims Bob)",
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "watch", "capture_timezone": "UTC", "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_watch"})},
            {"Authorization": "Bearer token_alice_watch", "x-tenant-id": "t_alpha", "x-user-id": "u_bob", "x-dp-instance-id": "dp_alice_watch"},
            403,
            "Header x-user-id does not match authenticated binding"
        ),
        (
            "1.4 Header x-tenant-id spoofing (watch claims Beta)",
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "watch", "capture_timezone": "UTC", "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_watch"})},
            {"Authorization": "Bearer token_alice_watch", "x-tenant-id": "t_beta", "x-user-id": "u_alice", "x-dp-instance-id": "dp_alice_watch"},
            403,
            "Header x-tenant-id does not match authenticated binding"
        ),
        (
            "1.5 URL path tenant breach (watch calls Beta path with Alpha headers)",
            "/tenants/t_beta/vaults/v_beta_1/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "watch", "capture_timezone": "UTC", "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_watch"})},
            {"Authorization": "Bearer token_alice_watch", "x-tenant-id": "t_alpha", "x-user-id": "u_alice", "x-dp-instance-id": "dp_alice_watch"},
            403,
            "Access denied: micro-isolation breach"
        ),
        (
            "1.6 URL path vault breach (watch calls Alpha Vault 2 path with Vault 1 header)",
            "/tenants/t_alpha/vaults/v_alpha_2/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "watch", "capture_timezone": "UTC", "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_watch"})},
            {"Authorization": "Bearer token_alice_watch", "x-tenant-id": "t_alpha", "x-vault-id": "v_alpha_1", "x-user-id": "u_alice", "x-dp-instance-id": "dp_alice_watch"},
            403,
            "Access denied: micro-isolation breach"
        ),
        (
            "1.7 capture_metadata.dp_instance_id spoofing (claims phone)",
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_timezone": "UTC", "capture_metadata": json.dumps({"dp_instance_id": "dp_alice_phone"})},
            {"Authorization": "Bearer token_alice_watch", "x-tenant-id": "t_alpha", "x-user-id": "u_alice"},
            403,
            "capture_metadata device ID does not match authenticated binding"
        ),
        (
            "1.8 capture_metadata.device_id alias spoofing (claims phone via device_id)",
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_timezone": "UTC", "capture_metadata": json.dumps({"device_id": "dp_alice_phone"})},
            {"Authorization": "Bearer token_alice_watch", "x-tenant-id": "t_alpha", "x-user-id": "u_alice"},
            403,
            "capture_metadata device ID does not match authenticated binding"
        ),
        (
            "1.9 capture_metadata cross-tenant device spoofing (claims Bob phone)",
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_timezone": "UTC", "capture_metadata": json.dumps({"dp_instance_id": "dp_bob_phone"})},
            {"Authorization": "Bearer token_alice_watch", "x-tenant-id": "t_alpha", "x-user-id": "u_alice"},
            403,
            "capture_metadata device ID does not match authenticated binding"
        ),
        (
            "1.10 Legitimate non-primary token without headers (evaluates against bound_dp -> 409 NOT_PRIMARY)",
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            {"started_at": now_iso, "format": "aac", "source": "watch", "capture_timezone": "UTC", "capture_metadata": json.dumps({"segment_index": 0})},
            {"Authorization": "Bearer token_alice_watch"},
            409,
            "recording.upload_rejected_not_primary"
        ),
    ]

    for label, path, data, headers, expected_code, expected_detail in cases:
        res = client.post(
            path,
            data=data,
            files={"file": ("chunk.aac", stream, "audio/aac")},
            headers=headers
        )
        body = res.text
        code_match = (res.status_code == expected_code)
        detail_match = (expected_detail in body)
        if code_match and detail_match:
            passed += 1
            print(f"[PASS] {label}: {res.status_code}")
        else:
            failed += 1
            msg = f"{label} -> Expected ({expected_code}, '{expected_detail}'), Got ({res.status_code}, '{body[:80]}')"
            failures.append(msg)
            print(f"[FAIL] {msg}")

    return passed, failed, failures


def run_vector_2_tests(client: TestClient, settings: Settings, stream: bytes, now_iso: str) -> Tuple[int, int, List[str], List[Dict[str, Any]]]:
    print("\n" + "=" * 80)
    print("VECTOR 2: ADVERSARIAL FUZZING OF segment_index")
    print("=" * 80)

    headers = {
        "Authorization": f"Bearer {settings.api_token}",
        "x-tenant-id": "t_alpha",
        "x-vault-id": "v_alpha_1",
        "x-user-id": "u_alice",
        "x-dp-instance-id": "dp_alice_phone"
    }

    test_matrix = [
        # (category, label, raw_json_payload, expected_status)
        ("null", "null literal", '{"dp_instance_id": "dp_alice_phone", "segment_index": null}', 422),
        ("negative", "int -1", '{"dp_instance_id": "dp_alice_phone", "segment_index": -1}', 422),
        ("negative", "int -99", '{"dp_instance_id": "dp_alice_phone", "segment_index": -99}', 422),
        ("negative", "string \"-1\"", '{"dp_instance_id": "dp_alice_phone", "segment_index": "-1"}', 422),
        ("negative", "float -0.5", '{"dp_instance_id": "dp_alice_phone", "segment_index": -0.5}', 422),
        ("negative", "float -1.5", '{"dp_instance_id": "dp_alice_phone", "segment_index": -1.5}', 422),
        ("float", "float 12.34", '{"dp_instance_id": "dp_alice_phone", "segment_index": 12.34}', 422),
        ("float", "float 0.1", '{"dp_instance_id": "dp_alice_phone", "segment_index": 0.1}', 422),
        ("float", "float 0.9", '{"dp_instance_id": "dp_alice_phone", "segment_index": 0.9}', 422),
        ("float", "float 3.14159", '{"dp_instance_id": "dp_alice_phone", "segment_index": 3.14159}', 422),
        ("float", "scientific 1e2", '{"dp_instance_id": "dp_alice_phone", "segment_index": 1e2}', 422),
        ("special_float", "Infinity", '{"dp_instance_id": "dp_alice_phone", "segment_index": Infinity}', 422),
        ("special_float", "-Infinity", '{"dp_instance_id": "dp_alice_phone", "segment_index": -Infinity}', 422),
        ("special_float", "NaN", '{"dp_instance_id": "dp_alice_phone", "segment_index": NaN}', 422),
        ("special_float", "overflow float 1e1000", '{"dp_instance_id": "dp_alice_phone", "segment_index": 1e1000}', 422),
        ("special_string", "string \"NaN\"", '{"dp_instance_id": "dp_alice_phone", "segment_index": "NaN"}', 422),
        ("special_string", "string \"Infinity\"", '{"dp_instance_id": "dp_alice_phone", "segment_index": "Infinity"}', 422),
        ("special_string", "string \"-Infinity\"", '{"dp_instance_id": "dp_alice_phone", "segment_index": "-Infinity"}', 422),
        ("special_string", "empty string \"\"", '{"dp_instance_id": "dp_alice_phone", "segment_index": ""}', 422),
        ("special_string", "whitespace \"   \"", '{"dp_instance_id": "dp_alice_phone", "segment_index": "   "}', 422),
        ("special_string", "float string \"12.34\"", '{"dp_instance_id": "dp_alice_phone", "segment_index": "12.34"}', 422),
        ("special_string", "hex string \"0x10\"", '{"dp_instance_id": "dp_alice_phone", "segment_index": "0x10"}', 422),
        ("special_string", "text \"abc\"", '{"dp_instance_id": "dp_alice_phone", "segment_index": "abc"}', 422),
        ("boolean", "bool true", '{"dp_instance_id": "dp_alice_phone", "segment_index": true}', 422),
        ("boolean", "bool false", '{"dp_instance_id": "dp_alice_phone", "segment_index": false}', 422),
        ("large_int", "huge int 10**30", '{"dp_instance_id": "dp_alice_phone", "segment_index": ' + str(10**30) + '}', 422),
        ("large_int", "int 2**63", '{"dp_instance_id": "dp_alice_phone", "segment_index": ' + str(2**63) + '}', 422),
        ("complex_type", "list [1, 2]", '{"dp_instance_id": "dp_alice_phone", "segment_index": [1, 2]}', 422),
        ("complex_type", "object {\"idx\": 1}", '{"dp_instance_id": "dp_alice_phone", "segment_index": {"idx": 1}}', 422),
        ("invalid_json", "malformed json", '{"dp_instance_id": "dp_alice_phone", broken}', 422),
        ("invalid_json", "json array root", '[{"segment_index": 0}]', 422),
        ("omitted", "omitted segment_index (default 0)", '{"dp_instance_id": "dp_alice_phone", "live_session_id": "sess_omit_harness"}', 201),
        ("valid_int", "int 0", '{"dp_instance_id": "dp_alice_phone", "live_session_id": "sess_v0_harness", "segment_index": 0}', 201),
        ("valid_int", "int 1", '{"dp_instance_id": "dp_alice_phone", "live_session_id": "sess_v1_harness", "segment_index": 1}', 201),
        ("valid_int", "int 42", '{"dp_instance_id": "dp_alice_phone", "live_session_id": "sess_v42_harness", "segment_index": 42}', 201),
        ("valid_int", "string \"42\"", '{"dp_instance_id": "dp_alice_phone", "live_session_id": "sess_vs42_harness", "segment_index": "42"}', 201),
    ]

    passed = 0
    failed = 0
    failures = []
    matrix_records = []

    for cat, label, payload, exp in test_matrix:
        res = client.post(
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            data={"started_at": now_iso, "format": "aac", "source": "phone", "capture_timezone": "UTC", "capture_metadata": payload},
            files={"file": ("chunk.aac", stream, "audio/aac")},
            headers=headers
        )
        verdict = "PASS" if res.status_code == exp else "FAIL"
        body_snippet = res.text.strip().replace("\n", " ")[:60]
        if verdict == "PASS":
            passed += 1
            print(f"[PASS] {cat.upper()}: {label} -> {res.status_code}")
        else:
            failed += 1
            msg = f"{cat.upper()}: {label} -> Expected HTTP {exp}, got HTTP {res.status_code}: {body_snippet}"
            failures.append(msg)
            print(f"[FAIL] {msg}")

        matrix_records.append({
            "category": cat,
            "label": label,
            "expected": exp,
            "actual": res.status_code,
            "verdict": verdict,
            "detail": body_snippet
        })

    return passed, failed, failures, matrix_records


def main():
    with tempfile.TemporaryDirectory() as td:
        settings = Settings(
            data_dir=Path(td),
            api_token="owner-admin-token-at-least-24-chars-long",
            run_worker=False
        )
        app = create_app(settings)
        db = app.state.db
        with db.connect(immediate=True) as conn:
            conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('t_alpha', 'Alpha Corp', 'alpha'), ('t_beta', 'Beta Inc', 'beta')")
            conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('v_alpha_1', 't_alpha', 'Vault Alpha 1', 1), ('v_alpha_2', 't_alpha', 'Vault Alpha 2', 0), ('v_beta_1', 't_beta', 'Vault Beta 1', 1)")
            conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('u_alice', 't_alpha', 'alice@alpha.com', 'Alice'), ('u_bob', 't_beta', 'bob@beta.com', 'Bob')")
            conn.execute("""
                INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
                VALUES ('dp_alice_phone', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Phone', 'phone', 1),
                       ('dp_alice_watch', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Watch', 'watch', 0),
                       ('dp_bob_phone', 't_beta', 'v_beta_1', 'u_bob', 'Bob Phone', 'phone', 1)
            """)
            conn.execute("""
                INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
                VALUES ('bind_alice_watch', 't_alpha', 'u_alice', 'dp_alice_watch', 'uuid_watch', 'android', 'token_alice_watch', 'bound'),
                       ('bind_bob_phone', 't_beta', 'u_bob', 'dp_bob_phone', 'uuid_bob_phone', 'android', 'token_bob_phone', 'bound')
            """)

        client = TestClient(app, raise_server_exceptions=False)
        stream = make_adts_stream(3)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        v1_passed, v1_failed, v1_failures = run_vector_1_tests(client, settings, stream, now_iso)
        v2_passed, v2_failed, v2_failures, matrix_records = run_vector_2_tests(client, settings, stream, now_iso)

        print("\n" + "=" * 80)
        print("SUMMARY RESULTS")
        print("=" * 80)
        print(f"Vector 1 (Header/Metadata Spoofing): {v1_passed} PASSED, {v1_failed} FAILED")
        print(f"Vector 2 (segment_index Fuzzing):   {v2_passed} PASSED, {v2_failed} FAILED")
        total_failed = v1_failed + v2_failed

        if total_failed > 0:
            print("\n>>> DEFINITIVE VERDICT: CHALLENGE_FAILED <<<")
            print(f"Found {total_failed} empirical violations.")
            sys.exit(1)
        else:
            print("\n>>> DEFINITIVE VERDICT: APPROVE <<<")
            sys.exit(0)


if __name__ == "__main__":
    main()
