"""Tier 2: Boundary B09 - Timeline Interval Clipping Edge & Corner Boundaries.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 3.4.6 & Contract C5.
Verifies touching boundaries, identical endpoints, alternating nested fragments, and sort determinism.
"""

from __future__ import annotations
import pytest
from tests.e2e.harness.timeline_harness import (
    TimelineInterval,
    clip_timeline_intervals,
    P0_LIVE_CARD,
    P1_ANALYZING_CHAIN,
    P2_ACTIVITY,
    P3_SCATTERED_CHAT,
    P4_COVERAGE,
)


class TestClippingEdgeBoundaries:
    """Verifies edge cases in timeline interval geometric clipping."""

    def test_touching_intervals_have_no_overlap_and_no_clipping(self):
        """1. Intervals touching at exact border (e.g. 100..200 and 200..300) are both preserved."""
        i1 = TimelineInterval("i1", 100, 200, P2_ACTIVITY, "Activity")
        i2 = TimelineInterval("i2", 200, 300, P4_COVERAGE, "Coverage")

        clipped = clip_timeline_intervals([i1, i2])
        assert len(clipped) == 2
        assert (clipped[0].start_ms, clipped[0].end_ms) == (100, 200)
        assert (clipped[1].start_ms, clipped[1].end_ms) == (200, 300)

    def test_identical_start_and_end_with_different_tiers(self):
        """2. Identical intervals [500, 1000]: P1 completely eclipses P3."""
        i_low = TimelineInterval("low", 500, 1000, P3_SCATTERED_CHAT, "Chat")
        i_high = TimelineInterval("high", 500, 1000, P1_ANALYZING_CHAIN, "Chain")

        clipped = clip_timeline_intervals([i_low, i_high])
        assert len(clipped) == 1
        assert clipped[0].interval_id == "high"
        assert clipped[0].tier == P1_ANALYZING_CHAIN

    def test_multiple_higher_priority_punches_through_lower_interval(self):
        """3. Two higher priority intervals punch two holes inside a large lower priority interval."""
        # Lower: 0..1000
        lower = TimelineInterval("base", 0, 1000, P4_COVERAGE, "Coverage")
        # Higher hole 1: 200..300
        h1 = TimelineInterval("h1", 200, 300, P1_ANALYZING_CHAIN, "Chain")
        # Higher hole 2: 600..700
        h2 = TimelineInterval("h2", 600, 700, P1_ANALYZING_CHAIN, "Chain")

        clipped = clip_timeline_intervals([lower, h1, h2])

        # Expected:
        # base_part0: 0..200
        # h1: 200..300
        # base_part1: 300..600
        # h2: 600..700
        # base_part2: 700..1000
        assert len(clipped) == 5
        ranges = [(x.start_ms, x.end_ms, x.tier) for x in clipped]
        assert ranges == [
            (0, 200, P4_COVERAGE),
            (200, 300, P1_ANALYZING_CHAIN),
            (300, 600, P4_COVERAGE),
            (600, 700, P1_ANALYZING_CHAIN),
            (700, 1000, P4_COVERAGE),
        ]

    def test_randomly_shuffled_input_produces_identical_deterministic_output(self):
        """4. Regardless of input list order, output intervals are deterministically identical."""
        items = [
            TimelineInterval("a", 100, 300, P4_COVERAGE, "Coverage"),
            TimelineInterval("b", 200, 250, P0_LIVE_CARD, "Live"),
            TimelineInterval("c", 400, 500, P2_ACTIVITY, "Activity"),
        ]

        import itertools
        outputs = []
        for perm in itertools.permutations(items):
            res = clip_timeline_intervals(list(perm))
            summary = [(x.interval_id, x.start_ms, x.end_ms, x.tier) for x in res]
            outputs.append(summary)

        # All permutations must produce identical result
        for out in outputs[1:]:
            assert out == outputs[0]

    def test_invalid_negative_duration_raises_value_error(self):
        """5. Constructing interval with start_ms > end_ms raises ValueError."""
        with pytest.raises(ValueError, match="Invalid interval"):
            TimelineInterval("bad", 500, 400, P2_ACTIVITY, "Activity")
