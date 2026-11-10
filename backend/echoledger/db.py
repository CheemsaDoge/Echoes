from __future__ import annotations
import contextlib, datetime, json, sqlite3, time, uuid
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
            migration_file = Path(__file__).parent / "migrations" / "001_initial_schema.sql"
            if migration_file.exists():
                c.executescript(migration_file.read_text(encoding="utf-8"))
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


