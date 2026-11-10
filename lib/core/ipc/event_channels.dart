/// EventChannel subscribers for the 4 Android native recording event streams.
/// Provides typed Dart DTOs and handles backpressure and test injection.
library;

import 'dart:async';

/// Periodic 1-second state snapshot from `hemory/recording/state`.
class RecordingStateSnapshot {
  final String state;
  final String recordingId;
  final int elapsedMs;
  final int runningSinceMs;

  const RecordingStateSnapshot({
    required this.state,
    required this.recordingId,
    required this.elapsedMs,
    required this.runningSinceMs,
  });

  factory RecordingStateSnapshot.fromMap(Map<dynamic, dynamic> map) {
    return RecordingStateSnapshot(
      state: (map['state'] as String?) ?? 'idle',
      recordingId: (map['recordingId'] as String?) ?? '',
      elapsedMs: (map['elapsedMs'] as num?)?.toInt() ?? 0,
      runningSinceMs: (map['runningSinceMs'] as num?)?.toInt() ?? 0,
    );
  }
}

/// Discrete operational lifecycle events from `hemory/recording/events`.
class RecordingLifecycleEvent {
  final String type; // STATE_CHANGED, SLICE_COMPLETED, ERROR, INTERRUPTION
  final Map<String, dynamic> payload;

  const RecordingLifecycleEvent({
    required this.type,
    required this.payload,
  });

  factory RecordingLifecycleEvent.fromMap(Map<dynamic, dynamic> map) {
    final type = (map['type'] as String?) ?? (map['event'] as String?) ?? 'UNKNOWN';
    final payload = Map<String, dynamic>.from(map);
    return RecordingLifecycleEvent(type: type, payload: payload);
  }
}

/// 150ms periodic audio decibel power from `hemory/recording/metering`.
class MeteringSnapshot {
  final double rmsDb;
  final double peakDb;
  final int timestampMs;

  const MeteringSnapshot({
    required this.rmsDb,
    required this.peakDb,
    required this.timestampMs,
  });

  /// Normalizes rmsDb [-60.0, 0.0] to [0.05, 1.0] for waveform rendering.
  double get normalizedPower {
    final clamped = rmsDb.clamp(-60.0, 0.0);
    return ((clamped + 60.0) / 60.0).clamp(0.05, 1.0);
  }

  factory MeteringSnapshot.fromMap(Map<dynamic, dynamic> map) {
    return MeteringSnapshot(
      rmsDb: (map['rmsDb'] as num?)?.toDouble() ?? -60.0,
      peakDb: (map['peakDb'] as num?)?.toDouble() ?? -60.0,
      timestampMs: (map['timestamp'] as num?)?.toInt() ?? 0,
    );
  }
}

/// Verified segment completion event from `hemory/recording/segments`.
class SegmentCompletedEvent {
  final String recordingId;
  final int segmentIndex;
  final String filePath;
  final String metaFilePath;
  final int durationMs;
  final int sizeBytes;
  final String sha256;
  final String splitReason;

  const SegmentCompletedEvent({
    required this.recordingId,
    required this.segmentIndex,
    required this.filePath,
    required this.metaFilePath,
    required this.durationMs,
    required this.sizeBytes,
    required this.sha256,
    required this.splitReason,
  });

  factory SegmentCompletedEvent.fromMap(Map<dynamic, dynamic> map) {
    return SegmentCompletedEvent(
      recordingId: (map['recordingId'] as String?) ?? '',
      segmentIndex: (map['segmentIndex'] as num?)?.toInt() ?? 0,
      filePath: (map['filePath'] as String?) ?? '',
      metaFilePath: (map['metaFilePath'] as String?) ?? '',
      durationMs: (map['durationMs'] as num?)?.toInt() ?? 0,
      sizeBytes: (map['sizeBytes'] as num?)?.toInt() ?? 0,
      sha256: (map['sha256'] as String?) ?? '',
      splitReason: (map['splitReason'] as String?) ?? 'silence_window',
    );
  }
}

/// Abstract stream registry interface for EventChannels.
abstract class IRecordingEventStreams {
  Stream<RecordingStateSnapshot> get stateStream;
  Stream<RecordingLifecycleEvent> get eventsStream;
  Stream<MeteringSnapshot> get meteringStream;
  Stream<SegmentCompletedEvent> get segmentsStream;
}

/// Concrete stream registry supporting custom stream sources.
class RecordingEventStreams implements IRecordingEventStreams {
  final Stream<RecordingStateSnapshot>? _state;
  final Stream<RecordingLifecycleEvent>? _events;
  final Stream<MeteringSnapshot>? _metering;
  final Stream<SegmentCompletedEvent>? _segments;

  RecordingEventStreams({
    Stream<RecordingStateSnapshot>? stateStream,
    Stream<RecordingLifecycleEvent>? eventsStream,
    Stream<MeteringSnapshot>? meteringStream,
    Stream<SegmentCompletedEvent>? segmentsStream,
  })  : _state = stateStream,
        _events = eventsStream,
        _metering = meteringStream,
        _segments = segmentsStream;

  @override
  Stream<RecordingStateSnapshot> get stateStream => _state ?? const Stream.empty();

  @override
  Stream<RecordingLifecycleEvent> get eventsStream => _events ?? const Stream.empty();

  @override
  Stream<MeteringSnapshot> get meteringStream => _metering ?? const Stream.empty();

  @override
  Stream<SegmentCompletedEvent> get segmentsStream => _segments ?? const Stream.empty();
}
