#!/usr/bin/env python3
"""
Empirical Adversarial Mathematical & Boundary Challenge Harness for Milestone M3.
Evaluates:
  1. Razor-Edge Threshold Boundaries (0.78, 0.72, 0.60) & Decision Routing Monotonicity
  2. Spherical Centroid Mathematical Invariance (||C||_2 = 1.0 +- 1e-6) across 1,000 Updates
  3. Orthonormal Projection Isometry & Machine-Precision Angle Preservation (Delta < 1e-5)
  4. Acoustic Pre-qualification Under Extreme Pathological Inputs (Silence, Clipping, Overflow)
  5. Valid AAC ADTS Bitstream Synthesis & Decoder Integrity (MPEG-4 AAC-LC 16kHz Mono)
  6. Contract C6 3-Tier Dynamic Fallback & Multi-Tenant Boundary Isolation
"""

from __future__ import annotations

import io
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure backend modules are importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import numpy as np
from fastapi.testclient import TestClient

from echoledger.api import create_app
from echoledger.config import Settings
from echoledger.voiceprint import (
    TAU_IDENTIFICATION,
    TAU_CLUSTERING,
    TAU_REJECTION,
    MARGIN_IDENTIFICATION,
    MIN_SPEECH_DURATION_SEC,
    MIN_SNR_DB,
    MAX_CLIPPING_RATIO,
    EMBEDDING_DIM,
    EMBEDDING_BYTES,
    unit_normalize,
    pack_embedding,
    unpack_embedding,
    cosine_similarity,
    get_orthonormal_projection,
    project_to_512,
    generate_synthetic_embedding,
    calculate_snr,
    calculate_clipping_ratio,
    check_acoustic_qualification,
    compute_centroid,
    update_centroid_incremental,
    update_centroid_ema,
    match_enrolled_speaker,
    cluster_segment,
    hierarchical_cluster_segments,
    build_adts_header,
    generate_synthetic_aac,
    decode_audio_to_pcm16,
)
from echoledger.speakers import resolve_speaker_name, SessionSpeakerResolver


def make_vector_with_cosine(u: np.ndarray, target_sim: float, seed: int = 123) -> np.ndarray:
    """Construct a unit vector v such that dot(u, v) == target_sim within floating tolerance."""
    u_norm = u / np.linalg.norm(u)
    rng = np.random.default_rng(seed)
    random_vec = rng.standard_normal(len(u_norm))
    # Gram-Schmidt orthogonalization
    proj = np.dot(random_vec, u_norm) * u_norm
    ortho = random_vec - proj
    ortho_norm = np.linalg.norm(ortho)
    if ortho_norm < 1e-12:
        ortho = np.roll(u_norm, 1)
        ortho = ortho - np.dot(ortho, u_norm) * u_norm
        ortho_norm = np.linalg.norm(ortho)
    ortho = ortho / ortho_norm

    target_sim = float(np.clip(target_sim, -1.0, 1.0))
    v = target_sim * u_norm + np.sqrt(max(0.0, 1.0 - target_sim**2)) * ortho
    return (v / np.linalg.norm(v)).astype(np.float32)


# =============================================================================
# SUITE 1: Razor-Edge Threshold Boundaries & Decision Routing
# =============================================================================

def test_suite_threshold_boundaries() -> None:
    print("\n[Suite 1] Razor-Edge Threshold Boundaries & Decision Routing...")
    rng = np.random.default_rng(20261110)
    base_u = unit_normalize(rng.standard_normal(EMBEDDING_DIM))

    # 1.1 Identification boundary (tau = 0.78)
    spk_profile = {"speaker_id": "spk_alpha", "centroid": base_u}
    # Competitor with low similarity to guarantee margin >= 0.05
    spk_competitor = {"speaker_id": "spk_beta", "centroid": unit_normalize(-base_u)}
    enrolled = [spk_profile, spk_competitor]

    v_above_078 = make_vector_with_cosine(base_u, 0.78001)
    res_above = match_enrolled_speaker(v_above_078, enrolled, threshold=0.78, margin=0.05)
    assert res_above["accepted"] is True, f"Expected accepted=True at 0.78001, got {res_above}"
    assert res_above["speaker_id"] == "spk_alpha"

    v_exact_078 = make_vector_with_cosine(base_u, 0.78000)
    res_exact = match_enrolled_speaker(v_exact_078, enrolled, threshold=0.78, margin=0.05)
    assert res_exact["accepted"] is True, f"Expected accepted=True at exact 0.78000, got {res_exact}"

    v_below_078 = make_vector_with_cosine(base_u, 0.77999)
    res_below = match_enrolled_speaker(v_below_078, enrolled, threshold=0.78, margin=0.05)
    assert res_below["accepted"] is False, f"Expected accepted=False at 0.77999, got {res_below}"
    assert res_below["speaker_id"] is None

    # 1.2 Identification margin boundary (delta = 0.05)
    # Target candidate has similarity 0.85
    v_query = make_vector_with_cosine(base_u, 0.85)
    # Construct competitor vectors relative to v_query
    # Margin = 0.85 - sim_competitor
    # If sim_competitor = 0.80001 -> delta = 0.04999 < 0.05 -> rejected
    # If sim_competitor = 0.79999 -> delta = 0.05001 > 0.05 -> accepted
    v_comp_close = make_vector_with_cosine(v_query, 0.80001, seed=456)
    v_comp_far = make_vector_with_cosine(v_query, 0.79999, seed=789)

    enrolled_rejected_margin = [
        {"speaker_id": "spk_1", "centroid": base_u},
        {"speaker_id": "spk_2", "centroid": v_comp_close},
    ]
    res_margin_fail = match_enrolled_speaker(v_query, enrolled_rejected_margin, threshold=0.78, margin=0.05)
    assert res_margin_fail["accepted"] is False, f"Margin < 0.05 should be rejected, got {res_margin_fail}"

    enrolled_accepted_margin = [
        {"speaker_id": "spk_1", "centroid": base_u},
        {"speaker_id": "spk_2", "centroid": v_comp_far},
    ]
    res_margin_pass = match_enrolled_speaker(v_query, enrolled_accepted_margin, threshold=0.78, margin=0.05)
    assert res_margin_pass["accepted"] is True, f"Margin > 0.05 should be accepted, got {res_margin_pass}"

    # 1.3 Clustering merge boundary (tau = 0.72)
    pending_clusters = [{"cluster_id": "cls_01", "embedding_blob": pack_embedding(base_u)}]

    v_merge_above = make_vector_with_cosine(base_u, 0.72001)
    act_above = cluster_segment(v_merge_above, pending_clusters, threshold=0.72, rejection_floor=0.60)
    assert act_above["action"] == "merge" and act_above["cluster_id"] == "cls_01", f"Failed at 0.72001: {act_above}"

    v_merge_exact = make_vector_with_cosine(base_u, 0.72000)
    act_exact = cluster_segment(v_merge_exact, pending_clusters, threshold=0.72, rejection_floor=0.60)
    assert act_exact["action"] == "merge" and act_exact["cluster_id"] == "cls_01", f"Failed at exact 0.72000: {act_exact}"

    v_merge_below = make_vector_with_cosine(base_u, 0.71999)
    act_below = cluster_segment(v_merge_below, pending_clusters, threshold=0.72, rejection_floor=0.60)
    assert act_below["action"] == "new_cluster" and act_below["cluster_id"] is None, f"Failed at 0.71999: {act_below}"

    # 1.4 Rejection floor boundary (tau = 0.60)
    v_floor_above = make_vector_with_cosine(base_u, 0.60001)
    act_floor_above = cluster_segment(v_floor_above, pending_clusters, threshold=0.72, rejection_floor=0.60)
    assert act_floor_above["action"] == "new_cluster", f"Failed at 0.60001: {act_floor_above}"

    v_floor_exact = make_vector_with_cosine(base_u, 0.60000)
    act_floor_exact = cluster_segment(v_floor_exact, pending_clusters, threshold=0.72, rejection_floor=0.60)
    assert act_floor_exact["action"] == "new_cluster", f"Failed at exact 0.60000: {act_floor_exact}"

    v_floor_below = make_vector_with_cosine(base_u, 0.59999)
    act_floor_below = cluster_segment(v_floor_below, pending_clusters, threshold=0.72, rejection_floor=0.60)
    assert act_floor_below["action"] == "reject", f"Failed at 0.59999: {act_floor_below}"

    # 1.5 5,000-Point Fine-Grained Sweep to Prove Decision Monotonicity
    print("  -> Sweeping 5,000 points across [-0.10, 1.00] for monotonic interval partition...")
    sim_sweep = np.linspace(-0.10, 1.00, 5000)
    for s in sim_sweep:
        v_test = make_vector_with_cosine(base_u, s, seed=int(abs(s) * 100000) + 1)
        decision = cluster_segment(v_test, pending_clusters, threshold=0.72, rejection_floor=0.60)
        action = decision["action"]
        actual_sim = decision["similarity"]
        if actual_sim >= 0.72:
            assert action == "merge", f"Monotonicity error at sim={actual_sim:.6f}: got {action}"
        elif actual_sim >= 0.60:
            assert action == "new_cluster", f"Monotonicity error at sim={actual_sim:.6f}: got {action}"
        else:
            assert action == "reject", f"Monotonicity error at sim={actual_sim:.6f}: got {action}"

    print("  [PASS] Suite 1: Razor-Edge Threshold Boundaries verified with zero inversion or rounding error.")


# =============================================================================
# SUITE 2: Spherical Centroid Mathematical Invariance & Numerical Stability
# =============================================================================

def test_suite_centroid_mathematical_invariance() -> None:
    print("\n[Suite 2] Spherical Centroid Mathematical Invariance & Numerical Stability...")
    rng = np.random.default_rng(42)

    # 2.1 1,000 Sequential Incremental Updates on S^{511}
    current_centroid = unit_normalize(rng.standard_normal(EMBEDDING_DIM))
    max_norm_err = 0.0

    for step in range(1, 1001):
        sample_vec = unit_normalize(rng.standard_normal(EMBEDDING_DIM))
        current_centroid = update_centroid_incremental(current_centroid, step, sample_vec)
        norm = float(np.linalg.norm(current_centroid))
        err = abs(1.0 - norm)
        if err > max_norm_err:
            max_norm_err = err
        assert err < 1e-6, f"Norm drift at step {step}: norm={norm:.8f}, err={err:.2e}"
        assert np.isfinite(current_centroid).all(), f"Non-finite value at step {step}"

    print(f"  -> 1,000 incremental updates completed: max ||C||_2 error = {max_norm_err:.3e} (limit < 1e-6)")

    # 2.2 Extreme Sample Count Scaling: N = 10^1 .. 10^8
    c_base = unit_normalize(rng.standard_normal(EMBEDDING_DIM))
    for exp in range(1, 9):
        n = 10**exp
        sample = unit_normalize(rng.standard_normal(EMBEDDING_DIM))
        c_next = update_centroid_incremental(c_base, n, sample)
        norm_next = float(np.linalg.norm(c_next))
        assert abs(1.0 - norm_next) < 1e-6, f"Scaling breakdown at N=10^{exp}: norm={norm_next:.8f}"
        assert np.isfinite(c_next).all(), f"Non-finite output at N=10^{exp}"

    # 2.3 Pathological Antipodal Collapse Handling
    u = unit_normalize(rng.standard_normal(EMBEDDING_DIM))
    antipodal_e = -u
    # At N=1: S = 1*u + (-u) = 0
    safe_c = update_centroid_incremental(u, 1, antipodal_e)
    assert np.isfinite(safe_c).all(), "Antipodal update produced non-finite values"
    assert abs(1.0 - np.linalg.norm(safe_c)) < 1e-6, "Antipodal fallback is not unit-normalized"

    # Nearly antipodal: u + (-u + 1e-13 * ortho)
    ortho = np.roll(u, 1)
    ortho = ortho - np.dot(ortho, u) * u
    ortho = ortho / np.linalg.norm(ortho)
    near_antipodal_e = unit_normalize(-u + 1e-8 * ortho)
    safe_near = update_centroid_incremental(u, 1, near_antipodal_e)
    assert np.isfinite(safe_near).all()
    assert abs(1.0 - np.linalg.norm(safe_near)) < 1e-6

    # 2.4 Dynamic Momentum EMA Updates (1,000 steps)
    ema_centroid = unit_normalize(rng.standard_normal(EMBEDDING_DIM))
    max_ema_err = 0.0
    for step in range(1, 1001):
        incoming = unit_normalize(rng.standard_normal(EMBEDDING_DIM))
        ema_centroid = update_centroid_ema(ema_centroid, incoming, sample_count=step)
        norm_ema = float(np.linalg.norm(ema_centroid))
        err_ema = abs(1.0 - norm_ema)
        if err_ema > max_ema_err:
            max_ema_err = err_ema
        assert err_ema < 1e-6, f"EMA drift at step {step}: norm={norm_ema:.8f}"
        assert np.isfinite(ema_centroid).all()

    print(f"  -> 1,000 EMA updates completed: max ||C_EMA||_2 error = {max_ema_err:.3e} (limit < 1e-6)")

    # 2.5 IEEE-754 32-bit Float Binary Packing/Unpacking Invariance
    for _ in range(500):
        v_rand = unit_normalize(rng.standard_normal(EMBEDDING_DIM))
        blob = pack_embedding(v_rand)
        assert len(blob) == EMBEDDING_BYTES == 2048, f"Blob size mismatch: {len(blob)}"
        v_unpacked = unpack_embedding(blob)
        assert abs(1.0 - np.linalg.norm(v_unpacked)) < 1e-6
        max_diff = float(np.max(np.abs(v_rand - v_unpacked)))
        assert max_diff < 1e-7, f"Float32 pack/unpack precision loss: {max_diff:.2e}"

    print("  [PASS] Suite 2: Spherical Centroid Mathematical Invariance & Stability verified.")


# =============================================================================
# SUITE 3: Orthonormal Projection Isometry & Angle Preservation
# =============================================================================

def test_suite_orthonormal_isometry() -> None:
    print("\n[Suite 3] Orthonormal Projection Isometry & Machine-Precision Angle Preservation...")
    rng = np.random.default_rng(2026)

    test_dims = [16, 64, 128, 192, 256, 384]

    for dim_in in test_dims:
        W = get_orthonormal_projection(dim_in, EMBEDDING_DIM)
        assert W.shape == (dim_in, EMBEDDING_DIM), f"Matrix shape mismatch: {W.shape}"

        # 3.1 Orthonormality check: W @ W.T == I_{dim_in}
        W_WT = W @ W.T
        eye = np.eye(dim_in, dtype=np.float32)
        diff_eye = float(np.max(np.abs(W_WT - eye)))
        assert diff_eye < 1e-5, f"Orthonormality violation for dim={dim_in}: max error = {diff_eye:.2e}"

        # 3.2 Isometry and Cosine Similarity Preservation for 2,500 random pairs
        max_cos_diff = 0.0
        max_norm_diff = 0.0

        for _ in range(2500):
            u = unit_normalize(rng.standard_normal(dim_in))
            v = unit_normalize(rng.standard_normal(dim_in))

            cos_orig = cosine_similarity(u, v)

            u_proj = project_to_512(u)
            v_proj = project_to_512(v)

            norm_u = float(np.linalg.norm(u_proj))
            norm_v = float(np.linalg.norm(v_proj))
            max_norm_diff = max(max_norm_diff, abs(1.0 - norm_u), abs(1.0 - norm_v))

            cos_proj = cosine_similarity(u_proj, v_proj)
            diff_cos = abs(cos_orig - cos_proj)
            if diff_cos > max_cos_diff:
                max_cos_diff = diff_cos

            assert diff_cos < 1e-5, (
                f"Isometry violation: dim={dim_in}, orig={cos_orig:.7f}, proj={cos_proj:.7f}, diff={diff_cos:.2e}"
            )

        print(
            f"  -> dim {dim_in:3d} -> 512: max cosine deviation across 2,500 pairs = {max_cos_diff:.3e}, "
            f"max norm deviation = {max_norm_diff:.3e}"
        )

    print("  [PASS] Suite 3: Orthonormal Projection Isometry rigorously verified.")


# =============================================================================
# SUITE 4: Acoustic Qualification Under Extreme Pathological Inputs
# =============================================================================

def test_suite_acoustic_prequalification() -> None:
    print("\n[Suite 4] Acoustic Pre-qualification Under Extreme Pathological Inputs...")

    # 4.1 Digital silence (all zeros, 48,000 samples @ 16kHz = 3.0s)
    silence = np.zeros(48000, dtype=np.int16)
    snr_silence = calculate_snr(silence)
    assert snr_silence == 0.0, f"Expected SNR=0.0 for pure silence, got {snr_silence}"
    qual_silence, reason = check_acoustic_qualification(silence)
    assert qual_silence is False, "Pure silence must not qualify"
    assert "SNR" in reason

    # 4.2 Sub-frame and duration boundary checks
    for length in [0, 1, 15, 319, 320, 16000, 47999]:
        short_buf = np.zeros(length, dtype=np.int16)
        qual_short, reason_short = check_acoustic_qualification(short_buf)
        assert qual_short is False, f"Length {length} should fail qualification"
        assert "duration" in reason_short.lower()

    # 4.3 int16 Negative Full-Scale Saturation (-32768) Clipping Overflow Test
    # In two's complement int16, abs(-32768) overflows if not converted to int32/float
    overflow_samples = np.array([-32768, -32767, 32767, 0, 100], dtype=np.int16)
    clip_ratio = calculate_clipping_ratio(overflow_samples, threshold=32760)
    assert clip_ratio == 0.6, f"Expected 3/5=0.6 clipping, got {clip_ratio}"

    # 4.4 Razor-Edge Clipping Threshold: 0.005 (0.5%)
    # Total samples = 48,000. 0.005 * 48000 = 240 clipped samples.
    # 239 clipped: 239 / 48000 = 0.00497916... < 0.005 -> Passes clipping check
    # 241 clipped: 241 / 48000 = 0.00502083... > 0.005 -> Fails clipping check
    t = np.linspace(0, 3.0, 48000, endpoint=False)
    # Sine wave with amplitude 10,000 (SNR > 20dB)
    clean_audio = (10000.0 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    assert calculate_clipping_ratio(clean_audio) == 0.0

    # 239 clipped samples
    audio_239 = clean_audio.copy()
    audio_239[:239] = 32767
    ratio_239 = calculate_clipping_ratio(audio_239)
    assert ratio_239 < MAX_CLIPPING_RATIO
    qual_239, reason_239 = check_acoustic_qualification(audio_239)
    assert qual_239 is True, f"239 clipped samples should qualify, got {reason_239}"

    # 241 clipped samples
    audio_241 = clean_audio.copy()
    audio_241[:241] = 32767
    ratio_241 = calculate_clipping_ratio(audio_241)
    assert ratio_241 >= MAX_CLIPPING_RATIO
    qual_241, reason_241 = check_acoustic_qualification(audio_241)
    assert qual_241 is False, "241 clipped samples should fail clipping ratio"
    assert "clipping" in reason_241.lower()

    # 4.5 SNR Edge Case: 11.9 dB vs 12.1 dB
    # Create controlled signal where signal frames = S, noise frames = N
    # 48,000 samples = 150 frames of 320 samples.
    # Signal frames (top 20% = 30 frames) vs noise frames (bottom 20% = 30 frames).
    # SNR = 10 * log10(S / N). To get 11.9 dB: S/N = 10^(1.19) ~ 15.488
    # To get 12.1 dB: S/N = 10^(1.21) ~ 16.218
    controlled_119 = np.zeros(48000, dtype=np.int16)
    # 30 noise frames (9,600 samples) with amplitude 1000 -> RMS^2 = 1,000,000 / 2 = 500,000
    t_noise = np.linspace(0, 9600 / 16000, 9600, endpoint=False)
    controlled_119[:9600] = (1000.0 * np.sin(2 * np.pi * 500 * t_noise)).astype(np.int16)
    # 30 signal frames (9,600 samples)
    t_sig = np.linspace(0, 9600 / 16000, 9600, endpoint=False)
    # Amplitude A = 1000 * sqrt(15.488) ~ 3935
    controlled_119[-9600:] = (3935.0 * np.sin(2 * np.pi * 500 * t_sig)).astype(np.int16)
    # Fill middle frames with intermediate amplitude 2000
    controlled_119[9600:-9600] = (2000.0 * np.sin(2 * np.pi * 500 * np.linspace(0, 28800/16000, 28800, endpoint=False))).astype(np.int16)

    snr_119 = calculate_snr(controlled_119)
    assert 11.7 <= snr_119 < 12.0, f"Expected SNR near 11.9, got {snr_119:.2f}"
    qual_119, reason_119 = check_acoustic_qualification(controlled_119)
    assert qual_119 is False, f"SNR < 12.0dB must fail: {reason_119}"

    # Now raise signal amplitude to 4050 -> S/N ~ 16.40 -> SNR ~ 12.15 dB
    controlled_121 = controlled_119.copy()
    controlled_121[-9600:] = (4050.0 * np.sin(2 * np.pi * 500 * t_sig)).astype(np.int16)
    snr_121 = calculate_snr(controlled_121)
    assert snr_121 >= 12.0, f"Expected SNR >= 12.0, got {snr_121:.2f}"
    qual_121, reason_121 = check_acoustic_qualification(controlled_121)
    assert qual_121 is True, f"SNR >= 12.0dB must qualify, got {reason_121}"

    print("  [PASS] Suite 4: Acoustic Pre-qualification Under Extreme Pathological Inputs verified.")


# =============================================================================
# SUITE 5: Valid AAC ADTS Bitstream Synthesis & Decoder Integrity
# =============================================================================

def test_suite_adts_bitstream_and_audio_preview() -> None:
    print("\n[Suite 5] Valid AAC ADTS Bitstream Synthesis & Decoder Integrity...")

    # 5.1 Bit-level ADTS Header Syntax
    payload_len = 128
    header = build_adts_header(payload_len, sample_rate=16000, channels=1, profile=2)
    assert len(header) == 7, f"Header length must be 7 bytes, got {len(header)}"

    # Parse bits
    b0, b1, b2, b3, b4, b5, b6 = header
    syncword = (b0 << 4) | (b1 >> 4)
    assert syncword == 0xFFF, f"Syncword must be 0xFFF, got 0x{syncword:03X}"

    id_bit = (b1 >> 3) & 0x01
    assert id_bit == 0, f"MPEG-4 ID must be 0, got {id_bit}"

    layer = (b1 >> 1) & 0x03
    assert layer == 0, f"Layer must be 00, got {layer}"

    protection_absent = b1 & 0x01
    assert protection_absent == 1, f"Protection absent must be 1 (no CRC), got {protection_absent}"

    adts_profile = (b2 >> 6) & 0x03
    assert adts_profile == 1, f"Profile must be 1 (AAC-LC profile-1), got {adts_profile}"

    sr_idx = (b2 >> 2) & 0x0F
    assert sr_idx == 8, f"Sample rate index for 16kHz must be 8, got {sr_idx}"

    ch_cfg = ((b2 & 0x01) << 2) | ((b3 >> 6) & 0x03)
    assert ch_cfg == 1, f"Channel configuration must be 1 (Mono), got {ch_cfg}"

    frame_len = ((b3 & 0x03) << 11) | (b4 << 3) | ((b5 >> 5) & 0x07)
    assert frame_len == payload_len + 7, f"Frame length mismatch: expected {payload_len + 7}, got {frame_len}"

    # 5.2 Synthetic AAC Stream Synthesis & Header Walk
    synth_aac = generate_synthetic_aac(duration_sec=5.0, sample_rate=16000)
    assert len(synth_aac) > 100, "Synthetic AAC stream is empty or too short"

    # Walk all frames in the stream
    offset = 0
    frame_count = 0
    while offset + 7 <= len(synth_aac):
        b0, b1 = synth_aac[offset], synth_aac[offset + 1]
        assert (b0 == 0xFF) and ((b1 & 0xF0) == 0xF0), f"Lost sync at byte offset {offset}"
        flen = ((synth_aac[offset + 3] & 0x03) << 11) | (synth_aac[offset + 4] << 3) | ((synth_aac[offset + 5] >> 5) & 0x07)
        assert flen >= 7, f"Invalid frame length: {flen}"
        assert offset + flen <= len(synth_aac), f"Frame length {flen} overruns stream buffer at offset {offset}"
        offset += flen
        frame_count += 1

    assert frame_count >= 50, f"Expected >= 50 AAC frames for 5s audio, got {frame_count}"
    print(f"  -> Successfully walked {frame_count} continuous ADTS frames without syntax error.")

    # 5.3 External Decoder Interoperability (FFmpeg / decode_audio_to_pcm16)
    pcm = decode_audio_to_pcm16(synth_aac)
    assert len(pcm) > 0, "Failed to decode synthesized AAC stream to PCM16"
    decoded_duration = len(pcm) / 16000.0
    assert 4.5 <= decoded_duration <= 5.5, f"Expected ~5s decoded audio, got {decoded_duration:.2f}s"
    print(f"  -> Decoded PCM16 duration: {decoded_duration:.2f}s ({len(pcm)} samples @ 16kHz).")

    # 5.4 REST API Endpoint Test: GET /clusters/{cluster_id}/audio_preview
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(
            data_dir=Path(tmpdir),
            api_token="test_token_adts_boundary_32_chars_long",
            mcp_token="test_mcp_distinct_token_32_chars_long",
            asr_provider="mock",
            vad_provider="energy_demo",
            run_worker=False,
        )
        app = create_app(settings)
        db = app.state.pipeline.db
        vault_id = "vlt_adts_test"
        with db.connect(immediate=True) as conn:
            conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name) VALUES (?, ?, ?)", (vault_id, "ten_default", "Test Vault"))

        emb = pack_embedding(generate_synthetic_embedding("adts_speaker"))
        db.create_voiceprint_cluster("cls_adts_01", "ten_default", vault_id, embedding_blob=emb, confidence_score=0.92)

        with TestClient(app) as client:
            headers = {
                "Authorization": "Bearer test_token_adts_boundary_32_chars_long",
                "x-tenant-id": "ten_default",
                "x-vault-id": vault_id,
            }
            res = client.get("/clusters/cls_adts_01/audio_preview", headers=headers)
            assert res.status_code == 200, f"Expected 200 OK, got {res.status_code}: {res.text}"
            assert res.headers.get("content-type") == "audio/aac"
            stream_bytes = res.content
            assert len(stream_bytes) > 100
            assert stream_bytes[0] == 0xFF
            assert (stream_bytes[1] & 0xF0) == 0xF0

            # Decode returned stream
            decoded_api_pcm = decode_audio_to_pcm16(stream_bytes)
            assert len(decoded_api_pcm) >= 48000, f"Decoded stream too short: {len(decoded_api_pcm)} samples"

    print("  [PASS] Suite 5: Valid AAC ADTS Bitstream Synthesis & Decoder Integrity verified.")


# =============================================================================
# SUITE 6: Contract C6 3-Tier Fallback & Multi-Tenant Boundary Challenge
# =============================================================================

def test_suite_contract_c6_and_multi_tenant_boundaries() -> None:
    print("\n[Suite 6] Contract C6 3-Tier Fallback & Multi-Tenant Boundary Challenge...")

    # 6.1 Fallback Hierarchy Tests
    context = {
        "speaker_profiles": {
            "spk_enrolled": {"name": "Alice Wonderland"},
            "spk_nameless": {"name": ""},
        },
        "active_roster": {
            "spk_suspect": {"suspected_name": "Bob Builder"},
            "cls_cluster_suspect": {"suspected_name": "Charlie Chaplin"},
        },
        "unknown_speaker_index": 7,
    }

    # Tier 1: Enrolled Named Profile
    res_t1 = resolve_speaker_name("spk_enrolled", context)
    assert res_t1 == "Alice Wonderland", f"Tier 1 failure: {res_t1}"

    # Tier 2: Profile without name or unknown id, but in active roster
    res_t2 = resolve_speaker_name("spk_suspect", context)
    assert res_t2 == "Bob Builder", f"Tier 2 failure: {res_t2}"

    # Tier 3: Neither enrolled nor rostered -> Speaker {unknown_speaker_index}
    res_t3 = resolve_speaker_name("spk_unknown_stranger", context)
    assert res_t3 == "Speaker 7", f"Tier 3 failure: {res_t3}"

    # 6.2 SessionSpeakerResolver Deterministic Monotonic Indexing
    resolver = SessionSpeakerResolver(
        speaker_profiles={"spk_01": {"name": "Diana Prince"}},
        active_roster={"cls_pending_a": {"suspected_name": "Edward Cullen"}},
    )

    assert resolver.resolve(speaker_id="spk_01") == "Diana Prince"
    assert resolver.resolve(cluster_id="cls_pending_a") == "Edward Cullen"

    # Anonymous cluster 1
    s1_first = resolver.resolve(cluster_id="cls_anon_101")
    assert s1_first == "Speaker 1"
    # Idempotent re-resolution of same anonymous cluster
    s1_second = resolver.resolve(cluster_id="cls_anon_101")
    assert s1_second == "Speaker 1"

    # Anonymous cluster 2
    s2 = resolver.resolve(cluster_id="cls_anon_102")
    assert s2 == "Speaker 2"

    # Unlabeled utterance
    s3 = resolver.resolve()
    assert s3 == "Speaker 3"

    # 6.3 Multi-Tenant 404 Boundary Isolation
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(
            data_dir=Path(tmpdir),
            api_token="test_token_isolation_32_chars_long",
            mcp_token="test_mcp_distinct_token_32_chars_long",
            asr_provider="mock",
            vad_provider="energy_demo",
            run_worker=False,
        )
        app = create_app(settings)
        db = app.state.pipeline.db

        with db.connect(immediate=True) as conn:
            conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug, plan_tier, status) VALUES ('ten_alpha', 'Tenant Alpha', 'ten-alpha', 'free', 'active')")
            conn.execute("INSERT OR IGNORE INTO tenants (tenant_id, name, slug, plan_tier, status) VALUES ('ten_beta', 'Tenant Beta', 'ten-beta', 'free', 'active')")
            conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, password_hash) VALUES ('usr_alpha', 'ten_alpha', 'alpha@test.com', 'hash')")
            conn.execute("INSERT OR IGNORE INTO users (user_id, tenant_id, email, password_hash) VALUES ('usr_beta', 'ten_beta', 'beta@test.com', 'hash')")
            conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name) VALUES ('vlt_alpha', 'ten_alpha', 'Vault Alpha')")
            conn.execute("INSERT OR IGNORE INTO vaults (vault_id, tenant_id, name) VALUES ('vlt_beta', 'ten_beta', 'Vault Beta')")

        emb = pack_embedding(generate_synthetic_embedding("tenant_speaker"))
        db.create_voiceprint_cluster("cls_vault_alpha", "ten_alpha", "vlt_alpha", embedding_blob=emb, confidence_score=0.85)
        db.create_speaker_profile("spk_vault_alpha", "ten_alpha", "vlt_alpha", "usr_alpha", name="Alpha Speaker")

        with TestClient(app) as client:
            # Request from Vault Beta attempting to read Vault Alpha cluster
            beta_headers = {
                "Authorization": "Bearer test_token_isolation_32_chars_long",
                "x-tenant-id": "ten_beta",
                "x-vault-id": "vlt_beta",
                "x-user-id": "usr_beta",
            }
            res_cls_404 = client.get("/clusters/cls_vault_alpha", headers=beta_headers)
            assert res_cls_404.status_code == 404, f"Cross-vault cluster read leaked existence: {res_cls_404.status_code}"

            res_spk_404 = client.get("/speakers/spk_vault_alpha", headers=beta_headers)
            assert res_spk_404.status_code == 404, f"Cross-vault speaker read leaked existence: {res_spk_404.status_code}"

            # Cross-vault cluster mutation
            patch_res = client.patch(
                "/clusters/cls_vault_alpha",
                headers=beta_headers,
                json={"action": "mark_new", "name": "Illegal Alien"},
            )
            assert patch_res.status_code == 404, f"Cross-vault patch leaked existence: {patch_res.status_code}"

    print("  [PASS] Suite 6: Contract C6 3-Tier Fallback & Multi-Tenant Boundary Isolation verified.")


# =============================================================================
# MAIN RUNNER
# =============================================================================

def main() -> int:
    print("=" * 78)
    print("  MILICENT M3 ADVERSARIAL MATHEMATICAL & BOUNDARY CHALLENGE HARNESS")
    print("=" * 78)

    try:
        test_suite_threshold_boundaries()
        test_suite_centroid_mathematical_invariance()
        test_suite_orthonormal_isometry()
        test_suite_acoustic_prequalification()
        test_suite_adts_bitstream_and_audio_preview()
        test_suite_contract_c6_and_multi_tenant_boundaries()

        print("\n" + "=" * 78)
        print("  ALL 6 ADVERSARIAL CHALLENGE SUITES PASSED WITHOUT DEFECT.")
        print("  MATHEMATICAL INVARIANCES, BOUNDARIES & BITSTREAM INTEGRITY FULLY APPROVED.")
        print("=" * 78 + "\n")
        return 0
    except AssertionError as exc:
        print(f"\n[FATAL CHALLENGE FAILURE] {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    except Exception as exc:
        print(f"\n[UNEXPECTED ERROR] {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
