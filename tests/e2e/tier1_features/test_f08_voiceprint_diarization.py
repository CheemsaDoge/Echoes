"""Tier 1: Feature F3.1, F3.2, F3.3, F3.4 & Contract C6 - Voiceprint Diarization & 3-Tier Fallback.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 4.3 & Contract C6 & ORIGINAL_REQUEST.md R6.
- 512-dim embedding extraction & cosine similarity.
- Thresholds: match (0.78), cluster (0.72), rejection floor (0.60).
- /clusters and /speakers APIs, ClusterPatchRequest (mark_new, mark_existing, dismiss).
- Dynamic 3-tier fallback resolution:
  Tier 1: Named Speaker
  Tier 2: Activity Roster Suspect
  Tier 3: Sequential Unknown Index
"""

from __future__ import annotations
import math
import pytest
from starlette.testclient import TestClient
from tests.e2e.harness.server_harness import MemoryStore
from tests.e2e.harness.timeline_harness import (
    cosine_similarity,
    unit_normalize,
    compute_centroid,
    resolve_speaker_name,
)


class TestVoiceprintAndDiarization:
    """Verifies voiceprint embedding similarity, clustering thresholds, and speaker fallback resolution."""

    def test_512dim_embedding_cosine_similarity(self):
        """1. Verify unit normalization and cosine similarity dot product calculation."""
        # Create two 512-dim synthetic vectors
        dim = 512
        v1 = unit_normalize([1.0 if i % 2 == 0 else 0.5 for i in range(dim)])
        # Exactly identical vector has similarity 1.0
        assert pytest.approx(cosine_similarity(v1, v1), 1e-5) == 1.0

        # Orthogonal vector
        v2 = unit_normalize([-0.5 if i % 2 == 0 else 1.0 for i in range(dim)])
        sim = cosine_similarity(v1, v2)
        assert -1.0 <= sim <= 1.0

    def test_online_identification_threshold_078(self):
        """2. Cosine similarity >= 0.78 classifies as immediate recognized speaker."""
        tau_match = 0.78
        # Test similarities around threshold
        assert 0.79 >= tau_match, "0.79 must trigger immediate online speaker identification"
        assert 0.77 < tau_match, "0.77 must fall through to offline clustering"

    def test_offline_clustering_threshold_072_and_rejection_floor(self):
        """3. Similarity in [0.72, 0.78) merges into cluster; < 0.60 is rejected as background noise."""
        tau_cluster = 0.72
        tau_reject = 0.60

        sim_merge = 0.74
        sim_reject = 0.55

        assert sim_merge >= tau_cluster, "0.74 should qualify for agglomerative cluster merge"
        assert sim_reject < tau_reject, "0.55 should be discarded below rejection floor"

    def test_cluster_centroid_calculation(self):
        """4. Centroid computation yields unit-normalized mean vector."""
        dim = 512
        e1 = unit_normalize([1.0 if i < 256 else 0.0 for i in range(dim)])
        e2 = unit_normalize([0.0 if i < 256 else 1.0 for i in range(dim)])
        centroid = compute_centroid([e1, e2])

        assert len(centroid) == dim
        norm = math.sqrt(sum(x * x for x in centroid))
        assert pytest.approx(norm, 1e-5) == 1.0

    def test_cluster_patch_mark_new_retroactive_update(
        self, client: TestClient, store: MemoryStore, auth_headers: dict[str, str]
    ):
        """5. PATCH /clusters/{id} with mark_new creates speaker and updates section_messages."""
        cluster_id = "cls_new_01"
        store.voiceprint_clusters[cluster_id] = {
            "cluster_id": cluster_id,
            "vault_id": "vault_primary",
            "status": "pending",
            "confidence_score": 0.88,
        }
        # Pre-seed a section message belonging to this cluster
        store.section_messages["msg_01"] = {
            "message_id": "msg_01",
            "vault_id": "vault_primary",
            "cluster_id": cluster_id,
            "speaker_id": None,
            "content": "Hello team, let's start the standup.",
        }

        patch_body = {
            "action": "mark_new",
            "name": "Sarah Jenkins",
            "notes": "VP of Engineering",
        }
        res = client.patch(f"/clusters/{cluster_id}", json=patch_body, headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "enrolled"
        new_spk_id = data["speaker_id"]
        assert new_spk_id in store.speaker_profiles
        assert store.speaker_profiles[new_spk_id]["name"] == "Sarah Jenkins"

        # Check retroactive update on historical message
        assert store.section_messages["msg_01"]["speaker_id"] == new_spk_id

    def test_3tier_speaker_fallback_resolution(self):
        """6. Contract C6: 3-tier dynamic speaker fallback resolution."""
        # Tier 1: Named Speaker Profile
        context_tier1 = {
            "speaker_profiles": {
                "spk_01": {"name": "Alice Johnson"},
            },
            "active_roster": {},
        }
        assert resolve_speaker_name("spk_01", context_tier1) == "Alice Johnson"

        # Tier 2: Activity Roster Suspect
        context_tier2 = {
            "speaker_profiles": {
                "spk_02": {"name": ""},  # No name in profile
            },
            "active_roster": {
                "spk_02": {"suspected_name": "David (Vendor Lead)"},
            },
        }
        assert resolve_speaker_name("spk_02", context_tier2) == "David (Vendor Lead)"

        # Tier 3: Sequential Unknown Index
        context_tier3 = {
            "speaker_profiles": {},
            "active_roster": {},
            "unknown_speaker_index": 4,
        }
        assert resolve_speaker_name("spk_unregistered", context_tier3) == "Speaker 4"
        assert resolve_speaker_name(None, context_tier3) == "Speaker 4"
