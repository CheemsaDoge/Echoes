from __future__ import annotations
import io, os, wave
from pathlib import Path
from dataclasses import dataclass, asdict
import numpy as np

SAMPLE_RATE = 16000

@dataclass(frozen=True)
class Span:
    packed_start: int
    packed_end: int
    original_start: int
    original_end: int
    chunk_id: str
    chunk_start: int

    def to_dict(self):
        return asdict(self)

def read_wav(data: bytes) -> np.ndarray:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
                raise ValueError("Expected mono, 16 kHz, signed 16-bit PCM WAV")
            n = w.getnframes()
            if n <= 0 or n > SAMPLE_RATE * 180:
                raise ValueError("Audio must contain between 1 sample and 180 seconds")
            raw = w.readframes(n)
            if len(raw) != n * 2:
                raise ValueError("Truncated WAV")
            return np.frombuffer(raw, dtype="<i2").copy()
    except (wave.Error, EOFError) as e:
        raise ValueError("Invalid WAV") from e

def wav_bytes(samples: np.ndarray) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.astype("<i2").tobytes())
    return out.getvalue()

def atomic_write(path: Path, content: bytes) -> None:
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temp, "xb") as f:
            os.chmod(temp, 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        # Persist the rename on POSIX filesystems.
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)

def merge_intervals(intervals: list[tuple[int, int]], n: int) -> list[tuple[int, int]]:
    result: list[list[int]] = []
    for a, b in sorted((max(0, int(a)), min(n, int(b))) for a, b in intervals):
        if b <= a:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return [(a, b) for a, b in result]

def pack_audio(samples: np.ndarray, intervals: list[tuple[int, int]],
               sources: list[dict], separator_ms: int = 120) -> tuple[np.ndarray, list[Span]]:
    """sources cover input samples; original offsets are in the session's sample clock.

    Inserted separators have no Span. Never invent original timestamps for them.
    """
    arrays, mapping = [], []
    pos = 0
    previous_end = 0
    for idx, (a, b) in enumerate(merge_intervals(intervals, len(samples))):
        if idx and separator_ms:
            count = min(separator_ms * SAMPLE_RATE // 1000, a - previous_end)
            arrays.append(np.zeros(count, dtype=np.int16))
            pos += count
        arrays.append(samples[a:b])
        for src in sources:
            lo, hi = max(a, src["input_start"]), min(b, src["input_end"])
            if lo < hi:
                original = src["original_start"] + lo - src["input_start"]
                mapping.append(Span(pos + lo - a, pos + hi - a, original,
                                    original + hi - lo, src["chunk_id"], src["chunk_start"]))
        pos += b - a
        previous_end = b
    return (np.concatenate(arrays) if arrays else np.zeros(0, dtype=np.int16)), mapping

def restore_interval(start_ms: int, end_ms: int, mapping: list[Span], session_start_ms: int) -> list[dict]:
    a, b = start_ms * SAMPLE_RATE // 1000, end_ms * SAMPLE_RATE // 1000
    restored = []
    for span in mapping:
        lo, hi = max(a, span.packed_start), min(b, span.packed_end)
        if lo < hi:
            original = span.original_start + lo - span.packed_start
            original_end = original + hi - lo
            restored.append({
                "chunk_id": span.chunk_id,
                "source_start_sample": original - span.chunk_start,
                "source_end_sample": original_end - span.chunk_start,
                "session_start_sample": original,
                "session_end_sample": original_end,
                "start_ms": session_start_ms + original * 1000 // SAMPLE_RATE,
                "end_ms": session_start_ms + original_end * 1000 // SAMPLE_RATE,
            })
    return restored
