/// Unit tests for Contract C5 5-Tier Priority Interval Clipping Engine.
/// Covers all 12 edge cases from TC-CLIP-01 to TC-CLIP-12.
library;

import '../../lib/core/models/timeline_models.dart';
import '../../lib/ui/timeline/clipping_engine.dart';

void assertEqual<T>(T actual, T expected, String message) {
  if (actual != expected) {
    throw AssertionError('$message: Expected $expected, got $actual');
  }
}

void assertTrue(bool condition, String message) {
  if (!condition) {
    throw AssertionError('Assertion failed: $message');
  }
}

void main() {
  print('Running Contract C5 5-Tier Priority Clipping Engine Tests (TC-CLIP-01 to TC-CLIP-12)...');

  // TC-CLIP-01: Case C (Truncation on right)
  // P0 LiveCard [585, 615) over P2 Activity [540, 600) -> P2 [540, 585) + P0 [585, 615)
  {
    final p2 = TimelineInterval(
      intervalId: 'act_01',
      startMs: 540,
      endMs: 600,
      tier: TimelineTier.p2Activity,
      itemType: 'Activity',
    );
    final p0 = TimelineInterval(
      intervalId: 'live_01',
      startMs: 585,
      endMs: 615,
      tier: TimelineTier.p0LiveCard,
      itemType: 'TimelineLiveCard',
    );

    final res = TimelineClippingEngine.clipTimelineIntervals([p2, p0]);
    assertEqual(res.length, 2, 'TC-CLIP-01 length');
    assertEqual(res[0].intervalId, 'act_01', 'TC-CLIP-01 first id');
    assertEqual(res[0].startMs, 540, 'TC-CLIP-01 first start');
    assertEqual(res[0].endMs, 585, 'TC-CLIP-01 first end');
    assertEqual(res[1].intervalId, 'live_01', 'TC-CLIP-01 second id');
    assertEqual(res[1].startMs, 585, 'TC-CLIP-01 second start');
    assertEqual(res[1].endMs, 615, 'TC-CLIP-01 second end');
    print('  [PASS] TC-CLIP-01: Truncation on right');
  }

  // TC-CLIP-02: Case D (Split into disjoint sub-intervals)
  // P1 AnalyzingChain [1400, 1600) in middle of P4 Coverage [1000, 2000)
  // -> P4 [1000, 1400) + P1 [1400, 1600) + P4 [1600, 2000)
  {
    final p4 = TimelineInterval(
      intervalId: 'cov_01',
      startMs: 1000,
      endMs: 2000,
      tier: TimelineTier.p4Coverage,
      itemType: 'Coverage',
    );
    final p1 = TimelineInterval(
      intervalId: 'chain_01',
      startMs: 1400,
      endMs: 1600,
      tier: TimelineTier.p1AnalyzingChain,
      itemType: 'AnalyzingChainCard',
    );

    final res = TimelineClippingEngine.clipTimelineIntervals([p4, p1]);
    assertEqual(res.length, 3, 'TC-CLIP-02 length');
    assertEqual(res[0].startMs, 1000, 'TC-CLIP-02 part0 start');
    assertEqual(res[0].endMs, 1400, 'TC-CLIP-02 part0 end');
    assertEqual(res[0].tier, TimelineTier.p4Coverage, 'TC-CLIP-02 part0 tier');
    assertEqual(res[1].startMs, 1400, 'TC-CLIP-02 part1 start');
    assertEqual(res[1].endMs, 1600, 'TC-CLIP-02 part1 end');
    assertEqual(res[1].tier, TimelineTier.p1AnalyzingChain, 'TC-CLIP-02 part1 tier');
    assertEqual(res[2].startMs, 1600, 'TC-CLIP-02 part2 start');
    assertEqual(res[2].endMs, 2000, 'TC-CLIP-02 part2 end');
    assertEqual(res[2].tier, TimelineTier.p4Coverage, 'TC-CLIP-02 part2 tier');
    print('  [PASS] TC-CLIP-02: Split into disjoint pieces');
  }

  // TC-CLIP-03: Case E (Complete absorption / elimination)
  // P0 LiveCard [400, 900) completely covers P3 ScatteredChat [500, 800) -> P0 [400, 900) only
  {
    final p3 = TimelineInterval(
      intervalId: 'chat_01',
      startMs: 500,
      endMs: 800,
      tier: TimelineTier.p3ScatteredChat,
      itemType: 'ScatteredChat',
    );
    final p0 = TimelineInterval(
      intervalId: 'live_01',
      startMs: 400,
      endMs: 900,
      tier: TimelineTier.p0LiveCard,
      itemType: 'TimelineLiveCard',
    );

    final res = TimelineClippingEngine.clipTimelineIntervals([p3, p0]);
    assertEqual(res.length, 1, 'TC-CLIP-03 length');
    assertEqual(res[0].intervalId, 'live_01', 'TC-CLIP-03 id');
    assertEqual(res[0].startMs, 400, 'TC-CLIP-03 start');
    assertEqual(res[0].endMs, 900, 'TC-CLIP-03 end');
    print('  [PASS] TC-CLIP-03: Complete absorption');
  }

  // TC-CLIP-04: Touching borders (No overlap, no clipping)
  // P2 Activity [100, 200) and P4 Coverage [200, 300) -> Both preserved untouched
  {
    final p2 = TimelineInterval(
      intervalId: 'act_01',
      startMs: 100,
      endMs: 200,
      tier: TimelineTier.p2Activity,
      itemType: 'Activity',
    );
    final p4 = TimelineInterval(
      intervalId: 'cov_01',
      startMs: 200,
      endMs: 300,
      tier: TimelineTier.p4Coverage,
      itemType: 'Coverage',
    );

    final res = TimelineClippingEngine.clipTimelineIntervals([p4, p2]);
    assertEqual(res.length, 2, 'TC-CLIP-04 length');
    assertEqual(res[0].startMs, 100, 'TC-CLIP-04 first start');
    assertEqual(res[0].endMs, 200, 'TC-CLIP-04 first end');
    assertEqual(res[1].startMs, 200, 'TC-CLIP-04 second start');
    assertEqual(res[1].endMs, 300, 'TC-CLIP-04 second end');
    print('  [PASS] TC-CLIP-04: Touching borders preserved untouched');
  }

  // TC-CLIP-05: Identical endpoints: P3 [500, 1000) and P1 [500, 1000) -> P1 wins
  {
    final p3 = TimelineInterval(
      intervalId: 'chat_01',
      startMs: 500,
      endMs: 1000,
      tier: TimelineTier.p3ScatteredChat,
      itemType: 'Chat',
    );
    final p1 = TimelineInterval(
      intervalId: 'chain_01',
      startMs: 500,
      endMs: 1000,
      tier: TimelineTier.p1AnalyzingChain,
      itemType: 'Chain',
    );

    final res = TimelineClippingEngine.clipTimelineIntervals([p3, p1]);
    assertEqual(res.length, 1, 'TC-CLIP-05 length');
    assertEqual(res[0].intervalId, 'chain_01', 'TC-CLIP-05 id');
    assertEqual(res[0].tier, TimelineTier.p1AnalyzingChain, 'TC-CLIP-05 tier');
    print('  [PASS] TC-CLIP-05: Identical boundaries favor higher tier');
  }

  // TC-CLIP-06: Multi-tier recursive cascade
  // P2 [0, 1000), P1 [200, 800), P0 [400, 600)
  // Output: 5 segments: P2 [0, 200), P1 [200, 400), P0 [400, 600), P1 [600, 800), P2 [800, 1000)
  {
    final p2 = TimelineInterval(
      intervalId: 'act',
      startMs: 0,
      endMs: 1000,
      tier: TimelineTier.p2Activity,
      itemType: 'Activity',
    );
    final p1 = TimelineInterval(
      intervalId: 'chain',
      startMs: 200,
      endMs: 800,
      tier: TimelineTier.p1AnalyzingChain,
      itemType: 'Chain',
    );
    final p0 = TimelineInterval(
      intervalId: 'live',
      startMs: 400,
      endMs: 600,
      tier: TimelineTier.p0LiveCard,
      itemType: 'Live',
    );

    final res = TimelineClippingEngine.clipTimelineIntervals([p2, p1, p0]);
    assertEqual(res.length, 5, 'TC-CLIP-06 length');
    assertEqual(res[0].tier, TimelineTier.p2Activity, 'TC-CLIP-06 seg 0 tier');
    assertEqual(res[0].startMs, 0, 'TC-CLIP-06 seg 0 start');
    assertEqual(res[0].endMs, 200, 'TC-CLIP-06 seg 0 end');

    assertEqual(res[1].tier, TimelineTier.p1AnalyzingChain, 'TC-CLIP-06 seg 1 tier');
    assertEqual(res[1].startMs, 200, 'TC-CLIP-06 seg 1 start');
    assertEqual(res[1].endMs, 400, 'TC-CLIP-06 seg 1 end');

    assertEqual(res[2].tier, TimelineTier.p0LiveCard, 'TC-CLIP-06 seg 2 tier');
    assertEqual(res[2].startMs, 400, 'TC-CLIP-06 seg 2 start');
    assertEqual(res[2].endMs, 600, 'TC-CLIP-06 seg 2 end');

    assertEqual(res[3].tier, TimelineTier.p1AnalyzingChain, 'TC-CLIP-06 seg 3 tier');
    assertEqual(res[3].startMs, 600, 'TC-CLIP-06 seg 3 start');
    assertEqual(res[3].endMs, 800, 'TC-CLIP-06 seg 3 end');

    assertEqual(res[4].tier, TimelineTier.p2Activity, 'TC-CLIP-06 seg 4 tier');
    assertEqual(res[4].startMs, 800, 'TC-CLIP-06 seg 4 start');
    assertEqual(res[4].endMs, 1000, 'TC-CLIP-06 seg 4 end');
    print('  [PASS] TC-CLIP-06: Multi-tier cascade clipping');
  }

  // TC-CLIP-07: Multiple higher holes in single lower interval
  // P4 [0, 1000), P1 [200, 300), P1 [600, 700)
  // Output: 5 segments: P4 [0, 200), P1 [200, 300), P4 [300, 600), P1 [600, 700), P4 [700, 1000)
  {
    final p4 = TimelineInterval(
      intervalId: 'cov',
      startMs: 0,
      endMs: 1000,
      tier: TimelineTier.p4Coverage,
      itemType: 'Coverage',
    );
    final p1a = TimelineInterval(
      intervalId: 'chain1',
      startMs: 200,
      endMs: 300,
      tier: TimelineTier.p1AnalyzingChain,
      itemType: 'Chain',
    );
    final p1b = TimelineInterval(
      intervalId: 'chain2',
      startMs: 600,
      endMs: 700,
      tier: TimelineTier.p1AnalyzingChain,
      itemType: 'Chain',
    );

    final res = TimelineClippingEngine.clipTimelineIntervals([p4, p1a, p1b]);
    assertEqual(res.length, 5, 'TC-CLIP-07 length');
    assertEqual(res[0].startMs, 0, 'seg 0 start');
    assertEqual(res[0].endMs, 200, 'seg 0 end');
    assertEqual(res[1].startMs, 200, 'seg 1 start');
    assertEqual(res[1].endMs, 300, 'seg 1 end');
    assertEqual(res[2].startMs, 300, 'seg 2 start');
    assertEqual(res[2].endMs, 600, 'seg 2 end');
    assertEqual(res[3].startMs, 600, 'seg 3 start');
    assertEqual(res[3].endMs, 700, 'seg 3 end');
    assertEqual(res[4].startMs, 700, 'seg 4 start');
    assertEqual(res[4].endMs, 1000, 'seg 4 end');
    print('  [PASS] TC-CLIP-07: Multiple holes punched');
  }

  // TC-CLIP-08: Input permutation determinism
  {
    final p2 = TimelineInterval(intervalId: 'act', startMs: 0, endMs: 1000, tier: 2, itemType: 'Act');
    final p1 = TimelineInterval(intervalId: 'chain', startMs: 200, endMs: 800, tier: 1, itemType: 'Chain');
    final p0 = TimelineInterval(intervalId: 'live', startMs: 400, endMs: 600, tier: 0, itemType: 'Live');

    final permutations = [
      [p2, p1, p0],
      [p2, p0, p1],
      [p1, p2, p0],
      [p1, p0, p2],
      [p0, p2, p1],
      [p0, p1, p2],
    ];

    List<TimelineInterval>? baseline;
    for (final perm in permutations) {
      final res = TimelineClippingEngine.clipTimelineIntervals(perm);
      if (baseline == null) {
        baseline = res;
      } else {
        assertEqual(res.length, baseline.length, 'Permutation length mismatch');
        for (var i = 0; i < res.length; i++) {
          assertEqual(res[i].startMs, baseline[i].startMs, 'Permutation $i startMs mismatch');
          assertEqual(res[i].endMs, baseline[i].endMs, 'Permutation $i endMs mismatch');
          assertEqual(res[i].tier, baseline[i].tier, 'Permutation $i tier mismatch');
        }
      }
    }
    print('  [PASS] TC-CLIP-08: All 6 permutations produce identical output');
  }

  // TC-CLIP-09: Degenerate intervals (zero duration or empty list)
  {
    final empty = TimelineClippingEngine.clipTimelineIntervals([]);
    assertEqual(empty.length, 0, 'Empty input produces empty output');

    final degen = TimelineInterval(intervalId: 'zero', startMs: 100, endMs: 100, tier: 2, itemType: 'Zero');
    final p0 = TimelineInterval(intervalId: 'live', startMs: 200, endMs: 300, tier: 0, itemType: 'Live');
    final res = TimelineClippingEngine.clipTimelineIntervals([degen, p0]);
    assertEqual(res.length, 1, 'Zero duration filtered out');
    assertEqual(res[0].intervalId, 'live', 'Live interval preserved');
    print('  [PASS] TC-CLIP-09: Zero-duration and empty list handled gracefully');
  }

  // TC-CLIP-10: Invalid interval (startMs > endMs) throws ArgumentError
  {
    bool threw = false;
    try {
      TimelineInterval(intervalId: 'bad', startMs: 500, endMs: 400, tier: 2, itemType: 'Bad');
    } on ArgumentError {
      threw = true;
    }
    assertTrue(threw, 'Invalid interval startMs > endMs must throw ArgumentError');
    print('  [PASS] TC-CLIP-10: startMs > endMs throws ArgumentError');
  }

  // TC-CLIP-11: Idle segment filling on [0, 1440]
  {
    final i1 = TimelineInterval(intervalId: 'ev1', startMs: 100, endMs: 300, tier: 2, itemType: 'Act');
    final i2 = TimelineInterval(intervalId: 'ev2', startMs: 600, endMs: 800, tier: 1, itemType: 'Chain');

    final filled = TimelineClippingEngine.fillCalendarIdleSegments(
      placedIntervals: [i1, i2],
      dayStartMs: 0,
      dayEndMs: 1440,
    );

    // Expected 5 intervals:
    // [0, 100) idle, [100, 300) ev1, [300, 600) idle, [600, 800) ev2, [800, 1440) idle
    assertEqual(filled.length, 5, 'TC-CLIP-11 length');
    assertEqual(filled[0].itemType, 'calendar_idle_segment', 'seg 0 is idle');
    assertEqual(filled[0].startMs, 0, 'seg 0 start');
    assertEqual(filled[0].endMs, 100, 'seg 0 end');

    assertEqual(filled[1].intervalId, 'ev1', 'seg 1 is ev1');

    assertEqual(filled[2].itemType, 'calendar_idle_segment', 'seg 2 is idle');
    assertEqual(filled[2].startMs, 300, 'seg 2 start');
    assertEqual(filled[2].endMs, 600, 'seg 2 end');

    assertEqual(filled[3].intervalId, 'ev2', 'seg 3 is ev2');

    assertEqual(filled[4].itemType, 'calendar_idle_segment', 'seg 4 is idle');
    assertEqual(filled[4].startMs, 800, 'seg 4 start');
    assertEqual(filled[4].endMs, 1440, 'seg 4 end');
    print('  [PASS] TC-CLIP-11: Idle gap filling covers entire 0..1440m range');
  }

  // TC-CLIP-12: Midnight boundary clamping
  {
    final crossMidnight = TimelineInterval(
      intervalId: 'midnight_span',
      startMs: 1400,
      endMs: 1480,
      tier: TimelineTier.p2Activity,
      itemType: 'Activity',
    );

    final dayD = TimelineClippingEngine.clampToDayBoundary(crossMidnight, 0, 1440);
    assertTrue(dayD != null, 'Day D clamped must not be null');
    assertEqual(dayD!.startMs, 1400, 'Day D start');
    assertEqual(dayD.endMs, 1440, 'Day D end');
    assertTrue(dayD.endsAfterPage, 'Day D endsAfterPage must be true');
    assertTrue(!dayD.startsBeforePage, 'Day D startsBeforePage must be false');

    final dayDNext = TimelineClippingEngine.clampToDayBoundary(
      crossMidnight.copyWith(startMs: crossMidnight.startMs - 1440, endMs: crossMidnight.endMs - 1440),
      0,
      1440,
    );
    assertTrue(dayDNext != null, 'Day D+1 clamped must not be null');
    assertEqual(dayDNext!.startMs, 0, 'Day D+1 start');
    assertEqual(dayDNext.endMs, 40, 'Day D+1 end');
    assertTrue(dayDNext.startsBeforePage, 'Day D+1 startsBeforePage must be true');
    assertTrue(!dayDNext.endsAfterPage, 'Day D+1 endsAfterPage must be false');
    print('  [PASS] TC-CLIP-12: Midnight crossing clamped with proper boundary flags');
  }

  print('\nALL 12 CONTRACT C5 CLIPPING TESTS PASSED SUCCESSFULLY!');
}
