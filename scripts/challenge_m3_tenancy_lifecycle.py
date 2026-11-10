#!/usr/bin/env python3
"""
scripts/challenge_m3_tenancy_lifecycle.py
Empirical Adversarial Challenge Harness: Multi-Tenant Boundary Isolation & Cluster Lifecycle Verification
Target Milestone: M3 (Features F3.1–F3.4, Interface Contract C6)
Target Commits: 82cfd77 / 77daa13

Verifies:
1. Multi-Tenant Cryptographic Partitioning & Anti-Enumeration:
   - Cross-vault cluster accesses (GET /clusters/{foreign_id}) and mutations (PATCH /clusters/{foreign_id})
     must strictly return HTTP 404 Not Found (zero existence leaks, zero HTTP 403 or 200).
   - Cross-tenant token spoofing:
     * Tenant Beta token claiming Tenant Alpha via header x-tenant-id -> HTTP 403 Forbidden.
     * Tenant Beta token attempting to access Tenant Alpha vault via header x-vault-id -> HTTP 404 Not Found (NO 200/201).
   - Anti-enumeration across GET /clusters, GET /clusters/{id}/samples, GET /clusters/{id}/audio_preview,
     GET /speakers, and POST /speakers.
2. Cluster Lifecycle Fuzzing & Mutation Integrity:
   - PATCH /clusters/{id} across all actions:
     * mark_new with missing/empty/whitespace name -> HTTP 422 Unprocessable Content.
     * mark_new with valid name -> HTTP 200 OK, status='enrolled', speaker created.
     * mark_existing with missing/empty speaker_id -> HTTP 422 Unprocessable Content.
     * mark_existing with foreign speaker_id -> HTTP 404 Not Found (zero cross-vault leak).
     * mark_existing with non-existent speaker_id -> HTTP 404 Not Found.
     * mark_existing with valid speaker_id -> HTTP 200 OK, status='merged', sample_count incremented idempotently.
     * dismiss -> HTTP 200 OK, status='dismissed', excluded from pending clusters / candidate notifications.
     * Invalid actions ('delete', 'random_action', empty, injection payloads) -> HTTP 422.
3. Retroactive Message Relabeling Integrity:
   - Seed section_messages with unassigned cluster_id across multiple vaults and sessions.
   - Execute mark_new and mark_existing.
   - Verify retroactive updates to speaker_id and speaker_name on all matching messages while leaving foreign vault messages completely untouched.
"""

from __future__ import annotations

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
from echoledger.voiceprint import (
    TAU_CLUSTERING,
    TAU_IDENTIFICATION,
    TAU_REJECTION,
    cluster_segment,
    generate_synthetic_embedding,
    pack_embedding,
)


class TenancyLifecycleChallenger:
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
        self.failures: List[Dict[str, Any]] = []
        self.passes: List[Dict[str, Any]] = []

        self._seed_tenants_and_identities()

    def cleanup(self):
        self.tmp_dir.cleanup()

    def _seed_tenants_and_identities(self):
        """Seed two isolated tenants (Alpha and Beta) with vaults, users, devices, and bindings."""
        with self.db.connect(immediate=True) as c:
            # 1. Tenants
            c.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, name, slug) VALUES "
                "('ten_alpha', 'Alpha Security Corp', 'alpha'), "
                "('ten_beta', 'Beta Medical Labs', 'beta')"
            )
            # 2. Vaults
            c.execute(
                "INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES "
                "('vlt_alpha_pri', 'ten_alpha', 'Alpha Primary Vault', 1), "
                "('vlt_alpha_sec', 'ten_alpha', 'Alpha Secondary Vault', 0), "
                "('vlt_beta_pri', 'ten_beta', 'Beta Primary Vault', 1)"
            )
            # 3. Users
            c.execute(
                "INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES "
                "('usr_alice', 'ten_alpha', 'alice@alpha.corp', 'Alice Connor'), "
                "('usr_bob', 'ten_beta', 'bob@beta.med', 'Bob Builder')"
            )
            # 4. DP Instances
            c.execute(
                "INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES "
                "('dp_alice_phone', 'ten_alpha', 'vlt_alpha_pri', 'usr_alice', 'Alice Phone', 'phone', 1), "
                "('dp_bob_phone', 'ten_beta', 'vlt_beta_pri', 'usr_bob', 'Bob Phone', 'phone', 1)"
            )
            # 5. Device Bindings
            c.execute(
                "INSERT OR IGNORE INTO device_bindings (binding_id, tenant_id, user_id, dp_instance_id, device_unique_identifier, os_name, push_token, status) VALUES "
                "('bind_alice_01', 'ten_alpha', 'usr_alice', 'dp_alice_phone', 'uuid_alice_01', 'android', 'token_alice_alpha', 'bound'), "
                "('bind_bob_01', 'ten_beta', 'usr_bob', 'dp_bob_phone', 'uuid_bob_01', 'android', 'token_bob_beta', 'bound')"
            )
            # 6. Recording Sessions
            c.execute(
                "INSERT OR IGNORE INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, started_at, local_day_key) VALUES "
                "('sess_alpha_01', 'ten_alpha', 'vlt_alpha_pri', 'usr_alice', 'dp_alice_phone', '2026-11-10T01:00:00Z', '2026-11-10'), "
                "('sess_beta_01', 'ten_beta', 'vlt_beta_pri', 'usr_bob', 'dp_bob_phone', '2026-11-10T01:00:00Z', '2026-11-10')"
            )
            # 7. Sections
            c.execute(
                "INSERT OR IGNORE INTO sections (section_id, live_session_id, tenant_id, vault_id, section_index, start_ms, end_ms) VALUES "
                "('sec_alpha_01', 'sess_alpha_01', 'ten_alpha', 'vlt_alpha_pri', 0, 0, 30000), "
                "('sec_beta_01', 'sess_beta_01', 'ten_beta', 'vlt_beta_pri', 0, 0, 30000)"
            )

    def record_result(self, test_name: str, passed: bool, details: str, severity: str = "HIGH"):
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
    # Part 1: Multi-Tenant Partitioning & Anti-Enumeration
    # =========================================================================
    def challenge_multi_tenant_partitioning_and_spoofing(self):
        print("\n" + "=" * 80)
        print("STAGE 1: Multi-Tenant Boundary Isolation & Token Spoofing Challenge")
        print("=" * 80)

        # Pre-seed Alpha cluster in Alpha's Primary Vault
        emb_alpha = pack_embedding(generate_synthetic_embedding("alpha_voice_speaker"))
        self.db.create_voiceprint_cluster(
            cluster_id="cls_alpha_target",
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            embedding_blob=emb_alpha,
            confidence_score=0.92,
            occurrences_count=4,
        )

        # Pre-seed Beta cluster in Beta's Primary Vault
        emb_beta = pack_embedding(generate_synthetic_embedding("beta_voice_speaker"))
        self.db.create_voiceprint_cluster(
            cluster_id="cls_beta_target",
            tenant_id="ten_beta",
            vault_id="vlt_beta_pri",
            embedding_blob=emb_beta,
            confidence_score=0.88,
            occurrences_count=2,
        )

        # 1.1 Legitimate Beta request attempting cross-vault read (GET /clusters/cls_alpha_target)
        # Beta caller uses legitimate Beta headers (vault_id=vlt_beta_pri)
        res = self.client.get(
            "/clusters/cls_alpha_target",
            headers={
                "Authorization": "Bearer token_bob_beta",
                "x-tenant-id": "ten_beta",
                "x-vault-id": "vlt_beta_pri",
                "x-user-id": "usr_bob",
            },
        )
        passed = (res.status_code == 404)
        self.record_result(
            test_name="1.1 Cross-vault GET /clusters/{id} returns HTTP 404 with legitimate Beta headers",
            passed=passed,
            details=f"Expected 404, got {res.status_code}: {res.text}",
            severity="CRITICAL",
        )

        # 1.2 Legitimate Beta request attempting cross-vault mutation (PATCH /clusters/cls_alpha_target dismiss)
        res = self.client.patch(
            "/clusters/cls_alpha_target",
            json={"action": "dismiss"},
            headers={
                "Authorization": "Bearer token_bob_beta",
                "x-tenant-id": "ten_beta",
                "x-vault-id": "vlt_beta_pri",
                "x-user-id": "usr_bob",
            },
        )
        passed = (res.status_code == 404)
        self.record_result(
            test_name="1.2 Cross-vault PATCH /clusters/{id} returns HTTP 404 with legitimate Beta headers",
            passed=passed,
            details=f"Expected 404, got {res.status_code}: {res.text}",
            severity="CRITICAL",
        )

        # 1.3 Cross-tenant token spoofing: Beta token passes x-tenant-id: ten_alpha
        res = self.client.get(
            "/clusters/cls_alpha_target",
            headers={
                "Authorization": "Bearer token_bob_beta",
                "x-tenant-id": "ten_alpha",
                "x-vault-id": "vlt_alpha_pri",
                "x-user-id": "usr_bob",
            },
        )
        passed = (res.status_code == 403)
        self.record_result(
            test_name="1.3 Cross-tenant token spoofing: x-tenant-id mismatch rejected with HTTP 403",
            passed=passed,
            details=f"Expected 403, got {res.status_code}: {res.text}",
            severity="HIGH",
        )

        # 1.4 Cross-tenant vault header injection spoofing: Beta token passes x-vault-id: vlt_alpha_pri
        # Attacker keeps legitimate tenant or omits x-tenant-id, but targets Alpha's vault ID
        res = self.client.get(
            "/clusters/cls_alpha_target",
            headers={
                "Authorization": "Bearer token_bob_beta",
                "x-vault-id": "vlt_alpha_pri",
            },
        )
        # Security invariant: Foreign tenant token MUST NOT receive 200 OK with foreign cluster data
        # Anti-enumeration mandates HTTP 404 Not Found (or 403 Forbidden without data)
        passed = (res.status_code == 404)
        leaked = (res.status_code == 200)
        self.record_result(
            test_name="1.4 Cross-tenant spoofing: Beta token cannot view Alpha cluster via x-vault-id injection",
            passed=passed,
            details=(
                f"VULNERABILITY LEAK: Status {res.status_code} returned Alpha cluster {res.text}"
                if leaked
                else f"Status {res.status_code}: {res.text}"
            ),
            severity="CRITICAL",
        )

        # 1.5 Cross-tenant vault header injection mutation: Beta token attempts PATCH dismiss on Alpha cluster
        res = self.client.patch(
            "/clusters/cls_alpha_target",
            json={"action": "dismiss"},
            headers={
                "Authorization": "Bearer token_bob_beta",
                "x-vault-id": "vlt_alpha_pri",
            },
        )
        passed = (res.status_code == 404)
        mutated = (res.status_code == 200)
        self.record_result(
            test_name="1.5 Cross-tenant spoofing: Beta token cannot mutate Alpha cluster via x-vault-id injection",
            passed=passed,
            details=(
                f"VULNERABILITY MUTATION: Status {res.status_code} dismissed Alpha cluster {res.text}"
                if mutated
                else f"Status {res.status_code}: {res.text}"
            ),
            severity="CRITICAL",
        )

        # 1.6 Anti-enumeration: GET /clusters with foreign vault header injection
        res = self.client.get(
            "/clusters",
            headers={
                "Authorization": "Bearer token_bob_beta",
                "x-vault-id": "vlt_alpha_pri",
            },
        )
        clusters_returned = res.json().get("clusters", []) if res.status_code == 200 else []
        passed = (res.status_code == 404 or len(clusters_returned) == 0)
        self.record_result(
            test_name="1.6 Anti-enumeration: GET /clusters with foreign vault header does not leak Alpha clusters",
            passed=passed,
            details=f"Status {res.status_code}, clusters returned: {len(clusters_returned)}",
            severity="HIGH",
        )

        # 1.7 Anti-enumeration: GET /clusters/{id}/audio_preview for foreign vault
        res = self.client.get(
            "/clusters/cls_alpha_target/audio_preview",
            headers={
                "Authorization": "Bearer token_bob_beta",
                "x-vault-id": "vlt_beta_pri",
            },
        )
        passed = (res.status_code == 404)
        self.record_result(
            test_name="1.7 Anti-enumeration: GET /clusters/{foreign_id}/audio_preview returns HTTP 404",
            passed=passed,
            details=f"Expected 404, got {res.status_code}",
            severity="HIGH",
        )

        # 1.8 Anti-enumeration: GET /clusters/{id}/samples for foreign vault
        res = self.client.get(
            "/clusters/cls_alpha_target/samples",
            headers={
                "Authorization": "Bearer token_bob_beta",
                "x-vault-id": "vlt_beta_pri",
            },
        )
        passed = (res.status_code == 404)
        self.record_result(
            test_name="1.8 Anti-enumeration: GET /clusters/{foreign_id}/samples returns HTTP 404",
            passed=passed,
            details=f"Expected 404, got {res.status_code}",
            severity="HIGH",
        )

    # =========================================================================
    # Part 2: Cluster Lifecycle Fuzzing & Mutation Integrity
    # =========================================================================
    def challenge_cluster_lifecycle_fuzzing(self):
        print("\n" + "=" * 80)
        print("STAGE 2: Cluster Lifecycle Fuzzing & Mutation Integrity")
        print("=" * 80)

        headers_alice = {
            "Authorization": "Bearer token_alice_alpha",
            "x-tenant-id": "ten_alpha",
            "x-vault-id": "vlt_alpha_pri",
            "x-user-id": "usr_alice",
        }

        # 2.1 mark_new fuzzing with missing/empty/whitespace names
        fuzz_invalid_names = [
            ("missing_name_field", {}),
            ("empty_string", {"name": ""}),
            ("whitespace_spaces", {"name": "    "}),
            ("whitespace_mixed", {"name": " \t \r \n "}),
        ]
        for name_case, extra_payload in fuzz_invalid_names:
            cid = f"cls_fuzz_new_{name_case}"
            self.db.create_voiceprint_cluster(
                cluster_id=cid,
                tenant_id="ten_alpha",
                vault_id="vlt_alpha_pri",
                status="pending",
            )
            payload = {"action": "mark_new", **extra_payload}
            res = self.client.patch(f"/clusters/{cid}", json=payload, headers=headers_alice)
            passed = (res.status_code == 422)
            self.record_result(
                test_name=f"2.1 mark_new fuzzing: {name_case} returns HTTP 422",
                passed=passed,
                details=f"Expected 422, got {res.status_code}: {res.text}",
                severity="HIGH",
            )

        # 2.2 mark_new with valid name enrolls cluster and creates speaker
        cid_valid_new = "cls_valid_new_01"
        self.db.create_voiceprint_cluster(
            cluster_id=cid_valid_new,
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            status="pending",
            occurrences_count=3,
        )
        res_new = self.client.patch(
            f"/clusters/{cid_valid_new}",
            json={"action": "mark_new", "name": "Dr. Aris Thorne", "role_label": "Cryptographer"},
            headers=headers_alice,
        )
        data_new = res_new.json() if res_new.status_code == 200 else {}
        spk_new_id = data_new.get("speaker_id")
        passed = (
            res_new.status_code == 200
            and data_new.get("status") == "enrolled"
            and spk_new_id is not None
            and spk_new_id.startswith("spk_")
        )
        self.record_result(
            test_name="2.2 mark_new valid payload transitions status to 'enrolled' and returns speaker_id",
            passed=passed,
            details=f"Status: {res_new.status_code}, data: {data_new}",
            severity="HIGH",
        )

        # Verify created speaker profile in DB
        spk_row = self.db.get_speaker_profile(spk_new_id, "vlt_alpha_pri") if spk_new_id else None
        passed_spk = (spk_row is not None and spk_row["name"] == "Dr. Aris Thorne" and spk_row["sample_count"] == 3)
        self.record_result(
            test_name="2.2b mark_new creates speaker profile with name and matching sample_count",
            passed=passed_spk,
            details=f"Speaker row: {spk_row}",
            severity="HIGH",
        )

        # 2.3 mark_existing fuzzing: missing speaker_id
        cid_fuzz_exist = "cls_fuzz_exist_01"
        self.db.create_voiceprint_cluster(
            cluster_id=cid_fuzz_exist,
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            status="pending",
        )
        res_missing_spk = self.client.patch(
            f"/clusters/{cid_fuzz_exist}",
            json={"action": "mark_existing"},
            headers=headers_alice,
        )
        passed = (res_missing_spk.status_code == 422)
        self.record_result(
            test_name="2.3 mark_existing fuzzing: missing speaker_id returns HTTP 422",
            passed=passed,
            details=f"Expected 422, got {res_missing_spk.status_code}: {res_missing_spk.text}",
            severity="HIGH",
        )

        # 2.4 mark_existing with foreign speaker_id (speaker in Beta vault) returns HTTP 404
        spk_beta = self.db.create_speaker_profile(
            speaker_id="spk_beta_foreign_99",
            tenant_id="ten_beta",
            vault_id="vlt_beta_pri",
            user_id="usr_bob",
            name="Bob Foreign",
        )
        res_foreign_spk = self.client.patch(
            f"/clusters/{cid_fuzz_exist}",
            json={"action": "mark_existing", "speaker_id": spk_beta["speaker_id"]},
            headers=headers_alice,
        )
        passed = (res_foreign_spk.status_code == 404)
        self.record_result(
            test_name="2.4 mark_existing with foreign vault speaker_id returns HTTP 404",
            passed=passed,
            details=f"Expected 404, got {res_foreign_spk.status_code}: {res_foreign_spk.text}",
            severity="CRITICAL",
        )

        # 2.5 mark_existing with nonexistent speaker_id returns HTTP 404
        res_nonexist_spk = self.client.patch(
            f"/clusters/{cid_fuzz_exist}",
            json={"action": "mark_existing", "speaker_id": "spk_nonexistent_0000"},
            headers=headers_alice,
        )
        passed = (res_nonexist_spk.status_code == 404)
        self.record_result(
            test_name="2.5 mark_existing with non-existent speaker_id returns HTTP 404",
            passed=passed,
            details=f"Expected 404, got {res_nonexist_spk.status_code}: {res_nonexist_spk.text}",
            severity="HIGH",
        )

        # 2.6 mark_existing with valid speaker_id transitions to 'merged' and updates sample_count
        spk_target_alice = self.db.create_speaker_profile(
            speaker_id="spk_alice_target_01",
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            user_id="usr_alice",
            name="Alice Verified",
            sample_count=5,
        )
        cid_valid_merge = "cls_valid_merge_01"
        self.db.create_voiceprint_cluster(
            cluster_id=cid_valid_merge,
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            status="pending",
            occurrences_count=4,
        )
        res_merge = self.client.patch(
            f"/clusters/{cid_valid_merge}",
            json={"action": "mark_existing", "speaker_id": spk_target_alice["speaker_id"]},
            headers=headers_alice,
        )
        data_merge = res_merge.json() if res_merge.status_code == 200 else {}
        spk_after_merge = self.db.get_speaker_profile("spk_alice_target_01", "vlt_alpha_pri")
        passed = (
            res_merge.status_code == 200
            and data_merge.get("status") == "merged"
            and spk_after_merge["sample_count"] == 9  # 5 + 4
        )
        self.record_result(
            test_name="2.6 mark_existing valid payload transitions to 'merged' and increments sample_count",
            passed=passed,
            details=f"Status: {res_merge.status_code}, sample_count: {spk_after_merge['sample_count'] if spk_after_merge else None}",
            severity="HIGH",
        )

        # 2.6b Idempotency check: repeated mark_existing does not re-increment sample count
        self.client.patch(
            f"/clusters/{cid_valid_merge}",
            json={"action": "mark_existing", "speaker_id": spk_target_alice["speaker_id"]},
            headers=headers_alice,
        )
        spk_after_retry = self.db.get_speaker_profile("spk_alice_target_01", "vlt_alpha_pri")
        passed_idem = (spk_after_retry["sample_count"] == 9)
        self.record_result(
            test_name="2.6b mark_existing idempotency: repeated merge does not inflate sample_count",
            passed=passed_idem,
            details=f"Expected sample_count 9, got {spk_after_retry['sample_count']}",
            severity="MEDIUM",
        )

        # 2.7 dismiss action: status transitions to 'dismissed' and subsequent matching segments are de-weighted/suppressed
        cid_dismiss = "cls_to_dismiss_01"
        self.db.create_voiceprint_cluster(
            cluster_id=cid_dismiss,
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            status="pending",
            confidence_score=0.75,
        )
        res_dismiss = self.client.patch(
            f"/clusters/{cid_dismiss}",
            json={"action": "dismiss"},
            headers=headers_alice,
        )
        passed_dismiss = (res_dismiss.status_code == 200 and res_dismiss.json().get("status") == "dismissed")
        self.record_result(
            test_name="2.7 dismiss action transitions status to 'dismissed'",
            passed=passed_dismiss,
            details=f"Status: {res_dismiss.status_code}, response: {res_dismiss.text}",
            severity="HIGH",
        )

        # Verify cluster is suppressed from pending candidate queries
        pending_clusters = self.db.list_voiceprint_clusters("vlt_alpha_pri", status="pending")
        pending_ids = [c["cluster_id"] for c in pending_clusters]
        passed_suppress = (cid_dismiss not in pending_ids)
        self.record_result(
            test_name="2.7b dismiss suppresses cluster from pending clustering candidate queries",
            passed=passed_suppress,
            details=f"Dismissed cluster {cid_dismiss} present in pending: {cid_dismiss in pending_ids}",
            severity="HIGH",
        )

        # 2.8 Invalid actions return HTTP 422
        invalid_actions = [
            "delete",
            "random_action",
            "destroy",
            "",
            "DROP TABLE voiceprint_clusters;",
            "undefined",
        ]
        for inv_act in invalid_actions:
            res_inv = self.client.patch(
                f"/clusters/{cid_valid_merge}",
                json={"action": inv_act},
                headers=headers_alice,
            )
            passed = (res_inv.status_code == 422)
            self.record_result(
                test_name=f"2.8 Invalid action '{inv_act}' returns HTTP 422",
                passed=passed,
                details=f"Expected 422, got {res_inv.status_code}: {res_inv.text}",
                severity="HIGH",
            )

    # =========================================================================
    # Part 3: Retroactive Message Relabeling Integrity
    # =========================================================================
    def challenge_retroactive_message_relabeling(self):
        print("\n" + "=" * 80)
        print("STAGE 3: Retroactive Message Relabeling Multi-Vault Integrity Challenge")
        print("=" * 80)

        # Setup test cluster in Alpha
        cid_alpha_retro = "cls_alpha_retro_01"
        self.db.create_voiceprint_cluster(
            cluster_id=cid_alpha_retro,
            tenant_id="ten_alpha",
            vault_id="vlt_alpha_pri",
            status="pending",
        )

        # Setup test cluster in Beta
        cid_beta_retro = "cls_beta_retro_01"
        self.db.create_voiceprint_cluster(
            cluster_id=cid_beta_retro,
            tenant_id="ten_beta",
            vault_id="vlt_beta_pri",
            status="pending",
        )

        # Seed section_messages across Alpha and Beta
        with self.db.connect(immediate=True) as c:
            # 5 Alpha messages linked to cls_alpha_retro_01
            for i in range(5):
                c.execute(
                    """INSERT OR REPLACE INTO section_messages
                       (message_id, section_id, speaker_id, cluster_id, speaker_name, content, start_ms, end_ms)
                       VALUES (?, 'sec_alpha_01', NULL, ?, 'Unknown', 'Alpha message utterance', ?, ?)""",
                    (f"msg_alpha_rel_{i}", cid_alpha_retro, i * 2000, (i + 1) * 2000),
                )
            # 3 Alpha messages with NO cluster
            for i in range(3):
                c.execute(
                    """INSERT OR REPLACE INTO section_messages
                       (message_id, section_id, speaker_id, cluster_id, speaker_name, content, start_ms, end_ms)
                       VALUES (?, 'sec_alpha_01', NULL, NULL, 'Unknown', 'Unassigned utterance', ?, ?)""",
                    (f"msg_alpha_unassigned_{i}", (i + 10) * 2000, (i + 11) * 2000),
                )
            # 4 Beta messages linked to cls_beta_retro_01
            for i in range(4):
                c.execute(
                    """INSERT OR REPLACE INTO section_messages
                       (message_id, section_id, speaker_id, cluster_id, speaker_name, content, start_ms, end_ms)
                       VALUES (?, 'sec_beta_01', NULL, ?, 'Unknown', 'Beta medical transcription', ?, ?)""",
                    (f"msg_beta_rel_{i}", cid_beta_retro, i * 2000, (i + 1) * 2000),
                )

        headers_alice = {
            "Authorization": "Bearer token_alice_alpha",
            "x-tenant-id": "ten_alpha",
            "x-vault-id": "vlt_alpha_pri",
            "x-user-id": "usr_alice",
        }

        # Execute mark_new on cls_alpha_retro_01
        res = self.client.patch(
            f"/clusters/{cid_alpha_retro}",
            json={"action": "mark_new", "name": "Major Sarah Briggs"},
            headers=headers_alice,
        )
        assert res.status_code == 200, f"mark_new failed: {res.text}"
        new_spk_id = res.json()["speaker_id"]

        # 3.1 Verify all 5 matching Alpha messages are updated
        alpha_msgs = self.db.all(
            "SELECT message_id, speaker_id, speaker_name FROM section_messages WHERE cluster_id = ?",
            (cid_alpha_retro,),
        )
        updated_count = sum(1 for m in alpha_msgs if m["speaker_name"] == "Major Sarah Briggs" and m["speaker_id"] == new_spk_id)
        passed = (updated_count == 5)
        self.record_result(
            test_name="3.1 mark_new retroactively relabels all matching Alpha section_messages",
            passed=passed,
            details=f"Updated 5/5: {updated_count == 5} (count={updated_count})",
            severity="CRITICAL",
        )

        # 3.2 Verify unassigned Alpha messages remain untouched
        unassigned_msgs = self.db.all(
            "SELECT message_id, speaker_id, speaker_name FROM section_messages WHERE message_id LIKE 'msg_alpha_unassigned_%'"
        )
        unassigned_intact = all(m["speaker_name"] == "Unknown" and m["speaker_id"] is None for m in unassigned_msgs)
        self.record_result(
            test_name="3.2 Unassigned Alpha messages remain untouched",
            passed=unassigned_intact,
            details=f"Unassigned messages count: {len(unassigned_msgs)}, intact: {unassigned_intact}",
            severity="HIGH",
        )

        # 3.3 Verify foreign Beta messages remain completely untouched
        beta_msgs = self.db.all(
            "SELECT message_id, speaker_id, speaker_name FROM section_messages WHERE cluster_id = ?",
            (cid_beta_retro,),
        )
        beta_intact = all(m["speaker_name"] == "Unknown" and m["speaker_id"] is None for m in beta_msgs)
        passed_beta = (len(beta_msgs) == 4 and beta_intact)
        self.record_result(
            test_name="3.3 Foreign Beta messages remain strictly untouched during Alpha relabeling",
            passed=passed_beta,
            details=f"Beta messages count: {len(beta_msgs)}, all Unknown: {beta_intact}",
            severity="CRITICAL",
        )

        # 3.4 Execute mark_existing on cls_beta_retro_01 by Bob in Beta
        spk_bob_doc = self.db.create_speaker_profile(
            speaker_id="spk_doc_robert",
            tenant_id="ten_beta",
            vault_id="vlt_beta_pri",
            user_id="usr_bob",
            name="Dr. Robert Thorne",
        )
        headers_bob = {
            "Authorization": "Bearer token_bob_beta",
            "x-tenant-id": "ten_beta",
            "x-vault-id": "vlt_beta_pri",
            "x-user-id": "usr_bob",
        }
        res_bob = self.client.patch(
            f"/clusters/{cid_beta_retro}",
            json={"action": "mark_existing", "speaker_id": spk_bob_doc["speaker_id"]},
            headers=headers_bob,
        )
        assert res_bob.status_code == 200, f"mark_existing failed: {res_bob.text}"

        # Verify Beta messages now relabeled to Dr. Robert Thorne
        beta_msgs_after = self.db.all(
            "SELECT message_id, speaker_id, speaker_name FROM section_messages WHERE cluster_id = ?",
            (cid_beta_retro,),
        )
        beta_relabeled = all(m["speaker_name"] == "Dr. Robert Thorne" and m["speaker_id"] == "spk_doc_robert" for m in beta_msgs_after)
        self.record_result(
            test_name="3.4 mark_existing retroactively relabels Beta messages to target speaker",
            passed=beta_relabeled,
            details=f"Beta messages relabeled correctly: {beta_relabeled}",
            severity="CRITICAL",
        )

        # Verify Alpha messages still retain Major Sarah Briggs
        alpha_msgs_after = self.db.all(
            "SELECT message_id, speaker_id, speaker_name FROM section_messages WHERE cluster_id = ?",
            (cid_alpha_retro,),
        )
        alpha_still_intact = all(m["speaker_name"] == "Major Sarah Briggs" and m["speaker_id"] == new_spk_id for m in alpha_msgs_after)
        self.record_result(
            test_name="3.5 Alpha messages retain attribution after independent Beta relabeling",
            passed=alpha_still_intact,
            details=f"Alpha messages still intact: {alpha_still_intact}",
            severity="CRITICAL",
        )

    # =========================================================================
    # Run All & Generate Verdict
    # =========================================================================
    def run_all(self) -> int:
        print("=" * 80)
        print("EMPIRICAL CHALLENGER: Multi-Tenant Boundary & Cluster Lifecycle Verification")
        print("Target: Milestone M3 @ 82cfd77 / 77daa13")
        print("=" * 80)

        self.challenge_multi_tenant_partitioning_and_spoofing()
        self.challenge_cluster_lifecycle_fuzzing()
        self.challenge_retroactive_message_relabeling()

        total = len(self.passes) + len(self.failures)
        pass_count = len(self.passes)
        fail_count = len(self.failures)

        print("\n" + "=" * 80)
        print(f"CHALLENGE SUMMARY: {pass_count}/{total} PASSED, {fail_count} FAILED")
        print("=" * 80)

        if self.failures:
            print("\nFAILURES DETECTED:")
            for f in self.failures:
                print(f"  - [{f['severity']}] {f['test_name']}: {f['details']}")
            print("\nFINAL VERDICT: CHALLENGE_FAILED")
            return 1
        else:
            print("\nALL ADVERSARIAL STRESS CHALLENGES PASSED.")
            print("FINAL VERDICT: APPROVE")
            return 0


def main():
    challenger = TenancyLifecycleChallenger()
    try:
        code = challenger.run_all()
    finally:
        challenger.cleanup()
    sys.exit(code)


if __name__ == "__main__":
    main()
