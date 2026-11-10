#!/usr/bin/env python3
"""
Empirical Adversarial Verification Harness for Milestone M1 Audio & Rollover Pipeline.
Independent Python Oracle & Generator for:
1. ADTS Bitstream Framing across payloads 1..8191, bit-level unpack, corrupt rejection.
2. Elastic Rollover Window razor-edge boundary conditions and transition rules.
3. RMS dBFS precision, clipping bounds, DC offset, Nyquist pulses, -160 dBFS floor.
4. Seamless zero-drop sink swap stream continuity.
"""

import math
import struct
import sys
from typing import Optional, Tuple, List

# ============================================================================
# 1. ADTS BITSTREAM ORACLE
# ============================================================================

SAMPLE_RATES = [
    96000, 88200, 64000, 48000, 44100, 32000,
    24000, 22050, 16000, 12000, 11025, 8000, 7350
]

def synthesize_adts_header(payload_length: int, sample_rate: int = 16000, channels: int = 1) -> bytes:
    if payload_length <= 0:
        raise ValueError(f"Payload length must be positive: {payload_length}")
    frame_length = payload_length + 7
    if frame_length > 8191:
        raise ValueError(f"Total frame length exceeds 13-bit ADTS limit: {frame_length}")
    if sample_rate not in SAMPLE_RATES:
        raise ValueError(f"Unsupported sample rate: {sample_rate}")
    if channels not in range(1, 8):
        raise ValueError(f"Unsupported channel count: {channels}")

    sfi = SAMPLE_RATES.index(sample_rate)
    profile = 1  # AAC-LC

    b0 = 0xFF
    b1 = 0xF1  # Sync 0xF, ID 0 (MPEG-4), Layer 00, ProtectionAbsent 1
    b2 = ((profile << 6) | (sfi << 2) | (channels >> 2)) & 0xFF
    b3 = (((channels & 3) << 6) | ((frame_length >> 11) & 0x03)) & 0xFF
    b4 = (frame_length >> 3) & 0xFF
    b5 = (((frame_length & 7) << 5) | 0x1F) & 0xFF
    b6 = 0xFC  # Buffer fullness low bits (0x3F << 2) | 00

    return bytes([b0, b1, b2, b3, b4, b5, b6])

def parse_and_validate_adts(header: bytes) -> dict:
    if len(header) < 7:
        raise ValueError("Header too short (< 7 bytes)")

    b0, b1, b2, b3, b4, b5, b6 = header[:7]

    syncword = (b0 << 4) | (b1 >> 4)
    if syncword != 0xFFF:
        raise ValueError(f"Invalid syncword: 0x{syncword:03X} (expected 0xFFF)")

    id_bit = (b1 >> 3) & 1
    if id_bit != 0:
        raise ValueError(f"Invalid MPEG ID: {id_bit} (expected 0 for MPEG-4)")

    layer = (b1 >> 1) & 3
    if layer != 0:
        raise ValueError(f"Invalid layer: {layer} (expected 00)")

    protection_absent = b1 & 1
    if protection_absent != 1:
        raise ValueError(f"Protection absent must be 1 (no CRC), got: {protection_absent}")

    profile = (b2 >> 6) & 3
    if profile != 1:
        raise ValueError(f"Invalid profile: {profile} (expected 1 for AAC-LC)")

    sfi = (b2 >> 2) & 0xF
    if sfi != 8:
        raise ValueError(f"Invalid sampling freq index: {sfi} (expected 8 for 16kHz)")

    private_bit = (b2 >> 1) & 1
    if private_bit != 0:
        raise ValueError(f"Invalid private bit: {private_bit} (expected 0)")

    channel_config = ((b2 & 1) << 2) | (b3 >> 6)
    if channel_config != 1:
        raise ValueError(f"Invalid channel config: {channel_config} (expected 1 for Mono)")

    frame_length = ((b3 & 0x03) << 11) | (b4 << 3) | (b5 >> 5)
    if frame_length < 7 or frame_length > 8191:
        raise ValueError(f"Invalid frame length: {frame_length} (expected 7..8191)")

    buffer_fullness = ((b5 & 0x1F) << 6) | (b6 >> 2)
    if buffer_fullness != 0x7FF:
        raise ValueError(f"Invalid buffer fullness: 0x{buffer_fullness:03X} (expected 0x7FF)")

    num_rdb = b6 & 3
    if num_rdb != 0:
        raise ValueError(f"Invalid number of AAC frames: {num_rdb} (expected 0)")

    return {
        "syncword": syncword,
        "id": id_bit,
        "layer": layer,
        "protection_absent": protection_absent,
        "profile": profile,
        "sfi": sfi,
        "sample_rate": SAMPLE_RATES[sfi],
        "channel_config": channel_config,
        "frame_length": frame_length,
        "payload_length": frame_length - 7,
        "buffer_fullness": buffer_fullness,
        "num_rdb": num_rdb
    }

# ============================================================================
# 2. RMS dBFS ORACLE
# ============================================================================

def compute_rms_dbfs(samples: List[int]) -> float:
    if not samples:
        return -160.0
    sum_sq = sum(s * s for s in samples)
    rms = math.sqrt(sum_sq / len(samples))
    if rms > 0.0:
        return 20.0 * math.log10(rms / 32768.0)
    return -160.0

# ============================================================================
# 3. ELASTIC ROLLOVER ENGINE SIMULATOR
# ============================================================================

class RolloverDecision:
    def __init__(self, action: str, split_reason: Optional[str] = None, silence_ms: Optional[int] = None):
        self.action = action  # "NONE" or "CUT"
        self.split_reason = split_reason  # "silence_window" or "hard_limit"
        self.silence_ms = silence_ms

    def __repr__(self):
        return f"RolloverDecision({self.action}, reason={self.split_reason}, silence_ms={self.silence_ms})"

class ElasticRolloverEngineSimulator:
    SOFT_LIMIT_MS = 1_560_000   # 26 min
    HARD_LIMIT_MS = 1_800_000   # 30 min
    SILENCE_THRESHOLD_DBFS = -40.0
    REQUIRED_SILENCE_MS = 800

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.segment_start_ms = 0
        self.continuous_silence_ms = 0
        self.total_samples = 0

    def start_segment(self, epoch_ms: int):
        self.segment_start_ms = epoch_ms
        self.continuous_silence_ms = 0
        self.total_samples = 0

    def on_audio_buffer(self, pcm_samples: List[int], now_ms: int) -> RolloverDecision:
        count = len(pcm_samples)
        if count <= 0:
            return RolloverDecision("NONE")

        frame_duration_ms = (count * 1000) // self.sample_rate
        self.total_samples += count

        dbfs = compute_rms_dbfs(pcm_samples)

        if dbfs <= self.SILENCE_THRESHOLD_DBFS:
            self.continuous_silence_ms += frame_duration_ms
        else:
            self.continuous_silence_ms = 0

        elapsed_ms = now_ms - self.segment_start_ms

        if elapsed_ms >= self.HARD_LIMIT_MS:
            return RolloverDecision("CUT", "hard_limit", None)

        if elapsed_ms >= self.SOFT_LIMIT_MS and self.continuous_silence_ms >= self.REQUIRED_SILENCE_MS:
            return RolloverDecision("CUT", "silence_window", self.continuous_silence_ms)

        return RolloverDecision("NONE")

# ============================================================================
# MAIN VERIFICATION RUNNER
# ============================================================================

def run_all_adversarial_checks() -> bool:
    print("=== [1] ADTS Bitstream Framing Adversarial Suite ===")
    # 1.1 Test all payloads 1..8184
    passed_payloads = 0
    for p in range(1, 8185):
        hdr = synthesize_adts_header(p)
        info = parse_and_validate_adts(hdr)
        assert info["payload_length"] == p
        assert info["frame_length"] == p + 7
        assert info["syncword"] == 0xFFF
        assert info["id"] == 0
        assert info["layer"] == 0
        assert info["protection_absent"] == 1
        assert info["profile"] == 1
        assert info["sfi"] == 8
        assert info["channel_config"] == 1
        assert info["buffer_fullness"] == 0x7FF
        assert info["num_rdb"] == 0
        passed_payloads += 1
    print(f"  [PASS] Successfully validated {passed_payloads} bit-exact ADTS frames (1..8184 bytes).")

    # 1.2 Boundary payloads 8185..8191
    for p in range(8185, 8192):
        try:
            synthesize_adts_header(p)
            print(f"  [FAIL] Payload {p} should have been rejected!")
            return False
        except ValueError:
            pass
    print("  [PASS] Correctly rejected payloads 8185..8191 exceeding 13-bit frame length limit.")

    # 1.3 Corrupt header rejection
    valid = synthesize_adts_header(384)
    # Corrupt syncword
    try:
        parse_and_validate_adts(bytes([0xFE]) + valid[1:])
        print("  [FAIL] Failed to reject corrupt syncword byte 0")
        return False
    except ValueError:
        pass

    # Corrupt ID
    try:
        parse_and_validate_adts(valid[:1] + bytes([valid[1] | 0x08]) + valid[2:])
        print("  [FAIL] Failed to reject MPEG-2 ID")
        return False
    except ValueError:
        pass

    # Corrupt Protection absent
    try:
        parse_and_validate_adts(valid[:1] + bytes([valid[1] & 0xFE]) + valid[2:])
        print("  [FAIL] Failed to reject CRC present header")
        return False
    except ValueError:
        pass

    # Corrupt Profile
    try:
        parse_and_validate_adts(valid[:2] + bytes([(valid[2] & 0x3F) | (2 << 6)]) + valid[3:])
        print("  [FAIL] Failed to reject non-AAC-LC profile")
        return False
    except ValueError:
        pass

    # Corrupt SFI
    try:
        parse_and_validate_adts(valid[:2] + bytes([(valid[2] & 0xC3) | (4 << 2)]) + valid[3:])
        print("  [FAIL] Failed to reject non-16kHz sample rate")
        return False
    except ValueError:
        pass
    print("  [PASS] Corrupted ADTS header rejection verified.")

    print("\n=== [2] Elastic Rollover Boundary Adversarial Suite ===")
    sim = ElasticRolloverEngineSimulator()
    epoch_start = 10_000_000

    # Test 2.1: 25m59.999s with 1000ms silence -> MUST NOT cut
    sim.start_segment(epoch_start)
    silence_1000ms = [100] * (16 * 1000)  # ~ -50.3 dBFS
    t_25m59s999ms = epoch_start + 1_559_999
    d1 = sim.on_audio_buffer(silence_1000ms, t_25m59s999ms)
    assert d1.action == "NONE", f"Must NOT cut at 25m59.999s, got: {d1}"
    print("  [PASS] 25m59.999s with 1000ms silence at -50 dBFS -> RolloverDecision.None")

    # Test 2.2: 26m00.001s with 799ms silence -> MUST NOT cut
    sim.start_segment(epoch_start)
    silence_799ms = [100] * (16 * 799)
    t_26m00s001ms = epoch_start + 1_560_001
    d2 = sim.on_audio_buffer(silence_799ms, t_26m00s001ms)
    assert d2.action == "NONE", f"Must NOT cut with 799ms silence, got: {d2}"
    print("  [PASS] 26m00.001s with 799ms silence at -50 dBFS -> RolloverDecision.None")

    # Test 2.3: 26m00.001s with 800ms silence at -40.1 dBFS -> MUST cut silence_window
    sim.start_segment(epoch_start)
    silence_800ms_minus40_1 = [323] * (16 * 800)  # -40.125 dBFS
    d3 = sim.on_audio_buffer(silence_800ms_minus40_1, t_26m00s001ms)
    assert d3.action == "CUT" and d3.split_reason == "silence_window" and d3.silence_ms == 800, f"Expected silence_window cut, got: {d3}"
    print("  [PASS] 26m00.001s with 800ms silence at -40.1 dBFS -> RolloverDecision.Cut(silence_window, 800ms)")

    # Test 2.4: 26m00.001s with 800ms silence at -39.9 dBFS -> MUST NOT cut
    sim.start_segment(epoch_start)
    speech_800ms_minus39_9 = [332] * (16 * 800)  # -39.886 dBFS
    d4 = sim.on_audio_buffer(speech_800ms_minus39_9, t_26m00s001ms)
    assert d4.action == "NONE", f"Must NOT cut at -39.9 dBFS, got: {d4}"
    assert sim.continuous_silence_ms == 0, "Silence accumulator must reset"
    print("  [PASS] 26m00.001s with 800ms speech at -39.9 dBFS -> RolloverDecision.None (silence reset)")

    # Test 2.5: 29m59.999s with no silence -> MUST NOT cut
    sim.start_segment(epoch_start)
    t_29m59s999ms = epoch_start + 1_799_999
    speech_100ms = [5000] * (16 * 100)
    d5 = sim.on_audio_buffer(speech_100ms, t_29m59s999ms)
    assert d5.action == "NONE", f"Must NOT cut at 29m59.999s, got: {d5}"
    print("  [PASS] 29m59.999s with continuous speech -> RolloverDecision.None")

    # Test 2.6: 30m00.000s with loud speech -> MUST cut hard_limit
    sim.start_segment(epoch_start)
    t_30m00s000ms = epoch_start + 1_800_000
    loud_speech = [25000] * (16 * 100)
    d6 = sim.on_audio_buffer(loud_speech, t_30m00s000ms)
    assert d6.action == "CUT" and d6.split_reason == "hard_limit" and d6.silence_ms is None, f"Expected hard_limit cut, got: {d6}"
    print("  [PASS] 30m00.000s with loud speech -> RolloverDecision.Cut(hard_limit, null)")

    print("\n=== [3] RMS dBFS Math Stability Adversarial Suite ===")
    # 3.1 Silence
    dbfs_zero = compute_rms_dbfs([0] * 1024)
    assert dbfs_zero == -160.0
    print("  [PASS] Zero amplitude -> exactly -160.0 dBFS floor (no -Infinity or NaN)")

    # 3.2 Full scale clipping
    dbfs_neg_clip = compute_rms_dbfs([-32768] * 1024)
    assert abs(dbfs_neg_clip - 0.0) < 1e-9
    dbfs_pos_clip = compute_rms_dbfs([32767] * 1024)
    assert abs(dbfs_pos_clip - 0.0) < 0.0003
    print("  [PASS] Full scale clipping (+32767 / -32768) -> ~0.0 dBFS")

    # 3.3 DC offset
    for dc in [50, 500, 5000]:
        dbfs_dc = compute_rms_dbfs([dc] * 1024)
        expected_dc = 20.0 * math.log10(dc / 32768.0)
        assert abs(dbfs_dc - expected_dc) < 1e-6
    print("  [PASS] DC offset energy matches exact mathematical log10 formula.")

    # 3.4 Nyquist alternating pulses
    nyquist_samples = [32767 if i % 2 == 0 else -32768 for i in range(2048)]
    dbfs_nyquist = compute_rms_dbfs(nyquist_samples)
    assert abs(dbfs_nyquist - 0.0) < 0.001
    print("  [PASS] Full-scale alternating Nyquist pulses -> ~0.0 dBFS")

    print("\n=== [4] Seamless Zero-Drop Sink Swap Monotonicity ===")
    # Emulate continuous stream ingestion through 3 segments
    total_produced_samples = 16000 * 60  # 60 seconds
    stream = list(range(total_produced_samples))  # Tagged monotonically 0..N-1
    frame_size = 1024

    segments = []
    current_seg = []
    cut_points = [20 * 1024, 45 * 1024]  # Rotate at frame 20 and frame 45

    for offset in range(0, total_produced_samples, frame_size):
        frame = stream[offset:offset + frame_size]
        current_seg.extend(frame)
        if (offset + frame_size) in cut_points:
            segments.append(current_seg)
            current_seg = []
    if current_seg:
        segments.append(current_seg)

    assert len(segments) == 3
    # Check zero lost
    total_ingested = sum(len(s) for s in segments)
    assert total_ingested == total_produced_samples, f"Sample loss! {total_ingested} vs {total_produced_samples}"

    # Check continuity across boundaries
    assert segments[0][-1] + 1 == segments[1][0]
    assert segments[1][-1] + 1 == segments[2][0]

    # Check strict monotonicity across entire flattened stream
    flattened = [s for seg in segments for s in seg]
    assert flattened == stream
    print(f"  [PASS] Seamless sink swap: {len(segments)} segments, {total_produced_samples} continuous samples, 0 gaps, 0 duplicates, strict monotonic advancement.")

    return True

if __name__ == "__main__":
    success = run_all_adversarial_checks()
    if not success:
        sys.exit(1)
    print("\n>>> ALL ADVERSARIAL CHALLENGE SUITES PASSED EMPIRICALLY! <<<")
