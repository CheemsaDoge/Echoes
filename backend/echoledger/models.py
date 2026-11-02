from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

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
    @model_validator(mode="after")
    def thresholds(self):
        if self.negative_threshold >= self.threshold:
            raise ValueError("negative_threshold must be below threshold")
        if self.uncertainty_threshold > self.threshold:
            raise ValueError("uncertainty_threshold must not exceed threshold")
        return self

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
