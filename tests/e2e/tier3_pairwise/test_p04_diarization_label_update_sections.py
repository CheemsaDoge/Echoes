"""Tier 3: Pairwise P04 - Diarization Cluster Labeling Updating Historical Section Messages.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 4.3.3 & Contract C6.
Cross-feature interaction between:
1. Voiceprint Clustering & Pending Clusters (/clusters)
2. Cluster Patch Mutation (mark_new)
3. Retroactive Section Messages Relabeling
4. Dynamic 3-Tier Speaker Fallback Resolution (Contract C6)
"""

from __future__ import annotations
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.timeline_harness import resolve_speaker_name


class TestDiarizationLabelUpdateSections:
    """Verifies that labeling a voiceprint cluster retroactively updates historical transcript messages."""

    def test_cluster_labeling_updates_historical_section_messages_and_speaker_resolution(
        self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str]
    ):
        vault_id = "vault_primary"
        cluster_id = "cls_evelyn_01"

        # 1. Seed a pending cluster
        store.voiceprint_clusters[cluster_id] = {
            "cluster_id": cluster_id,
            "vault_id": vault_id,
            "status": "pending",
            "confidence_score": 0.91,
            "occurrences_count": 3,
        }

        # 2. Seed 3 historical section messages belonging to this cluster with speaker_id = None
        for i in range(3):
            mid = f"msg_ev_{i}"
            store.section_messages[mid] = {
                "message_id": mid,
                "vault_id": vault_id,
                "cluster_id": cluster_id,
                "speaker_id": None,
                "content": f"Statement number {i} from speaker in cluster.",
            }

        # Before labeling: fallback resolves to sequential unknown index ("Speaker 1")
        ctx_before = {
            "speaker_profiles": store.speaker_profiles,
            "active_roster": {},
            "unknown_speaker_index": 1,
        }
        name_before = resolve_speaker_name(store.section_messages["msg_ev_0"]["speaker_id"], ctx_before)
        assert name_before == "Speaker 1"

        # 3. User reviews cluster in UI and assigns name via PATCH /clusters/{id}
        patch_payload = {
            "action": "mark_new",
            "name": "Dr. Evelyn Reed",
            "notes": "Chief Scientist",
        }
        res = client.patch(f"/clusters/{cluster_id}", json=patch_payload, headers=auth_headers)
        assert res.status_code == 200
        patch_data = res.json()
        assert patch_data["status"] == "enrolled"
        assigned_spk_id = patch_data["speaker_id"]

        # 4. Verify retroactive update: all 3 section messages now have speaker_id set
        for i in range(3):
            mid = f"msg_ev_{i}"
            assert store.section_messages[mid]["speaker_id"] == assigned_spk_id

        # 5. Verify 3-tier fallback resolution now resolves to "Dr. Evelyn Reed"
        ctx_after = {
            "speaker_profiles": store.speaker_profiles,
            "active_roster": {},
            "unknown_speaker_index": 1,
        }
        name_after = resolve_speaker_name(store.section_messages["msg_ev_0"]["speaker_id"], ctx_after)
        assert name_after == "Dr. Evelyn Reed"

    def test_cluster_merging_into_existing_speaker(
        self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str]
    ):
        """User merges cluster into existing profile Spk_01 (Alice) -> section messages updated to Alice."""
        vault_id = "vault_primary"
        cluster_id = "cls_merge_01"

        # Existing profile
        existing_spk_id = "spk_alice_01"
        store.speaker_profiles[existing_spk_id] = {
            "speaker_id": existing_spk_id,
            "vault_id": vault_id,
            "name": "Alice Johnson",
        }

        # Pending cluster
        store.voiceprint_clusters[cluster_id] = {
            "cluster_id": cluster_id,
            "vault_id": vault_id,
            "status": "pending",
        }
        store.section_messages["msg_alice_unlinked"] = {
            "message_id": "msg_alice_unlinked",
            "vault_id": vault_id,
            "cluster_id": cluster_id,
            "speaker_id": None,
            "content": "Merging this speech into Alice.",
        }

        res = client.patch(
            f"/clusters/{cluster_id}",
            json={"action": "mark_existing", "speaker_id": existing_spk_id},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["status"] == "merged"

        # Check section message updated to Alice
        assert store.section_messages["msg_alice_unlinked"]["speaker_id"] == existing_spk_id
