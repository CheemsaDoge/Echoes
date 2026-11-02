from __future__ import annotations
import hashlib, math
from pathlib import Path
from typing import Protocol
import numpy as np
from .audio import SAMPLE_RATE, merge_intervals
from .models import VadConfig

FRAME = 512

class FrameScorer(Protocol):
    model_id: str
    def probabilities(self, samples: np.ndarray) -> np.ndarray: ...

class PassthroughScorer:
    model_id = "passthrough-v1-no-detection"
    def probabilities(self, samples):
        return np.ones(math.ceil(len(samples) / FRAME), dtype=np.float32)

class EnergyDemoScorer:
    """Synthetic-test/demo ONLY. Energy is not reliable speech detection."""
    model_id = "energy-demo-v1-not-speech-detection"
    def probabilities(self, samples):
        scores = []
        for i in range(0, len(samples), FRAME):
            frame = samples[i:i + FRAME].astype(np.float32) / 32768
            rms = float(np.sqrt(np.mean(frame * frame)))
            scores.append(min(1., max(0., (rms - .005) / .035)))
        return np.asarray(scores, dtype=np.float32)

class SileroOnnxScorer:
    """Silero's 16 kHz recurrent ONNX interface. Model weights are not bundled."""
    def __init__(self, path: str):
        import onnxruntime as ort
        model = Path(path)
        if not model.is_file():
            raise RuntimeError("VAD model missing: configure VAD_MODEL_PATH")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        self.model_id = "silero-onnx-sha256:" + hashlib.sha256(model.read_bytes()).hexdigest()
        names = {x.name for x in self.session.get_inputs()}
        if names != {"input", "sr", "state"}:
            raise RuntimeError("Unsupported Silero ONNX signature; expected input/sr/state")

    def probabilities(self, samples):
        # Each call owns recurrent state; concurrent calibration calls cannot corrupt it.
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, 64), dtype=np.float32)
        audio = samples.astype(np.float32) / 32768.
        scores = []
        for i in range(0, len(audio), FRAME):
            frame = audio[i:i + FRAME]
            if len(frame) < FRAME:
                frame = np.pad(frame, (0, FRAME - len(frame)))
            inp = np.concatenate((context, frame.reshape(1, -1)), axis=1)
            prob, state = self.session.run(None, {"input": inp, "sr": np.array(16000, dtype=np.int64), "state": state})
            scores.append(float(np.asarray(prob).reshape(-1)[0]))
            context = inp[:, -64:]
        return np.asarray(scores, dtype=np.float32)

def make_scorer(name: str, model_path: str = "") -> FrameScorer:
    if name == "passthrough":
        return PassthroughScorer()
    if name == "energy_demo":
        return EnergyDemoScorer()
    if name == "silero":
        return SileroOnnxScorer(model_path)
    raise ValueError("Unknown VAD provider")

def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.pad(mask.astype(np.int8), (1, 1))
    starts = np.flatnonzero(np.diff(padded) == 1)
    ends = np.flatnonzero(np.diff(padded) == -1)
    return list(zip(starts.tolist(), ends.tolist()))

def speech_intervals(probs: np.ndarray, n_samples: int, config: VadConfig) -> list[tuple[int, int]]:
    if len(probs) != math.ceil(n_samples / FRAME) or not np.isfinite(probs).all():
        raise ValueError("Invalid VAD probabilities")
    if np.any(probs < 0) or np.any(probs > 1):
        raise ValueError("VAD probabilities must be within [0,1]")
    min_speech = config.min_speech_ms * SAMPLE_RATE // 1000
    silence_frames = max(1, math.ceil(config.min_silence_ms / 32))
    intervals = []
    start, low_start = None, None
    for i, p in enumerate(probs):
        if start is None:
            if p >= config.threshold:
                start = i
            continue
        if p < config.negative_threshold:
            if low_start is None:
                low_start = i
            if i - low_start + 1 >= silence_frames:
                if (low_start - start) * FRAME >= min_speech:
                    intervals.append((start * FRAME, low_start * FRAME))
                start, low_start = None, None
        else:
            low_start = None
    if start is not None:
        end = low_start * FRAME if low_start is not None else n_samples
        if end - start * FRAME >= min_speech:
            intervals.append((start * FRAME, end))
    # Keep ambiguous/short utterances in conservative mode. This trades token savings for recall.
    if config.retain_uncertain:
        intervals.extend((a * FRAME, b * FRAME) for a, b in runs(probs >= config.uncertainty_threshold))
    pad = config.pad_ms * SAMPLE_RATE // 1000
    return merge_intervals([(a - pad, b + pad) for a, b in intervals], n_samples)

def score_intervals(predicted: list[tuple[int, int]], truth: list[tuple[int, int]], n: int) -> dict:
    pred = merge_intervals(predicted, n)
    gold = merge_intervals(truth, n)
    predicted_n = sum(b - a for a, b in pred)
    gold_n = sum(b - a for a, b in gold)
    tp = sum(max(0, min(b, d) - max(a, c)) for a, b in pred for c, d in gold)
    fn, fp = gold_n - tp, predicted_n - tp
    negative_n = n - gold_n
    return {
        "speech_recall": tp / gold_n if gold_n else None,
        "retained_precision": tp / predicted_n if predicted_n else None,
        "non_speech_retained_rate": fp / negative_n if negative_n else None,
        "removed_fraction": 1 - predicted_n / n if n else 0,
        "missed_speech_ms": fn * 1000 / SAMPLE_RATE,
        "false_retained_ms": fp * 1000 / SAMPLE_RATE,
        "labeled_speech_ms": gold_n * 1000 / SAMPLE_RATE,
        "labeled_non_speech_ms": negative_n * 1000 / SAMPLE_RATE,
    }
