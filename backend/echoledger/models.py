from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator, computed_field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class SessionCreate(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    started_at_ms: int = Field(ge=0)
    timezone_offset_min: int = Field(ge=-840, le=840, default=480)
    purpose: Literal["journal", "calibration"] = "journal"
    device_id: str = Field(default="android", max_length=80)

class SessionClose(StrictModel):
    final_seq: int = Field(ge=-1, le=1000000)
    interrupted: bool = False

class ChunkMeta(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    start_sample: int = Field(ge=0)
    sample_count: int = Field(gt=0, le=480000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_monotonic_ns: int = Field(default=0, ge=0)
    clock_drift_ms: int = 0

class VadConfig(StrictModel):
    threshold: float = Field(default=.35, ge=.05, le=.95)
    negative_threshold: float = Field(default=.20, ge=0, le=.9)
    min_speech_ms: int = Field(default=96, ge=0, le=1000)
    min_silence_ms: int = Field(default=800, ge=32, le=5000)
    pad_ms: int = Field(default=300, ge=0, le=1500)
    retain_uncertain: bool = True
    uncertainty_threshold: float = Field(default=.12, ge=0, le=.9)
    max_segment_ms: int = Field(default=30000, ge=1000, le=120000)
    rms_gate: float = Field(default=0.005, ge=0.0, le=1.0)

    @computed_field
    @property
    def end_threshold(self) -> float:
        return self.negative_threshold

    @computed_field
    @property
    def uncertain_threshold(self) -> float:
        return self.uncertainty_threshold

    @model_validator(mode="before")
    @classmethod
    def sync_aliases(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if "end_threshold" in data:
                val = data.pop("end_threshold")
                if "negative_threshold" not in data:
                    data["negative_threshold"] = val
            if "uncertain_threshold" in data:
                val = data.pop("uncertain_threshold")
                if "uncertainty_threshold" not in data:
                    data["uncertainty_threshold"] = val
        return data

    @model_validator(mode="after")
    def thresholds(self):
        if self.negative_threshold >= self.threshold:
            raise ValueError("negative_threshold must be below threshold")
        if self.uncertainty_threshold > self.threshold:
            raise ValueError("uncertainty_threshold must not exceed threshold")
        return self

class VadConfigPatch(StrictModel):
    threshold: float | None = Field(default=None, ge=.05, le=.95)
    negative_threshold: float | None = Field(default=None, ge=0, le=.9)
    min_speech_ms: int | None = Field(default=None, ge=0, le=1000)
    min_silence_ms: int | None = Field(default=None, ge=32, le=5000)
    pad_ms: int | None = Field(default=None, ge=0, le=1500)
    retain_uncertain: bool | None = None
    uncertainty_threshold: float | None = Field(default=None, ge=0, le=.9)
    max_segment_ms: int | None = Field(default=None, ge=1000, le=120000)
    rms_gate: float | None = Field(default=None, ge=0.0, le=1.0)

    @computed_field
    @property
    def end_threshold(self) -> float | None:
        return self.negative_threshold

    @computed_field
    @property
    def uncertain_threshold(self) -> float | None:
        return self.uncertainty_threshold

    @model_validator(mode="before")
    @classmethod
    def sync_aliases(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if "end_threshold" in data:
                val = data.pop("end_threshold")
                if "negative_threshold" not in data:
                    data["negative_threshold"] = val
            if "uncertain_threshold" in data:
                val = data.pop("uncertain_threshold")
                if "uncertainty_threshold" not in data:
                    data["uncertainty_threshold"] = val
        return data

VALID_VAD_PROVIDERS = {
    "passthrough", "energy_demo", "silero", "silero_v5", "silero_v4", "silero_v3",
    "ten_vad", "ten_vad_int8", "webrtc", "webrtc_0", "webrtc_1", "webrtc_2", "webrtc_3"
}

class AsrConfig(StrictModel):
    asr_provider: Literal["disabled", "mock", "gemini", "vertexai"] = "disabled"
    gemini_key: str = ""
    has_gemini_key: bool = False
    gemini_model: str = "gemini-3.5-transcribe"
    gemini_mode: Literal["transcribe", "structured"] = "transcribe"
    vertex_project: str = ""
    vertex_location: str = "global"
    vertex_model: str = "gemini-3.8-flash"
    vertex_endpoint: str = ""
    vertex_api_key: str = ""
    has_vertex_api_key: bool = False
    asr_configured: bool = False

class AsrConfigPatch(StrictModel):
    asr_provider: Literal["disabled", "mock", "gemini", "vertexai"] | None = None
    gemini_key: str | None = None
    gemini_model: str | None = None
    gemini_mode: Literal["transcribe", "structured"] | None = None
    vertex_project: str | None = None
    vertex_location: str | None = None
    vertex_model: str | None = None
    vertex_endpoint: str | None = None
    vertex_api_key: str | None = None

class VadRuntimeConfig(StrictModel):
    vad_provider: str = "passthrough"
    vad_model: str = ""
    active_profile_id: str | None = None
    active_profile_name: str | None = None
    config: VadConfig = Field(default_factory=VadConfig)

class VadRuntimeConfigPatch(StrictModel):
    vad_provider: str | None = None
    vad_model: str | None = None
    config: VadConfigPatch | None = None

    @field_validator("vad_provider")
    @classmethod
    def validate_vad_prov(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_VAD_PROVIDERS:
            raise ValueError(f"Unknown VAD provider: {v}")
        return v

class VoiceprintConfig(StrictModel):
    speaker_model_id: str = Field(default="wespeaker_ecapa_tdnn512", min_length=1, max_length=120)
    similarity_threshold: float = Field(default=0.8, ge=0.1, le=0.99)
    margin: float = Field(default=0.1, ge=0.0, le=0.5)
    min_speech_ms: int = Field(default=1500, ge=100, le=60000)
    auto_match_enabled: bool = True
    consent_required: bool = True

class VoiceprintConfigPatch(StrictModel):
    speaker_model_id: str | None = Field(default=None, min_length=1, max_length=120)
    similarity_threshold: float | None = Field(default=None, ge=0.1, le=0.99)
    margin: float | None = Field(default=None, ge=0.0, le=0.5)
    min_speech_ms: int | None = Field(default=None, ge=100, le=60000)
    auto_match_enabled: bool | None = None
    consent_required: bool | None = None

class RuntimeConfigResponse(StrictModel):
    asr: AsrConfig
    vad: VadRuntimeConfig
    voiceprint: VoiceprintConfig

    # Top-level flattened fields for client convenience
    asr_provider: Literal["disabled", "mock", "gemini", "vertexai"]
    gemini_key: str
    has_gemini_key: bool
    gemini_model: str
    gemini_mode: Literal["transcribe", "structured"]
    vertex_project: str
    vertex_location: str
    vertex_model: str
    vertex_endpoint: str
    vertex_api_key: str
    has_vertex_api_key: bool
    vad_provider: str
    vad_model: str
    vad_config: VadConfig
    speaker_model_id: str
    similarity_threshold: float
    margin: float
    min_speech_ms: int
    auto_match_enabled: bool
    consent_required: bool

class RuntimeConfigPatch(StrictModel):
    asr: AsrConfigPatch | None = None
    vad: VadRuntimeConfigPatch | None = None
    voiceprint: VoiceprintConfigPatch | None = None

    asr_provider: Literal["disabled", "mock", "gemini", "vertexai"] | None = None
    gemini_key: str | None = None
    gemini_model: str | None = None
    gemini_mode: Literal["transcribe", "structured"] | None = None
    vertex_project: str | None = None
    vertex_location: str | None = None
    vertex_model: str | None = None
    vertex_endpoint: str | None = None
    vertex_api_key: str | None = None

    vad_provider: str | None = None
    vad_model: str | None = None
    vad_config: VadConfigPatch | None = None
    config: VadConfigPatch | None = None

    speaker_model_id: str | None = Field(default=None, min_length=1, max_length=120)
    similarity_threshold: float | None = Field(default=None, ge=0.1, le=0.99)
    margin: float | None = Field(default=None, ge=0.0, le=0.5)
    min_speech_ms: int | None = Field(default=None, ge=100, le=60000)
    auto_match_enabled: bool | None = None
    consent_required: bool | None = None

    @field_validator("vad_provider")
    @classmethod
    def validate_vad_prov(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_VAD_PROVIDERS:
            raise ValueError(f"Unknown VAD provider: {v}")
        return v

class ProfileCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    config: VadConfig = Field(default_factory=VadConfig)
    activate: bool = False

class Interval(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self

class EvaluationRequest(StrictModel):
    chunk_id: str
    speech_intervals: list[Interval] = Field(max_length=200)
    fully_labeled: bool = False
    config: VadConfig = Field(default_factory=VadConfig)
    scenario: str = Field(default="quiet", max_length=80)

class SpeakerCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=1000)

class SpeakerAssign(StrictModel):
    speaker_id: str | None = None

class VoiceprintCreate(StrictModel):
    model_id: str = Field(min_length=1, max_length=120)
    embedding: list[float] = Field(min_length=8, max_length=4096)
    evidence_segment_id: str
    consent: bool

class VoiceMatch(StrictModel):
    model_id: str = Field(min_length=1, max_length=120)
    embedding: list[float] = Field(min_length=8, max_length=4096)
    threshold: float | None = Field(default=None, ge=0, le=1)
    margin: float | None = Field(default=None, ge=0, le=1)

class EventTypeCreate(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,39}$")
    name: str = Field(min_length=1, max_length=40)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    color: str = Field(default="#61BBA6", pattern=r"^#[0-9a-fA-F]{6}$")

class EventPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    type_id: str | None = None

class MemoryPatch(StrictModel):
    text: str | None = Field(default=None, min_length=1, max_length=8000)
    confirmed: bool | None = None

class AsrSegment(StrictModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(max_length=16000)
    speaker_label: str = Field(default="unknown", max_length=80)
    timing_quality: Literal["word", "estimated", "chunk"] = "estimated"
    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("invalid transcript interval")
        return self


# ============================================================================
# Tenancy & Multi-Tenant Entities (Milestone M2)
# ============================================================================

class TenancyContext(StrictModel):
    user_id: str
    tenant_id: str
    vault_id: str
    dp_instance_id: str | None = None
    email: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["user"])
    auth_mode: Literal["jwt", "dp_session", "api_key", "bearer_legacy"] = "jwt"

class TenantDto(StrictModel):
    tenant_id: str
    name: str
    slug: str
    plan_tier: Literal["free", "pro", "enterprise"] = "free"
    status: Literal["active", "suspended", "disconnected"] = "active"
    is_active: bool = False
    settings: dict = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

class TenantListResponse(StrictModel):
    tenants: list[TenantDto]

class TenantSwitchResponse(StrictModel):
    tenant_id: str
    vault_id: str
    access_token: str

class VaultDto(StrictModel):
    vault_id: str
    tenant_id: str
    name: str
    is_default: bool = False
    status: Literal["initializing", "ready", "locked"] = "ready"
    storage_used_bytes: int = 0
    storage_quota_bytes: int = 10737418240
    created_at: str | None = None
    updated_at: str | None = None

class VaultListResponse(StrictModel):
    vaults: list[VaultDto]

class DpInstanceCreate(StrictModel):
    dp_instance_name: str = Field(min_length=1, max_length=255)
    device_kind: Literal["phone", "watch", "official_cloud", "agent"]
    device_metadata: dict = Field(default_factory=dict)

class DpInstanceDto(StrictModel):
    dp_instance_id: str
    dp_instance_name: str
    dp_unit_url: str = ""
    dp_base_url: str = ""
    device_kind: Literal["phone", "watch", "official_cloud", "agent"]
    is_primary: bool = False
    is_official: bool = False
    last_active_at: str | None = None
    created_at: str | None = None

class DpInstanceListResponse(StrictModel):
    instances: list[DpInstanceDto]

class DeviceBindingCreate(StrictModel):
    dp_instance_id: str
    device_unique_identifier: str = Field(min_length=1, max_length=255)
    device_model: str | None = None
    os_name: Literal["android", "ios", "watchos", "linux"]
    os_version: str | None = None
    push_token: str | None = None

class DeviceBindingDto(StrictModel):
    binding_id: str
    dp_instance_id: str
    device_unique_identifier: str
    device_model: str | None = None
    os_name: str
    os_version: str | None = None
    status: Literal["bound", "revoked", "disconnected"] = "bound"
    dp_session_token: str | None = None
    last_active_at: str | None = None

class DeviceBindingListResponse(StrictModel):
    bindings: list[DeviceBindingDto]

class PushTokenRegistration(StrictModel):
    device_unique_identifier: str
    push_token: str
    push_provider: str = "fcm"
    app_version: str | None = None

# ============================================================================
# Single Primary Lease Protocol (Contract C3)
# ============================================================================

class PrimaryDeviceRequest(StrictModel):
    dp_instance_id: str
    vault_id: str

class PrimaryDeviceResponse(StrictModel):
    dp_instance_id: str
    is_primary: bool = True
    effective_from: str

# ============================================================================
# Audio Ingestion & Multipart Payload (Contract C2)
# ============================================================================

class CaptureMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    split_reason: Literal["window", "silence", "size_limit", "user_stop", "silence_window", "hard_limit"] = "window"
    sample_rate: int = 16000
    bitrate_kbps: int = 48
    channels: int = 1
    battery_level: float | None = None
    is_charging: bool | None = None
    live_session_id: str | None = None
    dp_instance_id: str | None = None
    device_id: str | None = None
    silence_ms_before_cut: int | None = None
    segment_index: int | None = None
    recording_id: str | None = None

class UploadRecordingResponse(StrictModel):
    recording_id: str
    asset_id: str
    artifact_id: str
    storage_key: str
    status: Literal["queued_for_processing"] = "queued_for_processing"

UploadResponse = UploadRecordingResponse

# ============================================================================
# Listening Schedules & MCP API Keys
# ============================================================================

class WeeklyWindowDto(StrictModel):
    weekday: int = Field(ge=1, le=7)  # 1 (Mon) - 7 (Sun)
    start_minutes: int = Field(ge=0, le=1440)
    end_minutes: int = Field(ge=0, le=1440)
    window_start: str = "09:00"
    window_end: str = "18:00"

    @model_validator(mode="after")
    def validate_window_order(self):
        if self.end_minutes <= self.start_minutes:
            raise ValueError("end_minutes must be greater than start_minutes")
        return self

class ListeningScheduleDto(StrictModel):
    weekly_windows: list[WeeklyWindowDto] = Field(default_factory=list)
    is_enabled: bool = True
    revision: int = 1
    timezone: str = "UTC"

class McpApiKeyCreate(StrictModel):
    key_name: str = Field(min_length=1, max_length=128)
    dp_instance_id: str
    scopes: list[str] = Field(default_factory=lambda: ["search_memory", "search_sections"])

class McpApiKeyCreated(StrictModel):
    key_id: str
    key_name: str
    api_key: str  # Plaintext key returned once
    key_prefix: str = "hm_live_"
    scopes: list[str]
    created_at: str

# ============================================================================
# Speakers & Voiceprint Clusters DTOs (Milestone M3)
# ============================================================================

class SpeakerProfileDto(StrictModel):
    speaker_id: str
    tenant_id: str
    vault_id: str
    user_id: str
    name: str
    suspected_name: str | None = None
    role_label: str | None = None
    color_palette: str | None = None
    sample_count: int = 0
    avatar_url: str | None = None
    created_at: str
    updated_at: str

class SpeakerListResponse(StrictModel):
    speakers: list[SpeakerProfileDto]

class SpeakerCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    role_label: str | None = None
    cluster_id: str | None = None

class SpeakerCreateResponse(StrictModel):
    speaker_id: str
    name: str
    created_at: str

class VoiceprintClusterDto(StrictModel):
    cluster_id: str
    tenant_id: str
    vault_id: str
    speaker_id: str | None = None
    status: Literal["pending", "identified", "enrolled", "merged", "dismissed"]
    audio_sample_url: str | None = None
    confidence_score: float = 0.0
    occurrences_count: int = 1
    created_at: str
    updated_at: str

class ClusterListResponse(StrictModel):
    clusters: list[VoiceprintClusterDto]

class ClusterSampleDto(StrictModel):
    sample_id: str
    segment_id: str | None = None
    session_id: str | None = None
    audio_url: str
    start_ms: int
    end_ms: int
    duration_ms: int
    confidence: float = 1.0

class ClusterSamplesResponse(StrictModel):
    cluster_id: str
    samples: list[ClusterSampleDto]

class ClusterPatchRequest(StrictModel):
    action: Literal["mark_new", "mark_existing", "dismiss"]
    name: str | None = None
    notes: str | None = None
    role_label: str | None = None
    speaker_id: str | None = None

class ClusterPatchResponse(StrictModel):
    cluster_id: str
    status: str
    speaker_id: str | None = None


# =============================================================================
# Milestone M4: Chat, Processing Chains & Mutations Models
# =============================================================================

class ChatTurnCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str | None = None
    prompt: str | None = None
    client_turn_id: str | None = None
    client_context: dict[str, Any] | None = None
    active_memory_window: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_content(self):
        text = (self.message or self.prompt or "").strip()
        if not text:
            raise ValueError("Either 'message' or 'prompt' must be provided and non-empty")
        return self

    @property
    def effective_prompt(self) -> str:
        return (self.message or self.prompt or "").strip()


class ChatTurnCancelResponse(StrictModel):
    thread_id: str
    turn_id: str
    status: Literal["cancelled"] = "cancelled"
    cancelled_at: str


class ChatThreadCreateRequest(StrictModel):
    thread_id: str | None = None
    title: str | None = None
    metadata: dict[str, Any] | None = None


class ChatThreadDto(StrictModel):
    thread_id: str
    tenant_id: str
    vault_id: str
    user_id: str
    title: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class ChatTurnDto(StrictModel):
    turn_id: str
    thread_id: str
    tenant_id: str
    vault_id: str
    turn_index: int
    prompt: str
    response_text: str
    status: str
    tokens_used: int
    client_turn_id: str | None = None
    created_at: str
    completed_at: str | None = None


ProcessingChainStatus = Literal["pending", "running", "completed", "failed"]
ProcessingChainStep = Literal["slicing", "vad", "transcribing", "diarizing", "summarizing"]


class ProcessingChainDto(StrictModel):
    chain_id: str
    recording_id: str
    live_session_id: str | None = None
    status: ProcessingChainStatus
    current_step: ProcessingChainStep
    progress_pct: float = Field(ge=0.0, le=100.0)
    error_code: str | None = None
    error_message: str | None = None
    analyzing_start_at: str
    analyzing_end_at: str | None = None
    started_at: str | None = None
    updated_at: str


class ProcessingChainsResponse(StrictModel):
    chains: list[ProcessingChainDto]
    total: int
    server_time: str


RecordingMutationType = Literal[
    "segment_uploaded",
    "segment_transcribed",
    "cluster_updated",
    "speaker_renamed",
    "event_created",
    "redact_audio",
    "retention_updated",
]


class RecordingMutationDto(StrictModel):
    cursor: int
    id: int | None = None
    tenant_id: str
    vault_id: str
    entity_type: str
    entity_id: str
    mutation_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_json: str | None = None
    created_at: str


class RecordingMutationsResponse(StrictModel):
    mutations: list[RecordingMutationDto]
    next_cursor: int
    latest_cursor: int = 0
    has_more: bool


class ClientMutationAction(StrictModel):
    mutation_id: str | None = None
    live_session_id: str | None = None
    action: str
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=0, ge=0)
    delete_audio: bool = True
    retain_transcript: bool = False


class ClientRecordingMutationsRequest(StrictModel):
    mutations: list[dict[str, Any]] | list[ClientMutationAction] = Field(min_length=1)


class ClientRecordingMutationsResponse(StrictModel):
    status: Literal["synced"] = "synced"
    processed_count: int


class MemoryEventPatchRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=64)


class DpInstanceEnsureRequest(StrictModel):
    dp_instance_id: str = Field(min_length=1, max_length=64)
    dp_instance_name: str = Field(default="Hemory Phone", min_length=1, max_length=255)
    device_kind: Literal["phone", "watch", "official_cloud", "agent"] = "phone"
    vault_id: str | None = None
    make_primary: bool = True




