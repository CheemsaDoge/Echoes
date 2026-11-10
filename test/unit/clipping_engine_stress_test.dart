/// Adversarial stress testing harness for Contract C5 5-Tier Priority Interval Clipping Engine.
/// Authored by challenger_m5_1.
library;

import 'dart:math';
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

/// Helper to generate all N! permutations of a list.
List<List<T>> permutations<T>(List<T> list) {
  if (list.isEmpty) return [[]];
  final result = <List<T>>[];
  for (var i = 0; i < list.length; i++) {
    final item = list[i];
    final remaining = List<T>.from(list)..removeAt(i);
    for (final perm in permutations(remaining)) {
      result.add([item, ...perm]);
    }
  }
  return result;
}

void main() {
  print('================================================================');
  print('CONTRACT C5 5-TIER CLIPPING ENGINE: ADVERSARIAL STRESS SUITE');
  print('================================================================\n');

  final overallStopwatch = Stopwatch()..start();
  var totalAssertions = 0;

  // --------------------------------------------------------------------------
  // OBJECTIVE 1: Massive Random Overlapping Interval Fuzzing (10,000+ intervals)
  // --------------------------------------------------------------------------
  print('>>> CHALLENGE 1: Massive Random Overlapping Interval Fuzzing (20,000 intervals)');
  {
    final sw = Stopwatch()..start();
    final random = Random(42); // Deterministic seed for reproducible adversarial fuzzing
    final rawIntervals = <TimelineInterval>[];
    const intervalCount = 20000;

    for (var i = 0; i < intervalCount; i++) {
      final s = random.nextInt(1440);
      final len = random.nextInt(300) + 1; // 1 to 300 minutes
      final tier = random.nextInt(5); // 0 to 4
      rawIntervals.add(TimelineInterval(
        intervalId: 'fuzz_$i',
        startMs: s,
        endMs: s + len,
        tier: tier,
        itemType: 'FuzzType_$tier',
      ));
    }

    final clipped = TimelineClippingEngine.clipTimelineIntervals(rawIntervals);
    sw.stop();
    print('    Clipped $intervalCount input intervals -> ${clipped.length} output disjoint intervals in ${sw.elapsedMilliseconds}ms');

    assertTrue(clipped.isNotEmpty, 'Fuzzed output must not be empty');
    totalAssertions++;

    // Invariant 1: Strictly sorted by startMs ascending
    // Invariant 2: Zero overlap: endMs of i <= startMs of i+1
    // Invariant 3: Positive duration for all output items
    // Invariant 4: No degenerate segments
    for (var i = 0; i < clipped.length; i++) {
      final item = clipped[i];
      assertTrue(item.durationMs > 0, 'Item $i duration must be > 0 (got ${item.durationMs})');
      assertTrue(item.startMs < item.endMs, 'Item $i startMs must be < endMs');
      totalAssertions += 2;

      if (i > 0) {
        final prev = clipped[i - 1];
        assertTrue(
          prev.endMs <= item.startMs,
          'Overlap detected between item ${i - 1} [${prev.startMs}, ${prev.endMs}) and item $i [${item.startMs}, ${item.endMs})',
        );
        assertTrue(
          prev.startMs <= item.startMs,
          'Sorting violation between item ${i - 1} and $i',
        );
        totalAssertions += 2;
      }
    }

    print('    [PASS] 20,000 interval fuzz invariant check: 0 overlaps, strictly sorted, positive durations.\n');
  }

  // --------------------------------------------------------------------------
  // OBJECTIVE 1B: Discrete Point-Wise Mathematical Oracle Validation
  // --------------------------------------------------------------------------
  print('>>> CHALLENGE 1B: Point-Wise Mathematical Oracle Verification');
  {
    final sw = Stopwatch()..start();
    final random = Random(999);
    // Generate 500 overlapping intervals across a 1,440-minute day
    final intervals = <TimelineInterval>[];
    for (var i = 0; i < 500; i++) {
      final s = random.nextInt(1440);
      final len = random.nextInt(120) + 1;
      final tier = random.nextInt(5);
      intervals.add(TimelineInterval(
        intervalId: 'oracle_$i',
        startMs: s,
        endMs: min(1440, s + len),
        tier: tier,
        itemType: 'OracleType_$tier',
      ));
    }

    // Mathematical Ground Truth Oracle:
    // For each minute m in [0, 1440), groundTruthTier(m) = min({I.tier | I covers m}) or null
    final groundTruth = List<int?>.filled(1440, null);
    for (final item in intervals) {
      for (var m = item.startMs; m < item.endMs; m++) {
        if (groundTruth[m] == null || item.tier < groundTruth[m]!) {
          groundTruth[m] = item.tier;
        }
      }
    }

    // Run engine clipping
    final clipped = TimelineClippingEngine.clipTimelineIntervals(intervals);

    // Verify engine output matches ground truth oracle for every single minute
    final engineGrid = List<int?>.filled(1440, null);
    for (final item in clipped) {
      for (var m = item.startMs; m < item.endMs; m++) {
        assertTrue(engineGrid[m] == null, 'Engine output has overlap at minute $m');
        engineGrid[m] = item.tier;
        totalAssertions++;
      }
    }

    for (var m = 0; m < 1440; m++) {
      assertEqual(engineGrid[m], groundTruth[m], 'Oracle mismatch at minute $m');
      totalAssertions++;
    }
    sw.stop();
    print('    [PASS] Point-wise oracle verified across all 1,440 minutes in ${sw.elapsedMilliseconds}ms: 100% exact priority match!\n');
  }

  // --------------------------------------------------------------------------
  // OBJECTIVE 2: Dense Combinatorial Permutations (N! input permutations)
  // --------------------------------------------------------------------------
  print('>>> CHALLENGE 2: Dense Combinatorial Permutations (Output Determinism)');
  {
    // Test 2A: 5 intervals of distinct tiers (P0..P4) with complex cascading overlap
    // 5! = 120 permutations
    {
      final p0 = TimelineInterval(intervalId: 'p0', startMs: 300, endMs: 400, tier: TimelineTier.p0LiveCard, itemType: 'P0');
      final p1 = TimelineInterval(intervalId: 'p1', startMs: 200, endMs: 500, tier: TimelineTier.p1AnalyzingChain, itemType: 'P1');
      final p2 = TimelineInterval(intervalId: 'p2', startMs: 150, endMs: 600, tier: TimelineTier.p2Activity, itemType: 'P2');
      final p3 = TimelineInterval(intervalId: 'p3', startMs: 100, endMs: 700, tier: TimelineTier.p3ScatteredChat, itemType: 'P3');
      final p4 = TimelineInterval(intervalId: 'p4', startMs: 0, endMs: 800, tier: TimelineTier.p4Coverage, itemType: 'P4');

      final perms5 = permutations([p0, p1, p2, p3, p4]);
      assertEqual(perms5.length, 120, 'Permutations count 5!');
      totalAssertions++;

      final baseline = TimelineClippingEngine.clipTimelineIntervals(perms5[0]);
      for (var i = 1; i < perms5.length; i++) {
        final res = TimelineClippingEngine.clipTimelineIntervals(perms5[i]);
        assertEqual(res.length, baseline.length, 'Permutation $i count mismatch');
        for (var k = 0; k < baseline.length; k++) {
          assertEqual(res[k].startMs, baseline[k].startMs, 'Permutation $i item $k startMs mismatch');
          assertEqual(res[k].endMs, baseline[k].endMs, 'Permutation $i item $k endMs mismatch');
          assertEqual(res[k].tier, baseline[k].tier, 'Permutation $i item $k tier mismatch');
        }
        totalAssertions += 1 + baseline.length * 3;
      }
      print('    [PASS] 5! (120) permutations of cascading 5 tiers produced 100% deterministic identical outputs.');
    }

    // Test 2B: 6 intervals with staggered overlapping boundaries
    // 6! = 720 permutations
    {
      final intervals = [
        TimelineInterval(intervalId: 's1', startMs: 0, endMs: 100, tier: 0, itemType: 'T0'),
        TimelineInterval(intervalId: 's2', startMs: 50, endMs: 150, tier: 1, itemType: 'T1'),
        TimelineInterval(intervalId: 's3', startMs: 100, endMs: 200, tier: 2, itemType: 'T2'),
        TimelineInterval(intervalId: 's4', startMs: 150, endMs: 250, tier: 3, itemType: 'T3'),
        TimelineInterval(intervalId: 's5', startMs: 200, endMs: 300, tier: 4, itemType: 'T4'),
        TimelineInterval(intervalId: 's6', startMs: 50, endMs: 250, tier: 2, itemType: 'T2_wide'),
      ];

      final perms6 = permutations(intervals);
      assertEqual(perms6.length, 720, 'Permutations count 6!');
      totalAssertions++;

      final baseline = TimelineClippingEngine.clipTimelineIntervals(perms6[0]);
      for (var i = 1; i < perms6.length; i++) {
        final res = TimelineClippingEngine.clipTimelineIntervals(perms6[i]);
        assertEqual(res.length, baseline.length, 'Permutation $i count mismatch');
        for (var k = 0; k < baseline.length; k++) {
          assertEqual(res[k].startMs, baseline[k].startMs, 'Permutation $i item $k startMs mismatch');
          assertEqual(res[k].endMs, baseline[k].endMs, 'Permutation $i item $k endMs mismatch');
          assertEqual(res[k].tier, baseline[k].tier, 'Permutation $i item $k tier mismatch');
        }
        totalAssertions += 1 + baseline.length * 3;
      }
      print('    [PASS] 6! (720) permutations of staggered overlaps produced 100% deterministic identical outputs.');
    }

    // Test 2C: 7 intervals combinatorial stress
    // 7! = 5,040 permutations
    {
      final sw = Stopwatch()..start();
      final intervals = [
        TimelineInterval(intervalId: 'c1', startMs: 10, endMs: 100, tier: 0, itemType: 'T0'),
        TimelineInterval(intervalId: 'c2', startMs: 80, endMs: 150, tier: 1, itemType: 'T1'),
        TimelineInterval(intervalId: 'c3', startMs: 120, endMs: 200, tier: 2, itemType: 'T2'),
        TimelineInterval(intervalId: 'c4', startMs: 180, endMs: 250, tier: 1, itemType: 'T1_b'),
        TimelineInterval(intervalId: 'c5', startMs: 50, endMs: 220, tier: 3, itemType: 'T3'),
        TimelineInterval(intervalId: 'c6', startMs: 0, endMs: 300, tier: 4, itemType: 'T4'),
        TimelineInterval(intervalId: 'c7', startMs: 250, endMs: 280, tier: 0, itemType: 'T0_b'),
      ];

      final perms7 = permutations(intervals);
      assertEqual(perms7.length, 5040, 'Permutations count 7!');
      totalAssertions++;

      final baseline = TimelineClippingEngine.clipTimelineIntervals(perms7[0]);
      for (var i = 1; i < perms7.length; i++) {
        final res = TimelineClippingEngine.clipTimelineIntervals(perms7[i]);
        assertEqual(res.length, baseline.length, 'Permutation $i count mismatch');
        for (var k = 0; k < baseline.length; k++) {
          assertEqual(res[k].startMs, baseline[k].startMs, 'Permutation $i item $k startMs mismatch');
          assertEqual(res[k].endMs, baseline[k].endMs, 'Permutation $i item $k endMs mismatch');
          assertEqual(res[k].tier, baseline[k].tier, 'Permutation $i item $k tier mismatch');
        }
        totalAssertions += 1 + baseline.length * 3;
      }
      sw.stop();
      print('    [PASS] 7! (5,040) permutations executed in ${sw.elapsedMilliseconds}ms with 100% output determinism.\n');
    }
  }

  // --------------------------------------------------------------------------
  // OBJECTIVE 3: Degenerate Interval Stress
  // --------------------------------------------------------------------------
  print('>>> CHALLENGE 3: Degenerate Interval Stress');
  {
    // 3.1: Zero-duration intervals (startMs == endMs)
    {
      final d1 = TimelineInterval(intervalId: 'z1', startMs: 0, endMs: 0, tier: 0, itemType: 'Zero');
      final d2 = TimelineInterval(intervalId: 'z2', startMs: 500, endMs: 500, tier: 2, itemType: 'Zero');
      final d3 = TimelineInterval(intervalId: 'z3', startMs: 1440, endMs: 1440, tier: 4, itemType: 'Zero');
      final normal = TimelineInterval(intervalId: 'norm', startMs: 100, endMs: 200, tier: 1, itemType: 'Norm');

      final res = TimelineClippingEngine.clipTimelineIntervals([d1, d2, d3, normal]);
      assertEqual(res.length, 1, 'All zero-duration intervals must be filtered');
      assertEqual(res[0].intervalId, 'norm', 'Normal interval must survive');
      totalAssertions += 2;
      print('    [PASS] 3.1: Zero-duration intervals at 0, middle, and 1440 filtered cleanly.');
    }

    // 3.2: Negative duration intervals (startMs > endMs) must throw ArgumentError at construction
    {
      var caught = 0;
      final badPairs = [
        [100, 50],
        [1440, 0],
        [1, 0],
        [-10, -50],
      ];
      for (final pair in badPairs) {
        try {
          TimelineInterval(intervalId: 'bad', startMs: pair[0], endMs: pair[1], tier: 2, itemType: 'Bad');
        } on ArgumentError {
          caught++;
        }
      }
      assertEqual(caught, badPairs.length, 'All negative-duration intervals throw ArgumentError');
      totalAssertions += 2;
      print('    [PASS] 3.2: startMs > endMs throws ArgumentError immediately at model boundary.');
    }

    // 3.3: Duplicate boundaries across all 5 tiers
    {
      // 5 intervals at identical [100, 500) from P0 to P4
      final intervals = List.generate(
        5,
        (i) => TimelineInterval(intervalId: 't$i', startMs: 100, endMs: 500, tier: i, itemType: 'Tier$i'),
      );
      final res = TimelineClippingEngine.clipTimelineIntervals(intervals);
      assertEqual(res.length, 1, 'Duplicate boundaries across tiers must leave exactly 1 interval');
      assertEqual(res[0].tier, TimelineTier.p0LiveCard, 'P0 must completely dominate all other tiers');
      assertEqual(res[0].startMs, 100, 'Start preserved');
      assertEqual(res[0].endMs, 500, 'End preserved');
      totalAssertions += 4;
      print('    [PASS] 3.3: Duplicate boundaries across tiers strictly dominated by highest tier.');
    }

    // 3.4: Duplicate boundaries WITHIN the same tier
    {
      final a = TimelineInterval(intervalId: 'A', startMs: 100, endMs: 200, tier: 2, itemType: 'ActA');
      final b = TimelineInterval(intervalId: 'B', startMs: 100, endMs: 200, tier: 2, itemType: 'ActB');

      final resAB = TimelineClippingEngine.clipTimelineIntervals([a, b]);
      final resBA = TimelineClippingEngine.clipTimelineIntervals([b, a]);

      assertEqual(resAB.length, 1, 'Same tier identical boundary must yield 1 item');
      assertEqual(resBA.length, 1, 'Same tier identical boundary must yield 1 item');
      assertEqual(resAB[0].startMs, 100, 'resAB start');
      assertEqual(resAB[0].endMs, 200, 'resAB end');
      assertEqual(resBA[0].startMs, 100, 'resBA start');
      assertEqual(resBA[0].endMs, 200, 'resBA end');
      totalAssertions += 6;
      print('    [PASS] 3.4: Duplicate boundaries within same tier coalesce to single interval.');
    }

    // 3.5: Intervals spanning beyond 1440 and negative coordinates
    {
      final outHigh = TimelineInterval(intervalId: 'high', startMs: 1400, endMs: 1600, tier: 1, itemType: 'T1');
      final outFarHigh = TimelineInterval(intervalId: 'farHigh', startMs: 2000, endMs: 2500, tier: 2, itemType: 'T2');
      final outLow = TimelineInterval(intervalId: 'low', startMs: -200, endMs: 100, tier: 3, itemType: 'T3');
      final outFarLow = TimelineInterval(intervalId: 'farLow', startMs: -500, endMs: -100, tier: 4, itemType: 'T4');

      // Clipping engine handles arbitrary integer coordinates without exceptions
      final clipped = TimelineClippingEngine.clipTimelineIntervals([outHigh, outFarHigh, outLow, outFarLow]);
      assertTrue(clipped.isNotEmpty, 'Clipped arbitrary coordinates output');
      totalAssertions++;

      // Clamping to day boundary [0, 1440]
      final cHigh = TimelineClippingEngine.clampToDayBoundary(outHigh, 0, 1440);
      assertTrue(cHigh != null, 'cHigh not null');
      assertEqual(cHigh!.startMs, 1400, 'cHigh start');
      assertEqual(cHigh.endMs, 1440, 'cHigh end');
      assertTrue(cHigh.endsAfterPage, 'cHigh endsAfterPage');

      final cFarHigh = TimelineClippingEngine.clampToDayBoundary(outFarHigh, 0, 1440);
      assertEqual(cFarHigh, null, 'Far high interval outside day clamped to null');

      final cLow = TimelineClippingEngine.clampToDayBoundary(outLow, 0, 1440);
      assertTrue(cLow != null, 'cLow not null');
      assertEqual(cLow!.startMs, 0, 'cLow start');
      assertEqual(cLow.endMs, 100, 'cLow end');
      assertTrue(cLow.startsBeforePage, 'cLow startsBeforePage');

      final cFarLow = TimelineClippingEngine.clampToDayBoundary(outFarLow, 0, 1440);
      assertEqual(cFarLow, null, 'Far low interval outside day clamped to null');
      totalAssertions += 9;
      print('    [PASS] 3.5: Boundary clamping correctly clips spans crossing 0 and 1440, drops outside spans.\n');
    }
  }

  // --------------------------------------------------------------------------
  // OBJECTIVE 4: Hole-Punching Stress (1,000 Micro-Intervals through P4 Interval)
  // --------------------------------------------------------------------------
  print('>>> CHALLENGE 4: Hole-Punching Stress (1,000 Micro-Intervals)');
  {
    final sw = Stopwatch()..start();
    // Wide base interval: P4 Coverage [0, 100000)
    final base = TimelineInterval(
      intervalId: 'base_coverage',
      startMs: 0,
      endMs: 100000,
      tier: TimelineTier.p4Coverage,
      itemType: 'Coverage',
    );

    // 1000 higher-priority micro-intervals:
    // k = 1..1000: [k * 90, k * 90 + 10) of tier P0 LiveCard
    // Each punches a 10ms hole in the P4 interval.
    final microIntervals = <TimelineInterval>[];
    for (var k = 1; k <= 1000; k++) {
      microIntervals.add(TimelineInterval(
        intervalId: 'micro_$k',
        startMs: k * 90,
        endMs: k * 90 + 10,
        tier: TimelineTier.p0LiveCard,
        itemType: 'TimelineLiveCard',
      ));
    }

    // Input: base + 1000 micro-intervals
    final input = [base, ...microIntervals];
    final res = TimelineClippingEngine.clipTimelineIntervals(input);
    sw.stop();

    // Verification:
    // 1000 non-touching micro-intervals punch 1000 disjoint holes into [0, 100000).
    // Disjoint remainder count of P4 pieces: exactly 1001 pieces!
    // Total intervals in output: 1001 (P4) + 1000 (P0) = 2001 intervals!
    assertEqual(res.length, 2001, 'Hole punch output total length');
    totalAssertions++;

    final p4Pieces = res.where((i) => i.tier == TimelineTier.p4Coverage).toList();
    final p0Pieces = res.where((i) => i.tier == TimelineTier.p0LiveCard).toList();
    assertEqual(p4Pieces.length, 1001, 'Exact disjoint remainder count of P4 pieces');
    assertEqual(p0Pieces.length, 1000, 'Exact count of surviving P0 micro-intervals');
    totalAssertions += 2;

    // Verify contiguous sorting & zero gap/zero overlap inside [0, 100000)
    var totalDuration = 0;
    for (var i = 0; i < res.length; i++) {
      final item = res[i];
      totalDuration += item.durationMs;

      if (i > 0) {
        final prev = res[i - 1];
        assertEqual(prev.endMs, item.startMs, 'Contiguous partition between item $i and ${i - 1}');
        assertTrue(prev.tier != item.tier, 'Alternating tiers between piece $i and ${i - 1}');
        totalAssertions += 2;
      }
    }

    assertEqual(totalDuration, 100000, 'Total duration conserved across all 2001 pieces');
    totalAssertions++;
    print('    [PASS] 1,000 micro-intervals punched in ${sw.elapsedMilliseconds}ms -> exactly 1,001 P4 remnants, 2,001 contiguous segments.\n');
  }

  // --------------------------------------------------------------------------
  // OBJECTIVE 5: Idle Gap-Filling Invariant (Zero Gaps, Zero Overlaps, Sum == 1440)
  // --------------------------------------------------------------------------
  print('>>> CHALLENGE 5: Idle Gap-Filling Invariant on [0, 1440]');
  {
    void assertCompletePartition(List<TimelineInterval> list, String scenario) {
      assertTrue(list.isNotEmpty, '$scenario: Result must not be empty');
      assertEqual(list.first.startMs, 0, '$scenario: First interval must start at 0');
      assertEqual(list.last.endMs, 1440, '$scenario: Last interval must end at 1440');

      var sumDuration = 0;
      for (var i = 0; i < list.length; i++) {
        final curr = list[i];
        assertTrue(curr.durationMs > 0, '$scenario: Item $i must have positive duration');
        sumDuration += curr.durationMs;

        if (i > 0) {
          final prev = list[i - 1];
          assertEqual(
            prev.endMs,
            curr.startMs,
            '$scenario: Disjoint/Gap violation at index $i: prev.endMs=${prev.endMs} != curr.startMs=${curr.startMs}',
          );
        }
      }
      assertEqual(sumDuration, 1440, '$scenario: Sum of all durations must equal 1440 exactly');
    }

    // 5.1: Empty placed intervals -> single idle segment [0, 1440)
    {
      final filled = TimelineClippingEngine.fillCalendarIdleSegments(
        placedIntervals: [],
        dayStartMs: 0,
        dayEndMs: 1440,
      );
      assertEqual(filled.length, 1, 'Empty placed yields 1 idle interval');
      assertEqual(filled[0].itemType, 'calendar_idle_segment', 'Type is calendar_idle_segment');
      assertCompletePartition(filled, '5.1 Empty placed');
      totalAssertions += 5;
    }

    // 5.2: Single interval starting at 0, ending before 1440
    {
      final filled = TimelineClippingEngine.fillCalendarIdleSegments(
        placedIntervals: [
          TimelineInterval(intervalId: 'act', startMs: 0, endMs: 600, tier: 2, itemType: 'Act'),
        ],
        dayStartMs: 0,
        dayEndMs: 1440,
      );
      assertEqual(filled.length, 2, 'Starts at 0');
      assertCompletePartition(filled, '5.2 Starts at 0');
      totalAssertions += 5;
    }

    // 5.3: Single interval starting after 0, ending at 1440
    {
      final filled = TimelineClippingEngine.fillCalendarIdleSegments(
        placedIntervals: [
          TimelineInterval(intervalId: 'act', startMs: 800, endMs: 1440, tier: 2, itemType: 'Act'),
        ],
        dayStartMs: 0,
        dayEndMs: 1440,
      );
      assertEqual(filled.length, 2, 'Ends at 1440');
      assertCompletePartition(filled, '5.3 Ends at 1440');
      totalAssertions += 5;
    }

    // 5.4: Single interval completely covering [0, 1440)
    {
      final filled = TimelineClippingEngine.fillCalendarIdleSegments(
        placedIntervals: [
          TimelineInterval(intervalId: 'full', startMs: 0, endMs: 1440, tier: 4, itemType: 'Cov'),
        ],
        dayStartMs: 0,
        dayEndMs: 1440,
      );
      assertEqual(filled.length, 1, 'Fully covered yields 1 interval (no idle)');
      assertCompletePartition(filled, '5.4 Full coverage');
      totalAssertions += 5;
    }

    // 5.5: Multiple intervals with touching borders
    {
      final filled = TimelineClippingEngine.fillCalendarIdleSegments(
        placedIntervals: [
          TimelineInterval(intervalId: 'i1', startMs: 200, endMs: 500, tier: 1, itemType: 'T1'),
          TimelineInterval(intervalId: 'i2', startMs: 500, endMs: 900, tier: 2, itemType: 'T2'),
          TimelineInterval(intervalId: 'i3', startMs: 900, endMs: 1200, tier: 0, itemType: 'T0'),
        ],
        dayStartMs: 0,
        dayEndMs: 1440,
      );
      assertEqual(filled.length, 5, '3 touching intervals + 2 edge idles');
      assertCompletePartition(filled, '5.5 Touching borders');
      totalAssertions += 5;
    }

    // 5.6: 1,000 Random Fuzz Combinations of Placed Intervals
    {
      final rnd = Random(1337);
      for (var round = 0; round < 1000; round++) {
        // Generate random disjoint intervals within [0, 1440]
        final count = rnd.nextInt(20);
        final cuts = <int>{0, 1440};
        for (var c = 0; c < count * 2; c++) {
          cuts.add(rnd.nextInt(1441));
        }
        final sortedCuts = cuts.toList()..sort();
        final raw = <TimelineInterval>[];
        for (var idx = 0; idx < sortedCuts.length - 1; idx += 2) {
          final s = sortedCuts[idx];
          final e = sortedCuts[idx + 1];
          if (e > s) {
            raw.add(TimelineInterval(
              intervalId: 'rand_${round}_$idx',
              startMs: s,
              endMs: e,
              tier: rnd.nextInt(5),
              itemType: 'Type',
            ));
          }
        }

        // Clip first to ensure geometric validity
        final clipped = TimelineClippingEngine.clipTimelineIntervals(raw);
        // Fill idle
        final partitioned = TimelineClippingEngine.fillCalendarIdleSegments(
          placedIntervals: clipped,
          dayStartMs: 0,
          dayEndMs: 1440,
        );

        assertCompletePartition(partitioned, '5.6 Fuzz round $round');
        totalAssertions += 5;
      }
      print('    [PASS] 1,000 randomized fuzz configurations completely partitioned [0, 1440] with 0 gaps and 0 overlaps.');
    }
  }

  overallStopwatch.stop();
  print('\n================================================================');
  print('ALL ADVERSARIAL STRESS TESTS PASSED!');
  print('Total Assertions Executed: $totalAssertions');
  print('Total Execution Time: ${overallStopwatch.elapsedMilliseconds}ms');
  print('================================================================');
}
