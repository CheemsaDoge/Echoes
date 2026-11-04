import pytest
from fastapi.testclient import TestClient
from echoledger.api import create_app
from echoledger.config import Settings
from conftest import TOKEN, MCP_TOKEN

@pytest.fixture
def test_client(tmp_path):
    app = create_app(Settings(
        data_dir=tmp_path,
        api_token=TOKEN,
        mcp_token=MCP_TOKEN,
        asr_provider="mock",
        vad_provider="energy_demo",
        run_worker=False
    ))
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + TOKEN
        yield c, app, tmp_path


def test_runtime_config_auth(test_client):
    client, _, _ = test_client
    # Missing auth
    assert client.get("/v1/runtime-config", headers={"Authorization": ""}).status_code == 401
    assert client.put("/v1/runtime-config", json={}, headers={"Authorization": ""}).status_code == 401
    # Invalid token
    assert client.get("/v1/runtime-config", headers={"Authorization": "Bearer bad-token"}).status_code == 401
    assert client.get("/v1/runtime-config", headers={"Authorization": "Bearer " + MCP_TOKEN}).status_code == 401


def test_runtime_config_get_defaults(test_client):
    client, _, _ = test_client
    resp = client.get("/v1/runtime-config")
    assert resp.status_code == 200
    data = resp.json()

    # Structural sections check
    assert "asr" in data
    assert "vad" in data
    assert "voiceprint" in data

    # ASR defaults
    assert data["asr"]["asr_provider"] == "mock"
    assert data["asr"]["has_gemini_key"] is False
    assert data["asr"]["gemini_key"] == ""
    assert data["asr"]["has_vertex_api_key"] is False
    assert data["asr"]["vertex_api_key"] == ""
    assert data["asr"]["gemini_model"] == "gemini-3.5-transcribe"
    assert data["asr"]["gemini_mode"] == "transcribe"

    # VAD defaults
    assert data["vad"]["vad_provider"] == "energy_demo"
    assert "config" in data["vad"]
    assert data["vad"]["config"]["threshold"] == 0.35
    assert data["vad"]["config"]["end_threshold"] == 0.20
    assert data["vad"]["config"]["negative_threshold"] == 0.20
    assert data["vad"]["active_profile_id"] == "default-v1"

    # Voiceprint defaults
    assert data["voiceprint"]["speaker_model_id"] == "wespeaker_ecapa_tdnn512"
    assert data["voiceprint"]["similarity_threshold"] == 0.8
    assert data["voiceprint"]["margin"] == 0.1
    assert data["voiceprint"]["min_speech_ms"] == 1500
    assert data["voiceprint"]["auto_match_enabled"] is True
    assert data["voiceprint"]["consent_required"] is True

    # Flat aliases check
    assert data["asr_provider"] == "mock"
    assert data["vad_provider"] == "energy_demo"
    assert data["speaker_model_id"] == "wespeaker_ecapa_tdnn512"
    assert data["vad_config"]["threshold"] == 0.35


def test_runtime_config_update_ai_and_masking(test_client):
    client, app, _ = test_client

    # 1. Update gemini configuration
    patch_payload = {
        "asr_provider": "gemini",
        "gemini_key": "AIzaSyTestGeminiKey1234567890",
        "gemini_model": "gemini-2.0-flash",
        "gemini_mode": "structured",
    }
    resp = client.put("/v1/runtime-config", json=patch_payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["asr"]["asr_provider"] == "gemini"
    assert data["asr"]["has_gemini_key"] is True
    assert data["asr"]["gemini_key"] == "***"  # masked
    assert data["gemini_key"] == "***"
    assert data["has_gemini_key"] is True
    assert data["gemini_model"] == "gemini-2.0-flash"
    assert data["gemini_mode"] == "structured"
    assert app.state.settings.gemini_key == "AIzaSyTestGeminiKey1234567890"

    # 2. Sending masked placeholder "***" should NOT overwrite key
    resp2 = client.put("/v1/runtime-config", json={"gemini_key": "***", "gemini_model": "gemini-3.5-transcribe"})
    assert resp2.status_code == 200
    assert resp2.json()["has_gemini_key"] is True
    assert app.state.settings.gemini_key == "AIzaSyTestGeminiKey1234567890"
    assert app.state.settings.gemini_model == "gemini-3.5-transcribe"

    # 3. Clearing key explicitly with ""
    resp3 = client.put("/v1/runtime-config", json={"gemini_key": ""})
    assert resp3.status_code == 200
    assert resp3.json()["has_gemini_key"] is False
    assert resp3.json()["gemini_key"] == ""
    assert app.state.settings.gemini_key == ""

    # 4. Vertex AI credentials & endpoint
    patch_vertex = {
        "asr_provider": "vertexai",
        "vertex_project": "test-gcp-project",
        "vertex_location": "us-central1",
        "vertex_model": "gemini-2.5-flash",
        "vertex_endpoint": "https://custom-endpoint.googleapis.com",
        "vertex_api_key": "VertexApiKeySecret12345",
    }
    resp4 = client.put("/v1/runtime-config", json=patch_vertex)
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert data4["vertex_project"] == "test-gcp-project"
    assert data4["vertex_location"] == "us-central1"
    assert data4["vertex_model"] == "gemini-2.5-flash"
    assert data4["vertex_endpoint"] == "https://custom-endpoint.googleapis.com"
    assert data4["has_vertex_api_key"] is True
    assert data4["vertex_api_key"] == "***"
    assert app.state.settings.vertex_api_key == "VertexApiKeySecret12345"


def test_runtime_config_update_vad(test_client):
    client, app, _ = test_client

    patch_vad = {
        "vad_provider": "webrtc_2",
        "vad_config": {
            "threshold": 0.50,
            "end_threshold": 0.25,
            "min_speech_ms": 128,
            "min_silence_ms": 600,
            "pad_ms": 250,
            "uncertain_threshold": 0.15,
            "max_segment_ms": 20000,
            "rms_gate": 0.01
        }
    }
    resp = client.put("/v1/runtime-config", json=patch_vad)
    assert resp.status_code == 200
    data = resp.json()

    assert data["vad_provider"] == "webrtc_2"
    assert data["vad"]["vad_provider"] == "webrtc_2"
    assert app.state.settings.vad_provider == "webrtc_2"
    cfg = data["vad"]["config"]
    assert cfg["threshold"] == 0.50
    assert cfg["end_threshold"] == 0.25
    assert cfg["negative_threshold"] == 0.25
    assert cfg["min_speech_ms"] == 128
    assert cfg["min_silence_ms"] == 600
    assert cfg["pad_ms"] == 250
    assert cfg["uncertain_threshold"] == 0.15
    assert cfg["uncertainty_threshold"] == 0.15
    assert cfg["max_segment_ms"] == 20000
    assert cfg["rms_gate"] == 0.01

    # Verify invalid vad_provider rejected
    bad_prov = client.put("/v1/runtime-config", json={"vad_provider": "nonexistent_vad"})
    assert bad_prov.status_code == 422

    # Verify invalid vad thresholds rejected
    bad_thresholds = client.put("/v1/runtime-config", json={"vad_config": {"threshold": 0.2, "negative_threshold": 0.3}})
    assert bad_thresholds.status_code == 422


def test_runtime_config_update_voiceprint_and_status(test_client):
    client, app, _ = test_client

    patch_vp = {
        "speaker_model_id": "campplus_cn",
        "similarity_threshold": 0.85,
        "margin": 0.15,
        "min_speech_ms": 2000,
        "auto_match_enabled": False,
        "consent_required": True,
    }
    resp = client.put("/v1/runtime-config", json=patch_vp)
    assert resp.status_code == 200
    data = resp.json()
    assert data["speaker_model_id"] == "campplus_cn"
    assert data["similarity_threshold"] == 0.85
    assert data["margin"] == 0.15
    assert data["min_speech_ms"] == 2000
    assert data["auto_match_enabled"] is False
    assert data["consent_required"] is True

    # Check /v1/status includes voiceprint_config and vad_active_profile
    status_resp = client.get("/v1/status")
    assert status_resp.status_code == 200
    s_data = status_resp.json()
    assert "vad_active_profile" in s_data
    assert s_data["vad_active_profile"]["id"] == "default-v1"
    assert "voiceprint_config" in s_data
    assert s_data["voiceprint_config"]["speaker_model_id"] == "campplus_cn"
    assert s_data["voiceprint_config"]["similarity_threshold"] == 0.85
    assert s_data["voiceprint_config"]["margin"] == 0.15
    assert s_data["voiceprint_config"]["min_speech_ms"] == 2000
    assert s_data["voiceprint_config"]["auto_match_enabled"] is False

    # Validation limits
    assert client.put("/v1/runtime-config", json={"similarity_threshold": 1.5}).status_code == 422
    assert client.put("/v1/runtime-config", json={"margin": 0.8}).status_code == 422
    assert client.put("/v1/runtime-config", json={"min_speech_ms": 20}).status_code == 422


def test_runtime_config_modular_patch(test_client):
    client, _, _ = test_client

    modular_payload = {
        "asr": {
            "asr_provider": "mock",
            "gemini_model": "gemini-test-modular",
        },
        "vad": {
            "vad_provider": "passthrough",
            "config": {
                "threshold": 0.6,
                "end_threshold": 0.2,
            }
        },
        "voiceprint": {
            "speaker_model_id": "eres2net_cn",
            "similarity_threshold": 0.75,
        }
    }
    resp = client.put("/v1/runtime-config", json=modular_payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["asr"]["asr_provider"] == "mock"
    assert data["asr"]["gemini_model"] == "gemini-test-modular"
    assert data["vad"]["vad_provider"] == "passthrough"
    assert data["vad"]["config"]["threshold"] == 0.6
    assert data["voiceprint"]["speaker_model_id"] == "eres2net_cn"
    assert data["voiceprint"]["similarity_threshold"] == 0.75


def test_runtime_config_persistence_across_app_recreation(test_client):
    client, _, data_dir = test_client

    # Write configs
    client.put("/v1/runtime-config", json={
        "asr_provider": "gemini",
        "gemini_key": "PersistedGeminiSecretKey98765",
        "gemini_model": "gemini-custom-persisted",
        "vad_provider": "webrtc_3",
        "speaker_model_id": "nemo_speakernet",
        "similarity_threshold": 0.88,
        "margin": 0.12,
        "vad_config": {
            "threshold": 0.55,
            "end_threshold": 0.22,
        }
    })

    # Create new app instance pointing to the exact same data_dir
    new_app = create_app(Settings(
        data_dir=data_dir,
        api_token=TOKEN,
        mcp_token=MCP_TOKEN,
        run_worker=False
    ))

    with TestClient(new_app) as new_client:
        new_client.headers["Authorization"] = "Bearer " + TOKEN
        resp = new_client.get("/v1/runtime-config")
        assert resp.status_code == 200
        data = resp.json()

        assert data["asr_provider"] == "gemini"
        assert data["has_gemini_key"] is True
        assert data["gemini_key"] == "***"
        assert new_app.state.settings.gemini_key == "PersistedGeminiSecretKey98765"
        assert data["gemini_model"] == "gemini-custom-persisted"
        assert data["vad_provider"] == "webrtc_3"
        assert data["speaker_model_id"] == "nemo_speakernet"
        assert data["similarity_threshold"] == 0.88
        assert data["margin"] == 0.12
        assert data["vad_config"]["threshold"] == 0.55
        assert data["vad_config"]["end_threshold"] == 0.22


def test_runtime_config_vertex_unconfigured_no_crash(test_client):
    client, _, _ = test_client
    # Switch to vertexai without credentials/project - should not crash with NameError (os.getenv)
    resp = client.put("/v1/runtime-config", json={"asr_provider": "vertexai"})
    assert resp.status_code == 200
    assert resp.json()["asr_provider"] == "vertexai"
    # GET should also work
    get_resp = client.get("/v1/runtime-config")
    assert get_resp.status_code == 200
    assert get_resp.json()["asr_provider"] == "vertexai"


def test_runtime_config_atomic_rollback_on_failure(test_client):
    client, app, _ = test_client
    initial_provider = app.state.settings.asr_provider
    initial_vad = app.state.settings.vad_provider

    # Send a request with a valid ASR change but invalid VAD threshold
    fail_resp = client.put("/v1/runtime-config", json={
        "asr_provider": "disabled",
        "vad_config": {"threshold": 0.2, "negative_threshold": 0.3}
    })
    assert fail_resp.status_code == 422
    # Verify live settings were NOT modified
    assert app.state.settings.asr_provider == initial_provider
    assert app.state.settings.vad_provider == initial_vad
    # Verify database was NOT modified
    assert app.state.db.get_setting("asr.provider") is None


def test_runtime_config_voice_match_uses_runtime_thresholds(test_client):
    client, app, _ = test_client
    from echoledger.models import VoiceMatch

    # Update runtime thresholds to high standards
    client.put("/v1/runtime-config", json={
        "similarity_threshold": 0.95,
        "margin": 0.25,
    })

    # Enroll a dummy voiceprint
    spk_id = client.post("/v1/speakers", json={"name": "Alice"}).json()["id"]
    from conftest import create_session, upload
    sid = create_session(client, "sess-match-01")
    upload(client, sid)
    client.post(f"/v1/sessions/{sid}/close", json={"final_seq": 0})
    app.state.pipeline.tick()
    segments = app.state.db.all("SELECT id FROM segments")
    seg_id = segments[0]["id"]

    dim = 16
    vec = [1.0] + [0.0] * (dim - 1)
    client.post(f"/v1/speakers/{spk_id}/voiceprints", json={
        "model_id": "test_model",
        "embedding": vec,
        "evidence_segment_id": seg_id,
        "consent": True
    })

    # Test match with similarity ~0.85 (less than 0.95) and threshold omitted
    query_vec = [0.85] + [float((1 - 0.85**2)**0.5)] + [0.0] * (dim - 2)
    match_resp = client.post("/v1/speaker-matches", json={
        "model_id": "test_model",
        "embedding": query_vec,
    })
    assert match_resp.status_code == 200
    res = match_resp.json()
    # At default 0.80 this would be accepted, but with runtime 0.95 it must be rejected!
    assert res["accepted"] is False

    # Explicit override in request still takes precedence
    override_resp = client.post("/v1/speaker-matches", json={
        "model_id": "test_model",
        "embedding": query_vec,
        "threshold": 0.80,
    })
    assert override_resp.status_code == 200
    assert override_resp.json()["accepted"] is True


def test_runtime_config_vad_versioned_profiles(test_client):
    client, app, _ = test_client
    initial_active = app.state.db.active_profile()

    # Update VAD settings
    client.put("/v1/runtime-config", json={
        "vad_config": {"threshold": 0.48}
    })

    new_active = app.state.db.active_profile()
    # Profile ID should be new (versioned) rather than overwriting initial
    assert new_active["id"] != initial_active["id"]
    # The old profile still exists in DB for queued batches
    old_row = app.state.db.one("SELECT * FROM profiles WHERE id=?", (initial_active["id"],))
    assert old_row is not None
    assert old_row["active"] == 0

