#!/usr/bin/env python3
"""
scripts/challenge_m2_iter3_extended_fuzzing.py
Empirical Challenger Test Harness for Milestone M2 Iteration 3:
Author: teamwork_challenger (challenger_m2_iter3_1)

Evaluates:
Section A: segment_index Adversarial Boundary Matrix (44 permutations):
   1. Extreme large integers (2**63 - 1, 2**63, 2**64, 10**100, -2**63, -2**63 - 1)
   2. Unicode non-ASCII numeric characters (e.g. '٢' Arabic numeral 2, '２' fullwidth 2, '²' superscript, '½', 'Ⅳ', '①', '零')
   3. Leading/trailing whitespace and tabs (e.g. "  42 \t ", "\n 100 \r ", "\t +7 \n")
   4. Whitespace-only and signs without digits ("  ", "\t\n", "+", "-", "--1", "++1", "+-1", "+ 1", "- 1")
   5. Nested lists/objects in JSON ([1, [2, 3]], {"a": {"b": 1}}, [], {}, [10])
   6. Null values and missing keys (null literal, "null" string, missing key, empty object)
Section B: Multipart Payload Ingestion Boundary Matrix (20 permutations):
   1. Missing required multipart fields (started_at, format, source, file, capture_metadata) -> HTTP 422
   2. Invalid audio formats (mp3, wav, flac, ogg) -> HTTP 422
   3. Invalid capture sources (satellite, bluetooth_speaker, radio) -> HTTP 422
   4. Malformed capture_metadata (unclosed JSON, JSON primitives instead of objects) -> HTTP 422
   5. Invalid timestamps and extreme clock drifts -> HTTP 409 (LeaseRejectionException)
   6. Invalid audio stream payloads (zero-byte file, non-ADTS garbage bytes) -> HTTP 400 (AudioBitstreamException)
Section C: Invariant & Zero-500 Verification:
   Ensures that across 100% of adversarial and fuzzing permutations, ZERO unhandled
   HTTP 500 Internal Server Errors are triggered.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi.testclient import TestClient
from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from tests.test_tenancy_m2 import make_adts_stream


def setup_test_environment(temp_dir: str):
    settings = Settings(
        data_dir=Path(temp_dir),
        api_token="owner-admin-token-at-least-24-chars-long",
        run_worker=False,
    )
    app = create_app(settings)
    db: Database = app.state.db
    with db.connect(immediate=True) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tenants (tenant_id, name, slug) "
            "VALUES ('t_alpha', 'Alpha Corp', 'alpha'), ('t_beta', 'Beta Inc', 'beta')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) "
            "VALUES ('v_alpha_1', 't_alpha', 'Vault Alpha 1', 1), ('v_beta_1', 't_beta', 'Vault Beta 1', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) "
            "VALUES ('u_alice', 't_alpha', 'alice@alpha.com', 'Alice'), ('u_bob', 't_beta', 'bob@beta.com', 'Bob')"
        )
        conn.execute("""
            INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary)
            VALUES ('dp_alice_phone', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Phone', 'phone', 1),
                   ('dp_alice_watch', 't_alpha', 'v_alpha_1', 'u_alice', 'Alice Watch', 'watch', 0),
                   ('dp_bob_phone', 't_beta', 'v_beta_1', 'u_bob', 'Bob Phone', 'phone', 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
            VALUES ('bind_alice_phone', 't_alpha', 'u_alice', 'dp_alice_phone', 'uuid_phone', 'android', 'token_alice_phone', 'bound'),
                   ('bind_alice_watch', 't_alpha', 'u_alice', 'dp_alice_watch', 'uuid_watch', 'android', 'token_alice_watch', 'bound'),
                   ('bind_bob_phone', 't_beta', 'u_bob', 'dp_bob_phone', 'uuid_bob_phone', 'android', 'token_bob_phone', 'bound')
        """)

    client = TestClient(app, raise_server_exceptions=False)
    return settings, client, db


def run_section_a_segment_index_stress(client: TestClient, settings: Settings, stream: bytes, now_iso: str):
    print("=" * 80)
    print("SECTION A: segment_index ADVERSARIAL & BOUNDARY MATRIX")
    print("=" * 80)

    headers = {
        "Authorization": f"Bearer {settings.api_token}",
        "x-tenant-id": "t_alpha",
        "x-vault-id": "v_alpha_1",
        "x-user-id": "u_alice",
        "x-dp-instance-id": "dp_alice_phone",
    }

    test_cases: List[Tuple[str, str, Any, int, str]] = [
        # 1. Extreme large integers
        ("1.1 Extreme Int", "2**63 - 1 (maximum valid signed 64-bit int)", 2**63 - 1, 201, "boundary_valid"),
        ("1.2 Extreme Int", "2**63 (overflow SQLite signed 64-bit)", 2**63, 422, "overflow_rejected"),
        ("1.3 Extreme Int", "2**64 (overflow unsigned 64-bit)", 2**64, 422, "overflow_rejected"),
        ("1.4 Extreme Int", "10**100 (googol overflow)", 10**100, 422, "overflow_rejected"),
        ("1.5 Extreme Int", "-2**63 (negative signed 64-bit min)", -(2**63), 422, "negative_rejected"),
        ("1.6 Extreme Int", "-(2**63 + 1) (negative underflow)", -(2**63 + 1), 422, "underflow_rejected"),
        ("1.7 Extreme Int", "string 2**63", str(2**63), 422, "overflow_string_rejected"),
        ("1.8 Extreme Int", "string 10**100", str(10**100), 422, "overflow_string_rejected"),

        # 2. Unicode numeric and non-ASCII characters
        # Note: In Python, Arabic numeral '٢' and Fullwidth '２' are unicode decimal digits ('Nd').
        # When parsed by int('٢'), Python converts to integer 2.
        # Non-decimal Unicode characters ('²', '½', 'Ⅳ', '①', '零') cannot be converted to int and must return 422.
        ("2.1 Unicode", "superscript 2 ('²')", "²", 422, "unicode_non_decimal_rejected"),
        ("2.2 Unicode", "vulgar fraction 1/2 ('½')", "½", 422, "unicode_fraction_rejected"),
        ("2.3 Unicode", "roman numeral IV ('Ⅳ')", "Ⅳ", 422, "unicode_roman_rejected"),
        ("2.4 Unicode", "circled 1 ('①')", "①", 422, "unicode_circled_rejected"),
        ("2.5 Unicode", "chinese zero ('零')", "零", 422, "unicode_cjk_rejected"),
        ("2.6 Unicode", "chinese one ('一')", "一", 422, "unicode_cjk_rejected"),
        ("2.7 Unicode", "arabic-indic numeral 2 ('٢')", "٢", 201, "unicode_nd_decimal_coerced"),
        ("2.8 Unicode", "fullwidth numeral 2 ('２')", "２", 201, "unicode_nd_decimal_coerced"),
        ("2.9 Unicode", "negative arabic-indic ('-٢')", "-٢", 422, "negative_unicode_rejected"),
        ("2.10 Unicode", "arabic-indic with decimal dot ('٢.0')", "٢.0", 422, "unicode_float_rejected"),

        # 3. Leading/trailing whitespace and tabs
        ("3.1 Whitespace", "padded spaces ('  42  ')", "  42  ", 201, "whitespace_stripped_valid"),
        ("3.2 Whitespace", "padded tabs & spaces ('  42 \\t ')", "  42 \t ", 201, "tabs_stripped_valid"),
        ("3.3 Whitespace", "padded newline & cr ('\\n 100 \\r ')", "\n 100 \r ", 201, "newlines_stripped_valid"),
        ("3.4 Whitespace", "padded with explicit positive sign ('\\t +7 \\n')", "\t +7 \n", 201, "signed_stripped_valid"),
        ("3.5 Whitespace", "padded with negative sign ('\\t -7 \\n')", "\t -7 \n", 422, "negative_stripped_rejected"),

        # 4. Whitespace-only and signs without digits
        ("4.1 Signs/Blanks", "whitespace only ('   ')", "   ", 422, "whitespace_only_rejected"),
        ("4.2 Signs/Blanks", "tabs and newlines only ('\\t\\n\\r')", "\t\n\r", 422, "whitespace_only_rejected"),
        ("4.3 Signs/Blanks", "plus sign only ('+')", "+", 422, "plus_only_rejected"),
        ("4.4 Signs/Blanks", "minus sign only ('-')", "-", 422, "minus_only_rejected"),
        ("4.5 Signs/Blanks", "double minus ('--1')", "--1", 422, "double_minus_rejected"),
        ("4.6 Signs/Blanks", "double plus ('++1')", "++1", 422, "double_plus_rejected"),
        ("4.7 Signs/Blanks", "mixed signs ('+-1')", "+-1", 422, "mixed_signs_rejected"),
        ("4.8 Signs/Blanks", "mixed signs ('-+1')", "-+1", 422, "mixed_signs_rejected"),
        ("4.9 Signs/Blanks", "space after plus ('+ 1')", "+ 1", 422, "space_in_sign_rejected"),
        ("4.10 Signs/Blanks", "space after minus ('- 1')", "- 1", 422, "space_in_sign_rejected"),

        # 5. Nested lists/objects in JSON
        ("5.1 Nested Types", "nested list ([1, [2, 3]])", [1, [2, 3]], 422, "nested_list_rejected"),
        ("5.2 Nested Types", "nested dict ({'a': {'b': 1}})", {"a": {"b": 1}}, 422, "nested_dict_rejected"),
        ("5.3 Nested Types", "empty list ([])", [], 422, "empty_list_rejected"),
        ("5.4 Nested Types", "empty dict ({})", {}, 422, "empty_dict_rejected"),
        ("5.5 Nested Types", "single item list ([10])", [10], 422, "single_item_list_rejected"),
        ("5.6 Nested Types", "dict with index key ({'segment_index': 0})", {"segment_index": 0}, 422, "dict_rejected"),

        # 6. Null values and missing keys
        ("6.1 Null/Missing", "null literal (None)", None, 422, "null_rejected"),
        ("6.2 Null/Missing", "string 'null'", "null", 422, "string_null_rejected"),
        ("6.3 Null/Missing", "string 'None'", "None", 422, "string_none_rejected"),
        ("6.4 Null/Missing", "missing segment_index key", "__OMIT__", 201, "omitted_defaults_to_zero"),
        ("6.5 Null/Missing", "empty metadata object ({})", "__EMPTY_DICT__", 201, "empty_dict_defaults_to_zero"),
    ]

    passed = 0
    failed = 0
    server_500s = 0
    results = []

    for group, label, val, expected_status, reason in test_cases:
        sess_id = f"sess_{label[:15].strip().replace(' ', '_').replace('/', '_')}_{expected_status}"
        if val == "__OMIT__":
            meta = {"dp_instance_id": "dp_alice_phone", "live_session_id": sess_id}
        elif val == "__EMPTY_DICT__":
            meta = {}
        else:
            meta = {"dp_instance_id": "dp_alice_phone", "live_session_id": sess_id, "segment_index": val}

        payload_str = json.dumps(meta)

        res = client.post(
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            data={
                "started_at": now_iso,
                "format": "aac",
                "source": "phone",
                "capture_timezone": "UTC",
                "capture_metadata": payload_str,
            },
            files={"file": ("chunk.aac", stream, "audio/aac")},
            headers=headers,
        )

        actual_status = res.status_code
        if actual_status == 500:
            server_500s += 1

        is_pass = (actual_status == expected_status)
        verdict = "PASS" if is_pass else "FAIL"
        if is_pass:
            passed += 1
            print(f"[PASS] {group} - {label}: HTTP {actual_status} (Expected {expected_status})")
        else:
            failed += 1
            body_preview = res.text.strip().replace("\n", " ")[:90]
            print(f"[FAIL] {group} - {label}: Got HTTP {actual_status}, Expected {expected_status}. Body: {body_preview}")

        results.append({
            "group": group,
            "label": label,
            "expected": expected_status,
            "actual": actual_status,
            "verdict": verdict,
            "detail": res.text[:80],
        })

    print(f"\nSection A Summary: {passed} PASSED, {failed} FAILED, {server_500s} HTTP 500s")
    return passed, failed, server_500s, results


def run_section_b_multipart_stress(client: TestClient, settings: Settings, stream: bytes, now_iso: str):
    print("\n" + "=" * 80)
    print("SECTION B: MULTIPART PAYLOAD INGESTION BOUNDARY MATRIX")
    print("=" * 80)

    headers = {
        "Authorization": f"Bearer {settings.api_token}",
        "x-tenant-id": "t_alpha",
        "x-vault-id": "v_alpha_1",
        "x-user-id": "u_alice",
        "x-dp-instance-id": "dp_alice_phone",
    }

    base_metadata = json.dumps({"dp_instance_id": "dp_alice_phone", "segment_index": 0})

    cases: List[Tuple[str, str, Dict[str, Any], Dict[str, Any], int, str]] = [
        # Missing required multipart fields -> HTTP 422 Unprocessable Content
        (
            "B.1 Missing Field", "Missing started_at",
            {"format": "aac", "source": "phone", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "missing_started_at"
        ),
        (
            "B.2 Missing Field", "Missing format",
            {"started_at": now_iso, "source": "phone", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "missing_format"
        ),
        (
            "B.3 Missing Field", "Missing source",
            {"started_at": now_iso, "format": "aac", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "missing_source"
        ),
        (
            "B.4 Missing Field", "Missing capture_metadata",
            {"started_at": now_iso, "format": "aac", "source": "phone"},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "missing_capture_metadata"
        ),
        (
            "B.5 Missing Field", "Missing file attachment",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_metadata": base_metadata},
            {},
            422, "missing_file"
        ),

        # Invalid formats -> HTTP 422 Unprocessable Content
        (
            "B.6 Invalid Format", "mp3 audio format",
            {"started_at": now_iso, "format": "mp3", "source": "phone", "capture_metadata": base_metadata},
            {"file": ("chunk.mp3", stream, "audio/mpeg")},
            422, "invalid_format_mp3"
        ),
        (
            "B.7 Invalid Format", "wav audio format",
            {"started_at": now_iso, "format": "wav", "source": "phone", "capture_metadata": base_metadata},
            {"file": ("chunk.wav", stream, "audio/wav")},
            422, "invalid_format_wav"
        ),
        (
            "B.8 Invalid Format", "empty string format",
            {"started_at": now_iso, "format": "", "source": "phone", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "empty_format"
        ),

        # Invalid sources -> HTTP 422 Unprocessable Content
        (
            "B.9 Invalid Source", "satellite source",
            {"started_at": now_iso, "format": "aac", "source": "satellite", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "invalid_source_satellite"
        ),
        (
            "B.10 Invalid Source", "empty string source",
            {"started_at": now_iso, "format": "aac", "source": "", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "empty_source"
        ),

        # Malformed capture_metadata JSON -> HTTP 422 Unprocessable Content
        (
            "B.11 JSON Malformed", "unclosed JSON object",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_metadata": '{"dp_instance_id": "dp_alice_phone"'},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "unclosed_json"
        ),
        (
            "B.12 JSON Malformed", "JSON root array",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_metadata": '[{"dp_instance_id": "dp_alice_phone"}]'},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "array_root_json"
        ),
        (
            "B.13 JSON Malformed", "JSON string primitive",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_metadata": '"simple_string"'},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "string_primitive_json"
        ),
        (
            "B.14 JSON Malformed", "JSON number primitive",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_metadata": '12345'},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "number_primitive_json"
        ),
        (
            "B.15 JSON Malformed", "JSON boolean primitive",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_metadata": 'true'},
            {"file": ("chunk.aac", stream, "audio/aac")},
            422, "boolean_primitive_json"
        ),

        # Timestamps and clock drifts -> HTTP 409 (LeaseRejectionException: recording.upload_rejected_invalid_time)
        (
            "B.16 Bad Timestamp", "non-date string ('not-a-timestamp')",
            {"started_at": "not-a-timestamp", "format": "aac", "source": "phone", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", stream, "audio/aac")},
            409, "invalid_timestamp_string"
        ),
        (
            "B.17 Bad Timestamp", "future timestamp (+2 hours, exceeding clock drift)",
            {
                "started_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                "format": "aac", "source": "phone", "capture_metadata": base_metadata
            },
            {"file": ("chunk.aac", stream, "audio/aac")},
            409, "future_clock_drift_exceeded"
        ),

        # Audio stream payloads -> HTTP 400 (AudioBitstreamException: empty_audio_file or INVALID_AUDIO_STREAM)
        (
            "B.18 Corrupted Audio", "zero-byte payload",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", b"", "audio/aac")},
            400, "zero_byte_audio_rejected"
        ),
        (
            "B.19 Corrupted Audio", "non-ADTS random garbage bytes (32KB)",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", os.urandom(32768), "audio/aac")},
            400, "non_adts_garbage_rejected"
        ),
        (
            "B.20 Corrupted Audio", "MP3 sync word header (0xFFFB) claiming AAC",
            {"started_at": now_iso, "format": "aac", "source": "phone", "capture_metadata": base_metadata},
            {"file": ("chunk.aac", b"\xff\xfb\x90\x64\x00\x00\x00", "audio/aac")},
            400, "mp3_header_rejected_as_adts"
        ),
    ]

    passed = 0
    failed = 0
    server_500s = 0
    results = []

    for group, label, form_data, file_dict, expected_status, reason in cases:
        res = client.post(
            "/tenants/t_alpha/vaults/v_alpha_1/recordings/upload",
            data=form_data,
            files=file_dict,
            headers=headers,
        )

        actual_status = res.status_code
        if actual_status == 500:
            server_500s += 1

        is_pass = (actual_status == expected_status)
        verdict = "PASS" if is_pass else "FAIL"
        if is_pass:
            passed += 1
            print(f"[PASS] {group} - {label}: HTTP {actual_status} (Expected {expected_status})")
        else:
            failed += 1
            body_preview = res.text.strip().replace("\n", " ")[:90]
            print(f"[FAIL] {group} - {label}: Got HTTP {actual_status}, Expected {expected_status}. Body: {body_preview}")

        results.append({
            "group": group,
            "label": label,
            "expected": expected_status,
            "actual": actual_status,
            "verdict": verdict,
            "detail": res.text[:80],
        })

    print(f"\nSection B Summary: {passed} PASSED, {failed} FAILED, {server_500s} HTTP 500s")
    return passed, failed, server_500s, results


def main():
    print("=" * 80)
    print("STARTING EXTENDED ADVERSARIAL FUZZING & BOUNDARY HARNESS (M2 ITERATION 3)")
    print("=" * 80)

    with tempfile.TemporaryDirectory() as td:
        settings, client, db = setup_test_environment(td)
        stream = make_adts_stream(3)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        p_a, f_a, err500_a, res_a = run_section_a_segment_index_stress(client, settings, stream, now_iso)
        p_b, f_b, err500_b, res_b = run_section_b_multipart_stress(client, settings, stream, now_iso)

        total_passed = p_a + p_b
        total_failed = f_a + f_b
        total_500s = err500_a + err500_b

        print("\n" + "=" * 80)
        print("FINAL RESULTS")
        print("=" * 80)
        print(f"Total Tests Run: {total_passed + total_failed}")
        print(f"Passed:         {total_passed}")
        print(f"Failed:         {total_failed}")
        print(f"HTTP 500s:      {total_500s}")

        if total_500s > 0:
            print("\nCRITICAL FAILURE: Detected unhandled HTTP 500 Internal Server Errors!")
            print(">>> DEFINITIVE VERDICT: CHALLENGE_FAILED <<<")
            sys.exit(2)
        elif total_failed > 0:
            print(f"\nFAILURE: {total_failed} boundary tests did not match expected behavior!")
            print(">>> DEFINITIVE VERDICT: CHALLENGE_FAILED <<<")
            sys.exit(1)
        else:
            print("\nALL BOUNDARY & ADVERSARIAL STRESS TESTS PASSED WITH ZERO HTTP 500 EXCEPTIONS!")
            print(">>> DEFINITIVE VERDICT: APPROVE <<<")
            sys.exit(0)


if __name__ == "__main__":
    main()
