import 'dart:math';
import '../lib/core/models/timeline_models.dart';
import '../lib/ui/timeline/clipping_engine.dart';
import '../lib/core/models/speaker_models.dart';
import '../lib/ui/chat/speaker_resolver.dart';

void assertTrue(bool condition, String message) {
  if (!condition) {
    throw AssertionError('FAILED: $message');
  }
}

void assertEqual<T>(T actual, T expected, String message) {
  if (actual != expected) {
    throw AssertionError('FAILED: $message | Expected: $expected, Got: $actual');
  }
}

void main() {
  print('====================================================');
  print('  ADVERSARIAL STRESS TEST: CONTRACTS C5 & C6');
  print('====================================================\n');

  testClippingEngineProperties();
  testClippingRussianDolls();
  testClippingExtremeTimestamps();
  testIdleFillingCompleteness();
  testSpeakerResolverPrecedenceStress();
  testSpeakerResolverTagRegexStress();
  testSpeakerResolverHighConcurrency();

  print('\n>>> ALL ADVERSARIAL STRESS TESTS PASSED WITH 100% SUCCESS! <<<');
}

void testClippingEngineProperties() {
  print('Test 1: Fuzz testing clipping engine invariants (100 randomized rounds)...');
  final rng = Random(42);

  for (var round = 0; round < 100; round++) {
    final count = rng.nextInt(30) + 1;
    final intervals = <TimelineInterval>[];

    for (var i = 0; i < count; i++) {
      final s = rng.nextInt(1440);
      final len = rng.nextInt(300) + 1;
      final e = min(1440, s + len);
      final tier = rng.nextInt(5); // 0..4

      intervals.add(TimelineInterval(
        intervalId: 'rnd_${round}_$i',
        startMs: s,
        endMs: e,
        tier: tier,
        itemType: 'Item_$tier',
      ));
    }

    final clipped = TimelineClippingEngine.clipTimelineIntervals(intervals);

    // Invariant 1: Strictly non-overlapping
    for (var i = 0; i < clipped.length - 1; i++) {
      assertTrue(
        clipped[i].endMs <= clipped[i + 1].startMs,
        'Round $round: Overlap detected between ${clipped[i]} and ${clipped[i + 1]}',
      );
    }

    // Invariant 2: Positive duration
    for (final c in clipped) {
      assertTrue(c.durationMs > 0, 'Round $round: Non-positive duration in $c');
    }

    // Invariant 3: Mathematical Priority Dominance
    // For sample points t in [0..1440], the tier present in clipped must equal min(tiers in input covering t)
    for (var t = 0; t < 1440; t += 5) {
      int? minInputTier;
      for (final item in intervals) {
        if (item.startMs <= t && t < item.endMs) {
          if (minInputTier == null || item.tier < minInputTier) {
            minInputTier = item.tier;
          }
        }
      }

      int? clippedTier;
      for (final c in clipped) {
        if (c.startMs <= t && t < c.endMs) {
          clippedTier = c.tier;
          break;
        }
      }

      assertEqual(
        clippedTier,
        minInputTier,
        'Round $round at minute $t: Mathematical priority dominance violated',
      );
    }

    // Invariant 4: Permutation determinism
    final shuffled = List<TimelineInterval>.from(intervals)..shuffle(rng);
    final clippedShuffled = TimelineClippingEngine.clipTimelineIntervals(shuffled);
    assertEqual(
      clippedShuffled.length,
      clipped.length,
      'Round $round: Shuffled count mismatch',
    );
    for (var i = 0; i < clipped.length; i++) {
      assertEqual(clippedShuffled[i].startMs, clipped[i].startMs, 'Round $round item $i startMs mismatch');
      assertEqual(clippedShuffled[i].endMs, clipped[i].endMs, 'Round $round item $i endMs mismatch');
      assertEqual(clippedShuffled[i].tier, clipped[i].tier, 'Round $round item $i tier mismatch');
    }
  }

  print('  [PASS] 100 rounds of property fuzzing passed. Non-overlapping & priority dominance verified.');
}

void testClippingRussianDolls() {
  print('Test 2: Russian Dolls (deep nesting of tiers P4 to P0)...');
  // P4: [0, 1000)
  // P3: [100, 900)
  // P2: [200, 800)
  // P1: [300, 700)
  // P0: [400, 600)
  final intervals = [
    TimelineInterval(intervalId: 'p4', startMs: 0, endMs: 1000, tier: 4, itemType: 'P4'),
    TimelineInterval(intervalId: 'p3', startMs: 100, endMs: 900, tier: 3, itemType: 'P3'),
    TimelineInterval(intervalId: 'p2', startMs: 200, endMs: 800, tier: 2, itemType: 'P2'),
    TimelineInterval(intervalId: 'p1', startMs: 300, endMs: 700, tier: 1, itemType: 'P1'),
    TimelineInterval(intervalId: 'p0', startMs: 400, endMs: 600, tier: 0, itemType: 'P0'),
  ];

  final clipped = TimelineClippingEngine.clipTimelineIntervals(intervals);

  // Expected 9 segments:
  // [0, 100) P4
  // [100, 200) P3
  // [200, 300) P2
  // [300, 400) P1
  // [400, 600) P0
  // [600, 700) P1
  // [700, 800) P2
  // [800, 900) P3
  // [900, 1000) P4
  assertEqual(clipped.length, 9, 'Russian doll segment count');
  final expectedTiers = [4, 3, 2, 1, 0, 1, 2, 3, 4];
  final expectedStarts = [0, 100, 200, 300, 400, 600, 700, 800, 900];
  final expectedEnds = [100, 200, 300, 400, 600, 700, 800, 900, 1000];

  for (var i = 0; i < 9; i++) {
    assertEqual(clipped[i].tier, expectedTiers[i], 'Doll $i tier');
    assertEqual(clipped[i].startMs, expectedStarts[i], 'Doll $i start');
    assertEqual(clipped[i].endMs, expectedEnds[i], 'Doll $i end');
  }

  print('  [PASS] Russian dolls 9-slice symmetry verified.');
}

void testClippingExtremeTimestamps() {
  print('Test 3: Extreme epoch timestamps (epoch milliseconds ~ 1.7e12)...');
  const base = 1731234567000;
  final intervals = [
    TimelineInterval(intervalId: 'p2', startMs: base, endMs: base + 3600000, tier: 2, itemType: 'P2'),
    TimelineInterval(intervalId: 'p0', startMs: base + 1800000, endMs: base + 2400000, tier: 0, itemType: 'P0'),
  ];

  final clipped = TimelineClippingEngine.clipTimelineIntervals(intervals);
  assertEqual(clipped.length, 3, 'Epoch timestamp count');
  assertEqual(clipped[0].startMs, base, 'Epoch seg 0 start');
  assertEqual(clipped[0].endMs, base + 1800000, 'Epoch seg 0 end');
  assertEqual(clipped[1].startMs, base + 1800000, 'Epoch seg 1 start');
  assertEqual(clipped[1].endMs, base + 2400000, 'Epoch seg 1 end');
  assertEqual(clipped[2].startMs, base + 2400000, 'Epoch seg 2 start');
  assertEqual(clipped[2].endMs, base + 3600000, 'Epoch seg 2 end');
  print('  [PASS] 64-bit integer timestamp math verified without overflow or precision loss.');
}

void testIdleFillingCompleteness() {
  print('Test 4: Idle segment filling completeness on [0, 1440]...');
  final intervals = [
    TimelineInterval(intervalId: 'a', startMs: 200, endMs: 400, tier: 2, itemType: 'A'),
    TimelineInterval(intervalId: 'b', startMs: 500, endMs: 800, tier: 1, itemType: 'B'),
    TimelineInterval(intervalId: 'c', startMs: 800, endMs: 900, tier: 3, itemType: 'C'),
  ];

  final filled = TimelineClippingEngine.fillCalendarIdleSegments(
    placedIntervals: intervals,
    dayStartMs: 0,
    dayEndMs: 1440,
  );

  // Expected segments:
  // [0, 200) idle
  // [200, 400) a
  // [400, 500) idle
  // [500, 800) b
  // [800, 900) c (no idle between 800 and 800)
  // [900, 1440) idle
  assertEqual(filled.length, 6, 'Filled count');
  assertEqual(filled[0].startMs, 0, 'First start');
  assertEqual(filled.last.endMs, 1440, 'Last end');

  // Verify contiguous partition of [0, 1440]
  for (var i = 0; i < filled.length - 1; i++) {
    assertEqual(
      filled[i].endMs,
      filled[i + 1].startMs,
      'Contiguity violated between seg $i and ${i + 1}',
    );
  }

  // Verify total duration
  final totalDuration = filled.fold<int>(0, (sum, i) => sum + i.durationMs);
  assertEqual(totalDuration, 1440, 'Total duration must equal 1440');

  // Edge Case: Empty placed intervals
  final filledEmpty = TimelineClippingEngine.fillCalendarIdleSegments(
    placedIntervals: [],
    dayStartMs: 0,
    dayEndMs: 1440,
  );
  assertEqual(filledEmpty.length, 1, 'Empty filled count');
  assertEqual(filledEmpty[0].startMs, 0, 'Empty filled start');
  assertEqual(filledEmpty[0].endMs, 1440, 'Empty filled end');
  assertEqual(filledEmpty[0].itemType, 'calendar_idle_segment', 'Empty filled type');

  print('  [PASS] Idle filling continuous partition of [0, 1440] verified.');
}

void testSpeakerResolverPrecedenceStress() {
  print('Test 5: Speaker Resolver strict Contract C6 tier precedence...');
  // Setup profiles and roster with conflicting names to verify precedence
  final profiles = {
    'spk_complete': const SpeakerProfile(
      speakerId: 'spk_complete',
      name: 'Tier 1 Official Name',
      suspectedName: 'Tier 2b Suspect',
    ),
    'spk_suspect_only': const SpeakerProfile(
      speakerId: 'spk_suspect_only',
      name: '',
      suspectedName: 'Tier 2b Suspect Name',
    ),
    'spk_empty': const SpeakerProfile(
      speakerId: 'spk_empty',
      name: '   ',
      suspectedName: '',
    ),
  };

  final roster = {
    'spk_complete': const MemoryEventSpeakerRosterEntry(
      speakerId: 'spk_complete',
      suspectedName: 'Tier 2 Roster Name',
    ),
    'spk_empty': const MemoryEventSpeakerRosterEntry(
      speakerId: 'spk_empty',
      suspectedName: 'Tier 2 Roster For Empty',
    ),
  };

  // 1. Tier 1 must beat Tier 2 and Tier 2b
  final r1 = SpeakerResolver.resolveSpeakerName(
    speakerId: 'spk_complete',
    context: UtteranceContext(speakerProfiles: profiles, activeRoster: roster),
  );
  assertEqual(r1, 'Tier 1 Official Name', 'Tier 1 precedence');

  // 2. Empty Tier 1 profile name must fall back to Tier 2 roster
  final r2 = SpeakerResolver.resolveSpeakerName(
    speakerId: 'spk_empty',
    context: UtteranceContext(speakerProfiles: profiles, activeRoster: roster),
  );
  assertEqual(r2, 'Tier 2 Roster For Empty', 'Tier 2 roster fallback');

  // 3. Profile with only suspectedName falls back to Tier 2b
  final r3 = SpeakerResolver.resolveSpeakerName(
    speakerId: 'spk_suspect_only',
    context: UtteranceContext(speakerProfiles: profiles),
  );
  assertEqual(r3, 'Tier 2b Suspect Name', 'Tier 2b suspect fallback');

  // 4. Preserved concrete utterance label beats Tier 3 unknown index
  final r4 = SpeakerResolver.resolveSpeakerName(
    speakerId: 'spk_unknown',
    context: const UtteranceContext(localLabel: 'Audience Member 1'),
  );
  assertEqual(r4, 'Audience Member 1', 'Preserved concrete utterance label');

  // 5. Generic label ('Speaker 9', 'Unknown', 'Speaker') falls back to Tier 3
  for (final generic in ['Unknown', 'Speaker', 'Speaker 42', 'speaker 100']) {
    final r5 = SpeakerResolver.resolveSpeakerName(
      speakerId: 'spk_unknown',
      context: UtteranceContext(localLabel: generic, unknownSpeakerIndex: 7),
    );
    assertEqual(r5, 'Speaker 7', 'Generic label "$generic" fallback to Tier 3');
  }

  print('  [PASS] 5-tier speaker precedence hierarchy strictly verified.');
}

void testSpeakerResolverTagRegexStress() {
  print('Test 6: Speaker tag regex substitution adversarial stress...');
  final session = SessionSpeakerResolver(
    speakerProfiles: {
      'spk_01': const SpeakerProfile(speakerId: 'spk_01', name: 'Alice'),
      'spk-special_02': const SpeakerProfile(speakerId: 'spk-special_02', name: 'Bob'),
    },
  );

  // 1. Mixed bracket styles, punctuation, and adjacent tags
  const rawText = 'Start [speaker:spk_01]<speaker:spk-special_02>[speaker:spk_anon_1] middle. Price is \$50.00! End with <speaker:spk_anon_1>';
  final replaced = session.replaceSpeakerTags(rawText);

  // spk_01 -> Alice
  // spk-special_02 -> Bob
  // spk_anon_1 -> Speaker 1
  // second spk_anon_1 -> Speaker 1 (reused token)
  assertEqual(
    replaced,
    'Start AliceBobSpeaker 1 middle. Price is \$50.00! End with Speaker 1',
    'Complex tag replacement and \$ regex shielding',
  );

  // 2. Malformed tags should NOT match or throw
  const malformed = 'Text with [speaker:] and <speaker:> and [speaker] and <speaker>';
  final untouched = session.replaceSpeakerTags(malformed);
  assertEqual(untouched, malformed, 'Malformed tags preserved untouched');

  print('  [PASS] Tag regex parsing, special characters, and token reuse verified.');
}

void testSpeakerResolverHighConcurrency() {
  print('Test 7: High scale monotonic index test (1,000 distinct unknown speakers)...');
  final session = SessionSpeakerResolver();

  for (var i = 1; i <= 1000; i++) {
    final name = session.resolve(speakerId: 'spk_fuzz_$i');
    assertEqual(name, 'Speaker $i', 'Monotonic unknown index $i');
  }

  // Re-verify deterministic caching for random previously generated tokens
  for (final testIdx in [1, 50, 200, 500, 999, 1000]) {
    final name = session.resolve(speakerId: 'spk_fuzz_$testIdx');
    assertEqual(name, 'Speaker $testIdx', 'Deterministic lookup for token $testIdx');
  }

  print('  [PASS] 1,000 monotonic anonymous speaker tokens cleanly resolved and cached.');
}
