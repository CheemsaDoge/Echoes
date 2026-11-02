from __future__ import annotations
import json
import numpy as np

def normalize(vector: list[float]) -> np.ndarray:
    a = np.asarray(vector, dtype=np.float64)
    if a.ndim != 1 or not 8 <= len(a) <= 4096 or not np.isfinite(a).all():
        raise ValueError("Embedding must contain 8..4096 finite values")
    norm = float(np.linalg.norm(a))
    if norm < 1e-12:
        raise ValueError("Zero embedding is invalid")
    return a / norm

def match_speaker(vector: list[float], model_id: str, enrolled: list[dict], threshold=.8, margin=.1) -> dict:
    query = normalize(vector)
    # Aggregate by person so two samples of one person do not become rival candidates.
    by_person = {}
    for entry in enrolled:
        if entry["model_id"] != model_id:
            continue
        v = normalize(json.loads(entry["embedding"]))
        if len(v) != len(query):
            continue
        similarity = float(np.clip(query @ v, -1., 1.))
        person = entry["speaker_id"]
        by_person[person] = max(by_person.get(person, -1.), similarity)
    ranked = sorted(by_person.items(), key=lambda x: x[1], reverse=True)
    best = ranked[0] if ranked else (None, -1.)
    second = ranked[1][1] if len(ranked) > 1 else -1.
    accepted = best[1] >= threshold and best[1] - second >= margin
    return {"speaker_id": best[0] if accepted else None, "accepted": accepted,
            "similarity": best[1] if ranked else None, "margin": best[1] - second if ranked else None,
            "candidates": [{"speaker_id": k, "similarity": v} for k, v in ranked[:5]],
            "warning": "Thresholds require calibration for this embedding model and recording environment."}
