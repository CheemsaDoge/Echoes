"""Tier 1: Feature F5.3, F5.5 & Contract C5 - Dual-Mode Timeline & 5-Tier Clipping Engine.
Authoritative source: HEMORY_SYSTEM_SPECIFICATION.md Section 3.4.6 & Contract C5 & ORIGINAL_REQUEST.md R5.
Priority tiers:
- P0: TimelineLiveCard (0)
- P1: AnalyzingChainCard (1)
- P2: Activity (MemoryEventBrief) (2)
- P3: ScatteredChatGroup (3)
- P4: TimelineRecordingCoverageSegment & calendar_idle_segment (4)
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


class TestTimelineClippingEngine:
    """Verifies the 5-tier interval clipping engine and dual-mode timeline resolution."""

    def test_higher_priority_truncates_overlapping_lower_priority(self):
        """1. P0 LiveCard truncates overlapping P2 Activity interval."""
        # P2 Activity: 09:00 - 10:00 (540m to 600m in minutes)
        p2_activity = TimelineInterval(
            interval_id="act_01",
            start_ms=540,
            end_ms=600,
            tier=P2_ACTIVITY,
            item_type="Activity",
        )
        # P0 LiveCard: 09:45 - 10:15 (585m to 615m)
        p0_live = TimelineInterval(
            interval_id="live_01",
            start_ms=585,
            end_ms=615,
            tier=P0_LIVE_CARD,
            item_type="TimelineLiveCard",
        )

        clipped = clip_timeline_intervals([p2_activity, p0_live])

        # Expected:
        # P2 Activity is truncated to 540..585
        # P0 LiveCard occupies 585..615
        assert len(clipped) == 2
        assert clipped[0].interval_id == "act_01"
        assert (clipped[0].start_ms, clipped[0].end_ms) == (540, 585)
        assert clipped[1].interval_id == "live_01"
        assert (clipped[1].start_ms, clipped[1].end_ms) == (585, 615)

    def test_higher_priority_splits_lower_priority_into_two_disjoint_segments(self):
        """2. P1 AnalyzingChainCard in the middle of P4 Coverage splits it into 2 sub-intervals."""
        # P4 Coverage: 1000..2000 ms
        p4_coverage = TimelineInterval("cov_01", 1000, 2000, P4_COVERAGE, "Coverage")
        # P1 ChainCard: 1400..1600 ms (inside P4)
        p1_chain = TimelineInterval("chain_01", 1400, 1600, P1_ANALYZING_CHAIN, "AnalyzingChainCard")

        clipped = clip_timeline_intervals([p4_coverage, p1_chain])

        # Expected 3 intervals:
        # 1: cov_01_part0: 1000..1400
        # 2: chain_01: 1400..1600
        # 3: cov_01_part1: 1600..2000
        assert len(clipped) == 3
        assert (clipped[0].start_ms, clipped[0].end_ms) == (1000, 1400)
        assert clipped[0].tier == P4_COVERAGE
        assert (clipped[1].start_ms, clipped[1].end_ms) == (1400, 1600)
        assert clipped[1].tier == P1_ANALYZING_CHAIN
        assert (clipped[2].start_ms, clipped[2].end_ms) == (1600, 2000)
        assert clipped[2].tier == P4_COVERAGE

    def test_higher_priority_completely_absorbs_lower_priority(self):
        """3. P0 LiveCard fully covering P3 ScatteredChat drops P3 completely."""
        p3_chat = TimelineInterval("chat_01", 500, 800, P3_SCATTERED_CHAT, "ScatteredChat")
        p0_live = TimelineInterval("live_01", 400, 900, P0_LIVE_CARD, "TimelineLiveCard")

        clipped = clip_timeline_intervals([p3_chat, p0_live])
        assert len(clipped) == 1
        assert clipped[0].interval_id == "live_01"
        assert (clipped[0].start_ms, clipped[0].end_ms) == (400, 900)

    def test_disjoint_intervals_preserve_all_tiers_sorted(self):
        """4. Disjoint non-overlapping intervals across all tiers are preserved in chronological order."""
        i0 = TimelineInterval("p0", 100, 200, P0_LIVE_CARD, "Live")
        i1 = TimelineInterval("p1", 300, 400, P1_ANALYZING_CHAIN, "Chain")
        i2 = TimelineInterval("p2", 500, 600, P2_ACTIVITY, "Activity")
        i3 = TimelineInterval("p3", 700, 800, P3_SCATTERED_CHAT, "Chat")
        i4 = TimelineInterval("p4", 900, 1000, P4_COVERAGE, "Coverage")

        clipped = clip_timeline_intervals([i4, i2, i0, i3, i1])
        assert len(clipped) == 5
        starts = [x.start_ms for x in clipped]
        assert starts == [100, 300, 500, 700, 900]

    def test_multi_tier_cascade_clipping(self):
        """5. P0 over P1 over P2 multi-tier cascading resolution."""
        # P2: 0..1000
        # P1: 200..800
        # P0: 400..600
        p2 = TimelineInterval("p2", 0, 1000, P2_ACTIVITY, "Activity")
        p1 = TimelineInterval("p1", 200, 800, P1_ANALYZING_CHAIN, "Chain")
        p0 = TimelineInterval("p0", 400, 600, P0_LIVE_CARD, "Live")

        clipped = clip_timeline_intervals([p2, p1, p0])
        # Expected:
        # P2 left: 0..200
        # P1 left: 200..400
        # P0: 400..600
        # P1 right: 600..800
        # P2 right: 800..1000
        assert len(clipped) == 5
        segments = [(x.start_ms, x.end_ms, x.tier) for x in clipped]
        assert segments == [
            (0, 200, P2_ACTIVITY),
            (200, 400, P1_ANALYZING_CHAIN),
            (400, 600, P0_LIVE_CARD),
            (600, 800, P1_ANALYZING_CHAIN),
            (800, 1000, P2_ACTIVITY),
        ]

    def test_empty_or_zero_duration_intervals_handled(self):
        """6. Empty input or zero-duration intervals are cleanly discarded."""
        assert clip_timeline_intervals([]) == []
        zero_len = TimelineInterval("zero", 100, 100, P2_ACTIVITY, "Activity")
        valid = TimelineInterval("valid", 200, 300, P2_ACTIVITY, "Activity")
        res = clip_timeline_intervals([zero_len, valid])
        assert len(res) == 1
        assert res[0].interval_id == "valid"
