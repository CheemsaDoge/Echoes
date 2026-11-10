from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time
from typing import Any, Protocol
import numpy as np
from .voiceprint import (
    project_to_512,
    generate_synthetic_embedding,
    pack_embedding,
    unpack_embedding,
    cosine_similarity,
    compute_centroid,
    update_centroid_incremental,
    update_centroid_ema,
)

def normalize(vector: list[float]) -> np.ndarray:
    a = np.asarray(vector, dtype=np.float64)
    if a.ndim != 1 or not 8 <= len(a) <= 4096 or not np.isfinite(a).all():
        raise ValueError("Embedding must contain 8..4096 finite values")
    norm = float(np.linalg.norm(a))
    if norm < 1e-12:
        raise ValueError("Zero embedding is invalid")
    return a / norm

def match_speaker(vector: list[float], model_id: str, enrolled: list[dict], threshold: float = 0.8, margin: float = 0.1) -> dict:
    query = normalize(vector)
    # Aggregate by person so two samples of one person do not become rival candidates.
    by_person: dict[str, float] = {}
    for entry in enrolled:
        if entry["model_id"] != model_id:
            continue
        v = normalize(json.loads(entry["embedding"]) if isinstance(entry["embedding"], str) else entry["embedding"])
        if len(v) != len(query):
            continue
        similarity = float(np.clip(query @ v, -1.0, 1.0))
        person = entry["speaker_id"]
        by_person[person] = max(by_person.get(person, -1.0), similarity)
    ranked = sorted(by_person.items(), key=lambda x: x[1], reverse=True)
    best = ranked[0] if ranked else (None, -1.0)
    second = ranked[1][1] if len(ranked) > 1 else -1.0
    accepted = best[1] >= threshold and best[1] - second >= margin
    return {
        "speaker_id": best[0] if accepted else None,
        "accepted": accepted,
        "similarity": best[1] if ranked else None,
        "margin": best[1] - second if ranked else None,
        "candidates": [{"speaker_id": k, "similarity": v} for k, v in ranked[:5]],
        "warning": "Thresholds require calibration for this embedding model and recording environment.",
    }

class SpeakerEmbedder:
    """Wrapper around real 16kHz Speaker Embedding ONNX models via sherpa-onnx."""
    def __init__(self, model_path: str, model_id: str):
        import sherpa_onnx
        p = Path(model_path)
        if not p.is_file():
            raise RuntimeError(f"Speaker embedding model missing: {model_path}")
        self.model_path = str(p)
        self.model_id = model_id
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=self.model_path,
            num_threads=1,
            provider="cpu"
        )
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.dim = self.extractor.dim

    def extract(self, samples: np.ndarray, sample_rate: int = 16000) -> list[float]:
        if len(samples) == 0:
            return [0.0] * self.dim
        if samples.dtype == np.int16:
            audio = samples.astype(np.float32) / 32768.0
        else:
            audio = samples.astype(np.float32)
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate, audio)
        stream.input_finished()
        return self.extractor.compute(stream)

class ProjectedEmbedder:
    """Wraps an underlying embedder and projects outputs to target dimensions (e.g. 512)."""
    def __init__(self, base_embedder: Any, target_dim: int = 512, model_id: str = "wespeaker_ecapa_tdnn512"):
        self.base_embedder = base_embedder
        self.dim = target_dim
        self.model_id = model_id

    def extract(self, samples: np.ndarray, sample_rate: int = 16000) -> list[float]:
        raw = self.base_embedder.extract(samples, sample_rate=sample_rate)
        if not raw or all(x == 0.0 for x in raw):
            return [0.0] * self.dim
        proj = project_to_512(np.array(raw, dtype=np.float32))
        return proj.tolist()

class SyntheticEmbedder:
    """Deterministic synthetic embedder when ONNX runtime or model weights are unavailable."""
    def __init__(self, model_id: str = "wespeaker_ecapa_tdnn512", dim: int = 512):
        self.model_id = model_id
        self.dim = dim

    def extract(self, samples: np.ndarray, sample_rate: int = 16000) -> list[float]:
        if len(samples) == 0:
            return [0.0] * self.dim
        h = hashlib.sha256(samples[:min(len(samples), 32000)].tobytes()).hexdigest()[:16]
        return generate_synthetic_embedding(h).tolist()

DEFAULT_SPEAKER_MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "models" / "speakers"

SPEAKER_MODEL_FILES = {
    "campplus_cn": "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
    "eres2net_cn": "3dspeaker_speech_eres2net_sv_zh-cn_16k-common.onnx",
    "wespeaker_resnet34": "wespeaker_zh_cnceleb_resnet34.onnx",
    "nemo_speakernet": "nemo_en_speakerverification_speakernet.onnx",
}

def _resolve_models_dir(models_dir: Path | None = None) -> Path:
    if models_dir is not None:
        return Path(models_dir)
    rel = Path("data/models/speakers")
    if rel.is_dir():
        return rel
    return DEFAULT_SPEAKER_MODELS_DIR

def list_available_speaker_models(models_dir: Path | None = None) -> list[dict]:
    eff_dir = _resolve_models_dir(models_dir)
    res = []
    meta_info = {
        "campplus_cn": ("Alibaba 3D-Speaker Cam++ (Chinese)", 192, "State-of-the-art fast Cam++ architecture"),
        "eres2net_cn": ("Alibaba 3D-Speaker ERes2Net (Chinese)", 192, "High-capacity ERes2Net architecture"),
        "wespeaker_resnet34": ("WeSpeaker CNCeleb ResNet34", 256, "ResNet34 trained on CNCeleb"),
        "nemo_speakernet": ("NeMo SpeakerNet (Multilingual)", 256, "Lightweight SpeakerNet from NVIDIA NeMo"),
    }
    for model_id, filename in SPEAKER_MODEL_FILES.items():
        file_path = eff_dir / filename
        name, dim, desc = meta_info.get(model_id, (model_id, 0, ""))
        res.append({
            "id": model_id,
            "name": name,
            "dim": dim,
            "file": filename,
            "model_path": str(file_path),
            "ready": file_path.is_file(),
            "description": desc,
        })
    return res

def make_speaker_embedder(name: str, model_path: str = "") -> Any:
    search_dirs = [Path("data/models/speakers"), DEFAULT_SPEAKER_MODELS_DIR]
    if name == "wespeaker_ecapa_tdnn512":
        explicit_onnx = model_path or os.getenv("ECHOLEDGER_VOICEPRINT_ONNX_PATH", "").strip()
        if explicit_onnx:
            exp_path = Path(os.path.expanduser(explicit_onnx))
            if exp_path.is_file():
                try:
                    base = SpeakerEmbedder(str(exp_path), model_id=name)
                    if base.dim == 512:
                        return base
                    return ProjectedEmbedder(base, target_dim=512, model_id=name)
                except Exception:
                    pass
        for sdir in search_dirs:
            target_file = sdir / "wespeaker_ecapa_tdnn512.onnx"
            if target_file.is_file():
                return SpeakerEmbedder(str(target_file), model_id=name)
        # Check if any base model is available to project to 512-dim
        for sdir in search_dirs:
            for candidate in ["wespeaker_resnet34", "campplus_cn", "eres2net_cn", "nemo_speakernet"]:
                cand_file = sdir / SPEAKER_MODEL_FILES[candidate]
                if cand_file.is_file():
                    try:
                        base = SpeakerEmbedder(str(cand_file), model_id=candidate)
                        return ProjectedEmbedder(base, target_dim=512, model_id=name)
                    except Exception:
                        pass
        return SyntheticEmbedder(model_id=name, dim=512)

    if name not in SPEAKER_MODEL_FILES:
        raise ValueError(f"Unknown speaker model: {name}. Supported: {list(SPEAKER_MODEL_FILES.keys())}")
    target_path = model_path
    if not target_path:
        for sdir in search_dirs:
            default_file = sdir / SPEAKER_MODEL_FILES[name]
            if default_file.is_file():
                target_path = str(default_file)
                break
        if not target_path:
            raise RuntimeError(f"Speaker model missing: {Path('data/models/speakers') / SPEAKER_MODEL_FILES[name]}")
    return SpeakerEmbedder(target_path, model_id=name)

def spot_check_speaker_models(
    labeled_chunks: list[dict],
    models: list[str] | None = None,
    rounds: int = 30,
    seed: int = 42,
) -> dict:
    """Run stratified random spot-check (Monte Carlo cross-validation) on speaker recognition.
    
    Each chunk in labeled_chunks must have:
      - 'speaker': str (person name, or '未知'/'路人'/'unknown'/'' for impostor/unassigned)
      - 'samples': np.ndarray (16kHz audio)
    """
    if models is None:
        models = [m["id"] for m in list_available_speaker_models() if m["ready"]]

    if not models:
        return {"error": "No speaker embedding models available"}

    # Filter chunks with non-trivial audio (> 0.2s)
    valid_chunks = [c for c in labeled_chunks if len(c.get("samples", [])) >= 3200]
    if len(valid_chunks) < 3:
        return {"error": "Need at least 3 labeled chunks with audio for speaker evaluation"}

    # Separate known speakers and unknown/impostors
    is_unknown = lambda spk: (not spk) or spk in ("未知", "路人", "未知路人", "unknown", "none", "未标记")
    known_speakers = set(c["speaker"] for c in valid_chunks if not is_unknown(c.get("speaker")))
    
    if len(known_speakers) < 1:
        return {"error": "Need at least 1 known enrolled speaker with labeled chunks"}

    rng = random.Random(seed)
    model_evaluations = []

    for model_name in models:
        try:
            embedder = make_speaker_embedder(model_name)
        except Exception as e:
            continue

        # 1. Precompute embeddings & measure latency
        embeddings = {}
        total_ext_time = 0.0
        total_audio_sec = 0.0

        for chunk in valid_chunks:
            samples = chunk["samples"]
            t0 = time.perf_counter()
            vec = embedder.extract(samples)
            total_ext_time += (time.perf_counter() - t0)
            total_audio_sec += len(samples) / 16000.0
            norm_vec = normalize(vec)
            embeddings[chunk["id"]] = norm_vec

        avg_latency_ms = (total_ext_time * 1000.0) / len(valid_chunks)
        rtf = total_ext_time / max(1e-6, total_audio_sec)

        # 2. Compute similarity distribution (Intra-speaker vs Inter-speaker)
        intra_sims, inter_sims = [], []
        for i in range(len(valid_chunks)):
            for j in range(i + 1, len(valid_chunks)):
                c1, c2 = valid_chunks[i], valid_chunks[j]
                if is_unknown(c1.get("speaker")) or is_unknown(c2.get("speaker")):
                    continue
                v1, v2 = embeddings[c1["id"]], embeddings[c2["id"]]
                sim = float(v1 @ v2)
                if c1["speaker"] == c2["speaker"]:
                    intra_sims.append(sim)
                else:
                    inter_sims.append(sim)

        mean_intra = float(np.mean(intra_sims)) if intra_sims else 0.8
        mean_inter = float(np.mean(inter_sims)) if inter_sims else 0.3
        discrim_gap = mean_intra - mean_inter

        # 3. Parameter sweep & Monte Carlo spot check rounds
        best_cfg = None
        best_acc = -1.0

        for th in [0.45, 0.55, 0.65, 0.72, 0.80]:
            for mg in [0.03, 0.06, 0.10]:
                round_accuracies = []
                round_known_recalls = []
                round_unknown_rejections = []
                round_fars = []
                round_frrs = []

                for _ in range(rounds):
                    # For each known speaker, randomly pick 1 chunk for enrollment
                    enrolled = []
                    query_chunks = []

                    chunks_by_speaker: dict[str, list] = {}
                    for c in valid_chunks:
                        chunks_by_speaker.setdefault(c.get("speaker", ""), []).append(c)

                    for spk, ch_list in chunks_by_speaker.items():
                        if is_unknown(spk):
                            query_chunks.extend(ch_list)
                        else:
                            enrolled_sample = rng.choice(ch_list)
                            enrolled.append({
                                "speaker_id": spk,
                                "model_id": model_name,
                                "embedding": embeddings[enrolled_sample["id"]].tolist(),
                            })
                            # Remaining are test queries
                            for c in ch_list:
                                if c["id"] != enrolled_sample["id"]:
                                    query_chunks.append(c)

                    if not query_chunks:
                        continue

                    correct = 0
                    known_total, known_correct = 0, 0
                    unknown_total, unknown_correct = 0, 0
                    impostor_accept_count, frr_count, confusion_count = 0, 0, 0

                    for q in query_chunks:
                        true_spk = q.get("speaker", "")
                        q_vec = embeddings[q["id"]].tolist()
                        match = match_speaker(q_vec, model_name, enrolled, threshold=th, margin=mg)
                        pred_spk = match["speaker_id"]

                        if is_unknown(true_spk):
                            unknown_total += 1
                            if pred_spk is None:
                                unknown_correct += 1
                                correct += 1
                            else:
                                impostor_accept_count += 1
                        else:
                            known_total += 1
                            if pred_spk == true_spk:
                                known_correct += 1
                                correct += 1
                            elif pred_spk is None:
                                frr_count += 1
                            else:
                                confusion_count += 1

                    acc = correct / len(query_chunks) if query_chunks else 1.0
                    round_accuracies.append(acc)
                    if known_total > 0:
                        round_known_recalls.append(known_correct / known_total)
                        round_frrs.append(frr_count / known_total)
                    if unknown_total > 0:
                        round_unknown_rejections.append(unknown_correct / unknown_total)
                        round_fars.append(impostor_accept_count / unknown_total)

                avg_acc = float(np.mean(round_accuracies)) if round_accuracies else 0.0
                if avg_acc > best_acc:
                    best_acc = avg_acc
                    best_cfg = {
                        "threshold": th,
                        "margin": mg,
                        "accuracy": round(avg_acc, 4),
                        "known_recall": round(float(np.mean(round_known_recalls)), 4) if round_known_recalls else 1.0,
                        "unknown_rejection": round(float(np.mean(round_unknown_rejections)), 4) if round_unknown_rejections else 1.0,
                        "far": round(float(np.mean(round_fars)), 4) if round_fars else 0.0,
                        "frr": round(float(np.mean(round_frrs)), 4) if round_frrs else 0.0,
                    }

        model_evaluations.append({
            "model": model_name,
            "dim": embedder.dim,
            "latency_ms": round(avg_latency_ms, 2),
            "rtf": round(rtf, 5),
            "intra_similarity": round(mean_intra, 4),
            "inter_similarity": round(mean_inter, 4),
            "discrimination_gap": round(discrim_gap, 4),
            "best_config": best_cfg,
        })

    # Sort by accuracy then discrimination gap
    model_evaluations.sort(key=lambda m: (m["best_config"]["accuracy"], m["discrimination_gap"]), reverse=True)
    winner = model_evaluations[0] if model_evaluations else None

    return {
        "chunks_evaluated": len(valid_chunks),
        "known_speakers": list(known_speakers),
        "rounds_per_config": rounds,
        "winner": winner["model"] if winner else None,
        "recommended_config": winner["best_config"] if winner else None,
        "models": model_evaluations,
    }


def resolve_speaker_name(
    speaker_id: str | None,
    utterance_context: dict[str, Any],
) -> str:
    """
    Contract C6: 3-Tier Dynamic Speaker Fallback Resolution.
    - Tier 1: Confirmed Named Speaker Profile (non-empty name)
    - Tier 2: Activity Roster Suspect (non-empty suspected_name)
    - Tier 3: Sequential Unknown Speaker Index (Speaker {unknown_speaker_index})
    """
    speaker_profiles = utterance_context.get("speaker_profiles", {})
    active_roster = utterance_context.get("active_roster", {})

    # Tier 1: Enrolled / Named Speaker Profiles
    if speaker_id and speaker_id in speaker_profiles:
        profile = speaker_profiles[speaker_id]
        name = profile.get("name") if isinstance(profile, dict) else getattr(profile, "name", None)
        if name and str(name).strip():
            return str(name).strip()

    # Tier 2: Activity Roster Suspects
    if speaker_id and speaker_id in active_roster:
        roster_entry = active_roster[speaker_id]
        suspected = (
            roster_entry.get("suspected_name")
            if isinstance(roster_entry, dict)
            else getattr(roster_entry, "suspected_name", None)
        )
        if suspected and str(suspected).strip():
            return str(suspected).strip()

    # Fallback to suspected_name on profile if present
    if speaker_id and speaker_id in speaker_profiles:
        profile = speaker_profiles[speaker_id]
        suspected = (
            profile.get("suspected_name")
            if isinstance(profile, dict)
            else getattr(profile, "suspected_name", None)
        )
        if suspected and str(suspected).strip():
            return str(suspected).strip()

    # Existing local label on utterance context (if provided and non-generic)
    local_label = utterance_context.get("speaker_name") or utterance_context.get("local_label")
    if local_label and str(local_label).strip() and str(local_label).strip() not in ("Unknown", "Speaker") and not str(local_label).strip().startswith("Speaker"):
        return str(local_label).strip()

    # Tier 3: Sequential Unknown Speaker Index
    unknown_idx = utterance_context.get("unknown_speaker_index", 1)
    return f"Speaker {unknown_idx}"


class SessionSpeakerResolver:
    """Session-scoped deterministic speaker resolver managing Tier 3 sequential indexing."""

    def __init__(
        self,
        speaker_profiles: dict[str, Any] | None = None,
        active_roster: dict[str, Any] | None = None,
    ):
        self.speaker_profiles = dict(speaker_profiles or {})
        self.active_roster = dict(active_roster or {})
        self._unknown_map: dict[str, int] = {}
        self._next_index: int = 1

    def resolve(
        self,
        speaker_id: str | None = None,
        cluster_id: str | None = None,
        local_label: str | None = None,
    ) -> str:
        # Tier 1: Enrolled Profile
        if speaker_id and speaker_id in self.speaker_profiles:
            profile = self.speaker_profiles[speaker_id]
            name = profile.get("name") if isinstance(profile, dict) else getattr(profile, "name", None)
            if name and str(name).strip():
                return str(name).strip()

        # Tier 2: Active Roster Suspect
        key = speaker_id or cluster_id or local_label
        if key and key in self.active_roster:
            roster_entry = self.active_roster[key]
            suspected = (
                roster_entry.get("suspected_name")
                if isinstance(roster_entry, dict)
                else getattr(roster_entry, "suspected_name", None)
            )
            if suspected and str(suspected).strip():
                return str(suspected).strip()

        if speaker_id and speaker_id in self.speaker_profiles:
            profile = self.speaker_profiles[speaker_id]
            suspected = (
                profile.get("suspected_name")
                if isinstance(profile, dict)
                else getattr(profile, "suspected_name", None)
            )
            if suspected and str(suspected).strip():
                return str(suspected).strip()

        # Prioritize existing concrete name on utterance if not a generic placeholder
        if local_label and str(local_label).strip() and str(local_label).strip() not in ("Unknown", "Speaker") and not str(local_label).strip().startswith("Speaker"):
            return str(local_label).strip()

        # Tier 3: Deterministic Sequential Index
        unassigned_key = speaker_id or cluster_id or local_label or f"_anon_{len(self._unknown_map) + 1}"
        if unassigned_key not in self._unknown_map:
            self._unknown_map[unassigned_key] = self._next_index
            self._next_index += 1

        idx = self._unknown_map[unassigned_key]
        return f"Speaker {idx}"

