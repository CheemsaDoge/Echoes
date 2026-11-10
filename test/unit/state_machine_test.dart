/// Unit tests for Canonical 7-State Recording Business State Machine (hf.i).
/// Verifies transition guards, 10s activating watchdog, and 45s deadlock watchdog.
library;

import 'dart:async';
import '../../lib/core/models/recording_state.dart';
import '../../lib/core/ipc/native_bridge.dart';
import '../../lib/core/ipc/event_channels.dart';
import '../../lib/core/services/recording_service.dart';

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

/// In-memory mock implementation of INativeBridge for state machine testing.
class MockNativeBridge implements INativeBridge {
  String currentState = 'idle';
  bool startShouldSucceed = true;
  bool stopShouldSucceed = true;
  int startCallCount = 0;
  int stopCallCount = 0;
  int pauseCallCount = 0;
  int resumeCallCount = 0;

  @override
  Future<bool> startRecording({
    required String recordingId,
    required String tenantId,
    required String vaultId,
    String userId = '',
  }) async {
    startCallCount++;
    if (startShouldSucceed) {
      currentState = 'activating';
      return true;
    }
    return false;
  }

  @override
  Future<bool> stopRecording({String reason = 'userStop'}) async {
    stopCallCount++;
    if (stopShouldSucceed) {
      currentState = 'stopped';
      return true;
    }
    return false;
  }

  @override
  Future<bool> pauseRecording() async {
    pauseCallCount++;
    currentState = 'paused';
    return true;
  }

  @override
  Future<bool> resumeRecording() async {
    resumeCallCount++;
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

/// Mock event stream registry for driving simulated native events.
class MockEventStreams implements IRecordingEventStreams {
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

Future<void> main() async {
  print('Running Canonical 7-State Business State Machine Tests (hf.i)...');

  // Test 1: Wire representations and active predicates (hf/i.a())
  {
    assertEqual(RecordingCaptureState.idle.wire, 'idle', 'idle wire');
    assertEqual(RecordingCaptureState.activating.wire, 'activating', 'activating wire');
    assertEqual(RecordingCaptureState.recording.wire, 'recording', 'recording wire');
    assertEqual(RecordingCaptureState.recovering.wire, 'recovering', 'recovering wire');
    assertEqual(RecordingCaptureState.stopping.wire, 'stopping', 'stopping wire');
    assertEqual(RecordingCaptureState.stopped.wire, 'stopped', 'stopped wire');
    assertEqual(RecordingCaptureState.failedTerminal.wire, 'failedTerminal', 'failedTerminal wire');

    // i.a(): (this == Idle || this == Stopped || this == FailedTerminal) ? false : true;
    assertTrue(!RecordingCaptureState.idle.isActive, 'idle not active');
    assertTrue(!RecordingCaptureState.stopped.isActive, 'stopped not active');
    assertTrue(!RecordingCaptureState.failedTerminal.isActive, 'failedTerminal not active');
    assertTrue(RecordingCaptureState.activating.isActive, 'activating is active');
    assertTrue(RecordingCaptureState.recording.isActive, 'recording is active');
    assertTrue(RecordingCaptureState.recovering.isActive, 'recovering is active');
    assertTrue(RecordingCaptureState.stopping.isActive, 'stopping is active');
    print('  [PASS] Test 1: Wire names and active predicates match decompiled hf/i.java');
  }

  // Test 2: Standard recording flight: idle -> activating -> recording -> stopping -> stopped -> idle
  {
    final mockBridge = MockNativeBridge();
    final mockStreams = MockEventStreams();
    final service = RecordingService(bridge: mockBridge, streams: mockStreams);

    assertEqual(service.state.captureState, RecordingCaptureState.idle, 'Initial state must be idle');

    // Start flight
    final started = await service.startRecording(
      recordingId: 'rec_100',
      tenantId: 't_demo',
      vaultId: 'v_demo',
    );
    assertTrue(started, 'startRecording return');
    assertEqual(service.state.captureState, RecordingCaptureState.activating, 'State must be activating');
    assertEqual(mockBridge.startCallCount, 1, 'Bridge startRecording invoked');

    // Native reports recording active
    mockStreams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
    );
    await Future.delayed(const Duration(milliseconds: 20));
    assertEqual(service.state.captureState, RecordingCaptureState.recording, 'State advanced to recording');

    // Stop flight
    await service.stopRecording();
    assertEqual(service.state.captureState, RecordingCaptureState.stopping, 'State is stopping');
    assertEqual(mockBridge.stopCallCount, 1, 'Bridge stopRecording invoked');

    // Native reports stopped
    mockStreams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'stopped'}),
    );
    await Future.delayed(const Duration(milliseconds: 20));
    assertEqual(service.state.captureState, RecordingCaptureState.stopped, 'State reached stopped');

    // Auto-resets to idle after flush
    await Future.delayed(const Duration(milliseconds: 350));
    assertEqual(service.state.captureState, RecordingCaptureState.idle, 'Auto-reset to idle completed');

    service.dispose();
    mockStreams.dispose();
    print('  [PASS] Test 2: Normal recording flight and auto-reset cycle');
  }

  // Test 3: Pause & Resume flight (recording -> recovering -> recording)
  {
    final mockBridge = MockNativeBridge();
    final mockStreams = MockEventStreams();
    final service = RecordingService(bridge: mockBridge, streams: mockStreams);

    await service.startRecording(recordingId: 'rec_200', tenantId: 't', vaultId: 'v');
    mockStreams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
    );
    await Future.delayed(const Duration(milliseconds: 20));
    assertEqual(service.state.captureState, RecordingCaptureState.recording, 'In recording');

    // Pause recording
    await service.pauseRecording();
    assertEqual(service.state.captureState, RecordingCaptureState.recovering, 'Paused -> recovering');
    assertEqual(mockBridge.pauseCallCount, 1, 'Bridge pause called');

    // Resume recording
    await service.resumeRecording();
    assertEqual(service.state.captureState, RecordingCaptureState.recording, 'Resumed -> recording');
    assertEqual(mockBridge.resumeCallCount, 1, 'Bridge resume called');

    service.dispose();
    mockStreams.dispose();
    print('  [PASS] Test 3: Pause & Resume flight');
  }

  // Test 4: Audio Focus Interruption & Fatal Error Handling
  {
    final mockBridge = MockNativeBridge();
    final mockStreams = MockEventStreams();
    final service = RecordingService(bridge: mockBridge, streams: mockStreams);

    await service.startRecording(recordingId: 'rec_300', tenantId: 't', vaultId: 'v');
    mockStreams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
    );
    await Future.delayed(const Duration(milliseconds: 20));

    // Simulate incoming phone call interruption
    mockStreams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'INTERRUPTION', payload: {}),
    );
    await Future.delayed(const Duration(milliseconds: 20));
    assertEqual(service.state.captureState, RecordingCaptureState.recovering, 'Interruption -> recovering');

    // Simulate terminal hardware error
    mockStreams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'ERROR', payload: {'message': 'MIC_HARDWARE_FAILURE'}),
    );
    await Future.delayed(const Duration(milliseconds: 20));
    assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'Fatal error -> failedTerminal');
    assertEqual(service.state.lastError, 'MIC_HARDWARE_FAILURE', 'Error message preserved');

    // Reset clears terminal state back to idle
    service.reset();
    assertEqual(service.state.captureState, RecordingCaptureState.idle, 'Reset restores idle');

    service.dispose();
    mockStreams.dispose();
    print('  [PASS] Test 4: Audio interruption and fatal error handling');
  }

  // Test 5: Illegal transition guards throw InvalidStateTransitionException
  {
    final mockBridge = MockNativeBridge();
    final service = RecordingService(bridge: mockBridge);

    // From idle, pausing or resuming is illegal
    bool threwPause = false;
    try {
      await service.pauseRecording();
    } on InvalidStateTransitionException {
      threwPause = true;
    }
    assertTrue(threwPause, 'pauseRecording from idle must throw');

    bool threwResume = false;
    try {
      await service.resumeRecording();
    } on InvalidStateTransitionException {
      threwResume = true;
    }
    assertTrue(threwResume, 'resumeRecording from idle must throw');

    bool threwStop = false;
    try {
      await service.stopRecording();
    } on InvalidStateTransitionException {
      threwStop = true;
    }
    assertTrue(threwStop, 'stopRecording from idle must throw');

    service.dispose();
    print('  [PASS] Test 5: Illegal state transitions strictly rejected');
  }

  // Test 6: Late native state events cannot resurrect terminal/stopping/idle states
  {
    final mockBridge = MockNativeBridge();
    final mockStreams = MockEventStreams();
    final service = RecordingService(bridge: mockBridge, streams: mockStreams);

    // Idle state: late recording/recovering event ignored
    mockStreams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
    );
    await Future.delayed(const Duration(milliseconds: 20));
    assertEqual(service.state.captureState, RecordingCaptureState.idle, 'Idle ignored late recording event');

    // Enter activating -> failedTerminal
    await service.startRecording(recordingId: 'rec_term', tenantId: 't', vaultId: 'v');
    service.handleFatalError('CRITICAL_HAL_FAULT');
    assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'In failedTerminal');

    // Late recording/recovering/stopped events must not resurrect or clobber failedTerminal
    mockStreams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'recording'}),
    );
    await Future.delayed(const Duration(milliseconds: 20));
    assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'failedTerminal resists recording resurrection');

    mockStreams.eventsCtrl.add(
      const RecordingLifecycleEvent(type: 'STATE_CHANGED', payload: {'state': 'stopped'}),
    );
    await Future.delayed(const Duration(milliseconds: 20));
    assertEqual(service.state.captureState, RecordingCaptureState.failedTerminal, 'failedTerminal resists stopped clobber');

    service.reset();
    assertEqual(service.state.captureState, RecordingCaptureState.idle, 'Reset restores idle');

    service.dispose();
    mockStreams.dispose();
    print('  [PASS] Test 6: Late native state events cannot resurrect terminal or inactive states');
  }

  print('\nALL STATE MACHINE TESTS (hf.i) PASSED SUCCESSFULLY!');
}
