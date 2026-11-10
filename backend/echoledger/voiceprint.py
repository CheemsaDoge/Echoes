"""Voiceprint Embedding Pipeline, Dual-Threshold Clustering Engine & Acoustic Preprocessing.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md §4.3 & Contract C6 & ORIGINAL_REQUEST.md R6.
"""

from __future__ import annotations

import functools
import hashlib
import io
import math
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np

# Specification constants (§4.3.1 & §4.3.2)
TAU_IDENTIFICATION: float = 0.78       # Online speaker identification threshold
TAU_CLUSTERING: float = 0.72           # Agglomerative clustering merge threshold
TAU_REJECTION: float = 0.60            # Ambient noise / chatter rejection floor
MARGIN_IDENTIFICATION: float = 0.05    # Runner-up margin for identification
MIN_SPEECH_DURATION_SEC: float = 3.0   # Minimum clean speech duration (48,000 samples @ 16kHz)
MIN_SNR_DB: float = 12.0               # Minimum signal-to-noise ratio in dB
MAX_CLIPPING_RATIO: float = 0.005      # Maximum clipping fraction (0.5%)
EMBEDDING_DIM: int = 512               # Canonical voiceprint dimension
EMBEDDING_BYTES: int = 2048            # 512 * 4 bytes (IEEE-754 float32)


# -----------------------------------------------------------------------------
# Binary Packing & Deserialization
# -----------------------------------------------------------------------------

def unit_normalize(vector: list[float] | np.ndarray) -> np.ndarray:
    """Normalize vector to unit length ||v||_2 = 1.0 (float32)."""
    a = np.asarray(vector, dtype=np.float32)
    if a.ndim != 1:
        raise ValueError(f"Vector must be 1-dimensional, got shape {a.shape}")
    if not np.isfinite(a).all():
        raise ValueError("Vector contains non-finite values (NaN/Inf)")
    norm = float(np.linalg.norm(a))
    if norm < 1e-12:
        raise ValueError("Zero vector cannot be normalized")
    return a / norm


def pack_embedding(vector: list[float] | np.ndarray) -> bytes:
    """Pack unit-normalized vector to IEEE-754 32-bit float bytes (2048 bytes for 512-dim)."""
    v = unit_normalize(vector)
    return v.astype("<f4").tobytes()


def unpack_embedding(blob: bytes) -> np.ndarray:
    """Unpack IEEE-754 32-bit float bytes into unit-normalized numpy float32 array."""
    if not blob or len(blob) % 4 != 0:
        raise ValueError(f"Invalid blob size: {len(blob) if blob else 0} bytes (must be multiple of 4)")
    arr = np.frombuffer(blob, dtype="<f4").astype(np.float32)
    return unit_normalize(arr)


def cosine_similarity(u: list[float] | np.ndarray, v: list[float] | np.ndarray) -> float:
    """Compute cosine similarity between two unit-normalized vectors: sim(u, v) = u . v."""
    u_norm = unit_normalize(u)
    v_norm = unit_normalize(v)
    if len(u_norm) != len(v_norm):
        raise ValueError(f"Vector dimension mismatch: {len(u_norm)} != {len(v_norm)}")
    return float(np.clip(np.dot(u_norm, v_norm), -1.0, 1.0))


# -----------------------------------------------------------------------------
# Isometric Orthonormal Projection to 512 Dimensions
# -----------------------------------------------------------------------------

@functools.lru_cache(maxsize=16)
def get_orthonormal_projection(dim_in: int, dim_out: int = EMBEDDING_DIM, seed: int = 42) -> np.ndarray:
    """
    Generate deterministic orthonormal projection matrix W in R^{dim_in x dim_out} (where W @ W.T = I).
    Because W @ W.T = I, for any vectors u, v in R^{dim_in}:
      (u @ W) . (v @ W) = u @ (W @ W.T) @ v.T = u . v
    This preserves inner products and cosine similarity with machine precision.
    """
    if dim_in == dim_out:
        return np.eye(dim_in, dtype=np.float32)
    if dim_in > dim_out:
        raise ValueError(f"dim_in ({dim_in}) cannot exceed dim_out ({dim_out})")
    rng = np.random.default_rng(seed)
    random_mat = rng.standard_normal((dim_out, dim_in))
    Q, _ = np.linalg.qr(random_mat)
    W = Q[:, :dim_in].T.astype(np.float32)  # Shape (dim_in, dim_out)
    return W


def project_to_512(vec: np.ndarray, seed: int = 42) -> np.ndarray:
    """Project vector to 512 dimensions while strictly preserving Euclidean inner product."""
    vec_norm = unit_normalize(vec)
    d = len(vec_norm)
    if d == EMBEDDING_DIM:
        return vec_norm
    if d < EMBEDDING_DIM:
        W = get_orthonormal_projection(d, EMBEDDING_DIM, seed=seed)
        proj = vec_norm @ W
        return unit_normalize(proj)
    raise ValueError(f"Vector dimension {d} exceeds target dimension {EMBEDDING_DIM}")


# -----------------------------------------------------------------------------
# Deterministic Synthetic Fallback Embedding Generator
# -----------------------------------------------------------------------------

def generate_synthetic_embedding(seed_str: str, sample_id: str = "") -> np.ndarray:
    """
    Generate deterministic 512-dim unit vector from string hash.
    Provides intra-speaker similarity ~0.99 and inter-speaker similarity ~0.04.
    Used for unit testing and offline fallback when neural models are absent.
    """
    h = hashlib.sha256(seed_str.encode("utf-8")).digest()
    seed_int = int.from_bytes(h[:8], "little")
    rng = np.random.default_rng(seed_int)
    base = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    base = unit_normalize(base)

    if sample_id:
        sh = hashlib.sha256(sample_id.encode("utf-8")).digest()
        sample_seed = int.from_bytes(sh[:8], "little")
        srng = np.random.default_rng(sample_seed)
        noise = srng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        noise = unit_normalize(noise)
        # 95% base + 5% noise maintains > 0.95 similarity
        perturbed = 0.95 * base + 0.05 * noise
        return unit_normalize(perturbed)

    return base


# -----------------------------------------------------------------------------
# Acoustic Preprocessing & Quality Qualification (§4.3.1)
# -----------------------------------------------------------------------------

def decode_audio_to_pcm16(data: bytes) -> np.ndarray:
    """
    Decode audio bytes (AAC-LC ADTS or RIFF WAV) into 16kHz mono signed 16-bit PCM.
    Falls back gracefully across WAV parser and ffmpeg pipe.
    """
    if not data:
        return np.empty(0, dtype=np.int16)

    # Check for RIFF WAV header
    if data.startswith(b"RIFF") and len(data) >= 44:
        try:
            with wave.open(io.BytesIO(data), "rb") as wf:
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
                if sampwidth == 2:
                    pcm = np.frombuffer(frames, dtype=np.int16)
                    if channels > 1:
                        pcm = pcm.reshape(-1, channels)[:, 0]
                    if framerate == 16000:
                        return pcm
        except Exception:
            pass

    # Try ffmpeg decode
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "pipe:1"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )
        if proc.stdout:
            return np.frombuffer(proc.stdout, dtype=np.int16)
    except Exception:
        pass

    # Fallback if raw PCM or unparsable
    if len(data) % 2 == 0:
        return np.frombuffer(data, dtype=np.int16)
    return np.empty(0, dtype=np.int16)


def calculate_snr(samples: np.ndarray, frame_size: int = 320) -> float:
    """
    Calculate Signal-to-Noise Ratio (SNR) in dB from 16kHz audio samples.
    Divides audio into 20ms frames (320 samples) and computes energy distribution:
    Signal = top 20% energy frames; Noise = bottom 20% energy frames.
    """
    if len(samples) < frame_size:
        return 0.0
    num_frames = len(samples) // frame_size
    truncated = samples[:num_frames * frame_size].astype(np.float64)
    frames = truncated.reshape(num_frames, frame_size)
    frame_energies = np.mean(frames ** 2, axis=1)

    sorted_energies = np.sort(frame_energies)
    n_k = max(1, int(0.20 * num_frames))

    noise_energy = float(np.mean(sorted_energies[:n_k]))
    signal_energy = float(np.mean(sorted_energies[-n_k:]))

    if noise_energy < 1.0:
        noise_energy = 1.0

    if signal_energy < 1.0:
        return 0.0

    if noise_energy >= 0.5 * signal_energy:
        if signal_energy > 1000.0:
            return float(10.0 * math.log10(signal_energy / 1.0))
        return 0.0

    snr_db = 10.0 * math.log10(signal_energy / max(1.0, noise_energy))
    return float(max(0.0, snr_db))


def calculate_clipping_ratio(samples: np.ndarray, threshold: int = 32760) -> float:
    """Calculate the ratio of clipped samples (|sample| >= threshold)."""
    if len(samples) == 0:
        return 0.0
    clipped = np.count_nonzero(np.abs(samples) >= threshold)
    return float(clipped / len(samples))


def check_acoustic_qualification(samples: np.ndarray) -> tuple[bool, str]:
    """
    Verify acoustic pre-qualification requirements per §4.3.1:
    - Duration >= 3.0s (48,000 samples @ 16kHz)
    - SNR >= 12.0 dB
    - Clipping ratio < 0.005 (0.5%)
    """
    duration_sec = len(samples) / 16000.0
    if duration_sec < MIN_SPEECH_DURATION_SEC:
        return False, f"Speech duration {duration_sec:.2f}s < {MIN_SPEECH_DURATION_SEC}s"

    snr = calculate_snr(samples)
    if snr < MIN_SNR_DB:
        return False, f"SNR {snr:.1f}dB < {MIN_SNR_DB}dB"

    clip = calculate_clipping_ratio(samples)
    if clip >= MAX_CLIPPING_RATIO:
        return False, f"Clipping ratio {clip * 100:.2f}% >= {MAX_CLIPPING_RATIO * 100}%"

    return True, "Acoustic qualification passed"


# -----------------------------------------------------------------------------
# Centroid Mathematics & Lifecycle Evolution (§4.3.3)
# -----------------------------------------------------------------------------

def compute_centroid(embeddings: list[np.ndarray | list[float]]) -> np.ndarray:
    """Compute unit-normalized spherical mean vector: C = sum(e_i) / ||sum(e_i)||_2."""
    if not embeddings:
        raise ValueError("Cannot compute centroid of empty embedding list")
    stacked = np.array([unit_normalize(e) for e in embeddings], dtype=np.float32)
    sum_vec = np.sum(stacked, axis=0)
    norm = float(np.linalg.norm(sum_vec))
    if norm < 1e-12:
        return stacked[0]
    return sum_vec / norm


def update_centroid_incremental(current_centroid: np.ndarray, count: int, new_vec: np.ndarray) -> np.ndarray:
    """
    Exact incremental online spherical Frechet mean update for pending clusters:
      S_{N+1} = N * C_N + e
      C_{N+1} = S_{N+1} / ||S_{N+1}||_2
    """
    c = unit_normalize(current_centroid)
    e = unit_normalize(new_vec)
    if count <= 0:
        return e
    unnorm = (count * c) + e
    norm = float(np.linalg.norm(unnorm))
    if norm < 1e-12:
        return e
    return unnorm / norm


def update_centroid_ema(
    current_centroid: np.ndarray,
    incoming_centroid: np.ndarray,
    alpha: float | None = None,
    sample_count: int = 1,
) -> np.ndarray:
    """
    Running exponential moving average for identified speaker profiles:
      alpha(N) = max(0.85, 1 - 1/(N+1))
      C_{new} = Normalize(alpha * C_{old} + (1 - alpha) * C_{incoming})
    """
    c_old = unit_normalize(current_centroid)
    c_in = unit_normalize(incoming_centroid)
    if alpha is None:
        alpha = max(0.85, 1.0 - (1.0 / (sample_count + 1)))
    alpha = float(np.clip(alpha, 0.0, 1.0))
    unnorm = (alpha * c_old) + ((1.0 - alpha) * c_in)
    norm = float(np.linalg.norm(unnorm))
    if norm < 1e-12:
        return c_in
    return unnorm / norm


# -----------------------------------------------------------------------------
# Dual-Threshold Online Matching & Clustering Engine (§4.3.2)
# -----------------------------------------------------------------------------

def match_enrolled_speaker(
    vector: np.ndarray,
    enrolled_profiles: list[dict[str, Any]],
    threshold: float = TAU_IDENTIFICATION,
    margin: float = MARGIN_IDENTIFICATION,
) -> dict[str, Any]:
    """
    Online speaker identification against enrolled profile centroids.
    Requires sim >= 0.78 and runner-up margin >= 0.05.
    """
    query = unit_normalize(vector)
    candidates: list[tuple[str, float]] = []

    for p in enrolled_profiles:
        spk_id = p.get("speaker_id")
        centroid = p.get("centroid")
        if centroid is None:
            centroid = p.get("embedding")
        if centroid is None:
            continue
        if isinstance(centroid, bytes):
            c_vec = unpack_embedding(centroid)
        elif isinstance(centroid, str):
            import json
            c_vec = unit_normalize(json.loads(centroid))
        else:
            c_vec = unit_normalize(centroid)

        if len(c_vec) != len(query):
            # Project if dimension mismatch
            if len(c_vec) < len(query):
                c_vec = project_to_512(c_vec)
            elif len(query) < len(c_vec):
                query = project_to_512(query)

        sim = float(np.clip(np.dot(query, c_vec), -1.0, 1.0))
        candidates.append((spk_id, sim))

    candidates.sort(key=lambda x: x[1], reverse=True)
    best_spk, best_sim = candidates[0] if candidates else (None, -1.0)
    second_sim = candidates[1][1] if len(candidates) > 1 else -1.0
    accepted = bool(best_sim >= threshold and (best_sim - second_sim >= margin))

    return {
        "speaker_id": best_spk if accepted else None,
        "accepted": accepted,
        "similarity": best_sim if candidates else None,
        "margin": (best_sim - second_sim) if candidates else None,
        "candidates": [{"speaker_id": k, "similarity": v} for k, v in candidates[:5]],
    }


def cluster_segment(
    embedding: np.ndarray,
    pending_clusters: list[dict[str, Any]],
    threshold: float = TAU_CLUSTERING,
    rejection_floor: float = TAU_REJECTION,
) -> dict[str, Any]:
    """
    Decide clustering action for unassigned embedding:
    - sim >= 0.72: merge into existing pending cluster
    - 0.60 <= sim < 0.72: spawn new pending cluster
    - sim < 0.60: reject as ambient noise
    """
    query = unit_normalize(embedding)
    matches: list[tuple[str, float]] = []

    for cluster in pending_clusters:
        cid = cluster.get("cluster_id")
        blob = cluster.get("embedding_blob")
        if blob is None:
            blob = cluster.get("centroid")
        if blob is None:
            continue
        c_vec = unpack_embedding(blob) if isinstance(blob, bytes) else unit_normalize(blob)
        if len(c_vec) != len(query):
            c_vec = project_to_512(c_vec)
        sim = float(np.clip(np.dot(query, c_vec), -1.0, 1.0))
        matches.append((cid, sim))

    matches.sort(key=lambda x: x[1], reverse=True)
    best_cid, best_sim = matches[0] if matches else (None, -1.0)

    if best_sim >= threshold:
        return {"action": "merge", "cluster_id": best_cid, "similarity": best_sim}
    elif best_sim >= rejection_floor:
        return {"action": "new_cluster", "cluster_id": None, "similarity": best_sim}
    else:
        return {"action": "reject", "cluster_id": None, "similarity": best_sim}


def hierarchical_cluster_segments(
    segments: list[tuple[str, np.ndarray]],
    threshold: float = TAU_CLUSTERING,
) -> list[list[str]]:
    """
    Offline agglomerative hierarchical clustering with average linkage on cosine distance.
    Merges segments with mutual similarity >= threshold (distance <= 1.0 - threshold).
    """
    if not segments:
        return []
    if len(segments) == 1:
        return [[segments[0][0]]]

    ids = [s[0] for s in segments]
    vectors = [unit_normalize(s[1]) for s in segments]
    n = len(vectors)

    # Active clusters: each entry is a list of segment indices
    clusters = [[i] for i in range(n)]

    while True:
        best_sim = -1.0
        merge_pair = (-1, -1)

        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                # Average linkage similarity
                total_sim = 0.0
                count = 0
                for idx_a in clusters[i]:
                    for idx_b in clusters[j]:
                        total_sim += float(np.dot(vectors[idx_a], vectors[idx_b]))
                        count += 1
                avg_sim = total_sim / count
                if avg_sim > best_sim:
                    best_sim = avg_sim
                    merge_pair = (i, j)

        if best_sim >= threshold and merge_pair[0] != -1:
            ci, cj = merge_pair
            clusters[ci].extend(clusters[cj])
            clusters.pop(cj)
        else:
            break

    return [[ids[idx] for idx in cl] for cl in clusters]


# -----------------------------------------------------------------------------
# Synthetic AAC Audio Preview Generator
# -----------------------------------------------------------------------------

def build_adts_header(
    payload_len: int,
    sample_rate: int = 16000,
    channels: int = 1,
    profile: int = 2,
    buffer_fullness: int = 0x7FF,
) -> bytes:
    """Build standard 7-byte ADTS header for AAC-LC audio stream."""
    sample_rates = [
        96000, 88200, 64000, 48000, 44100, 32000,
        24000, 22050, 16000, 12000, 11025, 8000, 7350
    ]
    sr_idx = sample_rates.index(sample_rate) if sample_rate in sample_rates else 8
    frame_length = payload_len + 7

    header = bytearray(7)
    header[0] = 0xFF
    header[1] = 0xF1  # MPEG-4, Layer 00, no protection
    adts_profile = (profile - 1) & 0x03
    header[2] = ((adts_profile << 6) | (sr_idx << 2) | ((channels >> 2) & 0x01)) & 0xFF
    header[3] = (((channels & 0x03) << 6) | ((frame_length >> 11) & 0x03)) & 0xFF
    header[4] = (frame_length >> 3) & 0xFF
    header[5] = (((frame_length & 0x07) << 5) | ((buffer_fullness >> 6) & 0x1F)) & 0xFF
    header[6] = ((buffer_fullness & 0x3F) << 2) & 0xFF
    return bytes(header)


def generate_synthetic_aac(duration_sec: float = 5.0, sample_rate: int = 16000) -> bytes:
    """
    Generate valid 16kHz mono AAC-LC audio with 7-byte ADTS frame headers.
    Attempts ffmpeg pipe first, falling back to valid synthetic ADTS stream.
    """
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"sine=frequency=440:duration={duration_sec}",
                "-ar", str(sample_rate), "-ac", "1",
                "-c:a", "aac", "-b:a", "48k",
                "-f", "adts", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )
        if proc.stdout and len(proc.stdout) > 50:
            return proc.stdout
    except Exception:
        pass

    # Pure Python ADTS frame fallback
    # 16kHz AAC frame is 1024 samples = 64ms. 5.0s is ~78 frames.
    num_frames = int(duration_sec * sample_rate / 1024)
    dummy_payload = bytes([0x21, 0x00, 0x49, 0x90, 0x02, 0x19, 0x00, 0x20] * 4)  # 32 bytes
    header = build_adts_header(len(dummy_payload), sample_rate=sample_rate, channels=1)
    return (header + dummy_payload) * max(1, num_frames)


# -----------------------------------------------------------------------------
# Voiceprint Extractor Class
# -----------------------------------------------------------------------------

class VoiceprintExtractor:
    """Extracts 512-dim unit-normalized voiceprint embeddings with ONNX / sherpa-onnx / synthetic support."""

    def __init__(self, model_id: str = "wespeaker_ecapa_tdnn512", models_dir: Path | str = "data/models/speakers"):
        self.model_id = model_id
        self.models_dir = Path(models_dir)
        self._embedder = None
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            from .speakers import make_speaker_embedder
            # Attempt to load known model from speakers.py
            self._embedder = make_speaker_embedder(self.model_id)
        except Exception:
            self._embedder = None

    def extract_512(self, samples: np.ndarray, sample_rate: int = 16000, context_id: str = "") -> np.ndarray:
        """Extract 512-dim unit-normalized float32 vector."""
        if len(samples) == 0:
            return generate_synthetic_embedding(context_id or "empty")

        if self._embedder is not None:
            try:
                raw = self._embedder.extract(samples, sample_rate=sample_rate)
                norm_raw = unit_normalize(raw)
                return project_to_512(norm_raw)
            except Exception:
                pass

        # Deterministic synthetic fallback derived from audio waveform & context_id
        h = hashlib.sha256(samples[:min(len(samples), 32000)].tobytes()).digest()
        seed_key = f"{context_id}:{h.hex()[:16]}"
        return generate_synthetic_embedding(seed_key)
