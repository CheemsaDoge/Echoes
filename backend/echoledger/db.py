from __future__ import annotations
import contextlib, datetime, json, re, sqlite3, time, uuid
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

def sanitize_fts_tokens(query: str) -> list[str]:
    """Extract clean, safe alphanumeric and unicode tokens for SQLite FTS5 MATCH queries."""
    sanitized = "".join(ch for ch in query if ch.isprintable() and ch != "\x00")
    tokens = re.findall(r"\w+", sanitized, flags=re.UNICODE)
    return [t.strip()[:128] for t in tokens if t.strip()][:32]

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
            migrations_dir = Path(__file__).parent / "migrations"
            if migrations_dir.exists():
                for m_file in sorted(migrations_dir.glob("*.sql")):
                    c.executescript(m_file.read_text(encoding="utf-8"))
            try:
                c_fts = c.execute("SELECT count(*) FROM section_messages_fts").fetchone()[0]
                if c_fts == 0:
                    c.execute("INSERT INTO section_messages_fts(message_id, content) SELECT message_id, content FROM section_messages")
            except Exception:
                pass
            try:
                table_info = [r[1] for r in c.execute("PRAGMA table_info(section_messages)").fetchall()]
                if table_info and "cluster_id" not in table_info:
                    c.execute("ALTER TABLE section_messages ADD COLUMN cluster_id VARCHAR(64) NULL REFERENCES voiceprint_clusters(cluster_id) ON DELETE SET NULL")
                    c.execute("CREATE INDEX IF NOT EXISTS idx_section_messages_cluster ON section_messages(cluster_id)")
                vc_sql_row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='voiceprint_clusters'").fetchone()
                if vc_sql_row and vc_sql_row[0] and ("'enrolled'" not in vc_sql_row[0] or "'merged'" not in vc_sql_row[0]):
                    c.execute("PRAGMA foreign_keys=OFF")
                    c.execute("""
                        CREATE TABLE voiceprint_clusters_new (
                            cluster_id VARCHAR(64) PRIMARY KEY,
                            tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                            vault_id VARCHAR(64) NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
                            speaker_id VARCHAR(64) NULL REFERENCES speaker_profiles(speaker_id) ON DELETE SET NULL,
                            status VARCHAR(32) NOT NULL DEFAULT 'pending',
                            embedding_blob BYTEA NULL,
                            audio_sample_url TEXT NULL,
                            confidence_score REAL NOT NULL DEFAULT 0.0,
                            occurrences_count INTEGER NOT NULL DEFAULT 1,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            CONSTRAINT chk_clusters_status CHECK (status IN ('pending', 'identified', 'enrolled', 'merged', 'dismissed'))
                        )
                    """)
                    c.execute("INSERT INTO voiceprint_clusters_new SELECT * FROM voiceprint_clusters")
                    c.execute("DROP TABLE voiceprint_clusters")
                    c.execute("ALTER TABLE voiceprint_clusters_new RENAME TO voiceprint_clusters")
                    c.execute("CREATE INDEX IF NOT EXISTS idx_voiceprint_clusters_status ON voiceprint_clusters(vault_id, status)")
                    c.execute("CREATE INDEX IF NOT EXISTS idx_voiceprint_clusters_speaker ON voiceprint_clusters(speaker_id)")
                    c.execute("PRAGMA foreign_keys=ON")
            except Exception:
                pass
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
            if not c.execute("SELECT 1 FROM tenants WHERE tenant_id='ten_default'").fetchone():
                c.execute(
                    "INSERT OR IGNORE INTO tenants (tenant_id, name, slug, plan_tier, status) VALUES ('ten_default', 'Default Tenant', 'default', 'free', 'active')"
                )
                c.execute(
                    "INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name, is_default) VALUES ('vlt_default', 'ten_default', 'Default Vault', 1)"
                )
                c.execute(
                    "INSERT OR IGNORE INTO users (user_id, tenant_id, email, full_name) VALUES ('usr_default', 'ten_default', 'user@hemory.local', 'Default User')"
                )
                c.execute(
                    "INSERT OR IGNORE INTO dp_instances (dp_instance_id, tenant_id, vault_id, user_id, dp_instance_name, device_kind, is_primary) VALUES ('dp_default', 'ten_default', 'vlt_default', 'usr_default', 'Default Phone', 'phone', 1)"
                )
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

    def get_tenant(self, tenant_id: str) -> dict | None:
        return self.one("SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,))

    def list_tenants(self) -> list[dict]:
        return self.all("SELECT * FROM tenants ORDER BY created_at")

    def get_vault(self, vault_id: str, tenant_id: str | None = None) -> dict | None:
        if tenant_id:
            return self.one("SELECT * FROM vaults WHERE vault_id=? AND tenant_id=?", (vault_id, tenant_id))
        return self.one("SELECT * FROM vaults WHERE vault_id=?", (vault_id,))

    def list_vaults(self, tenant_id: str) -> list[dict]:
        return self.all("SELECT * FROM vaults WHERE tenant_id=? ORDER BY created_at", (tenant_id,))

    def get_user(self, user_id: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE user_id=?", (user_id,))

    def get_dp_instance(self, dp_instance_id: str) -> dict | None:
        return self.one("SELECT * FROM dp_instances WHERE dp_instance_id=?", (dp_instance_id,))

    def list_dp_instances(self, vault_id: str) -> list[dict]:
        return self.all("SELECT * FROM dp_instances WHERE vault_id=? ORDER BY created_at", (vault_id,))

    def get_primary_dp_instance(self, tenant_id: str, vault_id: str, user_id: str | None = None) -> dict | None:
        if user_id:
            return self.one(
                "SELECT * FROM dp_instances WHERE tenant_id=? AND vault_id=? AND user_id=? AND is_primary=1",
                (tenant_id, vault_id, user_id),
            )
        return self.one(
            "SELECT * FROM dp_instances WHERE tenant_id=? AND vault_id=? AND is_primary=1",
            (tenant_id, vault_id),
        )

    def set_primary_dp_instance(self, tenant_id: str, vault_id: str, user_id: str, dp_instance_id: str) -> str:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect(immediate=True) as c:
            c.execute(
                "UPDATE dp_instances SET is_primary=0, updated_at=? WHERE tenant_id=? AND vault_id=? AND user_id=? AND is_primary!=0",
                (now_iso, tenant_id, vault_id, user_id),
            )
            c.execute(
                "UPDATE dp_instances SET is_primary=1, updated_at=?, last_active_at=? WHERE dp_instance_id=?",
                (now_iso, now_iso, dp_instance_id),
            )
        return now_iso

    def get_listening_schedule(
        self, tenant_id: str, vault_id: str, user_id: str | None = None, dp_instance_id: str | None = None
    ) -> dict | None:
        if dp_instance_id:
            row = self.one(
                "SELECT * FROM listening_schedules WHERE dp_instance_id=? AND tenant_id=? AND vault_id=?",
                (dp_instance_id, tenant_id, vault_id),
            )
            if row:
                return row
        if user_id:
            return self.one(
                "SELECT * FROM listening_schedules WHERE user_id=? AND tenant_id=? AND vault_id=?",
                (user_id, tenant_id, vault_id),
            )
        return None

    def upsert_recording_session(
        self,
        live_session_id: str,
        tenant_id: str,
        vault_id: str,
        user_id: str,
        dp_instance_id: str,
        local_day_key: str,
        started_at: str,
        title: str | None = None,
    ) -> dict:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect(immediate=True) as c:
            existing = c.execute("SELECT * FROM recording_sessions WHERE live_session_id=?", (live_session_id,)).fetchone()
            if not existing:
                c.execute(
                    """INSERT INTO recording_sessions (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, title, started_at, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'recording', ?, ?)""",
                    (live_session_id, tenant_id, vault_id, user_id, dp_instance_id, local_day_key, title, started_at, now_iso, now_iso),
                )
            else:
                existing_dict = dict(existing)
                if (
                    existing_dict["tenant_id"] != tenant_id
                    or existing_dict["vault_id"] != vault_id
                    or existing_dict["user_id"] != user_id
                ):
                    raise PermissionError(
                        f"Recording session {live_session_id} belongs to a different tenant/vault/user"
                    )
                c.execute(
                    "UPDATE recording_sessions SET updated_at=? WHERE live_session_id=?",
                    (now_iso, live_session_id),
                )
        return self.one("SELECT * FROM recording_sessions WHERE live_session_id=?", (live_session_id,))

    def insert_recording_segment(
        self,
        segment_id: str,
        live_session_id: str,
        tenant_id: str,
        vault_id: str,
        user_id: str,
        dp_instance_id: str,
        segment_index: int,
        storage_key: str,
        started_at: str,
        audio_format: str = "aac",
        sample_rate: int = 16000,
        bitrate_kbps: int = 48,
        duration_ms: int = 0,
        file_size_bytes: int = 0,
        split_reason: str | None = None,
        upload_status: str = "uploaded",
    ) -> dict:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect(immediate=True) as c:
            c.execute(
                """INSERT INTO recording_segments (
                    segment_id, live_session_id, tenant_id, vault_id, user_id, dp_instance_id,
                    segment_index, storage_key, audio_format, sample_rate, bitrate_kbps,
                    duration_ms, file_size_bytes, started_at, finalized_at, split_reason,
                    upload_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    segment_id, live_session_id, tenant_id, vault_id, user_id, dp_instance_id,
                    segment_index, storage_key, audio_format, sample_rate, bitrate_kbps,
                    duration_ms, file_size_bytes, started_at, now_iso, split_reason,
                    upload_status, now_iso, now_iso,
                ),
            )
            emit(c, "recording.segment_uploaded", segment_id)
        return self.one("SELECT * FROM recording_segments WHERE segment_id=?", (segment_id,))

    # ========================================================================
    # Speaker Profiles & Voiceprint Clusters (Milestone M3)
    # ========================================================================

    def list_speaker_profiles(self, vault_id: str) -> list[dict]:
        rows = self.all(
            """SELECT speaker_id, tenant_id, vault_id, user_id, name, suspected_name,
                      role_label, color_palette, sample_count, created_at, updated_at
               FROM speaker_profiles
               WHERE vault_id = ?
               ORDER BY created_at ASC""",
            (vault_id,),
        )
        if not rows:
            # Check legacy speakers table for backward compatibility if empty
            legacy = self.all("SELECT id AS speaker_id, name, notes FROM speakers ORDER BY name")
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            return [
                {
                    "speaker_id": r["speaker_id"],
                    "tenant_id": "ten_default",
                    "vault_id": vault_id,
                    "user_id": "usr_default",
                    "name": r["name"],
                    "suspected_name": None,
                    "role_label": r.get("notes") or None,
                    "color_palette": None,
                    "sample_count": 0,
                    "avatar_url": None,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                }
                for r in legacy
            ]
        result = []
        for r in rows:
            d = dict(r)
            d["avatar_url"] = None
            result.append(d)
        return result

    def get_speaker_profile(self, speaker_id: str, vault_id: str | None = None) -> dict | None:
        if vault_id:
            row = self.one(
                """SELECT speaker_id, tenant_id, vault_id, user_id, name, suspected_name,
                          role_label, color_palette, sample_count, created_at, updated_at
                   FROM speaker_profiles
                   WHERE speaker_id = ? AND vault_id = ?""",
                (speaker_id, vault_id),
            )
        else:
            row = self.one(
                """SELECT speaker_id, tenant_id, vault_id, user_id, name, suspected_name,
                          role_label, color_palette, sample_count, created_at, updated_at
                   FROM speaker_profiles
                   WHERE speaker_id = ?""",
                (speaker_id,),
            )
        if row:
            d = dict(row)
            d["avatar_url"] = None
            return d
        return None

    def create_speaker_profile(
        self,
        speaker_id: str,
        tenant_id: str,
        vault_id: str,
        user_id: str,
        name: str,
        role_label: str | None = None,
        color_palette: str | None = None,
        sample_count: int = 0,
        suspected_name: str | None = None,
    ) -> dict:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect(immediate=True) as c:
            c.execute(
                """INSERT INTO speaker_profiles (
                    speaker_id, tenant_id, vault_id, user_id, name, suspected_name,
                    role_label, color_palette, sample_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    speaker_id, tenant_id, vault_id, user_id, name, suspected_name,
                    role_label, color_palette, sample_count, now_iso, now_iso,
                ),
            )
            emit(c, "speaker.created", speaker_id)
        return self.get_speaker_profile(speaker_id, vault_id)

    def list_voiceprint_clusters(self, vault_id: str, status: str | None = None) -> list[dict]:
        if status:
            rows = self.all(
                """SELECT cluster_id, tenant_id, vault_id, speaker_id, status, confidence_score,
                          occurrences_count, audio_sample_url, created_at, updated_at
                   FROM voiceprint_clusters
                   WHERE vault_id = ? AND status = ?
                   ORDER BY created_at DESC""",
                (vault_id, status),
            )
        else:
            rows = self.all(
                """SELECT cluster_id, tenant_id, vault_id, speaker_id, status, confidence_score,
                          occurrences_count, audio_sample_url, created_at, updated_at
                   FROM voiceprint_clusters
                   WHERE vault_id = ?
                   ORDER BY created_at DESC""",
                (vault_id,),
            )
        result = []
        for r in rows:
            d = dict(r)
            if not d.get("audio_sample_url"):
                d["audio_sample_url"] = f"/clusters/{d['cluster_id']}/audio_preview"
            result.append(d)
        return result

    def get_voiceprint_cluster(self, cluster_id: str, vault_id: str | None = None) -> dict | None:
        if vault_id:
            row = self.one(
                """SELECT cluster_id, tenant_id, vault_id, speaker_id, status, confidence_score,
                          occurrences_count, audio_sample_url, created_at, updated_at
                   FROM voiceprint_clusters
                   WHERE cluster_id = ? AND vault_id = ?""",
                (cluster_id, vault_id),
            )
        else:
            row = self.one(
                """SELECT cluster_id, tenant_id, vault_id, speaker_id, status, confidence_score,
                          occurrences_count, audio_sample_url, created_at, updated_at
                   FROM voiceprint_clusters
                   WHERE cluster_id = ?""",
                (cluster_id,),
            )
        if row:
            d = dict(row)
            if not d.get("audio_sample_url"):
                d["audio_sample_url"] = f"/clusters/{d['cluster_id']}/audio_preview"
            return d
        return None

    def get_cluster_embedding_blob(self, cluster_id: str) -> bytes | None:
        row = self.one("SELECT embedding_blob FROM voiceprint_clusters WHERE cluster_id = ?", (cluster_id,))
        return row["embedding_blob"] if row else None

    def create_voiceprint_cluster(
        self,
        cluster_id: str,
        tenant_id: str,
        vault_id: str,
        status: str = "pending",
        embedding_blob: bytes | None = None,
        audio_sample_url: str | None = None,
        confidence_score: float = 0.0,
        occurrences_count: int = 1,
        speaker_id: str | None = None,
    ) -> dict:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        url = audio_sample_url or f"/clusters/{cluster_id}/audio_preview"
        with self.connect(immediate=True) as c:
            c.execute(
                """INSERT INTO voiceprint_clusters (
                    cluster_id, tenant_id, vault_id, speaker_id, status, embedding_blob,
                    audio_sample_url, confidence_score, occurrences_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cluster_id, tenant_id, vault_id, speaker_id, status, embedding_blob,
                    url, confidence_score, occurrences_count, now_iso, now_iso,
                ),
            )
            emit(c, "cluster.created", cluster_id)
        return self.get_voiceprint_cluster(cluster_id, vault_id)

    def patch_voiceprint_cluster_mark_new(
        self,
        cluster_id: str,
        tenant_id: str,
        vault_id: str,
        user_id: str,
        name: str,
        notes: str | None = None,
        role_label: str | None = None,
    ) -> dict:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        new_spk_id = f"spk_{uuid.uuid4().hex[:12]}"
        label = role_label or notes
        with self.connect(immediate=True) as c:
            cluster = c.execute(
                "SELECT * FROM voiceprint_clusters WHERE cluster_id = ? AND vault_id = ?",
                (cluster_id, vault_id),
            ).fetchone()
            if not cluster:
                raise ValueError("Cluster not found")
            sample_count = cluster["occurrences_count"] if "occurrences_count" in cluster.keys() else 1

            # 1. Create speaker profile
            c.execute(
                """INSERT INTO speaker_profiles (
                    speaker_id, tenant_id, vault_id, user_id, name,
                    role_label, sample_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_spk_id, tenant_id, vault_id, user_id, name, label, sample_count, now_iso, now_iso),
            )

            # 2. Update cluster
            c.execute(
                """UPDATE voiceprint_clusters
                   SET status = 'enrolled', speaker_id = ?, updated_at = ?
                   WHERE cluster_id = ?""",
                (new_spk_id, now_iso, cluster_id),
            )

            # 3. Retroactively update section_messages
            c.execute(
                """UPDATE section_messages
                   SET speaker_id = ?, speaker_name = ?
                   WHERE cluster_id = ?""",
                (new_spk_id, name, cluster_id),
            )
            emit(c, "cluster.enrolled", cluster_id)
            emit(c, "speaker.created", new_spk_id)

        return {
            "cluster_id": cluster_id,
            "status": "enrolled",
            "speaker_id": new_spk_id,
        }

    def patch_voiceprint_cluster_mark_existing(
        self,
        cluster_id: str,
        vault_id: str,
        target_speaker_id: str,
    ) -> dict:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect(immediate=True) as c:
            cluster = c.execute(
                "SELECT * FROM voiceprint_clusters WHERE cluster_id = ? AND vault_id = ?",
                (cluster_id, vault_id),
            ).fetchone()
            if not cluster:
                raise ValueError("Cluster not found")

            spk = c.execute(
                "SELECT * FROM speaker_profiles WHERE speaker_id = ? AND vault_id = ?",
                (target_speaker_id, vault_id),
            ).fetchone()
            if not spk:
                raise KeyError("Target speaker not found")

            occurrences = cluster["occurrences_count"] if "occurrences_count" in cluster.keys() else 1
            was_already_merged = (cluster["status"] == "merged" and cluster["speaker_id"] == target_speaker_id)

            # 1. Update cluster
            c.execute(
                """UPDATE voiceprint_clusters
                   SET status = 'merged', speaker_id = ?, updated_at = ?
                   WHERE cluster_id = ?""",
                (target_speaker_id, now_iso, cluster_id),
            )

            # 2. Update speaker sample count (idempotent: only if not already merged to this target speaker)
            if not was_already_merged:
                c.execute(
                    """UPDATE speaker_profiles
                       SET sample_count = sample_count + ?, updated_at = ?
                       WHERE speaker_id = ?""",
                    (occurrences, now_iso, target_speaker_id),
                )

            # 3. Retroactively update section_messages
            c.execute(
                """UPDATE section_messages
                   SET speaker_id = ?, speaker_name = ?
                   WHERE cluster_id = ?""",
                (target_speaker_id, spk["name"], cluster_id),
            )
            emit(c, "cluster.merged", cluster_id)

        return {
            "cluster_id": cluster_id,
            "status": "merged",
            "speaker_id": target_speaker_id,
        }

    def patch_voiceprint_cluster_dismiss(
        self,
        cluster_id: str,
        vault_id: str,
    ) -> dict:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect(immediate=True) as c:
            cluster = c.execute(
                "SELECT * FROM voiceprint_clusters WHERE cluster_id = ? AND vault_id = ?",
                (cluster_id, vault_id),
            ).fetchone()
            if not cluster:
                raise ValueError("Cluster not found")
            c.execute(
                """UPDATE voiceprint_clusters
                   SET status = 'dismissed', updated_at = ?
                   WHERE cluster_id = ?""",
                (now_iso, cluster_id),
            )
            emit(c, "cluster.dismissed", cluster_id)

        return {
            "cluster_id": cluster_id,
            "status": "dismissed",
        }

    def get_cluster_samples(self, cluster_id: str, vault_id: str) -> list[dict]:
        cluster = self.get_voiceprint_cluster(cluster_id, vault_id)
        if not cluster:
            raise ValueError("Cluster not found")

        messages = self.all(
            """SELECT sm.message_id, sm.section_id, sm.start_ms, sm.end_ms, sm.confidence,
                      s.live_session_id AS session_id
               FROM section_messages sm
               LEFT JOIN sections s ON sm.section_id = s.section_id
               WHERE sm.cluster_id = ?
               ORDER BY sm.start_ms ASC""",
            (cluster_id,),
        )
        samples = []
        if messages:
            for m in messages:
                samples.append({
                    "sample_id": f"smp_{m['message_id']}",
                    "segment_id": m.get("section_id"),
                    "session_id": m.get("session_id"),
                    "audio_url": f"/clusters/{cluster_id}/audio_preview",
                    "start_ms": int(m["start_ms"]),
                    "end_ms": int(m["end_ms"]),
                    "duration_ms": int(m["end_ms"] - m["start_ms"]),
                    "confidence": float(m["confidence"]),
                })
        else:
            # Fallback sample representing cluster
            samples.append({
                "sample_id": f"smp_{cluster_id}",
                "segment_id": None,
                "session_id": None,
                "audio_url": f"/clusters/{cluster_id}/audio_preview",
                "start_ms": 0,
                "end_ms": 5000,
                "duration_ms": 5000,
                "confidence": float(cluster.get("confidence_score", 1.0)),
            })
        return samples

    def retroactive_update_section_messages_speaker(
        self,
        cluster_id: str,
        speaker_id: str,
        speaker_name: str,
    ) -> int:
        with self.connect(immediate=True) as c:
            cur = c.execute(
                """UPDATE section_messages
                   SET speaker_id = ?, speaker_name = ?
                   WHERE cluster_id = ?""",
                (speaker_id, speaker_name, cluster_id),
            )
            return cur.rowcount

    # -------------------------------------------------------------------------
    # Milestone M4: Chat Threads & Turns
    # -------------------------------------------------------------------------
    def get_chat_thread(self, thread_id: str, vault_id: str | None = None) -> dict | None:
        if vault_id:
            row = self.one("SELECT * FROM chat_threads WHERE thread_id = ? AND vault_id = ?", (thread_id, vault_id))
        else:
            row = self.one("SELECT * FROM chat_threads WHERE thread_id = ?", (thread_id,))
        if not row:
            return None
        res = dict(row)
        try:
            res["metadata"] = json.loads(res.get("metadata") or "{}")
        except Exception:
            res["metadata"] = {}
        return res

    def create_chat_thread(
        self,
        thread_id: str,
        tenant_id: str,
        vault_id: str,
        user_id: str,
        title: str | None = None,
        metadata: dict | str | None = None,
    ) -> dict:
        meta_str = json.dumps(metadata) if isinstance(metadata, dict) else (metadata or "{}")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.connect(immediate=True) as c:
            c.execute(
                """INSERT INTO chat_threads (thread_id, tenant_id, vault_id, user_id, title, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (thread_id, tenant_id, vault_id, user_id, title, meta_str, now_iso, now_iso),
            )
        return self.get_chat_thread(thread_id, vault_id)

    def list_chat_threads(self, vault_id: str, limit: int = 50) -> list[dict]:
        rows = self.all(
            "SELECT * FROM chat_threads WHERE vault_id = ? ORDER BY updated_at DESC LIMIT ?",
            (vault_id, limit),
        )
        res = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d.get("metadata") or "{}")
            except Exception:
                d["metadata"] = {}
            res.append(d)
        return res

    def get_next_turn_index(self, thread_id: str) -> int:
        row = self.one("SELECT COALESCE(MAX(turn_index), 0) + 1 AS next_idx FROM chat_turns WHERE thread_id = ?", (thread_id,))
        return int(row["next_idx"]) if row else 1

    def create_chat_turn(
        self,
        turn_id: str,
        thread_id: str,
        tenant_id: str,
        vault_id: str,
        prompt: str,
        turn_index: int | None = None,
        client_turn_id: str | None = None,
        client_context: dict | str | None = None,
        status: str = "pending",
    ) -> dict:
        ctx_str = json.dumps(client_context) if isinstance(client_context, dict) else (client_context or "{}")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with self.connect(immediate=True) as c:
            # Check if turn_id was already inserted by a racing request
            cursor = c.execute("SELECT * FROM chat_turns WHERE turn_id = ?", (turn_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                return self.get_chat_turn(turn_id, vault_id) or d

            if turn_index is None:
                cursor = c.execute(
                    "SELECT COALESCE(MAX(turn_index), 0) + 1 AS next_idx FROM chat_turns WHERE thread_id = ?",
                    (thread_id,),
                )
                r_idx = cursor.fetchone()
                turn_index = int(r_idx["next_idx"]) if r_idx else 1

            try:
                c.execute(
                    """INSERT INTO chat_turns (
                           turn_id, thread_id, tenant_id, vault_id, turn_index,
                           prompt, response_text, status, tokens_used,
                           client_turn_id, client_context, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, '', ?, 0, ?, ?, ?)""",
                    (turn_id, thread_id, tenant_id, vault_id, turn_index, prompt, status, client_turn_id, ctx_str, now_iso),
                )
            except sqlite3.IntegrityError:
                # Race condition recovery: check if turn_id exists
                cursor = c.execute("SELECT * FROM chat_turns WHERE turn_id = ?", (turn_id,))
                row = cursor.fetchone()
                if row:
                    d = dict(row)
                    return self.get_chat_turn(turn_id, vault_id) or d

                # Conflict on (thread_id, turn_index) with distinct turn_id: recompute turn_index
                cursor = c.execute(
                    "SELECT COALESCE(MAX(turn_index), 0) + 1 AS next_idx FROM chat_turns WHERE thread_id = ?",
                    (thread_id,),
                )
                r_idx = cursor.fetchone()
                new_turn_index = int(r_idx["next_idx"]) if r_idx else 1
                c.execute(
                    """INSERT INTO chat_turns (
                           turn_id, thread_id, tenant_id, vault_id, turn_index,
                           prompt, response_text, status, tokens_used,
                           client_turn_id, client_context, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, '', ?, 0, ?, ?, ?)""",
                    (turn_id, thread_id, tenant_id, vault_id, new_turn_index, prompt, status, client_turn_id, ctx_str, now_iso),
                )

        return self.get_chat_turn(turn_id, vault_id)

    def get_chat_turn(self, turn_id: str, vault_id: str | None = None) -> dict | None:
        if vault_id:
            row = self.one("SELECT * FROM chat_turns WHERE turn_id = ? AND vault_id = ?", (turn_id, vault_id))
        else:
            row = self.one("SELECT * FROM chat_turns WHERE turn_id = ?", (turn_id,))
        if not row:
            return None
        res = dict(row)
        try:
            res["client_context"] = json.loads(res.get("client_context") or "{}")
        except Exception:
            res["client_context"] = {}
        return res

    def update_chat_turn(
        self,
        turn_id: str,
        status: str | None = None,
        response_text: str | None = None,
        tokens_used: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        completed_at: str | None = None,
        vault_id: str | None = None,
    ) -> None:
        updates = []
        params = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if response_text is not None:
            updates.append("response_text = ?")
            params.append(response_text)
        if tokens_used is not None:
            updates.append("tokens_used = ?")
            params.append(tokens_used)
        if error_code is not None:
            updates.append("error_code = ?")
            params.append(error_code)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)

        if not updates:
            return

        sql = f"UPDATE chat_turns SET {', '.join(updates)} WHERE turn_id = ?"
        params.append(turn_id)
        if vault_id is not None:
            sql += " AND vault_id = ?"
            params.append(vault_id)
        with self.connect(immediate=True) as c:
            c.execute(sql, tuple(params))

    def update_chat_turn_status(self, turn_id: str, status: str, vault_id: str | None = None) -> None:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.connect(immediate=True) as c:
            if vault_id:
                c.execute(
                    "UPDATE chat_turns SET status = ?, completed_at = ? WHERE turn_id = ? AND vault_id = ?",
                    (status, now_iso, turn_id, vault_id),
                )
            else:
                c.execute(
                    "UPDATE chat_turns SET status = ?, completed_at = ? WHERE turn_id = ?",
                    (status, now_iso, turn_id),
                )

    # -------------------------------------------------------------------------
    # Milestone M4: Processing Chains (Feature F4.4, P1 Timeline Clipping)
    # -------------------------------------------------------------------------
    def create_processing_chain(
        self,
        chain_id: str,
        tenant_id: str,
        vault_id: str,
        recording_id: str,
        live_session_id: str | None = None,
        current_step: str = "slicing",
        progress_pct: float = 0.0,
        status: str = "pending",
    ) -> dict:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.connect(immediate=True) as c:
            c.execute(
                """INSERT INTO processing_chains (
                       chain_id, tenant_id, vault_id, recording_id, live_session_id,
                       status, current_step, progress_pct, analyzing_start_at, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chain_id, tenant_id, vault_id, recording_id, live_session_id, status, current_step, progress_pct, now_iso, now_iso, now_iso),
            )
        return self.get_processing_chain(chain_id, vault_id)

    def get_processing_chain(self, chain_id: str, vault_id: str | None = None) -> dict | None:
        if vault_id:
            row = self.one("SELECT * FROM processing_chains WHERE chain_id = ? AND vault_id = ?", (chain_id, vault_id))
        else:
            row = self.one("SELECT * FROM processing_chains WHERE chain_id = ?", (chain_id,))
        if not row:
            return None
        res = dict(row)
        res["started_at"] = res.get("analyzing_start_at")
        return res

    def update_processing_chain(
        self,
        chain_id: str,
        status: str | None = None,
        current_step: str | None = None,
        progress_pct: float | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        analyzing_end_at: str | None = None,
    ) -> None:
        updates = ["updated_at = ?"]
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        params = [now_iso]
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if current_step is not None:
            updates.append("current_step = ?")
            params.append(current_step)
        if progress_pct is not None:
            updates.append("progress_pct = ?")
            params.append(progress_pct)
        if error_code is not None:
            updates.append("error_code = ?")
            params.append(error_code)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if analyzing_end_at is not None:
            updates.append("analyzing_end_at = ?")
            params.append(analyzing_end_at)

        sql = f"UPDATE processing_chains SET {', '.join(updates)} WHERE chain_id = ?"
        params.append(chain_id)
        with self.connect(immediate=True) as c:
            c.execute(sql, tuple(params))

    def list_processing_chains(
        self,
        vault_id: str,
        status: str | None = None,
        since: str | None = None,
        session_id: str | None = None,
        recording_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions = ["vault_id = ?"]
        params: list[Any] = [vault_id]

        if status:
            if status == "active":
                conditions.append("status IN ('pending', 'running')")
            elif status != "all":
                conditions.append("status = ?")
                params.append(status)
        else:
            # Default active
            conditions.append("status IN ('pending', 'running')")

        if since:
            conditions.append("updated_at >= ?")
            params.append(since)

        if session_id:
            conditions.append("live_session_id = ?")
            params.append(session_id)

        if recording_id:
            conditions.append("recording_id = ?")
            params.append(recording_id)

        sql = f"""SELECT * FROM processing_chains
                  WHERE {' AND '.join(conditions)}
                  ORDER BY updated_at DESC
                  LIMIT ?"""
        params.append(limit)
        rows = self.all(sql, tuple(params))
        res = []
        for r in rows:
            d = dict(r)
            d["started_at"] = d.get("analyzing_start_at")
            res.append(d)
        return res

    # -------------------------------------------------------------------------
    # Milestone M4: Recording Mutations (Feature F4.4, Causal Cursor Ordering)
    # -------------------------------------------------------------------------
    def append_recording_mutation(
        self,
        tenant_id: str,
        vault_id: str,
        entity_type: str,
        entity_id: str,
        mutation_type: str,
        payload_json: dict | str,
    ) -> dict:
        pay_str = json.dumps(payload_json) if isinstance(payload_json, dict) else (payload_json or "{}")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.connect(immediate=True) as c:
            cur = c.execute(
                """INSERT INTO recording_mutations (
                       tenant_id, vault_id, entity_type, entity_id, mutation_type, payload_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tenant_id, vault_id, entity_type, entity_id, mutation_type, pay_str, now_iso),
            )
            cursor_id = cur.lastrowid
        row = self.one("SELECT * FROM recording_mutations WHERE cursor = ?", (cursor_id,))
        if not row:
            return {"cursor": cursor_id, "id": cursor_id, "payload": payload_json if isinstance(payload_json, dict) else {}}
        d = dict(row)
        d["id"] = d["cursor"]
        try:
            d["payload"] = json.loads(d["payload_json"])
        except Exception:
            d["payload"] = {}
        return d

    def list_recording_mutations(
        self,
        vault_id: str,
        cursor: int = 0,
        limit: int = 50,
        mutation_type: str | None = None,
    ) -> tuple[list[dict], int, bool]:
        limit = max(1, min(limit, 1000))
        query = "SELECT * FROM recording_mutations WHERE vault_id = ? AND cursor > ?"
        params: list[Any] = [vault_id, cursor]
        if mutation_type:
            query += " AND mutation_type = ?"
            params.append(mutation_type)
        query += " ORDER BY cursor ASC LIMIT ?"
        params.append(limit + 1)
        rows = self.all(query, tuple(params))
        has_more = len(rows) > limit
        selected = rows[:limit]
        res = []
        for r in selected:
            d = dict(r)
            d["id"] = d["cursor"]
            try:
                d["payload"] = json.loads(d["payload_json"])
            except Exception:
                d["payload"] = {}
            res.append(d)
        next_cursor = selected[-1]["cursor"] if selected else cursor
        return res, next_cursor, has_more

    # -------------------------------------------------------------------------
    # Milestone M4: Episodic Memory Events & Long Memory Retrieval
    # -------------------------------------------------------------------------
    def create_memory_event(
        self,
        event_id: str,
        tenant_id: str,
        vault_id: str,
        title: str,
        summary: str,
        start_time: str,
        end_time: str,
        session_id: str | None = None,
        category: str = "general",
        speaker_names: list[str] | None = None,
        keywords: list[str] | None = None,
        confidence: float = 1.0,
    ) -> dict:
        spk_str = json.dumps(speaker_names or [])
        kw_str = json.dumps(keywords or [])
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.connect(immediate=True) as c:
            c.execute(
                """INSERT INTO memory_events (
                       event_id, tenant_id, vault_id, session_id, title, summary,
                       category, start_time, end_time, speaker_names, keywords, confidence,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, tenant_id, vault_id, session_id, title, summary, category,
                 start_time, end_time, spk_str, kw_str, confidence, now_iso, now_iso),
            )
        row = self.one("SELECT * FROM memory_events WHERE event_id = ?", (event_id,))
        return dict(row) if row else {}

    def search_sections(
        self,
        vault_id: str,
        query: str,
        session_id: str | None = None,
        speaker_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        from .speakers import SessionSpeakerResolver

        clean_query = "".join(ch for ch in query if ch.isprintable() and ch != "\x00").strip()
        if not clean_query:
            return []

        tokens = sanitize_fts_tokens(clean_query)
        fts_term = " ".join(f'"{t}"' for t in tokens) if tokens else None
        like_pat = f"%{clean_query}%"

        # Contract C6 Speaker Fallback Resolution
        profiles_rows = self.all("SELECT speaker_id, name, suspected_name FROM speaker_profiles WHERE vault_id = ?", (vault_id,))
        profiles_map = {r["speaker_id"]: dict(r) for r in profiles_rows}
        resolver = SessionSpeakerResolver(speaker_profiles=profiles_map)

        sql_fts = """
        SELECT sm.message_id, sm.section_id, sm.speaker_id, sm.cluster_id, sm.speaker_name,
               sm.content, sm.start_ms, sm.end_ms, sm.confidence,
               s.live_session_id AS session_id, s.title AS section_title,
               rs.started_at AS session_started_at
        FROM section_messages sm
        INNER JOIN sections s ON sm.section_id = s.section_id
        LEFT JOIN recording_sessions rs ON s.live_session_id = rs.live_session_id
        WHERE s.vault_id = ?
          AND (? IS NULL OR s.live_session_id = ?)
          AND (? IS NULL OR sm.speaker_id = ? OR sm.cluster_id = ?)
          AND (
              (sm.message_id IN (SELECT message_id FROM section_messages_fts WHERE section_messages_fts MATCH ?))
              OR (sm.content LIKE ?)
          )
        ORDER BY rs.started_at DESC, sm.start_ms ASC
        LIMIT ?
        """

        sql_like_only = """
        SELECT sm.message_id, sm.section_id, sm.speaker_id, sm.cluster_id, sm.speaker_name,
               sm.content, sm.start_ms, sm.end_ms, sm.confidence,
               s.live_session_id AS session_id, s.title AS section_title,
               rs.started_at AS session_started_at
        FROM section_messages sm
        INNER JOIN sections s ON sm.section_id = s.section_id
        LEFT JOIN recording_sessions rs ON s.live_session_id = rs.live_session_id
        WHERE s.vault_id = ?
          AND (? IS NULL OR s.live_session_id = ?)
          AND (? IS NULL OR sm.speaker_id = ? OR sm.cluster_id = ?)
          AND sm.content LIKE ?
        ORDER BY rs.started_at DESC, sm.start_ms ASC
        LIMIT ?
        """

        rows = []
        if fts_term:
            try:
                rows = self.all(
                    sql_fts,
                    (vault_id, session_id, session_id, speaker_id, speaker_id, speaker_id, fts_term, like_pat, limit),
                )
            except sqlite3.OperationalError:
                rows = []

        if not rows and like_pat:
            try:
                rows = self.all(
                    sql_like_only,
                    (vault_id, session_id, session_id, speaker_id, speaker_id, speaker_id, like_pat, limit),
                )
            except sqlite3.OperationalError:
                rows = []

        results = []
        for r in rows:
            spk_name = resolver.resolve(
                speaker_id=r.get("speaker_id"),
                cluster_id=r.get("cluster_id"),
                local_label=r.get("speaker_name"),
            )
            raw_spk = r.get("speaker_name")
            if spk_name and not spk_name.startswith("Speaker"):
                resolved_name = spk_name
            elif raw_spk and raw_spk not in ("Unknown", "Speaker") and not raw_spk.startswith("Speaker"):
                resolved_name = raw_spk
            else:
                resolved_name = spk_name or raw_spk or "Speaker 1"

            results.append({
                "message_id": r["message_id"],
                "section_id": r.get("section_id"),
                "session_id": r.get("session_id"),
                "speaker_name": resolved_name,
                "speaker_id": r.get("speaker_id"),
                "content": r["content"],
                "start_ms": int(r.get("start_ms") or 0),
                "end_ms": int(r.get("end_ms") or 0),
                "timestamp_iso": r.get("session_started_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "confidence": float(r.get("confidence") or 1.0),
                "audio_preview_url": f"/clusters/{r['cluster_id']}/audio_preview" if r.get("cluster_id") else None,
            })
        return results

    def search_memory(
        self,
        vault_id: str,
        query: str,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        clean_query = "".join(ch for ch in query if ch.isprintable() and ch != "\x00").strip()
        if not clean_query:
            return []

        tokens = sanitize_fts_tokens(clean_query)
        fts_term = " ".join(f'"{t}"' for t in tokens) if tokens else None
        like_pat = f"%{clean_query}%"

        # 1. Search memory_events table (FTS5 + LIKE)
        me_rows = []
        if fts_term:
            try:
                me_rows = self.all(
                    """SELECT event_id AS memory_id, 'memory_event' AS kind, title, summary AS snippet,
                              start_time AS occurred_at, speaker_names, confidence
                       FROM memory_events
                       WHERE vault_id = ?
                         AND (
                             (event_id IN (SELECT event_id FROM memory_events_fts WHERE memory_events_fts MATCH ?))
                             OR (title LIKE ? OR summary LIKE ? OR keywords LIKE ?)
                         )
                         AND (? IS NULL OR start_time >= ?)
                         AND (? IS NULL OR end_time <= ?)
                       ORDER BY start_time DESC
                       LIMIT ?""",
                    (vault_id, fts_term, like_pat, like_pat, like_pat, date_from, date_from, date_to, date_to, limit),
                )
            except sqlite3.OperationalError:
                me_rows = []

        if not me_rows:
            try:
                me_rows = self.all(
                    """SELECT event_id AS memory_id, 'memory_event' AS kind, title, summary AS snippet,
                              start_time AS occurred_at, speaker_names, confidence
                       FROM memory_events
                       WHERE vault_id = ?
                         AND (title LIKE ? OR summary LIKE ? OR keywords LIKE ?)
                         AND (? IS NULL OR start_time >= ?)
                         AND (? IS NULL OR end_time <= ?)
                       ORDER BY start_time DESC
                       LIMIT ?""",
                    (vault_id, like_pat, like_pat, like_pat, date_from, date_from, date_to, date_to, limit),
                )
            except sqlite3.OperationalError:
                me_rows = []

        # 2. Search summaries table
        sum_rows = []
        try:
            sum_rows = self.all(
                """SELECT summary_id AS memory_id, 'summary' AS kind,
                          target_type || ' ' || target_id AS title,
                          summary_markdown AS snippet,
                          created_at AS occurred_at, '[]' AS speaker_names, 1.0 AS confidence
                   FROM summaries
                   WHERE vault_id = ?
                     AND (summary_markdown LIKE ? OR topics LIKE ?)
                     AND (? IS NULL OR created_at >= ?)
                     AND (? IS NULL OR created_at <= ?)
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (vault_id, like_pat, like_pat, date_from, date_from, date_to, date_to, limit),
            )
        except sqlite3.OperationalError:
            sum_rows = []

        combined = [dict(r) for r in me_rows] + [dict(r) for r in sum_rows]

        # 3. If still empty, search section_messages for transcript occurrences
        if not combined:
            sections = self.search_sections(vault_id, query=query, limit=limit)
            for s in sections:
                combined.append({
                    "memory_id": f"mem_{s['message_id']}",
                    "kind": "transcript_snippet",
                    "title": f"Dialogue with {s['speaker_name']}",
                    "snippet": s["content"],
                    "occurred_at": s["timestamp_iso"],
                    "speaker_name": s["speaker_name"],
                    "confidence": s["confidence"],
                })

        return combined[:limit]

    def list_recent_vault_sessions(self, vault_id: str, limit: int = 5) -> list[dict]:
        rows = self.all(
            """SELECT live_session_id, started_at, duration_ms, status
               FROM recording_sessions
               WHERE vault_id = ?
               ORDER BY started_at DESC
               LIMIT ?""",
            (vault_id, limit),
        )
        return [dict(r) for r in rows]


EchoDatabase = Database


def get_db(root: Path | str | None = None) -> Database:
    if root is None:
        from .config import Settings
        root = Settings().data_dir
    return Database(Path(root))



