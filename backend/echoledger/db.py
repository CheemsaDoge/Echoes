from __future__ import annotations
import contextlib, json, sqlite3, time, uuid
from pathlib import Path
from .models import VadConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS profiles(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL, created_ms INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_profile ON profiles(active) WHERE active=1;
CREATE TABLE IF NOT EXISTS deleted_sessions(id TEXT PRIMARY KEY, deleted_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
 id TEXT PRIMARY KEY, started_at_ms INTEGER NOT NULL, timezone_offset_min INTEGER NOT NULL,
 purpose TEXT NOT NULL, device_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'recording',
 final_seq INTEGER, next_batch_seq INTEGER NOT NULL DEFAULT 0, interrupted INTEGER NOT NULL DEFAULT 0, created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL, start_sample INTEGER NOT NULL, sample_count INTEGER NOT NULL,
 sha256 TEXT NOT NULL, path TEXT NOT NULL, metadata TEXT NOT NULL, created_ms INTEGER NOT NULL,
 batch_id TEXT, UNIQUE(session_id, seq));
CREATE TABLE IF NOT EXISTS batches(
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL, chunk_ids TEXT NOT NULL,
 profile_id TEXT NOT NULL REFERENCES profiles(id), state TEXT NOT NULL DEFAULT 'queued',
 attempts INTEGER NOT NULL DEFAULT 0, available_ms INTEGER NOT NULL, lease_until_ms INTEGER,
 lease_token TEXT, error TEXT, metrics TEXT NOT NULL DEFAULT '{}', created_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_ready ON batches(state, available_ms);
CREATE TABLE IF NOT EXISTS speakers(id TEXT PRIMARY KEY, name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS segments(
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE, ordinal INTEGER NOT NULL,
 start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, text TEXT NOT NULL,
 local_speaker TEXT NOT NULL, speaker_id TEXT REFERENCES speakers(id) ON DELETE SET NULL,
 source_spans TEXT NOT NULL, timing_quality TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0,
 UNIQUE(batch_id,ordinal));
CREATE INDEX IF NOT EXISTS segments_time ON segments(start_ms);
CREATE TABLE IF NOT EXISTS voiceprints(
 id TEXT PRIMARY KEY, speaker_id TEXT NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
 model_id TEXT NOT NULL, embedding TEXT NOT NULL,
 evidence_segment_id TEXT NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
 consent INTEGER NOT NULL CHECK(consent=1), created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS event_types(id TEXT PRIMARY KEY, name TEXT NOT NULL, keywords TEXT NOT NULL, color TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
 type_id TEXT NOT NULL REFERENCES event_types(id), user_edited INTEGER NOT NULL DEFAULT 0,
 is_demo INTEGER NOT NULL DEFAULT 0, updated_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS events_time ON events(start_ms);
CREATE TABLE IF NOT EXISTS event_segments(
 event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 segment_id TEXT NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
 PRIMARY KEY(event_id,segment_id));
CREATE TABLE IF NOT EXISTS memories(
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
 text TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0, user_edited INTEGER NOT NULL DEFAULT 0, created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS changes(
 cursor INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entity_id TEXT NOT NULL, created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS usage(
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
 day TEXT NOT NULL, audio_seconds REAL NOT NULL, provider TEXT NOT NULL, created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS calibrations(
 id TEXT PRIMARY KEY, chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
 scenario TEXT NOT NULL, config TEXT NOT NULL, labels TEXT NOT NULL, result TEXT NOT NULL, created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv_settings(
 key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ms INTEGER NOT NULL);
"""

def now_ms() -> int:
    return time.time_ns() // 1_000_000

def uid() -> str:
    return str(uuid.uuid4())

def emit(conn: sqlite3.Connection, kind: str, entity_id: str) -> None:
    conn.execute("INSERT INTO changes(kind,entity_id,created_ms) VALUES(?,?,?)", (kind, entity_id, now_ms()))

class Database:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "echoledger.sqlite3"
        with self.connect() as c:
            c.executescript(SCHEMA)
            versions = [r[0] for r in c.execute("SELECT version FROM schema_version")]
            if versions != [1]:
                raise RuntimeError("Unsupported database schema; migrate explicitly")
            if not c.execute("SELECT 1 FROM profiles").fetchone():
                c.execute("INSERT INTO profiles VALUES(?,?,?,?,1)", ("default-v1", "Conservative", VadConfig().model_dump_json(), now_ms()))
            types = [
                ("unknown", "Uncategorized", [], "#718094"),
                ("study", "Study", ["\u7b97\u6cd5", "\u5b66\u4e60", "\u4f5c\u4e1a", "\u8bba\u6587", "algorithm"], "#9ABD55"),
                ("meeting", "Meeting", ["\u7ec4\u4f1a", "\u5f00\u4f1a", "\u9879\u76ee", "meeting"], "#76A9DC"),
                ("meal", "Meal", ["\u5403\u996d", "\u5348\u996d", "\u70b9\u9910", "\u665a\u996d", "lunch"], "#C5A767"),
                ("social", "Social", ["\u804a\u5929", "\u6563\u6b65", "\u670b\u53cb", "chat"], "#61BBA6"),
            ]
            for key, name, words, color in types:
                c.execute("INSERT OR IGNORE INTO event_types VALUES(?,?,?,?)", (key, name, json.dumps(words), color))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextlib.contextmanager
    def connect(self, immediate: bool = False):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        try:
            if immediate:
                c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def active_profile(self) -> dict:
        row = self.one("SELECT * FROM profiles WHERE active=1")
        if not row:
            with self.connect(immediate=True) as c:
                if not c.execute("SELECT 1 FROM profiles WHERE id='default-v1'").fetchone():
                    c.execute("INSERT INTO profiles VALUES(?,?,?,?,1)", ("default-v1", "Conservative", VadConfig().model_dump_json(), now_ms()))
                else:
                    c.execute("UPDATE profiles SET active=1 WHERE id='default-v1'")
            row = self.one("SELECT * FROM profiles WHERE active=1")
        return row

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv_settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect(immediate=True) as c:
            c.execute("""INSERT INTO kv_settings(key, value, updated_ms) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ms=excluded.updated_ms""",
                (key, value, now_ms()))

    def get_all_settings(self) -> dict[str, str]:
        rows = self.all("SELECT key, value FROM kv_settings")
        return {r["key"]: r["value"] for r in rows}

    def load_into_settings(self, settings) -> None:
        for key, val in self.get_all_settings().items():
            if key == "asr.provider" and hasattr(settings, "asr_provider"):
                settings.asr_provider = val
            elif key == "asr.gemini_key" and hasattr(settings, "gemini_key"):
                settings.gemini_key = val
            elif key == "asr.gemini_model" and hasattr(settings, "gemini_model"):
                settings.gemini_model = val
            elif key == "asr.gemini_mode" and hasattr(settings, "gemini_mode"):
                settings.gemini_mode = val
            elif key == "asr.vertex_project" and hasattr(settings, "vertex_project"):
                settings.vertex_project = val
            elif key == "asr.vertex_location" and hasattr(settings, "vertex_location"):
                settings.vertex_location = val
            elif key == "asr.vertex_model" and hasattr(settings, "vertex_model"):
                settings.vertex_model = val
            elif key == "asr.vertex_endpoint" and hasattr(settings, "vertex_endpoint"):
                settings.vertex_endpoint = val
            elif key == "asr.vertex_api_key" and hasattr(settings, "vertex_api_key"):
                settings.vertex_api_key = val
            elif key == "vad.provider" and hasattr(settings, "vad_provider"):
                settings.vad_provider = val
            elif key == "vad.model" and hasattr(settings, "vad_model"):
                settings.vad_model = val
            elif key == "voiceprint.speaker_model_id" and hasattr(settings, "speaker_model_id"):
                settings.speaker_model_id = val
            elif key == "voiceprint.similarity_threshold" and hasattr(settings, "similarity_threshold"):
                try:
                    settings.similarity_threshold = float(val)
                except ValueError:
                    pass
            elif key == "voiceprint.margin" and hasattr(settings, "margin"):
                try:
                    settings.margin = float(val)
                except ValueError:
                    pass
            elif key == "voiceprint.min_speech_ms" and hasattr(settings, "min_speech_ms"):
                try:
                    settings.min_speech_ms = int(val)
                except ValueError:
                    pass
            elif key == "voiceprint.auto_match_enabled" and hasattr(settings, "auto_match_enabled"):
                settings.auto_match_enabled = val.lower() == "true"
            elif key == "voiceprint.consent_required" and hasattr(settings, "consent_required"):
                settings.consent_required = val.lower() == "true"
