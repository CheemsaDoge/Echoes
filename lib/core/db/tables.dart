/// Drift Table Definitions for Hemory local SQLite database.
/// Authoritative schema covering 6 tables per M5 Specification.
library;

import 'package:drift/drift.dart';

// 1. Aggregated Recording Sessions Table
@DataClassName('RecordingSessionEntry')
class RecordingSessions extends Table {
  TextColumn get liveSessionId => text()();
  TextColumn get tenantId => text()();
  TextColumn get vaultId => text()();
  TextColumn get userId => text()();
  TextColumn get localDayKey => text()(); // 'YYYY-MM-DD'
  Int64Column get startedAt => int64()(); // epoch milliseconds
  Int64Column get endedAt => int64().nullable()();
  Int64Column get durationMs => int64().withDefault(const Constant(0))();
  TextColumn get syncStatus => text().withDefault(const Constant('local'))(); // 'local', 'syncing', 'synced', 'failed'
  Int64Column get createdAt => int64()();
  Int64Column get updatedAt => int64()();

  @override
  Set<Column> get primaryKey => {liveSessionId};
}

// 2. Individual Audio Slices & Segments Table
@DataClassName('RecordingSegmentEntry')
class RecordingSegments extends Table {
  TextColumn get segmentId => text()();
  TextColumn get liveSessionId => text().references(RecordingSessions, #liveSessionId, onDelete: KeyAction.cascade)();
  IntColumn get segmentIndex => integer()();
  Int64Column get startedAt => int64()();
  Int64Column get durationMs => int64()();
  TextColumn get localFileKey => text()(); // path to .aac
  TextColumn get metaFileKey => text()();  // path to companion .meta
  Int64Column get byteSize => int64()();
  TextColumn get sha256 => text()();
  TextColumn get splitReason => text()();  // 'silence_window', 'hard_limit', 'manual_stop', etc.
  TextColumn get storageKey => text().nullable()();
  TextColumn get uploadStatus => text().withDefault(const Constant('pending'))(); // 'pending', 'uploading', 'completed', 'failed'
  BoolColumn get isUploaded => boolean().withDefault(const Constant(false))();
  Int64Column get createdAt => int64()();

  @override
  Set<Column> get primaryKey => {segmentId};
}

// 3. WorkManager Persistent Upload Queue Table (Drift Mirror)
@DataClassName('UploadQueueEntry')
class UploadQueue extends Table {
  TextColumn get uploadId => text()();
  TextColumn get tenantId => text()();
  TextColumn get vaultId => text()();
  TextColumn get segmentId => text()();
  TextColumn get localFileKey => text()();
  IntColumn get priority => integer().withDefault(const Constant(0))();
  TextColumn get uploadStatus => text()(); // 'Pending', 'Uploading', 'Completed', 'FailedRecoverable', 'FailedPermanent'
  IntColumn get attempts => integer().withDefault(const Constant(0))();
  IntColumn get maxAttempts => integer().withDefault(const Constant(5))();
  Int64Column get nextRetryAt => int64().nullable()();
  Int64Column get startedAt => int64()();
  TextColumn get uploadBlockReason => text().nullable()();
  TextColumn get payload => text().nullable()();
  Int64Column get createdAt => int64()();
  Int64Column get updatedAt => int64()();

  @override
  Set<Column> get primaryKey => {uploadId};
}

// 4. Offline Timeline Events Cache Table
@DataClassName('TimelineEventEntry')
class TimelineEventsCache extends Table {
  TextColumn get eventId => text()();
  TextColumn get tenantId => text()();
  TextColumn get vaultId => text()();
  TextColumn get liveSessionId => text().nullable()();
  TextColumn get eventType => text()(); // 'activity', 'processing_chain', 'scattered_chat_group'
  TextColumn get title => text()();
  TextColumn get summary => text().nullable()();
  Int64Column get startedAt => int64()();
  Int64Column get endedAt => int64().nullable()();
  Int64Column get durationMs => int64().withDefault(const Constant(0))();
  IntColumn get priorityTier => integer()(); // 0: P0 Live, 1: P1 Chain, 2: P2 Activity, 3: P3 Chat, 4: P4 Coverage
  IntColumn get speakerCount => integer().withDefault(const Constant(0))();
  TextColumn get payloadJson => text()();
  Int64Column get createdAt => int64()();
  Int64Column get updatedAt => int64()();

  @override
  Set<Column> get primaryKey => {eventId};
}

// 5. Offline Speaker Profiles Cache Table
@DataClassName('SpeakerProfileEntry')
class SpeakerProfilesCache extends Table {
  TextColumn get speakerId => text()();
  TextColumn get tenantId => text()();
  TextColumn get vaultId => text()();
  TextColumn get userId => text()();
  TextColumn get name => text()();
  TextColumn get suspectedName => text().nullable()();
  TextColumn get roleLabel => text().nullable()();
  TextColumn get colorPalette => text().nullable()();
  IntColumn get sampleCount => integer().withDefault(const Constant(0))();
  TextColumn get avatarUrl => text().nullable()();
  TextColumn get voiceprintId => text().nullable()();
  Int64Column get createdAt => int64()();
  Int64Column get updatedAt => int64()();

  @override
  Set<Column> get primaryKey => {speakerId};
}

// 6. Mutation Sync State Table (Causal Cursor Tracking)
@DataClassName('MutationSyncStateEntry')
class MutationSyncState extends Table {
  TextColumn get vaultId => text()();
  Int64Column get lastCursor => int64().withDefault(const Constant(0))();
  Int64Column get lastSyncedAt => int64()();

  @override
  Set<Column> get primaryKey => {vaultId};
}
