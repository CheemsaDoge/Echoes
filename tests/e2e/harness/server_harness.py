"""Server Test Harness implementing the authoritative Hemory System Specification.
Covers 4-tier tenancy, single primary lease, multipart upload, SSE chat turns,
voiceprint clusters, speaker profiles, and listening schedules.
"""

from __future__ import annotations
import asyncio
import datetime
import json
import secrets
import time
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Depends, Header, HTTPException, Request, Response, status, Body
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .audio_harness import validate_adts_stream


# Top-level Pydantic Request Models (required for Python 3.12 / Pydantic v2 type adapter resolution)
class PrimaryDeviceRequest(BaseModel):
    dp_instance_id: str
    vault_id: str


class ChatTurnRequest(BaseModel):
    client_turn_id: str | None = None
    message: str
    active_memory_window: dict[str, Any] | None = None


class SpeakerCreate(BaseModel):
    name: str
    role_label: str | None = None
    cluster_id: str | None = None


class ClusterPatchRequest(BaseModel):
    action: str  # "mark_new", "mark_existing", "dismiss"
    name: str | None = None
    notes: str | None = None
    speaker_id: str | None = None


class ScheduleUpdateRequest(BaseModel):
    revision: int
    timezone: str
    weekly_windows: list[dict[str, Any]]


# In-memory database store for test isolation
class MemoryStore:
    def __init__(self):
        self.reset()

    def reset(self):
        self.tenants: dict[str, dict[str, Any]] = {}
        self.vaults: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.dp_instances: dict[str, dict[str, Any]] = {}
        self.recording_sessions: dict[str, dict[str, Any]] = {}
        self.recording_segments: dict[str, dict[str, Any]] = {}
        self.speaker_profiles: dict[str, dict[str, Any]] = {}
        self.voiceprint_clusters: dict[str, dict[str, Any]] = {}
        self.section_messages: dict[str, dict[str, Any]] = {}
        self.listening_schedules: dict[str, dict[str, Any]] = {}
        self.auth_tokens: dict[str, dict[str, Any]] = {}
        self.turn_replay_buffer: dict[str, list[dict[str, Any]]] = {}  # {turn_id: list of events}
        self.active_turn_cancellations: set[str] = set()
        self.processing_chains: dict[str, dict[str, Any]] = {}
        self.recording_mutations: list[dict[str, Any]] = []

    def seed_default_hierarchy(
        self,
        tenant_id: str = "tenant_alpha",
        vault_id: str = "vault_primary",
        user_id: str = "user_001",
        dp_instance_id: str = "dp_phone_001",
    ):
        self.tenants[tenant_id] = {"tenant_id": tenant_id, "name": "Alpha Corp"}
        self.vaults[vault_id] = {"vault_id": vault_id, "tenant_id": tenant_id, "name": "Primary Vault"}
        self.users[user_id] = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "vault_id": vault_id,
            "email": "user@alpha.com",
        }
        self.dp_instances[dp_instance_id] = {
            "dp_instance_id": dp_instance_id,
            "tenant_id": tenant_id,
            "vault_id": vault_id,
            "user_id": user_id,
            "is_primary": True,
            "device_name": "Pixel 8 Pro",
        }
        # Auth token mapping
        token = "token_alpha_user_001"
        self.auth_tokens[token] = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "vault_id": vault_id,
            "dp_instance_id": dp_instance_id,
        }
        # Default listening schedule: revision 1, 24/7 allowed
        self.listening_schedules[user_id] = {
            "user_id": user_id,
            "revision": 1,
            "timezone": "UTC",
            "weekly_windows": [
                {"weekday": i, "window_start": "00:00", "window_end": "23:59", "start_minutes": 0, "end_minutes": 1439}
                for i in range(1, 8)
            ],
        }
        return token


def create_e2e_server_app(store: MemoryStore | None = None) -> FastAPI:
    if store is None:
        store = MemoryStore()
        store.seed_default_hierarchy()

    app = FastAPI(title="Hemory E2E Spec Server", version="1.0.0-PROD")
    app.state.store = store

    def authenticate_bearer(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
        token = authorization[7:].strip()
        auth_info = store.auth_tokens.get(token)
        if not auth_info:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        return auth_info

    # 1. Tenancy endpoints
    @app.get("/tenants/{tenant_id}")
    def get_tenant(tenant_id: str, auth: dict = Depends(authenticate_bearer)):
        if auth["tenant_id"] != tenant_id:
            raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")
        tenant = store.tenants.get(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return tenant

    @app.get("/tenants/{tenant_id}/vaults/{vault_id}")
    def get_vault(tenant_id: str, vault_id: str, auth: dict = Depends(authenticate_bearer)):
        if auth["tenant_id"] != tenant_id or auth["vault_id"] != vault_id:
            raise HTTPException(status_code=403, detail="Cross-vault access forbidden")
        vault = store.vaults.get(vault_id)
        if not vault or vault["tenant_id"] != tenant_id:
            raise HTTPException(status_code=404, detail="Vault not found")
        return vault

    # 2. Primary Device Lease Arbitration
    @app.put("/primary-device")
    def set_primary_device(body: PrimaryDeviceRequest, auth: dict = Depends(authenticate_bearer)):
        vault_id = body.vault_id
        dp_instance_id = body.dp_instance_id

        if auth["vault_id"] != vault_id:
            raise HTTPException(status_code=403, detail="Cannot alter lease of foreign vault")

        dp = store.dp_instances.get(dp_instance_id)
        if not dp or dp["vault_id"] != vault_id:
            raise HTTPException(status_code=404, detail="DP instance not found in specified vault")

        # Atomic transaction: Demote all other DP instances in the vault
        for d_id, instance in store.dp_instances.items():
            if instance["vault_id"] == vault_id:
                instance["is_primary"] = (d_id == dp_instance_id)

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return {
            "dp_instance_id": dp_instance_id,
            "is_primary": True,
            "effective_from": now_iso,
        }

    # 3. 6-Field Multipart Audio Upload Endpoint
    @app.post("/tenants/{tenant_id}/vaults/{vault_id}/recordings/upload", status_code=201)
    async def upload_recording(
        tenant_id: str,
        vault_id: str,
        request: Request,
        auth: dict = Depends(authenticate_bearer),
    ):
        # Micro-tenancy isolation check
        if auth["tenant_id"] != tenant_id or auth["vault_id"] != vault_id:
            raise HTTPException(status_code=403, detail="Access denied: micro-isolation breach")

        form = await request.form()

        # Validate presence of 6 required fields
        required_fields = ["started_at", "format", "source", "capture_timezone", "capture_metadata", "file"]
        for field in required_fields:
            if field not in form:
                raise HTTPException(status_code=422, detail=f"Missing required multipart field: {field}")

        started_at_str = form["started_at"]
        fmt = form["format"]
        source = form["source"]
        tz = form["capture_timezone"]
        metadata_str = form["capture_metadata"]
        file_field = form["file"]

        if fmt != "aac":
            raise HTTPException(status_code=422, detail=f"Invalid format: expected 'aac', got '{fmt}'")

        try:
            metadata = json.loads(metadata_str)
        except Exception:
            raise HTTPException(status_code=422, detail="capture_metadata must be valid JSON")

        uploading_dp_id = metadata.get("dp_instance_id") or auth.get("dp_instance_id")

        # Lease checks
        vault_dps = [d for d in store.dp_instances.values() if d["vault_id"] == vault_id]
        primary_dps = [d for d in vault_dps if d["is_primary"]]
        if not primary_dps:
            return JSONResponse(
                status_code=403,
                content={
                    "error_code": "recording.upload_rejected_no_primary",
                    "message": "No primary device active in vault",
                },
            )

        active_primary = primary_dps[0]
        if uploading_dp_id and uploading_dp_id != active_primary["dp_instance_id"]:
            return JSONResponse(
                status_code=409,
                content={
                    "error_code": "recording.upload_rejected_not_primary",
                    "message": f"Device {uploading_dp_id} is not the primary recording device",
                    "primary_dp_instance_id": active_primary["dp_instance_id"],
                },
            )

        # 2. Timestamp clock drift verification
        try:
            started_at = datetime.datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid ISO 8601 started_at timestamp")

        now = datetime.datetime.now(datetime.timezone.utc)
        drift_seconds = (started_at - now).total_seconds()
        # >30s ahead
        if drift_seconds > 30.0:
            return JSONResponse(
                status_code=409,
                content={
                    "error_code": "recording.upload_rejected_invalid_time",
                    "message": f"Clock drift: timestamp is {drift_seconds:.1f}s in the future (>30s)",
                },
            )
        # >7 days stale
        if (now - started_at).total_seconds() > 7 * 86400:
            return JSONResponse(
                status_code=409,
                content={
                    "error_code": "recording.upload_rejected_invalid_time",
                    "message": "Timestamp is stale (> 7 days)",
                },
            )

        # 3. Listening schedule window check
        user_id = auth["user_id"]
        sched = store.listening_schedules.get(user_id)
        if sched and sched.get("weekly_windows"):
            weekday = started_at.isoweekday()  # 1=Mon, 7=Sun
            minute_of_day = started_at.hour * 60 + started_at.minute
            in_window = any(
                w["weekday"] == weekday and w["start_minutes"] <= minute_of_day <= w["end_minutes"]
                for w in sched["weekly_windows"]
            )
            if not in_window:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error_code": "recording.upload_rejected_out_of_primary_window",
                        "message": "Upload outside configured listening schedule window",
                    },
                )

        # 4. Audio byte validation (MPEG-4 AAC with ADTS 7-byte header)
        audio_bytes = await file_field.read()
        is_valid_adts, err, frames = validate_adts_stream(audio_bytes)
        if not is_valid_adts:
            return JSONResponse(
                status_code=400,
                content={
                    "error_code": "INVALID_AUDIO_STREAM",
                    "message": f"Audio file is not a valid ADTS AAC stream: {err}",
                },
            )

        # Persist segment record in memory
        rec_id = metadata.get("recording_id", f"rec_{secrets.token_hex(8)}")
        seg_id = f"seg_{secrets.token_hex(8)}"
        store.recording_segments[seg_id] = {
            "segment_id": seg_id,
            "recording_id": rec_id,
            "vault_id": vault_id,
            "byte_size": len(audio_bytes),
            "frame_count": len(frames),
            "started_at": started_at_str,
            "metadata": metadata,
        }

        # Create processing chain
        chain_id = f"chn_{seg_id.removeprefix('seg_')}"
        store.processing_chains[chain_id] = {
            "chain_id": chain_id,
            "recording_id": rec_id,
            "live_session_id": metadata.get("live_session_id"),
            "tenant_id": tenant_id,
            "vault_id": vault_id,
            "status": "pending",
            "current_step": "slicing",
            "progress_pct": 10.0,
            "analyzing_start_at": started_at_str,
            "analyzing_end_at": None,
            "started_at": started_at_str,
            "updated_at": started_at_str,
        }

        # Append mutation
        cursor = len(store.recording_mutations) + 1
        store.recording_mutations.append({
            "cursor": cursor,
            "id": cursor,
            "tenant_id": tenant_id,
            "vault_id": vault_id,
            "entity_type": "segment",
            "entity_id": seg_id,
            "mutation_type": "segment_uploaded",
            "payload": {
                "recording_id": rec_id,
                "live_session_id": metadata.get("live_session_id"),
                "segment_id": seg_id,
                "storage_key": f"recordings/{vault_id}/{rec_id}/{seg_id}.aac",
            },
            "created_at": started_at_str,
        })

        return {
            "recording_id": rec_id,
            "asset_id": f"ast_{secrets.token_hex(8)}",
            "artifact_id": f"art_{secrets.token_hex(8)}",
            "storage_key": f"recordings/{vault_id}/{rec_id}/{seg_id}.aac",
            "status": "queued_for_processing",
        }

    # 4. SSE Chat Turn Streaming Wire Protocol
    @app.post("/chat/threads/{thread_id}/turns")
    async def stream_chat_turn(
        thread_id: str,
        body: ChatTurnRequest,
        request: Request,
        auth: dict = Depends(authenticate_bearer),
    ):
        turn_id = body.client_turn_id or f"turn_{secrets.token_hex(8)}"
        last_event_id = request.headers.get("Last-Event-ID")

        # Prepare full event sequence
        events: list[dict[str, Any]] = [
            {
                "seq": 1,
                "event": "turn_progress",
                "data": {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "phase": "recalling",
                    "message": "Searching your memory timeline",
                },
            },
            {
                "seq": 2,
                "event": "tool_call_started",
                "data": {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "tool_call_id": f"tc_{secrets.token_hex(4)}",
                    "tool_name": "search_memory",
                    "arguments": {"query": body.message, "limit": 3},
                    "status": "running",
                },
            },
            {
                "seq": 3,
                "event": "tool_call_completed",
                "data": {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "tool_call_id": f"tc_{secrets.token_hex(4)}",
                    "tool_name": "search_memory",
                    "status": "completed",
                    "result": {"matches_count": 1, "snippet": f"Found transcript matching '{body.message}'"},
                    "elapsed_ms": 120,
                },
            },
            {
                "seq": 4,
                "event": "turn_progress",
                "data": {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "phase": "summarizing",
                    "message": "Formulating episodic response",
                },
            },
            {
                "seq": 5,
                "event": "text_delta",
                "data": {
                    "turn_id": turn_id,
                    "delta": "Based on your ambient audio journal, ",
                },
            },
            {
                "seq": 6,
                "event": "text_delta",
                "data": {
                    "turn_id": turn_id,
                    "delta": "the requested memory was successfully indexed.",
                },
            },
            {
                "seq": 7,
                "event": "turn_completed",
                "data": {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "status": "completed",
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                },
            },
        ]

        # Store in circular replay buffer
        store.turn_replay_buffer[turn_id] = events

        start_seq = 0
        if last_event_id and last_event_id.startswith(f"{turn_id}_"):
            try:
                start_seq = int(last_event_id.split("_")[-1])
            except ValueError:
                start_seq = 0

        async def sse_generator() -> AsyncGenerator[str, None]:
            for ev in events:
                if ev["seq"] <= start_seq:
                    continue
                # Check cancellation
                if turn_id in store.active_turn_cancellations:
                    fail_ev = {
                        "id": f"{turn_id}_{ev['seq']:03d}",
                        "event": "turn_failed",
                        "data": json.dumps({
                            "thread_id": thread_id,
                            "turn_id": turn_id,
                            "error_code": "CANCELLED",
                            "turn_error_kind": "cancelled",
                            "message": "Turn was cancelled by client",
                        }),
                    }
                    yield f"id: {fail_ev['id']}\nevent: {fail_ev['event']}\ndata: {fail_ev['data']}\n\n"
                    break

                eid = f"{turn_id}_{ev['seq']:03d}"
                event_type = ev["event"]
                data_str = json.dumps(ev["data"])
                yield f"id: {eid}\nevent: {event_type}\ndata: {data_str}\n\n"
                await asyncio.sleep(0.001)

        return StreamingResponse(sse_generator(), media_type="text/event-stream")

    @app.post("/chat/threads/{thread_id}/turns/{turn_id}/cancel")
    def cancel_chat_turn(thread_id: str, turn_id: str, auth: dict = Depends(authenticate_bearer)):
        store.active_turn_cancellations.add(turn_id)
        return {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "status": "cancelled",
            "cancelled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    # 5. Voiceprint Clusters & Speakers Endpoints
    @app.get("/speakers")
    def list_speakers(auth: dict = Depends(authenticate_bearer)):
        vault_id = auth["vault_id"]
        speakers = [s for s in store.speaker_profiles.values() if s.get("vault_id") == vault_id]
        return {"speakers": speakers}

    @app.post("/speakers", status_code=201)
    def create_speaker(body: SpeakerCreate, auth: dict = Depends(authenticate_bearer)):
        speaker_id = f"spk_{secrets.token_hex(4)}"
        profile = {
            "speaker_id": speaker_id,
            "vault_id": auth["vault_id"],
            "name": body.name,
            "role_label": body.role_label,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        store.speaker_profiles[speaker_id] = profile
        return profile

    @app.get("/clusters")
    def list_clusters(auth: dict = Depends(authenticate_bearer)):
        vault_id = auth["vault_id"]
        clusters = [c for c in store.voiceprint_clusters.values() if c.get("vault_id") == vault_id]
        return {"clusters": clusters}

    @app.get("/clusters/{cluster_id}")
    def get_cluster(cluster_id: str, auth: dict = Depends(authenticate_bearer)):
        c = store.voiceprint_clusters.get(cluster_id)
        if not c or c.get("vault_id") != auth["vault_id"]:
            raise HTTPException(status_code=404, detail="Cluster not found")
        return c

    @app.patch("/clusters/{cluster_id}")
    def patch_cluster(cluster_id: str, body: ClusterPatchRequest, auth: dict = Depends(authenticate_bearer)):
        vault_id = auth["vault_id"]
        cluster = store.voiceprint_clusters.get(cluster_id)
        if not cluster or cluster.get("vault_id") != vault_id:
            raise HTTPException(status_code=404, detail="Cluster not found")

        if body.action == "mark_new":
            if not body.name:
                raise HTTPException(status_code=422, detail="Name required for mark_new")
            new_speaker_id = f"spk_{secrets.token_hex(4)}"
            store.speaker_profiles[new_speaker_id] = {
                "speaker_id": new_speaker_id,
                "vault_id": vault_id,
                "name": body.name,
                "notes": body.notes,
            }
            cluster["status"] = "enrolled"
            cluster["speaker_id"] = new_speaker_id
            # Retroactively update section_messages
            for msg in store.section_messages.values():
                if msg.get("cluster_id") == cluster_id:
                    msg["speaker_id"] = new_speaker_id
            return {"cluster_id": cluster_id, "status": "enrolled", "speaker_id": new_speaker_id}

        elif body.action == "mark_existing":
            if not body.speaker_id or body.speaker_id not in store.speaker_profiles:
                raise HTTPException(status_code=404, detail="Target speaker_id not found")
            cluster["status"] = "merged"
            cluster["speaker_id"] = body.speaker_id
            for msg in store.section_messages.values():
                if msg.get("cluster_id") == cluster_id:
                    msg["speaker_id"] = body.speaker_id
            return {"cluster_id": cluster_id, "status": "merged", "speaker_id": body.speaker_id}

        elif body.action == "dismiss":
            cluster["status"] = "dismissed"
            return {"cluster_id": cluster_id, "status": "dismissed"}

        raise HTTPException(status_code=422, detail=f"Unknown action: {body.action}")

    # 6. Listening Schedule Synchronization
    @app.get("/users/me/listening-schedule")
    def get_schedule(auth: dict = Depends(authenticate_bearer)):
        user_id = auth["user_id"]
        sched = store.listening_schedules.get(user_id)
        if not sched:
            raise HTTPException(status_code=404, detail="Schedule not configured")
        return sched

    @app.put("/users/me/listening-schedule")
    def put_schedule(
        body: ScheduleUpdateRequest,
        request: Request,
        auth: dict = Depends(authenticate_bearer),
    ):
        user_id = auth["user_id"]
        current = store.listening_schedules.get(user_id, {"revision": 0, "weekly_windows": []})

        header_rev_str = request.headers.get("x-hemory-listening-schedule-revision")
        if header_rev_str is not None:
            try:
                header_rev = int(header_rev_str)
            except ValueError:
                raise HTTPException(status_code=422, detail="Invalid revision header")
        else:
            header_rev = body.revision

        # Optimistic concurrency check: if client revision is behind server revision
        if header_rev < current["revision"]:
            return JSONResponse(
                status_code=409,
                content={
                    "error_code": "SCHEDULE_REVISION_CONFLICT",
                    "server_revision": current["revision"],
                    "client_revision": header_rev,
                    "message": "Schedule revision conflict. Fetch latest revision and merge.",
                },
            )

        # Accept and increment revision
        next_rev = current["revision"] + 1
        updated = {
            "user_id": user_id,
            "revision": next_rev,
            "timezone": body.timezone,
            "weekly_windows": body.weekly_windows,
        }
        store.listening_schedules[user_id] = updated
        return updated

    # 7. Processing Chains & Recording Mutations Endpoints
    @app.get("/processing-chains")
    def list_processing_chains(
        status: str | None = None,
        auth: dict = Depends(authenticate_bearer),
    ):
        vault_id = auth["vault_id"]
        chains = [c for c in store.processing_chains.values() if c.get("vault_id") == vault_id]
        if status == "active" or status is None:
            chains = [c for c in chains if c["status"] in ("pending", "running")]
        elif status != "all":
            chains = [c for c in chains if c["status"] == status]
        return {
            "chains": chains,
            "total": len(chains),
            "server_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    @app.get("/processing-chains/{chain_id}")
    def get_processing_chain(
        chain_id: str,
        auth: dict = Depends(authenticate_bearer),
    ):
        vault_id = auth["vault_id"]
        chain = store.processing_chains.get(chain_id)
        if not chain or chain.get("vault_id") != vault_id:
            raise HTTPException(status_code=404, detail="Processing chain not found")
        return chain

    @app.get("/recording-mutations")
    def list_recording_mutations(
        cursor: int = 0,
        limit: int = 50,
        auth: dict = Depends(authenticate_bearer),
    ):
        vault_id = auth["vault_id"]
        filtered = [
            m for m in store.recording_mutations
            if m.get("vault_id") == vault_id and m["cursor"] > cursor
        ]
        has_more = len(filtered) > limit
        selected = filtered[:limit]
        next_cursor = selected[-1]["cursor"] if selected else cursor
        return {
            "mutations": selected,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    @app.post("/recording-mutations")
    def post_recording_mutations(
        body: dict = Body(...),
        auth: dict = Depends(authenticate_bearer),
    ):
        muts = body.get("mutations", [])
        for m in muts:
            cur = len(store.recording_mutations) + 1
            store.recording_mutations.append({
                "cursor": cur,
                "id": cur,
                "tenant_id": auth["tenant_id"],
                "vault_id": auth["vault_id"],
                "entity_type": "recording",
                "entity_id": m.get("live_session_id") or f"mut_{cur}",
                "mutation_type": m.get("action", "redact_audio"),
                "payload": m,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })
        return {"status": "synced", "processed_count": len(muts)}

    return app
