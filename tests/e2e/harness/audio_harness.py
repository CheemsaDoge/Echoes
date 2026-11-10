"""Audio harness for E2E tests conforming to HEMORY_SYSTEM_SPECIFICATION.md Section 2.4.4 & 2.4.5."""

from __future__ import annotations
import struct
import math
from typing import Any

# Sample rate table for MPEG-4 AAC
SAMPLE_RATES = [
    96000, 88200, 64000, 48000, 44100, 32000,
    24000, 22050, 16000, 12000, 11025, 8000, 7350
]
SAMPLE_RATE_16K_INDEX = 8  # 16000 Hz is at index 8 (0x08)
PROFILE_AAC_LC = 2  # AAC-LC in MPEG-4 spec; in ADTS 2-bit field it is (profile - 1) = 1
CHANNEL_MONO = 1  # 1 channel (mono)


def build_adts_header(
    payload_len: int,
    sample_rate: int = 16000,
    channels: int = 1,
    profile: int = 2,
    buffer_fullness: int = 0x7FF,
) -> bytes:
    """Pack a 7-byte MPEG-4 ADTS header per Section 2.4.4 of Hemory specification."""
    sample_rate_index = SAMPLE_RATES.index(sample_rate) if sample_rate in SAMPLE_RATES else 8
    frame_length = payload_len + 7

    header = bytearray(7)
    # Byte 0: Syncword bits 0..7 (all 1s)
    header[0] = 0xFF
    # Byte 1: Syncword bits 8..11 (0xF), ID=0 (MPEG-4), Layer=00, ProtectionAbsent=1 (0xF1)
    header[1] = 0xF1
    # Byte 2: profile (2 bits, profile - 1), sample_rate_index (4 bits), private=0, channel_config high bit
    adts_profile = (profile - 1) & 0x03
    header[2] = ((adts_profile << 6) | (sample_rate_index << 2) | ((channels >> 2) & 0x01)) & 0xFF
    # Byte 3: channel_config low bits (2 bits), orig/copy=0, home=0, copyright_id=0, copyright_start=0, frame_length high bits (2 bits)
    header[3] = (((channels & 0x03) << 6) | ((frame_length >> 11) & 0x03)) & 0xFF
    # Byte 4: frame_length middle 8 bits
    header[4] = ((frame_length >> 3) & 0xFF)
    # Byte 5: frame_length low 3 bits, buffer_fullness high 5 bits
    header[5] = (((frame_length & 0x07) << 5) | ((buffer_fullness >> 6) & 0x1F)) & 0xFF
    # Byte 6: buffer_fullness low 6 bits, num_aac_frames_minus_1 (2 bits, 0 for 1 frame)
    header[6] = (((buffer_fullness & 0x3F) << 2) | 0x00) & 0xFF

    return bytes(header)


def parse_adts_header(header_bytes: bytes) -> dict[str, Any]:
    """Parse and validate a 7-byte ADTS header."""
    if len(header_bytes) < 7:
        raise ValueError(f"ADTS header requires 7 bytes, got {len(header_bytes)}")

    b0, b1, b2, b3, b4, b5, b6 = header_bytes[:7]

    # Verify syncword 12 bits: 0xFFF
    syncword = (b0 << 4) | (b1 >> 4)
    if syncword != 0xFFF:
        raise ValueError(f"Invalid ADTS syncword: 0x{syncword:03X}, expected 0xFFF")

    mpeg_id = (b1 >> 3) & 0x01  # 0: MPEG-4, 1: MPEG-2
    layer = (b1 >> 1) & 0x03  # 00
    protection_absent = b1 & 0x01  # 1: no CRC (7-byte header), 0: CRC present (9-byte)

    profile_field = (b2 >> 6) & 0x03
    profile = profile_field + 1
    sample_rate_index = (b2 >> 2) & 0x0F
    sample_rate = SAMPLE_RATES[sample_rate_index] if sample_rate_index < len(SAMPLE_RATES) else None

    channel_config = ((b2 & 0x01) << 2) | ((b3 >> 6) & 0x03)

    frame_length = (((b3 & 0x03) << 11) | (b4 << 3) | ((b5 >> 5) & 0x07))
    buffer_fullness = (((b5 & 0x1F) << 6) | ((b6 >> 2) & 0x3F))
    num_frames = (b6 & 0x03) + 1

    return {
        "syncword": syncword,
        "mpeg_id": mpeg_id,
        "layer": layer,
        "protection_absent": protection_absent,
        "profile": profile,
        "sample_rate_index": sample_rate_index,
        "sample_rate": sample_rate,
        "channel_config": channel_config,
        "frame_length": frame_length,
        "payload_length": frame_length - 7,
        "buffer_fullness": buffer_fullness,
        "num_frames": num_frames,
    }


def validate_adts_stream(stream: bytes) -> tuple[bool, str, list[dict[str, Any]]]:
    """Validate that a stream consists entirely of valid concatenated ADTS frames."""
    if not stream:
        return False, "Stream is empty (0 bytes)", []

    offset = 0
    frames = []
    stream_len = len(stream)

    while offset < stream_len:
        if offset + 7 > stream_len:
            return False, f"Truncated header at offset {offset}: remaining bytes {stream_len - offset} < 7", frames

        header_bytes = stream[offset:offset + 7]
        try:
            parsed = parse_adts_header(header_bytes)
        except Exception as e:
            return False, f"Corrupted ADTS header at offset {offset}: {e}", frames

        frame_len = parsed["frame_length"]
        if frame_len < 7:
            return False, f"Invalid frame length {frame_len} < 7 at offset {offset}", frames

        if offset + frame_len > stream_len:
            return False, f"Truncated frame at offset {offset}: expected {frame_len} bytes, got {stream_len - offset}", frames

        frames.append({
            "offset": offset,
            "header": parsed,
            "payload_len": frame_len - 7,
        })
        offset += frame_len

    return True, "Valid ADTS stream", frames


def synthesize_adts_file(
    num_frames: int = 10,
    payload_size: int = 120,
    sample_rate: int = 16000,
    channels: int = 1,
) -> bytes:
    """Generate a synthesized binary ADTS AAC stream with given number of frames."""
    data = bytearray()
    for i in range(num_frames):
        # Synthetic payload with some pseudo audio variation
        payload = bytes((x * (i + 1)) % 256 for x in range(payload_size))
        hdr = build_adts_header(len(payload), sample_rate=sample_rate, channels=channels)
        data.extend(hdr)
        data.extend(payload)
    return bytes(data)


def evaluate_rollover(
    duration_ms: int,
    silence_duration_ms: int = 0,
    dbfs: float = -20.0,
    is_interrupted: bool = False,
    is_manual_stop: bool = False,
) -> tuple[bool, str | None]:
    """
    Elastic Rollover Decision Engine:
    Rule R2 from ORIGINAL_REQUEST.md & Section 2.4.5:
    - Duration < 26 minutes (1,560,000 ms): no silence cut.
    - 26 min <= Duration < 30 minutes (1,800,000 ms):
        Cut if silence >= 800ms AND dBFS <= -40.0 dBFS -> trigger 'silence_window'
    - Duration >= 30 minutes (1,800,000 ms):
        Hard limit reached -> trigger 'hard_limit'
    - External interruption -> 'interruption'
    - Manual user stop -> 'manual_stop'
    """
    if is_manual_stop:
        return True, "manual_stop"
    if is_interrupted:
        return True, "interruption"

    # Thresholds in milliseconds
    window_start_ms = 26 * 60 * 1000  # 1,560,000 ms
    hard_limit_ms = 30 * 60 * 1000    # 1,800,000 ms

    if duration_ms >= hard_limit_ms:
        return True, "hard_limit"

    if duration_ms >= window_start_ms:
        if silence_duration_ms >= 800 and dbfs <= -40.0:
            return True, "silence_window"

    return False, None
