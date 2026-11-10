"""Comprehensive Test Suite for Server Voiceprint Diarization & 3-Tier Fallback Engine.
Milestone M3: Features F3.1–F3.4, Interface Contract C6.
"""

from __future__ import annotations

import math
import numpy as np
import pytest
from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.voiceprint import (
    TAU_IDENTIFICATION,
    TAU_CLUSTERING,
    TAU_REJECTION,
    MARGIN_IDENTIFICATION,
    MIN_SPEECH_DURATION_SEC,
    MIN_SNR_DB,
    MAX_CLIPPING_RATIO,
    EMBEDDING_DIM,
    EMBEDDING_BYTES,
    unit_normalize,
    pack_embedding,
    unpack_embedding,
    cosine_similarity,
    get_orthonormal_projection,
    project_to_512,
    generate_synthetic_embedding,
    calculate_snr,
    calculate_clipping_ratio,
    check_acoustic_qualification,
    compute_centroid,
    update_centroid_incremental,
    update_centroid_ema,
    match_enrolled_speaker,
    cluster_segment,
    hierarchical_cluster_segments,
    generate_synthetic_aac,
    decode_audio_to_pcm16,
    VoiceprintExtractor,
)
from echoledger.speakers import resolve_speaker_name, SessionSpeakerResolver

TOKEN = "test-token-at-least-32-chars-long-voiceprint"
MCP_TOKEN = "test-mcp-distinct-token-at-least-32-chars-long"


@pytest.fixture
def test_app(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        api_token=TOKEN,
        mcp_token=MCP_TOKEN,
        asr_provider="mock",
        vad_provider="energy_demo",
        run_worker=False,
    )
    return create_app(settings)


@pytest.fixture
def auth_client(test_app):
    with TestClient(test_app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        c.headers["x-tenant-id"] = "ten_default"
        c.headers["x-vault-id"] = "vlt_default"
        c.headers["x-user-id"] = "usr_default"
        yield c


# ============================================================================
# 1. 512-dim Embedding Extraction, Normalization & Projection
# ============================================================================

class TestVoiceprintEmbeddingPipeline:
    def test_unit_normalize_and_cosine_similarity(self):
        # 1. Normalization maintains unit Euclidean norm
        raw = [1.0 if i % 2 == 0 else 0.5 for i in range(EMBEDDING_DIM)]
        v = unit_normalize(raw)
        assert len(v) == EMBEDDING_DIM
        assert pytest.approx(float(np.linalg.norm(v)), 1e-6) == 1.0

        # 2. Identical vector cosine similarity is exactly 1.0
        assert pytest.approx(cosine_similarity(v, v), 1e-5) == 1.0

        # 3. Orthogonal vector cosine similarity is 0.0
        raw_ortho = [-0.5 if i % 2 == 0 else 1.0 for i in range(EMBEDDING_DIM)]
        v_ortho = unit_normalize(raw_ortho)
        assert pytest.approx(cosine_similarity(v, v_ortho), 1e-4) == 0.0

    def test_invalid_normalization_raises_error(self):
        # Zero vector cannot be normalized
        with pytest.raises(ValueError, match="Zero vector"):
            unit_normalize([0.0] * EMBEDDING_DIM)

        # Non-finite values raise error
        with pytest.raises(ValueError, match="non-finite"):
            unit_normalize([1.0, float("nan"), 0.5])

        # Multi-dimensional arrays raise error
        with pytest.raises(ValueError, match="1-dimensional"):
            unit_normalize([[1.0, 2.0], [3.0, 4.0]])

    def test_orthonormal_projection_isometry(self):
        # Projecting 192-dim (Cam++) to 512-dim preserves exact inner products
        rng = np.random.default_rng(123)
        u192 = unit_normalize(rng.standard_normal(192))
        v192 = unit_normalize(rng.standard_normal(192))

        sim_orig = cosine_similarity(u192, v192)
        u512 = project_to_512(u192)
        v512 = project_to_512(v192)

        assert len(u512) == 512
        assert len(v512) == 512
        assert pytest.approx(float(np.linalg.norm(u512)), 1e-6) == 1.0
        assert pytest.approx(float(np.linalg.norm(v512)), 1e-6) == 1.0

        sim_proj = cosine_similarity(u512, v512)
        # Deviation must be within machine precision
        assert abs(sim_orig - sim_proj) < 1e-5

    def test_orthonormal_projection_matrix_properties(self):
        W = get_orthonormal_projection(256, 512)
        assert W.shape == (256, 512)
        # W @ W.T must equal identity matrix of size 256
        identity = W @ W.T
        assert np.allclose(identity, np.eye(256), atol=1e-5)

    def test_binary_packing_and_unpacking(self):
        vec = unit_normalize([float(i) for i in range(EMBEDDING_DIM)])
        blob = pack_embedding(vec)
        assert len(blob) == EMBEDDING_BYTES  # Exactly 2048 bytes

        unpacked = unpack_embedding(blob)
        assert len(unpacked) == EMBEDDING_DIM
        assert unpacked.dtype == np.float32
        assert pytest.approx(float(np.linalg.norm(unpacked)), 1e-6) == 1.0
        assert np.allclose(vec, unpacked, atol=1e-6)

        # Corrupted blob size raises ValueError
        with pytest.raises(ValueError, match="Invalid blob size"):
            unpack_embedding(b"short_invalid_blob")

    def test_synthetic_embedding_generator(self):
        emb1 = generate_synthetic_embedding("speaker_alice")
        emb2 = generate_synthetic_embedding("speaker_alice")
        assert len(emb1) == EMBEDDING_DIM
        # Deterministic identical output for identical key
        assert np.array_equal(emb1, emb2)

        # Intra-speaker sample perturbance maintains > 0.95 similarity
        emb_sample1 = generate_synthetic_embedding("speaker_alice", sample_id="sample_01")
        emb_sample2 = generate_synthetic_embedding("speaker_alice", sample_id="sample_02")
        assert cosine_similarity(emb1, emb_sample1) >= 0.94
        assert cosine_similarity(emb_sample1, emb_sample2) >= 0.90

        # Inter-speaker similarity is low (< 0.25)
        emb_bob = generate_synthetic_embedding("speaker_bob")
        assert abs(cosine_similarity(emb1, emb_bob)) < 0.25


# ============================================================================
# 2. Acoustic Preprocessing & Qualification
# ============================================================================

class TestAcousticQualification:
    def test_audio_decoding(self):
        # Decode empty bytes
        empty = decode_audio_to_pcm16(b"")
        assert len(empty) == 0

        # Decode synthetic AAC
        aac_bytes = generate_synthetic_aac(duration_sec=1.0)
        assert len(aac_bytes) > 0
        decoded = decode_audio_to_pcm16(aac_bytes)
        assert len(decoded) > 0
        assert decoded.dtype == np.int16

    def test_snr_calculation(self):
        # 16kHz sine tone has high SNR
        t = np.linspace(0, 1.0, 16000, endpoint=False)
        clean_sine = (np.sin(2 * np.pi * 440 * t) * 20000).astype(np.int16)
        snr_clean = calculate_snr(clean_sine)
        assert snr_clean > 15.0

        # Pure constant/silence has 0 SNR
        silence = np.zeros(16000, dtype=np.int16)
        assert calculate_snr(silence) == 0.0

    def test_clipping_ratio(self):
        samples = np.array([32767, -32767, 100, 200], dtype=np.int16)
        ratio = calculate_clipping_ratio(samples, threshold=32760)
        assert ratio == 0.5

    def test_acoustic_qualification_gates(self):
        # Duration gate (< 3.0s fails)
        short_audio = np.zeros(16000 * 2, dtype=np.int16)  # 2.0s
        passed, msg = check_acoustic_qualification(short_audio)
        assert not passed
        assert "duration" in msg.lower()

        # Clean 4.0s sine tone passes all gates
        t = np.linspace(0, 4.0, 16000 * 4, endpoint=False)
        clean_audio = (np.sin(2 * np.pi * 440 * t) * 15000).astype(np.int16)
        passed, msg = check_acoustic_qualification(clean_audio)
        assert passed
        assert "passed" in msg.lower()


# ============================================================================
# 3. Dual-Threshold Clustering Engine & Centroid Mathematics
# ============================================================================

class TestClusteringAndCentroidMath:
    def test_identification_threshold_078(self):
        enrolled_vec = generate_synthetic_embedding("speaker_enrolled")
        profiles = [{"speaker_id": "spk_01", "centroid": enrolled_vec}]

        # Query matching enrolled speaker with sim >= 0.78
        query_match = 0.95 * enrolled_vec + 0.05 * unit_normalize(np.ones(512))
        query_match = unit_normalize(query_match)
        res = match_enrolled_speaker(query_match, profiles, threshold=TAU_IDENTIFICATION, margin=MARGIN_IDENTIFICATION)
        assert res["accepted"] is True
        assert res["speaker_id"] == "spk_01"
        assert res["similarity"] >= TAU_IDENTIFICATION

        # Query below threshold (0.75 < 0.78)
        query_low = 0.75 * enrolled_vec + 0.66 * unit_normalize(np.ones(512))
        query_low = unit_normalize(query_low)
        res_low = match_enrolled_speaker(query_low, profiles, threshold=TAU_IDENTIFICATION, margin=MARGIN_IDENTIFICATION)
        assert res_low["accepted"] is False
        assert res_low["speaker_id"] is None

    def test_clustering_threshold_072_and_rejection_floor_060(self):
        cand_vec = generate_synthetic_embedding("pending_cluster_01")
        pending = [{"cluster_id": "cls_01", "embedding_blob": pack_embedding(cand_vec)}]

        # Merge action (sim >= 0.72)
        query_merge = unit_normalize(0.85 * cand_vec + 0.15 * unit_normalize(np.ones(512)))
        dec_merge = cluster_segment(query_merge, pending, threshold=TAU_CLUSTERING, rejection_floor=TAU_REJECTION)
        assert dec_merge["action"] == "merge"
        assert dec_merge["cluster_id"] == "cls_01"
        assert dec_merge["similarity"] >= TAU_CLUSTERING

        # New cluster action (0.60 <= sim < 0.72)
        # Create vector with sim around 0.65
        ortho = unit_normalize(np.ones(512))
        ortho = unit_normalize(ortho - float(np.dot(ortho, cand_vec)) * cand_vec)
        query_new = unit_normalize(0.65 * cand_vec + math.sqrt(1 - 0.65**2) * ortho)
        dec_new = cluster_segment(query_new, pending, threshold=TAU_CLUSTERING, rejection_floor=TAU_REJECTION)
        assert dec_new["action"] == "new_cluster"
        assert dec_new["cluster_id"] is None
        assert TAU_REJECTION <= dec_new["similarity"] < TAU_CLUSTERING

        # Reject action (sim < 0.60)
        query_reject = unit_normalize(0.40 * cand_vec + math.sqrt(1 - 0.40**2) * ortho)
        dec_reject = cluster_segment(query_reject, pending, threshold=TAU_CLUSTERING, rejection_floor=TAU_REJECTION)
        assert dec_reject["action"] == "reject"
        assert dec_reject["similarity"] < TAU_REJECTION

    def test_centroid_incremental_update_maintains_unit_norm(self):
        c = generate_synthetic_embedding("centroid_seed")
        count = 1
        for i in range(20):
            new_v = generate_synthetic_embedding(f"sample_{i}")
            c = update_centroid_incremental(c, count, new_v)
            count += 1
            assert pytest.approx(float(np.linalg.norm(c)), 1e-6) == 1.0

    def test_centroid_ema_update_maintains_unit_norm(self):
        c_old = generate_synthetic_embedding("old_profile")
        c_incoming = generate_synthetic_embedding("incoming_cluster")
        c_new = update_centroid_ema(c_old, c_incoming, alpha=0.85)
        assert pytest.approx(float(np.linalg.norm(c_new)), 1e-6) == 1.0

    def test_hierarchical_clustering_average_linkage(self):
        # Create 3 segments from speaker A and 2 segments from speaker B
        s1 = ("s1", generate_synthetic_embedding("speaker_a", sample_id="sample_01"))
        s2 = ("s2", generate_synthetic_embedding("speaker_a", sample_id="sample_02"))
        s3 = ("s3", generate_synthetic_embedding("speaker_a", sample_id="sample_03"))

        s4 = ("s4", generate_synthetic_embedding("speaker_b", sample_id="sample_04"))
        s5 = ("s5", generate_synthetic_embedding("speaker_b", sample_id="sample_05"))

        clusters = hierarchical_cluster_segments([s1, s2, s3, s4, s5], threshold=0.72)
        assert len(clusters) == 2
        cluster_sets = [set(cl) for cl in clusters]
        assert {"s1", "s2", "s3"} in cluster_sets
        assert {"s4", "s5"} in cluster_sets


# ============================================================================
# 4. Contract C6 3-Tier Dynamic Fallback Resolution Engine
# ============================================================================

class TestSpeakerFallbackResolution:
    def test_fallback_tier1_confirmed_named_speaker(self):
        context = {
            "speaker_profiles": {
                "spk_01": {"name": "Dr. Sarah Lin", "role_label": "Chief Scientist"},
            },
            "active_roster": {},
        }
        assert resolve_speaker_name("spk_01", context) == "Dr. Sarah Lin"

    def test_fallback_tier2_activity_roster_suspect(self):
        context = {
            "speaker_profiles": {
                "spk_02": {"name": ""},  # Empty profile name
            },
            "active_roster": {
                "spk_02": {"suspected_name": "Marcus (DevOps)"},
            },
        }
        assert resolve_speaker_name("spk_02", context) == "Marcus (DevOps)"

    def test_fallback_tier3_unknown_speaker_index(self):
        context = {
            "speaker_profiles": {},
            "active_roster": {},
            "unknown_speaker_index": 7,
        }
        assert resolve_speaker_name("spk_unknown", context) == "Speaker 7"
        assert resolve_speaker_name(None, context) == "Speaker 7"

    def test_session_speaker_resolver_monotonic_stability(self):
        resolver = SessionSpeakerResolver(
            speaker_profiles={"spk_alice": {"name": "Alice"}},
            active_roster={"cls_suspect": {"suspected_name": "Bob (Contractor)"}},
        )

        # Tier 1 enrolled
        assert resolver.resolve(speaker_id="spk_alice") == "Alice"

        # Tier 2 suspect
        assert resolver.resolve(cluster_id="cls_suspect") == "Bob (Contractor)"

        # Tier 3 unknown speakers get deterministic monotonic 1-based indices
        spk_label_1 = resolver.resolve(cluster_id="cls_anon_1")
        assert spk_label_1 == "Speaker 1"

        spk_label_2 = resolver.resolve(cluster_id="cls_anon_2")
        assert spk_label_2 == "Speaker 2"

        # Repeated resolution for cls_anon_1 remains "Speaker 1"
        assert resolver.resolve(cluster_id="cls_anon_1") == "Speaker 1"


# ============================================================================
# 5. REST API Endpoints & Multi-Tenant Boundary Isolation
# ============================================================================

class TestSpeakersAndClustersApi:
    def test_get_and_post_speakers_scoped(self, auth_client: TestClient):
        # 1. Initially empty or legacy default
        res = auth_client.get("/speakers")
        assert res.status_code == 200
        data = res.json()
        assert "speakers" in data

        # 2. POST /speakers creates new speaker
        create_res = auth_client.post(
            "/speakers",
            json={"name": "Alice Wonderland", "role_label": "Lead Architect"},
        )
        assert create_res.status_code == 201
        created = create_res.json()
        assert created["name"] == "Alice Wonderland"
        spk_id = created["speaker_id"]
        assert spk_id.startswith("spk_")

        # 3. GET /speakers/{id} retrieves speaker
        detail_res = auth_client.get(f"/speakers/{spk_id}")
        assert detail_res.status_code == 200
        spk = detail_res.json()
        assert spk["speaker_id"] == spk_id
        assert spk["name"] == "Alice Wonderland"
        assert spk["role_label"] == "Lead Architect"

    def test_get_speaker_404_foreign_vault(self, auth_client: TestClient, test_app):
        # Pre-seed speaker in a different vault
        db = test_app.state.pipeline.db
        with db.connect(immediate=True) as c:
            c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name) VALUES ('vlt_other_vault', 'ten_default', 'Other Vault')")
        foreign_spk = db.create_speaker_profile(
            speaker_id="spk_foreign_01",
            tenant_id="ten_default",
            vault_id="vlt_other_vault",
            user_id="usr_default",
            name="Foreign Speaker",
        )
        # Attempt to access foreign speaker using default vault credentials
        res = auth_client.get(f"/speakers/{foreign_spk['speaker_id']}")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    def test_list_and_get_clusters_scoped(self, auth_client: TestClient, test_app):
        db = test_app.state.pipeline.db
        cluster = db.create_voiceprint_cluster(
            cluster_id="cls_test_01",
            tenant_id="ten_default",
            vault_id="vlt_default",
            status="pending",
            confidence_score=0.88,
            occurrences_count=3,
        )

        # GET /clusters returns candidate cluster
        res = auth_client.get("/clusters")
        assert res.status_code == 200
        clusters = res.json()["clusters"]
        assert any(c["cluster_id"] == "cls_test_01" for c in clusters)

        # Filter by ?status=pending
        res_pending = auth_client.get("/clusters?status=pending")
        assert res_pending.status_code == 200
        assert any(c["cluster_id"] == "cls_test_01" for c in res_pending.json()["clusters"])

        # Filter by ?status=identified (should not match)
        res_ident = auth_client.get("/clusters?status=identified")
        assert res_ident.status_code == 200
        assert not any(c["cluster_id"] == "cls_test_01" for c in res_ident.json()["clusters"])

        # GET /clusters/{id}
        detail = auth_client.get("/clusters/cls_test_01")
        assert detail.status_code == 200
        assert detail.json()["cluster_id"] == "cls_test_01"

    def test_cluster_foreign_vault_returns_404(self, auth_client: TestClient, test_app):
        db = test_app.state.pipeline.db
        with db.connect(immediate=True) as c:
            c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name) VALUES ('vlt_secret_chamber', 'ten_default', 'Secret Chamber')")
        db.create_voiceprint_cluster(
            cluster_id="cls_foreign_99",
            tenant_id="ten_default",
            vault_id="vlt_secret_chamber",
            status="pending",
        )

        res = auth_client.get("/clusters/cls_foreign_99")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    def test_cluster_audio_preview_returns_binary_aac(self, auth_client: TestClient, test_app):
        db = test_app.state.pipeline.db
        db.create_voiceprint_cluster(
            cluster_id="cls_audio_01",
            tenant_id="ten_default",
            vault_id="vlt_default",
            status="pending",
        )

        res = auth_client.get("/clusters/cls_audio_01/audio_preview")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("audio/aac")
        assert len(res.content) > 50
        # Check MPEG-4 ADTS syncword (0xFFF)
        assert res.content[0] == 0xFF
        assert (res.content[1] & 0xF0) == 0xF0

    def test_cluster_samples_endpoint(self, auth_client: TestClient, test_app):
        db = test_app.state.pipeline.db
        db.create_voiceprint_cluster(
            cluster_id="cls_samples_01",
            tenant_id="ten_default",
            vault_id="vlt_default",
            status="pending",
        )

        res = auth_client.get("/clusters/cls_samples_01/samples")
        assert res.status_code == 200
        data = res.json()
        assert data["cluster_id"] == "cls_samples_01"
        assert len(data["samples"]) >= 1
        assert "audio_url" in data["samples"][0]

    def test_patch_cluster_mark_new_enrolled_and_retroactive_update(self, auth_client: TestClient, test_app):
        db = test_app.state.pipeline.db
        cluster_id = "cls_patch_new_01"
        db.create_voiceprint_cluster(
            cluster_id=cluster_id,
            tenant_id="ten_default",
            vault_id="vlt_default",
            status="pending",
        )

        # Insert section and section_message associated with this cluster
        with db.connect(immediate=True) as c:
            c.execute(
                """INSERT INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, started_at)
                   VALUES ('sess_01', 'ten_default', 'vlt_default', 'usr_default', 'dp_default', '2026-11-10', '2026-11-10T00:00:00Z')"""
            )
            c.execute(
                """INSERT INTO sections (section_id, tenant_id, vault_id, live_session_id, section_index, start_ms, end_ms)
                   VALUES ('sec_01', 'ten_default', 'vlt_default', 'sess_01', 0, 0, 10000)"""
            )
            c.execute(
                """INSERT INTO section_messages (message_id, section_id, speaker_id, cluster_id, speaker_name, content, start_ms, end_ms)
                   VALUES ('msg_retro_01', 'sec_01', NULL, ?, 'Unknown', 'Test utterance content', 1000, 4000)""",
                (cluster_id,),
            )

        # Execute PATCH /clusters/{id} with mark_new
        patch_res = auth_client.patch(
            f"/clusters/{cluster_id}",
            json={
                "action": "mark_new",
                "name": "Sarah Connor",
                "notes": "Security Specialist",
            },
        )
        assert patch_res.status_code == 200
        data = patch_res.json()
        assert data["status"] == "enrolled"
        new_spk_id = data["speaker_id"]
        assert new_spk_id.startswith("spk_")

        # Verify cluster updated
        cluster_row = db.get_voiceprint_cluster(cluster_id, "vlt_default")
        assert cluster_row["status"] == "enrolled"
        assert cluster_row["speaker_id"] == new_spk_id

        # Verify retroactive message update
        msg_row = db.one("SELECT speaker_id, speaker_name FROM section_messages WHERE message_id='msg_retro_01'")
        assert msg_row["speaker_id"] == new_spk_id
        assert msg_row["speaker_name"] == "Sarah Connor"

    def test_patch_cluster_mark_existing_merged(self, auth_client: TestClient, test_app):
        db = test_app.state.pipeline.db
        cluster_id = "cls_patch_exist_01"
        db.create_voiceprint_cluster(
            cluster_id=cluster_id,
            tenant_id="ten_default",
            vault_id="vlt_default",
            status="pending",
        )
        # Pre-seed existing speaker profile
        target_spk = db.create_speaker_profile(
            speaker_id="spk_target_01",
            tenant_id="ten_default",
            vault_id="vlt_default",
            user_id="usr_default",
            name="John Doe",
            sample_count=2,
        )

        # Execute mark_existing
        patch_res = auth_client.patch(
            f"/clusters/{cluster_id}",
            json={
                "action": "mark_existing",
                "speaker_id": target_spk["speaker_id"],
            },
        )
        assert patch_res.status_code == 200
        data = patch_res.json()
        assert data["status"] == "merged"
        assert data["speaker_id"] == target_spk["speaker_id"]

        # Verify profile sample count incremented
        updated_spk = db.get_speaker_profile(target_spk["speaker_id"], "vlt_default")
        assert updated_spk["sample_count"] > 2

    def test_patch_cluster_mark_existing_foreign_speaker_returns_404(self, auth_client: TestClient, test_app):
        db = test_app.state.pipeline.db
        cluster_id = "cls_patch_foreign_target"
        db.create_voiceprint_cluster(
            cluster_id=cluster_id,
            tenant_id="ten_default",
            vault_id="vlt_default",
            status="pending",
        )
        # Target speaker in another vault
        with db.connect(immediate=True) as c:
            c.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name) VALUES ('vlt_inaccessible', 'ten_default', 'Inaccessible Vault')")
        db.create_speaker_profile(
            speaker_id="spk_other_vault_spk",
            tenant_id="ten_default",
            vault_id="vlt_inaccessible",
            user_id="usr_default",
            name="Foreign Person",
        )

        patch_res = auth_client.patch(
            f"/clusters/{cluster_id}",
            json={
                "action": "mark_existing",
                "speaker_id": "spk_other_vault_spk",
            },
        )
        assert patch_res.status_code == 404
        assert "not found" in patch_res.json()["detail"].lower()

    def test_patch_cluster_dismiss(self, auth_client: TestClient, test_app):
        db = test_app.state.pipeline.db
        cluster_id = "cls_dismiss_01"
        db.create_voiceprint_cluster(
            cluster_id=cluster_id,
            tenant_id="ten_default",
            vault_id="vlt_default",
            status="pending",
        )

        patch_res = auth_client.patch(
            f"/clusters/{cluster_id}",
            json={"action": "dismiss"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["status"] == "dismissed"

        cluster = db.get_voiceprint_cluster(cluster_id, "vlt_default")
        assert cluster["status"] == "dismissed"

    def test_patch_cluster_validation_errors_422(self, auth_client: TestClient, test_app):
        db = test_app.state.pipeline.db
        cluster_id = "cls_val_01"
        db.create_voiceprint_cluster(
            cluster_id=cluster_id,
            tenant_id="ten_default",
            vault_id="vlt_default",
            status="pending",
        )

        # Empty name on mark_new returns 422
        res_empty = auth_client.patch(
            f"/clusters/{cluster_id}",
            json={"action": "mark_new", "name": "   "},
        )
        assert res_empty.status_code == 422

        # Missing speaker_id on mark_existing returns 422
        res_missing_spk = auth_client.patch(
            f"/clusters/{cluster_id}",
            json={"action": "mark_existing"},
        )
        assert res_missing_spk.status_code == 422

        # Invalid action returns 422
        res_invalid = auth_client.patch(
            f"/clusters/{cluster_id}",
            json={"action": "invalid_action"},
        )
        assert res_invalid.status_code == 422
