"""Hemory Segment Processing Pipeline, Smart Event Aggregation & Timeline Read Engine.

Processes uploaded AAC-LC ADTS recording segments (`recording_segments` + `processing_chains`)
through:
  1. Anti-hallucination VAD + RMS energy speech gating (`slicing` -> `vad`)
  2. Multi-provider ASR + hallucination filtering + word-to-turn grouping (`transcribing`)
  3. 512-dim voiceprint diarization, enrolled matching & real AAC cluster previews (`diarizing`)
  4. P3 Scattered Chat folding vs P2 Structured Activity LLM summarization (`summarizing` -> `completed`)
  5. Chronological ascending day timeline (`GET /timeline/day`) & event detail queries.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import re
import secrets
import sqlite3
import subprocess
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np

from .audio import SAMPLE_RATE, atomic_write, pack_audio, restore_interval
from .config import Settings
from .db import Database
from .models import AsrSegment, VadConfig
from .providers import ProviderBlocked, ProviderError
from .speakers import SessionSpeakerResolver
from .storage import StorageService
from .vad import speech_intervals
from .voiceprint import (
    MARGIN_IDENTIFICATION,
    TAU_CLUSTERING,
    TAU_IDENTIFICATION,
    TAU_REJECTION,
    VoiceprintExtractor,
    calculate_clipping_ratio,
    calculate_snr,
    cluster_segment,
    compute_centroid,
    decode_audio_to_pcm16,
    match_enrolled_speaker,
    pack_embedding,
    unpack_embedding,
    update_centroid_incremental,
)

logger = logging.getLogger("echoledger.hemory_pipeline")

# Thresholds separating P3 ScatteredChat from P2 Structured Activity
CONTINUITY_WINDOW_MS = 5 * 60 * 1000       # 5 minutes continuity window
SCATTERED_GROUP_GAP_MS = 10 * 60 * 1000    # 10 minutes grouping window for P3 snippets
MIN_ACTIVITY_SPEECH_MS = 20_000            # >= 20s active speech -> P2 Activity
MIN_ACTIVITY_TURNS = 3                     # OR >= 3 conversational turns -> P2 Activity
MIN_ACTIVITY_CHARS = 55                    # OR >= 55 clean characters -> P2 Activity

VALID_CATEGORIES = {"meeting", "work", "learning", "meal", "social", "life", ""}

# Known single-phrase ASR silence/noise hallucinations
KNOWN_NOISE_HALLUCINATIONS = {
    "system",
    "system.",
    "thank you",
    "thank you.",
    "thank you for watching",
    "thank you for watching.",
    "thanks for watching",
    "thanks for watching.",
    "subtitles by the amara.org community",
    "amara.org",
    "uh",
    "um",
    "hmm",
    "ah",
    "oh",
    "uh, what would i like to tell you?",
}


def parse_iso_utc(iso_str: str) -> dt.datetime:
    cleaned = (iso_str or "").strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def epoch_ms_to_iso(ms: int) -> str:
    return (
        dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def join_turn_text(parts: list[str]) -> str:
    text = ""
    for part in parts:
        p = part.strip() if part else ""
        if not p:
            continue
        if (
            text
            and text[-1].isascii()
            and (text[-1].isalnum() or text[-1] in ",.;:!?")
            and p[0].isascii()
            and p[0].isalnum()
        ):
            text += " "
        text += p
    return text.strip()


def is_hallucinated_utterance(
    text: str,
    speech_ms: int,
    snr_db: float,
    is_demo: bool = False,
) -> bool:
    """Filter non-speech ASR hallucinations while preserving genuine short remarks."""
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if is_demo:
        return False

    # Strip punctuation/whitespace to inspect core lexical characters
    alnum_only = re.sub(r"[\s\W_]+", "", cleaned, flags=re.UNICODE)
    if not alnum_only:
        return True

    lowered = cleaned.casefold().strip()
    if lowered in KNOWN_NOISE_HALLUCINATIONS:
        return True

    # Ultra-low SNR + tiny 1-char noise burst
    if len(alnum_only) <= 1 and speech_ms < 900 and snr_db < 8.0:
        return True

    return False


def encode_pcm16_to_aac(samples: np.ndarray, fallback_aac_bytes: bytes = b"") -> bytes:
    """Encode 16kHz mono int16 PCM slice into real ADTS AAC-LC bytes for voiceprint preview."""
    if len(samples) > 0:
        # Cap preview to 8 seconds for snappy mobile playback
        clipped = samples[: SAMPLE_RATE * 8].astype("<i2")
        try:
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "s16le",
                    "-ar",
                    str(SAMPLE_RATE),
                    "-ac",
                    "1",
                    "-i",
                    "pipe:0",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "48k",
                    "-f",
                    "adts",
                    "pipe:1",
                ],
                input=clipped.tobytes(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=10,
            )
            if proc.stdout and len(proc.stdout) > 32:
                return proc.stdout
        except Exception:
            pass

    if fallback_aac_bytes:
        return fallback_aac_bytes
    return b""


def _normalize_message_epoch_ms(
    raw_start_ms: int,
    raw_end_ms: int,
    session_started_at: str | None,
) -> tuple[int, int]:
    """Support both UTC epoch ms (>= 1e11) and legacy session-relative ms (< 1e11)."""
    if raw_start_ms >= 100_000_000_000:
        return int(raw_start_ms), max(int(raw_start_ms), int(raw_end_ms))
    base_ms = 0
    if session_started_at:
        try:
            base_ms = int(parse_iso_utc(session_started_at).timestamp() * 1000)
        except Exception:
            base_ms = 0
    s_ms = base_ms + max(0, int(raw_start_ms))
    e_ms = base_ms + max(int(raw_start_ms), int(raw_end_ms))
    return s_ms, e_ms


class HemorySegmentProcessor:
    """Processes uploaded `recording_segments` & `processing_chains` end-to-end."""

    def __init__(self, pipeline: Any, retry_backoff_sec: float = 5.0, max_retries: int = 3):
        self.pipeline = pipeline
        self.db: Database = pipeline.db
        self.settings: Settings = pipeline.settings
        self.retry_backoff_sec = retry_backoff_sec
        self.max_retries = max_retries
        self.storage = StorageService(
            self.settings.data_dir,
            storage_dir=getattr(self.settings, "storage_dir", None),
        )
        self._voiceprint_extractor: VoiceprintExtractor | None = None
        self._voiceprint_model_id: str | None = None

    @property
    def voiceprint_extractor(self) -> VoiceprintExtractor:
        model_id = getattr(self.settings, "speaker_model_id", "") or "wespeaker_ecapa_tdnn512"
        if self._voiceprint_extractor is None or self._voiceprint_model_id != model_id:
            self._voiceprint_extractor = VoiceprintExtractor(model_id=model_id)
            self._voiceprint_model_id = model_id
        return self._voiceprint_extractor

    def claim_next_segment_chain(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Atomically claim the oldest pending (or stale running) segment processing chain."""
        now_dt = dt.datetime.now(dt.timezone.utc)
        now_iso = now_dt.isoformat()
        stale_iso = (now_dt - dt.timedelta(minutes=10)).isoformat()
        retry_cutoff_iso = (now_dt - dt.timedelta(seconds=self.retry_backoff_sec)).isoformat()

        with self.db.connect(immediate=True) as c:
            row = c.execute(
                """
                SELECT pc.*,
                       rs.segment_id, rs.segment_index, rs.storage_key, rs.started_at AS seg_started_at,
                       rs.duration_ms AS seg_duration_ms, rs.sample_rate AS seg_sample_rate,
                       rs.split_reason AS seg_split_reason, rs.user_id AS seg_user_id
                FROM processing_chains pc
                INNER JOIN recording_segments rs
                    ON rs.segment_id = ('seg_' || SUBSTR(pc.chain_id, 5))
                   AND rs.vault_id = pc.vault_id
                WHERE rs.upload_status = 'uploaded'
                  AND (
                      (
                          pc.status = 'pending'
                          AND (
                              pc.error_code IS NULL
                              OR pc.error_code NOT LIKE 'RETRYABLE_%'
                              OR pc.updated_at <= ?
                          )
                      )
                      OR (pc.status = 'running' AND pc.updated_at < ?)
                  )
                ORDER BY rs.started_at ASC, rs.segment_index ASC
                LIMIT 1
                """,
                (retry_cutoff_iso, stale_iso),
            ).fetchone()
            if row is None:
                return None

            d = dict(row)
            prev_retry_count = 0
            prev_err = str(d.get("error_code") or "")
            if prev_err.startswith("RETRYABLE_"):
                try:
                    prev_retry_count = int(prev_err.split("_", 1)[1])
                except ValueError:
                    prev_retry_count = 0

            updated = c.execute(
                """
                UPDATE processing_chains
                SET status = 'running', current_step = 'vad', progress_pct = 25.0,
                    error_code = NULL, error_message = NULL, updated_at = ?
                WHERE chain_id = ? AND status = ?
                """,
                (now_iso, d["chain_id"], d["status"]),
            ).rowcount
            if not updated:
                return None

        chain = {
            "chain_id": d["chain_id"],
            "tenant_id": d["tenant_id"],
            "vault_id": d["vault_id"],
            "recording_id": d["recording_id"],
            "live_session_id": d.get("live_session_id"),
            "retry_count": prev_retry_count,
        }
        seg = {
            "segment_id": d["segment_id"],
            "live_session_id": d["live_session_id"],
            "tenant_id": d["tenant_id"],
            "vault_id": d["vault_id"],
            "user_id": d.get("seg_user_id") or "usr_default",
            "segment_index": int(d["segment_index"]),
            "storage_key": d["storage_key"],
            "started_at": d["seg_started_at"],
            "duration_ms": int(d.get("seg_duration_ms") or 0),
            "sample_rate": int(d.get("seg_sample_rate") or SAMPLE_RATE),
            "split_reason": d.get("seg_split_reason") or "window",
        }
        return chain, seg

    def tick(self) -> bool:
        claimed = self.claim_next_segment_chain()
        if claimed is None:
            return False
        chain, seg = claimed
        try:
            self.process_segment(chain, seg)
        except ProviderBlocked as exc:
            self._fail_chain(chain, str(exc), "ASR provider is not configured or blocked")
        except ProviderError as exc:
            attempt = int(chain.get("retry_count") or 0) + 1
            if getattr(exc, "retryable", False) and attempt <= self.max_retries:
                self._requeue_chain(chain, attempt, str(exc))
            else:
                self._fail_chain(chain, str(exc), "Upstream ASR provider error")
        except Exception as exc:
            logger.exception("Hemory segment processing failed for chain=%s", chain["chain_id"])
            self._fail_chain(chain, f"PROCESSING_{type(exc).__name__}", str(exc) or "Internal audio processing error")
        return True

    def _requeue_chain(self, chain: dict[str, Any], attempt: int, reason: str) -> None:
        self.db.update_processing_chain(
            chain_id=chain["chain_id"],
            status="pending",
            current_step="transcribing",
            progress_pct=15.0,
            error_code=f"RETRYABLE_{attempt}",
            error_message=f"Retry {attempt}/{self.max_retries}: {reason}"[:240],
        )

    def _fail_chain(self, chain: dict[str, Any], error_code: str, error_message: str) -> None:
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        self.db.update_processing_chain(
            chain_id=chain["chain_id"],
            status="failed",
            error_code=error_code[:64],
            error_message=error_message[:240],
            analyzing_end_at=now_iso,
        )

    def _complete_chain(self, chain: dict[str, Any]) -> None:
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        self.db.update_processing_chain(
            chain_id=chain["chain_id"],
            status="completed",
            current_step="summarizing",
            progress_pct=100.0,
            error_code=None,
            error_message=None,
            analyzing_end_at=now_iso,
        )

    def _update_session_bounds(self, seg: dict[str, Any], actual_duration_ms: int) -> None:
        seg_start_dt = parse_iso_utc(seg["started_at"])
        dur_ms = max(actual_duration_ms, int(seg.get("duration_ms") or 0))
        seg_end_dt = seg_start_dt + dt.timedelta(milliseconds=dur_ms)
        seg_end_iso = seg_end_dt.isoformat().replace("+00:00", "Z")
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

        with self.db.connect(immediate=True) as c:
            sess = c.execute(
                "SELECT started_at, ended_at, duration_ms, status FROM recording_sessions WHERE live_session_id = ?",
                (seg["live_session_id"],),
            ).fetchone()
            if not sess:
                return
            total_row = c.execute(
                "SELECT COALESCE(SUM(duration_ms), 0) AS total_ms FROM recording_segments WHERE live_session_id = ?",
                (seg["live_session_id"],),
            ).fetchone()
            total_ms = max(int(total_row["total_ms"] or 0), dur_ms)
            prev_end = sess["ended_at"]
            new_end = seg_end_iso
            if prev_end:
                try:
                    if parse_iso_utc(prev_end) > seg_end_dt:
                        new_end = prev_end
                except Exception:
                    pass
            new_status = (
                "stopped"
                if seg.get("split_reason") in {"user_stop", "userStop"}
                else sess["status"]
            )
            c.execute(
                """
                UPDATE recording_sessions
                SET ended_at = ?, duration_ms = ?, status = ?, updated_at = ?
                WHERE live_session_id = ?
                """,
                (new_end, total_ms, new_status, now_iso, seg["live_session_id"]),
            )

    def process_segment(self, chain: dict[str, Any], seg: dict[str, Any]) -> None:
        chain_id = chain["chain_id"]
        tenant_id = seg["tenant_id"]
        vault_id = seg["vault_id"]
        session_id = seg["live_session_id"]
        seg_id = seg["segment_id"]

        audio_bytes = self.storage.read_recording(seg["storage_key"])
        samples = decode_audio_to_pcm16(audio_bytes)
        actual_duration_ms = len(samples) * 1000 // SAMPLE_RATE if len(samples) > 0 else seg["duration_ms"]
        self._update_session_bounds(seg, actual_duration_ms)

        if len(samples) < int(0.30 * SAMPLE_RATE):
            self._finish_silent_segment(chain, seg)
            return

        # 1. Anti-hallucination VAD + RMS energy gating
        active_prof = self.db.active_profile()
        base_cfg = (
            VadConfig.model_validate_json(active_prof["config"])
            if active_prof and active_prof.get("config")
            else VadConfig()
        )
        vad_cfg = base_cfg.model_copy(
            update={
                "retain_uncertain": False,
                "threshold": max(0.45, float(base_cfg.threshold)),
                "min_speech_ms": max(300, int(base_cfg.min_speech_ms)),
            }
        )
        probs = self.pipeline.scorer.probabilities(samples)
        raw_intervals = speech_intervals(probs, len(samples), vad_cfg)

        # Drop near-zero RMS intervals (silence / room hiss)
        gated_intervals: list[tuple[int, int]] = []
        for a, b in raw_intervals:
            chunk = samples[a:b]
            if len(chunk) < int(0.25 * SAMPLE_RATE):
                continue
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
            if rms < 150.0:
                continue
            gated_intervals.append((a, b))

        if not gated_intervals:
            self._finish_silent_segment(chain, seg)
            return

        sources = [
            {
                "input_start": 0,
                "input_end": len(samples),
                "original_start": 0,
                "chunk_id": seg_id,
                "chunk_start": 0,
            }
        ]
        packed, mapping = pack_audio(samples, gated_intervals, sources)
        if len(packed) < int(0.30 * SAMPLE_RATE):
            self._finish_silent_segment(chain, seg)
            return

        overall_snr_db = calculate_snr(packed)

        # 2. ASR Transcription
        self.db.update_processing_chain(
            chain_id=chain_id,
            status="running",
            current_step="transcribing",
            progress_pct=55.0,
        )
        self._check_asr_ready()
        transcript = self.pipeline.transcriber.transcribe(packed)

        seg_start_epoch_ms = int(parse_iso_utc(seg["started_at"]).timestamp() * 1000)
        turns = self._restore_and_group_turns(
            raw_segments=transcript.segments,
            mapping=mapping,
            seg_start_epoch_ms=seg_start_epoch_ms,
            samples=samples,
            overall_snr_db=overall_snr_db,
            is_demo=transcript.is_demo,
        )

        if not turns:
            self._finish_silent_segment(chain, seg)
            return

        # 3. Speaker Diarization & Voiceprint Clustering
        self.db.update_processing_chain(
            chain_id=chain_id,
            status="running",
            current_step="diarizing",
            progress_pct=75.0,
        )
        attributed_turns = self._attribute_speakers(
            turns=turns,
            samples=samples,
            audio_bytes=audio_bytes,
            tenant_id=tenant_id,
            vault_id=vault_id,
            seg_id=seg_id,
        )

        # 4. Persist Sections & Section Messages (alimenting FTS5 search & P3 timeline)
        self.db.update_processing_chain(
            chain_id=chain_id,
            status="running",
            current_step="summarizing",
            progress_pct=90.0,
        )
        section_id = self._persist_section_and_messages(
            seg=seg,
            turns=attributed_turns,
        )

        # 5. Smart Aggregation: Fold into P3 ScatteredChat OR Summarize into P2 Activity
        self._aggregate_or_fold_conversation(
            seg=seg,
            section_id=section_id,
            turns=attributed_turns,
            is_demo=transcript.is_demo,
        )

        # 6. Complete Chain & Emit Mutation
        self._complete_chain(chain)
        self.db.append_recording_mutation(
            tenant_id=tenant_id,
            vault_id=vault_id,
            entity_type="segment",
            entity_id=seg_id,
            mutation_type="segment_transcribed",
            payload_json={
                "segment_id": seg_id,
                "live_session_id": session_id,
                "section_id": section_id,
                "utterance_count": len(attributed_turns),
                "speech_ms": int(len(packed) * 1000 // SAMPLE_RATE),
            },
        )

    def _finish_silent_segment(self, chain: dict[str, Any], seg: dict[str, Any]) -> None:
        self._complete_chain(chain)
        self.db.append_recording_mutation(
            tenant_id=seg["tenant_id"],
            vault_id=seg["vault_id"],
            entity_type="segment",
            entity_id=seg["segment_id"],
            mutation_type="segment_transcribed",
            payload_json={
                "segment_id": seg["segment_id"],
                "live_session_id": seg["live_session_id"],
                "utterance_count": 0,
                "speech_ms": 0,
            },
        )

    def _check_asr_ready(self) -> None:
        if self.pipeline._explicit_transcriber:
            return
        if self.settings.asr_provider == "disabled":
            raise ProviderBlocked("ASR_NOT_CONFIGURED")
        if self.settings.asr_provider == "gemini" and not self.settings.gemini_key:
            raise ProviderBlocked("GEMINI_KEY_MISSING")

    def _restore_and_group_turns(
        self,
        raw_segments: list[AsrSegment],
        mapping: list[Any],
        seg_start_epoch_ms: int,
        samples: np.ndarray,
        overall_snr_db: float,
        is_demo: bool,
    ) -> list[dict[str, Any]]:
        raw_items: list[dict[str, Any]] = []
        for s in raw_segments:
            spans = restore_interval(s.start_ms, s.end_ms, mapping, seg_start_epoch_ms)
            if not spans or not (s.text or "").strip():
                continue
            raw_items.append(
                {
                    "speaker_label": (s.speaker_label or "speaker_1").strip() or "speaker_1",
                    "text": s.text.strip(),
                    "start_ms": int(spans[0]["start_ms"]),
                    "end_ms": max(int(spans[0]["start_ms"]) + 1, int(spans[-1]["end_ms"])),
                    "sample_start": int(spans[0]["source_start_sample"]),
                    "sample_end": int(spans[-1]["source_end_sample"]),
                    "timing_quality": s.timing_quality,
                }
            )

        if not raw_items:
            return []

        raw_items.sort(key=lambda item: (item["start_ms"], item["end_ms"]))

        # Merge contiguous word-level or ultra-close fragments from the same local speaker
        grouped: list[dict[str, Any]] = []
        for item in raw_items:
            if grouped:
                prev = grouped[-1]
                same_speaker = prev["speaker_label"] == item["speaker_label"]
                gap_ms = item["start_ms"] - prev["end_ms"]
                should_merge = same_speaker and (
                    item["timing_quality"] == "word"
                    or prev["timing_quality"] == "word"
                    or (gap_ms <= 1200 and len(prev["text"]) + len(item["text"]) <= 220)
                )
                if should_merge and gap_ms <= 2500:
                    prev["parts"].append(item["text"])
                    prev["text"] = join_turn_text(prev["parts"])
                    prev["end_ms"] = max(prev["end_ms"], item["end_ms"])
                    prev["sample_end"] = max(prev["sample_end"], item["sample_end"])
                    prev["sample_slices"].append((item["sample_start"], item["sample_end"]))
                    continue

            grouped.append(
                {
                    "speaker_label": item["speaker_label"],
                    "parts": [item["text"]],
                    "text": item["text"],
                    "start_ms": item["start_ms"],
                    "end_ms": item["end_ms"],
                    "sample_start": item["sample_start"],
                    "sample_end": item["sample_end"],
                    "sample_slices": [(item["sample_start"], item["sample_end"])],
                    "timing_quality": item["timing_quality"],
                }
            )

        clean_turns: list[dict[str, Any]] = []
        for turn in grouped:
            speech_ms = max(1, turn["end_ms"] - turn["start_ms"])
            slice_arrays = [
                samples[max(0, a) : min(len(samples), b)]
                for a, b in turn["sample_slices"]
                if min(len(samples), b) > max(0, a)
            ]
            turn_pcm = np.concatenate(slice_arrays) if slice_arrays else np.empty(0, dtype=np.int16)
            turn_snr = calculate_snr(turn_pcm) if len(turn_pcm) >= 320 else overall_snr_db

            if is_hallucinated_utterance(
                text=turn["text"],
                speech_ms=speech_ms,
                snr_db=turn_snr,
                is_demo=is_demo,
            ):
                continue

            turn["pcm"] = turn_pcm
            turn["snr_db"] = turn_snr
            clean_turns.append(turn)

        return clean_turns

    def _load_enrolled_centroids(self, vault_id: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        profiles_rows = self.db.all(
            "SELECT speaker_id, name, suspected_name FROM speaker_profiles WHERE vault_id = ?",
            (vault_id,),
        )
        profiles_map = {r["speaker_id"]: dict(r) for r in profiles_rows}

        cluster_rows = self.db.all(
            """
            SELECT speaker_id, embedding_blob
            FROM voiceprint_clusters
            WHERE vault_id = ?
              AND status IN ('enrolled', 'merged', 'identified')
              AND speaker_id IS NOT NULL
              AND embedding_blob IS NOT NULL
            """,
            (vault_id,),
        )
        by_speaker: dict[str, list[np.ndarray]] = {}
        for row in cluster_rows:
            spk_id = row["speaker_id"]
            if spk_id not in profiles_map:
                continue
            try:
                vec = unpack_embedding(row["embedding_blob"])
                by_speaker.setdefault(spk_id, []).append(vec)
            except Exception:
                continue

        enrolled_list = []
        for spk_id, vecs in by_speaker.items():
            if vecs:
                enrolled_list.append(
                    {
                        "speaker_id": spk_id,
                        "centroid": compute_centroid(vecs),
                    }
                )
        return profiles_map, enrolled_list

    def _attribute_speakers(
        self,
        turns: list[dict[str, Any]],
        samples: np.ndarray,
        audio_bytes: bytes,
        tenant_id: str,
        vault_id: str,
        seg_id: str,
    ) -> list[dict[str, Any]]:
        profiles_map, enrolled_list = self._load_enrolled_centroids(vault_id)
        resolver = SessionSpeakerResolver(speaker_profiles=profiles_map)

        # Pool audio per local speaker label inside this segment for higher-SNR embeddings
        by_label_pcm: dict[str, list[np.ndarray]] = {}
        for t in turns:
            if len(t["pcm"]) > 0:
                by_label_pcm.setdefault(t["speaker_label"], []).append(t["pcm"])

        label_resolution: dict[str, dict[str, Any]] = {}
        min_cluster_sec = max(1.5, float(getattr(self.settings, "min_speech_ms", 1500) or 1500) / 1000.0)
        sim_threshold = float(getattr(self.settings, "similarity_threshold", TAU_IDENTIFICATION) or TAU_IDENTIFICATION)
        margin = float(getattr(self.settings, "margin", MARGIN_IDENTIFICATION) or MARGIN_IDENTIFICATION)
        auto_match = bool(getattr(self.settings, "auto_match_enabled", True))

        for label, chunks in by_label_pcm.items():
            pooled = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.int16)
            duration_sec = len(pooled) / float(SAMPLE_RATE)
            speaker_id: str | None = None
            cluster_id: str | None = None
            confidence: float = 0.85

            if duration_sec >= 1.0:
                embedding = self.voiceprint_extractor.extract_512(
                    pooled,
                    sample_rate=SAMPLE_RATE,
                    context_id=f"{seg_id}:{label}",
                )
                if auto_match and enrolled_list:
                    match = match_enrolled_speaker(
                        embedding,
                        enrolled_list,
                        threshold=sim_threshold,
                        margin=margin,
                    )
                    if match["accepted"] and match["speaker_id"]:
                        speaker_id = match["speaker_id"]
                        confidence = float(match.get("similarity") or 0.9)

                if (
                    speaker_id is None
                    and duration_sec >= min_cluster_sec
                    and calculate_snr(pooled) >= 6.0
                    and calculate_clipping_ratio(pooled) < 0.02
                ):
                    cluster_id, confidence = self._assign_or_create_pending_cluster(
                        embedding=embedding,
                        pooled_pcm=pooled,
                        fallback_aac=audio_bytes,
                        tenant_id=tenant_id,
                        vault_id=vault_id,
                    )

            display_name = resolver.resolve(
                speaker_id=speaker_id,
                cluster_id=cluster_id,
                local_label=None,
            )
            label_resolution[label] = {
                "speaker_id": speaker_id,
                "cluster_id": cluster_id,
                "speaker_name": display_name,
                "confidence": round(confidence, 4),
            }

        attributed: list[dict[str, Any]] = []
        for t in turns:
            res = label_resolution.get(t["speaker_label"])
            if res is None:
                res = {
                    "speaker_id": None,
                    "cluster_id": None,
                    "speaker_name": resolver.resolve(local_label=None),
                    "confidence": 0.8,
                }
            attributed.append(
                {
                    "speaker_id": res["speaker_id"],
                    "cluster_id": res["cluster_id"],
                    "speaker_name": res["speaker_name"],
                    "content": t["text"],
                    "start_ms": t["start_ms"],
                    "end_ms": t["end_ms"],
                    "confidence": res["confidence"],
                }
            )
        return attributed

    def _assign_or_create_pending_cluster(
        self,
        embedding: np.ndarray,
        pooled_pcm: np.ndarray,
        fallback_aac: bytes,
        tenant_id: str,
        vault_id: str,
    ) -> tuple[str | None, float]:
        pending_rows = self.db.all(
            """
            SELECT cluster_id, embedding_blob, occurrences_count
            FROM voiceprint_clusters
            WHERE vault_id = ? AND status = 'pending' AND embedding_blob IS NOT NULL
            """,
            (vault_id,),
        )
        decision = cluster_segment(
            embedding=embedding,
            pending_clusters=[dict(r) for r in pending_rows],
            threshold=TAU_CLUSTERING,
            rejection_floor=TAU_REJECTION,
        )
        action = decision["action"]
        sim = float(decision.get("similarity") or 0.75)

        if action == "reject":
            return None, sim

        clusters_dir = self.settings.data_dir / "clusters"
        if action == "merge" and decision.get("cluster_id"):
            cid = decision["cluster_id"]
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            matched_row = next((r for r in pending_rows if r["cluster_id"] == cid), None)
            if matched_row and matched_row["embedding_blob"]:
                old_vec = unpack_embedding(matched_row["embedding_blob"])
                old_count = int(matched_row["occurrences_count"] or 1)
                updated_vec = update_centroid_incremental(old_vec, old_count, embedding)
                blob = pack_embedding(updated_vec)
                with self.db.connect(immediate=True) as c:
                    c.execute(
                        """
                        UPDATE voiceprint_clusters
                        SET embedding_blob = ?, occurrences_count = occurrences_count + 1,
                            confidence_score = MAX(confidence_score, ?), updated_at = ?
                        WHERE cluster_id = ? AND vault_id = ?
                        """,
                        (blob, round(sim, 4), now_iso, cid, vault_id),
                    )
            preview_path = clusters_dir / f"{cid}.aac"
            if not preview_path.is_file():
                aac_preview = encode_pcm16_to_aac(pooled_pcm, fallback_aac)
                if aac_preview:
                    atomic_write(preview_path, aac_preview)
            return cid, sim

        # Spawn new pending cluster with real human voice preview
        new_cid = f"clu_{secrets.token_hex(6)}"
        preview_path = clusters_dir / f"{new_cid}.aac"
        aac_preview = encode_pcm16_to_aac(pooled_pcm, fallback_aac)
        if aac_preview:
            atomic_write(preview_path, aac_preview)

        self.db.create_voiceprint_cluster(
            cluster_id=new_cid,
            tenant_id=tenant_id,
            vault_id=vault_id,
            status="pending",
            embedding_blob=pack_embedding(embedding),
            audio_sample_url=f"/clusters/{new_cid}/audio_preview",
            confidence_score=round(max(0.65, sim), 4),
            occurrences_count=1,
        )
        self.db.append_recording_mutation(
            tenant_id=tenant_id,
            vault_id=vault_id,
            entity_type="cluster",
            entity_id=new_cid,
            mutation_type="cluster_updated",
            payload_json={
                "cluster_id": new_cid,
                "status": "pending",
                "audio_sample_url": f"/clusters/{new_cid}/audio_preview",
            },
        )
        return new_cid, max(0.65, sim)

    def _persist_section_and_messages(
        self,
        seg: dict[str, Any],
        turns: list[dict[str, Any]],
    ) -> str:
        seg_token = seg["segment_id"].removeprefix("seg_")
        section_id = f"sec_{seg_token}"
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        start_ms = min(t["start_ms"] for t in turns)
        end_ms = max(t["end_ms"] for t in turns)
        full_transcript = "\n".join(f"{t['speaker_name']}: {t['content']}" for t in turns)
        unique_speakers = len({t["speaker_name"] for t in turns})

        with self.db.connect(immediate=True) as c:
            existing = c.execute(
                "SELECT section_id FROM sections WHERE section_id = ? OR (live_session_id = ? AND section_index = ?)",
                (section_id, seg["live_session_id"], seg["segment_index"]),
            ).fetchone()
            if existing:
                section_id = existing["section_id"]
                c.execute("DELETE FROM section_messages WHERE section_id = ?", (section_id,))
                c.execute(
                    """
                    UPDATE sections
                    SET title = ?, start_ms = ?, end_ms = ?, transcript = ?,
                        speaker_count = ?, updated_at = ?
                    WHERE section_id = ?
                    """,
                    (
                        seg["segment_id"],
                        start_ms,
                        end_ms,
                        full_transcript,
                        unique_speakers,
                        now_iso,
                        section_id,
                    ),
                )
            else:
                c.execute(
                    """
                    INSERT INTO sections (
                        section_id, live_session_id, tenant_id, vault_id, section_index,
                        title, start_ms, end_ms, transcript, speaker_count, quality_score,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?)
                    """,
                    (
                        section_id,
                        seg["live_session_id"],
                        seg["tenant_id"],
                        seg["vault_id"],
                        seg["segment_index"],
                        seg["segment_id"],
                        start_ms,
                        end_ms,
                        full_transcript,
                        unique_speakers,
                        now_iso,
                        now_iso,
                    ),
                )

            for idx, t in enumerate(turns):
                msg_id = f"msg_{seg_token}_{idx:03d}"
                c.execute(
                    """
                    INSERT INTO section_messages (
                        message_id, section_id, speaker_id, cluster_id, speaker_name,
                        content, start_ms, end_ms, confidence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_id,
                        section_id,
                        t["speaker_id"],
                        t["cluster_id"],
                        t["speaker_name"],
                        t["content"],
                        t["start_ms"],
                        t["end_ms"],
                        t["confidence"],
                        now_iso,
                    ),
                )
        return section_id

    def _aggregate_or_fold_conversation(
        self,
        seg: dict[str, Any],
        section_id: str,
        turns: list[dict[str, Any]],
        is_demo: bool,
    ) -> None:
        vault_id = seg["vault_id"]
        tenant_id = seg["tenant_id"]
        first_start_ms = min(t["start_ms"] for t in turns)
        last_end_ms = max(t["end_ms"] for t in turns)

        # Check for an ongoing/recent P2 memory_event within 5 minutes before first_start_ms
        window_from_iso = epoch_ms_to_iso(first_start_ms - CONTINUITY_WINDOW_MS)
        window_to_iso = epoch_ms_to_iso(last_end_ms + 60_000)
        recent_events = self.db.all(
            """
            SELECT * FROM memory_events
            WHERE vault_id = ?
              AND end_time >= ?
              AND start_time <= ?
            ORDER BY end_time DESC
            LIMIT 1
            """,
            (vault_id, window_from_iso, window_to_iso),
        )

        if recent_events:
            target_ev = dict(recent_events[0])
            ev_start_ms = min(first_start_ms, int(parse_iso_utc(target_ev["start_time"]).timestamp() * 1000))
            ev_end_ms = max(last_end_ms, int(parse_iso_utc(target_ev["end_time"]).timestamp() * 1000))
            window_msgs = self._fetch_messages_in_epoch_range(vault_id, ev_start_ms, ev_end_ms)
            if window_msgs:
                summary_meta = self._summarize_conversation(window_msgs, is_demo=is_demo)
                self._upsert_memory_event(
                    event_id=target_ev["event_id"],
                    tenant_id=tenant_id,
                    vault_id=vault_id,
                    session_id=target_ev.get("session_id") or seg["live_session_id"],
                    start_ms=ev_start_ms,
                    end_ms=ev_end_ms,
                    summary_meta=summary_meta,
                    messages=window_msgs,
                )
            return

        # Gather unassigned recent messages within the 5-minute continuity window
        candidate_msgs = self._fetch_unassigned_recent_messages(
            vault_id=vault_id,
            from_ms=first_start_ms - CONTINUITY_WINDOW_MS,
            to_ms=last_end_ms,
        )
        if not candidate_msgs:
            return

        total_speech_ms = sum(max(0, m["end_ms"] - m["start_ms"]) for m in candidate_msgs)
        clean_chars = sum(
            len(re.sub(r"\[DEMO\]|\s+", "", m["content"]))
            for m in candidate_msgs
        )
        turn_count = len(candidate_msgs)

        is_substantial = (
            total_speech_ms >= MIN_ACTIVITY_SPEECH_MS
            or turn_count >= MIN_ACTIVITY_TURNS
            or clean_chars >= MIN_ACTIVITY_CHARS
        )
        if not is_substantial:
            # Leave as P3 ScatteredChat (automatically grouped by GET /timeline/day)
            return

        ev_start_ms = min(m["start_ms"] for m in candidate_msgs)
        ev_end_ms = max(m["end_ms"] for m in candidate_msgs)
        summary_meta = self._summarize_conversation(candidate_msgs, is_demo=is_demo)
        event_id = f"evt_{secrets.token_hex(6)}"
        self._upsert_memory_event(
            event_id=event_id,
            tenant_id=tenant_id,
            vault_id=vault_id,
            session_id=seg["live_session_id"],
            start_ms=ev_start_ms,
            end_ms=ev_end_ms,
            summary_meta=summary_meta,
            messages=candidate_msgs,
        )

    def _fetch_messages_in_epoch_range(
        self,
        vault_id: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        rows = self.db.all(
            """
            SELECT sm.message_id, sm.section_id, sm.speaker_id, sm.cluster_id,
                   sm.speaker_name, sm.content, sm.start_ms, sm.end_ms, sm.confidence,
                   s.live_session_id, s.title AS segment_ref,
                   rs.started_at AS session_started_at
            FROM section_messages sm
            INNER JOIN sections s ON sm.section_id = s.section_id
            LEFT JOIN recording_sessions rs ON s.live_session_id = rs.live_session_id
            WHERE s.vault_id = ?
              AND sm.end_ms >= ?
              AND sm.start_ms <= ?
            ORDER BY sm.start_ms ASC
            """,
            (vault_id, start_ms, end_ms),
        )
        return [dict(r) for r in rows]

    def _fetch_unassigned_recent_messages(
        self,
        vault_id: str,
        from_ms: int,
        to_ms: int,
    ) -> list[dict[str, Any]]:
        msgs = self._fetch_messages_in_epoch_range(vault_id, from_ms, to_ms)
        if not msgs:
            return []
        ev_rows = self.db.all(
            """
            SELECT start_time, end_time FROM memory_events
            WHERE vault_id = ? AND end_time >= ? AND start_time <= ?
            """,
            (vault_id, epoch_ms_to_iso(from_ms), epoch_ms_to_iso(to_ms)),
        )
        covered_spans = [
            (
                int(parse_iso_utc(r["start_time"]).timestamp() * 1000),
                int(parse_iso_utc(r["end_time"]).timestamp() * 1000),
            )
            for r in ev_rows
        ]
        unassigned = []
        for m in msgs:
            mid = (int(m["start_ms"]) + int(m["end_ms"])) // 2
            if any(cs <= mid <= ce for cs, ce in covered_spans):
                continue
            unassigned.append(m)
        return unassigned

    def _upsert_memory_event(
        self,
        event_id: str,
        tenant_id: str,
        vault_id: str,
        session_id: str | None,
        start_ms: int,
        end_ms: int,
        summary_meta: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        profiles_rows = self.db.all(
            "SELECT speaker_id, name, suspected_name FROM speaker_profiles WHERE vault_id = ?",
            (vault_id,),
        )
        resolver = SessionSpeakerResolver(
            speaker_profiles={r["speaker_id"]: dict(r) for r in profiles_rows}
        )
        speakers_ordered: list[str] = []
        for m in messages:
            resolved = resolver.resolve(
                speaker_id=m.get("speaker_id"),
                cluster_id=m.get("cluster_id"),
                local_label=m.get("speaker_name"),
            )
            if resolved not in speakers_ordered:
                speakers_ordered.append(resolved)

        title = summary_meta["title"]
        summary = summary_meta["summary"]
        category = summary_meta["category"]
        keywords = summary_meta["keywords"]
        start_iso = epoch_ms_to_iso(start_ms)
        end_iso = epoch_ms_to_iso(max(start_ms + 1000, end_ms))
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

        with self.db.connect(immediate=True) as c:
            existing = c.execute(
                "SELECT event_id FROM memory_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing:
                c.execute(
                    """
                    UPDATE memory_events
                    SET title = ?, summary = ?, category = ?, start_time = ?, end_time = ?,
                        speaker_names = ?, keywords = ?, updated_at = ?
                    WHERE event_id = ?
                    """,
                    (
                        title,
                        summary,
                        category,
                        start_iso,
                        end_iso,
                        json.dumps(speakers_ordered, ensure_ascii=False),
                        json.dumps(keywords, ensure_ascii=False),
                        now_iso,
                        event_id,
                    ),
                )
            else:
                c.execute(
                    """
                    INSERT INTO memory_events (
                        event_id, tenant_id, vault_id, session_id, title, summary,
                        category, start_time, end_time, speaker_names, keywords,
                        confidence, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?)
                    """,
                    (
                        event_id,
                        tenant_id,
                        vault_id,
                        session_id,
                        title,
                        summary,
                        category,
                        start_iso,
                        end_iso,
                        json.dumps(speakers_ordered, ensure_ascii=False),
                        json.dumps(keywords, ensure_ascii=False),
                        now_iso,
                        now_iso,
                    ),
                )

        self.db.append_recording_mutation(
            tenant_id=tenant_id,
            vault_id=vault_id,
            entity_type="event",
            entity_id=event_id,
            mutation_type="event_created",
            payload_json={
                "tenant_id": tenant_id,
                "live_session_id": session_id,
                "event_type": "activity",
                "title": title,
                "summary": summary,
                "category": category,
                "started_at": start_ms,
                "ended_at": max(start_ms + 1000, end_ms),
                "duration_ms": max(1000, end_ms - start_ms),
                "priority_tier": 2,
                "speaker_count": len(speakers_ordered),
                "payload_json": json.dumps(
                    {
                        "speakers": speakers_ordered,
                        "tags": keywords,
                        "category": category,
                    },
                    ensure_ascii=False,
                ),
            },
        )

    def _summarize_conversation(
        self,
        messages: list[dict[str, Any]],
        is_demo: bool = False,
    ) -> dict[str, Any]:
        if not is_demo and self.settings.asr_provider in {"vertexai", "gemini"}:
            llm_res = self._try_llm_summarize(messages)
            if llm_res is not None:
                return llm_res
        return self._heuristic_summarize(messages)

    def _try_llm_summarize(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        dialogue = "\n".join(
            f"[{m.get('speaker_name') or 'Speaker 1'}] {m['content']}"
            for m in messages[:40]
        )
        prompt = (
            "Summarize this recorded conversation into structured JSON.\n"
            "Rules:\n"
            "1. Use the same primary language as the transcript.\n"
            "2. 'title': concise topic headline (4-22 chars). NEVER copy a raw sentence verbatim and NEVER output 'Unknown'.\n"
            "3. 'summary': 1-2 sentence objective synthesis of key points, decisions, or context. Must NOT repeat 'title'.\n"
            "4. 'category': strictly one of ['meeting', 'work', 'learning', 'meal', 'social', 'life', ''].\n"
            "5. 'keywords': 1-4 short topic tags without '#'.\n\n"
            f"Transcript:\n{dialogue}"
        )
        try:
            from google import genai
            from google.genai import types as genai_types
            from .config import resolve_vertex_credentials_path

            client = None
            model_name = "gemini-3.8-flash"
            if self.settings.asr_provider == "vertexai":
                model_name = self.settings.vertex_model or "gemini-3.8-flash"
                if model_name == "gemini-3.5-transcribe":
                    model_name = "gemini-3.8-flash"
                cred_path, detected_proj = (
                    resolve_vertex_credentials_path(self.settings.vertex_credentials)
                    if self.settings.vertex_credentials
                    else ("", "")
                )
                proj = self.settings.vertex_project or detected_proj
                loc = self.settings.vertex_location or "global"
                if self.settings.vertex_api_key and not proj and not cred_path:
                    client = genai.Client(vertexai=True, api_key=self.settings.vertex_api_key)
                else:
                    creds = None
                    if cred_path and Path(cred_path).is_file():
                        import google.auth

                        creds, d_proj = google.auth.load_credentials_from_file(
                            cred_path,
                            scopes=["https://www.googleapis.com/auth/cloud-platform"],
                        )
                        if not proj and d_proj:
                            proj = d_proj
                    if proj or creds:
                        client = genai.Client(
                            vertexai=True,
                            project=proj or None,
                            location=loc,
                            credentials=creds,
                        )
            elif self.settings.gemini_key:
                model_name = self.settings.gemini_model or "gemini-2.5-flash"
                if model_name == "gemini-3.5-transcribe":
                    model_name = "gemini-2.5-flash"
                client = genai.Client(api_key=self.settings.gemini_key)

            if client is None:
                return None

            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            raw = (resp.text or "").strip()
            if not raw:
                return None
            parsed = json.loads(raw)
            title = str(parsed.get("title") or "").strip()[:60]
            summary = str(parsed.get("summary") or "").strip()[:500]
            category = str(parsed.get("category") or "").strip().lower()
            if category not in VALID_CATEGORIES:
                category = ""
            keywords = [
                str(k).strip().lstrip("#")[:24]
                for k in (parsed.get("keywords") or [])
                if str(k).strip()
            ][:4]
            if not title or title.lower() == "unknown":
                return None
            if summary == title:
                summary = ""
            return {
                "title": title,
                "summary": summary,
                "category": category,
                "keywords": keywords,
            }
        except Exception as exc:
            logger.debug("LLM summarization fallback triggered: %s", type(exc).__name__)
            return None

    def _heuristic_summarize(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        combined = " ".join(m["content"].replace("[DEMO]", "").strip() for m in messages).strip()
        lower = combined.casefold()

        category = ""
        keywords: list[str] = []
        category_rules = [
            ("meeting", ["会议", "组会", "开会", "项目", "评审", "排期", "meeting", "roadmap", "sync", "sprint"]),
            ("work", ["代码", "接口", "部署", "需求", "重构", "测试", "api", "bug", "release", "client"]),
            ("learning", ["算法", "论文", "课程", "复习", "学习", "lecture", "paper", "study", "model"]),
            ("meal", ["吃饭", "午饭", "晚饭", "早餐", "点餐", "餐厅", "火锅", "lunch", "dinner", "coffee"]),
            ("social", ["周末", "散步", "聚会", "朋友", "电影", "旅行", "friend", "trip", "weekend"]),
        ]
        for cat, terms in category_rules:
            matched = [t for t in terms if t in lower]
            if matched:
                category = cat
                keywords = matched[:3]
                break

        first_clause = re.split(r"[。！？!?；;\n]", combined)[0].strip()
        first_clause = re.sub(r"^(嗯|啊|那个|就是|uh|um)[，,\s]*", "", first_clause, flags=re.IGNORECASE).strip()

        cat_labels = {
            "meeting": "会议讨论",
            "work": "工作交流",
            "learning": "学习研讨",
            "meal": "用餐交流",
            "social": "日常交谈",
        }
        prefix = cat_labels.get(category, "对话记录")
        if first_clause and len(first_clause) <= 18:
            title = f"{prefix}：{first_clause}"
        elif first_clause:
            title = f"{prefix}：{first_clause[:16]}…"
        else:
            title = prefix

        speakers = []
        for m in messages:
            spk = m.get("speaker_name") or "Speaker 1"
            if spk not in speakers:
                speakers.append(spk)
        spk_text = "、".join(speakers[:3])
        turn_count = len(messages)
        excerpt = combined[:110] + ("…" if len(combined) > 110 else "")
        summary = f"{spk_text} 共进行 {turn_count} 段交流，主要内容：{excerpt}"
        if summary.strip() == title.strip():
            summary = f"{spk_text} 共 {turn_count} 段发言记录。"

        return {
            "title": title,
            "summary": summary,
            "category": category,
            "keywords": keywords,
        }


# -----------------------------------------------------------------------------
# Timeline Day & Memory Event Query Engine
# -----------------------------------------------------------------------------


def refresh_vault_events_for_cluster(
    db: Database,
    vault_id: str,
    tenant_id: str,
    cluster_id: str,
    speaker_id: str | None = None,
    speaker_name: str | None = None,
) -> None:
    """Refresh `memory_events.speaker_names` when a voiceprint cluster is confirmed/merged."""
    msgs = db.all(
        """
        SELECT sm.start_ms, sm.end_ms
        FROM section_messages sm
        INNER JOIN sections s ON sm.section_id = s.section_id
        WHERE s.vault_id = ? AND (sm.cluster_id = ? OR (? IS NOT NULL AND sm.speaker_id = ?))
        """,
        (vault_id, cluster_id, speaker_id, speaker_id),
    )
    if msgs:
        min_iso = epoch_ms_to_iso(min(int(m["start_ms"]) for m in msgs) - 60_000)
        max_iso = epoch_ms_to_iso(max(int(m["end_ms"]) for m in msgs) + 60_000)
        events = db.all(
            """
            SELECT event_id, start_time, end_time
            FROM memory_events
            WHERE vault_id = ? AND end_time >= ? AND start_time <= ?
            """,
            (vault_id, min_iso, max_iso),
        )
        profiles = {
            r["speaker_id"]: dict(r)
            for r in db.all(
                "SELECT speaker_id, name, suspected_name FROM speaker_profiles WHERE vault_id = ?",
                (vault_id,),
            )
        }
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        for ev in events:
            ev_s = int(parse_iso_utc(ev["start_time"]).timestamp() * 1000)
            ev_e = int(parse_iso_utc(ev["end_time"]).timestamp() * 1000)
            ev_msgs = db.all(
                """
                SELECT sm.speaker_id, sm.cluster_id, sm.speaker_name
                FROM section_messages sm
                INNER JOIN sections s ON sm.section_id = s.section_id
                WHERE s.vault_id = ? AND sm.end_ms >= ? AND sm.start_ms <= ?
                ORDER BY sm.start_ms ASC
                """,
                (vault_id, ev_s, ev_e),
            )
            resolver = SessionSpeakerResolver(speaker_profiles=profiles)
            names: list[str] = []
            for m in ev_msgs:
                nm = resolver.resolve(
                    speaker_id=m.get("speaker_id"),
                    cluster_id=m.get("cluster_id"),
                    local_label=m.get("speaker_name"),
                )
                if nm not in names:
                    names.append(nm)
            if names:
                with db.connect(immediate=True) as c:
                    c.execute(
                        "UPDATE memory_events SET speaker_names = ?, updated_at = ? WHERE event_id = ?",
                        (json.dumps(names, ensure_ascii=False), now_iso, ev["event_id"]),
                    )

    db.append_recording_mutation(
        tenant_id=tenant_id,
        vault_id=vault_id,
        entity_type="cluster",
        entity_id=cluster_id,
        mutation_type="cluster_updated",
        payload_json={
            "cluster_id": cluster_id,
            "speaker_id": speaker_id,
            "speaker_name": speaker_name,
        },
    )


def resolve_tzinfo(tz_name: str | None) -> tuple[dt.tzinfo, str]:
    raw = (tz_name or "").strip() or "Asia/Shanghai"
    m = re.match(r"^(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?$", raw, flags=re.IGNORECASE)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hours = int(m.group(2))
        mins = int(m.group(3) or "0")
        if hours <= 14 and mins < 60:
            offset = dt.timedelta(minutes=sign * (hours * 60 + mins))
            label = f"UTC{m.group(1)}{hours:02d}:{mins:02d}"
            return dt.timezone(offset), label
    try:
        return ZoneInfo(raw), raw
    except Exception:
        return dt.timezone.utc, "UTC"


def build_timeline_day_response(
    db: Database,
    vault_id: str,
    date_str: str | None = None,
    tz_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Assemble strictly ascending (`00:00 -> 24:00`) day timeline across P1..P4 tiers."""
    tz, tz_name = resolve_tzinfo(tz_name)

    if date_str:
        try:
            target_date = dt.date.fromisoformat(date_str)
        except ValueError:
            target_date = dt.datetime.now(tz).date()
    else:
        target_date = dt.datetime.now(tz).date()

    local_midnight = dt.datetime(
        target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz
    )
    local_next_midnight = local_midnight + dt.timedelta(days=1)
    day_start_ms = int(local_midnight.timestamp() * 1000)
    day_end_ms = int(local_next_midnight.timestamp() * 1000)
    day_start_iso = epoch_ms_to_iso(day_start_ms)
    day_end_iso = epoch_ms_to_iso(day_end_ms)

    profiles = {
        r["speaker_id"]: dict(r)
        for r in db.all(
            "SELECT speaker_id, name, suspected_name FROM speaker_profiles WHERE vault_id = ?",
            (vault_id,),
        )
    }
    pending_cluster_set = {
        r["cluster_id"]
        for r in db.all(
            "SELECT cluster_id FROM voiceprint_clusters WHERE vault_id = ? AND status = 'pending'",
            (vault_id,),
        )
    }

    def clamp_minutes(s_ms: int, e_ms: int) -> tuple[int, int]:
        s_min = max(0, min(1439, int((s_ms - day_start_ms) // 60_000)))
        raw_e_min = int((max(s_ms + 1000, e_ms) - day_start_ms + 59_999) // 60_000)
        e_min = max(s_min + 1, min(1440, raw_e_min))
        return s_min, e_min

    # 1. P1 Active Processing Chains overlapping target local day
    chain_rows = db.all(
        """
        SELECT pc.*, rs.started_at AS seg_started_at, rs.duration_ms AS seg_duration_ms
        FROM processing_chains pc
        LEFT JOIN recording_segments rs
            ON rs.segment_id = ('seg_' || SUBSTR(pc.chain_id, 5))
           AND rs.vault_id = pc.vault_id
        WHERE pc.vault_id = ?
          AND pc.status IN ('pending', 'running')
        ORDER BY COALESCE(rs.started_at, pc.analyzing_start_at) ASC
        """,
        (vault_id,),
    )
    chains_out: list[dict[str, Any]] = []
    for r in chain_rows:
        anchor_iso = r.get("seg_started_at") or r.get("analyzing_start_at")
        try:
            c_start_ms = int(parse_iso_utc(anchor_iso).timestamp() * 1000)
        except Exception:
            continue
        dur = max(60_000, int(r.get("seg_duration_ms") or 60_000))
        c_end_ms = c_start_ms + dur
        if c_end_ms <= day_start_ms or c_start_ms >= day_end_ms:
            continue
        s_min, e_min = clamp_minutes(c_start_ms, c_end_ms)
        chains_out.append(
            {
                "chain_id": r["chain_id"],
                "recording_id": r["recording_id"],
                "live_session_id": r.get("live_session_id"),
                "status": r["status"],
                "current_step": r["current_step"],
                "progress_pct": float(r.get("progress_pct") or 0.0),
                "start_epoch_ms": c_start_ms,
                "end_epoch_ms": c_end_ms,
                "start_min": s_min,
                "end_min": e_min,
            }
        )

    # Load all day utterances once (ordered strictly ascending)
    raw_msgs = db.all(
        """
        SELECT sm.message_id, sm.section_id, sm.speaker_id, sm.cluster_id,
               sm.speaker_name, sm.content, sm.start_ms, sm.end_ms, sm.confidence,
               s.live_session_id, s.title AS segment_ref,
               rs.started_at AS session_started_at
        FROM section_messages sm
        INNER JOIN sections s ON sm.section_id = s.section_id
        LEFT JOIN recording_sessions rs ON s.live_session_id = rs.live_session_id
        WHERE s.vault_id = ?
        ORDER BY sm.start_ms ASC
        """,
        (vault_id,),
    )
    day_msgs: list[dict[str, Any]] = []
    for m in raw_msgs:
        s_ms, e_ms = _normalize_message_epoch_ms(
            int(m["start_ms"] or 0),
            int(m["end_ms"] or 0),
            m.get("session_started_at"),
        )
        if e_ms <= day_start_ms or s_ms >= day_end_ms:
            continue
        md = dict(m)
        md["start_epoch_ms"] = s_ms
        md["end_epoch_ms"] = e_ms
        seg_ref = str(m.get("segment_ref") or "")
        md["segment_id"] = seg_ref if seg_ref.startswith("seg_") else None
        day_msgs.append(md)
    day_msgs.sort(key=lambda x: (x["start_epoch_ms"], x["end_epoch_ms"]))

    # 2. P2 Structured Memory Events overlapping target local day (ascending)
    ev_rows = db.all(
        """
        SELECT * FROM memory_events
        WHERE vault_id = ?
          AND end_time > ?
          AND start_time < ?
        ORDER BY start_time ASC
        """,
        (vault_id, day_start_iso, day_end_iso),
    )
    activities_out: list[dict[str, Any]] = []
    covered_spans: list[tuple[int, int]] = []
    for ev in ev_rows:
        try:
            ev_s_ms = int(parse_iso_utc(ev["start_time"]).timestamp() * 1000)
            ev_e_ms = int(parse_iso_utc(ev["end_time"]).timestamp() * 1000)
        except Exception:
            continue
        if ev_e_ms <= day_start_ms or ev_s_ms >= day_end_ms:
            continue
        covered_spans.append((ev_s_ms, ev_e_ms))
        s_min, e_min = clamp_minutes(ev_s_ms, ev_e_ms)

        overlapping_msgs = [
            m
            for m in day_msgs
            if m["end_epoch_ms"] >= ev_s_ms and m["start_epoch_ms"] <= ev_e_ms
        ]
        resolver = SessionSpeakerResolver(speaker_profiles=profiles)
        resolved_speakers: list[str] = []
        pending_cids: list[str] = []
        for m in overlapping_msgs:
            nm = resolver.resolve(
                speaker_id=m.get("speaker_id"),
                cluster_id=m.get("cluster_id"),
                local_label=m.get("speaker_name"),
            )
            if nm not in resolved_speakers:
                resolved_speakers.append(nm)
            cid = m.get("cluster_id")
            if cid and cid in pending_cluster_set and cid not in pending_cids:
                pending_cids.append(cid)

        if not resolved_speakers:
            try:
                stored_spks = json.loads(ev.get("speaker_names") or "[]")
                resolved_speakers = [str(s) for s in stored_spks if str(s).strip()]
            except Exception:
                resolved_speakers = []

        try:
            keywords = [str(k) for k in json.loads(ev.get("keywords") or "[]") if str(k).strip()]
        except Exception:
            keywords = []

        raw_cat = str(ev.get("category") or "").strip().lower()
        clean_cat = "" if raw_cat in {"unknown", "general", "uncategorized"} else raw_cat
        title = str(ev.get("title") or "").strip()
        summary = str(ev.get("summary") or "").strip()
        if summary == title:
            summary = ""

        activities_out.append(
            {
                "event_id": ev["event_id"],
                "session_id": ev.get("session_id"),
                "title": title,
                "summary": summary,
                "category": clean_cat,
                "start_time": ev["start_time"],
                "end_time": ev["end_time"],
                "start_epoch_ms": ev_s_ms,
                "end_epoch_ms": ev_e_ms,
                "duration_ms": max(1000, ev_e_ms - ev_s_ms),
                "start_min": s_min,
                "end_min": e_min,
                "speaker_names": resolved_speakers,
                "keywords": keywords,
                "pending_cluster_ids": pending_cids,
                "utterance_count": len(overlapping_msgs),
            }
        )
    activities_out.sort(key=lambda x: (x["start_epoch_ms"], x["end_epoch_ms"]))

    # 3. P3 Scattered Chat Groups (utterances NOT inside any P2 Activity, grouped ascending)
    unassigned_msgs: list[dict[str, Any]] = []
    for m in day_msgs:
        mid = (m["start_epoch_ms"] + m["end_epoch_ms"]) // 2
        if any(cs <= mid <= ce for cs, ce in covered_spans):
            continue
        unassigned_msgs.append(m)

    scattered_groups: list[list[dict[str, Any]]] = []
    for m in unassigned_msgs:
        if (
            scattered_groups
            and (m["start_epoch_ms"] - scattered_groups[-1][-1]["end_epoch_ms"]) <= SCATTERED_GROUP_GAP_MS
        ):
            scattered_groups[-1].append(m)
        else:
            scattered_groups.append([m])

    scattered_out: list[dict[str, Any]] = []
    for grp in scattered_groups:
        g_start = grp[0]["start_epoch_ms"]
        g_end = max(m["end_epoch_ms"] for m in grp)
        s_min, e_min = clamp_minutes(g_start, g_end)
        resolver = SessionSpeakerResolver(speaker_profiles=profiles)
        snippets: list[dict[str, Any]] = []
        pending_cids: list[str] = []
        for m in grp:
            spk_name = resolver.resolve(
                speaker_id=m.get("speaker_id"),
                cluster_id=m.get("cluster_id"),
                local_label=m.get("speaker_name"),
            )
            cid = m.get("cluster_id")
            if cid and cid in pending_cluster_set and cid not in pending_cids:
                pending_cids.append(cid)
            local_t = dt.datetime.fromtimestamp(m["start_epoch_ms"] / 1000.0, tz=tz).strftime("%H:%M")
            seg_id = m.get("segment_id")
            snippets.append(
                {
                    "message_id": m["message_id"],
                    "speaker_id": m.get("speaker_id"),
                    "cluster_id": cid,
                    "speaker": spk_name,
                    "text": m["content"],
                    "time": local_t,
                    "start_epoch_ms": m["start_epoch_ms"],
                    "end_epoch_ms": m["end_epoch_ms"],
                    "segment_id": seg_id,
                    "audio_url": f"/recordings/segments/{seg_id}/audio" if seg_id else None,
                }
            )
        scattered_out.append(
            {
                "group_id": f"scat_{grp[0]['message_id']}",
                "start_epoch_ms": g_start,
                "end_epoch_ms": g_end,
                "duration_ms": max(1000, g_end - g_start),
                "start_min": s_min,
                "end_min": e_min,
                "pending_cluster_ids": pending_cids,
                "snippets": snippets,
            }
        )

    # 4. P4 Recording Coverage Segments on target local day (merged contiguous spans)
    seg_rows = db.all(
        """
        SELECT segment_id, started_at, duration_ms
        FROM recording_segments
        WHERE vault_id = ? AND upload_status = 'uploaded'
          AND started_at >= ? AND started_at < ?
        ORDER BY started_at ASC
        """,
        (
            vault_id,
            epoch_ms_to_iso(day_start_ms - 3600_000),
            day_end_iso,
        ),
    )
    raw_cov: list[tuple[int, int]] = []
    for r in seg_rows:
        try:
            s_ms = int(parse_iso_utc(r["started_at"]).timestamp() * 1000)
        except Exception:
            continue
        dur = max(10_000, int(r.get("duration_ms") or 30_000))
        e_ms = s_ms + dur
        if e_ms <= day_start_ms or s_ms >= day_end_ms:
            continue
        s_min, e_min = clamp_minutes(s_ms, e_ms)
        raw_cov.append((s_min, e_min))

    raw_cov.sort()
    merged_cov: list[list[int]] = []
    for s_min, e_min in raw_cov:
        if merged_cov and s_min <= merged_cov[-1][1] + 2:
            merged_cov[-1][1] = max(merged_cov[-1][1], e_min)
        else:
            merged_cov.append([s_min, e_min])

    coverage_out = [
        {"start_min": a, "end_min": b}
        for a, b in merged_cov
    ]

    # 5. Multi-day density map around target_date [-21d, +7d] for DateStrip dots
    range_start_local = local_midnight - dt.timedelta(days=21)
    range_end_local = local_midnight + dt.timedelta(days=8)
    range_start_ms = int(range_start_local.timestamp() * 1000)
    range_end_ms = int(range_end_local.timestamp() * 1000)

    density: dict[str, int] = {}
    ev_density_rows = db.all(
        """
        SELECT start_time FROM memory_events
        WHERE vault_id = ? AND start_time >= ? AND start_time < ?
        """,
        (vault_id, epoch_ms_to_iso(range_start_ms), epoch_ms_to_iso(range_end_ms)),
    )
    for r in ev_density_rows:
        try:
            d_key = parse_iso_utc(r["start_time"]).astimezone(tz).strftime("%Y-%m-%d")
            density[d_key] = density.get(d_key, 0) + 1
        except Exception:
            continue

    sec_density_rows = db.all(
        """
        SELECT start_ms FROM sections
        WHERE vault_id = ? AND start_ms >= ? AND start_ms < ?
        """,
        (vault_id, range_start_ms, range_end_ms),
    )
    for r in sec_density_rows:
        try:
            d_key = dt.datetime.fromtimestamp(int(r["start_ms"]) / 1000.0, tz=tz).strftime("%Y-%m-%d")
            density[d_key] = max(density.get(d_key, 0), 1)
        except Exception:
            continue

    return {
        "date": target_date.isoformat(),
        "timezone": tz_name,
        "chains": chains_out,
        "activities": activities_out,
        "scattered_chats": scattered_out,
        "coverage": coverage_out,
        "density": density,
        "server_time": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def get_memory_event_detail(
    db: Database,
    vault_id: str,
    event_id: str,
    tz_name: str = "Asia/Shanghai",
) -> dict[str, Any] | None:
    """Fetch full structured detail, speaker-resolved utterances & audio URLs for a P2 Activity."""
    ev = db.one(
        "SELECT * FROM memory_events WHERE event_id = ? AND vault_id = ?",
        (event_id, vault_id),
    )
    if not ev:
        return None

    tz, _ = resolve_tzinfo(tz_name)

    ev_s_ms = int(parse_iso_utc(ev["start_time"]).timestamp() * 1000)
    ev_e_ms = int(parse_iso_utc(ev["end_time"]).timestamp() * 1000)

    profiles = {
        r["speaker_id"]: dict(r)
        for r in db.all(
            "SELECT speaker_id, name, suspected_name FROM speaker_profiles WHERE vault_id = ?",
            (vault_id,),
        )
    }
    pending_cluster_set = {
        r["cluster_id"]
        for r in db.all(
            "SELECT cluster_id FROM voiceprint_clusters WHERE vault_id = ? AND status = 'pending'",
            (vault_id,),
        )
    }
    resolver = SessionSpeakerResolver(speaker_profiles=profiles)

    msg_rows = db.all(
        """
        SELECT sm.message_id, sm.section_id, sm.speaker_id, sm.cluster_id,
               sm.speaker_name, sm.content, sm.start_ms, sm.end_ms, sm.confidence,
               s.title AS segment_ref, rs.started_at AS session_started_at
        FROM section_messages sm
        INNER JOIN sections s ON sm.section_id = s.section_id
        LEFT JOIN recording_sessions rs ON s.live_session_id = rs.live_session_id
        WHERE s.vault_id = ?
        ORDER BY sm.start_ms ASC
        """,
        (vault_id,),
    )

    utterances: list[dict[str, Any]] = []
    segment_ids: list[str] = []
    speakers_list: list[str] = []
    for m in msg_rows:
        s_ms, e_ms = _normalize_message_epoch_ms(
            int(m["start_ms"] or 0),
            int(m["end_ms"] or 0),
            m.get("session_started_at"),
        )
        if e_ms < ev_s_ms or s_ms > ev_e_ms:
            continue
        spk_name = resolver.resolve(
            speaker_id=m.get("speaker_id"),
            cluster_id=m.get("cluster_id"),
            local_label=m.get("speaker_name"),
        )
        if spk_name not in speakers_list:
            speakers_list.append(spk_name)
        seg_ref = str(m.get("segment_ref") or "")
        seg_id = seg_ref if seg_ref.startswith("seg_") else None
        if seg_id and seg_id not in segment_ids:
            segment_ids.append(seg_id)
        cid = m.get("cluster_id")
        utterances.append(
            {
                "message_id": m["message_id"],
                "speaker_id": m.get("speaker_id"),
                "cluster_id": cid,
                "is_cluster_pending": bool(cid and cid in pending_cluster_set),
                "speaker_name": spk_name,
                "content": m["content"],
                "start_epoch_ms": s_ms,
                "end_epoch_ms": e_ms,
                "time": dt.datetime.fromtimestamp(s_ms / 1000.0, tz=tz).strftime("%H:%M:%S"),
                "segment_id": seg_id,
                "audio_url": f"/recordings/segments/{seg_id}/audio" if seg_id else None,
            }
        )

    if not speakers_list:
        try:
            speakers_list = [str(x) for x in json.loads(ev.get("speaker_names") or "[]") if str(x).strip()]
        except Exception:
            speakers_list = []

    try:
        keywords = [str(k) for k in json.loads(ev.get("keywords") or "[]") if str(k).strip()]
    except Exception:
        keywords = []

    segments_out = []
    for sid in segment_ids:
        sr = db.one(
            "SELECT segment_id, started_at, duration_ms FROM recording_segments WHERE segment_id = ? AND vault_id = ?",
            (sid, vault_id),
        )
        if sr:
            segments_out.append(
                {
                    "segment_id": sr["segment_id"],
                    "started_at": sr["started_at"],
                    "duration_ms": int(sr.get("duration_ms") or 0),
                    "audio_url": f"/recordings/segments/{sr['segment_id']}/audio",
                }
            )

    raw_cat = str(ev.get("category") or "").strip().lower()
    clean_cat = "" if raw_cat in {"unknown", "general", "uncategorized"} else raw_cat
    title = str(ev.get("title") or "").strip()
    summary = str(ev.get("summary") or "").strip()
    if summary == title:
        summary = ""

    return {
        "event_id": ev["event_id"],
        "session_id": ev.get("session_id"),
        "title": title,
        "summary": summary,
        "category": clean_cat,
        "start_time": ev["start_time"],
        "end_time": ev["end_time"],
        "start_epoch_ms": ev_s_ms,
        "end_epoch_ms": ev_e_ms,
        "duration_ms": max(1000, ev_e_ms - ev_s_ms),
        "speaker_names": speakers_list,
        "keywords": keywords,
        "utterances": utterances,
        "segments": segments_out,
    }
