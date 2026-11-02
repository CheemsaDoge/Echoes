from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))
    api_token: str = field(default_factory=lambda: os.getenv("API_TOKEN", ""))
    mcp_token: str = field(default_factory=lambda: os.getenv("MCP_TOKEN", ""))
    asr_provider: str = field(default_factory=lambda: os.getenv("ASR_PROVIDER", "disabled"))
    gemini_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.5-transcribe"))
    gemini_mode: str = field(default_factory=lambda: os.getenv("GEMINI_MODE", "transcribe"))
    vertex_project: str = field(default_factory=lambda: os.getenv("VERTEX_PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", "")))
    vertex_location: str = field(default_factory=lambda: os.getenv("VERTEX_LOCATION", "us-central1"))
    vertex_model: str = field(default_factory=lambda: os.getenv("VERTEX_MODEL", "gemini-2.5-flash"))
    vertex_credentials: str = field(default_factory=lambda: os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""))
    vertex_api_key: str = field(default_factory=lambda: os.getenv("VERTEX_API_KEY", ""))
    vertex_endpoint: str = field(default_factory=lambda: os.getenv("VERTEX_ENDPOINT", ""))
    vad_provider: str = field(default_factory=lambda: os.getenv("VAD_PROVIDER", "passthrough"))
    vad_model: str = field(default_factory=lambda: os.getenv("VAD_MODEL_PATH", ""))
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
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
