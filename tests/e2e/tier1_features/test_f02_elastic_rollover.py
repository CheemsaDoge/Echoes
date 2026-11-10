"""Tier 1: Feature F1.3 - Elastic Rollover Window Decision Engine Verification.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.4.5 & ORIGINAL_REQUEST.md R2.
- Duration < 26m: no silence cut.
- 26m <= Duration < 30m: cut on silence >= 800ms and <= -40.0 dBFS ('silence_window').
- Duration >= 30m: hard limit cut ('hard_limit').
"""

from __future__ import annotations
import pytest
from tests.e2e.harness.audio_harness import evaluate_rollover


class TestElasticRolloverWindow:
    """Tests the elastic rollover window slicing decision logic."""

    def test_under_26min_no_silence_cut(self):
        """1. Audio recording duration < 26m must never cut even during long silence."""
        # Test 10m, 20m, 25m with 5 seconds of deep silence at -60 dBFS
        durations = [10 * 60 * 1000, 20 * 60 * 1000, 25 * 60 * 1000]
        for dur in durations:
            should_cut, reason = evaluate_rollover(
                duration_ms=dur,
                silence_duration_ms=5000,
                dbfs=-60.0,
            )
            assert should_cut is False, f"Should not cut before 26m for duration {dur}ms"
            assert reason is None

    def test_window_26_to_30min_triggers_silence_window_cut(self):
        """2. Duration in 26..30m with silence >= 800ms at <= -40.0 dBFS triggers silence_window cut."""
        # 27 minutes (1,620,000 ms), 850ms silence at -45.0 dBFS
        dur = 27 * 60 * 1000
        should_cut, reason = evaluate_rollover(
            duration_ms=dur,
            silence_duration_ms=850,
            dbfs=-45.0,
        )
        assert should_cut is True
        assert reason == "silence_window"

    def test_window_26_to_30min_ignores_short_silence(self):
        """3. Duration in 26..30m with silence < 800ms (e.g. 700ms) does NOT trigger cut."""
        dur = 28 * 60 * 1000
        should_cut, reason = evaluate_rollover(
            duration_ms=dur,
            silence_duration_ms=700,
            dbfs=-50.0,
        )
        assert should_cut is False
        assert reason is None

    def test_window_26_to_30min_ignores_loud_audio_during_pause(self):
        """4. Duration in 26..30m with audio > -40.0 dBFS (e.g. -30.0 dBFS) does NOT trigger cut."""
        dur = 29 * 60 * 1000
        should_cut, reason = evaluate_rollover(
            duration_ms=dur,
            silence_duration_ms=1200,
            dbfs=-30.0,  # Audio too loud for silence threshold
        )
        assert should_cut is False
        assert reason is None

    def test_duration_30min_triggers_hard_limit_cut(self):
        """5. Duration >= 30m triggers hard limit cut regardless of silence or speech."""
        # Exactly 30m (1,800,000 ms) while someone is actively speaking (-18.0 dBFS, 0ms silence)
        dur = 30 * 60 * 1000
        should_cut, reason = evaluate_rollover(
            duration_ms=dur,
            silence_duration_ms=0,
            dbfs=-18.0,
        )
        assert should_cut is True
        assert reason == "hard_limit"

    def test_interruption_and_manual_stop_triggers(self):
        """6. External audio focus loss or manual stop triggers immediate cut."""
        cut_manual, reason_manual = evaluate_rollover(duration_ms=5000, is_manual_stop=True)
        assert cut_manual is True
        assert reason_manual == "manual_stop"

        cut_int, reason_int = evaluate_rollover(duration_ms=12000, is_interrupted=True)
        assert cut_int is True
        assert reason_int == "interruption"
