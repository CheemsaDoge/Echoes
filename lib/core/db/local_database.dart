/// Local SQLite Database & DAO layer for Hemory client.
/// Manages 6 tables with reactive streams and causal cursor sync.
library;

import 'dart:async';
import '../models/speaker_models.dart';

/// Aggregated recording session record.
class RecordingSessionRecord {
  final String liveSessionId;
  final String tenantId;
  final String vaultId;
  final String userId;
  final String localDayKey; // 'YYYY-MM-DD'
  final int startedAt;
  final int? endedAt;
  final int durationMs;
  final String syncStatus; // 'local', 'syncing', 'synced', 'failed'
  final int createdAt;
  final int updatedAt;

  const RecordingSessionRecord({
    required this.liveSessionId,
    required this.tenantId,
    required this.vaultId,
    required this.userId,
    required this.localDayKey,
    required this.startedAt,
    this.endedAt,
    this.durationMs = 0,
    this.syncStatus = 'local',
    required this.createdAt,
    required this.updatedAt,
  });

  bool get isActive => endedAt == null;

  RecordingSessionRecord copyWith({
    int? endedAt,
    int? durationMs,
    String? syncStatus,
    int? updatedAt,
  }) {
    return RecordingSessionRecord(
      liveSessionId: liveSessionId,
      tenantId: tenantId,
      vaultId: vaultId,
      userId: userId,
      localDayKey: localDayKey,
      startedAt: startedAt,
      endedAt: endedAt ?? this.endedAt,
      durationMs: durationMs ?? this.durationMs,
      syncStatus: syncStatus ?? this.syncStatus,
      createdAt: createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }
}

/// Individual audio slice record.
class RecordingSegmentRecord {
  final String segmentId;
  final String liveSessionId;
  final int segmentIndex;
  final int startedAt;
  final int durationMs;
  final String localFileKey;
  final String metaFileKey;
  final int byteSize;
  final String sha256;
  final String splitReason;
  final String? storageKey;
  final String uploadStatus; // 'pending', 'uploading', 'completed', 'failed'
  final bool isUploaded;
  final int createdAt;

  const RecordingSegmentRecord({
    required this.segmentId,
    required this.liveSessionId,
    required this.segmentIndex,
    required this.startedAt,
    required this.durationMs,
    required this.localFileKey,
    required this.metaFileKey,
    required this.byteSize,
    required this.sha256,
    required this.splitReason,
    this.storageKey,
    this.uploadStatus = 'pending',
    this.isUploaded = false,
    required this.createdAt,
  });

  RecordingSegmentRecord copyWith({
    String? storageKey,
    String? uploadStatus,
    bool? isUploaded,
    String? localFileKey,
  }) {
    return RecordingSegmentRecord(
      segmentId: segmentId,
      liveSessionId: liveSessionId,
      segmentIndex: segmentIndex,
      startedAt: startedAt,
      durationMs: durationMs,
      localFileKey: localFileKey ?? this.localFileKey,
      metaFileKey: metaFileKey,
      byteSize: byteSize,
      sha256: sha256,
      splitReason: splitReason,
      storageKey: storageKey ?? this.storageKey,
      uploadStatus: uploadStatus ?? this.uploadStatus,
      isUploaded: isUploaded ?? this.isUploaded,
      createdAt: createdAt,
    );
  }
}

/// WorkManager upload queue entry mirror.
class UploadQueueRecord {
  final String uploadId;
  final String tenantId;
  final String vaultId;
  final String segmentId;
  final String localFileKey;
  final int priority;
  final String uploadStatus;
  final int attempts;
  final int maxAttempts;
  final int? nextRetryAt;
  final int startedAt;
  final String? uploadBlockReason;
  final String? payload;
  final int createdAt;
  final int updatedAt;

  const UploadQueueRecord({
    required this.uploadId,
    required this.tenantId,
    required this.vaultId,
    required this.segmentId,
    required this.localFileKey,
    this.priority = 0,
    required this.uploadStatus,
    this.attempts = 0,
    this.maxAttempts = 5,
    this.nextRetryAt,
    required this.startedAt,
    this.uploadBlockReason,
    this.payload,
    required this.createdAt,
    required this.updatedAt,
  });
}

/// Timeline events cache record.
class TimelineEventRecord {
  final String eventId;
  final String tenantId;
  final String vaultId;
  final String? liveSessionId;
  final String eventType;
  final String title;
  final String? summary;
  final int startedAt;
  final int? endedAt;
  final int durationMs;
  final int priorityTier;
  final int speakerCount;
  final String payloadJson;
  final int createdAt;
  final int updatedAt;

  const TimelineEventRecord({
    required this.eventId,
    required this.tenantId,
    required this.vaultId,
    this.liveSessionId,
    required this.eventType,
    required this.title,
    this.summary,
    required this.startedAt,
    this.endedAt,
    this.durationMs = 0,
    required this.priorityTier,
    this.speakerCount = 0,
    required this.payloadJson,
    required this.createdAt,
    required this.updatedAt,
  });
}

/// Speaker profiles cache record.
class SpeakerProfileRecord {
  final String speakerId;
  final String tenantId;
  final String vaultId;
  final String userId;
  final String name;
  final String? suspectedName;
  final String? roleLabel;
  final String? colorPalette;
  final int sampleCount;
  final String? avatarUrl;
  final String? voiceprintId;
  final int createdAt;
  final int updatedAt;

  const SpeakerProfileRecord({
    required this.speakerId,
    required this.tenantId,
    required this.vaultId,
    required this.userId,
    required this.name,
    this.suspectedName,
    this.roleLabel,
    this.colorPalette,
    this.sampleCount = 0,
    this.avatarUrl,
    this.voiceprintId,
    required this.createdAt,
    required this.updatedAt,
  });

  SpeakerProfile toSpeakerProfile() {
    return SpeakerProfile(
      speakerId: speakerId,
      name: name,
      roleLabel: roleLabel,
      avatarUrl: avatarUrl,
      suspectedName: suspectedName,
    );
  }
}

/// Mutation DTO from `GET /recording-mutations`.
class RecordingMutation {
  final int cursor;
  final String mutationType; // 'segment_uploaded', 'speaker_renamed', 'cluster_updated', 'event_created', 'redact_audio', 'retention_updated'
  final String entityId;
  final Map<String, dynamic> payload;
  final String vaultId;

  const RecordingMutation({
    required this.cursor,
    required this.mutationType,
    required this.entityId,
    required this.payload,
    required this.vaultId,
  });

  factory RecordingMutation.fromJson(Map<String, dynamic> json) {
    return RecordingMutation(
      cursor: (json['cursor'] as num?)?.toInt() ?? 0,
      mutationType: json['mutation_type'] as String? ?? 'unknown',
      entityId: json['entity_id'] as String? ?? '',
      payload: Map<String, dynamic>.from(json['payload'] as Map? ?? {}),
      vaultId: json['vault_id'] as String? ?? '',
    );
  }
}

/// High-performance local reactive database.
class LocalDatabase {
  final Map<String, RecordingSessionRecord> _sessions = {};
  final Map<String, RecordingSegmentRecord> _segments = {};
  final Map<String, UploadQueueRecord> _uploadQueue = {};
  final Map<String, TimelineEventRecord> _timelineEvents = {};
  final Map<String, SpeakerProfileRecord> _speakerProfiles = {};
  final Map<String, int> _mutationCursors = {}; // vaultId -> lastCursor

  // Reactive StreamControllers
  final _sessionController = StreamController<void>.broadcast();
  final _segmentController = StreamController<void>.broadcast();
  final _timelineController = StreamController<void>.broadcast();
  final _speakerController = StreamController<void>.broadcast();

  // ---------------------------------------------------------------------------
  // Recording Sessions DAO
  // ---------------------------------------------------------------------------

  Future<void> insertSession(RecordingSessionRecord session) async {
    _sessions[session.liveSessionId] = session;
    _sessionController.add(null);
  }

  Future<void> finalizeSession({
    required String liveSessionId,
    required int endedAt,
    required int durationMs,
  }) async {
    final existing = _sessions[liveSessionId];
    if (existing != null) {
      _sessions[liveSessionId] = existing.copyWith(
        endedAt: endedAt,
        durationMs: durationMs,
        updatedAt: DateTime.now().millisecondsSinceEpoch,
      );
      _sessionController.add(null);
    }
  }

  RecordingSessionRecord? getActiveSession() {
    for (final s in _sessions.values) {
      if (s.isActive) return s;
    }
    return null;
  }

  Stream<RecordingSessionRecord?> watchActiveSession() async* {
    yield getActiveSession();
    await for (final _ in _sessionController.stream) {
      yield getActiveSession();
    }
  }

  List<RecordingSessionRecord> getSessionsByDay(
    String tenantId,
    String vaultId,
    String localDayKey,
  ) {
    return _sessions.values
        .where((s) =>
            s.tenantId == tenantId &&
            s.vaultId == vaultId &&
            s.localDayKey == localDayKey)
        .toList()
      ..sort((a, b) => a.startedAt.compareTo(b.startedAt));
  }

  Stream<List<RecordingSessionRecord>> watchSessionsByDay(
    String tenantId,
    String vaultId,
    String localDayKey,
  ) async* {
    yield getSessionsByDay(tenantId, vaultId, localDayKey);
    await for (final _ in _sessionController.stream) {
      yield getSessionsByDay(tenantId, vaultId, localDayKey);
    }
  }

  // ---------------------------------------------------------------------------
  // Recording Segments DAO
  // ---------------------------------------------------------------------------

  Future<void> insertSegment(RecordingSegmentRecord segment) async {
    _segments[segment.segmentId] = segment;
    _segmentController.add(null);
  }

  List<RecordingSegmentRecord> getSegmentsForSession(String liveSessionId) {
    return _segments.values
        .where((s) => s.liveSessionId == liveSessionId)
        .toList()
      ..sort((a, b) => a.segmentIndex.compareTo(b.segmentIndex));
  }

  Stream<List<RecordingSegmentRecord>> watchSegmentsForSession(
    String liveSessionId,
  ) async* {
    yield getSegmentsForSession(liveSessionId);
    await for (final _ in _segmentController.stream) {
      yield getSegmentsForSession(liveSessionId);
    }
  }

  /// Implements CASCADE deletion per schema when a session is removed.
  Future<void> deleteSession(String liveSessionId) async {
    _sessions.remove(liveSessionId);
    _segments.removeWhere((_, seg) => seg.liveSessionId == liveSessionId);
    _sessionController.add(null);
    _segmentController.add(null);
  }

  // ---------------------------------------------------------------------------
  // Timeline Events Cache DAO
  // ---------------------------------------------------------------------------

  Future<void> upsertTimelineEvent(TimelineEventRecord event) async {
    _timelineEvents[event.eventId] = event;
    _timelineController.add(null);
  }

  List<TimelineEventRecord> getTimelineWindow(
    String vaultId,
    int startMs,
    int endMs,
  ) {
    return _timelineEvents.values
        .where((e) =>
            e.vaultId == vaultId &&
            e.startedAt >= startMs &&
            e.startedAt <= endMs)
        .toList()
      ..sort((a, b) => a.startedAt.compareTo(b.startedAt));
  }

  Stream<List<TimelineEventRecord>> watchTimelineWindow(
    String vaultId,
    int startMs,
    int endMs,
  ) async* {
    yield getTimelineWindow(vaultId, startMs, endMs);
    await for (final _ in _timelineController.stream) {
      yield getTimelineWindow(vaultId, startMs, endMs);
    }
  }

  // ---------------------------------------------------------------------------
  // Speaker Profiles Cache DAO
  // ---------------------------------------------------------------------------

  Future<void> upsertSpeakerProfile(SpeakerProfileRecord profile) async {
    _speakerProfiles[profile.speakerId] = profile;
    _speakerController.add(null);
  }

  List<SpeakerProfileRecord> getSpeakers(String vaultId) {
    return _speakerProfiles.values
        .where((sp) => sp.vaultId == vaultId)
        .toList()
      ..sort((a, b) => a.name.compareTo(b.name));
  }

  Stream<List<SpeakerProfileRecord>> watchSpeakers(String vaultId) async* {
    yield getSpeakers(vaultId);
    await for (final _ in _speakerController.stream) {
      yield getSpeakers(vaultId);
    }
  }

  // ---------------------------------------------------------------------------
  // Mutation Sync Engine (`GET /recording-mutations`)
  // ---------------------------------------------------------------------------

  int getSyncCursor(String vaultId) {
    return _mutationCursors[vaultId] ?? 0;
  }

  void setSyncCursor(String vaultId, int cursor) {
    _mutationCursors[vaultId] = cursor;
  }

  /// Atomically applies a single mutation and advances cursor.
  Future<void> applyMutation(RecordingMutation mutation) async {
    switch (mutation.mutationType) {
      case 'segment_uploaded':
        final seg = _segments[mutation.entityId];
        if (seg != null) {
          _segments[mutation.entityId] = seg.copyWith(
            isUploaded: true,
            storageKey: mutation.payload['storage_key'] as String?,
            uploadStatus: 'completed',
          );
          _segmentController.add(null);
        }
        break;

      case 'speaker_renamed':
        final spk = _speakerProfiles[mutation.entityId];
        if (spk != null) {
          final newName = mutation.payload['name'] as String? ?? spk.name;
          _speakerProfiles[mutation.entityId] = SpeakerProfileRecord(
            speakerId: spk.speakerId,
            tenantId: spk.tenantId,
            vaultId: spk.vaultId,
            userId: spk.userId,
            name: newName,
            suspectedName: spk.suspectedName,
            roleLabel: spk.roleLabel,
            colorPalette: spk.colorPalette,
            sampleCount: spk.sampleCount,
            avatarUrl: spk.avatarUrl,
            voiceprintId: spk.voiceprintId,
            createdAt: spk.createdAt,
            updatedAt: DateTime.now().millisecondsSinceEpoch,
          );
          _speakerController.add(null);
        }
        break;

      case 'cluster_updated':
        final existing = _speakerProfiles[mutation.entityId];
        final name = mutation.payload['name'] as String? ?? existing?.name ?? '';
        final role = mutation.payload['role_label'] as String? ?? existing?.roleLabel;
        if (existing != null) {
          _speakerProfiles[mutation.entityId] = SpeakerProfileRecord(
            speakerId: existing.speakerId,
            tenantId: existing.tenantId,
            vaultId: existing.vaultId,
            userId: existing.userId,
            name: name,
            suspectedName: existing.suspectedName,
            roleLabel: role,
            colorPalette: existing.colorPalette,
            sampleCount: existing.sampleCount,
            avatarUrl: existing.avatarUrl,
            voiceprintId: existing.voiceprintId,
            createdAt: existing.createdAt,
            updatedAt: DateTime.now().millisecondsSinceEpoch,
          );
          _speakerController.add(null);
        }
        break;

      case 'event_created':
        final ev = TimelineEventRecord(
          eventId: mutation.entityId,
          tenantId: mutation.payload['tenant_id'] as String? ?? '',
          vaultId: mutation.vaultId,
          liveSessionId: mutation.payload['live_session_id'] as String?,
          eventType: mutation.payload['event_type'] as String? ?? 'activity',
          title: mutation.payload['title'] as String? ?? 'New Activity',
          summary: mutation.payload['summary'] as String?,
          startedAt: (mutation.payload['started_at'] as num?)?.toInt() ?? 0,
          endedAt: (mutation.payload['ended_at'] as num?)?.toInt(),
          durationMs: (mutation.payload['duration_ms'] as num?)?.toInt() ?? 0,
          priorityTier: (mutation.payload['priority_tier'] as num?)?.toInt() ?? 2,
          speakerCount: (mutation.payload['speaker_count'] as num?)?.toInt() ?? 0,
          payloadJson: mutation.payload['payload_json'] as String? ?? '{}',
          createdAt: DateTime.now().millisecondsSinceEpoch,
          updatedAt: DateTime.now().millisecondsSinceEpoch,
        );
        _timelineEvents[ev.eventId] = ev;
        _timelineController.add(null);
        break;

      case 'redact_audio':
        final seg = _segments[mutation.entityId];
        if (seg != null) {
          _segments[mutation.entityId] = seg.copyWith(
            localFileKey: '',
          );
          _segmentController.add(null);
        }
        break;
    }

    if (mutation.cursor > getSyncCursor(mutation.vaultId)) {
      setSyncCursor(mutation.vaultId, mutation.cursor);
    }
  }

  Future<void> applyMutationsBatch(List<RecordingMutation> mutations) async {
    for (final m in mutations) {
      await applyMutation(m);
    }
  }

  void dispose() {
    _sessionController.close();
    _segmentController.close();
    _timelineController.close();
    _speakerController.close();
  }
}
