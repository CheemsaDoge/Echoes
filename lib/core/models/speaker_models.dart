/// Speaker models conforming to Contract C6 (3-Tier Dynamic Text Speaker Fallback Resolution).
library;

/// Speaker profile representing an enrolled voiceprint or user-labeled speaker.
class SpeakerProfile {
  final String speakerId;
  final String name;
  final String? roleLabel;
  final String? avatarUrl;
  final String? suspectedName;

  const SpeakerProfile({
    required this.speakerId,
    required this.name,
    this.roleLabel,
    this.avatarUrl,
    this.suspectedName,
  });

  factory SpeakerProfile.fromJson(Map<String, dynamic> json) {
    return SpeakerProfile(
      speakerId: json['speaker_id'] as String? ?? json['id'] as String? ?? '',
      name: (json['name'] as String?)?.trim() ?? '',
      roleLabel: json['role_label'] as String?,
      avatarUrl: json['avatar_url'] as String?,
      suspectedName: json['suspected_name'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'speaker_id': speakerId,
        'name': name,
        if (roleLabel != null) 'role_label': roleLabel,
        if (avatarUrl != null) 'avatar_url': avatarUrl,
        if (suspectedName != null) 'suspected_name': suspectedName,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is SpeakerProfile &&
          runtimeType == other.runtimeType &&
          speakerId == other.speakerId &&
          name == other.name &&
          roleLabel == other.roleLabel &&
          avatarUrl == other.avatarUrl &&
          suspectedName == other.suspectedName;

  @override
  int get hashCode => Object.hash(speakerId, name, roleLabel, avatarUrl, suspectedName);

  @override
  String toString() => 'SpeakerProfile($speakerId, name: "$name", suspected: "$suspectedName")';
}

/// Roster candidate entry from active conversational memory activity.
class MemoryEventSpeakerRosterEntry {
  final String speakerId;
  final String suspectedName;
  final double confidenceScore;

  const MemoryEventSpeakerRosterEntry({
    required this.speakerId,
    required this.suspectedName,
    this.confidenceScore = 0.0,
  });

  factory MemoryEventSpeakerRosterEntry.fromJson(Map<String, dynamic> json) {
    return MemoryEventSpeakerRosterEntry(
      speakerId: json['speaker_id'] as String? ?? json['id'] as String? ?? '',
      suspectedName: (json['suspected_name'] as String?)?.trim() ?? '',
      confidenceScore: (json['confidence_score'] as num?)?.toDouble() ?? 0.0,
    );
  }

  Map<String, dynamic> toJson() => {
        'speaker_id': speakerId,
        'suspected_name': suspectedName,
        'confidence_score': confidenceScore,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is MemoryEventSpeakerRosterEntry &&
          runtimeType == other.runtimeType &&
          speakerId == other.speakerId &&
          suspectedName == other.suspectedName &&
          confidenceScore == other.confidenceScore;

  @override
  int get hashCode => Object.hash(speakerId, suspectedName, confidenceScore);

  @override
  String toString() =>
      'MemoryEventSpeakerRosterEntry($speakerId, suspected: "$suspectedName", conf: $confidenceScore)';
}

/// Context passed to resolve a single utterance speaker.
class UtteranceContext {
  final Map<String, SpeakerProfile> speakerProfiles;
  final Map<String, MemoryEventSpeakerRosterEntry> activeRoster;
  final String? localLabel;
  final int unknownSpeakerIndex;

  const UtteranceContext({
    this.speakerProfiles = const {},
    this.activeRoster = const {},
    this.localLabel,
    this.unknownSpeakerIndex = 1,
  });

  UtteranceContext copyWith({
    Map<String, SpeakerProfile>? speakerProfiles,
    Map<String, MemoryEventSpeakerRosterEntry>? activeRoster,
    String? localLabel,
    int? unknownSpeakerIndex,
  }) {
    return UtteranceContext(
      speakerProfiles: speakerProfiles ?? this.speakerProfiles,
      activeRoster: activeRoster ?? this.activeRoster,
      localLabel: localLabel ?? this.localLabel,
      unknownSpeakerIndex: unknownSpeakerIndex ?? this.unknownSpeakerIndex,
    );
  }
}
