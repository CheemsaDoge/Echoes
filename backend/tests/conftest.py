import hashlib, json
import numpy as np
import pytest
from fastapi.testclient import TestClient
from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.audio import wav_bytes

TOKEN = "test-owner-token-at-least-32-chars"
MCP_TOKEN = "test-readonly-token-at-least-32-chars"

@pytest.fixture
def app(tmp_path):
    return create_app(Settings(data_dir=tmp_path, api_token=TOKEN, mcp_token=MCP_TOKEN,
                              asr_provider="mock", vad_provider="energy_demo", run_worker=False))

@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + TOKEN
        yield c

def create_session(client, sid="session-00000001", purpose="journal"):
    response = client.post("/v1/sessions", json={"id": sid, "started_at_ms": 1730000000000, "purpose": purpose})
    assert response.status_code == 200, response.text
    return sid

def audio(seconds=1, voiced=True):
    n = int(seconds * 16000)
    if not voiced:
        return np.zeros(n, dtype=np.int16)
    return (np.sin(np.arange(n) * 2 * np.pi * 200 / 16000) * 9000).astype(np.int16)

def upload(client, sid, seq=0, samples=None, start_sample=None, chunk_id=None):
    samples = audio() if samples is None else samples
    content = wav_bytes(samples)
    metadata = {"id": chunk_id or f"{sid}-chunk-{seq:03d}", "start_sample": seq * len(samples) if start_sample is None else start_sample,
                "sample_count": len(samples), "sha256": hashlib.sha256(content).hexdigest()}
    return client.post(f"/v1/sessions/{sid}/chunks/{seq}", content=content,
                       headers={"Content-Type": "audio/wav", "X-Chunk-Metadata": json.dumps(metadata)})

def finish(client, app, sid="session-00000001", samples=None):
    create_session(client, sid)
    r = upload(client, sid, samples=samples)
    assert r.status_code == 200, r.text
    assert client.post(f"/v1/sessions/{sid}/close", json={"final_seq": 0}).status_code == 200
    assert app.state.pipeline.tick()
    return client.get("/v1/events").json()
