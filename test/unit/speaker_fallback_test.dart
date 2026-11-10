/// Unit tests for Contract C6 3-Tier Dynamic Text Speaker Fallback Resolution.
/// Covers all 9 test cases from TC-SPK-01 to TC-SPK-09.
library;

import '../../lib/core/models/speaker_models.dart';
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

void main() {
  print('Running Contract C6 Dynamic Speaker Fallback Tests (TC-SPK-01 to TC-SPK-09)...');

  // TC-SPK-01: Tier 1 (Profile with name)
  {
    final context = UtteranceContext(
      speakerProfiles: {
        'spk_01': const SpeakerProfile(speakerId: 'spk_01', name: 'Dr. Sarah Lin'),
      },
    );
    final resolved = SpeakerResolver.resolveSpeakerName(
      speakerId: 'spk_01',
      context: context,
    );
    assertEqual(resolved, 'Dr. Sarah Lin', 'TC-SPK-01 Tier 1 Profile Name');
    print('  [PASS] TC-SPK-01: Tier 1 Enrolled Profile Name');
  }

  // TC-SPK-02: Tier 2 (Active Roster Suspect)
  {
    final context = UtteranceContext(
      speakerProfiles: {},
      activeRoster: {
        'spk_02': const MemoryEventSpeakerRosterEntry(
          speakerId: 'spk_02',
          suspectedName: 'Marcus (DevOps)',
          confidenceScore: 0.88,
        ),
      },
    );
    final resolved = SpeakerResolver.resolveSpeakerName(
      speakerId: 'spk_02',
      context: context,
    );
    assertEqual(resolved, 'Marcus (DevOps)', 'TC-SPK-02 Tier 2 Roster Suspect');
    print('  [PASS] TC-SPK-02: Tier 2 Active Roster Suspect');
  }

  // TC-SPK-03: Tier 2b (Profile with empty name but non-empty suspectedName)
  {
    final context = UtteranceContext(
      speakerProfiles: {
        'spk_03': const SpeakerProfile(
          speakerId: 'spk_03',
          name: '',
          suspectedName: 'Elena',
        ),
      },
    );
    final resolved = SpeakerResolver.resolveSpeakerName(
      speakerId: 'spk_03',
      context: context,
    );
    assertEqual(resolved, 'Elena', 'TC-SPK-03 Tier 2b Profile Suspect');
    print('  [PASS] TC-SPK-03: Tier 2b Profile Suspect Fallback');
  }

  // TC-SPK-04: Concrete Utterance Label Preservation
  {
    final context = const UtteranceContext(
      speakerProfiles: {},
      activeRoster: {},
      localLabel: 'Alice',
    );
    final resolved = SpeakerResolver.resolveSpeakerName(
      speakerId: 'spk_anon',
      context: context,
    );
    assertEqual(resolved, 'Alice', 'TC-SPK-04 Concrete Local Label');
    print('  [PASS] TC-SPK-04: Concrete Utterance Label Preserved');
  }

  // TC-SPK-05: Generic Label ("Speaker", "Unknown", "Speaker 99") falls back to Tier 3
  {
    for (final genericLabel in ['Speaker', 'Unknown', 'Speaker 5', '  ']) {
      final context = UtteranceContext(
        speakerProfiles: {},
        activeRoster: {},
        localLabel: genericLabel,
        unknownSpeakerIndex: 1,
      );
      final resolved = SpeakerResolver.resolveSpeakerName(
        speakerId: 'spk_anon',
        context: context,
      );
      assertEqual(resolved, 'Speaker 1', 'TC-SPK-05 Generic label fallback for $genericLabel');
    }
    print('  [PASS] TC-SPK-05: Generic Labels Fall Back to Tier 3');
  }

  // TC-SPK-06, 07, 08: SessionSpeakerResolver Deterministic Unknown Indexing
  {
    final session = SessionSpeakerResolver(
      speakerProfiles: {
        'spk_01': const SpeakerProfile(speakerId: 'spk_01', name: 'Dr. Sarah Lin'),
      },
      activeRoster: {
        'spk_roster': const MemoryEventSpeakerRosterEntry(
          speakerId: 'spk_roster',
          suspectedName: 'Marcus',
        ),
      },
    );

    // Named profile resolves to Dr. Sarah Lin
    assertEqual(session.resolve(speakerId: 'spk_01'), 'Dr. Sarah Lin', 'Session resolved spk_01');

    // First unknown anonymous gets Speaker 1
    final anon1 = session.resolve(speakerId: 'spk_anon_first');
    assertEqual(anon1, 'Speaker 1', 'TC-SPK-06 First anonymous index');
    print('  [PASS] TC-SPK-06: First Anonymous Speaker -> "Speaker 1"');

    // Second unknown anonymous gets Speaker 2
    final anon2 = session.resolve(clusterId: 'cls_anon_second');
    assertEqual(anon2, 'Speaker 2', 'TC-SPK-07 Second anonymous index');
    print('  [PASS] TC-SPK-07: Second Anonymous Speaker -> "Speaker 2"');

    // Repeat first unknown anonymous -> must deterministically return Speaker 1
    final repeat1 = session.resolve(speakerId: 'spk_anon_first');
    assertEqual(repeat1, 'Speaker 1', 'TC-SPK-08 Repeated anonymous token maintains identical index');
    print('  [PASS] TC-SPK-08: Deterministic Index Token Reuse');
  }

  // TC-SPK-09: In-Text Speaker Tag Replacer
  {
    final session = SessionSpeakerResolver(
      speakerProfiles: {
        'spk_01': const SpeakerProfile(speakerId: 'spk_01', name: 'Dr. Sarah Lin'),
      },
    );

    final text = 'Hello [speaker:spk_01], meet [speaker:spk_anon] and <speaker:spk_other>!';
    final replaced = session.replaceSpeakerTags(text);
    assertEqual(
      replaced,
      'Hello Dr. Sarah Lin, meet Speaker 1 and Speaker 2!',
      'TC-SPK-09 In-Text Tag Replacer',
    );
    print('  [PASS] TC-SPK-09: In-Text Tag Replacement ([speaker:id] and <speaker:id>)');
  }

  print('\nALL 9 CONTRACT C6 SPEAKER FALLBACK TESTS PASSED SUCCESSFULLY!');
}
