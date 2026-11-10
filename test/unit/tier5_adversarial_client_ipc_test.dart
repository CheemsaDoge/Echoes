/// Milestone M6 Phase 2: Tier 5 White-Box Client & Cross-Layer IPC Adversarial Suite.
/// Direct empirical verification of Dart client library implementation in lib/.
library;

import 'dart:async';
import 'dart:io';

import '../../lib/core/models/recording_state.dart';
import '../../lib/core/models/timeline_models.dart';
import '../../lib/core/models/speaker_models.dart';
import '../../lib/core/ipc/native_bridge.dart';
import '../../lib/core/ipc/event_channels.dart';
import '../../lib/core/services/recording_service.dart';
import '../../lib/ui/timeline/clipping_engine.dart';
import '../../lib/ui/chat/speaker_resolver.dart';
import '../../lib/core/db/local_database.dart';

void assertEqual<T>(T actual, T expected, String message) {
  if (actual != expected) {
    throw AssertionError('$message: Expected "$expected", got "$actual"');
  }
}

void assertTrue(bool condition, String message) {
  if (!condition) {
    throw AssertionError('Assertion failed: $message');
  }
}

// ============================================================================
// Virtual Clock Harness for Deterministic Time and Race Testing
// ============================================================================

class VirtualTimer implements Timer {
  final VirtualClock clock;
  final int dueMs;
  final void Function() callback;
  bool _isActive = true;

  VirtualTimer(this.clock, this.dueMs, this.callback);

  @override
  bool get isActive => _isActive;

  @override
  int get tick => 1;

  @override
  void cancel() {
    _isActive = false;
  }

  void fire() {
    if (_isActive) {
      _isActive = false;
      callback();
    }
  }
}

class VirtualClock {
  int currentTimeMs = 0;
  final List<VirtualTimer> timers = [];

  Timer createTimer(Duration duration, void Function() callback) {
    final timer = VirtualTimer(this, currentTimeMs + duration.inMilliseconds, callback);
    timers.add(timer);
    return timer;
  }

  void advance(Duration duration) {
    currentTimeMs += duration.inMilliseconds;
    final due = timers.where((t) => t.dueMs <= currentTimeMs && t.isActive).toList();
    due.sort((a, b) => a.dueMs.compareTo(b.dueMs));
    for (final t in due) {
      timers.remove(t);
      t.fire();
    }
  }

  int get pendingTimersCount => timers.where((t) => t.isActive).length;
}

// ============================================================================
// Mock Native Bridge & Streams for State Machine Injection
// ============================================================================

class MockAdversarialNativeBridge implements INativeBridge {
  String currentState = 'idle';
  int startCalls = 0;
  int stopCalls = 0;
  int pauseCalls = 0;
  int resumeCalls = 0;
  String? lastStopReason;

  @override
  Future<bool> startRecording({
    required String recordingId,
    required String tenantId,
    required String vaultId,
    String userId = '',
  }) async {
    startCalls++;
    currentState = 'activating';
    return true;
  }

  @override
  Future<bool> stopRecording({String reason = 'userStop'}) async {
    stopCalls++;
    lastStopReason = reason;
    currentState = 'stopped';
    return true;
  }

  @override
  Future<bool> pauseRecording() async {
    pauseCalls++;
    currentState = 'paused';
    return true;
  }

  @override
  Future<bool> resumeRecording() async {
    resumeCalls++;
    currentState = 'recording';
    return true;
  }

  @override
  Future<String> getRecordingState() async => currentState;

  @override
  Future<bool> storeAuth(Auth7Tuple auth) async => true;

  @override
  Future<Auth7Tuple?> getAuth() async => null;

  @override
  Future<bool> clearAuth() async => true;

  @override
  Future<bool> signOutAuth() async => true;
}

class MockAdversarialStreams implements IRecordingEventStreams {
  final StreamController<RecordingStateSnapshot> stateController = StreamController<RecordingStateSnapshot>.broadcast();
  final StreamController<RecordingLifecycleEvent> eventsController = StreamController<RecordingLifecycleEvent>.broadcast();
  final StreamController<MeteringSnapshot> meteringController = StreamController<MeteringSnapshot>.broadcast();
  final StreamController<SegmentCompletedEvent> segmentsController = StreamController<SegmentCompletedEvent>.broadcast();

  @override
  Stream<RecordingStateSnapshot> get stateStream => stateController.stream;

  @override
  Stream<RecordingLifecycleEvent> get eventsStream => eventsController.stream;

  @override
  Stream<MeteringSnapshot> get meteringStream => meteringController.stream;

  @override
  Stream<SegmentCompletedEvent> get segmentsStream => segmentsController.stream;

  void dispose() {
    stateController.close();
    eventsController.close();
    meteringController.close();
    segmentsController.close();
  }
}

// ============================================================================
// Main Adversarial Test Execution
// ============================================================================

Future<void> main() async {
  print('=================================================================');
  print('  TIER 5 ADVERSARIAL WHITE-BOX CLIENT & IPC SUITE (Milestone M6)');
  print('=================================================================\n');

  int totalTests = 0;
  int passedTests = 0;

  void test(String name, void Function() fn) {
    totalTests++;
    try {
      fn();
      passedTests++;
      print('  [PASS] $name');
    } catch (e, st) {
      print('  [FAIL] $name: $e\n$st');
    }
  }

  Future<void> asyncTest(String name, Future<void> Function() fn) async {
    totalTests++;
    try {
      await fn();
      passedTests++;
      print('  [PASS] $name');
    } catch (e, st) {
      print('  [FAIL] $name: $e\n$st');
    }
  }

  // --------------------------------------------------------------------------
  // 1. Riverpod 7-State Business State Machine (hf.i) & Watchdogs
  // --------------------------------------------------------------------------
  print('>>> SECTION 1: Riverpod 7-State State Machine (hf.i) & Watchdogs');

  test('1.1: State predicates and canonical transition allowances', () {
    const activeStates = [
      RecordingCaptureState.activating,
      RecordingCaptureState.recording,
      RecordingCaptureState.recovering,
      RecordingCaptureState.stopping,
    ];
    const inactiveStates = [
      RecordingCaptureState.idle,
      RecordingCaptureState.stopped,
      RecordingCaptureState.failedTerminal,
    ];

    for (final s in activeStates) {
      assertTrue(s.isActive, '${s.wire} must be active');
    }
    for (final s in inactiveStates) {
      assertTrue(!s.isActive, '${s.wire} must be inactive');
    }

    assertTrue(RecordingCaptureState.idle.canStart, 'idle can start');
    assertTrue(RecordingCaptureState.stopped.canStart, 'stopped can start');
    assertTrue(RecordingCaptureState.failedTerminal.canStart, 'failedTerminal can start');
    assertTrue(!RecordingCaptureState.recording.canStart, 'recording cannot start');
    assertTrue(!RecordingCaptureState.activating.canStart, 'activating cannot start');
  });

  await asyncTest('1.2: Activating watchdog race vs late native state event', () async {
    final bridge = MockAdversarialNativeBridge();
    final streams = MockAdversarialStreams();
    final service = RecordingService(bridge: bridge, streams: streams);

    await service.startRecording(recordingId: 'r1', tenantId: 't1', vaultId: 'v1');
    assertEqual(service.state.captureState, RecordingCaptureState.activating, 'activating');

    // Emit native state event before 10s: watchdog cancelled cleanly
    streams.stateController.add(const RecordingStateSnapshot(
      state: 'recording',
      recordingId: 'r1',
      elapsedMs: 250,
      runningSinceMs: 1000,
    ));

    await Future.delayed(const Duration(milliseconds: 50));
    assertEqual(service.state.captureState, RecordingCaptureState.recording, 'entered recording');
    assertEqual(service.state.elapsedMs, 250, 'elapsed ms updated');

    service.dispose();
    streams.dispose();
  });

  await asyncTest('1.3: Terminal latching immunity against stale async resurrection', () async {
    final bridge = MockAdversarialNativeBridge();
    final streams = MockAdversarialStreams();
    final service = RecordingService(bridge: bridge, streams: streams);

    await service.startRecording(recordingId: 'r_term', tenantId: 't1', vaultId: 'v1');
    service.handleFatalError('HARDWARE_AUDIO_PANIC');
    assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'failedTerminal');

    // Stale native packets arrive after fatal error
    streams.stateController.add(const RecordingStateSnapshot(
      state: 'recording',
      recordingId: 'r_term',
      elapsedMs: 5000,
      runningSinceMs: 1000,
    ));
    await Future.delayed(const Duration(milliseconds: 30));
    assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'state not resurrected');

    streams.eventsController.add(const RecordingLifecycleEvent(
      type: 'STATE_CHANGED',
      payload: {'state': 'recovering'},
    ));
    await Future.delayed(const Duration(milliseconds: 30));
    assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'state not corrupted');

    service.dispose();
    streams.dispose();
  });

  await asyncTest('1.4: Unexpected async ordering during stopping flush', () async {
    final bridge = MockAdversarialNativeBridge();
    final streams = MockAdversarialStreams();
    final service = RecordingService(bridge: bridge, streams: streams);

    await service.startRecording(recordingId: 'r_stop', tenantId: 't1', vaultId: 'v1');
    streams.stateController.add(const RecordingStateSnapshot(
      state: 'recording',
      recordingId: 'r_stop',
      elapsedMs: 1000,
      runningSinceMs: 1000,
    ));
    await Future.delayed(const Duration(milliseconds: 30));

    await service.stopRecording(reason: 'userStop');
    assertEqual(service.state.captureState, RecordingCaptureState.stopping, 'stopping');

    // Late recording packet in flight discarded
    streams.stateController.add(const RecordingStateSnapshot(
      state: 'recording',
      recordingId: 'r_stop',
      elapsedMs: 1050,
      runningSinceMs: 1000,
    ));
    await Future.delayed(const Duration(milliseconds: 30));
    assertEqual(service.state.captureState, RecordingCaptureState.stopping, 'recording packet ignored in stopping');

    // Native stopped arrives
    streams.stateController.add(const RecordingStateSnapshot(
      state: 'stopped',
      recordingId: 'r_stop',
      elapsedMs: 1100,
      runningSinceMs: 1000,
    ));
    await Future.delayed(const Duration(milliseconds: 30));
    assertEqual(service.state.captureState, RecordingCaptureState.stopped, 'stopped');

    // Wait 350ms for auto-reset to idle
    await Future.delayed(const Duration(milliseconds: 350));
    assertEqual(service.state.captureState, RecordingCaptureState.idle, 'auto-reset to idle');

    service.dispose();
    streams.dispose();
  });

  // --------------------------------------------------------------------------
  // 2. Timeline Clipping Engine (Contract C5) & Extreme Timestamps
  // --------------------------------------------------------------------------
  print('\n>>> SECTION 2: Timeline Clipping Engine (Contract C5) & Extreme Timestamps');

  test('2.1: Extreme timestamps: negative epoch ms and far-future Year 2099', () {
    // Negative timestamps
    final negBase = TimelineInterval(intervalId: 'neg_cov', startMs: -10000, endMs: -2000, tier: TimelineTier.p4Coverage, itemType: 'Coverage');
    final negHole = TimelineInterval(intervalId: 'neg_chain', startMs: -5000, endMs: -3000, tier: TimelineTier.p1AnalyzingChain, itemType: 'Chain');

    final clippedNeg = TimelineClippingEngine.clipTimelineIntervals([negBase, negHole]);
    assertEqual(clippedNeg.length, 3, '3 pieces in negative interval');
    assertEqual(clippedNeg[0].startMs, -10000, 'neg piece 0 start');
    assertEqual(clippedNeg[0].endMs, -5000, 'neg piece 0 end');
    assertEqual(clippedNeg[1].startMs, -5000, 'neg piece 1 start');
    assertEqual(clippedNeg[1].endMs, -3000, 'neg piece 1 end');
    assertEqual(clippedNeg[2].startMs, -3000, 'neg piece 2 start');
    assertEqual(clippedNeg[2].endMs, -2000, 'neg piece 2 end');

    // Far future epoch ms (> Year 2099: 4,102,444,800,000 ms)
    const tFuture = 4102444800000;
    final futBase = TimelineInterval(intervalId: 'fut_b', startMs: tFuture, endMs: tFuture + 3600000, tier: TimelineTier.p4Coverage, itemType: 'Coverage');
    final futLive = TimelineInterval(intervalId: 'fut_l', startMs: tFuture + 600000, endMs: tFuture + 1200000, tier: TimelineTier.p0LiveCard, itemType: 'Live');
    final clippedFut = TimelineClippingEngine.clipTimelineIntervals([futBase, futLive]);
    assertEqual(clippedFut.length, 3, '3 pieces in far-future interval');
    assertEqual(clippedFut[1].tier, TimelineTier.p0LiveCard, 'futLive preserved');
  });

  test('2.2: Zero-duration filtered, inverted timestamp throws ArgumentError', () {
    final zero = TimelineInterval(intervalId: 'z', startMs: 500, endMs: 500, tier: TimelineTier.p2Activity, itemType: 'Activity');
    final valid = TimelineInterval(intervalId: 'v', startMs: 500, endMs: 1000, tier: TimelineTier.p4Coverage, itemType: 'Coverage');
    final clipped = TimelineClippingEngine.clipTimelineIntervals([zero, valid]);
    assertEqual(clipped.length, 1, 'zero duration pruned');
    assertEqual(clipped[0].intervalId, 'v', 'valid interval kept');

    bool threw = false;
    try {
      TimelineInterval(intervalId: 'inv', startMs: 5000, endMs: 2000, tier: TimelineTier.p2Activity, itemType: 'Activity');
    } on ArgumentError {
      threw = true;
    }
    assertTrue(threw, 'startMs > endMs must throw ArgumentError');
  });

  test('2.3: Touching borders half-open [a, b) and [b, c) never clip each other', () {
    final i1 = TimelineInterval(intervalId: 'i1', startMs: 100, endMs: 200, tier: TimelineTier.p0LiveCard, itemType: 'Live');
    final i2 = TimelineInterval(intervalId: 'i2', startMs: 200, endMs: 300, tier: TimelineTier.p4Coverage, itemType: 'Coverage');
    final i3 = TimelineInterval(intervalId: 'i3', startMs: 300, endMs: 400, tier: TimelineTier.p2Activity, itemType: 'Activity');
    final clipped = TimelineClippingEngine.clipTimelineIntervals([i1, i2, i3]);
    assertEqual(clipped.length, 3, 'all 3 touching intervals kept intact');
    assertEqual(clipped[0].endMs, 200, 'i1 endMs');
    assertEqual(clipped[1].startMs, 200, 'i2 startMs');
    assertEqual(clipped[1].endMs, 300, 'i2 endMs');
    assertEqual(clipped[2].startMs, 300, 'i3 startMs');
  });

  test('2.4: Cascading hole-punching: 40 higher priority holes in large interval', () {
    final base = TimelineInterval(intervalId: 'base', startMs: 0, endMs: 100000, tier: TimelineTier.p4Coverage, itemType: 'Coverage');
    final holes = List.generate(40, (i) => TimelineInterval(
      intervalId: 'hole_$i',
      startMs: i * 2000 + 500,
      endMs: i * 2000 + 1500,
      tier: TimelineTier.p1AnalyzingChain,
      itemType: 'Chain',
    ));

    final clipped = TimelineClippingEngine.clipTimelineIntervals([base, ...holes]);
    assertEqual(clipped.length, 81, '40 holes inside base create 81 disjoint intervals');

    // Verify non-overlapping sorted invariant
    for (var idx = 0; idx < clipped.length - 1; idx++) {
      assertTrue(clipped[idx].endMs <= clipped[idx + 1].startMs, 'Intervals must not overlap');
    }
  });

  test('2.5: Idle gap filling on standard 24h day and non-standard DST days (23h / 25h)', () {
    final intervals = [
      TimelineInterval(intervalId: 'act1', startMs: 10000000, endMs: 20000000, tier: TimelineTier.p2Activity, itemType: 'Activity'),
      TimelineInterval(intervalId: 'act2', startMs: 40000000, endMs: 50000000, tier: TimelineTier.p2Activity, itemType: 'Activity'),
    ];

    // Standard 24h day
    final filled24h = TimelineClippingEngine.fillCalendarIdleSegments(
      placedIntervals: intervals,
      dayStartMs: 0,
      dayEndMs: 86400000,
    );
    assertEqual(filled24h.length, 5, '5 segments for 2 activities in 24h');
    assertEqual(filled24h[0].itemType, 'calendar_idle_segment', 'starts with idle');
    assertEqual(filled24h[0].endMs, 10000000, 'idle 1 end');
    assertEqual(filled24h.last.endMs, 86400000, 'ends at day boundary');

    // 23h DST spring-forward
    final filled23h = TimelineClippingEngine.fillCalendarIdleSegments(
      placedIntervals: intervals,
      dayStartMs: 0,
      dayEndMs: 82800000,
    );
    assertEqual(filled23h.last.endMs, 82800000, '23h day clamped');

    // 25h DST fall-back
    final filled25h = TimelineClippingEngine.fillCalendarIdleSegments(
      placedIntervals: intervals,
      dayStartMs: 0,
      dayEndMs: 90000000,
    );
    assertEqual(filled25h.last.endMs, 90000000, '25h day clamped');
  });

  test('2.6: Midnight crossing clamping and boundary flags', () {
    const dayStart = 0;
    const dayEnd = 86400000;

    // Crossing midnight before
    final before = TimelineInterval(intervalId: 'b4', startMs: -3600000, endMs: 1800000, tier: TimelineTier.p2Activity, itemType: 'Activity');
    final clampedB4 = TimelineClippingEngine.clampToDayBoundary(before, dayStart, dayEnd);
    assertTrue(clampedB4 != null, 'clamped before');
    assertEqual(clampedB4!.startMs, 0, 'clamped start');
    assertEqual(clampedB4.endMs, 1800000, 'clamped end');
    assertTrue(clampedB4.startsBeforePage, 'startsBeforePage is true');
    assertTrue(!clampedB4.endsAfterPage, 'endsAfterPage is false');

    // Crossing midnight after
    final after = TimelineInterval(intervalId: 'aft', startMs: 82800000, endMs: 90000000, tier: TimelineTier.p2Activity, itemType: 'Activity');
    final clampedAft = TimelineClippingEngine.clampToDayBoundary(after, dayStart, dayEnd);
    assertTrue(clampedAft != null, 'clamped after');
    assertEqual(clampedAft!.startMs, 82800000, 'clamped start');
    assertEqual(clampedAft.endMs, 86400000, 'clamped end');
    assertTrue(!clampedAft.startsBeforePage, 'startsBeforePage is false');
    assertTrue(clampedAft.endsAfterPage, 'endsAfterPage is true');

    // Entirely outside
    final outside = TimelineInterval(intervalId: 'out', startMs: -10000, endMs: -5000, tier: TimelineTier.p2Activity, itemType: 'Activity');
    assertEqual(TimelineClippingEngine.clampToDayBoundary(outside, dayStart, dayEnd), null, 'outside dropped');
  });

  // --------------------------------------------------------------------------
  // 3. Dynamic Speaker Resolver (Contract C6) & Tag Replacer Fuzzing
  // --------------------------------------------------------------------------
  print('\n>>> SECTION 3: Dynamic Speaker Resolver (Contract C6) & Tag Replacer');

  test('3.1: 3-tier fallback resolution precedence', () {
    final profiles = {
      'spk_alice': const SpeakerProfile(speakerId: 'spk_alice', name: 'Alice Smith'),
      'spk_suspect': const SpeakerProfile(speakerId: 'spk_suspect', name: '', suspectedName: 'Bob (Suspect)'),
    };
    final roster = {
      'spk_suspect': const MemoryEventSpeakerRosterEntry(speakerId: 'spk_suspect', suspectedName: 'Bob Roster Suspect'),
    };

    final resolver = SessionSpeakerResolver(speakerProfiles: profiles, activeRoster: roster);

    assertEqual(resolver.resolve(speakerId: 'spk_alice'), 'Alice Smith', 'Tier 1 enrolled');
    assertEqual(resolver.resolve(speakerId: 'spk_suspect'), 'Bob Roster Suspect', 'Tier 2 active roster');
    assertEqual(resolver.resolve(speakerId: 'spk_unseen_1'), 'Speaker 1', 'Tier 3 sequential anon 1');
    assertEqual(resolver.resolve(speakerId: 'spk_unseen_2'), 'Speaker 2', 'Tier 3 sequential anon 2');
    assertEqual(resolver.resolve(speakerId: 'spk_unseen_1'), 'Speaker 1', 'Deterministic index reuse');
  });

  test('3.2: Tag replacer fuzzed with nested, unclosed, empty, and malformed tags', () {
    final resolver = SessionSpeakerResolver(speakerProfiles: {
      'spk_01': const SpeakerProfile(speakerId: 'spk_01', name: 'Alice'),
      'spk_02': const SpeakerProfile(speakerId: 'spk_02', name: 'Bob'),
    });

    assertEqual(
      resolver.replaceSpeakerTags('Hello [speaker:spk_01] and <speaker:spk_02>!'),
      'Hello Alice and Bob!',
      'standard tags replaced',
    );

    // Nested brackets
    assertEqual(
      resolver.replaceSpeakerTags('[[speaker:spk_01]]'),
      '[Alice]',
      'nested brackets handled safely',
    );

    // Unclosed bracket
    assertEqual(
      resolver.replaceSpeakerTags('Unclosed [speaker:spk_01 text'),
      'Unclosed [speaker:spk_01 text',
      'unclosed bracket preserved',
    );

    // Empty tag
    assertEqual(
      resolver.replaceSpeakerTags('[speaker:]'),
      '[speaker:]',
      'empty tag preserved',
    );

    // Tag with space
    assertEqual(
      resolver.replaceSpeakerTags('[speaker: spk_01]'),
      '[speaker: spk_01]',
      'space in tag preserved',
    );
  });

  test('3.3: High volume scale: 5,000 tags replaced in single document', () {
    final resolver = SessionSpeakerResolver(speakerProfiles: {
      for (var i = 0; i < 10; i++)
        'spk_$i': SpeakerProfile(speakerId: 'spk_$i', name: 'SpeakerName_$i'),
    });

    final buffer = StringBuffer();
    for (var i = 0; i < 2500; i++) {
      buffer.writeln('Turn $i: [speaker:spk_${i % 10}] says hi to <speaker:spk_${(i + 1) % 10}>.');
    }
    final fullText = buffer.toString();

    final sw = Stopwatch()..start();
    final replaced = resolver.replaceSpeakerTags(fullText);
    sw.stop();

    assertTrue(!replaced.contains('[speaker:'), 'all bracket tags replaced');
    assertTrue(!replaced.contains('<speaker:'), 'all angle tags replaced');
    assertTrue(sw.elapsedMilliseconds < 100, '5000 tags replaced in ${sw.elapsedMilliseconds}ms (<100ms)');
  });

  // --------------------------------------------------------------------------
  // 4. Cross-Layer IPC Native Bridge & Ring Buffer Saturation
  // --------------------------------------------------------------------------
  print('\n>>> SECTION 4: Cross-Layer IPC Native Bridge & Ring Buffer Saturation');

  test('4.1: Auth7Tuple validation, type safety, and error handling', () {
    final validMap = {
      'accessToken': 'tok_access_123',
      'refreshToken': 'tok_refresh_456',
      'expiresAt': 1790000000,
      'baseUrl': 'https://api.hemory.app',
      'slotConnectionType': 'direct',
      'slotDpUnitId': 'dp_unit_01',
      'slotTenantId': 'tenant_prod',
    };

    final tuple = Auth7Tuple.fromMap(validMap);
    assertEqual(tuple.accessToken, 'tok_access_123', 'accessToken');
    assertEqual(tuple.expiresAt, 1790000000, 'expiresAt');

    // Missing key
    for (final k in validMap.keys) {
      final bad = Map<String, dynamic>.from(validMap)..remove(k);
      bool threw = false;
      try {
        Auth7Tuple.fromMap(bad);
      } on ArgumentError {
        threw = true;
      }
      assertTrue(threw, 'Missing key $k must throw');
    }

    // Whitespace only
    for (final ws in ['', ' ', '   ', '\t', '\n', '\r\n']) {
      final bad = Map<String, dynamic>.from(validMap)..['accessToken'] = ws;
      bool threw = false;
      try {
        Auth7Tuple.fromMap(bad);
      } on ArgumentError {
        threw = true;
      }
      assertTrue(threw, 'Whitespace accessToken must throw');
    }

    // Negative / non-positive timestamps
    for (final badTs in [0, -1, -500]) {
      final bad = Map<String, dynamic>.from(validMap)..['expiresAt'] = badTs;
      bool threw = false;
      try {
        Auth7Tuple.fromMap(bad);
      } on ArgumentError {
        threw = true;
      }
      assertTrue(threw, 'Timestamp $badTs must throw');
    }
  });

  test('4.2: Unicode Cf formatting characters empirical analysis', () {
    // Pure Cf formatting tokens: U+200B, U+200C, U+200D, U+200E, U+200F, U+2060
    const nonStrippedCf = ['\u200B', '\u200C', '\u200D', '\u200E', '\u200F', '\u2060'];

    for (final cf in nonStrippedCf) {
      // Demonstrates empirical vulnerability: Dart's trim().isEmpty is FALSE for these formatting characters!
      final trimBypass = !cf.trim().isEmpty;
      assertTrue(trimBypass, 'Standard trim() does not strip Unicode formatting $cf');
    }

    // U+FEFF (BOM) is stripped by Dart trim
    assertTrue('\uFEFF'.trim().isEmpty, 'BOM U+FEFF is stripped by Dart trim');
  });

  test('4.3: Metering power formula normalization [-60.0, 0.0] -> [0.05, 1.0]', () {
    assertEqual(MeteringSnapshot.fromMap({'rmsDb': 0.0}).normalizedPower, 1.0, '0 dBFS is 1.0');
    assertEqual(MeteringSnapshot.fromMap({'rmsDb': -30.0}).normalizedPower, 0.5, '-30 dBFS is 0.5');
    assertEqual(MeteringSnapshot.fromMap({'rmsDb': -60.0}).normalizedPower, 0.05, '-60 dBFS is 0.05');
    assertEqual(MeteringSnapshot.fromMap({'rmsDb': -120.0}).normalizedPower, 0.05, '-120 dBFS clamped to 0.05');
    assertEqual(MeteringSnapshot.fromMap({'rmsDb': 20.0}).normalizedPower, 1.0, '+20 dBFS clamped to 1.0');
  });

  // --------------------------------------------------------------------------
  // 5. Local Drift Database Cache & Mutation Sync Engine
  // --------------------------------------------------------------------------
  print('\n>>> SECTION 5: Local Database Cache & Mutation Sync Engine');

  await asyncTest('5.1: Session insertion, segment insertion and CASCADE deletion', () async {
    final db = LocalDatabase();

    const session = RecordingSessionRecord(
      liveSessionId: 'sess_1',
      tenantId: 't1',
      vaultId: 'v1',
      userId: 'u1',
      localDayKey: '2026-11-10',
      startedAt: 1000,
      createdAt: 1000,
      updatedAt: 1000,
    );
    await db.insertSession(session);

    for (var i = 0; i < 3; i++) {
      await db.insertSegment(RecordingSegmentRecord(
        segmentId: 'seg_$i',
        liveSessionId: 'sess_1',
        segmentIndex: i,
        startedAt: 1000 + i * 1800000,
        durationMs: 1800000,
        localFileKey: '/data/seg_$i.aac',
        metaFileKey: '/data/seg_$i.meta',
        byteSize: 10800000,
        sha256: 'sha_$i',
        splitReason: 'silence_window',
        createdAt: 1000 + i * 1800000,
      ));
    }

    assertEqual(db.getSegmentsForSession('sess_1').length, 3, '3 segments before delete');

    // CASCADE deletion
    await db.deleteSession('sess_1');
    assertEqual(db.getSegmentsForSession('sess_1').length, 0, '0 segments after CASCADE delete');

    db.dispose();
  });

  await asyncTest('5.2: Mutation sync causal cursor tracking and mutation replay', () async {
    final db = LocalDatabase();

    assertEqual(db.getSyncCursor('vault_1'), 0, 'initial cursor is 0');

    // Apply mutation 1: speaker renamed
    await db.upsertSpeakerProfile(const SpeakerProfileRecord(
      speakerId: 'spk_1',
      tenantId: 't1',
      vaultId: 'vault_1',
      userId: 'u1',
      name: 'Old Name',
      createdAt: 1000,
      updatedAt: 1000,
    ));

    await db.applyMutation(const RecordingMutation(
      cursor: 101,
      mutationType: 'speaker_renamed',
      entityId: 'spk_1',
      payload: {'name': 'New Validated Name'},
      vaultId: 'vault_1',
    ));

    assertEqual(db.getSyncCursor('vault_1'), 101, 'cursor advanced to 101');
    assertEqual(db.getSpeakers('vault_1').first.name, 'New Validated Name', 'speaker renamed reactively');

    db.dispose();
  });

  print('\n=================================================================');
  print('  TIER 5 ADVERSARIAL TEST SUMMARY:');
  print('  Total Tests: $totalTests, Passed: $passedTests, Failed: ${totalTests - passedTests}');
  print('=================================================================');

  if (passedTests != totalTests) {
    exitCode = 1;
  }
}
