from __future__ import annotations
import datetime as dt
import hashlib, json, os, shutil, time, uuid
from pathlib import Path
import numpy as np
from .audio import SAMPLE_RATE, read_wav, pack_audio, restore_interval, atomic_write, wav_bytes
from .config import Settings
from .db import Database, emit, now_ms, uid
from .models import VadConfig, AsrSegment
from .providers import (Transcriber, Transcript, DisabledTranscriber, MockTranscriber,
                        GeminiTranscriber, VertexAiTranscriber, ProviderBlocked, ProviderError)
from .vad import make_scorer, speech_intervals


def plan_batches(db: Database, settings: Settings, force_session: str | None = None) -> list[str]:
    queued = []
    with db.connect(immediate=True) as c:
        sessions = c.execute("SELECT * FROM sessions WHERE purpose='journal' ORDER BY created_ms").fetchall()
        for session in sessions:
            cursor = session["next_batch_seq"]
            for _ in range(50):
                rows = c.execute("SELECT * FROM chunks WHERE session_id=? AND seq>=? AND batch_id IS NULL ORDER BY seq LIMIT ?",
                                 (session["id"], cursor, settings.batch_chunks)).fetchall()
                contiguous = []
                for row in rows:
                    if row["seq"] != cursor + len(contiguous):
                        break
                    contiguous.append(row)
                if not contiguous:
                    break
                ready = (len(contiguous) >= settings.batch_chunks or
                         session["id"] == force_session or session["state"] == "stopped" or
                         now_ms() - contiguous[0]["created_ms"] >= settings.batch_wait_seconds * 1000)
                if not ready:
                    break
                profile_id = c.execute("SELECT id FROM profiles WHERE active=1").fetchone()[0]
                batch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"echoledger:{session['id']}:{cursor}:{contiguous[-1]['seq']}:{profile_id}"))
                ids = [row["id"] for row in contiguous]
                c.execute("""INSERT INTO batches(id,session_id,first_seq,last_seq,chunk_ids,profile_id,available_ms,created_ms)
                             VALUES(?,?,?,?,?,?,?,?)""",
                          (batch_id, session["id"], cursor, contiguous[-1]["seq"], json.dumps(ids), profile_id, now_ms(), now_ms()))
                for chunk_id in ids:
                    c.execute("UPDATE chunks SET batch_id=? WHERE id=?", (batch_id, chunk_id))
                cursor = contiguous[-1]["seq"] + 1
                c.execute("UPDATE sessions SET next_batch_seq=? WHERE id=?", (cursor, session["id"]))
                emit(c, "batch.queued", batch_id)
                queued.append(batch_id)
    return queued


def claim_batch(db: Database) -> dict | None:
    with db.connect(immediate=True) as c:
        row = c.execute("""SELECT * FROM batches WHERE
             (state IN ('queued','retry') AND available_ms<=?) OR
             (state='running' AND lease_until_ms<?) ORDER BY created_ms LIMIT 1""", (now_ms(), now_ms())).fetchone()
        if row is None:
            return None
        batch = dict(row)
        if batch["attempts"] >= 5:
            c.execute("UPDATE batches SET state='failed',error='MAX_ATTEMPTS' WHERE id=?", (batch["id"],))
            emit(c, "batch.failed", batch["id"])
            return None
        token = uid()
        c.execute("""UPDATE batches SET state='running',attempts=attempts+1,lease_token=?,lease_until_ms=? WHERE id=?""",
                  (token, now_ms() + 600_000, batch["id"]))
        batch.update(lease_token=token, attempts=batch["attempts"] + 1)
        emit(c, "batch.running", batch["id"])
        return batch


def join_text(parts: list[str]) -> str:
    text = ""
    for part in parts:
        if text and part and text[-1].isascii() and text[-1].isalnum() and part[0].isascii() and part[0].isalnum():
            text += " "
        text += part
    return text


class Pipeline:
    def __init__(self, db: Database, settings: Settings, scorer=None, transcriber: Transcriber | None = None):
        from .hemory_pipeline import HemorySegmentProcessor

        self.db, self.settings = db, settings
        self._scorer = scorer
        self._explicit_transcriber = transcriber is not None
        self.transcriber = transcriber or self.build_transcriber()
        self.hemory_processor = HemorySegmentProcessor(self)

    def build_transcriber(self) -> Transcriber:
        return {
            "disabled": lambda: DisabledTranscriber(), "mock": lambda: MockTranscriber(),
            "gemini": lambda: GeminiTranscriber(self.settings.gemini_key, self.settings.gemini_model, self.settings.gemini_mode),
            "vertexai": lambda: VertexAiTranscriber(
                project=self.settings.vertex_project,
                location=self.settings.vertex_location,
                model=self.settings.vertex_model,
                credentials_path=self.settings.vertex_credentials,
                api_key=self.settings.vertex_api_key,
                endpoint=self.settings.vertex_endpoint,
            ),
        }[self.settings.asr_provider]()

    def reload_transcriber(self) -> None:
        if not self._explicit_transcriber:
            self.transcriber = self.build_transcriber()

    def reload_scorer(self) -> None:
        self._scorer = make_scorer(self.settings.vad_provider, self.settings.vad_model)

    @property
    def scorer(self):
        if self._scorer is None:
            self._scorer = make_scorer(self.settings.vad_provider, self.settings.vad_model)
        return self._scorer

    def tick(self) -> bool:
        plan_batches(self.db, self.settings)
        batch = claim_batch(self.db)
        if batch is None:
            return self.hemory_processor.tick()
        try:
            self.process(batch)
        except ProviderBlocked as exc:
            self.fail(batch, "blocked", str(exc))
        except ProviderError as exc:
            retry = exc.retryable and batch["attempts"] < 5
            self.fail(batch, "retry" if retry else "failed", str(exc))
        except Exception as exc:
            # Do not persist exception messages: providers may include credentials or transcript content.
            self.fail(batch, "failed", "PROCESSING_" + type(exc).__name__)
        finally:
            if not self.db.one("SELECT id FROM sessions WHERE id=?", (batch["session_id"],)):
                shutil.rmtree(self.settings.data_dir / "derived" / batch["session_id"], ignore_errors=True)
        return True

    def fail(self, batch: dict, state: str, error: str):
        with self.db.connect(immediate=True) as c:
            n = c.execute("""UPDATE batches SET state=?,error=?,available_ms=?,lease_until_ms=NULL
                WHERE id=? AND lease_token=?""",
                (state, error[:200], now_ms() + min(900, 5 * 2 ** batch["attempts"]) * 1000,
                 batch["id"], batch["lease_token"])).rowcount
            if n:
                emit(c, "batch." + state, batch["id"])

    def reserve_budget(self, batch: dict, audio_seconds: float):
        if self.settings.asr_provider == "disabled":
            raise ProviderBlocked("ASR_NOT_CONFIGURED")
        if self.settings.asr_provider == "gemini" and not self.settings.gemini_key:
            raise ProviderBlocked("GEMINI_KEY_MISSING")
        if self.settings.asr_provider == "vertexai":
            if not self.settings.vertex_api_key and not self.settings.vertex_endpoint:
                if hasattr(self.transcriber, "discover_project"):
                    proj = getattr(self.transcriber, "project", "") or self.transcriber.discover_project()
                    if not proj:
                        raise ProviderBlocked("VERTEX_PROJECT_MISSING")
                elif not self.settings.vertex_project and not self.settings.vertex_credentials and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                    raise ProviderBlocked("VERTEX_PROJECT_MISSING")
        day = dt.datetime.now(dt.timezone.utc).date().isoformat()
        with self.db.connect(immediate=True) as c:
            if not c.execute("SELECT 1 FROM batches WHERE id=? AND lease_token=?", (batch["id"], batch["lease_token"])).fetchone():
                raise ProviderBlocked("BATCH_CANCELLED")
            # Log usage for statistics; daily audio budget limit is removed (unlimited)
            c.execute("INSERT INTO usage VALUES(?,?,?,?,?,?)",
                      (uid(), batch["id"], day, audio_seconds, self.settings.asr_provider, now_ms()))

    def process(self, batch: dict):
        begun = time.monotonic()
        session = self.db.one("SELECT * FROM sessions WHERE id=?", (batch["session_id"],))
        if session is None:
            return
        chunks = self.db.all("SELECT * FROM chunks WHERE batch_id=? ORDER BY seq", (batch["id"],))
        profile = self.db.one("SELECT * FROM profiles WHERE id=?", (batch["profile_id"],))
        config = VadConfig.model_validate_json(profile["config"])
        owned_start = chunks[0]["start_sample"]
        source_chunks = []
        # One second of previous audio supplies left context at processing boundaries.
        # Ownership by the original-sample midpoint prevents overlap-only words from duplicating.
        if batch["first_seq"] > 0:
            prev = self.db.one("SELECT * FROM chunks WHERE session_id=? AND seq=?", (session["id"], batch["first_seq"] - 1))
            if prev and prev["start_sample"] + prev["sample_count"] == owned_start:
                source_chunks.append((prev, max(0, prev["sample_count"] - SAMPLE_RATE)))
        source_chunks.extend((chunk, 0) for chunk in chunks)
        arrays, sources, offset = [], [], 0
        for chunk, skip in source_chunks:
            content = Path(chunk["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != chunk["sha256"]:
                raise ValueError("Stored chunk checksum mismatch")
            audio = read_wav(content)[skip:]
            arrays.append(audio)
            sources.append({"input_start": offset, "input_end": offset + len(audio),
                            "original_start": chunk["start_sample"] + skip,
                            "chunk_id": chunk["id"], "chunk_start": chunk["start_sample"]})
            offset += len(audio)
        samples = np.concatenate(arrays)
        probs = self.scorer.probabilities(samples)
        intervals = speech_intervals(probs, len(samples), config)
        packed, mapping = pack_audio(samples, intervals, sources)
        asr_meta = {"asr": self.settings.asr_provider}
        if self.settings.asr_provider == "vertexai":
            asr_meta["vertex_model"] = self.settings.vertex_model
            asr_meta["vertex_location"] = self.settings.vertex_location
        else:
            asr_meta["model"] = self.settings.gemini_model
            asr_meta["mode"] = self.settings.gemini_mode

        fingerprint = hashlib.sha256(json.dumps({
            "pipeline_version": 1, "chunks": [(r["sha256"], skip) for r, skip in source_chunks],
            "profile": profile["config"], "vad": self.scorer.model_id,
            **asr_meta,
        }, sort_keys=True).encode()).hexdigest()[:20]
        derived = self.settings.data_dir / "derived" / session["id"] / batch["id"] / fingerprint
        metrics = {"raw_samples": sum(c["sample_count"] for c in chunks),
                   "context_samples": len(samples) - sum(c["sample_count"] for c in chunks),
                   "retained_samples_with_context": sum(b - a for a, b in intervals),
                   "packed_samples": len(packed), "vad_model": self.scorer.model_id,
                   "profile_id": profile["id"], "fingerprint": fingerprint,
                   "mapping": [s.to_dict() for s in mapping], "intervals": intervals}
        atomic_write(derived / "vad.json", json.dumps({"metrics": metrics, "frame_samples": 512,
                     "probabilities": probs.tolist(), "profile": config.model_dump()}).encode())
        with self.db.connect(immediate=True) as c:
            c.execute("UPDATE batches SET metrics=? WHERE id=? AND lease_token=?",
                      (json.dumps(metrics), batch["id"], batch["lease_token"]))
        if len(packed) == 0:
            result = Transcript([], "vad-no-speech")
        else:
            cache = derived / "asr.json"
            if cache.exists():
                d = json.loads(cache.read_text())
                result = Transcript([AsrSegment.model_validate(s) for s in d["segments"]], d["provider"], d["is_demo"], d.get("usage", {}))
            else:
                self.reserve_budget(batch, len(packed) / SAMPLE_RATE)
                result = self.transcriber.transcribe(packed)
                atomic_write(cache, json.dumps({"segments": [s.model_dump() for s in result.segments],
                    "provider": result.provider, "is_demo": result.is_demo, "usage": result.usage}).encode())
            atomic_write(derived / "packed.wav", wav_bytes(packed))
        restored = []
        for segment in result.segments:
            spans = restore_interval(segment.start_ms, segment.end_ms, mapping, session["started_at_ms"])
            if not spans or not segment.text.strip():
                continue
            midpoint = (spans[0]["session_start_sample"] + spans[-1]["session_end_sample"]) / 2
            if midpoint < owned_start:
                continue
            restored.append((segment, spans))
        metrics.update(processing_ms=round((time.monotonic() - begun) * 1000), provider=result.provider,
                       asr_usage=result.usage, is_demo=result.is_demo, segment_count=len(restored))
        self.commit(batch, session, restored, metrics, result.is_demo)

    def commit(self, batch, session, restored, metrics, is_demo):
        with self.db.connect(immediate=True) as c:
            lease = c.execute("SELECT lease_token,state FROM batches WHERE id=?", (batch["id"],)).fetchone()
            if lease is None or lease[0] != batch["lease_token"] or lease[1] != "running":
                return
            segment_ids = []
            for i, (segment, spans) in enumerate(restored):
                segment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, batch["id"] + f":{i}"))
                c.execute("""INSERT INTO segments(id,session_id,batch_id,ordinal,start_ms,end_ms,text,
                    local_speaker,source_spans,timing_quality,is_demo) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (segment_id, session["id"], batch["id"], i, spans[0]["start_ms"], spans[-1]["end_ms"],
                     segment.text, batch["id"] + ":" + segment.speaker_label,
                     json.dumps(spans), segment.timing_quality, int(is_demo)))
                segment_ids.append(segment_id)
            if restored:
                text = join_text([s.text for s, _ in restored])
                scores = []
                for t in c.execute("SELECT * FROM event_types ORDER BY id"):
                    score = sum(text.casefold().count(k.casefold()) for k in json.loads(t["keywords"]) if k)
                    scores.append((score, t["id"]))
                best = max(scores, default=(0, "unknown"))
                type_id = best[1] if best[0] else "unknown"
                event_id = batch["id"]
                c.execute("""INSERT INTO events(id,session_id,start_ms,end_ms,title,summary,type_id,is_demo,updated_ms)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (event_id, session["id"],
                    min(spans[0]["start_ms"] for _, spans in restored), max(spans[-1]["end_ms"] for _, spans in restored),
                    text[:120], text[:12000], type_id, int(is_demo), now_ms()))
                c.executemany("INSERT INTO event_segments VALUES(?,?)", [(event_id, sid) for sid in segment_ids])
                c.execute("INSERT INTO memories(id,event_id,text,created_ms) VALUES(?,?,?,?)", (event_id, event_id, text[:8000], now_ms()))
                emit(c, "event.created", event_id)
            c.execute("UPDATE batches SET state='done',metrics=?,lease_until_ms=NULL,error=NULL WHERE id=?",
                      (json.dumps(metrics), batch["id"]))
            emit(c, "batch.done", batch["id"])
