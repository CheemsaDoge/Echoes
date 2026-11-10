from __future__ import annotations
import asyncio, contextlib, copy, datetime, hashlib, json, os, secrets, shutil, threading
from pathlib import Path
from fastapi import FastAPI, APIRouter, Depends, Header, HTTPException, Query, Request, File, Form, UploadFile
from starlette import status as http_status
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from .config import Settings
from .db import Database, emit, now_ms, uid
from .audio import atomic_write, read_wav, SAMPLE_RATE
from .models import (SessionCreate, SessionClose, ChunkMeta, ProfileCreate, EvaluationRequest,
                     SpeakerCreate, SpeakerAssign, VoiceprintCreate, VoiceMatch, EventTypeCreate,
                     EventPatch, MemoryPatch, VadConfig, VadConfigPatch, AsrConfig, AsrConfigPatch,
                     VadRuntimeConfig, VadRuntimeConfigPatch, VoiceprintConfig, VoiceprintConfigPatch,
                     RuntimeConfigResponse, RuntimeConfigPatch, PrimaryDeviceRequest,
                     PrimaryDeviceResponse, UploadRecordingResponse,
                     SpeakerProfileDto, SpeakerListResponse, SpeakerCreateRequest, SpeakerCreateResponse,
                     VoiceprintClusterDto, ClusterListResponse, ClusterSampleDto, ClusterSamplesResponse,
                     ClusterPatchRequest, ClusterPatchResponse,
                     ChatTurnCreateRequest, ChatTurnCancelResponse, ChatThreadCreateRequest,
                     ChatThreadDto, ChatTurnDto, ProcessingChainDto, ProcessingChainsResponse,
                     RecordingMutationDto, RecordingMutationsResponse,
                     ClientRecordingMutationsRequest, ClientRecordingMutationsResponse)
from .pipeline import Pipeline, plan_batches
from .worker import run_loop
from .queries import list_events, get_event, search_memories
from .speakers import normalize, match_speaker
from .voiceprint import generate_synthetic_aac
from .vad import speech_intervals, score_intervals
from .lease import LeaseRejectionException, PrimaryLeaseArbiter
from .storage import StorageService
from .upload import UploadService, AudioBitstreamException
from .sse import TurnStreamManager, SSE_HEADERS, parse_last_event_id, BufferedEvent
from .chat import GeminiChatAgent, SyntheticGeminiAgent
from .sync import (get_processing_chains_response, get_recording_mutations_response,
                   record_client_mutations)
from . import mcp


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.data_dir, db_path=settings.db_path)
    db.load_into_settings(settings)
    settings.validate()
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

    app = FastAPI(title="EchoLedger API", version="0.1.1", lifespan=lifespan,
                  description="Single-owner audio journal. All v1 routes require a Bearer token.",
                  docs_url="/docs", redoc_url=None)
    storage = StorageService(settings.data_dir, storage_dir=settings.storage_dir)
    upload_service = UploadService(db, storage, settings.max_upload_bytes)
    turn_manager = TurnStreamManager(retention_seconds=60.0)
    app.state.db, app.state.pipeline, app.state.settings = db, pipeline, settings
    app.state.storage, app.state.upload_service, app.state.turn_manager = storage, upload_service, turn_manager

    @app.exception_handler(LeaseRejectionException)
    def lease_rejection_handler(request: Request, exc: LeaseRejectionException):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    @app.exception_handler(AudioBitstreamException)
    def audio_bitstream_handler(request: Request, exc: AudioBitstreamException):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    def authorize(authorization: str | None = Header(default=None)):
        expected = "Bearer " + settings.api_token
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    def authenticate_tenancy(request: Request) -> dict[str, Any]:
        authorization = request.headers.get("Authorization", "")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
        token = authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=401, detail="Invalid bearer token")

        h_tenant = request.headers.get("x-tenant-id")
        h_vault = request.headers.get("x-vault-id")
        h_user = request.headers.get("x-user-id")
        h_dp = request.headers.get("x-dp-instance-id")

        is_admin = secrets.compare_digest(token, settings.api_token)

        tenant_id = h_tenant
        vault_id = h_vault
        user_id = h_user
        dp_instance_id = h_dp

        if not is_admin:
            binding = db.one("SELECT * FROM device_bindings WHERE push_token=? OR binding_id=?", (token, token))
            if binding:
                if h_user and h_user != binding["user_id"]:
                    raise HTTPException(status_code=403, detail="Header x-user-id does not match authenticated binding")
                if h_tenant and h_tenant != binding["tenant_id"]:
                    raise HTTPException(status_code=403, detail="Header x-tenant-id does not match authenticated binding")
                if h_dp and h_dp != binding["dp_instance_id"]:
                    raise HTTPException(status_code=403, detail="Header x-dp-instance-id does not match authenticated binding")
                user_id = binding["user_id"]
                tenant_id = binding["tenant_id"]
                dp_instance_id = binding["dp_instance_id"]
            else:
                user_row = db.one("SELECT * FROM users WHERE user_id=? OR email=?", (token, token))
                if user_row:
                    if h_user and h_user != user_row["user_id"]:
                        raise HTTPException(status_code=403, detail="Header x-user-id does not match authenticated user")
                    if h_tenant and h_tenant != user_row["tenant_id"]:
                        raise HTTPException(status_code=403, detail="Header x-tenant-id does not match authenticated user")
                    user_id = user_row["user_id"]
                    tenant_id = user_row["tenant_id"]

            if not tenant_id and user_id:
                u = db.get_user(user_id)
                if u:
                    tenant_id = u["tenant_id"]

            path_tenant = request.path_params.get("tenant_id") or request.path_params.get("tenantId")
            if tenant_id and path_tenant and tenant_id != path_tenant:
                raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")

            if h_vault:
                vault_row = db.get_vault(h_vault, tenant_id) if tenant_id else None
                if not vault_row:
                    raise HTTPException(status_code=404, detail="Vault not found")
                vault_id = vault_row["vault_id"]
            else:
                vault_id = None
                if binding and dp_instance_id and tenant_id:
                    dp_row = db.one(
                        "SELECT vault_id FROM dp_instances WHERE dp_instance_id=? AND tenant_id=?",
                        (dp_instance_id, tenant_id),
                    )
                    if dp_row and dp_row.get("vault_id"):
                        vault_id = dp_row["vault_id"]

                if not vault_id and tenant_id:
                    def_vlt = db.one(
                        "SELECT vault_id FROM vaults WHERE tenant_id=? AND is_default=1 LIMIT 1",
                        (tenant_id,),
                    )
                    if def_vlt and def_vlt.get("vault_id"):
                        vault_id = def_vlt["vault_id"]
                    else:
                        any_vlt = db.one(
                            "SELECT vault_id FROM vaults WHERE tenant_id=? LIMIT 1",
                            (tenant_id,),
                        )
                        if any_vlt and any_vlt.get("vault_id"):
                            vault_id = any_vlt["vault_id"]

                has_vault_in_path = "vault_id" in request.path_params or "vaultId" in request.path_params
                is_tenant_only_endpoint = bool(path_tenant and not has_vault_in_path)
                if not vault_id and not is_tenant_only_endpoint:
                    raise HTTPException(status_code=404, detail="Vault not found")

        return {
            "token": token,
            "is_admin": is_admin,
            "tenant_id": tenant_id,
            "vault_id": vault_id,
            "user_id": user_id,
            "dp_instance_id": dp_instance_id,
        }

    @app.get("/tenants/{tenant_id}")
    def get_tenant(tenant_id: str, request: Request, auth: dict = Depends(authenticate_tenancy)):
        if not auth.get("is_admin") and auth.get("tenant_id") and auth["tenant_id"] != tenant_id:
            raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")
        tenant = db.get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return tenant

    @app.get("/tenants/{tenant_id}/vaults/{vault_id}")
    def get_vault(tenant_id: str, vault_id: str, request: Request, auth: dict = Depends(authenticate_tenancy)):
        if not auth.get("is_admin"):
            if auth.get("tenant_id") and auth["tenant_id"] != tenant_id:
                raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")
            if auth.get("vault_id") and auth["vault_id"] != vault_id:
                vault = db.get_vault(vault_id, tenant_id)
                if not vault:
                    raise HTTPException(status_code=404, detail="Vault not found")
                raise HTTPException(status_code=403, detail="Cross-vault access forbidden")
        vault = db.get_vault(vault_id, tenant_id)
        if not vault:
            raise HTTPException(status_code=404, detail="Vault not found")
        return vault

    @app.get("/speakers", response_model=SpeakerListResponse, tags=["Speakers"])
    def list_speakers_scoped(request: Request, auth: dict = Depends(authenticate_tenancy)):
        vault_id = auth.get("vault_id") or "vlt_default"
        speakers = db.list_speaker_profiles(vault_id)
        return {"speakers": speakers}

    @app.post("/speakers", response_model=SpeakerCreateResponse, status_code=http_status.HTTP_201_CREATED, tags=["Speakers"])
    def create_speaker_scoped(payload: SpeakerCreateRequest, request: Request, auth: dict = Depends(authenticate_tenancy)):
        vault_id = auth.get("vault_id") or "vlt_default"
        tenant_id = auth.get("tenant_id") or "ten_default"
        user_id = auth.get("user_id") or "usr_default"
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Speaker name cannot be empty")

        cluster = None
        if payload.cluster_id:
            cluster = db.get_voiceprint_cluster(payload.cluster_id, vault_id)
            if not cluster:
                raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Cluster {payload.cluster_id} not found")

        new_spk_id = f"spk_{secrets.token_hex(6)}"
        sample_count = int(cluster.get("occurrences_count", 1)) if cluster else 0
        created = db.create_speaker_profile(
            speaker_id=new_spk_id,
            tenant_id=tenant_id,
            vault_id=vault_id,
            user_id=user_id,
            name=name,
            role_label=payload.role_label,
            sample_count=sample_count,
        )

        if cluster:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            with db.connect(immediate=True) as c:
                c.execute(
                    "UPDATE voiceprint_clusters SET status='enrolled', speaker_id=?, updated_at=? WHERE cluster_id=?",
                    (new_spk_id, now_iso, payload.cluster_id),
                )
                c.execute(
                    "UPDATE section_messages SET speaker_id=?, speaker_name=? WHERE cluster_id=?",
                    (new_spk_id, name, payload.cluster_id),
                )

        return {
            "speaker_id": created["speaker_id"],
            "name": created["name"],
            "created_at": created["created_at"],
        }

    @app.get("/speakers/{speaker_id}", response_model=SpeakerProfileDto, tags=["Speakers"])
    def get_speaker_scoped(speaker_id: str, request: Request, auth: dict = Depends(authenticate_tenancy)):
        vault_id = auth.get("vault_id") or "vlt_default"
        spk = db.get_speaker_profile(speaker_id, vault_id)
        if not spk:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Speaker not found")
        return spk

    @app.get("/clusters", response_model=ClusterListResponse, tags=["Clusters"])
    def list_clusters_scoped(
        request: Request,
        status: str | None = Query(default=None),
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"
        clusters = db.list_voiceprint_clusters(vault_id, status=status)
        return {"clusters": clusters}

    @app.get("/clusters/{cluster_id}", response_model=VoiceprintClusterDto, tags=["Clusters"])
    def get_cluster_scoped(cluster_id: str, request: Request, auth: dict = Depends(authenticate_tenancy)):
        vault_id = auth.get("vault_id") or "vlt_default"
        cluster = db.get_voiceprint_cluster(cluster_id, vault_id)
        if not cluster:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Cluster not found")
        return cluster

    @app.get("/clusters/{cluster_id}/samples", response_model=ClusterSamplesResponse, tags=["Clusters"])
    def get_cluster_samples_scoped(cluster_id: str, request: Request, auth: dict = Depends(authenticate_tenancy)):
        vault_id = auth.get("vault_id") or "vlt_default"
        cluster = db.get_voiceprint_cluster(cluster_id, vault_id)
        if not cluster:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Cluster not found")
        samples = db.get_cluster_samples(cluster_id, vault_id)
        return {"cluster_id": cluster_id, "samples": samples}

    @app.get("/clusters/{cluster_id}/audio_preview", tags=["Clusters"])
    def get_cluster_audio_preview(cluster_id: str, request: Request, auth: dict = Depends(authenticate_tenancy)):
        vault_id = auth.get("vault_id") or "vlt_default"
        cluster = db.get_voiceprint_cluster(cluster_id, vault_id)
        if not cluster:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Cluster not found")

        # Check physical file on disk
        preview_path = settings.data_dir / "clusters" / f"{cluster_id}.aac"
        if preview_path.is_file():
            return FileResponse(preview_path, media_type="audio/aac")

        # Synthesize 5-second AAC-LC stream with ADTS headers
        synth_aac = generate_synthetic_aac(duration_sec=5.0)
        return Response(content=synth_aac, media_type="audio/aac")

    @app.patch("/clusters/{cluster_id}", response_model=ClusterPatchResponse, tags=["Clusters"])
    def patch_cluster_scoped(
        cluster_id: str,
        payload: ClusterPatchRequest,
        request: Request,
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"
        tenant_id = auth.get("tenant_id") or "ten_default"
        user_id = auth.get("user_id") or "usr_default"

        cluster = db.get_voiceprint_cluster(cluster_id, vault_id)
        if not cluster:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Cluster not found")

        if payload.action == "mark_new":
            name = (payload.name or "").strip()
            if not name:
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Field 'name' is required for action 'mark_new'",
                )
            res = db.patch_voiceprint_cluster_mark_new(
                cluster_id=cluster_id,
                tenant_id=tenant_id,
                vault_id=vault_id,
                user_id=user_id,
                name=name,
                notes=payload.notes,
                role_label=payload.role_label,
            )
            return res

        elif payload.action == "mark_existing":
            if not payload.speaker_id:
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Field 'speaker_id' is required for action 'mark_existing'",
                )
            try:
                res = db.patch_voiceprint_cluster_mark_existing(
                    cluster_id=cluster_id,
                    vault_id=vault_id,
                    target_speaker_id=payload.speaker_id,
                )
                return res
            except KeyError:
                raise HTTPException(
                    status_code=http_status.HTTP_404_NOT_FOUND,
                    detail="Target speaker_id not found in caller vault",
                )

        elif payload.action == "dismiss":
            res = db.patch_voiceprint_cluster_dismiss(
                cluster_id=cluster_id,
                vault_id=vault_id,
            )
            return res

        else:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown action: {payload.action}",
            )

    @app.put("/primary-device", response_model=PrimaryDeviceResponse, tags=["Lease"])
    @app.put("/v1/primary-device", response_model=PrimaryDeviceResponse, tags=["Lease"])
    def put_primary_device(payload: PrimaryDeviceRequest, request: Request, auth: dict = Depends(authenticate_tenancy)):
        tenant_id = auth.get("tenant_id")
        user_id = auth.get("user_id")
        with db.connect(immediate=True) as conn:
            return PrimaryLeaseArbiter.acquire_primary_device(
                conn=conn,
                dp_instance_id=payload.dp_instance_id,
                vault_id=payload.vault_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )

    @app.post(
        "/tenants/{tenant_id}/vaults/{vault_id}/recordings/upload",
        status_code=http_status.HTTP_201_CREATED,
        response_model=UploadRecordingResponse,
        tags=["Recordings"],
    )
    @app.post(
        "/tenants/{tenantId}/vaults/{vaultId}/recordings/upload",
        status_code=http_status.HTTP_201_CREATED,
        response_model=UploadRecordingResponse,
        include_in_schema=False,
    )
    @app.post(
        "/v1/tenants/{tenant_id}/vaults/{vault_id}/recordings/upload",
        status_code=http_status.HTTP_201_CREATED,
        response_model=UploadRecordingResponse,
        include_in_schema=False,
    )
    async def upload_recording(
        request: Request,
        tenant_id: str | None = None,
        vault_id: str | None = None,
        tenantId: str | None = None,
        vaultId: str | None = None,
        started_at: str = Form(...),
        format: str = Form(...),
        source: str = Form(...),
        capture_timezone: str | None = Form(default=None),
        capture_metadata: str = Form(...),
        file: UploadFile = File(...),
        auth: dict = Depends(authenticate_tenancy),
    ):
        eff_tenant = tenant_id or tenantId
        eff_vault = vault_id or vaultId
        if not eff_tenant or not eff_vault:
            raise HTTPException(status_code=400, detail="Missing tenant_id or vault_id in path")

        if not auth.get("is_admin"):
            if auth.get("tenant_id") and auth["tenant_id"] != eff_tenant:
                raise HTTPException(status_code=403, detail="Access denied: micro-isolation breach")
            if auth.get("vault_id") and auth["vault_id"] != eff_vault:
                raise HTTPException(status_code=403, detail="Access denied: micro-isolation breach")

        uploading_dp_id = auth.get("dp_instance_id") or request.headers.get("x-dp-instance-id")

        return await upload_service.handle_upload(
            tenant_id=eff_tenant,
            vault_id=eff_vault,
            started_at=started_at,
            format_str=format,
            source=source,
            capture_timezone=capture_timezone,
            capture_metadata_str=capture_metadata,
            file=file,
            auth_context=auth,
            uploading_dp_id_header=uploading_dp_id,
        )

    def get_session(session_id):
        row = db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
        if not row:
            if db.one("SELECT id FROM deleted_sessions WHERE id=?", (session_id,)):
                raise HTTPException(410, "Session was deleted; do not upload it again")
            raise HTTPException(404, "Session not found")
        return row

    @app.get("/healthz")
    def health():
        return {"ok": True, "version": "0.1.1"}

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
        active_prof = db.active_profile()
        vad_active_profile = {
            "id": active_prof["id"],
            "name": active_prof["name"],
        } if active_prof else None
        voiceprint_config = {
            "speaker_model_id": settings.speaker_model_id,
            "similarity_threshold": settings.similarity_threshold,
            "margin": settings.margin,
            "min_speech_ms": settings.min_speech_ms,
            "auto_match_enabled": settings.auto_match_enabled,
            "consent_required": settings.consent_required,
        }
        return {"version": "0.1.0", "asr_provider": settings.asr_provider,
                "asr_configured": settings.asr_provider == "mock" or (settings.asr_provider == "gemini" and bool(settings.gemini_key)) or (settings.asr_provider == "vertexai" and bool(settings.vertex_project or settings.vertex_api_key or settings.vertex_credentials or settings.vertex_endpoint or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or getattr(pipeline.transcriber, "project", ""))),
                "vad_provider": settings.vad_provider, "vad_active_profile": vad_active_profile,
                "voice_embedding_provider": "not_configured",
                "speaker_identity": "manual-or-external-embedding", "voiceprint_config": voiceprint_config,
                "memory_mode": "evidence-excerpts-with-human-confirmation",
                "mcp_enabled": bool(settings.mcp_token), "batch_counts": counts, "cursor": latest["cursor"],
                "raw_audio_policy": "retained-until-explicit-delete", "is_demo": settings.asr_provider == "mock"}

    def _build_runtime_config_response() -> RuntimeConfigResponse:
        active_prof = db.active_profile()
        vad_cfg = VadConfig.model_validate_json(active_prof["config"])

        has_gemini = bool(settings.gemini_key)
        gemini_masked = "***" if has_gemini else ""

        has_vertex_key = bool(settings.vertex_api_key)
        vertex_key_masked = "***" if has_vertex_key else ""

        asr_configured = settings.asr_provider == "mock" or (settings.asr_provider == "gemini" and bool(settings.gemini_key)) or (settings.asr_provider == "vertexai" and bool(settings.vertex_project or settings.vertex_api_key or settings.vertex_credentials or settings.vertex_endpoint or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or getattr(pipeline.transcriber, "project", "")))

        asr_sub = AsrConfig(
            asr_provider=settings.asr_provider,
            gemini_key=gemini_masked,
            has_gemini_key=has_gemini,
            gemini_model=settings.gemini_model,
            gemini_mode=settings.gemini_mode,
            vertex_project=settings.vertex_project,
            vertex_location=settings.vertex_location,
            vertex_model=settings.vertex_model,
            vertex_endpoint=settings.vertex_endpoint,
            vertex_api_key=vertex_key_masked,
            has_vertex_api_key=has_vertex_key,
            asr_configured=asr_configured,
        )

        vad_sub = VadRuntimeConfig(
            vad_provider=settings.vad_provider,
            vad_model=settings.vad_model,
            active_profile_id=active_prof["id"],
            active_profile_name=active_prof["name"],
            config=vad_cfg,
        )

        voice_sub = VoiceprintConfig(
            speaker_model_id=settings.speaker_model_id,
            similarity_threshold=settings.similarity_threshold,
            margin=settings.margin,
            min_speech_ms=settings.min_speech_ms,
            auto_match_enabled=settings.auto_match_enabled,
            consent_required=settings.consent_required,
        )

        return RuntimeConfigResponse(
            asr=asr_sub,
            vad=vad_sub,
            voiceprint=voice_sub,
            asr_provider=settings.asr_provider,
            gemini_key=gemini_masked,
            has_gemini_key=has_gemini,
            gemini_model=settings.gemini_model,
            gemini_mode=settings.gemini_mode,
            vertex_project=settings.vertex_project,
            vertex_location=settings.vertex_location,
            vertex_model=settings.vertex_model,
            vertex_endpoint=settings.vertex_endpoint,
            vertex_api_key=vertex_key_masked,
            has_vertex_api_key=has_vertex_key,
            vad_provider=settings.vad_provider,
            vad_model=settings.vad_model,
            vad_config=vad_cfg,
            speaker_model_id=settings.speaker_model_id,
            similarity_threshold=settings.similarity_threshold,
            margin=settings.margin,
            min_speech_ms=settings.min_speech_ms,
            auto_match_enabled=settings.auto_match_enabled,
            consent_required=settings.consent_required,
        )

    config_lock = threading.Lock()

    @router.get("/runtime-config", response_model=RuntimeConfigResponse)
    def get_runtime_config():
        return _build_runtime_config_response()

    @router.put("/runtime-config", response_model=RuntimeConfigResponse)
    def update_runtime_config(body: RuntimeConfigPatch):
        with config_lock:
            asr_patch = body.asr or AsrConfigPatch()
            vad_patch = body.vad or VadRuntimeConfigPatch()
            vp_patch = body.voiceprint or VoiceprintConfigPatch()

            asr_prov = body.asr_provider or asr_patch.asr_provider
            g_key = body.gemini_key if body.gemini_key is not None else asr_patch.gemini_key
            g_model = body.gemini_model if body.gemini_model is not None else asr_patch.gemini_model
            g_mode = body.gemini_mode or asr_patch.gemini_mode
            v_proj = body.vertex_project if body.vertex_project is not None else asr_patch.vertex_project
            v_loc = body.vertex_location if body.vertex_location is not None else asr_patch.vertex_location
            v_model = body.vertex_model if body.vertex_model is not None else asr_patch.vertex_model
            v_endp = body.vertex_endpoint if body.vertex_endpoint is not None else asr_patch.vertex_endpoint
            v_key = body.vertex_api_key if body.vertex_api_key is not None else asr_patch.vertex_api_key

            vad_prov = body.vad_provider or vad_patch.vad_provider
            vad_mod = body.vad_model if body.vad_model is not None else vad_patch.vad_model
            v_cfg_patch = body.vad_config or body.config or vad_patch.config

            spk_mod = body.speaker_model_id or vp_patch.speaker_model_id
            sim_th = body.similarity_threshold if body.similarity_threshold is not None else vp_patch.similarity_threshold
            mrg = body.margin if body.margin is not None else vp_patch.margin
            min_spk_ms = body.min_speech_ms if body.min_speech_ms is not None else vp_patch.min_speech_ms
            auto_m = body.auto_match_enabled if body.auto_match_enabled is not None else vp_patch.auto_match_enabled
            cons_req = body.consent_required if body.consent_required is not None else vp_patch.consent_required

            def is_mask(val: str | None) -> bool:
                if not val:
                    return False
                s = val.strip()
                return len(s) > 0 and all(c == '*' for c in s)

            # 1. Stage candidate settings
            candidate_asr_prov = asr_prov if asr_prov is not None else settings.asr_provider
            candidate_g_key = settings.gemini_key if (g_key is None or is_mask(g_key)) else g_key.strip()
            candidate_g_model = g_model if g_model is not None else settings.gemini_model
            candidate_g_mode = g_mode if g_mode is not None else settings.gemini_mode
            candidate_v_proj = v_proj if v_proj is not None else settings.vertex_project
            candidate_v_loc = v_loc if v_loc is not None else settings.vertex_location
            candidate_v_model = v_model if v_model is not None else settings.vertex_model
            candidate_v_endp = v_endp if v_endp is not None else settings.vertex_endpoint
            candidate_v_key = settings.vertex_api_key if (v_key is None or is_mask(v_key)) else v_key.strip()

            candidate_vad_prov = vad_prov if vad_prov is not None else settings.vad_provider
            candidate_vad_mod = vad_mod if vad_mod is not None else settings.vad_model

            candidate_spk_mod = spk_mod if spk_mod is not None else settings.speaker_model_id
            candidate_sim_th = sim_th if sim_th is not None else settings.similarity_threshold
            candidate_mrg = mrg if mrg is not None else settings.margin
            candidate_min_spk = min_spk_ms if min_spk_ms is not None else settings.min_speech_ms
            candidate_auto_m = auto_m if auto_m is not None else settings.auto_match_enabled
            candidate_cons_req = cons_req if cons_req is not None else settings.consent_required

            # Validate candidate Settings object before touching database or live settings
            test_settings = copy.copy(settings)
            test_settings.asr_provider = candidate_asr_prov
            test_settings.gemini_key = candidate_g_key
            test_settings.gemini_model = candidate_g_model
            test_settings.gemini_mode = candidate_g_mode
            test_settings.vertex_project = candidate_v_proj
            test_settings.vertex_location = candidate_v_loc
            test_settings.vertex_model = candidate_v_model
            test_settings.vertex_endpoint = candidate_v_endp
            test_settings.vertex_api_key = candidate_v_key
            test_settings.vad_provider = candidate_vad_prov
            test_settings.vad_model = candidate_vad_mod
            test_settings.speaker_model_id = candidate_spk_mod
            test_settings.similarity_threshold = candidate_sim_th
            test_settings.margin = candidate_mrg
            test_settings.min_speech_ms = candidate_min_spk
            test_settings.auto_match_enabled = candidate_auto_m
            test_settings.consent_required = candidate_cons_req
            try:
                test_settings.validate()
            except ValueError as exc:
                raise HTTPException(422, f"Configuration validation failed: {exc}") from exc

            # Validate candidate VAD config if provided
            active_prof = db.active_profile()
            updated_vad_cfg = None
            if v_cfg_patch is not None:
                cur_vad = json.loads(active_prof["config"])
                patch_vad = {k: v for k, v in v_cfg_patch.model_dump().items() if v is not None}
                cur_vad.update(patch_vad)
                try:
                    updated_vad_cfg = VadConfig.model_validate(cur_vad)
                except (ValueError, ValidationError) as exc:
                    raise HTTPException(422, f"Invalid VAD config: {exc}") from exc

            # Test instantiating the new provider components before committing changes
            try:
                temp_pipeline = Pipeline(db, test_settings)
                _ = temp_pipeline.scorer
            except Exception as exc:
                raise HTTPException(500, f"Failed to initialize VAD provider '{candidate_vad_prov}': {exc}") from exc

            # 2. Persist to database atomically
            kv_updates = {}
            if asr_prov is not None:
                kv_updates["asr.provider"] = candidate_asr_prov
            if g_key is not None and not is_mask(g_key):
                kv_updates["asr.gemini_key"] = candidate_g_key
            if g_model is not None:
                kv_updates["asr.gemini_model"] = candidate_g_model
            if g_mode is not None:
                kv_updates["asr.gemini_mode"] = candidate_g_mode
            if v_proj is not None:
                kv_updates["asr.vertex_project"] = candidate_v_proj
            if v_loc is not None:
                kv_updates["asr.vertex_location"] = candidate_v_loc
            if v_model is not None:
                kv_updates["asr.vertex_model"] = candidate_v_model
            if v_endp is not None:
                kv_updates["asr.vertex_endpoint"] = candidate_v_endp
            if v_key is not None and not is_mask(v_key):
                kv_updates["asr.vertex_api_key"] = candidate_v_key
            if vad_prov is not None:
                kv_updates["vad.provider"] = candidate_vad_prov
            if vad_mod is not None:
                kv_updates["vad.model"] = candidate_vad_mod
            if spk_mod is not None:
                kv_updates["voiceprint.speaker_model_id"] = candidate_spk_mod
            if sim_th is not None:
                kv_updates["voiceprint.similarity_threshold"] = str(candidate_sim_th)
            if mrg is not None:
                kv_updates["voiceprint.margin"] = str(candidate_mrg)
            if min_spk_ms is not None:
                kv_updates["voiceprint.min_speech_ms"] = str(candidate_min_spk)
            if auto_m is not None:
                kv_updates["voiceprint.auto_match_enabled"] = str(candidate_auto_m).lower()
            if cons_req is not None:
                kv_updates["voiceprint.consent_required"] = str(candidate_cons_req).lower()

            with db.connect(immediate=True) as c:
                for k, v in kv_updates.items():
                    c.execute("""INSERT INTO kv_settings(key, value, updated_ms) VALUES(?,?,?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ms=excluded.updated_ms""",
                        (k, v, now_ms()))
                if updated_vad_cfg is not None:
                    new_profile_id = uid()
                    new_profile_name = f"{active_prof['name']} (Runtime)"
                    c.execute("UPDATE profiles SET active=0 WHERE active=1")
                    c.execute("INSERT INTO profiles VALUES(?,?,?,?,1)",
                              (new_profile_id, new_profile_name, updated_vad_cfg.model_dump_json(), now_ms()))
                    emit(c, "profile.created", new_profile_id)
                    emit(c, "profile.activated", new_profile_id)
                emit(c, "runtime_config.updated", "runtime-config")

            # 3. Apply staged settings to live state
            settings.asr_provider = candidate_asr_prov
            settings.gemini_key = candidate_g_key
            settings.gemini_model = candidate_g_model
            settings.gemini_mode = candidate_g_mode
            settings.vertex_project = candidate_v_proj
            settings.vertex_location = candidate_v_loc
            settings.vertex_model = candidate_v_model
            settings.vertex_endpoint = candidate_v_endp
            settings.vertex_api_key = candidate_v_key
            settings.vad_provider = candidate_vad_prov
            settings.vad_model = candidate_vad_mod
            settings.speaker_model_id = candidate_spk_mod
            settings.similarity_threshold = candidate_sim_th
            settings.margin = candidate_mrg
            settings.min_speech_ms = candidate_min_spk
            settings.auto_match_enabled = candidate_auto_m
            settings.consent_required = candidate_cons_req

            pipeline.reload_transcriber()
            pipeline.reload_scorer()

            return _build_runtime_config_response()

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
        th = body.threshold if body.threshold is not None else settings.similarity_threshold
        mg = body.margin if body.margin is not None else settings.margin
        try:
            return match_speaker(body.embedding, body.model_id, db.all("SELECT * FROM voiceprints"), th, mg)
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

    # -------------------------------------------------------------------------
    # Milestone M4: Chat Threads & SSE Turns
    # -------------------------------------------------------------------------
    @app.post("/chat/threads", status_code=201, response_model=ChatThreadDto, tags=["Chat"])
    def create_chat_thread_endpoint(
        body: ChatThreadCreateRequest | None = None,
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"
        tenant_id = auth.get("tenant_id") or "ten_default"
        user_id = auth.get("user_id") or "usr_default"

        req = body or ChatThreadCreateRequest()
        thread_id = req.thread_id or f"th_{secrets.token_hex(8)}"
        title = req.title or "New Chat"

        # If thread already exists in another vault, raise 404 to avoid enumeration
        existing = db.get_chat_thread(thread_id)
        if existing:
            if existing["vault_id"] != vault_id:
                raise HTTPException(status_code=404, detail="Thread not found")
            return existing

        created = db.create_chat_thread(
            thread_id=thread_id,
            tenant_id=tenant_id,
            vault_id=vault_id,
            user_id=user_id,
            title=title,
            metadata=req.metadata,
        )
        return created

    @app.get("/chat/threads/{thread_id}", response_model=ChatThreadDto, tags=["Chat"])
    def get_chat_thread_endpoint(
        thread_id: str,
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"
        thread = db.get_chat_thread(thread_id)
        if not thread or thread["vault_id"] != vault_id:
            raise HTTPException(status_code=404, detail="Thread not found")
        return thread

    @app.post("/chat/threads/{thread_id}/turns", tags=["Chat"])
    async def stream_chat_turn_endpoint(
        thread_id: str,
        payload: ChatTurnCreateRequest,
        request: Request,
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"
        tenant_id = auth.get("tenant_id") or "ten_default"
        user_id = auth.get("user_id") or "usr_default"

        # Multi-tenant scoping & thread auto-creation race handling
        thread = db.get_chat_thread(thread_id)
        if thread:
            if thread["vault_id"] != vault_id:
                raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
        else:
            try:
                db.create_chat_thread(
                    thread_id=thread_id,
                    tenant_id=tenant_id,
                    vault_id=vault_id,
                    user_id=user_id,
                    title=payload.effective_prompt[:64],
                )
            except Exception:
                thread = db.get_chat_thread(thread_id)
                if thread and thread["vault_id"] != vault_id:
                    raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

        turn_id = payload.client_turn_id or f"turn_{secrets.token_hex(8)}"

        # Check existing turn in DB for foreign vault isolation
        db_turn = db.get_chat_turn(turn_id)
        if db_turn and (db_turn.get("vault_id") != vault_id or db_turn.get("thread_id") != thread_id):
            raise HTTPException(status_code=404, detail="Turn not found")

        if not db_turn:
            try:
                db_turn = db.create_chat_turn(
                    turn_id=turn_id,
                    thread_id=thread_id,
                    tenant_id=tenant_id,
                    vault_id=vault_id,
                    prompt=payload.effective_prompt,
                    client_turn_id=payload.client_turn_id,
                    client_context=payload.client_context,
                )
            except Exception:
                db_turn = db.get_chat_turn(turn_id, vault_id)
                if not db_turn:
                    db_turn_any = db.get_chat_turn(turn_id)
                    if db_turn_any and db_turn_any.get("vault_id") != vault_id:
                        raise HTTPException(status_code=404, detail="Turn not found")
                    raise

        last_event_id = request.headers.get("Last-Event-ID")
        start_seq = parse_last_event_id(last_event_id, turn_id)

        # Handle pre-start cancellation registered in turn_manager
        is_cancelled_in_manager = turn_manager.is_cancelled(turn_id)
        buf = turn_manager.get(turn_id)
        if is_cancelled_in_manager:
            if buf is None:
                buf = turn_manager.get_or_create(turn_id, thread_id)
            buf.mark_cancelled()

        # Check buffer expiration (HTTP 410 Gone)
        is_reconnect_attempt = bool(last_event_id and (last_event_id.startswith(f"{turn_id}_") or last_event_id == turn_id))
        is_terminal_in_db = bool(db_turn and db_turn.get("status") in ("completed", "failed", "cancelled")) and not is_cancelled_in_manager

        if turn_manager.is_expired_turn(turn_id) or ((is_reconnect_attempt or is_terminal_in_db) and buf is None):
            return JSONResponse(
                status_code=410,
                content={"error": "turn_replay_buffer_expired", "message": "Turn replay buffer has expired"},
            )

        if buf is None:
            buf = turn_manager.get_or_create(turn_id, thread_id)

        # Ensure cancelled state is synchronized to DB if cancelled
        if is_cancelled_in_manager or (db_turn and db_turn.get("status") == "cancelled"):
            buf.mark_cancelled()
            try:
                db.update_chat_turn_status(turn_id, "cancelled", vault_id=vault_id)
            except Exception:
                pass

        # If not completed and no execution in progress, start agent execution task
        if not buf.is_completed and not buf.is_cancelled:
            if not turn_manager.is_executing(turn_id):
                use_vertex = settings.asr_provider == "vertexai" and bool(
                    settings.vertex_credentials or settings.vertex_project or settings.vertex_api_key
                )
                effective_chat_model = (
                    settings.vertex_model if use_vertex else settings.gemini_model
                ) or "gemini-3.8-flash"
                if effective_chat_model == "gemini-3.5-transcribe":
                    effective_chat_model = "gemini-3.8-flash"
                agent = GeminiChatAgent(
                    db=db,
                    api_key=settings.gemini_key,
                    model=effective_chat_model,
                    vertex_project=settings.vertex_project,
                    vertex_location=settings.vertex_location,
                    vertex_credentials=settings.vertex_credentials,
                    vertex_api_key=settings.vertex_api_key,
                    use_vertexai=use_vertex,
                )
                exec_task = asyncio.create_task(
                    agent.execute_turn(
                        buffer=buf,
                        prompt=payload.effective_prompt,
                        vault_id=vault_id,
                        user_id=user_id,
                        client_context=payload.client_context,
                    )
                )
                turn_manager.set_executing_task(turn_id, exec_task)

        async def event_generator() -> AsyncGenerator[str, None]:
            # Register subscriber queue to receive live events if turn is active
            queue: asyncio.Queue[BufferedEvent | None] | None = None
            if not buf.is_completed:
                queue = asyncio.Queue()
                buf._subscribers.add(queue)

            last_seq = start_seq
            try:
                # 1. Replay historical events
                events = buf.get_events_after(start_seq)
                for ev in events:
                    if ev.seq > last_seq:
                        last_seq = ev.seq
                    yield ev.raw_frame
                    await asyncio.sleep(0.0001)

                # 2. Progressively stream live events until terminal completion
                if queue is not None and not buf.is_completed:
                    while not buf.is_completed:
                        try:
                            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
                        except asyncio.TimeoutError:
                            if buf.is_completed:
                                break
                            continue
                        if ev is None:
                            break
                        if ev.seq > last_seq:
                            last_seq = ev.seq
                            yield ev.raw_frame

                # 3. Drain any remaining events in queue
                if queue is not None:
                    while not queue.empty():
                        ev = queue.get_nowait()
                        if ev is not None and ev.seq > last_seq:
                            last_seq = ev.seq
                            yield ev.raw_frame
            finally:
                if queue is not None:
                    buf._subscribers.discard(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream; charset=utf-8",
            headers=SSE_HEADERS,
        )

    @app.post("/chat/threads/{thread_id}/turns/{turn_id}/cancel", response_model=ChatTurnCancelResponse, tags=["Chat"])
    def cancel_chat_turn_endpoint(
        thread_id: str,
        turn_id: str,
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"

        thread = db.get_chat_thread(thread_id)
        if not thread or thread["vault_id"] != vault_id:
            raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

        turn = db.get_chat_turn(turn_id)
        if turn and (turn.get("vault_id") != vault_id or turn.get("thread_id") != thread_id):
            raise HTTPException(status_code=404, detail="Turn not found")

        turn_manager.cancel(turn_id)
        cancelled_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            db.update_chat_turn_status(turn_id, "cancelled", vault_id=vault_id)
        except Exception:
            pass

        return {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "status": "cancelled",
            "cancelled_at": cancelled_at,
        }

    # -------------------------------------------------------------------------
    # Milestone M4: Processing Chains (Feature F4.4, P1 Timeline Clipping)
    # -------------------------------------------------------------------------
    @app.get("/processing-chains", response_model=ProcessingChainsResponse, tags=["Sync"])
    def list_processing_chains_endpoint(
        status: str | None = None,
        since: str | None = None,
        session_id: str | None = None,
        recording_id: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"
        return get_processing_chains_response(
            db=db,
            vault_id=vault_id,
            status=status,
            since=since,
            session_id=session_id,
            recording_id=recording_id,
            limit=limit,
        )

    @app.get("/processing-chains/{chain_id}", response_model=ProcessingChainDto, tags=["Sync"])
    def get_processing_chain_endpoint(
        chain_id: str,
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"
        chain = db.get_processing_chain(chain_id)
        if not chain or chain["vault_id"] != vault_id:
            raise HTTPException(status_code=404, detail="Processing chain not found")
        return ProcessingChainDto(
            chain_id=chain["chain_id"],
            recording_id=chain["recording_id"],
            live_session_id=chain.get("live_session_id"),
            status=chain["status"],
            current_step=chain["current_step"],
            progress_pct=float(chain.get("progress_pct") or 0.0),
            error_code=chain.get("error_code"),
            error_message=chain.get("error_message"),
            analyzing_start_at=chain["analyzing_start_at"],
            analyzing_end_at=chain.get("analyzing_end_at"),
            started_at=chain.get("analyzing_start_at"),
            updated_at=chain["updated_at"],
        )

    # -------------------------------------------------------------------------
    # Milestone M4: Recording Mutations (Feature F4.4, Causal Cursor Ordering)
    # -------------------------------------------------------------------------
    @app.get("/recording-mutations", response_model=RecordingMutationsResponse, tags=["Sync"])
    def list_recording_mutations_endpoint(
        cursor: int = Query(0, ge=0),
        after: int | None = Query(None, ge=0),
        limit: int = Query(50, ge=1, le=1000),
        mutation_type: str | None = None,
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"
        effective_cursor = after if after is not None else cursor
        return get_recording_mutations_response(
            db=db,
            vault_id=vault_id,
            cursor=effective_cursor,
            limit=limit,
            mutation_type=mutation_type,
        )

    @app.post("/recording-mutations", response_model=ClientRecordingMutationsResponse, tags=["Sync"])
    def record_mutations_endpoint(
        body: ClientRecordingMutationsRequest,
        auth: dict = Depends(authenticate_tenancy),
    ):
        vault_id = auth.get("vault_id") or "vlt_default"
        tenant_id = auth.get("tenant_id") or "ten_default"
        return record_client_mutations(
            db=db,
            tenant_id=tenant_id,
            vault_id=vault_id,
            mutations=body.mutations,
        )

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
