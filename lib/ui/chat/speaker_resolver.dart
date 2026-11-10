/// Contract C6: 3-Tier Dynamic Text Speaker Fallback Resolution Engine.
/// Authoritative implementation matching system specification §3.4.7.
library;

import '../../core/models/speaker_models.dart';

class SpeakerResolver {
  /// Standalone 3-tier resolution adhering to Contract C6.
  static String resolveSpeakerName({
    required String? speakerId,
    required UtteranceContext context,
  }) {
    // Tier 1: Enrolled / Named Speaker Profiles
    if (speakerId != null && context.speakerProfiles.containsKey(speakerId)) {
      final profile = context.speakerProfiles[speakerId]!;
      if (profile.name.trim().isNotEmpty) {
        return profile.name.trim();
      }
    }

    // Tier 2: Active Activity Roster Suspects
    if (speakerId != null && context.activeRoster.containsKey(speakerId)) {
      final entry = context.activeRoster[speakerId]!;
      if (entry.suspectedName.trim().isNotEmpty) {
        return entry.suspectedName.trim();
      }
    }

    // Tier 2b: Fallback to suspected_name on profile if present
    if (speakerId != null && context.speakerProfiles.containsKey(speakerId)) {
      final profile = context.speakerProfiles[speakerId]!;
      if (profile.suspectedName != null && profile.suspectedName!.trim().isNotEmpty) {
        return profile.suspectedName!.trim();
      }
    }

    // Utterance Local Label Preservation (concrete non-generic name)
    final label = context.localLabel?.trim();
    if (label != null &&
        label.isNotEmpty &&
        label != 'Unknown' &&
        label != 'Speaker' &&
        !RegExp(r'^Speaker\s+\d+$', caseSensitive: false).hasMatch(label)) {
      return label;
    }

    // Tier 3: Sequential Unknown Speaker Index
    return 'Speaker ${context.unknownSpeakerIndex}';
  }
}

/// Session-scoped deterministic speaker resolver managing Tier 3 sequential indices.
class SessionSpeakerResolver {
  final Map<String, SpeakerProfile> speakerProfiles;
  final Map<String, MemoryEventSpeakerRosterEntry> activeRoster;
  final Map<String, int> _unknownIndexMap = {};
  int _nextIndex = 1;

  SessionSpeakerResolver({
    Map<String, SpeakerProfile>? speakerProfiles,
    Map<String, MemoryEventSpeakerRosterEntry>? activeRoster,
  })  : speakerProfiles = Map.unmodifiable(speakerProfiles ?? {}),
        activeRoster = Map.unmodifiable(activeRoster ?? {});

  /// Resolves display name for an utterance or cluster.
  String resolve({
    String? speakerId,
    String? clusterId,
    String? localLabel,
  }) {
    // Tier 1: Enrolled Profile
    if (speakerId != null && speakerProfiles.containsKey(speakerId)) {
      final profile = speakerProfiles[speakerId]!;
      if (profile.name.trim().isNotEmpty) {
        return profile.name.trim();
      }
    }

    // Tier 2: Active Roster Suspect (checked by speakerId, clusterId, or localLabel)
    final key = speakerId ?? clusterId ?? localLabel;
    if (key != null && activeRoster.containsKey(key)) {
      final roster = activeRoster[key]!;
      if (roster.suspectedName.trim().isNotEmpty) {
        return roster.suspectedName.trim();
      }
    }

    // Tier 2b: Suspected name on profile
    if (speakerId != null && speakerProfiles.containsKey(speakerId)) {
      final profile = speakerProfiles[speakerId]!;
      if (profile.suspectedName != null && profile.suspectedName!.trim().isNotEmpty) {
        return profile.suspectedName!.trim();
      }
    }

    // Preserved concrete label
    if (localLabel != null) {
      final trimmed = localLabel.trim();
      if (trimmed.isNotEmpty &&
          trimmed != 'Unknown' &&
          trimmed != 'Speaker' &&
          !RegExp(r'^Speaker\s+\d+$', caseSensitive: false).hasMatch(trimmed)) {
        return trimmed;
      }
    }

    // Tier 3: Deterministic Sequential Index
    final unassignedKey = speakerId ?? clusterId ?? localLabel ?? '_anon_${_unknownIndexMap.length + 1}';
    if (!_unknownIndexMap.containsKey(unassignedKey)) {
      _unknownIndexMap[unassignedKey] = _nextIndex++;
    }

    return 'Speaker ${_unknownIndexMap[unassignedKey]}';
  }

  /// Replaces inline speaker tags in transcript text.
  /// Matches [speaker:id] and <speaker:id>.
  String replaceSpeakerTags(String text) {
    if (text.isEmpty) return text;

    // Pattern matching [speaker:spk_01] or <speaker:spk_01>
    final regex = RegExp(r'\[speaker:([a-zA-Z0-9_\-]+)\]|<speaker:([a-zA-Z0-9_\-]+)>');
    return text.replaceAllMapped(regex, (match) {
      final spkId = match.group(1) ?? match.group(2);
      return resolve(speakerId: spkId);
    });
  }
}
