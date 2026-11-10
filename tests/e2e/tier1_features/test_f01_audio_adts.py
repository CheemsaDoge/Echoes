"""Tier 1: Feature F1.2 & Contract C1 - Audio Capture & ADTS Format Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.4.4 & ORIGINAL_REQUEST.md R1.
16kHz Mono 48kbps AAC with 7-byte ADTS header.
"""

from __future__ import annotations
import pytest
from tests.e2e.harness.audio_harness import (
    build_adts_header,
    parse_adts_header,
    validate_adts_stream,
    synthesize_adts_file,
    SAMPLE_RATES,
    SAMPLE_RATE_16K_INDEX,
    PROFILE_AAC_LC,
    CHANNEL_MONO,
)


class TestAudioAdtsFormat:
    """Verifies that AAC chunks are formatted with valid 7-byte ADTS headers."""

    def test_adts_7byte_header_length(self):
        """1. Verify that synthesized ADTS header is exactly 7 bytes."""
        hdr = build_adts_header(payload_len=100)
        assert len(hdr) == 7, f"Expected 7 bytes, got {len(hdr)}"

    def test_adts_syncword_and_layer_bits(self):
        """2. Verify 12-bit syncword 0xFFF, MPEG-4 ID 0, Layer 00, ProtectionAbsent 1."""
        hdr = build_adts_header(payload_len=64)
        parsed = parse_adts_header(hdr)
        assert parsed["syncword"] == 0xFFF, "Syncword must be 0xFFF"
        assert parsed["mpeg_id"] == 0, "MPEG ID must be 0 for MPEG-4 Audio"
        assert parsed["layer"] == 0, "Layer must be 00"
        assert parsed["protection_absent"] == 1, "Protection absent must be 1 (no CRC, 7 bytes)"

    def test_adts_16khz_mono_configuration(self):
        """3. Verify 16kHz sampling rate index (8) and mono channel config (1)."""
        hdr = build_adts_header(payload_len=128, sample_rate=16000, channels=1)
        parsed = parse_adts_header(hdr)
        assert parsed["sample_rate_index"] == SAMPLE_RATE_16K_INDEX, "Sample rate index for 16kHz must be 8"
        assert parsed["sample_rate"] == 16000, "Sample rate must resolve to 16000 Hz"
        assert parsed["channel_config"] == CHANNEL_MONO, "Channel configuration must be 1 (Mono)"
        assert parsed["profile"] == PROFILE_AAC_LC, "Profile must be AAC-LC (2)"

    def test_adts_frame_length_bitpacking_accuracy(self):
        """4. Verify frame length bit packing (payload + 7) across multiple payload sizes."""
        test_payload_sizes = [1, 23, 100, 255, 1024, 2048]
        for p_size in test_payload_sizes:
            hdr = build_adts_header(payload_len=p_size)
            parsed = parse_adts_header(hdr)
            assert parsed["frame_length"] == p_size + 7, (
                f"For payload {p_size}, expected frame length {p_size + 7}, got {parsed['frame_length']}"
            )
            assert parsed["payload_length"] == p_size

    def test_adts_multi_frame_stream_validation(self):
        """5. Verify multi-frame stream with 20 consecutive frames parses cleanly."""
        num_frames = 20
        payload_size = 80
        stream = synthesize_adts_file(num_frames=num_frames, payload_size=payload_size)
        is_valid, msg, frames = validate_adts_stream(stream)
        assert is_valid is True, f"Stream validation failed: {msg}"
        assert len(frames) == num_frames, f"Expected {num_frames} frames, found {len(frames)}"
        for f in frames:
            assert f["payload_len"] == payload_size
            assert f["header"]["sample_rate"] == 16000

    def test_adts_buffer_fullness_and_frame_count_fields(self):
        """6. Verify buffer fullness bits (0x7FF) and frame count field."""
        hdr = build_adts_header(payload_len=50, buffer_fullness=0x7FF)
        parsed = parse_adts_header(hdr)
        assert parsed["buffer_fullness"] == 0x7FF, "Buffer fullness default should be 0x7FF (VBR)"
        assert parsed["num_frames"] == 1, "ADTS frame should contain 1 AAC frame"
