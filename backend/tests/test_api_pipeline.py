import hashlib, json
import numpy as np
import pytest
from echoledger.db import Database, now_ms
from echoledger.pipeline import plan_batches, claim_batch
from echoledger.providers import ProviderError, MockTranscriber
from conftest import create_session, upload, audio, finish, TOKEN, MCP_TOKEN


def test_authentication(client):
    assert client.get("/healthz",headers={"Authorization":""}).status_code == 200
    assert client.get("/v1/status",headers={"Authorization":""}).status_code == 401
    assert client.get("/v1/status",headers={"Authorization":"Bearer "+MCP_TOKEN}).status_code == 401
    assert client.get("/v1/status").json()["is_demo"] is True

def test_idempotent_session(client):
    sid = create_session(client)
    create_session(client)
    assert len(client.get("/v1/sessions").json()) == 1
    assert client.post("/v1/sessions",json={"id":sid,"started_at_ms":1}).status_code == 409

def test_upload_idempotency(client):
    sid=create_session(client)
    assert upload(client,sid).json()["duplicate"] is False
    assert upload(client,sid).json()["duplicate"] is True
    assert upload(client,sid,samples=audio(voiced=False)).status_code == 409

def test_checksum_and_length(client):
    sid=create_session(client)
    r=client.post(f"/v1/sessions/{sid}/chunks/0",content=b"bad",headers={"X-Chunk-Metadata":json.dumps({
        "id":"chunk-test-0001","start_sample":0,"sample_count":1,"sha256":"0"*64})})
    assert r.status_code==422

def test_bad_metadata(client):
    sid=create_session(client)
    r=client.post(f"/v1/sessions/{sid}/chunks/0",content=b"",headers={"X-Chunk-Metadata":"{}"})
    assert r.status_code==422

def test_nonzero_first_sample_rejected(client):
    sid=create_session(client)
    assert upload(client,sid,start_sample=100).status_code==422

def test_sample_clock_gap_rejected(client):
    sid=create_session(client)
    assert upload(client,sid).status_code==200
    assert upload(client,sid,seq=1,start_sample=20000).status_code==409

def test_out_of_order_waits_for_missing_chunk(client,app):
    sid=create_session(client)
    assert upload(client,sid,seq=1).status_code==200
    assert client.post(f"/v1/sessions/{sid}/flush").json()["queued"]==[]
    assert upload(client,sid,seq=0).status_code==200
    assert len(client.post(f"/v1/sessions/{sid}/flush").json()["queued"])==1
    assert app.state.pipeline.tick()
    assert client.get("/v1/batches").json()[0]["state"]=="done"

def test_close_before_upload_and_final_sequence(client):
    sid=create_session(client)
    assert client.post(f"/v1/sessions/{sid}/close",json={"final_seq":0}).status_code==200
    assert upload(client,sid).status_code==200
    assert upload(client,sid,seq=1).status_code==409
    assert client.post(f"/v1/sessions/{sid}/close",json={"final_seq":1}).status_code==409

def test_pipeline_evidence_and_memory_confirmation(client,app):
    events=finish(client,app)
    assert len(events)==1 and events[0]["is_demo"]==1
    detail=client.get("/v1/events/"+events[0]["id"]).json()
    assert detail["segments"][0]["source_spans"][0]["chunk_id"]
    assert client.get("/v1/memories").json()==[]
    candidates=client.get("/v1/memories?confirmed_only=false").json()
    assert len(candidates)==1
    assert client.patch("/v1/memories/"+candidates[0]["id"],json={"confirmed":True}).status_code==200
    assert len(client.get("/v1/memories").json())==1

def test_silent_audio_has_no_hallucinated_event(client,app):
    assert finish(client,app,samples=audio(voiced=False))==[]
    batch=client.get("/v1/batches").json()[0]
    assert batch["state"]=="done" and batch["metrics"]["packed_samples"]==0
    assert client.get("/v1/usage").json()["days"]==[]

def test_batch_not_reprocessed(client,app):
    finish(client,app)
    assert not app.state.pipeline.tick()
    assert len(client.get("/v1/events").json())==1

def test_job_retry(client,app):
    class FailOnce:
        calls=0
        def transcribe(self,samples,language="zh"):
            self.calls+=1
            if self.calls==1: raise ProviderError("ASR_HTTP_429")
            return MockTranscriber().transcribe(samples)
    app.state.pipeline.transcriber=FailOnce()
    finish(client,app)
    batch=client.get("/v1/batches").json()[0]
    assert batch["state"]=="retry"
    assert client.post("/v1/batches/"+batch["id"]+"/retry").status_code==200
    assert app.state.pipeline.tick()
    assert len(client.get("/v1/events").json())==1

def test_lease_cannot_be_claimed_twice(client,app):
    sid=create_session(client)
    upload(client,sid)
    client.post(f"/v1/sessions/{sid}/flush")
    assert claim_batch(app.state.db)
    assert claim_batch(app.state.db) is None

def test_profile_snapshot_is_immutable_for_queued_job(client,app):
    sid=create_session(client)
    upload(client,sid)
    client.post(f"/v1/sessions/{sid}/flush")
    old=client.get("/v1/batches").json()[0]["profile_id"]
    new=client.post("/v1/vad/profiles",json={"name":"New","activate":True,"config":{"threshold":.5}}).json()
    assert new["id"]!=old
    assert client.get("/v1/batches").json()[0]["profile_id"]==old
    assert sum(p["active"] for p in client.get("/v1/vad/profiles").json())==1

def test_calibration_never_enters_asr(client,app):
    sid=create_session(client,purpose="calibration")
    upload(client,sid)
    client.post(f"/v1/sessions/{sid}/close",json={"final_seq":0})
    assert client.get("/v1/batches").json()==[]
    result=client.post("/v1/vad/evaluate",json={"chunk_id":sid+"-chunk-000","speech_intervals":[{"start":0,"end":16000}],"fully_labeled":True})
    assert result.status_code==200,result.text
    assert result.json()["metrics"]["speech_recall"]==1
    assert result.json()["validated_for_deployment"] is False

def test_calibration_requires_annotation(client):
    sid=create_session(client,purpose="calibration")
    upload(client,sid)
    assert client.post("/v1/vad/evaluate",json={"chunk_id":sid+"-chunk-000","speech_intervals":[]}).status_code==422
    assert client.post("/v1/vad/evaluate",json={"chunk_id":sid+"-chunk-000","speech_intervals":[{"start":0,"end":999999}],"fully_labeled":True}).status_code==422

def test_sweep_never_autoactivates(client):
    sid=create_session(client,purpose="calibration")
    upload(client,sid)
    r=client.post("/v1/vad/sweep",json={"chunk_id":sid+"-chunk-000","speech_intervals":[{"start":0,"end":16000}],"fully_labeled":True})
    assert r.status_code==200,r.text
    assert len(r.json()["candidates"])==27
    assert r.json()["automatically_activated"] is False
    assert len(client.get("/v1/vad/profiles").json())==1

def test_delete_cascades_and_tombstone(client,app):
    events=finish(client,app)
    sid="session-00000001"
    root=app.state.settings.data_dir
    assert (root/"audio"/sid).exists()
    assert client.delete("/v1/sessions/"+sid).status_code==200
    assert client.get("/v1/events").json()==[]
    assert client.get("/v1/memories?confirmed_only=false").json()==[]
    assert not (root/"audio"/sid).exists()
    assert client.post("/v1/sessions",json={"id":sid,"started_at_ms":1730000000000}).status_code==410
    assert client.delete("/v1/sessions/"+sid).status_code==200
    # Aggregate request budget cannot be reset by deleting source recordings.
    assert client.get("/v1/usage").json()["days"]

def test_changes_cursor(client,app):
    finish(client,app)
    a=client.get("/v1/changes?after=0&limit=2").json()
    assert len(a["items"])==2 and a["has_more"]
    b=client.get("/v1/changes",params={"after":a["cursor"]}).json()
    assert all(x["cursor"]>a["cursor"] for x in b["items"])

def test_sql_wildcards_are_literal(client,app):
    finish(client,app)
    mem=client.get("/v1/memories?confirmed_only=false").json()[0]
    client.patch("/v1/memories/"+mem["id"],json={"confirmed":True,"text":"hello world"})
    assert client.get("/v1/memories",params={"q":"%"}).json()==[]
    assert client.get("/v1/memories",params={"q":"' OR 1=1 --"}).json()==[]

def test_speaker_binding_scope(client,app):
    events=finish(client,app)
    detail=client.get("/v1/events/"+events[0]["id"]).json()
    person=client.post("/v1/speakers",json={"name":"Alice"}).json()
    r=client.put("/v1/segments/"+detail["segments"][0]["id"]+"/speaker",json={"speaker_id":person["id"]})
    assert r.status_code==200
    assert client.get("/v1/events").json()[0]["speaker_names"]==["Alice"]
    client.delete("/v1/speakers/"+person["id"])
    assert client.get("/v1/events").json()[0]["speaker_names"]==[]

def test_event_type_and_edit(client,app):
    eid=finish(client,app)[0]["id"]
    assert client.put("/v1/event-types/custom",json={"id":"custom","name":"Custom","keywords":["test"]}).status_code==200
    assert client.patch("/v1/events/"+eid,json={"title":"Renamed","type_id":"custom"}).status_code==200
    assert client.delete("/v1/event-types/custom").status_code==409
    assert client.delete("/v1/event-types/unknown").status_code==409

def test_persistence(client,app):
    finish(client,app)
    reopened=Database(app.state.settings.data_dir)
    assert reopened.one("SELECT COUNT(*) AS n FROM events")["n"]==1
    assert len(reopened.all("SELECT * FROM profiles"))==1

def test_unlimited_budget_accumulates_usage(client,app):
    events = finish(client, app, sid="session-00000001")
    assert len(events) == 1
    events2 = finish(client, app, sid="session-00000002")
    assert len(events2) == 2
    usage = client.get("/v1/usage").json()
    assert usage["daily_limit_seconds"] is None
    assert len(usage["days"]) >= 1
    assert usage["days"][0]["reserved_audio_seconds"] > 0
    batches = client.get("/v1/batches").json()
    assert all(b.get("error") != "DAILY_AUDIO_BUDGET_REACHED" for b in batches)


@pytest.mark.parametrize("field,value", [("api_token", "REPLACE_WITH_RANDOM_API_TOKEN"), ("mcp_token", "REPLACE_WITH_DISTINCT_RANDOM_MCP_TOKEN")])
def test_example_secrets_are_rejected(tmp_path, field, value):
    from echoledger.config import Settings
    kwargs = {"data_dir": tmp_path, "api_token": TOKEN, "mcp_token": MCP_TOKEN}
    kwargs[field] = value
    with pytest.raises(ValueError):
        Settings(**kwargs).validate()
