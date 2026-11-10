#!/usr/bin/env python3
"""
scripts/challenge_m4_multitenant_memory_penetration.py

Empirical Adversarial Challenge Harness:
Milestone M4 Multi-Tenant Boundary Penetration, Anti-Enumeration,
Memory Retrieval Isolation, and FTS5/SQL Injection Fuzzing.

Author: challenger_m4_2 (teamwork_challenger)
Target: Backend Chat Streaming (F4.1), Gemini Tools & Memory Retrieval (F4.3),
        Processing Chains & Mutations Sync (F4.4), Contract C5 & C6.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple

from starlette.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database


class MultitenantMemoryPenetrationHarness:
    """Adversarial stress and penetration test suite for M4 multi-tenant boundaries."""

    def __init__(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp_dir.name)
        self.settings = Settings(
            data_dir=self.data_dir,
            api_token="admin-secret-token-at-least-32-chars-hex",
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
        """Seed isolated Tenant Alpha and Tenant Beta with devices, sessions, and secrets."""
        with self.db.connect(immediate=True) as c:
            # 1. Tenant Alpha (Confidential corporate data)
            c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_alpha', 'Tenant Alpha', 'alpha')")
            c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_alpha', 'ten_alpha', 'Alpha Confidential Vault', 1)")
            c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_alpha', 'ten_alpha', 'ceo@alpha.corp', 'CEO Alpha')")
            c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_alpha', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'Pixel 9 Alpha', 'phone', 1)")
            c.execute(
                """INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
                   VALUES ('bnd_alpha', 'ten_alpha', 'usr_alpha', 'dp_alpha', 'uuid_alpha', 'android', 'tok_alpha_jwt_secret', 'bound')"""
            )
            c.execute(
                """INSERT OR IGNORE INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at)
                   VALUES ('sess_alpha', 'ten_alpha', 'vlt_alpha', 'usr_alpha', 'dp_alpha', '2026-11-10', '2026-11-10T08:00:00Z')"""
            )
            c.execute(
                """INSERT OR IGNORE INTO sections (section_id, live_session_id, tenant_id, vault_id, section_index, title, start_ms, end_ms)
                   VALUES ('sec_alpha', 'sess_alpha', 'ten_alpha', 'vlt_alpha', 0, 'Confidential Acquisition Meeting', 0, 15000)"""
            )
            # Alpha's confidential transcript
            c.execute(
                """INSERT OR IGNORE INTO section_messages (message_id, section_id, speaker_name, content, start_ms, end_ms)
                   VALUES ('msg_alpha_secret', 'sec_alpha', 'Alice', 'Project OMEGA-99: Acquisition secret code is OMEGA-X-9988.', 100, 1200)"""
            )
            # Alpha's confidential memory event
            c.execute(
                """INSERT OR IGNORE INTO memory_events (event_id, tenant_id, vault_id, session_id, category, title, summary, start_time, end_time, keywords)
                   VALUES ('ev_alpha_secret', 'ten_alpha', 'vlt_alpha', 'sess_alpha', 'meeting', 'Top Secret Alpha Buyout', 'Board authorized $500M buyout for Project OMEGA-99', '2026-11-10T08:00:00Z', '2026-11-10T08:15:00Z', 'OMEGA-99,buyout,secret')"""
            )

            # 2. Tenant Beta (External tenant / adversary)
            c.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES ('ten_beta', 'Tenant Beta', 'beta')")
            c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_beta', 'ten_beta', 'Beta Vault', 1)")
            c.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_beta', 'ten_beta', 'intruder@beta.org', 'User Beta')")
            c.execute("INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_beta', 'ten_beta', 'vlt_beta', 'usr_beta', 'Galaxy Beta', 'phone', 1)")
            c.execute(
                """INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status)
                   VALUES ('bnd_beta', 'ten_beta', 'usr_beta', 'dp_beta', 'uuid_beta', 'android', 'tok_beta_jwt_secret', 'bound')"""
            )
            c.execute(
                """INSERT OR IGNORE INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at)
                   VALUES ('sess_beta', 'ten_beta', 'vlt_beta', 'usr_beta', 'dp_beta', '2026-11-10', '2026-11-10T09:00:00Z')"""
            )

        self.headers_alpha = {
            "Authorization": "Bearer tok_alpha_jwt_secret",
            "x-tenant-id": "ten_alpha",
            "x-vault-id": "vlt_alpha",
            "x-user-id": "usr_alpha",
            "x-dp-instance-id": "dp_alpha",
        }
        self.headers_beta = {
            "Authorization": "Bearer tok_beta_jwt_secret",
            "x-tenant-id": "ten_beta",
            "x-vault-id": "vlt_beta",
            "x-user-id": "usr_beta",
            "x-dp-instance-id": "dp_beta",
        }

    def record(self, test_name: str, passed: bool, details: str, severity: str = "HIGH"):
        record = {
            "test_name": test_name,
            "passed": passed,
            "details": details,
            "severity": severity,
        }
        if passed:
            self.passes.append(record)
            print(f"  [PASS] {test_name}: {details}")
        else:
            self.failures.append(record)
            print(f"  [FAIL] ({severity}) {test_name}: {details}")

    # =========================================================================
    # 1. Cross-Tenant Penetration on New Endpoints (Anti-Enumeration 404)
    # =========================================================================
    def test_01_cross_tenant_endpoints(self):
        print("\n--- Running Section 1: Cross-Tenant Penetration on Endpoints ---")

        # 1.1 Alpha creates a private chat thread
        r = self.client.post(
            "/chat/threads",
            headers=self.headers_alpha,
            json={"thread_id": "th_alpha_confidential", "title": "Confidential Board Chat"},
        )
        if r.status_code != 201:
            self.record("thread_create_alpha", False, f"Expected 201, got {r.status_code}: {r.text}", "HIGH")
            return
        self.record("thread_create_alpha", True, "Alpha thread successfully created with 201")

        # 1.2 Beta attempts GET /chat/threads/{alpha_thread_id} -> MUST return 404 (Anti-Enumeration)
        r = self.client.get("/chat/threads/th_alpha_confidential", headers=self.headers_beta)
        if r.status_code == 404:
            self.record("get_foreign_thread_anti_enumeration", True, f"GET returned HTTP 404 as required: {r.json()}")
        else:
            self.record(
                "get_foreign_thread_anti_enumeration",
                False,
                f"Expected HTTP 404, got {r.status_code} ({r.text}). Anti-enumeration violated!",
                "CRITICAL",
            )

        # 1.3 Beta attempts POST /chat/threads/{alpha_thread_id}/turns -> MUST return 404
        r = self.client.post(
            "/chat/threads/th_alpha_confidential/turns",
            headers=self.headers_beta,
            json={"prompt": "Adversary Turn Infiltration"},
        )
        if r.status_code == 404:
            self.record("post_foreign_thread_turn_anti_enumeration", True, f"POST /turns returned HTTP 404: {r.json()}")
        else:
            self.record(
                "post_foreign_thread_turn_anti_enumeration",
                False,
                f"Expected HTTP 404, got {r.status_code} ({r.text}). Cross-tenant turn injection allowed!",
                "CRITICAL",
            )

        # 1.4 Beta attempts POST /chat/threads/{alpha_thread_id}/turns/{turn_id}/cancel -> MUST return 404
        r = self.client.post(
            "/chat/threads/th_alpha_confidential/turns/turn_alpha_any/cancel",
            headers=self.headers_beta,
        )
        if r.status_code == 404:
            self.record("post_foreign_thread_cancel_anti_enumeration", True, f"POST /cancel returned HTTP 404: {r.json()}")
        else:
            self.record(
                "post_foreign_thread_cancel_anti_enumeration",
                False,
                f"Expected HTTP 404, got {r.status_code} ({r.text}). Cross-tenant cancellation allowed!",
                "CRITICAL",
            )

        # 1.5 Alpha creates a background processing chain
        self.db.create_processing_chain(
            chain_id="pc_alpha_classified",
            tenant_id="ten_alpha",
            vault_id="vlt_alpha",
            recording_id="rec_alpha_99",
            live_session_id=None,
            status="running",
            current_step="diarizing",
            progress_pct=45.0,
        )

        # Beta attempts GET /processing-chains/{alpha_chain_id} -> MUST return 404
        r = self.client.get("/processing-chains/pc_alpha_classified", headers=self.headers_beta)
        if r.status_code == 404:
            self.record("get_foreign_processing_chain_anti_enumeration", True, f"GET /processing-chains/id returned HTTP 404: {r.json()}")
        else:
            self.record(
                "get_foreign_processing_chain_anti_enumeration",
                False,
                f"Expected HTTP 404, got {r.status_code} ({r.text}). Anti-enumeration violated on processing chain!",
                "CRITICAL",
            )

        # 1.6 Beta attempts cross-vault query parameters on GET /processing-chains
        r = self.client.get(
            "/processing-chains?session_id=sess_alpha&recording_id=rec_alpha_99&status=all",
            headers=self.headers_beta,
        )
        if r.status_code == 200:
            chains = r.json().get("chains", [])
            if len(chains) == 0:
                self.record("get_processing_chains_query_param_isolation", True, "Zero foreign chains leaked via query params")
            else:
                self.record("get_processing_chains_query_param_isolation", False, f"Foreign chains leaked: {chains}", "CRITICAL")
        else:
            self.record("get_processing_chains_query_param_isolation", False, f"Unexpected status {r.status_code}: {r.text}", "HIGH")

        # 1.7 Beta attempts cross-vault header spoofing x-vault-id: vlt_alpha with tok_beta
        spoofed_headers = dict(self.headers_beta)
        spoofed_headers["x-vault-id"] = "vlt_alpha"
        r = self.client.get("/processing-chains", headers=spoofed_headers)
        if r.status_code in (403, 404):
            self.record("spoofed_vault_header_rejected", True, f"Spoofed x-vault-id was rejected with HTTP {r.status_code}")
        else:
            self.record("spoofed_vault_header_rejected", False, f"Spoofed header succeeded with HTTP {r.status_code}: {r.text}", "CRITICAL")

        # 1.8 Beta attempts cross-vault header spoofing on /recording-mutations
        r = self.client.get("/recording-mutations", headers=spoofed_headers)
        if r.status_code in (403, 404):
            self.record("spoofed_vault_mutations_header_rejected", True, f"Spoofed x-vault-id rejected on mutations with HTTP {r.status_code}")
        else:
            self.record("spoofed_vault_mutations_header_rejected", False, f"Spoofed header succeeded on mutations with HTTP {r.status_code}: {r.text}", "CRITICAL")

    # =========================================================================
    # 2. Memory Retrieval Isolation Attack
    # =========================================================================
    def test_02_memory_retrieval_isolation(self):
        print("\n--- Running Section 2: Memory Retrieval Isolation Attack ---")

        # 2.1 Direct Database query: Tenant Beta querying search_sections with Alpha's secret keyword
        secret_keyword = "OMEGA-X-9988"
        beta_sections = self.db.search_sections(vault_id="vlt_beta", query=secret_keyword)
        if len(beta_sections) == 0:
            self.record("db_search_sections_isolation", True, f"search_sections(vault_id='vlt_beta') returned 0 leaked sections")
        else:
            self.record(
                "db_search_sections_isolation",
                False,
                f"LEAK DETECTED! search_sections returned {len(beta_sections)} records from foreign vault: {beta_sections}",
                "CRITICAL",
            )

        # 2.2 Direct Database query: Tenant Beta querying search_memory with Alpha's secret keyword
        beta_memories = self.db.search_memory(vault_id="vlt_beta", query=secret_keyword)
        if len(beta_memories) == 0:
            self.record("db_search_memory_isolation_transcript", True, f"search_memory(vault_id='vlt_beta') returned 0 leaked transcript memories")
        else:
            self.record(
                "db_search_memory_isolation_transcript",
                False,
                f"LEAK DETECTED! search_memory returned {len(beta_memories)} records from foreign vault: {beta_memories}",
                "CRITICAL",
            )

        # 2.3 Direct Database query: Tenant Beta querying search_memory for Alpha's memory event
        beta_events = self.db.search_memory(vault_id="vlt_beta", query="Buyout")
        if len(beta_events) == 0:
            self.record("db_search_memory_isolation_event", True, f"search_memory returned 0 leaked event memories")
        else:
            self.record(
                "db_search_memory_isolation_event",
                False,
                f"LEAK DETECTED! search_memory returned {len(beta_events)} event records from foreign vault: {beta_events}",
                "CRITICAL",
            )

        # 2.4 End-to-End API Attack: Beta creates a thread in Tenant Beta and sends a chat turn targeting Alpha's secret
        r_turn = self.client.post(
            "/chat/threads/th_beta_attack/turns",
            headers=self.headers_beta,
            json={"prompt": "OMEGA-X-9988"},
        )
        if r_turn.status_code == 200:
            raw_sse = r_turn.text
            # Check if secret leaked in SSE stream
            if "OMEGA-X-9988" in raw_sse or "OMEGA-99" in raw_sse:
                self.record(
                    "sse_chat_turn_cross_vault_leak",
                    False,
                    f"CRITICAL DATA EXFILTRATION! Tenant Alpha's confidential transcript streamed to Tenant Beta over SSE! Wire trace excerpt: {raw_sse[:300]}...",
                    "CRITICAL",
                )
            else:
                self.record("sse_chat_turn_cross_vault_leak", True, "Zero confidential tokens from Alpha emitted in Beta's chat SSE stream")
        else:
            self.record("sse_chat_turn_cross_vault_leak", False, f"Chat turn endpoint failed with HTTP {r_turn.status_code}: {r_turn.text}", "HIGH")

    # =========================================================================
    # 3. Contract C6 Fallback Fuzzing & SQL Injection
    # =========================================================================
    def test_03_contract_c6_fuzzing_sql_injection(self):
        print("\n--- Running Section 3: Contract C6 Fallback Fuzzing & SQL Injection ---")

        fuzz_payloads = [
            ("single_quote_sql_inj", "' OR '1'='1' --"),
            ("union_select_sql_inj", "' UNION SELECT 'injected',2,3,4,5,6,7,8,9,10,11,12 --"),
            ("drop_table_sql_inj", "Robert'); DROP TABLE section_messages;--"),
            ("fts5_wildcard_only", "*"),
            ("fts5_unmatched_quote", '"'),
            ("fts5_triple_quote", '"""'),
            ("fts5_empty_string", ""),
            ("fts5_whitespace_only", "   "),
            ("fts5_syntax_and_not", "AND NOT"),
            ("fts5_syntax_or", "OR"),
            ("fts5_syntax_not", "NOT"),
            ("fts5_syntax_colon_tilde", ": ~ ^"),
            ("fts5_syntax_parentheses", "()"),
            ("fts5_near_operator", "NEAR(acquisition, buyout)"),
            ("buffer_overflow_10k", "A" * 10000),
            ("unicode_control_chars", "\x00\x08\x0b\x0c\x1b[31m%s%n%d"),
            ("speaker_resolve_nonexistent", "UnknownSpeakerThatNeverExisted#9999"),
        ]

        # 3.1 Test direct db.search_sections with all fuzz payloads
        for tag, payload in fuzz_payloads:
            try:
                res = self.db.search_sections(vault_id="vlt_alpha", query=payload)
                if isinstance(res, list):
                    self.record(f"fuzz_search_sections_{tag}", True, f"Handled gracefully, returned {len(res)} results")
                else:
                    self.record(f"fuzz_search_sections_{tag}", False, f"Returned non-list type: {type(res)}", "HIGH")
            except sqlite3.OperationalError as oe:
                self.record(
                    f"fuzz_search_sections_{tag}",
                    False,
                    f"CRASH! Unhandled sqlite3.OperationalError in search_sections: {oe}",
                    "HIGH",
                )
            except Exception as e:
                self.record(
                    f"fuzz_search_sections_{tag}",
                    False,
                    f"CRASH! Unhandled {type(e).__name__} in search_sections: {e}",
                    "HIGH",
                )

        # 3.2 Test direct db.search_memory with all fuzz payloads
        for tag, payload in fuzz_payloads:
            try:
                res = self.db.search_memory(vault_id="vlt_alpha", query=payload)
                if isinstance(res, list):
                    self.record(f"fuzz_search_memory_{tag}", True, f"Handled gracefully, returned {len(res)} results")
                else:
                    self.record(f"fuzz_search_memory_{tag}", False, f"Returned non-list type: {type(res)}", "HIGH")
            except sqlite3.OperationalError as oe:
                self.record(
                    f"fuzz_search_memory_{tag}",
                    False,
                    f"CRASH! Unhandled sqlite3.OperationalError in search_memory: {oe}",
                    "HIGH",
                )
            except Exception as e:
                self.record(
                    f"fuzz_search_memory_{tag}",
                    False,
                    f"CRASH! Unhandled {type(e).__name__} in search_memory: {e}",
                    "HIGH",
                )

        # 3.3 Test API Chat Turn with critical fuzz payloads (wildcard '*', unmatched quote '"', and SQL injection)
        critical_api_payloads = [
            ("api_wildcard", "*"),
            ("api_unmatched_quote", '"'),
            ("api_sql_injection", "' OR '1'='1' --"),
            ("api_and_not", "AND NOT"),
        ]

        for tag, payload in critical_api_payloads:
            r = self.client.post(
                "/chat/threads/th_alpha_fuzz/turns",
                headers=self.headers_alpha,
                json={"prompt": payload},
            )
            if r.status_code == 200:
                self.record(f"api_turn_{tag}", True, f"Handled without crashing (HTTP 200 SSE stream)")
            elif r.status_code == 500:
                self.record(
                    f"api_turn_{tag}",
                    False,
                    f"ENDPOINT CRASH! Returned HTTP 500 Internal Server Error on payload {repr(payload)}: {r.text}",
                    "CRITICAL",
                )
            else:
                self.record(
                    f"api_turn_{tag}",
                    False,
                    f"Unexpected status code {r.status_code} on payload {repr(payload)}: {r.text}",
                    "HIGH",
                )

    # =========================================================================
    # Evaluation & Verdict
    # =========================================================================
    def evaluate(self) -> Tuple[str, Dict[str, Any]]:
        total_passes = len(self.passes)
        total_failures = len(self.failures)
        critical_failures = [f for f in self.failures if f.get("severity") == "CRITICAL"]
        high_failures = [f for f in self.failures if f.get("severity") == "HIGH"]

        print("\n" + "=" * 80)
        print("MILSTONE M4 PENETRATION & BOUNDARY CHALLENGE SUMMARY")
        print("=" * 80)
        print(f"Total Checks: {total_passes + total_failures}")
        print(f"Passes:       {total_passes}")
        print(f"Failures:     {total_failures} (Critical: {len(critical_failures)}, High: {len(high_failures)})")
        print("=" * 80)

        if total_failures == 0:
            verdict = "APPROVE"
            print("\nVERDICT: [ APPROVE ] - All penetration and boundary attacks resisted.")
        else:
            verdict = "CHALLENGE_FAILED"
            print(f"\nVERDICT: [ CHALLENGE_FAILED ] - {total_failures} vulnerabilities detected!")
            print("\nFailure breakdown:")
            for idx, f in enumerate(self.failures, 1):
                print(f"  {idx}. [{f['severity']}] {f['test_name']}: {f['details']}")

        summary = {
            "verdict": verdict,
            "total_checks": total_passes + total_failures,
            "passes_count": total_passes,
            "failures_count": total_failures,
            "critical_count": len(critical_failures),
            "high_count": len(high_failures),
            "passes": self.passes,
            "failures": self.failures,
        }
        return verdict, summary


def main():
    harness = MultitenantMemoryPenetrationHarness()
    try:
        harness.test_01_cross_tenant_endpoints()
        harness.test_02_memory_retrieval_isolation()
        harness.test_03_contract_c6_fuzzing_sql_injection()
        verdict, summary = harness.evaluate()
        return 0 if verdict == "APPROVE" else 1
    finally:
        harness.cleanup()


# Pytest compatibility hooks
def test_multitenant_memory_penetration():
    harness = MultitenantMemoryPenetrationHarness()
    try:
        harness.test_01_cross_tenant_endpoints()
        harness.test_02_memory_retrieval_isolation()
        harness.test_03_contract_c6_fuzzing_sql_injection()
        verdict, summary = harness.evaluate()
        assert verdict == "APPROVE", f"Milestone M4 failed penetration challenge with {summary['failures_count']} failures: {summary['failures']}"
    finally:
        harness.cleanup()


if __name__ == "__main__":
    sys.exit(main())
