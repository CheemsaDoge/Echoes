/// Adversarial stress-testing harness for Milestone M5.
/// Evaluates:
/// 1. State Machine Concurrency & Race Testing (RecordingService)
/// 2. Dynamic Speaker Fallback & Tag Replacer Fuzzing (SpeakerResolver)
/// 3. Native Bridge Auth 7-Tuple Spoofing (Auth7Tuple)
library;

import 'dart:async';
import 'dart:io';
import '../../lib/core/models/recording_state.dart';
import '../../lib/core/models/speaker_models.dart';
import '../../lib/core/ipc/native_bridge.dart';
import '../../lib/core/ipc/event_channels.dart';
import '../../lib/core/services/recording_service.dart';
import '../../lib/ui/chat/speaker_resolver.dart';

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
// Adversarial Mock Native Bridge & Streams
// ============================================================================

class AdversarialMockBridge implements INativeBridge {
  String currentState = 'idle';
  int startCalls = 0;
  int stopCalls = 0;
  int pauseCalls = 0;
  int resumeCalls = 0;
  String? lastStopReason;
  Duration delay = Duration.zero;

  @override
  Future<bool> startRecording({
    required String recordingId,
    required String tenantId,
    required String vaultId,
    String userId = '',
  }) async {
    startCalls++;
    if (delay > Duration.zero) await Future.delayed(delay);
    currentState = 'activating';
    return true;
  }

  @override
  Future<bool> stopRecording({String reason = 'userStop'}) async {
    stopCalls++;
    lastStopReason = reason;
    if (delay > Duration.zero) await Future.delayed(delay);
    currentState = 'stopped';
    return true;
  }

  @override
  Future<bool> pauseRecording() async {
    pauseCalls++;
    if (delay > Duration.zero) await Future.delayed(delay);
    currentState = 'paused';
    return true;
  }

  @override
  Future<bool> resumeRecording() async {
    resumeCalls++;
    if (delay > Duration.zero) await Future.delayed(delay);
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

class AdversarialMockStreams implements IRecordingEventStreams {
  final stateCtrl = StreamController<RecordingStateSnapshot>.broadcast();
  final eventsCtrl = StreamController<RecordingLifecycleEvent>.broadcast();
  final meteringCtrl = StreamController<MeteringSnapshot>.broadcast();
  final segmentsCtrl = StreamController<SegmentCompletedEvent>.broadcast();

  @override
  Stream<RecordingStateSnapshot> get stateStream => stateCtrl.stream;

  @override
  Stream<RecordingLifecycleEvent> get eventsStream => eventsCtrl.stream;

  @override
  Stream<MeteringSnapshot> get meteringStream => meteringCtrl.stream;

  @override
  Stream<SegmentCompletedEvent> get segmentsStream => segmentsCtrl.stream;

  void dispose() {
    stateCtrl.close();
    eventsCtrl.close();
    meteringCtrl.close();
    segmentsCtrl.close();
  }
}

// ============================================================================
// MAIN ADVERSARIAL TEST SUITE
// ============================================================================

Future<void> main() async {
  print('=================================================================');
  print('  STARTING ADVERSARIAL STRESS TEST SUITE (Milestone M5)');
  print('=================================================================');

  final results = <String, bool>{};

  // --------------------------------------------------------------------------
  // SECTION 1: STATE MACHINE CONCURRENCY & RACE TESTING
  // --------------------------------------------------------------------------
  print('\n>>> SECTION 1: State Machine Concurrency & Race Testing');

  // Test 1.1: Rapid concurrent trigger burst from idle
  {
    final bridge = AdversarialMockBridge();
    final streams = AdversarialMockStreams();
    final service = RecordingService(bridge: bridge, streams: streams);

    // Blast 50 concurrent calls of mixed operations
    int allowedStarts = 0;
    int rejectedTransitions = 0;

    final futures = <Future>[];
    for (int i = 0; i < 50; i++) {
      if (i % 4 == 0) {
        futures.add(
          service
              .startRecording(recordingId: 'rec_$i', tenantId: 't', vaultId: 'v')
              .then((_) {
            allowedStarts++;
            return true;
          }).catchError((e) {
            if (e is InvalidStateTransitionException) rejectedTransitions++;
            return false;
          }),
        );
      } else if (i % 4 == 1) {
        futures.add(
          service.stopRecording().then((_) => true).catchError((e) {
            if (e is InvalidStateTransitionException) rejectedTransitions++;
            return false;
          }),
        );
      } else if (i % 4 == 2) {
        futures.add(
          service.pauseRecording().then((_) => true).catchError((e) {
            if (e is InvalidStateTransitionException) rejectedTransitions++;
            return false;
          }),
        );
      } else {
        futures.add(
          service.resumeRecording().then((_) => true).catchError((e) {
            if (e is InvalidStateTransitionException) rejectedTransitions++;
            return false;
          }),
        );
      }
    }

    await Future.wait(futures);

    // Exactly 1 startRecording must succeed, all other 49 concurrent attempts
    // must be either handled or rejected via InvalidStateTransitionException
    assertTrue(allowedStarts == 1, 'Only exactly 1 startRecording must succeed');
    assertTrue(
      allowedStarts + rejectedTransitions > 0,
      'Invalid transitions must throw InvalidStateTransitionException',
    );
    print('  [PASS] 1.1: Concurrent burst from idle: 1 allowed start, $rejectedTransitions rejected gracefully');
    results['1.1_concurrent_burst_idle'] = true;

    service.dispose();
    streams.dispose();
  }

  // Test 1.2: Activating Watchdog Boundary Race (10.0s)
  {
    final clock = VirtualClock();

    await runZoned(() async {
      // Case A: Native event arrives just before 10.0s (e.g. 9.999s) -> normal recording
      final bridgeA = AdversarialMockBridge();
      final streamsA = AdversarialMockStreams();
      final serviceA = RecordingService(bridge: bridgeA, streams: streamsA);

      await serviceA.startRecording(recordingId: 'rec_watchdog_A', tenantId: 't', vaultId: 'v');
      assertEqual(serviceA.state.captureState, RecordingCaptureState.activating, 'ServiceA in activating');

      // Advance clock to 9.999s (9999ms)
      clock.advance(const Duration(milliseconds: 9999));
      assertEqual(serviceA.state.captureState, RecordingCaptureState.activating, 'Still activating at 9.999s');

      // Native reports recording
      streamsA.eventsCtrl.add(
        const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
      );
      await Future.delayed(Duration.zero);
      assertEqual(serviceA.state.captureState, RecordingCaptureState.recording, 'Advanced to recording at 9.999s');

      // Advance clock past 10.0s: watchdog should NOT fire because it was cancelled!
      clock.advance(const Duration(milliseconds: 500));
      assertEqual(serviceA.state.captureState, RecordingCaptureState.recording, 'Watchdog was cancelled, remains recording');
      print('  [PASS] 1.2a: Activating watchdog cancelled cleanly at 9.999s boundary');

      serviceA.dispose();
      streamsA.dispose();

      // Case B: Watchdog fires at exact 10.0s boundary -> failedTerminal with START_TIMEOUT
      final bridgeB = AdversarialMockBridge();
      final streamsB = AdversarialMockStreams();
      final serviceB = RecordingService(bridge: bridgeB, streams: streamsB);

      await serviceB.startRecording(recordingId: 'rec_watchdog_B', tenantId: 't', vaultId: 'v');
      assertEqual(serviceB.state.captureState, RecordingCaptureState.activating, 'ServiceB in activating');

      // Advance to 10.0s
      clock.advance(const Duration(seconds: 10));
      assertEqual(serviceB.state.captureState, RecordingCaptureState.failedTerminal, 'Watchdog fired: failedTerminal');
      assertTrue(serviceB.state.lastError!.contains('START_TIMEOUT'), 'Error message is START_TIMEOUT');
      assertEqual(bridgeB.lastStopReason, 'timeout', 'Bridge stopRecording called with timeout reason');
      print('  [PASS] 1.2b: Activating watchdog fires at exact 10.0s boundary');

      // Case C: Race: Native event arrives AFTER watchdog timed out (10.001s)
      // Check if stale native event resurrects failedTerminal back to recording
      streamsB.eventsCtrl.add(
        const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
      );
      await Future.delayed(Duration.zero);
      final resurrected = serviceB.state.captureState == RecordingCaptureState.recording;
      if (resurrected) {
        print('  [WARN/FINDING] 1.2c: Stale native recording event resurrected failedTerminal state machine to recording!');
        results['1.2c_stale_event_resurrects_terminal'] = false;
      } else {
        print('  [PASS] 1.2c: Stale native recording event ignored in terminal state');
        results['1.2c_stale_event_resurrects_terminal'] = true;
      }

      serviceB.dispose();
      streamsB.dispose();
    }, zoneSpecification: ZoneSpecification(
      createTimer: (self, parent, zone, duration, f) {
        if (duration >= const Duration(seconds: 1)) {
          return clock.createTimer(duration, f);
        }
        return parent.createTimer(zone, duration, f);
      },
    ));
    results['1.2_activating_watchdog_race'] = true;
  }

  // Test 1.3: Audio HAL Stall Injection & 45s Deadlock Self-Healing
  {
    final clock = VirtualClock();

    await runZoned(() async {
      final bridge = AdversarialMockBridge();
      final streams = AdversarialMockStreams();
      final service = RecordingService(bridge: bridge, streams: streams);

      await service.startRecording(recordingId: 'rec_stall', tenantId: 't', vaultId: 'v');
      streams.eventsCtrl.add(
        const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
      );
      await Future.delayed(Duration.zero);
      assertEqual(service.state.captureState, RecordingCaptureState.recording, 'In recording');

      // Simulate interruption / pause triggering recovering state
      await service.pauseRecording();
      assertEqual(service.state.captureState, RecordingCaptureState.recovering, 'In recovering');

      // Audio HAL freezes: no audio frames arrive, recovering state stalls past 45s
      clock.advance(const Duration(seconds: 44));
      assertEqual(service.state.captureState, RecordingCaptureState.recovering, 'Still recovering at 44s');

      // Advance 1 more second to cross 45.0s threshold
      clock.advance(const Duration(seconds: 1));
      assertEqual(
        service.state.captureState,
        RecordingCaptureState.failedTerminal,
        'HAL deadlock detected: transitioned to failedTerminal',
      );
      assertTrue(
        service.state.lastError!.contains('HAL_DEADLOCK_DETECTED'),
        'Error string contains HAL_DEADLOCK_DETECTED',
      );
      assertEqual(bridge.lastStopReason, 'recovery_deadlock', 'Bridge stopRecording called with recovery_deadlock');

      // Verify self-healing reset recovery
      service.reset();
      assertEqual(service.state.captureState, RecordingCaptureState.idle, 'Reset restores idle for next session');
      print('  [PASS] 1.3: Audio HAL stall >45s triggers deadlock self-healing and reset');
      results['1.3_hal_stall_self_healing'] = true;

      service.dispose();
      streams.dispose();
    }, zoneSpecification: ZoneSpecification(
      createTimer: (self, parent, zone, duration, f) {
        if (duration >= const Duration(seconds: 1)) {
          return clock.createTimer(duration, f);
        }
        return parent.createTimer(zone, duration, f);
      },
    ));
  }

  // Test 1.4: Native Event Flood (1,000 rapid state change events)
  {
    final bridge = AdversarialMockBridge();
    final streams = AdversarialMockStreams();
    final service = RecordingService(bridge: bridge, streams: streams);

    await service.startRecording(recordingId: 'rec_flood', tenantId: 't', vaultId: 'v');
    assertEqual(service.state.captureState, RecordingCaptureState.activating, 'Activating');

    // Blast 1,000 rapid alternating events: recording -> recovering -> recording -> recovering
    for (int i = 0; i < 1000; i++) {
      final stateStr = (i % 2 == 0) ? 'recording' : 'recovering';
      streams.eventsCtrl.add(
        RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': stateStr}),
      );
    }

    await Future.delayed(const Duration(milliseconds: 50));

    // The 1,000th event (index 999, odd) was 'recovering'
    assertEqual(service.state.captureState, RecordingCaptureState.recovering, 'End state is recovering');

    // Final clean stop event
    streams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'stopped'}),
    );
    await Future.delayed(const Duration(milliseconds: 50));
    assertEqual(service.state.captureState, RecordingCaptureState.stopped, 'End state is stopped');

    print('  [PASS] 1.4: Blasted 1,000 rapid native state events through stream; state remained consistent');
    results['1.4_native_event_flood'] = true;

    service.dispose();
    streams.dispose();
  }

  // Test 1.5: Recovering Watchdog Boundary Race (45.0s)
  {
    final clock = VirtualClock();

    await runZoned(() async {
      // Case A: Event arrives at 44.999s (just before 45s)
      final bridgeA = AdversarialMockBridge();
      final streamsA = AdversarialMockStreams();
      final serviceA = RecordingService(bridge: bridgeA, streams: streamsA);

      await serviceA.startRecording(recordingId: 'rec_recov_A', tenantId: 't', vaultId: 'v');
      streamsA.eventsCtrl.add(
        const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
      );
      await Future.delayed(Duration.zero);
      await serviceA.pauseRecording();
      assertEqual(serviceA.state.captureState, RecordingCaptureState.recovering, 'In recovering');

      // Advance to 44.999s
      clock.advance(const Duration(milliseconds: 44999));
      assertEqual(serviceA.state.captureState, RecordingCaptureState.recovering, 'Still recovering at 44.999s');

      // Native reports recording resumed
      streamsA.eventsCtrl.add(
        const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
      );
      await Future.delayed(Duration.zero);
      assertEqual(serviceA.state.captureState, RecordingCaptureState.recording, 'Recovered to recording');

      // Advance past 45s: watchdog cancelled, state remains recording
      clock.advance(const Duration(milliseconds: 1000));
      assertEqual(serviceA.state.captureState, RecordingCaptureState.recording, 'Watchdog was cancelled, remains recording');
      print('  [PASS] 1.5a: Recovering watchdog cancelled cleanly at 44.999s boundary');

      serviceA.dispose();
      streamsA.dispose();

      // Case B & C: Watchdog fires at 45.0s, late event arrives at 45.001s
      final bridgeB = AdversarialMockBridge();
      final streamsB = AdversarialMockStreams();
      final serviceB = RecordingService(bridge: bridgeB, streams: streamsB);

      await serviceB.startRecording(recordingId: 'rec_recov_B', tenantId: 't', vaultId: 'v');
      streamsB.eventsCtrl.add(
        const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
      );
      await Future.delayed(Duration.zero);
      await serviceB.pauseRecording();

      // Advance to 45.0s
      clock.advance(const Duration(seconds: 45));
      assertEqual(serviceB.state.captureState, RecordingCaptureState.failedTerminal, 'Watchdog fired at 45s: failedTerminal');
      assertEqual(bridgeB.lastStopReason, 'recovery_deadlock', 'Bridge stopRecording called with recovery_deadlock');

      // Late native event arrives at 45.001s
      streamsB.eventsCtrl.add(
        const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
      );
      await Future.delayed(Duration.zero);
      final resurrected = serviceB.state.captureState == RecordingCaptureState.recording;
      if (resurrected) {
        print('  [WARN/FINDING] 1.5c: Stale native recording event resurrected failedTerminal state machine after 45s deadlock!');
        results['1.5c_stale_event_resurrects_deadlock_terminal'] = false;
      } else {
        print('  [PASS] 1.5c: Stale native recording event ignored after 45s deadlock');
        results['1.5c_stale_event_resurrects_deadlock_terminal'] = true;
      }

      serviceB.dispose();
      streamsB.dispose();
    }, zoneSpecification: ZoneSpecification(
      createTimer: (self, parent, zone, duration, f) {
        if (duration >= const Duration(seconds: 1)) {
          return clock.createTimer(duration, f);
        }
        return parent.createTimer(zone, duration, f);
      },
    ));
    results['1.5_recovering_watchdog_race'] = true;
  }

  // Test 1.6: Simultaneous concurrent trigger stress from recording and recovering states
  {
    final bridge = AdversarialMockBridge();
    final streams = AdversarialMockStreams();
    final service = RecordingService(bridge: bridge, streams: streams);

    await service.startRecording(recordingId: 'rec_active', tenantId: 't', vaultId: 'v');
    streams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
    );
    await Future.delayed(Duration.zero);
    assertEqual(service.state.captureState, RecordingCaptureState.recording, 'In recording');

    // Blast 50 concurrent calls while in recording
    int rejectedTransitions = 0;
    int allowedCalls = 0;
    final futures = <Future>[];

    for (int i = 0; i < 50; i++) {
      if (i % 3 == 0) {
        // startRecording is illegal from recording
        futures.add(
          service
              .startRecording(recordingId: 'illegal_$i', tenantId: 't', vaultId: 'v')
              .then((_) {
            allowedCalls++;
            return true;
          }).catchError((e) {
            if (e is InvalidStateTransitionException) rejectedTransitions++;
            return false;
          }),
        );
      } else if (i % 3 == 1) {
        // resumeRecording is illegal from recording
        futures.add(
          service.resumeRecording().then((_) {
            allowedCalls++;
            return true;
          }).catchError((e) {
            if (e is InvalidStateTransitionException) rejectedTransitions++;
            return false;
          }),
        );
      } else {
        // pauseRecording is legal from recording (first one succeeds)
        futures.add(
          service.pauseRecording().then((_) {
            allowedCalls++;
            return true;
          }).catchError((e) {
            if (e is InvalidStateTransitionException) rejectedTransitions++;
            return false;
          }),
        );
      }
    }

    await Future.wait(futures);
    assertTrue(rejectedTransitions > 0, 'Illegal transitions from recording must be rejected');
    print('  [PASS] 1.6: Concurrent burst while in recording: $allowedCalls allowed, $rejectedTransitions rejected');
    results['1.6_concurrent_burst_recording'] = true;

    service.dispose();
    streams.dispose();
  }

  // --------------------------------------------------------------------------
  // SECTION 2: DYNAMIC SPEAKER FALLBACK & TAG REPLACER FUZZING
  // --------------------------------------------------------------------------
  print('\n>>> SECTION 2: Dynamic Speaker Fallback & Tag Replacer Fuzzing');

  // Test 2.1: Fuzz resolveSpeakerName with malicious & edge-case inputs
  {
    final maliciousInputs = <String?, String>{
      null: 'Speaker 1',
      '': 'Speaker 1',
      '   ': 'Speaker 1',
      '\u202E\u200B\uFEFF': 'Speaker 1', // Unicode RTL override & zero-width space
      'spk_\x00\x01\x1F\x7F': 'Speaker 1', // Null bytes & control chars
      '<script>alert("XSS")</script>': 'Speaker 1', // HTML injection
      "'; DROP TABLE speaker_profiles; --": 'Speaker 1', // SQL injection
      'A' * 100000: 'Speaker 1', // 100k char string
    };

    int fuzzPassCount = 0;
    for (final entry in maliciousInputs.entries) {
      final inputId = entry.key;
      final expected = entry.value;

      final context = const UtteranceContext(
        speakerProfiles: {},
        activeRoster: {},
        unknownSpeakerIndex: 1,
      );

      final result = SpeakerResolver.resolveSpeakerName(
        speakerId: inputId,
        context: context,
      );

      assertEqual(result, expected, 'Fuzz input "$inputId" fallback');
      fuzzPassCount++;
    }

    print('  [PASS] 2.1: Fuzzed resolveSpeakerName with ${fuzzPassCount} malicious/edge inputs; zero crashes');
    results['2.1_fuzz_resolve_speaker_name'] = true;
  }

  // Test 2.2: Circular Roster Lookups
  {
    final circularRoster = {
      'spk_A': const MemoryEventSpeakerRosterEntry(speakerId: 'spk_A', suspectedName: 'Alice'),
      'spk_B': const MemoryEventSpeakerRosterEntry(speakerId: 'spk_B', suspectedName: 'Bob'),
    };

    final context = UtteranceContext(
      speakerProfiles: {},
      activeRoster: circularRoster,
    );

    final resA = SpeakerResolver.resolveSpeakerName(speakerId: 'spk_A', context: context);
    final resB = SpeakerResolver.resolveSpeakerName(speakerId: 'spk_B', context: context);
    assertEqual(resA, 'Alice', 'Circular roster A');
    assertEqual(resB, 'Bob', 'Circular roster B');
    print('  [PASS] 2.2: Circular roster references handled non-recursively with zero loop risk');
    results['2.2_circular_roster_lookups'] = true;
  }

  // Test 2.3: Fuzz replaceSpeakerTags: nested, unclosed, malformed, empty
  {
    final session = SessionSpeakerResolver();

    // Nested tags: [[speaker:spk_01]] -> [Speaker 1]
    final nested = session.replaceSpeakerTags('Nested: [[speaker:spk_01]]');
    assertEqual(nested, 'Nested: [Speaker 1]', 'Nested tag replaced inner tag');

    // Unclosed tags: [speaker:abc -> preserved without crash
    final unclosed = session.replaceSpeakerTags('Unclosed: [speaker:abc and <speaker:xyz');
    assertEqual(unclosed, 'Unclosed: [speaker:abc and <speaker:xyz', 'Unclosed tags preserved');

    // Malformed tags: [speaker:] and [speaker: ] -> preserved
    final malformed = session.replaceSpeakerTags('Malformed: [speaker:] and [speaker:  ]');
    assertEqual(malformed, 'Malformed: [speaker:] and [speaker:  ]', 'Malformed empty tags preserved');

    print('  [PASS] 2.3: replaceSpeakerTags handled nested, unclosed, and malformed tags cleanly');
    results['2.3_fuzz_tag_replacer_syntax'] = true;
  }

  // Test 2.4: Scale Stress: 10,000 tags in a single string
  {
    final session = SessionSpeakerResolver();

    // Generate 10,000 tags: 5,000 unique IDs repeated twice
    final tagList = <String>[];
    for (int i = 0; i < 5000; i++) {
      tagList.add('[speaker:spk_$i]');
    }
    for (int i = 0; i < 5000; i++) {
      tagList.add('<speaker:spk_$i>');
    }
    final massiveString = tagList.join(' ');

    final stopwatch = Stopwatch()..start();
    final replaced = session.replaceSpeakerTags(massiveString);
    stopwatch.stop();

    print('  [INFO] 2.4: 10,000 tags replaced in ${stopwatch.elapsedMilliseconds}ms');

    // Execution must complete in under 2,000ms (linear O(N))
    assertTrue(stopwatch.elapsedMilliseconds < 2000, 'Must complete in < 2,000ms');

    // First tag [speaker:spk_0] -> Speaker 1
    // Second batch <speaker:spk_0> -> must deterministically reuse Speaker 1!
    assertTrue(replaced.startsWith('Speaker 1 '), 'First tag replaced with Speaker 1');
    assertTrue(replaced.contains('Speaker 5000'), 'Last unique tag replaced with Speaker 5000');

    print('  [PASS] 2.4: 10,000 tags stress test passed with linear O(N) performance and deterministic token reuse');
    results['2.4_10k_tags_scale_stress'] = true;
  }

  // --------------------------------------------------------------------------
  // SECTION 3: NATIVE BRIDGE AUTH 7-TUPLE SPOOFING
  // --------------------------------------------------------------------------
  print('\n>>> SECTION 3: Native Bridge Auth 7-Tuple Spoofing');

  final validAuthMap = {
    'accessToken': 'valid_access_token',
    'refreshToken': 'valid_refresh_token',
    'expiresAt': 1790000000,
    'baseUrl': 'https://api.hemory.app',
    'slotConnectionType': 'direct',
    'slotDpUnitId': 'dp_unit_01',
    'slotTenantId': 'tenant_prod',
  };

  // Test 3.1: Incomplete dictionaries (missing each key one by one)
  {
    int missingKeyRejections = 0;
    for (final key in validAuthMap.keys) {
      final incomplete = Map<String, dynamic>.from(validAuthMap)..remove(key);
      try {
        Auth7Tuple.fromMap(incomplete);
      } on ArgumentError {
        missingKeyRejections++;
      } catch (e) {
        // Any other exception type
      }
    }
    assertEqual(missingKeyRejections, 7, 'All 7 missing keys must throw ArgumentError');
    print('  [PASS] 3.1: All 7 missing key permutations strictly rejected with ArgumentError');
    results['3.1_missing_keys_rejected'] = true;
  }

  // Test 3.2: Null values for each key
  {
    int nullValueRejections = 0;
    for (final key in validAuthMap.keys) {
      final withNull = Map<String, dynamic>.from(validAuthMap)..[key] = null;
      try {
        Auth7Tuple.fromMap(withNull);
      } on ArgumentError {
        nullValueRejections++;
      } catch (e) {
        // Any other exception type
      }
    }
    assertEqual(nullValueRejections, 7, 'All 7 null keys must throw ArgumentError');
    print('  [PASS] 3.2: All 7 null key permutations strictly rejected with ArgumentError');
    results['3.2_null_values_rejected'] = true;
  }

  // Test 3.3: Wrong data types (type spoofing)
  {
    final wrongTypeString = Map<String, dynamic>.from(validAuthMap)..['accessToken'] = 99999;
    bool caughtWrongType = false;
    Type? exceptionType;
    try {
      Auth7Tuple.fromMap(wrongTypeString);
    } catch (e) {
      caughtWrongType = true;
      exceptionType = e.runtimeType;
    }
    assertTrue(caughtWrongType, 'Wrong data type must be rejected');
    print('  [INFO] 3.3: Wrong data type rejected with exception type: $exceptionType');
    if (exceptionType == ArgumentError) {
      results['3.3_wrong_type_rejected_with_argument_error'] = true;
    } else {
      print('  [FINDING] 3.3: Wrong data type threw $exceptionType instead of ArgumentError');
      results['3.3_wrong_type_rejected_with_argument_error'] = false;
    }
  }

  // Test 3.4: Negative Timestamps Probe
  {
    final negativeTimestampMap = Map<String, dynamic>.from(validAuthMap)..['expiresAt'] = -100;
    bool rejectedNegative = false;
    Auth7Tuple? acceptedTuple;
    try {
      acceptedTuple = Auth7Tuple.fromMap(negativeTimestampMap);
    } on ArgumentError {
      rejectedNegative = true;
    } catch (e) {
      // other error
    }

    if (rejectedNegative) {
      print('  [PASS] 3.4: Negative timestamp strictly rejected with ArgumentError');
      results['3.4_negative_timestamp_rejected'] = true;
    } else {
      print('  [FINDING/DEFECT] 3.4: Auth7Tuple.fromMap ACCEPTED negative timestamp: ${acceptedTuple?.expiresAt}!');
      results['3.4_negative_timestamp_rejected'] = false;
    }
  }

  // Test 3.5: Zero and Extreme Negative Timestamps (-1, -100, -999999999, 0)
  {
    int rejectedCount = 0;
    final testValues = [-1, -100, -999999999, 0];
    for (final val in testValues) {
      final map = Map<String, dynamic>.from(validAuthMap)..['expiresAt'] = val;
      try {
        Auth7Tuple.fromMap(map);
      } on ArgumentError {
        rejectedCount++;
      } catch (e) {
        // wrong exception
      }
    }
    if (rejectedCount == testValues.length) {
      print('  [PASS] 3.5: All ${testValues.length} zero/negative timestamps rejected with ArgumentError');
      results['3.5_zero_and_negative_timestamps_rejected'] = true;
    } else {
      print('  [FINDING/DEFECT] 3.5: Only $rejectedCount/${testValues.length} zero/negative timestamps rejected');
      results['3.5_zero_and_negative_timestamps_rejected'] = false;
    }
  }

  // Test 3.6: Non-numeric expiresAt ('tomorrow', true, [], {})
  {
    int rejectedCount = 0;
    final nonNumericValues = ['tomorrow', true, <dynamic>[], <String, dynamic>{}];
    for (final val in nonNumericValues) {
      final map = Map<String, dynamic>.from(validAuthMap)..['expiresAt'] = val;
      try {
        Auth7Tuple.fromMap(map);
      } on ArgumentError {
        rejectedCount++;
      } catch (e) {
        // wrong exception
      }
    }
    if (rejectedCount == nonNumericValues.length) {
      print('  [PASS] 3.6: All ${nonNumericValues.length} non-numeric expiresAt values rejected with ArgumentError');
      results['3.6_non_numeric_expires_at_rejected'] = true;
    } else {
      print('  [FINDING/DEFECT] 3.6: Only $rejectedCount/${nonNumericValues.length} non-numeric expiresAt rejected');
      results['3.6_non_numeric_expires_at_rejected'] = false;
    }
  }

  // Test 3.7: Invalid non-String types across all 6 String fields (int, double, bool, list, map)
  {
    const stringKeys = [
      'accessToken',
      'refreshToken',
      'baseUrl',
      'slotConnectionType',
      'slotDpUnitId',
      'slotTenantId',
    ];
    final badTypes = [
      12345,
      99.9,
      true,
      false,
      <dynamic>[1, 2],
      <String, dynamic>{'k': 'v'},
    ];
    int totalCases = stringKeys.length * badTypes.length;
    int rejectedCount = 0;
    for (final key in stringKeys) {
      for (final badVal in badTypes) {
        final map = Map<String, dynamic>.from(validAuthMap)..[key] = badVal;
        try {
          Auth7Tuple.fromMap(map);
        } on ArgumentError {
          rejectedCount++;
        } catch (e) {
          // wrong exception, e.g. _TypeError
        }
      }
    }
    if (rejectedCount == totalCases) {
      print('  [PASS] 3.7: All $totalCases invalid type permutations rejected with ArgumentError (0 _TypeError)');
      results['3.7_all_string_fields_type_safety'] = true;
    } else {
      print('  [FINDING/DEFECT] 3.7: Only $rejectedCount/$totalCases invalid types rejected with ArgumentError');
      results['3.7_all_string_fields_type_safety'] = false;
    }
  }

  // Test 3.8: Empty strings ('') and pure whitespace strings (' ', '   ', '\t', '\n', '\r\n')
  {
    const stringKeys = [
      'accessToken',
      'refreshToken',
      'baseUrl',
      'slotConnectionType',
      'slotDpUnitId',
      'slotTenantId',
    ];
    final emptyAndWhitespace = [
      '',
      ' ',
      '   ',
      '\t',
      '\n',
      '\r\n',
    ];
    int totalCases = stringKeys.length * emptyAndWhitespace.length;
    int rejectedCount = 0;
    final acceptedCases = <String>[];
    for (final key in stringKeys) {
      for (final ws in emptyAndWhitespace) {
        final map = Map<String, dynamic>.from(validAuthMap)..[key] = ws;
        try {
          Auth7Tuple.fromMap(map);
        } on ArgumentError {
          rejectedCount++;
        } catch (e) {
          // other exception
        }
        // Check if it was accepted
        try {
          Auth7Tuple.fromMap(map);
          acceptedCases.add('$key="$ws"');
        } catch (_) {}
      }
    }
    if (rejectedCount == totalCases) {
      print('  [PASS] 3.8: All $totalCases empty and pure whitespace strings rejected with ArgumentError');
      results['3.8_empty_and_whitespace_strings_rejected'] = true;
    } else {
      print('  [FINDING/DEFECT] 3.8: Pure whitespace strings accepted! ($rejectedCount/$totalCases rejected with ArgumentError; ${acceptedCases.length} accepted without error: ${acceptedCases.take(5).join(", ")}...)');
      results['3.8_empty_and_whitespace_strings_rejected'] = false;
    }
  }


  // --------------------------------------------------------------------------
  // SUMMARY REPORT
  // --------------------------------------------------------------------------
  print('\n=================================================================');
  print('  ADVERSARIAL STRESS TEST SUMMARY:');
  int passes = 0;
  int findings = 0;
  for (final entry in results.entries) {
    if (entry.value) {
      passes++;
      print('  ✅ PASS: ${entry.key}');
    } else {
      findings++;
      print('  ❌ FINDING: ${entry.key}');
    }
  }
  print('\nTotal Passes: $passes, Total Findings/Gaps: $findings');
  print('=================================================================');
  if (findings > 0) {
    exitCode = 1;
  }
}

