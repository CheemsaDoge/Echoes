import json
import httpx
import numpy as np
import pytest
from echoledger.speakers import normalize,match_speaker
from echoledger.providers import GeminiTranscriber,VertexAiTranscriber,parse_transcribe,ProviderError,ProviderBlocked
from conftest import finish,TOKEN,MCP_TOKEN

@pytest.mark.parametrize("vector", [[0]*8,[float("nan")]*8,[1]*3,[float("inf")]*8])
def test_invalid_voiceprint(vector):
    with pytest.raises(ValueError): normalize(vector)

def test_voice_match_unknown_and_model_version():
    enrolled=[{"speaker_id":"a","model_id":"v1","embedding":json.dumps([1,0,0,0,0,0,0,0])}]
    assert match_speaker([1,0,0,0,0,0,0,0],"v1",enrolled)["accepted"]
    assert not match_speaker([1,0,0,0,0,0,0,0],"v2",enrolled)["accepted"]
    assert not match_speaker([0,1,0,0,0,0,0,0],"v1",enrolled)["accepted"]

def test_voice_match_margin_and_person_aggregation():
    vec=json.dumps([1,0,0,0,0,0,0,0])
    entries=[{"speaker_id":"a","model_id":"v1","embedding":vec}]*2
    assert match_speaker([1,0,0,0,0,0,0,0],"v1",entries)["accepted"]
    entries.append({"speaker_id":"b","model_id":"v1","embedding":vec})
    assert not match_speaker([1,0,0,0,0,0,0,0],"v1",entries)["accepted"]

def example_response():
    return {"status":"completed","steps":[{"type":"model_output","content":[{"type":"text","text":"Hello world","annotations":[
        {"type":"word_info","text":"Hello","speaker":"spk_1","start_offset":"0.100s","end_offset":"0.400s"},
        {"type":"word_info","text":"world","speaker":"spk_1","start_offset":"0.500s","end_offset":"0.800s"}]}]}]}

def test_gemini_adapter_uses_stateless_audio_request():
    def handler(request):
        body=json.loads(request.content)
        assert body["store"] is False
        assert body["generation_config"]["transcription_config"]["mode"]["diarization_mode"]=="speaker"
        assert request.headers["x-goog-api-key"]=="fake-key"
        return httpx.Response(200,json=example_response())
    provider=GeminiTranscriber("fake-key","gemini-3.5-transcribe",transport=httpx.MockTransport(handler))
    result=provider.transcribe(np.zeros(16000,dtype=np.int16))
    assert len(result.segments)==2
    assert result.segments[0].timing_quality=="word"
    assert not result.is_demo

def test_gemini_no_words_has_coarse_timestamp_not_invented_alignment():
    result=parse_transcribe({"output_text":"hello"},2000)
    assert result[0].timing_quality=="chunk" and result[0].end_ms==2000
    assert parse_transcribe({"output_text":""},2000)==[]

def test_gemini_rejects_out_of_range():
    with pytest.raises(ProviderError): parse_transcribe(example_response(),10)

@pytest.mark.parametrize("code,retry",[(429,True),(500,True),(401,False),(400,False)])
def test_gemini_http_failure_classification(code,retry):
    provider=GeminiTranscriber("fake","model",transport=httpx.MockTransport(lambda req:httpx.Response(code,json={"error":"fake"})))
    with pytest.raises(ProviderError) as caught: provider.transcribe(np.zeros(16000,dtype=np.int16))
    assert caught.value.retryable is retry
    assert "fake" not in str(caught.value)

def test_gemini_requires_key():
    with pytest.raises(ProviderBlocked): GeminiTranscriber("","model").transcribe(np.zeros(16000,dtype=np.int16))

def mcp_call(client,method,params=None,req_id=1,token=MCP_TOKEN,headers=None):
    body={"jsonrpc":"2.0","id":req_id,"method":method,"params":params or {}}
    return client.post("/mcp",json=body,headers={"Authorization":"Bearer "+token,**(headers or {})})

def test_mcp_readonly_token_and_origin(client):
    assert mcp_call(client,"initialize",token=TOKEN).status_code==401
    assert mcp_call(client,"initialize",headers={"Origin":"https://attacker.invalid"}).status_code==403
    assert mcp_call(client,"initialize",headers={"MCP-Protocol-Version":"1900"}).status_code==400

def test_mcp_initialize_list_and_call(client,app):
    finish(client,app)
    r=mcp_call(client,"initialize",{"protocolVersion":"2025-03-26","clientInfo":{"name":"test","version":"1"}})
    assert r.status_code==200 and r.json()["result"]["protocolVersion"]=="2025-03-26"
    names=[t["name"] for t in mcp_call(client,"tools/list").json()["result"]["tools"]]
    assert names==["search_memories","list_events","get_event","list_speakers"]
    result=mcp_call(client,"tools/call",{"name":"list_events","arguments":{}}).json()["result"]
    data=json.loads(result["content"][0]["text"])
    assert len(data["data"])==1 and data["untrusted_recorded_content"]
    assert mcp_call(client,"tools/call",{"name":"delete_session","arguments":{}}).json()["error"]["code"]==-32602

def test_mcp_confirmed_only_and_validation(client,app):
    finish(client,app)
    r=mcp_call(client,"tools/call",{"name":"search_memories","arguments":{"query":""}})
    assert json.loads(r.json()["result"]["content"][0]["text"])["data"]==[]
    assert "error" in mcp_call(client,"tools/call",{"name":"list_events","arguments":{"from_ms":"oops"}}).json()
    assert "error" in mcp_call(client,"tools/call",{"name":"list_events","arguments":{"limit":999999}}).json()

def test_mcp_notification(client):
    r=client.post("/mcp",json={"jsonrpc":"2.0","method":"notifications/initialized"},headers={"Authorization":"Bearer "+MCP_TOKEN})
    assert r.status_code==202 and not r.content

def test_mcp_malformed(client):
    r=client.post("/mcp",content=b"{",headers={"Authorization":"Bearer "+MCP_TOKEN})
    assert r.status_code==400 and r.json()["error"]["code"]==-32700

def vertex_example_response(text="Hello from Vertex", start_ms=100, end_ms=800):
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "segments": [
                                    {
                                        "start_ms": start_ms,
                                        "end_ms": end_ms,
                                        "text": text,
                                        "speaker_label": "spk_0"
                                    }
                                ]
                            })
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 20, "totalTokenCount": 70}
    }

def test_vertexai_url_resolution(monkeypatch):
    import google.auth.exceptions
    monkeypatch.setattr("google.auth.default", lambda scopes=None: (_ for _ in ()).throw(google.auth.exceptions.DefaultCredentialsError("No credentials")))

    t_global = VertexAiTranscriber(project="p1", location="global")
    assert t_global.resolve_endpoint() == "https://aiplatform.googleapis.com/v1/projects/p1/locations/global/publishers/google/models/gemini-3.8-flash:generateContent"

    t_reg = VertexAiTranscriber(project="p1", location="asia-east1")
    assert t_reg.resolve_endpoint() == "https://asia-east1-aiplatform.googleapis.com/v1/projects/p1/locations/asia-east1/publishers/google/models/gemini-2.5-flash:generateContent"

    t_express = VertexAiTranscriber(api_key="k1", project="", model="gemini-3.8-flash")
    assert t_express.resolve_endpoint() == "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-3.8-flash:generateContent"

    t_override = VertexAiTranscriber(endpoint="https://custom.endpoint/generateContent")
    assert t_override.resolve_endpoint() == "https://custom.endpoint/generateContent"

    t_missing = VertexAiTranscriber(project="", location="us-central1")
    with pytest.raises(ProviderBlocked, match="VERTEX_PROJECT_MISSING"):
        t_missing.resolve_endpoint()

def test_vertexai_api_key_transcribe():
    def handler(request):
        assert request.headers["x-goog-api-key"] == "my-api-key"
        body = json.loads(request.content)
        assert "contents" in body
        assert body["generationConfig"]["audioTimestamp"] is True
        return httpx.Response(200, json=vertex_example_response("Test audio speech"))

    transcriber = VertexAiTranscriber(
        api_key="my-api-key",
        endpoint="https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash:generateContent",
        transport=httpx.MockTransport(handler)
    )
    result = transcriber.transcribe(np.zeros(16000, dtype=np.int16))
    assert len(result.segments) == 1
    assert result.segments[0].text == "Test audio speech"
    assert result.segments[0].start_ms == 100
    assert result.segments[0].end_ms == 800
    assert result.usage.get("totalTokenCount") == 70

def test_vertexai_bearer_token_transcribe():
    def handler(request):
        assert request.headers["authorization"] == "Bearer mock-token-123"
        return httpx.Response(200, json=vertex_example_response("Authenticated speech"))

    transcriber = VertexAiTranscriber(
        project="p1",
        endpoint="https://test.endpoint/generateContent",
        transport=httpx.MockTransport(handler)
    )
    transcriber.get_auth_headers = lambda client=None: {"Authorization": "Bearer mock-token-123"}
    result = transcriber.transcribe(np.zeros(16000, dtype=np.int16))
    assert len(result.segments) == 1
    assert result.segments[0].text == "Authenticated speech"

def test_vertexai_missing_credentials():
    transcriber = VertexAiTranscriber(project="p1", credentials_path="non/existent/file.json")
    with pytest.raises(ProviderBlocked, match="VERTEX_CREDENTIALS_MISSING"):
        transcriber.transcribe(np.zeros(16000, dtype=np.int16))

def test_vertexai_timestamp_out_of_range():
    def handler(request):
        return httpx.Response(200, json=vertex_example_response(start_ms=0, end_ms=50000))

    transcriber = VertexAiTranscriber(
        api_key="my-key",
        endpoint="https://test.endpoint/generateContent",
        transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderError, match="ASR_TIMESTAMP_OUT_OF_RANGE"):
        transcriber.transcribe(np.zeros(16000, dtype=np.int16))

@pytest.mark.parametrize("code,retry", [(429, True), (500, True), (401, False), (400, False)])
def test_vertexai_http_failure_classification(code, retry):
    transcriber = VertexAiTranscriber(
        api_key="my-key",
        endpoint="https://test.endpoint/generateContent",
        transport=httpx.MockTransport(lambda req: httpx.Response(code, json={"error": "vertex error"}))
    )
    with pytest.raises(ProviderError) as caught:
        transcriber.transcribe(np.zeros(16000, dtype=np.int16))
    assert caught.value.retryable is retry
    assert "vertex error" not in str(caught.value)

def test_vertexai_prompt_feedback_blocked():
    transcriber = VertexAiTranscriber(
        api_key="my-key",
        endpoint="https://test.endpoint/generateContent",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    )
    with pytest.raises(ProviderBlocked, match="VERTEX_PROMPT_BLOCKED_SAFETY"):
        transcriber.transcribe(np.zeros(16000, dtype=np.int16))

@pytest.mark.parametrize("reason", ["SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT"])
def test_vertexai_finish_reason_blocked(reason):
    transcriber = VertexAiTranscriber(
        api_key="my-key",
        endpoint="https://test.endpoint/generateContent",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
            "candidates": [{"finishReason": reason, "content": {"parts": [{"text": ""}]}}]
        }))
    )
    with pytest.raises(ProviderBlocked, match=f"VERTEX_FINISH_BLOCKED_{reason}"):
        transcriber.transcribe(np.zeros(16000, dtype=np.int16))

def test_vertexai_valid_silence_empty_segments():
    transcriber = VertexAiTranscriber(
        api_key="my-key",
        endpoint="https://test.endpoint/generateContent",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({"segments": []})}]}}],
            "usageMetadata": {"totalTokenCount": 20}
        }))
    )
    result = transcriber.transcribe(np.zeros(16000, dtype=np.int16))
    assert len(result.segments) == 0
    assert result.usage.get("totalTokenCount") == 20

def test_vertexai_empty_response_rejected():
    transcriber_no_candidates = VertexAiTranscriber(
        api_key="my-key",
        endpoint="https://test.endpoint/generateContent",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"candidates": []}))
    )
    with pytest.raises(ProviderError, match="ASR_EMPTY_CANDIDATES"):
        transcriber_no_candidates.transcribe(np.zeros(16000, dtype=np.int16))

    transcriber_empty_parts = VertexAiTranscriber(
        api_key="my-key",
        endpoint="https://test.endpoint/generateContent",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "   "}]}}]
        }))
    )
    with pytest.raises(ProviderError, match="ASR_EMPTY_CONTENT"):
        transcriber_empty_parts.transcribe(np.zeros(16000, dtype=np.int16))

def test_vertexai_adc_project_discovery(monkeypatch):
    class MockCreds:
        valid = True
        token = "mock-adc-token"
        project_id = "adc-proj-from-creds"
    monkeypatch.setattr("google.auth.default", lambda scopes=None: (MockCreds(), "discovered-adc-project"))
    t = VertexAiTranscriber(project="", location="asia-east1")
    endpoint = t.resolve_endpoint()
    assert "discovered-adc-project" in endpoint
    assert t.project == "discovered-adc-project"

def test_vertexai_credential_file_loader(tmp_path, monkeypatch):
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text('{"type": "authorized_user"}')
    class MockCreds:
        valid = True
        token = "mock-file-token"
        project_id = "file-project-id"
    monkeypatch.setattr("google.auth.load_credentials_from_file", lambda path, scopes=None: (MockCreds(), "file-project-id"))
    t = VertexAiTranscriber(credentials_path=str(cred_file))
    t.discover_project()
    assert t.project == "file-project-id"

def test_vertexai_auth_transport_error_retryable():
    import google.auth.exceptions
    class FailingCreds:
        valid = False
        token = None
        def refresh(self, request):
            raise google.auth.exceptions.TransportError("auth network timed out")
    t = VertexAiTranscriber(project="p1")
    t._credentials = FailingCreds()
    with pytest.raises(ProviderError) as exc:
        t.get_auth_headers()
    assert exc.value.retryable is True
    assert "ASR_AUTH_NETWORK_ERROR" in str(exc.value)

def test_vertexai_auth_invalid_credentials_not_retryable():
    import google.auth.exceptions
    class BadCreds:
        valid = False
        token = None
        def refresh(self, request):
            raise google.auth.exceptions.RefreshError("invalid_grant: Bad client")
    t = VertexAiTranscriber(project="p1")
    t._credentials = BadCreds()
    with pytest.raises(ProviderBlocked, match="VERTEX_CREDENTIALS_MISSING"):
        t.get_auth_headers()

