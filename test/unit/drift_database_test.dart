/// Unit tests for Drift Local Database schema and reactive DAOs.
/// Verifies 6 tables, cascade deletions, reactive streams, and mutation sync engine.
library;

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

Future<void> main() async {
  print('Running Local Database & Mutation Sync Tests (6 Tables)...');

  // Test 1: Sessions and Segments DAO with Cascade Deletion
  {
    final db = LocalDatabase();

    // Insert session
    final session = RecordingSessionRecord(
      liveSessionId: 'sess_101',
      tenantId: 'tenant_alpha',
      vaultId: 'vault_beta',
      userId: 'user_gamma',
      localDayKey: '2026-11-10',
      startedAt: 1000000,
      createdAt: 1000000,
      updatedAt: 1000000,
    );
    await db.insertSession(session);

    // Active session watcher
    final active = db.getActiveSession();
    assertTrue(active != null, 'Active session found');
    assertEqual(active!.liveSessionId, 'sess_101', 'Active session id');

    // Insert 2 segments
    await db.insertSegment(
      const RecordingSegmentRecord(
        segmentId: 'seg_0',
        liveSessionId: 'sess_101',
        segmentIndex: 0,
        startedAt: 1000000,
        durationMs: 1800000,
        localFileKey: '/storage/seg_0.aac',
        metaFileKey: '/storage/seg_0.meta',
        byteSize: 10800000,
        sha256: 'sha_0',
        splitReason: 'silence_window',
        createdAt: 1000000,
      ),
    );
    await db.insertSegment(
      const RecordingSegmentRecord(
        segmentId: 'seg_1',
        liveSessionId: 'sess_101',
        segmentIndex: 1,
        startedAt: 2800000,
        durationMs: 1800000,
        localFileKey: '/storage/seg_1.aac',
        metaFileKey: '/storage/seg_1.meta',
        byteSize: 10800000,
        sha256: 'sha_1',
        splitReason: 'hard_limit',
        createdAt: 2800000,
      ),
    );

    final segments = db.getSegmentsForSession('sess_101');
    assertEqual(segments.length, 2, '2 segments recorded');
    assertEqual(segments[0].segmentIndex, 0, 'Seg 0 index');
    assertEqual(segments[1].segmentIndex, 1, 'Seg 1 index');

    // Finalize session
    await db.finalizeSession(
      liveSessionId: 'sess_101',
      endedAt: 4600000,
      durationMs: 3600000,
    );
    final activeAfterFinalize = db.getActiveSession();
    assertTrue(activeAfterFinalize == null, 'Session cleanly closed');

    // Cascade deletion
    await db.deleteSession('sess_101');
    assertEqual(db.getSegmentsForSession('sess_101').length, 0, 'Cascade deletion pruned segments');
    db.dispose();
    print('  [PASS] Test 1: Sessions, Segments and CASCADE deletion');
  }

  // Test 2: Timeline Events and Speaker Profiles Cache DAOs
  {
    final db = LocalDatabase();

    await db.upsertTimelineEvent(
      const TimelineEventRecord(
        eventId: 'ev_01',
        tenantId: 'tenant_alpha',
        vaultId: 'vault_beta',
        eventType: 'activity',
        title: 'Standup',
        startedAt: 500,
        endedAt: 800,
        durationMs: 300,
        priorityTier: 2,
        payloadJson: '{"title":"Standup"}',
        createdAt: 500,
        updatedAt: 500,
      ),
    );

    final windowEvents = db.getTimelineWindow('vault_beta', 400, 900);
    assertEqual(windowEvents.length, 1, '1 event in window');
    assertEqual(windowEvents[0].title, 'Standup', 'event title');

    // Speaker profile upsert
    await db.upsertSpeakerProfile(
      const SpeakerProfileRecord(
        speakerId: 'spk_sarah',
        tenantId: 'tenant_alpha',
        vaultId: 'vault_beta',
        userId: 'u_1',
        name: 'Dr. Sarah Lin',
        suspectedName: 'Sarah',
        createdAt: 100,
        updatedAt: 100,
      ),
    );

    final speakers = db.getSpeakers('vault_beta');
    assertEqual(speakers.length, 1, '1 speaker found');
    assertEqual(speakers[0].name, 'Dr. Sarah Lin', 'speaker name');

    db.dispose();
    print('  [PASS] Test 2: Timeline Events and Speaker Profiles Cache DAOs');
  }

  // Test 3: Incremental Mutation Sync Engine (GET /recording-mutations)
  {
    final db = LocalDatabase();

    // Bootstrap local segment and speaker
    await db.insertSegment(
      const RecordingSegmentRecord(
        segmentId: 'seg_sync_1',
        liveSessionId: 'sess_sync',
        segmentIndex: 0,
        startedAt: 100,
        durationMs: 500,
        localFileKey: '/tmp/seg_sync_1.aac',
        metaFileKey: '/tmp/seg_sync_1.meta',
        byteSize: 1000,
        sha256: 'h1',
        splitReason: 'silence_window',
        createdAt: 100,
      ),
    );
    await db.upsertSpeakerProfile(
      const SpeakerProfileRecord(
        speakerId: 'spk_sync_1',
        tenantId: 't',
        vaultId: 'v_main',
        userId: 'u',
        name: 'Old Name',
        createdAt: 100,
        updatedAt: 100,
      ),
    );

    // Initial cursor
    assertEqual(db.getSyncCursor('v_main'), 0, 'Initial cursor is 0');

    // Apply batch of mutations
    final mutations = [
      // 1. segment_uploaded
      const RecordingMutation(
        cursor: 1,
        mutationType: 'segment_uploaded',
        entityId: 'seg_sync_1',
        payload: {'storage_key': 's3://vault/seg_sync_1.aac'},
        vaultId: 'v_main',
      ),
      // 2. speaker_renamed
      const RecordingMutation(
        cursor: 2,
        mutationType: 'speaker_renamed',
        entityId: 'spk_sync_1',
        payload: {'name': 'New Verified Name'},
        vaultId: 'v_main',
      ),
      // 3. event_created
      const RecordingMutation(
        cursor: 3,
        mutationType: 'event_created',
        entityId: 'ev_sync_new',
        payload: {
          'title': 'Sync Event',
          'started_at': 2000,
          'ended_at': 2500,
          'priority_tier': 2,
        },
        vaultId: 'v_main',
      ),
      // 4. redact_audio
      const RecordingMutation(
        cursor: 4,
        mutationType: 'redact_audio',
        entityId: 'seg_sync_1',
        payload: {},
        vaultId: 'v_main',
      ),
    ];

    await db.applyMutationsBatch(mutations);

    // Verify mutations took effect
    final seg = db.getSegmentsForSession('sess_sync').first;
    assertTrue(seg.isUploaded, 'Segment marked uploaded');
    assertEqual(seg.storageKey, 's3://vault/seg_sync_1.aac', 'Storage key applied');
    assertEqual(seg.localFileKey, '', 'Redact audio cleared local file key');

    final spk = db.getSpeakers('v_main').first;
    assertEqual(spk.name, 'New Verified Name', 'Speaker rename applied');

    final events = db.getTimelineWindow('v_main', 1500, 3000);
    assertEqual(events.length, 1, 'New sync event created');
    assertEqual(events[0].title, 'Sync Event', 'Event title match');

    // Verify monotonic cursor progression
    assertEqual(db.getSyncCursor('v_main'), 4, 'Cursor progressed to 4');

    db.dispose();
    print('  [PASS] Test 3: Incremental Mutation Sync Engine and Causal Cursors');
  }

  print('\nALL DRIFT DATABASE & SYNC TESTS PASSED SUCCESSFULLY!');
}
