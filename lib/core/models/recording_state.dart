/// Canonical 7-state recording business state machine models.
/// Reverse-engineered from decompiled APK bytecode `hf/i.java` and Dart symbol pool.
library;

/// Canonical 7-state enum from `hf/i.java`.
enum RecordingCaptureState {
  idle('idle'),
  activating('activating'),
  recording('recording'),
  recovering('recovering'),
  stopping('stopping'),
  stopped('stopped'),
  failedTerminal('failedTerminal');

  final String wire;
  const RecordingCaptureState(this.wire);

  /// Active capture predicate matching `hf/i.a()` in decompiled bytecode:
  /// (this == Idle || this == Stopped || this == FailedTerminal) ? false : true;
  bool get isActive =>
      this == activating ||
      this == recording ||
      this == recovering ||
      this == stopping;

  bool get isRecording => this == recording;
  bool get isPaused => this == recovering;
  bool get isTerminal => this == failedTerminal;
  bool get canStart => this == idle || this == stopped || this == failedTerminal;
  bool get canPause => this == recording;
  bool get canResume => this == recovering;
  bool get canStop => this == recording || this == recovering || this == activating;

  static RecordingCaptureState fromWire(String wire) {
    return RecordingCaptureState.values.firstWhere(
      (e) => e.wire == wire || e.name.toLowerCase() == wire.toLowerCase(),
      orElse: () => RecordingCaptureState.idle,
    );
  }
}

/// Thrown when an illegal state transition is attempted on the state machine.
class InvalidStateTransitionException implements Exception {
  final RecordingCaptureState from;
  final RecordingCaptureState to;
  final String message;

  const InvalidStateTransitionException({
    required this.from,
    required this.to,
    required this.message,
  });

  @override
  String toString() =>
      'InvalidStateTransitionException: Illegal transition from $from to $to ($message)';
}

/// Immutable snapshot representing the active recording session state.
class RecordingSessionState {
  final RecordingCaptureState captureState;
  final String? activeRecordingId;
  final String? tenantId;
  final String? vaultId;
  final String? userId;
  final int elapsedMs;
  final int runningSinceMs;
  final String? lastError;

  const RecordingSessionState({
    this.captureState = RecordingCaptureState.idle,
    this.activeRecordingId,
    this.tenantId,
    this.vaultId,
    this.userId,
    this.elapsedMs = 0,
    this.runningSinceMs = 0,
    this.lastError,
  });

  bool get isRecording => captureState.isRecording;
  bool get isActive => captureState.isActive;
  bool get isPaused => captureState.isPaused;
  bool get isTerminal => captureState.isTerminal;

  RecordingSessionState copyWith({
    RecordingCaptureState? captureState,
    String? activeRecordingId,
    String? tenantId,
    String? vaultId,
    String? userId,
    int? elapsedMs,
    int? runningSinceMs,
    String? lastError,
    bool clearError = false,
  }) {
    return RecordingSessionState(
      captureState: captureState ?? this.captureState,
      activeRecordingId: activeRecordingId ?? this.activeRecordingId,
      tenantId: tenantId ?? this.tenantId,
      vaultId: vaultId ?? this.vaultId,
      userId: userId ?? this.userId,
      elapsedMs: elapsedMs ?? this.elapsedMs,
      runningSinceMs: runningSinceMs ?? this.runningSinceMs,
      lastError: clearError ? null : (lastError ?? this.lastError),
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is RecordingSessionState &&
          runtimeType == other.runtimeType &&
          captureState == other.captureState &&
          activeRecordingId == other.activeRecordingId &&
          tenantId == other.tenantId &&
          vaultId == other.vaultId &&
          userId == other.userId &&
          elapsedMs == other.elapsedMs &&
          runningSinceMs == other.runningSinceMs &&
          lastError == other.lastError;

  @override
  int get hashCode => Object.hash(
        captureState,
        activeRecordingId,
        tenantId,
        vaultId,
        userId,
        elapsedMs,
        runningSinceMs,
        lastError,
      );

  @override
  String toString() =>
      'RecordingSessionState(state: ${captureState.wire}, id: $activeRecordingId, elapsed: ${elapsedMs}ms, error: $lastError)';
}
