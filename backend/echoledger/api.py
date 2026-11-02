from __future__ import annotations
import asyncio, contextlib, hashlib, json, secrets, shutil, threading
from pathlib import Path
from fastapi import FastAPI, APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from .config import Settings
from .db import Database, emit, now_ms, uid
from .audio import atomic_write, read_wav, SAMPLE_RATE
from .models import (SessionCreate, SessionClose, ChunkMeta, ProfileCreate, EvaluationRequest,
                     SpeakerCreate, SpeakerAssign, VoiceprintCreate, VoiceMatch, EventTypeCreate,
                     EventPatch, MemoryPatch, VadConfig)
from .pipeline import Pipeline, plan_batches
from .worker import run_loop
from .queries import list_events, get_event, search_memories
from .speakers import normalize, match_speaker
from .vad import speech_intervals, score_intervals
from . import mcp


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.validate()
    db = Database(settings.data_dir)
    pipeline = Pipeline(db, settings)
    stop = threading.Event()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        thread = None
        if settings.run_worker:
            thread = threading.Thread(target=run_loop, args=(pipeline, stop), name="audio-worker", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            await asyncio.to_thread(thread.join, 5)

    app = FastAPI(title="EchoLedger API", version="0.1.0", lifespan=lifespan,
                  description="Single-owner audio journal. All v1 routes require a Bearer token.",
                  docs_url="/docs", redoc_url=None)
    app.state.db, app.state.pipeline, app.state.settings = db, pipeline, settings

    def authorize(authorization: str | None = Header(default=None)):
        expected = "Bearer " + settings.api_token
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    def get_session(session_id):
        row = db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
        if not row:
            if db.one("SELECT id FROM deleted_sessions WHERE id=?", (session_id,)):
                raise HTTPException(410, "Session was deleted; do not upload it again")
            raise HTTPException(404, "Session not found")
        return row

    @app.get("/healthz")
    def health():
        return {"ok": True, "version": "0.1.0"}

    web_root = Path(__file__).resolve().parents[2] / "web"
    if (web_root / "index.html").exists():
        @app.get("/", include_in_schema=False)
        def console():
            return FileResponse(web_root / "index.html", headers={"Cache-Control": "no-store"})

    router = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])

    @router.get("/status")
    def status():
        counts = db.all("SELECT state,COUNT(*) AS count FROM batches GROUP BY state")
        latest = db.one("SELECT COALESCE(MAX(cursor),0) AS cursor FROM changes")
        return {"version": "0.1.0", "asr_provider": settings.asr_provider,
                "asr_configured": settings.asr_provider == "mock" or (settings.asr_provider == "gemini" and bool(settings.gemini_key)) or (settings.asr_provider == "vertexai" and bool(settings.vertex_project or settings.vertex_api_key or settings.vertex_credentials or settings.vertex_endpoint or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or getattr(pipeline.transcriber, "project", ""))),
                "vad_provider": settings.vad_provider, "voice_embedding_provider": "not_configured",
                "speaker_identity": "manual-or-external-embedding", "memory_mode": "evidence-excerpts-with-human-confirmation",
                "mcp_enabled": bool(settings.mcp_token), "batch_counts": counts, "cursor": latest["cursor"],
                "raw_audio_policy": "retained-until-explicit-delete", "is_demo": settings.asr_provider == "mock"}

    @router.post("/sessions")
    def create_session(body: SessionCreate):
        with db.connect(immediate=True) as c:
            if c.execute("SELECT 1 FROM deleted_sessions WHERE id=?", (body.id,)).fetchone():
                raise HTTPException(410, "Session was deleted")
            previous = c.execute("SELECT * FROM sessions WHERE id=?", (body.id,)).fetchone()
            if previous:
                if any(previous[k] != v for k, v in body.model_dump().items()):
                    raise HTTPException(409, "Session ID already has different metadata")
                return dict(previous)
            c.execute("""INSERT INTO sessions(id,started_at_ms,timezone_offset_min,purpose,device_id,created_ms)
                VALUES(?,?,?,?,?,?)""", (body.id, body.started_at_ms, body.timezone_offset_min, body.purpose, body.device_id, now_ms()))
            emit(c, "session.created", body.id)
        return get_session(body.id)

    @router.get("/sessions")
    def sessions(limit: int = Query(100, ge=1, le=500)):
        return db.all("""SELECT s.*, (SELECT COUNT(*) FROM chunks c WHERE c.session_id=s.id) AS chunks_received
            FROM sessions s ORDER BY started_at_ms DESC LIMIT ?""", (limit,))

    @router.post("/sessions/{session_id}/close")
    def close_session(session_id: str, body: SessionClose):
        get_session(session_id)
        with db.connect(immediate=True) as c:
            old = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if old["state"] == "stopped" and old["final_seq"] != body.final_seq:
                raise HTTPException(409, "final_seq is immutable after close")
            maximum = c.execute("SELECT MAX(seq) FROM chunks WHERE session_id=?", (session_id,)).fetchone()[0]
            if maximum is not None and maximum > body.final_seq:
                raise HTTPException(409, "final_seq is earlier than an uploaded chunk")
            c.execute("UPDATE sessions SET state='stopped',final_seq=?,interrupted=? WHERE id=?",
                      (body.final_seq, int(body.interrupted), session_id))
            emit(c, "session.stopped", session_id)
        plan_batches(db, settings)
        return get_session(session_id)

    def store_chunk(session_id: str, seq: int, meta: ChunkMeta, content: bytes):
        if seq < 0 or seq > 1000000:
            raise HTTPException(422, "Invalid sequence")
        if hashlib.sha256(content).hexdigest() != meta.sha256:
            raise HTTPException(422, "SHA-256 mismatch")
        try:
            audio = read_wav(content)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if len(audio) != meta.sample_count:
            raise HTTPException(422, "WAV length does not match sample_count")
        with db.connect(immediate=True) as c:
            session = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session:
                raise HTTPException(404, "Session not found")
            if session["final_seq"] is not None and seq > session["final_seq"]:
                raise HTTPException(409, "Chunk is beyond final_seq")
            previous = c.execute("SELECT * FROM chunks WHERE session_id=? AND seq=?", (session_id, seq)).fetchone()
            if previous:
                if previous["metadata"] != meta.model_dump_json():
                    raise HTTPException(409, "Chunk sequence already contains different content or metadata")
                return {"id": previous["id"], "seq": seq, "duplicate": True}
            if c.execute("SELECT 1 FROM chunks WHERE id=?", (meta.id,)).fetchone():
                raise HTTPException(409, "Chunk ID already exists")
            if seq == 0 and meta.start_sample != 0:
                raise HTTPException(422, "First chunk must start at sample zero")
            before = c.execute("SELECT * FROM chunks WHERE session_id=? AND seq=?", (session_id, seq - 1)).fetchone()
            after = c.execute("SELECT * FROM chunks WHERE session_id=? AND seq=?", (session_id, seq + 1)).fetchone()
            if before and before["start_sample"] + before["sample_count"] != meta.start_sample:
                raise HTTPException(409, "Non-contiguous sample clock; start a new session after a capture gap")
            if after and meta.start_sample + meta.sample_count != after["start_sample"]:
                raise HTTPException(409, "Non-contiguous sample clock")
            path = settings.data_dir / "audio" / session_id / f"{seq:08d}.wav"
            atomic_write(path, content)
            c.execute("""INSERT INTO chunks(id,session_id,seq,start_sample,sample_count,sha256,path,metadata,created_ms)
                VALUES(?,?,?,?,?,?,?,?,?)""", (meta.id, session_id, seq, meta.start_sample, meta.sample_count,
                meta.sha256, str(path), meta.model_dump_json(), now_ms()))
            emit(c, "chunk.received", meta.id)
        plan_batches(db, settings)
        return {"id": meta.id, "seq": seq, "duplicate": False}

    @router.post("/sessions/{session_id}/chunks/{seq}")
    async def upload_chunk(session_id: str, seq: int, request: Request,
                           metadata: str = Header(alias="X-Chunk-Metadata")):
        get_session(session_id)
        try:
            if len(metadata) > 4096:
                raise ValueError("Metadata too large")
            meta = ChunkMeta.model_validate_json(metadata)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, "Invalid X-Chunk-Metadata JSON") from exc
        content = bytearray()
        async for part in request.stream():
            content.extend(part)
            if len(content) > settings.max_upload_bytes:
                raise HTTPException(413, "Chunk is too large")
        return await run_in_threadpool(store_chunk, session_id, seq, meta, bytes(content))

    @router.get("/sessions/{session_id}/chunks")
    def list_chunks(session_id: str):
        get_session(session_id)
        return db.all("""SELECT c.id,c.seq,c.start_sample,c.sample_count,c.sha256,c.batch_id,b.state AS processing_state
            FROM chunks c LEFT JOIN batches b ON b.id=c.batch_id WHERE c.session_id=? ORDER BY c.seq""", (session_id,))

    @router.get("/chunks/{chunk_id}/audio")
    def chunk_audio(chunk_id: str):
        chunk = db.one("SELECT * FROM chunks WHERE id=?", (chunk_id,))
        if not chunk:
            raise HTTPException(404, "Chunk not found")
        return FileResponse(chunk["path"], media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @router.post("/sessions/{session_id}/flush")
    def flush(session_id: str):
        get_session(session_id)
        return {"queued": plan_batches(db, settings, force_session=session_id)}

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: str):
        with db.connect(immediate=True) as c:
            if not c.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                if c.execute("SELECT 1 FROM deleted_sessions WHERE id=?", (session_id,)).fetchone():
                    return {"deleted": True}
                raise HTTPException(404, "Session not found")
            c.execute("INSERT INTO deleted_sessions VALUES(?,?)", (session_id, now_ms()))
            c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            emit(c, "session.deleted", session_id)
        shutil.rmtree(settings.data_dir / "audio" / session_id, ignore_errors=True)
        shutil.rmtree(settings.data_dir / "derived" / session_id, ignore_errors=True)
        return {"deleted": True, "note": "Active logical data removed. Encrypted backup lifecycle must be managed separately."}

    @router.get("/batches")
    def batches(session_id: str | None = None, limit: int = Query(100, ge=1, le=500)):
        rows = db.all("SELECT * FROM batches WHERE (? IS NULL OR session_id=?) ORDER BY created_ms DESC LIMIT ?",
                      (session_id, session_id, limit))
        for r in rows:
            r["metrics"] = json.loads(r["metrics"])
            r["chunk_ids"] = json.loads(r["chunk_ids"])
            r.pop("lease_token", None)
        return rows

    @router.post("/batches/{batch_id}/retry")
    def retry_batch(batch_id: str):
        with db.connect(immediate=True) as c:
            row = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Batch not found")
            if row["state"] not in {"blocked", "failed", "retry"}:
                raise HTTPException(409, "Only blocked/failed/retry batches may be retried")
            c.execute("UPDATE batches SET state='queued',attempts=0,error=NULL,available_ms=? WHERE id=?", (now_ms(), batch_id))
            emit(c, "batch.queued", batch_id)
        return {"queued": True}

    @router.get("/events")
    def events(from_ms: int = Query(0, ge=0), to_ms: int = Query(9_000_000_000_000_000, ge=0), limit: int = Query(100, ge=1, le=500)):
        return list_events(db, from_ms, to_ms, limit)

    @router.get("/events/{event_id}")
    def event(event_id: str):
        row = get_event(db, event_id)
        if row is None:
            raise HTTPException(404, "Event not found")
        return row

    @router.patch("/events/{event_id}")
    def patch_event(event_id: str, body: EventPatch):
        row = event(event_id)
        with db.connect(immediate=True) as c:
            type_id = body.type_id if body.type_id is not None else row["type_id"]
            if not c.execute("SELECT 1 FROM event_types WHERE id=?", (type_id,)).fetchone():
                raise HTTPException(422, "Unknown event type")
            c.execute("UPDATE events SET title=?,type_id=?,user_edited=1,updated_ms=? WHERE id=?",
                      (body.title if body.title is not None else row["title"], type_id, now_ms(), event_id))
            emit(c, "event.updated", event_id)
        return event(event_id)

    @router.get("/event-types")
    def event_types():
        rows = db.all("SELECT * FROM event_types ORDER BY id")
        for row in rows:
            row["keywords"] = json.loads(row["keywords"])
        return rows

    @router.put("/event-types/{type_id}")
    def save_event_type(type_id: str, body: EventTypeCreate):
        if body.id != type_id:
            raise HTTPException(422, "IDs differ")
        with db.connect(immediate=True) as c:
            c.execute("""INSERT INTO event_types VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE
                SET name=excluded.name,keywords=excluded.keywords,color=excluded.color""",
                (body.id, body.name, json.dumps(body.keywords), body.color))
            emit(c, "event_type.updated", body.id)
        return body

    @router.delete("/event-types/{type_id}")
    def remove_event_type(type_id: str):
        with db.connect(immediate=True) as c:
            if type_id == "unknown" or c.execute("SELECT 1 FROM events WHERE type_id=?", (type_id,)).fetchone():
                raise HTTPException(409, "Type is required or referenced by events")
            c.execute("DELETE FROM event_types WHERE id=?", (type_id,))
            emit(c, "event_type.deleted", type_id)
        return {"deleted": True}

    @router.get("/memories")
    def memories(q: str = Query("", max_length=200), confirmed_only: bool = True,
                 from_ms: int = Query(0, ge=0), to_ms: int = Query(9_000_000_000_000_000, ge=0), limit: int = Query(100, ge=1, le=200)):
        return search_memories(db, q, confirmed_only, from_ms, to_ms, limit)

    @router.patch("/memories/{memory_id}")
    def patch_memory(memory_id: str, body: MemoryPatch):
        with db.connect(immediate=True) as c:
            row = c.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Memory not found")
            c.execute("UPDATE memories SET text=?,confirmed=?,user_edited=1 WHERE id=?",
                      (body.text if body.text is not None else row["text"],
                       int(body.confirmed) if body.confirmed is not None else row["confirmed"], memory_id))
            emit(c, "memory.updated", memory_id)
        return db.one("SELECT * FROM memories WHERE id=?", (memory_id,))

    @router.delete("/memories/{memory_id}")
    def delete_memory(memory_id: str):
        with db.connect(immediate=True) as c:
            c.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            emit(c, "memory.deleted", memory_id)
        return {"deleted": True, "scope": "memory-only; source recording and event remain"}

    @router.get("/speakers")
    def speakers():
        return db.all("""SELECT p.*, (SELECT COUNT(*) FROM voiceprints v WHERE v.speaker_id=p.id) AS voiceprint_count
            FROM speakers p ORDER BY name""")

    @router.post("/speakers")
    def create_speaker(body: SpeakerCreate):
        speaker_id = uid()
        with db.connect(immediate=True) as c:
            c.execute("INSERT INTO speakers VALUES(?,?,?,?)", (speaker_id, body.name, body.notes, now_ms()))
            emit(c, "speaker.created", speaker_id)
        return db.one("SELECT * FROM speakers WHERE id=?", (speaker_id,))

    @router.put("/speakers/{speaker_id}")
    def update_speaker(speaker_id: str, body: SpeakerCreate):
        with db.connect(immediate=True) as c:
            n = c.execute("UPDATE speakers SET name=?,notes=? WHERE id=?", (body.name, body.notes, speaker_id)).rowcount
            if not n:
                raise HTTPException(404, "Speaker not found")
            emit(c, "speaker.updated", speaker_id)
        return db.one("SELECT * FROM speakers WHERE id=?", (speaker_id,))

    @router.delete("/speakers/{speaker_id}")
    def delete_speaker(speaker_id: str):
        with db.connect(immediate=True) as c:
            c.execute("DELETE FROM speakers WHERE id=?", (speaker_id,))
            emit(c, "speaker.deleted", speaker_id)
        return {"deleted": True}

    @router.put("/segments/{segment_id}/speaker")
    def assign_speaker(segment_id: str, body: SpeakerAssign, same_local_label: bool = True):
        with db.connect(immediate=True) as c:
            segment = c.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
            if not segment:
                raise HTTPException(404, "Segment not found")
            if body.speaker_id and not c.execute("SELECT 1 FROM speakers WHERE id=?", (body.speaker_id,)).fetchone():
                raise HTTPException(422, "Speaker not found")
            if same_local_label:
                c.execute("UPDATE segments SET speaker_id=? WHERE batch_id=? AND local_speaker=?",
                          (body.speaker_id, segment["batch_id"], segment["local_speaker"]))
            else:
                c.execute("UPDATE segments SET speaker_id=? WHERE id=?", (body.speaker_id, segment_id))
            emit(c, "event.updated", segment["batch_id"])
        return {"assigned": True, "scope": "batch-local-label" if same_local_label else "segment"}

    @router.post("/speakers/{speaker_id}/voiceprints")
    def enroll(speaker_id: str, body: VoiceprintCreate):
        if not body.consent:
            raise HTTPException(422, "Explicit speaker consent is required")
        try:
            vector = normalize(body.embedding).tolist()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        voice_id = uid()
        with db.connect(immediate=True) as c:
            if not c.execute("SELECT 1 FROM speakers WHERE id=?", (speaker_id,)).fetchone():
                raise HTTPException(404, "Speaker not found")
            if not c.execute("SELECT 1 FROM segments WHERE id=?", (body.evidence_segment_id,)).fetchone():
                raise HTTPException(422, "Evidence segment not found")
            c.execute("INSERT INTO voiceprints VALUES(?,?,?,?,?,?,?)", (voice_id, speaker_id, body.model_id,
                      json.dumps(vector), body.evidence_segment_id, 1, now_ms()))
            emit(c, "speaker.updated", speaker_id)
        return {"id": voice_id, "model_id": body.model_id, "dimension": len(vector), "source": "externally-computed-embedding"}

    @router.post("/speaker-matches")
    def match(body: VoiceMatch):
        try:
            return match_speaker(body.embedding, body.model_id, db.all("SELECT * FROM voiceprints"), body.threshold, body.margin)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/vad/profiles")
    def profiles():
        rows = db.all("SELECT * FROM profiles ORDER BY created_ms DESC")
        for row in rows:
            row["config"] = json.loads(row["config"])
        return rows

    @router.post("/vad/profiles")
    def save_profile(body: ProfileCreate):
        profile_id = uid()
        with db.connect(immediate=True) as c:
            if body.activate:
                c.execute("UPDATE profiles SET active=0 WHERE active=1")
            c.execute("INSERT INTO profiles VALUES(?,?,?,?,?)", (profile_id, body.name, body.config.model_dump_json(), now_ms(), int(body.activate)))
            emit(c, "profile.created", profile_id)
        return {"id": profile_id, **body.model_dump()}

    @router.post("/vad/profiles/{profile_id}/activate")
    def activate_profile(profile_id: str):
        with db.connect(immediate=True) as c:
            if not c.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
                raise HTTPException(404, "Profile not found")
            c.execute("UPDATE profiles SET active=0 WHERE active=1")
            c.execute("UPDATE profiles SET active=1 WHERE id=?", (profile_id,))
            emit(c, "profile.activated", profile_id)
        return {"active": profile_id, "applies_to": "new-batches-only"}

    def evaluate_input(body):
        chunk = db.one("SELECT * FROM chunks WHERE id=?", (body.chunk_id,))
        if not chunk:
            raise HTTPException(404, "Chunk not found")
        if not body.fully_labeled:
            raise HTTPException(422, "Label all speech intervals and confirm fully_labeled; unlabeled time is counted as non-speech")
        samples = read_wav(Path(chunk["path"]).read_bytes())
        if any(i.end > len(samples) for i in body.speech_intervals):
            raise HTTPException(422, "Labels exceed audio length")
        try:
            probs = pipeline.scorer.probabilities(samples)
        except Exception as exc:
            raise HTTPException(503, "VAD provider is unavailable: " + type(exc).__name__) from exc
        gold = [(i.start, i.end) for i in body.speech_intervals]
        return samples, probs, gold

    @router.post("/vad/evaluate")
    def evaluate(body: EvaluationRequest):
        samples, probs, gold = evaluate_input(body)
        predicted = speech_intervals(probs, len(samples), body.config)
        scores = score_intervals(predicted, gold, len(samples))
        result = {"metrics": scores, "predicted_intervals": predicted, "probabilities": probs.tolist(),
                  "frame_samples": 512, "model_id": pipeline.scorer.model_id,
                  "validated_for_deployment": False,
                  "warning": "One labeled clip measures only this clip. Use separate sessions and scenarios for held-out validation."}
        with db.connect(immediate=True) as c:
            c.execute("INSERT INTO calibrations VALUES(?,?,?,?,?,?,?)", (uid(), body.chunk_id, body.scenario,
                body.config.model_dump_json(), json.dumps(gold), json.dumps(result), now_ms()))
        return result

    @router.post("/vad/sweep")
    def sweep(body: EvaluationRequest):
        samples, probs, gold = evaluate_input(body)
        results = []
        for threshold in (.25, .35, .50):
            for pad in (200, 300, 500):
                for uncertain in (.08, .12, .20):
                    cfg = body.config.model_copy(update={"threshold": threshold, "negative_threshold": min(.20, threshold - .1),
                        "pad_ms": pad, "uncertainty_threshold": min(uncertain, threshold)})
                    pred = speech_intervals(probs, len(samples), cfg)
                    results.append({"config": cfg.model_dump(), "metrics": score_intervals(pred, gold, len(samples))})
        qualifying = [r for r in results if r["metrics"]["speech_recall"] is not None and r["metrics"]["speech_recall"] >= .99]
        qualifying.sort(key=lambda r: r["metrics"]["removed_fraction"], reverse=True)
        return {"recommended": qualifying[0] if qualifying else None, "candidates": results,
                "validated_for_deployment": False, "automatically_activated": False,
                "warning": "Exploratory fit to this clip only. Validate on held-out recordings before activating."}

    @router.get("/changes")
    def changes(after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        rows = db.all("SELECT * FROM changes WHERE cursor>? ORDER BY cursor LIMIT ?", (after, limit))
        return {"items": rows, "cursor": rows[-1]["cursor"] if rows else after, "has_more": len(rows) == limit}

    @router.get("/updates")
    async def updates(request: Request, after: int = Query(0, ge=0), last_id: str | None = Header(default=None, alias="Last-Event-ID")):
        try:
            cursor = max(after, int(last_id or "0"))
        except ValueError:
            raise HTTPException(422, "Invalid Last-Event-ID")
        async def stream():
            nonlocal cursor
            idle = 0
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(db.all, "SELECT * FROM changes WHERE cursor>? ORDER BY cursor LIMIT 200", (cursor,))
                for row in rows:
                    cursor = row["cursor"]
                    yield f"id: {cursor}\nevent: change\ndata: {json.dumps(row)}\n\n"
                idle += 1
                if not rows and idle % 15 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1 if not rows else .01)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/usage")
    def usage():
        return {"days": db.all("SELECT day,provider,SUM(audio_seconds) AS reserved_audio_seconds,COUNT(*) AS attempts FROM usage GROUP BY day,provider ORDER BY day DESC LIMIT 60"),
                "daily_limit_seconds": None, "basis": "UTC request-day, conservative pre-request reservation; not a billing invoice"}

    app.include_router(router)

    @app.post("/mcp", tags=["MCP"])
    async def mcp_http(request: Request):
        if not settings.mcp_token:
            raise HTTPException(404, "MCP disabled")
        auth = request.headers.get("Authorization", "")
        if not secrets.compare_digest(auth, "Bearer " + settings.mcp_token):
            raise HTTPException(401, "Invalid MCP read-only token")
        origin = request.headers.get("Origin")
        if origin and origin not in settings.allowed_origins:
            raise HTTPException(403, "Origin not allowed")
        protocol = request.headers.get("MCP-Protocol-Version")
        if protocol and protocol not in mcp.PROTOCOLS:
            raise HTTPException(400, "Unsupported MCP protocol version")
        content = bytearray()
        async for part in request.stream():
            content.extend(part)
            if len(content) > 128_000:
                raise HTTPException(413, "MCP request too large")
        try:
            body = json.loads(content)
        except (ValueError, UnicodeError):
            return JSONResponse(mcp.error(None, -32700, "Parse error"), status_code=400)
        result = await run_in_threadpool(mcp.handle, db, body)
        if result is None:
            return Response(status_code=202)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return app
