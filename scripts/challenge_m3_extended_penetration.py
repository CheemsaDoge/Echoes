#!/usr/bin/env python3
"""
scripts/challenge_m3_extended_penetration.py
Empirical Adversarial Challenge Harness: Extended Multi-Tenant Penetration, Race Conditions, and Header Fuzzing
Milestone M3 Iteration 2 Gate Re-verification
"""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.db import Database
from echoledger.voiceprint import generate_synthetic_embedding, pack_embedding


class ExtendedPenetrationChallenger:
    def __init__(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp_dir.name)
        self.settings = Settings(
            data_dir=self.data_dir,
            api_token="admin-secret-token-at-least-32-characters-long",
            run_worker=False,
        )
        self.app = create_app(self.settings)
        self.db: Database = self.app.state.db
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.passes: List[Dict[str, Any]] = []
        self.failures: List[Dict[str, Any]] = []

        self._seed_data()

    def cleanup(self):
        self.tmp_dir.cleanup()

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

    def _seed_data(self):
        with self.db.connect(immediate=True) as c:
            # 1. Tenants
            c.execute(
                "INSERT INTO tenants (tenant_id, name, slug) VALUES "
                "('ten_alpha', 'Alpha Corp', 'alpha'), "
                "('ten_beta', 'Beta Inc', 'beta')"
            )
            # 2. Vaults (Alpha has two vaults, Beta has one)
            c.execute(
                "INSERT INTO vaults (vault_id, tenant_id, name, is_default) VALUES "
                "('vlt_alpha_pri', 'ten_alpha', 'Alpha Primary', 1), "
                "('vlt_alpha_sec', 'ten_alpha', 'Alpha Secondary', 0), "
                "('vlt_beta_pri', 'ten_beta', 'Beta Primary', 1)"
            )
            # 3. Users (Alice in Alpha, Bob in Beta, Charlie in Beta without device binding)
            c.execute(
                "INSERT INTO users (user_id, tenant_id, email, full_name) VALUES "
                "('usr_alice', 'ten_alpha', 'alice@alpha.corp', 'Alice Connor'), "
                "('usr_bob', 'ten_beta', 'bob@beta.corp', 'Bob Builder'), "
                "('usr_charlie', 'ten_beta', 'charlie@beta.corp', 'Charlie UserOnly')"
            )
            # 4. DP Instances & Bindings
            c.execute(
                "INSERT INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES "
                "('dp_alice', 'ten_alpha', 'vlt_alpha_pri', 'usr_alice', 'Alice Phone', 'phone', 1), "
                "('dp_bob', 'ten_beta', 'vlt_beta_pri', 'usr_bob', 'Bob Phone', 'phone', 1)"
            )
            c.execute(
                "INSERT INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status) VALUES "
                "('bind_alice', 'ten_alpha', 'usr_alice', 'dp_alice', 'uuid_alice', 'android', 'token_alice', 'bound'), "
                "('bind_bob', 'ten_beta', 'usr_bob', 'dp_bob', 'uuid_bob', 'android', 'token_bob', 'bound')"
            )
            # 5. Sessions & Sections
            c.execute(
                "INSERT INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, started_at, local_day_key) VALUES "
                "('sess_alpha_1', 'ten_alpha', 'vlt_alpha_pri', 'usr_alice', 'dp_alice', '2026-11-10T02:00:00Z', '2026-11-10'), "
                "('sess_alpha_2', 'ten_alpha', 'vlt_alpha_sec', 'usr_alice', 'dp_alice', '2026-11-10T02:00:00Z', '2026-11-10'), "
                "('sess_beta_1', 'ten_beta', 'vlt_beta_pri', 'usr_bob', 'dp_bob', '2026-11-10T02:00:00Z', '2026-11-10')"
            )
            c.execute(
                "INSERT INTO sections (section_id, live_session_id, tenant_id, vault_id, section_index, start_ms, end_ms) VALUES "
                "('sec_a1', 'sess_alpha_1', 'ten_alpha', 'vlt_alpha_pri', 0, 0, 10000), "
                "('sec_a2', 'sess_alpha_2', 'ten_alpha', 'vlt_alpha_sec', 0, 0, 10000), "
                "('sec_b1', 'sess_beta_1', 'ten_beta', 'vlt_beta_pri', 0, 0, 10000)"
            )

        # Pre-seed clusters
        emb = pack_embedding(generate_synthetic_embedding("ext_test_voice"))
        self.db.create_voiceprint_cluster("cls_a_pri", "ten_alpha", "vlt_alpha_pri", embedding_blob=emb, confidence_score=0.9)
        self.db.create_voiceprint_cluster("cls_a_sec", "ten_alpha", "vlt_alpha_sec", embedding_blob=emb, confidence_score=0.9)
        self.db.create_voiceprint_cluster("cls_b_pri", "ten_beta", "vlt_beta_pri", embedding_blob=emb, confidence_score=0.85)

        # Pre-seed speaker in Alpha Primary
        self.db.create_speaker_profile(
            speaker_id="spk_alice_target",
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            user_id="usr_alice",
            name="Alice Prime",
            sample_count=2,
        )

    # =========================================================================
    # Part 1: User-Only Token (No Device Binding) Multi-Tenant Spoofing
    # =========================================================================
    def test_user_only_token_spoofing(self):
        print("\n--- TEST SUITE 1: User-Only Token Spoofing & Tenancy Enforcement ---")
        token = "usr_charlie"  # Authenticates via users table, no device binding

        # 1.1 Bob/Charlie user token requesting Alpha Primary cluster
        res = self.client.get("/clusters/cls_a_pri", headers={"Authorization": f"Bearer {token}"})
        self.record("1.1 User-only token without headers returns 404 for foreign cluster",
                    res.status_code == 404, f"Status: {res.status_code}")

        # 1.2 User token injecting x-vault-id: vlt_alpha_pri
        res = self.client.get("/clusters/cls_a_pri", headers={
            "Authorization": f"Bearer {token}",
            "x-vault-id": "vlt_alpha_pri"
        })
        self.record("1.2 User-only token with foreign x-vault-id returns 404 (anti-enumeration)",
                    res.status_code == 404, f"Status: {res.status_code}")

        # 1.3 User token injecting x-vault-id: vlt_alpha_sec
        res = self.client.get("/clusters/cls_a_sec", headers={
            "Authorization": f"Bearer {token}",
            "x-vault-id": "vlt_alpha_sec"
        })
        self.record("1.3 User-only token with foreign secondary x-vault-id returns 404",
                    res.status_code == 404, f"Status: {res.status_code}")

        # 1.4 User token injecting x-tenant-id: ten_alpha
        res = self.client.get("/clusters/cls_a_pri", headers={
            "Authorization": f"Bearer {token}",
            "x-tenant-id": "ten_alpha"
        })
        self.record("1.4 User-only token with forged x-tenant-id returns 403 Forbidden",
                    res.status_code == 403, f"Status: {res.status_code}")

        # 1.5 User token injecting x-user-id: usr_alice
        res = self.client.get("/clusters/cls_a_pri", headers={
            "Authorization": f"Bearer {token}",
            "x-user-id": "usr_alice"
        })
        self.record("1.5 User-only token with forged x-user-id returns 403 Forbidden",
                    res.status_code == 403, f"Status: {res.status_code}")

        # 1.6 User token attempting POST /speakers with Alpha cluster
        res = self.client.post("/speakers", json={"name": "Attacker", "cluster_id": "cls_a_pri"}, headers={
            "Authorization": f"Bearer {token}",
        })
        self.record("1.6 User-only token POST /speakers with foreign cluster returns 404",
                    res.status_code == 404, f"Status: {res.status_code}")

        # 1.7 User token attempting POST /speakers with foreign vault header
        res = self.client.post("/speakers", json={"name": "Attacker", "cluster_id": "cls_a_pri"}, headers={
            "Authorization": f"Bearer {token}",
            "x-vault-id": "vlt_alpha_pri",
        })
        self.record("1.7 User-only token POST /speakers with foreign x-vault-id returns 404",
                    res.status_code == 404, f"Status: {res.status_code}")

    # =========================================================================
    # Part 2: Intra-Tenant Multi-Vault Micro-Isolation
    # =========================================================================
    def test_intra_tenant_multi_vault(self):
        print("\n--- TEST SUITE 2: Intra-Tenant Multi-Vault Micro-Isolation ---")
        token_alice = "token_alice"

        # 2.1 Alice default request to cluster in secondary vault returns 404 (scoped to primary by default)
        res = self.client.get("/clusters/cls_a_sec", headers={"Authorization": f"Bearer {token_alice}"})
        self.record("2.1 Request without x-vault-id defaults to primary vault; secondary cluster returns 404",
                    res.status_code == 404, f"Status: {res.status_code}")

        # 2.2 Alice explicit request to secondary vault returns 200 (permitted within same tenant)
        res = self.client.get("/clusters/cls_a_sec", headers={
            "Authorization": f"Bearer {token_alice}",
            "x-vault-id": "vlt_alpha_sec"
        })
        self.record("2.2 Request with own tenant secondary vault returns 200 OK",
                    res.status_code == 200, f"Status: {res.status_code}")

        # 2.3 Bob cannot access Alpha secondary vault even if explicitly specified
        res = self.client.get("/clusters/cls_a_sec", headers={
            "Authorization": "Bearer token_bob",
            "x-vault-id": "vlt_alpha_sec"
        })
        self.record("2.3 Foreign tenant token with Alpha secondary vault returns 404",
                    res.status_code == 404, f"Status: {res.status_code}")

    # =========================================================================
    # Part 3: Header Fuzzing & Malformed Injections
    # =========================================================================
    def test_header_fuzzing_and_injections(self):
        print("\n--- TEST SUITE 3: Header Fuzzing & Injection Resilience ---")
        token_bob = "token_bob"

        fuzz_vault_headers = [
            ("empty_string", ""),
            ("spaces", "   "),
            ("tabs", "\t\t"),
            ("sql_or", "' OR '1'='1"),
            ("sql_union", "' UNION SELECT * FROM vaults--"),
            ("traversal", "../vlt_alpha_pri"),
            ("null_byte", "vlt_alpha_pri\x00extra"),
            ("random_nonexistent", "vlt_999999999999999999"),
        ]

        for name, hval in fuzz_vault_headers:
            try:
                res = self.client.get(
                    "/clusters",
                    headers={"Authorization": f"Bearer {token_bob}", "x-vault-id": hval},
                )
                # Either 404 Not Found (vault not found) or safe 200 scoped to default vault if empty/invalid header stripped
                # In no circumstance should it return 500 Internal Server Error or leak Alpha data
                leak = False
                if res.status_code == 200:
                    clusters = res.json().get("clusters", [])
                    leak = any(c.get("cluster_id") == "cls_a_pri" for c in clusters)
                safe = res.status_code in (400, 404, 422) or (res.status_code == 200 and not leak)
                self.record(
                    f"3.1 Header fuzzing x-vault-id [{name}]: safe handling",
                    safe and not leak,
                    f"Status {res.status_code}, Alpha cluster leaked: {leak}",
                )
            except Exception as e:
                self.record(f"3.1 Header fuzzing x-vault-id [{name}]: exception", False, str(e))

    # =========================================================================
    # Part 4: Concurrency & Idempotency Stress Testing
    # =========================================================================
    def test_concurrency_and_idempotency(self):
        print("\n--- TEST SUITE 4: Concurrency & Idempotency Stress Testing ---")
        headers_alice = {"Authorization": "Bearer token_alice"}

        # 4.1 Concurrent mark_existing on the same cluster
        cid_conc = "cls_conc_merge_01"
        self.db.create_voiceprint_cluster(
            cluster_id=cid_conc,
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            status="pending",
            occurrences_count=5,
        )

        initial_spk = self.db.get_speaker_profile("spk_alice_target", "vlt_alpha_pri")
        initial_samples = initial_spk["sample_count"]

        # Run 10 parallel mark_existing requests
        def merge_task():
            client = TestClient(self.app, raise_server_exceptions=False)
            return client.patch(
                f"/clusters/{cid_conc}",
                json={"action": "mark_existing", "speaker_id": "spk_alice_target"},
                headers=headers_alice,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(merge_task) for _ in range(10)]
            results = [f.result() for f in futures]

        all_ok = all(r.status_code == 200 for r in results)
        final_spk = self.db.get_speaker_profile("spk_alice_target", "vlt_alpha_pri")
        expected_samples = initial_samples + 5
        idempotent_samples = (final_spk["sample_count"] == expected_samples)

        self.record(
            "4.1 Concurrent mark_existing requests succeed and preserve idempotent sample_count",
            all_ok and idempotent_samples,
            f"All 200 OK: {all_ok}, expected count: {expected_samples}, actual: {final_spk['sample_count']}",
        )

        # 4.2 Dismissed cluster cannot be re-dismissed or mutated to invalid state
        cid_dismiss = "cls_conc_dismiss_01"
        self.db.create_voiceprint_cluster(
            cluster_id=cid_dismiss,
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            status="pending",
        )
        res_d1 = self.client.patch(f"/clusters/{cid_dismiss}", json={"action": "dismiss"}, headers=headers_alice)
        res_d2 = self.client.patch(f"/clusters/{cid_dismiss}", json={"action": "dismiss"}, headers=headers_alice)
        self.record(
            "4.2 Dismiss action is idempotent across multiple requests",
            res_d1.status_code == 200 and res_d2.status_code == 200 and res_d2.json().get("status") == "dismissed",
            f"d1: {res_d1.status_code}, d2: {res_d2.status_code}",
        )

    # =========================================================================
    # Part 5: Anti-Enumeration across all Speaker & Cluster Endpoints
    # =========================================================================
    def test_comprehensive_anti_enumeration(self):
        print("\n--- TEST SUITE 5: Comprehensive Anti-Enumeration Checks ---")
        token_bob = "token_bob"
        headers_bob = {"Authorization": f"Bearer {token_bob}"}

        # 5.1 GET /speakers/{speaker_id} for Alpha speaker
        res = self.client.get("/speakers/spk_alice_target", headers=headers_bob)
        self.record("5.1 GET /speakers/{foreign_speaker_id} returns 404",
                    res.status_code == 404, f"Status: {res.status_code}")

        # 5.2 GET /speakers does not leak Alpha speakers
        res = self.client.get("/speakers", headers=headers_bob)
        spk_list = res.json().get("speakers", []) if res.status_code == 200 else []
        leaks = [s for s in spk_list if s.get("name") == "Alice Prime"]
        self.record("5.2 GET /speakers does not leak foreign tenant speakers",
                    res.status_code == 200 and len(leaks) == 0,
                    f"Status: {res.status_code}, foreign speakers found: {len(leaks)}")

        # 5.3 GET /clusters does not leak Alpha clusters
        res = self.client.get("/clusters", headers=headers_bob)
        cls_list = res.json().get("clusters", []) if res.status_code == 200 else []
        cls_leaks = [c for c in cls_list if c.get("cluster_id") in ("cls_a_pri", "cls_a_sec")]
        self.record("5.3 GET /clusters does not leak foreign tenant clusters",
                    res.status_code == 200 and len(cls_leaks) == 0,
                    f"Status: {res.status_code}, foreign clusters found: {len(cls_leaks)}")

    def run_all(self) -> int:
        print("=" * 80)
        print("EXTENDED ADVERSARIAL PENETRATION CHALLENGE: MILESTONE M3 ITERATION 2")
        print("=" * 80)

        self.test_user_only_token_spoofing()
        self.test_intra_tenant_multi_vault()
        self.test_header_fuzzing_and_injections()
        self.test_concurrency_and_idempotency()
        self.test_comprehensive_anti_enumeration()

        total = len(self.passes) + len(self.failures)
        print("\n" + "=" * 80)
        print(f"EXTENDED CHALLENGE SUMMARY: {len(self.passes)}/{total} PASSED, {len(self.failures)} FAILED")
        print("=" * 80)

        if self.failures:
            print("\nFAILURES DETECTED:")
            for f in self.failures:
                print(f"  - [{f['severity']}] {f['test_name']}: {f['details']}")
            return 1
        return 0


def main():
    challenger = ExtendedPenetrationChallenger()
    try:
        code = challenger.run_all()
    finally:
        challenger.cleanup()
    sys.exit(code)


if __name__ == "__main__":
    main()
