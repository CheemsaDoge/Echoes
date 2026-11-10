/// Riverpod 7-State Business State Machine Service (`hf.i`).
/// Enforces transition matrix, 10s activating watchdog, and 45s recovery deadlock watchdog.
library;

import 'dart:async';
import '../models/recording_state.dart';
import '../ipc/native_bridge.dart';
import '../ipc/event_channels.dart';

/// Lightweight StateNotifier base class compatible with Riverpod.
abstract class StateNotifier<T> {
  T _state;
  final List<void Function(T)> _listeners = [];

  StateNotifier(this._state);

  T get state => _state;

  set state(T value) {
    if (_state != value) {
      _state = value;
      for (final listener in List.of(_listeners)) {
        listener(_state);
      }
    }
  }

  void addListener(void Function(T) listener) {
    _listeners.add(listener);
    listener(_state);
  }

  void removeListener(void Function(T) listener) {
    _listeners.remove(listener);
  }

  void dispose() {
    _listeners.clear();
  }
}

class RecordingService extends StateNotifier<RecordingSessionState> {
  final INativeBridge _bridge;
  final IRecordingEventStreams? _streams;

  Timer? _activatingWatchdog;
  Timer? _recoveryWatchdog;
  StreamSubscription? _stateSub;
  StreamSubscription? _eventsSub;

  RecordingService({
    required INativeBridge bridge,
    IRecordingEventStreams? streams,
  })  : _bridge = bridge,
        _streams = streams,
        super(const RecordingSessionState()) {
    _initSubscriptions();
  }

  void _initSubscriptions() {
    if (_streams == null) return;

    _stateSub = _streams.stateStream.listen((snapshot) {
      final nativeState = RecordingCaptureState.fromWire(snapshot.state);
      _onNativeStateEvent(nativeState, snapshot.elapsedMs);
    });

    _eventsSub = _streams.eventsStream.listen((event) {
      if (event.type == 'STATE_CHANGED') {
        final stateWire = event.payload['state'] as String? ?? '';
        final nativeState = RecordingCaptureState.fromWire(stateWire);
        _onNativeStateEvent(nativeState, state.elapsedMs);
      } else if (event.type == 'INTERRUPTION') {
        handleInterruption();
      } else if (event.type == 'ERROR') {
        final err = event.payload['message'] as String? ?? 'NATIVE_ERROR';
        handleFatalError(err);
      }
    });
  }

  /// Initiates a recording session.
  /// Allowed from: idle, stopped, failedTerminal.
  Future<bool> startRecording({
    required String recordingId,
    required String tenantId,
    required String vaultId,
    String userId = '',
  }) async {
    if (!state.captureState.canStart) {
      throw InvalidStateTransitionException(
        from: state.captureState,
        to: RecordingCaptureState.activating,
        message: 'Cannot start recording from state ${state.captureState}',
      );
    }

    _cancelWatchdogs();

    state = state.copyWith(
      captureState: RecordingCaptureState.activating,
      activeRecordingId: recordingId,
      tenantId: tenantId,
      vaultId: vaultId,
      userId: userId,
      elapsedMs: 0,
      runningSinceMs: DateTime.now().millisecondsSinceEpoch,
      clearError: true,
    );

    // Arm 10s activating watchdog timer
    _activatingWatchdog = Timer(const Duration(seconds: 10), () {
      if (state.captureState == RecordingCaptureState.activating) {
        state = state.copyWith(
          captureState: RecordingCaptureState.failedTerminal,
          lastError: 'START_TIMEOUT: Native pipeline failed to respond in 10s',
        );
        _bridge.stopRecording(reason: 'timeout');
      }
    });

    final success = await _bridge.startRecording(
      recordingId: recordingId,
      tenantId: tenantId,
      vaultId: vaultId,
      userId: userId,
    );

    if (!success) {
      _cancelWatchdogs();
      state = state.copyWith(
        captureState: RecordingCaptureState.failedTerminal,
        lastError: 'START_FAILED: Native bridge rejected startRecording',
      );
      return false;
    }

    return true;
  }

  /// Pauses the active recording session.
  /// Allowed from: recording.
  Future<bool> pauseRecording() async {
    if (!state.captureState.canPause) {
      throw InvalidStateTransitionException(
        from: state.captureState,
        to: RecordingCaptureState.recovering,
        message: 'Cannot pause recording from state ${state.captureState}',
      );
    }

    _cancelWatchdogs();

    state = state.copyWith(
      captureState: RecordingCaptureState.recovering,
    );

    _armRecoveryWatchdog();
    return await _bridge.pauseRecording();
  }

  /// Resumes recording after being paused or recovering.
  /// Allowed from: recovering.
  Future<bool> resumeRecording() async {
    if (!state.captureState.canResume) {
      throw InvalidStateTransitionException(
        from: state.captureState,
        to: RecordingCaptureState.recording,
        message: 'Cannot resume recording from state ${state.captureState}',
      );
    }

    _cancelWatchdogs();

    state = state.copyWith(
      captureState: RecordingCaptureState.recording,
    );

    return await _bridge.resumeRecording();
  }

  /// Stops the recording session.
  /// Allowed from: recording, recovering, activating.
  Future<bool> stopRecording({String reason = 'userStop'}) async {
    if (!state.captureState.canStop) {
      throw InvalidStateTransitionException(
        from: state.captureState,
        to: RecordingCaptureState.stopping,
        message: 'Cannot stop recording from state ${state.captureState}',
      );
    }

    _cancelWatchdogs();

    state = state.copyWith(
      captureState: RecordingCaptureState.stopping,
    );

    final res = await _bridge.stopRecording(reason: reason);
    return res;
  }

  /// Handles incoming audio interruption (incoming call, loss of audio focus).
  void handleInterruption() {
    if (state.captureState == RecordingCaptureState.recording) {
      _cancelWatchdogs();
      state = state.copyWith(
        captureState: RecordingCaptureState.recovering,
      );
      _armRecoveryWatchdog();
    }
  }

  /// Handles fatal errors reported by the audio hardware or OS.
  void handleFatalError(String errorReason) {
    _cancelWatchdogs();
    state = state.copyWith(
      captureState: RecordingCaptureState.failedTerminal,
      lastError: errorReason,
    );
  }

  /// Resets from failedTerminal or stopped back to idle.
  void reset() {
    _cancelWatchdogs();
    state = const RecordingSessionState();
  }

  void _armRecoveryWatchdog() {
    _recoveryWatchdog?.cancel();
    // 45s HAL deadlock watchdog timer per spec
    _recoveryWatchdog = Timer(const Duration(seconds: 45), () {
      if (state.captureState == RecordingCaptureState.recovering) {
        state = state.copyWith(
          captureState: RecordingCaptureState.failedTerminal,
          lastError: 'HAL_DEADLOCK_DETECTED: Recovery exceeded 45s threshold',
        );
        _bridge.stopRecording(reason: 'recovery_deadlock');
      }
    });
  }

  void _onNativeStateEvent(RecordingCaptureState nativeState, int elapsedMs) {
    if (nativeState == RecordingCaptureState.recording) {
      _cancelWatchdogs();
      if (state.captureState != RecordingCaptureState.recording) {
        state = state.copyWith(
          captureState: RecordingCaptureState.recording,
          elapsedMs: elapsedMs,
        );
      } else {
        state = state.copyWith(elapsedMs: elapsedMs);
      }
    } else if (nativeState == RecordingCaptureState.recovering) {
      if (state.captureState == RecordingCaptureState.recording) {
        state = state.copyWith(captureState: RecordingCaptureState.recovering);
        _armRecoveryWatchdog();
      }
    } else if (nativeState == RecordingCaptureState.stopped) {
      _cancelWatchdogs();
      state = state.copyWith(
        captureState: RecordingCaptureState.stopped,
        elapsedMs: elapsedMs,
      );
      // Auto-reset to idle after stopping flush completes
      Future.delayed(const Duration(milliseconds: 300), () {
        if (state.captureState == RecordingCaptureState.stopped) {
          state = const RecordingSessionState();
        }
      });
    } else if (nativeState == RecordingCaptureState.failedTerminal) {
      handleFatalError('NATIVE_TERMINAL_FAILURE');
    }
  }

  void _cancelWatchdogs() {
    _activatingWatchdog?.cancel();
    _activatingWatchdog = null;
    _recoveryWatchdog?.cancel();
    _recoveryWatchdog = null;
  }

  @override
  void dispose() {
    _cancelWatchdogs();
    _stateSub?.cancel();
    _eventsSub?.cancel();
    super.dispose();
  }
}
