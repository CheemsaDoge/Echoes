from __future__ import annotations
import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_VOICEPRINT_ONNX = (
    DEFAULT_DATA_DIR / "models" / "speakers" / "wespeaker_zh_cnceleb_resnet34.onnx"
)


def resolve_vertex_credentials_path(raw_path: str = "") -> tuple[str, str]:
    """Resolve a Vertex AI service account path or shorthand prefix (e.g. ~/.../gcpx).

    Returns (resolved_file_path, detected_project_id).
    If raw_path is an explicit non-matching path, returns (raw_path, "") unchanged
    so downstream validation raises the expected error.
    """
    if not raw_path:
        return "", ""

    expanded = os.path.expanduser(raw_path.strip())
    candidates: list[str] = []

    p = Path(expanded)
    if p.is_file():
        candidates.append(str(p))
    else:
        # Support prefix/glob shorthand such as ~/cheemsadoge/.secret/llmapis/gcpx -> gcp*
        pattern = expanded
        if expanded.endswith("gcpx"):
            pattern = expanded[:-1] + "*"
        elif "*" not in expanded and p.parent.is_dir():
            pattern = expanded + "*"
        matched = sorted(glob.glob(pattern))
        # Prefer zizekanderson_magpie if present among matches
        matched.sort(key=lambda m: (0 if "zizekanderson_magpie" in m else 1, m))
        candidates.extend(matched)

    for cand in candidates:
        try:
            cp = Path(cand)
            if not cp.is_file() or cp.stat().st_size == 0:
                continue
            data = json.loads(cp.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("type") in {"service_account", "authorized_user"}:
                return str(cp), str(data.get("project_id") or data.get("quota_project_id") or "")
        except Exception:
            continue

    return raw_path, ""


def _default_vertex_model() -> str:
    explicit = os.getenv("VERTEX_MODEL")
    if explicit:
        return explicit
    loc = os.getenv("VERTEX_LOCATION", "global")
    return "gemini-3.8-flash" if loc in {"global", "us", "eu"} else "gemini-2.5-flash"


def _default_voiceprint_onnx_path() -> str:
    explicit = os.getenv("ECHOLEDGER_VOICEPRINT_ONNX_PATH", "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    if DEFAULT_VOICEPRINT_ONNX.is_file():
        return str(DEFAULT_VOICEPRINT_ONNX)
    speakers_dir = DEFAULT_DATA_DIR / "models" / "speakers"
    if speakers_dir.is_dir():
        for onnx_file in sorted(speakers_dir.glob("*.onnx")):
            if onnx_file.is_file():
                return str(onnx_file)
    return str(DEFAULT_VOICEPRINT_ONNX)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR))))
    db_path: Path | None = None
    storage_dir: Path | None = None
    voiceprint_onnx_path: str = field(default_factory=_default_voiceprint_onnx_path)
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

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        default_env_data_dir = Path(os.getenv("DATA_DIR", str(DEFAULT_DATA_DIR)))
        is_default_data_dir = self.data_dir.resolve() == default_env_data_dir.resolve()

        if self.db_path is not None:
            self.db_path = Path(os.path.expanduser(str(self.db_path)))
        elif is_default_data_dir and os.getenv("ECHOLEDGER_DB_PATH"):
            self.db_path = Path(os.path.expanduser(os.environ["ECHOLEDGER_DB_PATH"]))

        if self.storage_dir is not None:
            self.storage_dir = Path(os.path.expanduser(str(self.storage_dir)))
        elif is_default_data_dir and os.getenv("ECHOLEDGER_STORAGE_DIR"):
            self.storage_dir = Path(os.path.expanduser(os.environ["ECHOLEDGER_STORAGE_DIR"]))

        if self.vertex_credentials:
            resolved_cred, detected_proj = resolve_vertex_credentials_path(self.vertex_credentials)
            self.vertex_credentials = resolved_cred
            if not self.vertex_project and detected_proj:
                self.vertex_project = detected_proj

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path if self.db_path is not None else (self.data_dir / "echoledger.sqlite3")

    @property
    def resolved_storage_dir(self) -> Path:
        return self.storage_dir if self.storage_dir is not None else (self.data_dir / "recordings")

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
