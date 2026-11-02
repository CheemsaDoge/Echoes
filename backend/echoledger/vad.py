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
    def probabilities(self, samples: np.ndarray) -> np.ndarray:
        return np.ones(math.ceil(len(samples) / FRAME), dtype=np.float32)

class EnergyDemoScorer:
    """Synthetic-test/demo ONLY. Energy is not reliable speech detection."""
    model_id = "energy-demo-v1-not-speech-detection"
    def probabilities(self, samples: np.ndarray) -> np.ndarray:
        scores = []
        for i in range(0, len(samples), FRAME):
            frame = samples[i:i + FRAME].astype(np.float32) / 32768
            rms = float(np.sqrt(np.mean(frame * frame)))
            scores.append(min(1., max(0., (rms - .005) / .035)))
        return np.asarray(scores, dtype=np.float32)

class SileroOnnxScorer:
    """Silero's 16 kHz recurrent ONNX interface (v5). Model weights are not bundled."""
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

    def probabilities(self, samples: np.ndarray) -> np.ndarray:
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

class SileroV4OnnxScorer:
    """Silero VAD v4 recurrent ONNX interface."""
    def __init__(self, path: str):
        import onnxruntime as ort
        model = Path(path)
        if not model.is_file():
            raise RuntimeError(f"VAD model missing: {path}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        self.model_id = "silero-v4-sha256:" + hashlib.sha256(model.read_bytes()).hexdigest()
        names = {x.name for x in self.session.get_inputs()}
        if not {"input", "sr", "h", "c"}.issubset(names):
            raise RuntimeError("Unsupported Silero v4 ONNX signature; expected input/sr/h/c")

    def probabilities(self, samples: np.ndarray) -> np.ndarray:
        h = np.zeros((2, 1, 64), dtype=np.float32)
        c = np.zeros((2, 1, 64), dtype=np.float32)
        audio = samples.astype(np.float32) / 32768.0
        scores = []
        for i in range(0, len(audio), FRAME):
            frame = audio[i:i + FRAME]
            if len(frame) < FRAME:
                frame = np.pad(frame, (0, FRAME - len(frame)))
            prob, h, c = self.session.run(None, {
                "input": frame.reshape(1, -1),
                "sr": np.array(16000, dtype=np.int64),
                "h": h,
                "c": c,
            })
            scores.append(float(np.asarray(prob).reshape(-1)[0]))
        return np.asarray(scores, dtype=np.float32)

class SileroV3OnnxScorer:
    """Silero VAD v3 recurrent ONNX interface."""
    def __init__(self, path: str):
        import onnxruntime as ort
        model = Path(path)
        if not model.is_file():
            raise RuntimeError(f"VAD model missing: {path}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        self.model_id = "silero-v3-sha256:" + hashlib.sha256(model.read_bytes()).hexdigest()

    def probabilities(self, samples: np.ndarray) -> np.ndarray:
        h0 = np.zeros((2, 1, 64), dtype=np.float32)
        c0 = np.zeros((2, 1, 64), dtype=np.float32)
        audio = samples.astype(np.float32) / 32768.0
        scores = []
        for i in range(0, len(audio), FRAME):
            frame = audio[i:i + FRAME]
            if len(frame) < FRAME:
                frame = np.pad(frame, (0, FRAME - len(frame)))
            out, h0, c0 = self.session.run(None, {
                "input": frame.reshape(1, -1),
                "h0": h0,
                "c0": c0,
            })
            scores.append(float(out[0, 1, 0]))
        return np.asarray(scores, dtype=np.float32)

class TenVadOnnxScorer:
    """TEN-framework neural VAD model (16kHz) powered by sherpa-onnx runtime."""
    def __init__(self, path: str, threshold: float = 0.5):
        import sherpa_onnx
        model = Path(path)
        if not model.is_file():
            raise RuntimeError(f"VAD model missing: {path}")
        self.model_path = str(model)
        self.threshold = threshold
        self.model_id = "ten-vad-sha256:" + hashlib.sha256(model.read_bytes()).hexdigest()

    def probabilities(self, samples: np.ndarray) -> np.ndarray:
        if len(samples) == 0:
            return np.zeros(0, dtype=np.float32)
        import sherpa_onnx
        config = sherpa_onnx.VadModelConfig()
        config.sample_rate = 16000
        config.num_threads = 1
        config.provider = "cpu"
        config.ten_vad.model = self.model_path
        config.ten_vad.threshold = self.threshold
        config.ten_vad.min_silence_duration = 0.1
        config.ten_vad.min_speech_duration = 0.1
        config.ten_vad.window_size = 256
        vad = sherpa_onnx.VadModel.create(config)

        audio = samples.astype(np.float32) / 32768.0
        scores = []
        for i in range(0, len(audio), FRAME):
            frame = audio[i:i + FRAME]
            if len(frame) < FRAME:
                frame = np.pad(frame, (0, FRAME - len(frame)))
            s1 = 1.0 if vad.is_speech(frame[:256]) else 0.0
            s2 = 1.0 if vad.is_speech(frame[256:]) else 0.0
            scores.append(float((s1 + s2) / 2.0))
        return np.asarray(scores, dtype=np.float32)

class WebRtcScorer:
    """Google WebRTC GMM VAD supporting modes 0, 1, 2, 3."""
    def __init__(self, mode: int = 2):
        import webrtcvad
        if mode not in (0, 1, 2, 3):
            raise ValueError(f"Invalid WebRTC VAD mode: {mode}, expected 0, 1, 2, or 3")
        self.mode = mode
        self.model_id = f"webrtc-mode-{mode}"

    def probabilities(self, samples: np.ndarray) -> np.ndarray:
        if len(samples) == 0:
            return np.zeros(0, dtype=np.float32)
        import webrtcvad
        # Create fresh Vad instance per call to eliminate state leakage across independent recordings
        vad = webrtcvad.Vad(self.mode)
        pcm = samples.astype(np.int16)
        n_samples = len(pcm)
        sub_len = 160  # 10ms subframe at 16kHz

        # Process the entire stream contiguously across 10ms subframes
        n_subframes = math.ceil(n_samples / sub_len)
        sub_flags = np.zeros(n_subframes, dtype=np.float32)
        for idx in range(n_subframes):
            start = idx * sub_len
            end = min(n_samples, start + sub_len)
            chunk = pcm[start:end]
            if len(chunk) < sub_len:
                chunk = np.pad(chunk, (0, sub_len - len(chunk)))
            sub_flags[idx] = 1.0 if vad.is_speech(chunk.tobytes(), 16000) else 0.0

        # Map 10ms subframes back to the 512-sample (32ms) output frame grid
        n_frames = math.ceil(n_samples / FRAME)
        scores = np.zeros(n_frames, dtype=np.float32)
        for i in range(n_frames):
            frame_start = i * FRAME
            frame_end = min(n_samples, frame_start + FRAME)
            sub_start = frame_start // sub_len
            sub_end = math.ceil(frame_end / sub_len)
            sub_slice = sub_flags[sub_start:sub_end]
            scores[i] = float(np.mean(sub_slice)) if len(sub_slice) > 0 else 0.0

        return scores

def _find_model_path(candidates: list[str]) -> str:
    for c in candidates:
        if Path(c).is_file():
            return c
    return ""

def make_scorer(name: str, model_path: str = "") -> FrameScorer:
    if name == "passthrough":
        return PassthroughScorer()
    if name == "energy_demo":
        return EnergyDemoScorer()
    if name == "silero":
        # Keep strict backward-compatibility: if model_path is empty and not configured,
        # SileroOnnxScorer("") raises RuntimeError("VAD model missing: configure VAD_MODEL_PATH")
        return SileroOnnxScorer(model_path)
    if name == "silero_v5":
        target = model_path or _find_model_path(["data/models/vad/silero_vad_v5.onnx", "data/models/silero_vad.onnx"])
        if not target:
            raise RuntimeError("VAD model missing: data/models/vad/silero_vad_v5.onnx")
        return SileroOnnxScorer(target)
    if name == "silero_v4":
        target = model_path or _find_model_path(["data/models/vad/silero_vad_v4.onnx"])
        if not target:
            raise RuntimeError("VAD model missing: data/models/vad/silero_vad_v4.onnx")
        return SileroV4OnnxScorer(target)
    if name == "silero_v3":
        target = model_path or _find_model_path(["data/models/vad/silero_vad_v3.onnx"])
        if not target:
            raise RuntimeError("VAD model missing: data/models/vad/silero_vad_v3.onnx")
        return SileroV3OnnxScorer(target)
    if name == "ten_vad":
        target = model_path or _find_model_path(["data/models/vad/ten_vad.onnx"])
        if not target:
            raise RuntimeError("VAD model missing: data/models/vad/ten_vad.onnx")
        return TenVadOnnxScorer(target)
    if name == "ten_vad_int8":
        target = model_path or _find_model_path(["data/models/vad/ten_vad_int8.onnx"])
        if not target:
            raise RuntimeError("VAD model missing: data/models/vad/ten_vad_int8.onnx")
        return TenVadOnnxScorer(target)
    if name == "webrtc":
        return WebRtcScorer(mode=2)
    if name.startswith("webrtc_"):
        mode = int(name.split("_")[1])
        return WebRtcScorer(mode=mode)
    raise ValueError(f"Unknown VAD provider: {name}")

def list_available_vads(models_dir: Path = Path("data/models/vad")) -> list[dict]:
    """List all supported VAD providers and their local availability status."""
    vads = [
        {"id": "passthrough", "type": "rule", "ready": True, "description": "No filtering; keeps all audio"},
        {"id": "energy_demo", "type": "rule", "ready": True, "description": "Synthetic energy demonstration only"},
        {"id": "webrtc_0", "type": "gmm", "ready": True, "description": "WebRTC GMM (mode 0, least aggressive)"},
        {"id": "webrtc_1", "type": "gmm", "ready": True, "description": "WebRTC GMM (mode 1, low aggressiveness)"},
        {"id": "webrtc_2", "type": "gmm", "ready": True, "description": "WebRTC GMM (mode 2, moderate aggressiveness)"},
        {"id": "webrtc_3", "type": "gmm", "ready": True, "description": "WebRTC GMM (mode 3, highly aggressive)"},
        {
            "id": "silero_v5",
            "type": "neural_onnx",
            "model_path": str(models_dir / "silero_vad_v5.onnx"),
            "ready": (models_dir / "silero_vad_v5.onnx").is_file() or Path("data/models/silero_vad.onnx").is_file(),
            "description": "Silero VAD v5 ONNX (recurrent, input/sr/state)",
        },
        {
            "id": "silero_v4",
            "type": "neural_onnx",
            "model_path": str(models_dir / "silero_vad_v4.onnx"),
            "ready": (models_dir / "silero_vad_v4.onnx").is_file(),
            "description": "Silero VAD v4 ONNX (recurrent, input/sr/h/c)",
        },
        {
            "id": "silero_v3",
            "type": "neural_onnx",
            "model_path": str(models_dir / "silero_vad_v3.onnx"),
            "ready": (models_dir / "silero_vad_v3.onnx").is_file(),
            "description": "Silero VAD v3 ONNX (recurrent, input/h0/c0)",
        },
        {
            "id": "ten_vad",
            "type": "neural_onnx",
            "model_path": str(models_dir / "ten_vad.onnx"),
            "ready": (models_dir / "ten_vad.onnx").is_file(),
            "description": "TEN-framework VAD ONNX (16kHz)",
        },
        {
            "id": "ten_vad_int8",
            "type": "neural_onnx",
            "model_path": str(models_dir / "ten_vad_int8.onnx"),
            "ready": (models_dir / "ten_vad_int8.onnx").is_file(),
            "description": "TEN-framework quantized INT8 VAD ONNX",
        },
    ]
    return vads

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
