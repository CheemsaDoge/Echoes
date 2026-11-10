#!/usr/bin/env python3
"""
scripts/challenge_m4_adversarial_boundary_fuzzing.py

Milestone M4 Adversarial Boundary, Injection & Multi-Tenant Fuzzing Probe.
Stress-tests commit ce145ef (0.1.3.6) across:
1. SQL Injection on path params, request bodies, JSON metadata, context, and turn IDs.
2. Unexpected Unicode, null-bytes, control characters, and extreme payload sizes.
3. Multi-Tenant isolation, spoofed headers, cross-thread/cross-vault cancel & turn hijacking.
4. FTS5 grammar edge cases, delimiter bombs, unbalanced syntax, and query operator abuse.
5. SSE Reconnection Last-Event-ID fuzzing and rapid concurrent cancellation & turn creation.

Author: challenger_m4_recheck_2 (teamwork_challenger)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import secrets
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

from starlette.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database


class BoundaryFuzzingProbe:
    def __init__(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp_dir.name)
        self.settings = Settings(
            data_dir=self.data_dir,
            api_token="super-secret-admin-token-at-least-32-chars",
            run_worker=False,
        )
        self.app = create_app(self.settings)
        self.db: Database = self.app.state.db
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.passes: List[Dict[str, Any]] = []
        self.failures: List[Dict[str, Any]] = []

        self._seed_tenants()

    def cleanup(self):
        self.tmp_dir.cleanup()

    def _seed_tenants(self):
        with self.db.connect(immediate=True) as c:
            # Tenant Alpha
            c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_alpha', 'Tenant Alpha', 'alpha')")
            c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_alpha', 'ten_alpha', 'Alpha Vault', 1)")
            c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_alpha', 'ten_alpha', 'alpha@corp.local', 'User Alpha')")
            c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_alpha', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'Alpha Phone', 'phone', 1)")
            c.execute("INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status) VALUES ('bnd_alpha', 'ten_alpha', 'usr_alpha', 'dp_alpha', 'uuid_alpha', 'android', 'tok_alpha_jwt', 'bound')")
            c.execute("INSERT OR IGNORE INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at) VALUES ('sess_alpha', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'dp_alpha', '2026-11-10', '2026-11-10T10:00:00Z')")
            c.execute("INSERT OR IGNORE INTO sections (section_id, live_session_id, tenant_id, vault_id, section_index, title, start_ms, end_ms) VALUES ('sec_alpha', 'sess_alpha', 'ten_alpha', 'vlt_alpha', 0, 'Classified Strategy', 0, 10000)")
            c.execute("INSERT OR IGNORE INTO section_messages (message_id, section_id, speaker_name, content, start_ms, end_ms) VALUES ('msg_alpha_secret', 'sec_alpha', 'Bob Ross', 'Top secret Alpha key: SECRET-ALPHA-PASSWORD-999.', 100, 2000)")

            # Tenant Beta
            c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_beta', 'Tenant Beta', 'beta')")
            c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_beta', 'ten_beta', 'Beta Vault', 1)")
            c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_beta', 'ten_beta', 'beta@corp.local', 'User Beta')")
            c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_beta', 'ten_beta', 'vlt_beta', 'usr_beta', 'Beta Phone', 'phone', 1)")
            c.execute("INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status) VALUES ('bnd_beta', 'ten_beta', 'usr_beta', 'dp_beta', 'uuid_beta', 'android', 'tok_beta_jwt', 'bound')")
            c.execute("INSERT OR IGNORE INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at) VALUES ('sess_beta', 'ten_beta', 'vlt_beta', 'usr_beta', 'dp_beta', '2026-11-10', '2026-11-10T11:00:00Z')")

        self.headers_alpha = {
            "Authorization": "Bearer tok_alpha_jwt",
            "x-tenant-id": "ten_alpha",
            "x-vault-id": "vlt_alpha",
            "x-user-id": "usr_alpha",
            "x-dp-instance-id": "dp_alpha",
        }
        self.headers_beta = {
            "Authorization": "Bearer tok_beta_jwt",
            "x-tenant-id": "ten_beta",
            "x-vault-id": "vlt_beta",
            "x-user-id": "usr_beta",
            "x-dp-instance-id": "dp_beta",
        }
        self.headers_admin = {
            "Authorization": "Bearer super-secret-admin-token-at-least-32-chars",
            "x-tenant-id": "ten_beta",
            "x-vault-id": "vlt_beta",
        }

    def record_result(self, test_name: str, passed: bool, detail: str = "", severity: str = "HIGH"):
        if passed:
            self.passes.append({"test": test_name, "detail": detail})
            print(f"  [PASS] {test_name}: {detail}")
        else:
            self.failures.append({"test": test_name, "detail": detail, "severity": severity})
            print(f"  [FAIL] {test_name} ({severity}): {detail}")

    # =========================================================================
    # Test Suite 1: SQL Injection Attacks Across All Endpoints
    # =========================================================================
    def run_sqli_suite(self):
        print("\n=== Running Suite 1: SQL Injection Attacks Across Chat Endpoints ===")

        # 1.1 Path parameter SQL injection on GET /chat/threads/{id}
        sqli_paths = [
            "' OR '1'='1",
            "'; DROP TABLE chat_threads; --",
            "th_test' UNION SELECT * FROM users --",
            "th_1'; VACUUM; --",
            "th_%27%20OR%201=1--",
            "th_\" OR \"1\"=\"1",
        ]
        for p in sqli_paths:
            res = self.client.get(f"/chat/threads/{p}", headers=self.headers_alpha)
            self.record_result(
                f"sqli_get_thread_{p[:15]}",
                res.status_code in (404, 422),
                f"Status: {res.status_code}, safe rejection without 500 error",
            )

        # 1.2 POST /chat/threads body SQL injection
        sqli_bodies = [
            {"thread_id": "th_inj'; DROP TABLE chat_threads; --", "title": "Normal"},
            {"thread_id": "th_inj_title", "title": "'; DROP TABLE users; --"},
            {"thread_id": "th_inj_or", "title": "' OR 1=1 --"},
            {"thread_id": "th_inj_meta", "title": "Safe", "metadata": {"'; DROP TABLE vaults; --": "val' OR 1=1"}},
        ]
        for b in sqli_bodies:
            res = self.client.post("/chat/threads", json=b, headers=self.headers_alpha)
            self.record_result(
                f"sqli_post_thread_{b.get('thread_id', 'body')[:15]}",
                res.status_code in (201, 200, 400, 422),
                f"Status {res.status_code}, DB preserved",
            )

        # 1.3 Path parameter SQL injection on POST /chat/threads/{id}/turns
        for p in sqli_paths:
            res = self.client.post(f"/chat/threads/{p}/turns", json={"message": "hello"}, headers=self.headers_alpha)
            self.record_result(
                f"sqli_post_turn_path_{p[:15]}",
                res.status_code in (200, 404, 422),
                f"Status {res.status_code}, handled cleanly",
            )

        # 1.4 Body SQL injection on POST /chat/threads/{id}/turns
        t_res = self.client.post("/chat/threads", json={"title": "SQLi Probe"}, headers=self.headers_alpha)
        valid_th_id = t_res.json()["thread_id"]

        turn_bodies = [
            {"client_turn_id": "turn_inj'; DROP TABLE chat_turns; --", "message": "test prompt"},
            {"client_turn_id": "turn_inj_or' OR 1=1 --", "message": "test prompt"},
            {"message": "'; DROP TABLE recording_sessions; --"},
            {"message": "' UNION SELECT * FROM users --"},
            {"message": "test", "client_context": {"client_session_id": "'; DROP TABLE dp_instances; --", "timezone": "' OR '1'='1"}},
        ]
        for tb in turn_bodies:
            res = self.client.post(f"/chat/threads/{valid_th_id}/turns", json=tb, headers=self.headers_alpha)
            self.record_result(
                f"sqli_turn_body_{str(tb.get('client_turn_id') or tb.get('message'))[:15]}",
                res.status_code == 200,
                f"Status {res.status_code}, stream returned safely without crash",
            )

        # 1.5 Cancel endpoint SQL injection on existing thread with injection in turn_id
        for p in sqli_paths:
            res = self.client.post(f"/chat/threads/{valid_th_id}/turns/{p}/cancel", headers=self.headers_alpha)
            self.record_result(
                f"sqli_cancel_turn_{p[:15]}",
                res.status_code in (200, 404, 422),
                f"Status {res.status_code}, handled without error",
            )

        # 1.6 Cancel endpoint SQL injection on NON-EXISTENT thread path (must be anti-enumeration 404)
        for p in sqli_paths:
            fake_th = f"th_never_exists_{p}"
            res = self.client.post(f"/chat/threads/{fake_th}/turns/turn_some/cancel", headers=self.headers_alpha)
            self.record_result(
                f"sqli_cancel_nonexistent_thread_{p[:15]}",
                res.status_code in (404, 422),
                f"Status {res.status_code}, rejected with anti-enumeration 404",
            )

        # Verify database integrity: ensure key tables exist and have not been dropped
        with self.db.connect() as c:
            for tbl in ["chat_threads", "chat_turns", "users", "vaults", "recording_sessions", "section_messages"]:
                cnt = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                self.record_result(
                    f"db_integrity_table_{tbl}",
                    cnt >= 0,
                    f"Table {tbl} intact with {cnt} rows",
                )

    # =========================================================================
    # Test Suite 2: Unexpected Unicode, Null Bytes & Boundary Payloads
    # =========================================================================
    def run_unicode_boundary_suite(self):
        print("\n=== Running Suite 2: Unexpected Unicode & Boundary Payloads ===")

        # 2.1 Unicode edge cases in thread creation
        t_res = self.client.post("/chat/threads", json={"title": "Unicode Suite"}, headers=self.headers_alpha)
        th_id = t_res.json()["thread_id"]

        unicode_payloads = [
            ("null_byte_in_prompt", "Hello\x00World!"),
            ("zero_width_spaces", "Zero\u200b\u200c\u200d\ufeffWidth"),
            ("rtl_override", "\u202eEXE.DLL.TXT\u202c"),
            ("emoji_storm", "🚀🎉🔥" * 100),
            ("cjk_ideographs", "这是一段关于机密收购价格的长文本测试，包含中文日文韩文한글"),
            ("zalgo_diacritics", "T̷e̵s̷t̸ ̶Z̸a̷l̶g̸o̷ ̵T̷e̵x̷t̴"),
            ("large_64kb_prompt", "A" * 65536),
            ("empty_prompt", ""),
            ("whitespace_prompt", "   \t\r\n   "),
        ]

        for name, payload in unicode_payloads:
            res = self.client.post(
                f"/chat/threads/{th_id}/turns",
                json={"message": payload},
                headers=self.headers_alpha,
            )
            self.record_result(
                f"unicode_boundary_{name}",
                res.status_code in (200, 422),
                f"Status: {res.status_code} for {name}",
            )

        # 2.2 Deeply nested / malformed metadata
        deep_metadata = {"level0": {}}
        curr = deep_metadata["level0"]
        for i in range(1, 40):
            curr[f"level{i}"] = {}
            curr = curr[f"level{i}"]
        curr["leaf"] = "deep_value"

        res = self.client.post(
            "/chat/threads",
            json={"title": "Deep Meta", "metadata": deep_metadata},
            headers=self.headers_alpha,
        )
        self.record_result(
            "deep_nested_metadata",
            res.status_code in (201, 200),
            f"Status: {res.status_code}",
        )

    # =========================================================================
    # Test Suite 3: Multi-Tenant Header Manipulation & Cross-Vault Exploitation
    # =========================================================================
    def run_multitenant_penetration_suite(self):
        print("\n=== Running Suite 3: Multi-Tenant Header Manipulation & Isolation ===")

        # Setup: Create private Alpha thread & turn
        res_a = self.client.post("/chat/threads", json={"title": "Alpha Top Secret Thread"}, headers=self.headers_alpha)
        th_alpha = res_a.json()["thread_id"]

        res_a_turn = self.client.post(
            f"/chat/threads/{th_alpha}/turns",
            json={"client_turn_id": "turn_alpha_confidential_01", "message": "My password is SECRET-ALPHA-PASSWORD-999"},
            headers=self.headers_alpha,
        )
        self.record_result("alpha_turn_created", res_a_turn.status_code == 200, f"Turn created with status {res_a_turn.status_code}")

        # 3.1 Header Spoofing by Tenant Beta
        spoofed_headers_cases = [
            ("beta_claims_alpha_vault", {"Authorization": "Bearer tok_beta_jwt", "x-tenant-id": "ten_beta", "x-vault-id": "vlt_alpha", "x-user-id": "usr_beta"}),
            ("beta_claims_alpha_tenant", {"Authorization": "Bearer tok_beta_jwt", "x-tenant-id": "ten_alpha", "x-vault-id": "vlt_beta", "x-user-id": "usr_beta"}),
            ("beta_claims_alpha_user", {"Authorization": "Bearer tok_beta_jwt", "x-tenant-id": "ten_beta", "x-vault-id": "vlt_beta", "x-user-id": "usr_alpha"}),
            ("beta_claims_alpha_dp", {"Authorization": "Bearer tok_beta_jwt", "x-tenant-id": "ten_beta", "x-vault-id": "vlt_beta", "x-user-id": "usr_beta", "x-dp-instance-id": "dp_alpha"}),
        ]
        for name, hdrs in spoofed_headers_cases:
            res = self.client.get(f"/chat/threads/{th_alpha}", headers=hdrs)
            self.record_result(
                f"spoofed_header_{name}",
                res.status_code in (403, 404),
                f"Status: {res.status_code}, access denied",
            )

        # 3.2 Anti-enumeration on thread creation collision
        res_hijack = self.client.post(
            "/chat/threads",
            json={"thread_id": th_alpha, "title": "Beta Hijack Attempt"},
            headers=self.headers_beta,
        )
        self.record_result(
            "beta_thread_collision_anti_enumeration",
            res_hijack.status_code == 404,
            f"Status {res_hijack.status_code} (anti-enumeration 404)",
        )

        # 3.3 Beta tries to read, post turn, or cancel on Alpha's thread
        res_read = self.client.get(f"/chat/threads/{th_alpha}", headers=self.headers_beta)
        self.record_result("beta_read_alpha_thread", res_read.status_code == 404, f"Status: {res_read.status_code}")

        res_turn = self.client.post(
            f"/chat/threads/{th_alpha}/turns",
            json={"message": "Adversary probe"},
            headers=self.headers_beta,
        )
        self.record_result("beta_post_turn_alpha_thread", res_turn.status_code == 404, f"Status: {res_turn.status_code}")

        res_cancel = self.client.post(
            f"/chat/threads/{th_alpha}/turns/turn_alpha_confidential_01/cancel",
            headers=self.headers_beta,
        )
        self.record_result("beta_cancel_turn_alpha_thread", res_cancel.status_code == 404, f"Status: {res_cancel.status_code}")

        # 3.4 Beta creates its own thread, but passes client_turn_id = "turn_alpha_confidential_01"
        res_beta_th = self.client.post("/chat/threads", json={"title": "Beta Valid Thread"}, headers=self.headers_beta)
        th_beta = res_beta_th.json()["thread_id"]

        res_turn_hijack = self.client.post(
            f"/chat/threads/{th_beta}/turns",
            json={"client_turn_id": "turn_alpha_confidential_01", "message": "Turn ID reuse attack"},
            headers=self.headers_beta,
        )
        self.record_result(
            "beta_turn_id_reuse_attack",
            res_turn_hijack.status_code == 404,
            f"Status {res_turn_hijack.status_code} (anti-enumeration 404)",
        )

        # 3.5 Cross-thread cancel mismatch within the same tenant
        res_a2 = self.client.post("/chat/threads", json={"title": "Alpha Thread 2"}, headers=self.headers_alpha)
        th_alpha_2 = res_a2.json()["thread_id"]
        res_cross_cancel = self.client.post(
            f"/chat/threads/{th_alpha_2}/turns/turn_alpha_confidential_01/cancel",
            headers=self.headers_alpha,
        )
        self.record_result(
            "cross_thread_cancel_mismatch_same_tenant",
            res_cross_cancel.status_code == 404,
            f"Status {res_cross_cancel.status_code} (anti-enumeration 404)",
        )

        # 3.6 Cross-vault prompt exfiltration attack:
        # User Beta asks about Bob Ross or SECRET-ALPHA-PASSWORD-999
        res_exfil = self.client.post(
            f"/chat/threads/{th_beta}/turns",
            json={"message": "What did Bob Ross say about SECRET-ALPHA-PASSWORD-999?"},
            headers=self.headers_beta,
        )
        self.record_result(
            "cross_vault_exfil_stream_status",
            res_exfil.status_code == 200,
            f"Status {res_exfil.status_code}",
        )

        # Parse SSE events from res_exfil to verify database retrieval results
        raw_text = res_exfil.text
        tool_results: list[dict] = []
        deltas: list[str] = []
        for line in raw_text.splitlines():
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if data.get("tool_name") in ("search_sections", "search_memory") and data.get("status") == "completed":
                        tool_results.append(data.get("result", {}))
                    if "delta" in data:
                        deltas.append(data["delta"])
                except Exception:
                    pass

        reconstructed_response = "".join(deltas)

        # Verify that all executed tool queries returned 0 matches from Vault Alpha
        zero_matches = all(tr.get("matches_count", 0) == 0 for tr in tool_results) if tool_results else True
        no_sections_leaked = all(len(tr.get("sections", [])) == 0 for tr in tool_results)
        no_memories_leaked = all(len(tr.get("memories", [])) == 0 for tr in tool_results)

        # Check reconstructed response: verify that the assistant generated "no matching events or conversations"
        grounded_no_match = "no matching events or conversations were recorded" in reconstructed_response

        # Check that confidential alpha secrets do NOT appear anywhere in the reconstructed response
        no_secret_in_response = ("SECRET-ALPHA-PASSWORD-999" not in reconstructed_response) and ("Bob Ross" not in reconstructed_response)

        self.record_result(
            "cross_vault_exfil_zero_matches",
            zero_matches and no_sections_leaked and no_memories_leaked and grounded_no_match and no_secret_in_response,
            f"Tool matches_count=0, empty results list, reconstructed answer confirmed zero records in timeline ({reconstructed_response[:60]}...)",
        )

    # =========================================================================
    # Test Suite 4: FTS5 Deep Grammar & Operator Fuzzing
    # =========================================================================
    def run_fts5_grammar_fuzzing_suite(self):
        print("\n=== Running Suite 4: FTS5 Grammar & Operator Fuzzing ===")

        fts5_cases = [
            ("unmatched_single_quote", "Bob's secret"),
            ("unmatched_double_quote", 'Bob"s secret'),
            ("repeated_double_quotes", '"""""""""'),
            ("naked_wildcard", "*"),
            ("chained_wildcards", "* * * * *"),
            ("unbalanced_parentheses_open", "((((secret"),
            ("unbalanced_parentheses_close", "secret))))"),
            ("bare_and", "AND"),
            ("bare_or", "OR"),
            ("bare_not", "NOT"),
            ("and_not_sequence", "AND NOT AND NOT"),
            ("near_bare", "NEAR"),
            ("near_slash", "NEAR/"),
            ("near_zero", "NEAR/0"),
            ("near_huge", "NEAR/99999999"),
            ("colon_operator", "content:secret"),
            ("caret_operator", "^prefix"),
            ("tilde_operator", "fuzzy~2"),
            ("mixed_boolean_bomb", '((secret OR AND NOT *) NEAR/2 "quote")'),
            ("fuzz_like_percent", "%" * 50),
            ("fuzz_like_underscore", "_" * 50),
        ]

        for name, query in fts5_cases:
            # 1. Test db.search_sections
            try:
                res_sec = self.db.search_sections(vault_id="vlt_alpha", query=query)
                passed_sec = isinstance(res_sec, list)
                detail_sec = f"Returned {len(res_sec)} items without OperationalError"
            except Exception as e:
                passed_sec = False
                detail_sec = f"Raised exception: {e}"
            self.record_result(f"fts5_sections_{name}", passed_sec, detail_sec)

            # 2. Test db.search_memory
            try:
                res_mem = self.db.search_memory(vault_id="vlt_alpha", query=query)
                passed_mem = isinstance(res_mem, list)
                detail_mem = f"Returned {len(res_mem)} items without OperationalError"
            except Exception as e:
                passed_mem = False
                detail_mem = f"Raised exception: {e}"
            self.record_result(f"fts5_memory_{name}", passed_mem, detail_mem)

    # =========================================================================
    # Test Suite 5: Reconnection, Last-Event-ID Fuzzing & Cancel Races
    # =========================================================================
    def run_reconnection_cancel_suite(self):
        print("\n=== Running Suite 5: Reconnection & Cancel Races ===")

        # Create thread & turn for replay tests
        t_res = self.client.post("/chat/threads", json={"title": "Reconnection Suite"}, headers=self.headers_alpha)
        th_id = t_res.json()["thread_id"]

        turn_id = "turn_reconn_test_01"
        res_init = self.client.post(
            f"/chat/threads/{th_id}/turns",
            json={"client_turn_id": turn_id, "message": "Testing reconnection stream"},
            headers=self.headers_alpha,
        )
        self.record_result("reconnect_turn_init", res_init.status_code == 200, f"Init status {res_init.status_code}")

        # 5.1 Fuzz Last-Event-ID headers
        last_id_cases = [
            ("valid_seq_zero", f"{turn_id}_000", 200),
            ("valid_seq_two", f"{turn_id}_002", 200),
            ("turn_id_without_seq", turn_id, 200),
            ("turn_id_foreign_vault", "turn_beta_999_001", 200),
            ("negative_seq", f"{turn_id}_-5", 200),
            ("huge_overflow_seq", f"{turn_id}_9999999999999999999999999999999999999", 200),
            ("not_a_number_seq", f"{turn_id}_invalid_not_number", 200),
            ("sqli_in_last_event_id", f"{turn_id}_'; DROP TABLE chat_turns; --", 200),
            ("null_byte_last_event_id", f"{turn_id}_\x00001", 200),
        ]

        for name, hdr_val, expected_code in last_id_cases:
            hdrs = dict(self.headers_alpha)
            hdrs["Last-Event-ID"] = hdr_val
            res = self.client.post(
                f"/chat/threads/{th_id}/turns",
                json={"client_turn_id": turn_id, "message": "Reconnect attempt"},
                headers=hdrs,
            )
            self.record_result(
                f"last_event_id_{name}",
                res.status_code in (200, 410),
                f"Status: {res.status_code} (handled safely)",
            )

        # 5.2 Cancel Idempotency & Concurrency Stress
        all_cancel_200 = True
        for i in range(10):
            res_c = self.client.post(f"/chat/threads/{th_id}/turns/{turn_id}/cancel", headers=self.headers_alpha)
            if res_c.status_code != 200:
                all_cancel_200 = False
        self.record_result("cancel_sequential_idempotent_10x", all_cancel_200, "10 sequential cancels returned 200 OK")

        # 30 concurrent cancel requests across ThreadPoolExecutor
        turn_concurrent = "turn_concur_cancel_test"
        self.client.post(
            f"/chat/threads/{th_id}/turns",
            json={"client_turn_id": turn_concurrent, "message": "Turn for concurrent cancel"},
            headers=self.headers_alpha,
        )

        def do_cancel():
            c = TestClient(self.app, raise_server_exceptions=False)
            return c.post(
                f"/chat/threads/{th_id}/turns/{turn_concurrent}/cancel",
                headers=self.headers_alpha,
            ).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futs = [executor.submit(do_cancel) for _ in range(30)]
            codes = [f.result() for f in concurrent.futures.as_completed(futs)]

        self.record_result(
            "cancel_30_concurrent_threads",
            all(code == 200 for code in codes),
            f"All 30 concurrent cancels returned 200 OK without errors (codes sample: {codes[:5]})",
        )

        # 5.3 High concurrency creation across multi-tenant threads (50 concurrent threads)
        def do_concurrent_thread_and_turn(idx: int):
            c = TestClient(self.app, raise_server_exceptions=False)
            is_alpha = (idx % 2 == 0)
            hdrs = self.headers_alpha if is_alpha else self.headers_beta
            th_res = c.post("/chat/threads", json={"title": f"Concurrent Thread {idx}"}, headers=hdrs)
            if th_res.status_code != 201 and th_res.status_code != 200:
                return th_res.status_code
            th_id = th_res.json()["thread_id"]
            turn_res = c.post(f"/chat/threads/{th_id}/turns", json={"message": f"Concurrent turn prompt {idx}"}, headers=hdrs)
            return turn_res.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futs = [executor.submit(do_concurrent_thread_and_turn, i) for i in range(30)]
            concur_codes = [f.result() for f in concurrent.futures.as_completed(futs)]

        all_concur_ok = all(code == 200 for code in concur_codes)
        self.record_result(
            "concurrency_30_threads_and_turns",
            all_concur_ok,
            f"All 30 concurrent thread + turn flows succeeded with 200 OK without SQLite lockup",
        )

    # =========================================================================
    # Executive Summary & Verdict
    # =========================================================================
    def report(self) -> bool:
        total = len(self.passes) + len(self.failures)
        print("\n" + "=" * 80)
        print("ADVERSARIAL BOUNDARY FUZZING PROBE SUMMARY")
        print("=" * 80)
        print(f"Total Probes Executed: {total}")
        print(f"Passes:               {len(self.passes)}")
        print(f"Failures:             {len(self.failures)}")

        if self.failures:
            print("\nFAILURES DETECTED:")
            for f in self.failures:
                print(f"  - [{f['severity']}] {f['test']}: {f['detail']}")
            print("\nVERDICT: [ CHALLENGE_FAILED ]")
            return False
        else:
            print("\nVERDICT: [ APPROVE ] - All adversarial boundary, injection & tenancy probes resisted.")
            return True


def main():
    probe = BoundaryFuzzingProbe()
    try:
        probe.run_sqli_suite()
        probe.run_unicode_boundary_suite()
        probe.run_multitenant_penetration_suite()
        probe.run_fts5_grammar_fuzzing_suite()
        probe.run_reconnection_cancel_suite()
        success = probe.report()
        sys.exit(0 if success else 1)
    finally:
        probe.cleanup()


if __name__ == "__main__":
    main()
