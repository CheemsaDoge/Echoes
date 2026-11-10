"""Tier 2: Boundary B06 - Elastic Rollover Window Strict Boundary Value Analysis.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 2.4.5 & ORIGINAL_REQUEST.md R2.
Boundary conditions tested:
- 25m59.999s vs 26m00.000s
- 799ms silence vs 800ms silence
- -39.9 dBFS vs -40.0 dBFS vs -40.1 dBFS
- 29m59.999s vs 30m00.000s
"""

from __future__ import annotations
import pytest
from tests.e2e.harness.audio_harness import evaluate_rollover


class TestRolloverStrictBoundaries:
    """Verifies boundary value transitions for duration, silence duration, and dBFS thresholds."""

    def test_boundary_25m59s999ms_vs_26m00s000ms(self):
        """1. 1,559,999ms does not cut even on deep silence; 1,560,000ms cuts immediately on 800ms silence."""
        # 1ms before 26 minutes
        dur_just_before = 26 * 60 * 1000 - 1
        cut_before, reason_before = evaluate_rollover(dur_just_before, silence_duration_ms=2000, dbfs=-50.0)
        assert cut_before is False
        assert reason_before is None

        # Exactly 26 minutes
        dur_exact = 26 * 60 * 1000
        cut_exact, reason_exact = evaluate_rollover(dur_exact, silence_duration_ms=800, dbfs=-40.0)
        assert cut_exact is True
        assert reason_exact == "silence_window"

    def test_boundary_799ms_vs_800ms_silence(self):
        """2. In 26..30m window, 799ms silence does not cut; 800ms silence cuts."""
        dur = 27 * 60 * 1000

        # 799ms
        cut_799, reason_799 = evaluate_rollover(dur, silence_duration_ms=799, dbfs=-45.0)
        assert cut_799 is False
        assert reason_799 is None

        # 800ms
        cut_800, reason_800 = evaluate_rollover(dur, silence_duration_ms=800, dbfs=-45.0)
        assert cut_800 is True
        assert reason_800 == "silence_window"

    def test_boundary_dbfs_level_transition(self):
        """3. In 26..30m window with 800ms silence: -39.9 dBFS does not cut; -40.0 dBFS cuts; -40.1 dBFS cuts."""
        dur = 28 * 60 * 1000
        silence_ms = 850

        # -39.9 dBFS is too loud
        cut_399, _ = evaluate_rollover(dur, silence_duration_ms=silence_ms, dbfs=-39.9)
        assert cut_399 is False

        # -40.0 dBFS exact threshold
        cut_400, reason_400 = evaluate_rollover(dur, silence_duration_ms=silence_ms, dbfs=-40.0)
        assert cut_400 is True
        assert reason_400 == "silence_window"

        # -40.1 dBFS quieter than threshold
        cut_401, reason_401 = evaluate_rollover(dur, silence_duration_ms=silence_ms, dbfs=-40.1)
        assert cut_401 is True
        assert reason_401 == "silence_window"

    def test_boundary_29m59s999ms_vs_30m00s000ms(self):
        """4. 1,799,999ms during continuous speech does not cut; 1,800,000ms forces hard limit cut."""
        # 1ms before 30 minutes with active speech at -15 dBFS
        dur_just_before = 30 * 60 * 1000 - 1
        cut_before, _ = evaluate_rollover(dur_just_before, silence_duration_ms=0, dbfs=-15.0)
        assert cut_before is False

        # Exactly 30 minutes
        dur_exact = 30 * 60 * 1000
        cut_exact, reason_exact = evaluate_rollover(dur_exact, silence_duration_ms=0, dbfs=-15.0)
        assert cut_exact is True
        assert reason_exact == "hard_limit"

    def test_extreme_duration_well_past_30min(self):
        """5. Duration well beyond 30m (e.g. 45m or 2 hours) triggers hard_limit consistently."""
        cut_45m, reason_45m = evaluate_rollover(45 * 60 * 1000, silence_duration_ms=0, dbfs=-20.0)
        assert cut_45m is True
        assert reason_45m == "hard_limit"

        cut_2h, reason_2h = evaluate_rollover(120 * 60 * 1000, silence_duration_ms=0, dbfs=-20.0)
        assert cut_2h is True
        assert reason_2h == "hard_limit"
