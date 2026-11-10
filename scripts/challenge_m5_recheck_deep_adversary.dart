/// Deep Adversarial Race & Concurrency Stress Test for M5 Re-verification
/// Evaluates:
/// 1. Test 1.2c deep evaluation: Stale events after 10.0s activating watchdog
/// 2. Test 1.5c deep evaluation: Stale events after 45.0s recovery deadlock watchdog
/// 3. Stopping race deep evaluation: Rapid native recording events arriving during stopRecording()
/// 4. Flurry of mixed stale events in failedTerminal
/// 5. Delayed bridge stopRecording response concurrency
library;

import 'dart:async';
import '../lib/core/models/recording_state.dart';
import '../lib/core/ipc/native_bridge.dart';
import '../lib/core/ipc/event_channels.dart';
import '../lib/core/services/recording_service.dart';

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

class DelayedMockBridge implements INativeBridge {
  String currentState = 'idle';
  int stopCalls = 0;
  String? lastStopReason;
  Duration stopDelay = Duration.zero;

  @override
  Future<bool> startRecording({
    required String recordingId,
    required String tenantId,
    required String vaultId,
    String userId = '',
  }) async {
    currentState = 'activating';
    return true;
  }

  @override
  Future<bool> stopRecording({String reason = 'userStop'}) async {
    stopCalls++;
    lastStopReason = reason;
    if (stopDelay > Duration.zero) {
      await Future.delayed(stopDelay);
    }
    currentState = 'stopped';
    return true;
  }

  @override
  Future<bool> pauseRecording() async {
    currentState = 'recovering';
    return true;
  }

  @override
  Future<bool> resumeRecording() async {
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

class TestStreams implements IRecordingEventStreams {
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
}

Future<void> main() async {
  print('===============================================================');
  print('  DEEP ADVERSARIAL STRESS TEST FOR M5 RE-VERIFICATION');
  print('===============================================================');

  int totalTests = 0;
  int passedTests = 0;

  // -----------------------------------------------------------------
  // Challenge 1: Deep Evaluation of Test 1.2c (Activating Watchdog 10.0s)
  // -----------------------------------------------------------------
  print('\n>>> Challenge 1: Deep Evaluation of Test 1.2c');
  {
    final clock = VirtualClock();
    await runZoned(() async {
      final bridge = DelayedMockBridge();
      final streams = TestStreams();
      final service = RecordingService(bridge: bridge, streams: streams);

      await service.startRecording(
        recordingId: 'rec_deep_1_2c',
        tenantId: 'tenant_test',
        vaultId: 'vault_test',
      );
      assertEqual(service.state.captureState, RecordingCaptureState.activating, 'Must be activating');

      // Advance clock to trigger 10.0s timeout exactly
      clock.advance(const Duration(seconds: 10));

      assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'Must be failedTerminal');
      assertTrue(service.state.lastError!.startsWith('START_TIMEOUT:'), 'lastError must indicate START_TIMEOUT');
      final originalError = service.state.lastError;

      // Adversarial assault: Flood with 100 late events (recording, recovering, stopped, error)
      for (int i = 0; i < 100; i++) {
        final stateType = (i % 3 == 0)
            ? 'recording'
            : (i % 3 == 1)
                ? 'recovering'
                : 'stopped';
        streams.eventsCtrl.add(
          RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': stateType}),
        );
        streams.stateCtrl.add(
          RecordingStateSnapshot(recordingId: 'rec_deep_1_2c', state: stateType, elapsedMs: 5000 + i * 10, runningSinceMs: 0),
        );
      }
      await Future.delayed(const Duration(milliseconds: 50));

      // Invariant checks:
      assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'Must strictly remain failedTerminal');
      assertEqual(service.state.lastError, originalError, 'Original START_TIMEOUT error must not be corrupted');
      assertEqual(service.state.isActive, false, 'isActive must be false in terminal state');

      // Verify that after assault, user can still cleanly reset()
      service.reset();
      assertEqual(service.state.captureState, RecordingCaptureState.idle, 'Reset must restore idle state');
      assertEqual(service.state.lastError, null, 'Reset must clear error');

      service.dispose();
      streams.dispose();
      passedTests++;
      print('  [PASS] Challenge 1: 1.2c withstands 100 late events without resurrection or error clobbering');
    }, zoneSpecification: ZoneSpecification(
      createTimer: (self, parent, zone, duration, f) {
        if (duration >= const Duration(seconds: 1)) {
          return clock.createTimer(duration, f);
        }
        return parent.createTimer(zone, duration, f);
      },
    ));
    totalTests++;
  }

  // -----------------------------------------------------------------
  // Challenge 2: Deep Evaluation of Test 1.5c (Deadlock Watchdog 45.0s)
  // -----------------------------------------------------------------
  print('\n>>> Challenge 2: Deep Evaluation of Test 1.5c');
  {
    final clock = VirtualClock();
    await runZoned(() async {
      final bridge = DelayedMockBridge();
      final streams = TestStreams();
      final service = RecordingService(bridge: bridge, streams: streams);

      await service.startRecording(
        recordingId: 'rec_deep_1_5c',
        tenantId: 'tenant_test',
        vaultId: 'vault_test',
      );
      streams.eventsCtrl.add(
        const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
      );
      await Future.delayed(Duration.zero);
      assertEqual(service.state.captureState, RecordingCaptureState.recording, 'Entered recording');

      // Enter recovering
      await service.pauseRecording();
      assertEqual(service.state.captureState, RecordingCaptureState.recovering, 'Entered recovering');

      // Advance clock to trigger 45.0s deadlock
      clock.advance(const Duration(seconds: 45));
      assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'Must be failedTerminal');
      assertTrue(service.state.lastError!.startsWith('HAL_DEADLOCK_DETECTED:'), 'lastError must indicate HAL_DEADLOCK_DETECTED');
      final originalError = service.state.lastError;

      // Adversarial assault: Flood with 200 mixed events
      for (int i = 0; i < 200; i++) {
        final stateType = (i % 2 == 0) ? 'recording' : 'recovering';
        streams.eventsCtrl.add(
          RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': stateType}),
        );
        streams.stateCtrl.add(
          RecordingStateSnapshot(recordingId: 'rec_deep_1_5c', state: stateType, elapsedMs: 30000 + i * 10, runningSinceMs: 0),
        );
      }
      await Future.delayed(const Duration(milliseconds: 50));

      // Invariant checks:
      assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'Must strictly remain failedTerminal after deadlock');
      assertEqual(service.state.lastError, originalError, 'Deadlock error must not be altered');
      assertEqual(bridge.lastStopReason, 'recovery_deadlock', 'Bridge reason must be recovery_deadlock');

      service.dispose();
      streams.dispose();
      passedTests++;
      print('  [PASS] Challenge 2: 1.5c withstands 200 mixed events after 45s deadlock watchdog');
    }, zoneSpecification: ZoneSpecification(
      createTimer: (self, parent, zone, duration, f) {
        if (duration >= const Duration(seconds: 1)) {
          return clock.createTimer(duration, f);
        }
        return parent.createTimer(zone, duration, f);
      },
    ));
    totalTests++;
  }

  // -----------------------------------------------------------------
  // Challenge 3: Stopping Race Deep Evaluation
  // -----------------------------------------------------------------
  print('\n>>> Challenge 3: Stopping Race with Delayed Bridge and Stream Flood');
  {
    final bridge = DelayedMockBridge()..stopDelay = const Duration(milliseconds: 50);
    final streams = TestStreams();
    final service = RecordingService(bridge: bridge, streams: streams);

    await service.startRecording(
      recordingId: 'rec_stopping_race',
      tenantId: 'tenant_test',
      vaultId: 'vault_test',
    );
    streams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
    );
    await Future.delayed(Duration.zero);
    assertEqual(service.state.captureState, RecordingCaptureState.recording, 'In recording');

    // Initiate stopRecording() which will take 50ms to complete bridge call
    final stopFuture = service.stopRecording(reason: 'userStop');

    // Immediately check state: must be 'stopping'
    assertEqual(service.state.captureState, RecordingCaptureState.stopping, 'Must immediately be stopping');

    // Concurrently blast 300 rapid native 'recording' and 'recovering' events while stop is pending
    for (int i = 0; i < 300; i++) {
      streams.eventsCtrl.add(
        RecordingLifecycleEvent(
          type: 'STATE_CHANGED',
          payload: {'state': (i % 2 == 0) ? 'recording' : 'recovering'},
        ),
      );
      streams.stateCtrl.add(
        RecordingStateSnapshot(
          recordingId: 'rec_stopping_race',
          state: (i % 2 == 0) ? 'recording' : 'recovering',
          elapsedMs: 1000 + i,
          runningSinceMs: 0,
        ),
      );
    }

    // Wait a brief tick to process stream events while bridge is still busy
    await Future.delayed(const Duration(milliseconds: 10));
    assertEqual(service.state.captureState, RecordingCaptureState.stopping, 'Stopping must NEVER be overwritten by incoming recording packets');

    // Complete the bridge stop call
    final stopResult = await stopFuture;
    assertTrue(stopResult, 'stopRecording future must return true');
    assertEqual(service.state.captureState, RecordingCaptureState.stopping, 'Still stopping until stopped event arrives');

    // Send the final stopped event from native
    streams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'stopped'}),
    );
    await Future.delayed(const Duration(milliseconds: 10));
    assertEqual(service.state.captureState, RecordingCaptureState.stopped, 'Cleanly transitioned to stopped');

    // More late recording packets arrive while in stopped:
    streams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
    );
    await Future.delayed(const Duration(milliseconds: 10));
    assertEqual(service.state.captureState, RecordingCaptureState.stopped, 'Stopped state ignored late recording packets');

    // Wait for auto-reset (300ms)
    await Future.delayed(const Duration(milliseconds: 350));
    assertEqual(service.state.captureState, RecordingCaptureState.idle, 'Auto-reset cleanly reached idle');

    // Late packets in idle
    streams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
    );
    await Future.delayed(const Duration(milliseconds: 10));
    assertEqual(service.state.captureState, RecordingCaptureState.idle, 'Idle state ignored late recording packets');

    service.dispose();
    streams.dispose();
    passedTests++;
    totalTests++;
    print('  [PASS] Challenge 3: Stopping state is never overwritten during bridge latency and stream flood');
  }

  // -----------------------------------------------------------------
  // Challenge 4: Concurrent stopRecording() Calls During Activating
  // -----------------------------------------------------------------
  print('\n>>> Challenge 4: Concurrent Stop Calls During Activating');
  {
    final bridge = DelayedMockBridge()..stopDelay = const Duration(milliseconds: 30);
    final streams = TestStreams();
    final service = RecordingService(bridge: bridge, streams: streams);

    await service.startRecording(
      recordingId: 'rec_stop_activating',
      tenantId: 't',
      vaultId: 'v',
    );
    assertEqual(service.state.captureState, RecordingCaptureState.activating, 'Is activating');

    // User rapidly presses stop 10 times concurrently
    int allowedStops = 0;
    int rejectedStops = 0;
    final stopFutures = <Future>[];
    for (int i = 0; i < 10; i++) {
      stopFutures.add(
        service.stopRecording().then((_) {
          allowedStops++;
          return true;
        }).catchError((e) {
          if (e is InvalidStateTransitionException) {
            rejectedStops++;
          }
          return false;
        }),
      );
    }

    await Future.wait(stopFutures);
    assertEqual(service.state.captureState, RecordingCaptureState.stopping, 'State is stopping');
    assertEqual(allowedStops, 1, 'Exactly 1 stop was allowed');
    assertEqual(rejectedStops, 9, 'Remaining 9 stops were rejected with InvalidStateTransitionException');

    service.dispose();
    streams.dispose();
    passedTests++;
    totalTests++;
    print('  [PASS] Challenge 4: 10 concurrent stopRecording() during activating handled cleanly (1 success, 9 rejected)');
  }

  print('\n===============================================================');
  print('  DEEP ADVERSARIAL STRESS TEST SUMMARY: $passedTests / $totalTests PASSED');
  print('===============================================================');
}
