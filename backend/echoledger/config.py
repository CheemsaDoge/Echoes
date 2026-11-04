from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

def _default_vertex_model() -> str:
    explicit = os.getenv("VERTEX_MODEL")
    if explicit:
        return explicit
    loc = os.getenv("VERTEX_LOCATION", "global")
    return "gemini-3.8-flash" if loc in {"global", "us", "eu"} else "gemini-2.5-flash"

@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))
    api_token: str = field(default_factory=lambda: os.getenv("API_TOKEN", ""))
    mcp_token: str = field(default_factory=lambda: os.getenv("MCP_TOKEN", ""))
    asr_provider: str = field(default_factory=lambda: os.getenv("ASR_PROVIDER", "disabled"))
    gemini_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.5-transcribe"))
    gemini_mode: str = field(default_factory=lambda: os.getenv("GEMINI_MODE", "transcribe"))
    vertex_project: str = field(default_factory=lambda: os.getenv("VERTEX_PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", "")))
    vertex_location: str = field(default_factory=lambda: os.getenv("VERTEX_LOCATION", "global"))
    vertex_model: str = field(default_factory=_default_vertex_model)
    vertex_credentials: str = field(default_factory=lambda: os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""))
    vertex_api_key: str = field(default_factory=lambda: os.getenv("VERTEX_API_KEY", ""))
    vertex_endpoint: str = field(default_factory=lambda: os.getenv("VERTEX_ENDPOINT", ""))
    vad_provider: str = field(default_factory=lambda: os.getenv("VAD_PROVIDER", "passthrough"))
    vad_model: str = field(default_factory=lambda: os.getenv("VAD_MODEL_PATH", ""))
    speaker_model_id: str = field(default_factory=lambda: os.getenv("SPEAKER_MODEL_ID", "wespeaker_ecapa_tdnn512"))
    similarity_threshold: float = field(default_factory=lambda: float(os.getenv("SPEAKER_SIMILARITY_THRESHOLD", "0.8")))
    margin: float = field(default_factory=lambda: float(os.getenv("SPEAKER_MARGIN", "0.1")))
    min_speech_ms: int = field(default_factory=lambda: int(os.getenv("SPEAKER_MIN_SPEECH_MS", "1500")))
    auto_match_enabled: bool = field(default_factory=lambda: os.getenv("SPEAKER_AUTO_MATCH", "true").lower() == "true")
    consent_required: bool = field(default_factory=lambda: os.getenv("SPEAKER_CONSENT_REQUIRED", "true").lower() == "true")
    max_upload_bytes: int = 4_000_000
    batch_chunks: int = 4
    batch_wait_seconds: int = 90
    daily_audio_seconds: int | None = field(default=None)
    run_worker: bool = field(default_factory=lambda: os.getenv("RUN_WORKER", "true").lower() == "true")
    allowed_origins: tuple[str, ...] = field(default_factory=lambda: tuple(x for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x))

    def validate(self) -> None:
        if len(self.api_token) < 24 or self.api_token.startswith("REPLACE_"):
            raise ValueError("Set API_TOKEN to a random secret of at least 24 characters.")
        if self.mcp_token and (len(self.mcp_token) < 24 or self.mcp_token == self.api_token or self.mcp_token.startswith("REPLACE_")):
            raise ValueError("MCP_TOKEN must be a distinct random secret of at least 24 characters.")
        if self.asr_provider not in {"disabled", "mock", "gemini", "vertexai"}:
            raise ValueError("Unknown ASR_PROVIDER")
        valid_vads = {
            "passthrough", "energy_demo", "silero", "silero_v5", "silero_v4", "silero_v3",
            "ten_vad", "ten_vad_int8", "webrtc", "webrtc_0", "webrtc_1", "webrtc_2", "webrtc_3"
        }
        if self.vad_provider not in valid_vads:
            raise ValueError("Unknown VAD_PROVIDER")
        if self.gemini_mode not in {"transcribe", "structured"}:
            raise ValueError("Unknown GEMINI_MODE")
        if not (0.1 <= self.similarity_threshold <= 0.99):
            raise ValueError("similarity_threshold must be between 0.1 and 0.99")
        if not (0.0 <= self.margin <= 0.5):
            raise ValueError("margin must be between 0.0 and 0.5")
        if self.min_speech_ms < 100:
            raise ValueError("min_speech_ms must be at least 100")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
