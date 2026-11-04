from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator

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
    end_threshold: float = Field(default=.20, ge=0, le=.9)
    min_speech_ms: int = Field(default=96, ge=0, le=1000)
    min_silence_ms: int = Field(default=800, ge=32, le=5000)
    pad_ms: int = Field(default=300, ge=0, le=1500)
    retain_uncertain: bool = True
    uncertainty_threshold: float = Field(default=.12, ge=0, le=.9)
    uncertain_threshold: float = Field(default=.12, ge=0, le=.9)
    max_segment_ms: int = Field(default=30000, ge=1000, le=120000)
    rms_gate: float = Field(default=0.005, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def sync_aliases(cls, data):
        if isinstance(data, dict):
            if "negative_threshold" in data and "end_threshold" not in data:
                data["end_threshold"] = data["negative_threshold"]
            elif "end_threshold" in data and "negative_threshold" not in data:
                data["negative_threshold"] = data["end_threshold"]
            if "uncertainty_threshold" in data and "uncertain_threshold" not in data:
                data["uncertain_threshold"] = data["uncertainty_threshold"]
            elif "uncertain_threshold" in data and "uncertainty_threshold" not in data:
                data["uncertainty_threshold"] = data["uncertain_threshold"]
        return data

    @model_validator(mode="after")
    def thresholds(self):
        if self.negative_threshold >= self.threshold or self.end_threshold >= self.threshold:
            raise ValueError("negative_threshold must be below threshold")
        if self.uncertainty_threshold > self.threshold or self.uncertain_threshold > self.threshold:
            raise ValueError("uncertainty_threshold must not exceed threshold")
        return self

class VadConfigPatch(StrictModel):
    threshold: float | None = Field(default=None, ge=.05, le=.95)
    negative_threshold: float | None = Field(default=None, ge=0, le=.9)
    end_threshold: float | None = Field(default=None, ge=0, le=.9)
    min_speech_ms: int | None = Field(default=None, ge=0, le=1000)
    min_silence_ms: int | None = Field(default=None, ge=32, le=5000)
    pad_ms: int | None = Field(default=None, ge=0, le=1500)
    retain_uncertain: bool | None = None
    uncertainty_threshold: float | None = Field(default=None, ge=0, le=.9)
    uncertain_threshold: float | None = Field(default=None, ge=0, le=.9)
    max_segment_ms: int | None = Field(default=None, ge=1000, le=120000)
    rms_gate: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def sync_aliases(cls, data):
        if isinstance(data, dict):
            if "negative_threshold" in data and "end_threshold" not in data:
                data["end_threshold"] = data["negative_threshold"]
            elif "end_threshold" in data and "negative_threshold" not in data:
                data["negative_threshold"] = data["end_threshold"]
            if "uncertainty_threshold" in data and "uncertain_threshold" not in data:
                data["uncertain_threshold"] = data["uncertainty_threshold"]
            elif "uncertain_threshold" in data and "uncertainty_threshold" not in data:
                data["uncertainty_threshold"] = data["uncertain_threshold"]
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
    threshold: float = Field(default=.80, ge=0, le=1)
    margin: float = Field(default=.10, ge=0, le=1)

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
