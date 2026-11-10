/// Timeline models conforming to Contract C5 (5-Tier Priority Interval Clipping Engine).
library;

/// Priority tiers per Contract C5. Lower integer indicates strictly higher priority.
abstract class TimelineTier {
  static const int p0LiveCard = 0;       // Live recording card with waveform
  static const int p1AnalyzingChain = 1;  // Ongoing audio processing pipeline
  static const int p2Activity = 2;        // Structured conversational memory card
  static const int p3ScatteredChat = 3;   // Scattered dialog turns & chat excerpts
  static const int p4Coverage = 4;        // Raw audio coverage ribbon & idle gaps

  static const Map<int, String> tierNames = {
    p0LiveCard: 'P0_LIVE_CARD',
    p1AnalyzingChain: 'P1_ANALYZING_CHAIN',
    p2Activity: 'P2_ACTIVITY',
    p3ScatteredChat: 'P3_SCATTERED_CHAT',
    p4Coverage: 'P4_COVERAGE',
  };
}

/// Two timeline view modes supported by the presentation layer.
enum TimelineViewMode {
  calendar,
  river;

  bool get isCalendar => this == calendar;
  bool get isRiver => this == river;
}

/// Immutable timeline interval on the [0, 1440] minute or millisecond grid.
class TimelineInterval {
  final String intervalId;
  final int startMs;
  final int endMs;
  final int tier;
  final String itemType;
  final Map<String, dynamic>? payload;
  final bool startsBeforePage;
  final bool endsAfterPage;

  TimelineInterval({
    required this.intervalId,
    required this.startMs,
    required this.endMs,
    required this.tier,
    required this.itemType,
    this.payload,
    this.startsBeforePage = false,
    this.endsAfterPage = false,
  }) {
    if (startMs > endMs) {
      throw ArgumentError('Invalid interval: startMs ($startMs) > endMs ($endMs)');
    }
  }

  int get durationMs => endMs - startMs;

  TimelineInterval copyWith({
    String? intervalId,
    int? startMs,
    int? endMs,
    int? tier,
    String? itemType,
    Map<String, dynamic>? payload,
    bool? startsBeforePage,
    bool? endsAfterPage,
  }) {
    return TimelineInterval(
      intervalId: intervalId ?? this.intervalId,
      startMs: startMs ?? this.startMs,
      endMs: endMs ?? this.endMs,
      tier: tier ?? this.tier,
      itemType: itemType ?? this.itemType,
      payload: payload ?? this.payload,
      startsBeforePage: startsBeforePage ?? this.startsBeforePage,
      endsAfterPage: endsAfterPage ?? this.endsAfterPage,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is TimelineInterval &&
          runtimeType == other.runtimeType &&
          intervalId == other.intervalId &&
          startMs == other.startMs &&
          endMs == other.endMs &&
          tier == other.tier &&
          itemType == other.itemType &&
          startsBeforePage == other.startsBeforePage &&
          endsAfterPage == other.endsAfterPage;

  @override
  int get hashCode => Object.hash(
        intervalId,
        startMs,
        endMs,
        tier,
        itemType,
        startsBeforePage,
        endsAfterPage,
      );

  @override
  String toString() =>
      'TimelineInterval(id: $intervalId, range: [$startMs..$endMs], tier: ${TimelineTier.tierNames[tier]}, type: $itemType)';
}

/// Continuous coverage segment for CoveragePainter on [0, 1440] minute grid.
class TimelineCoverageSegment {
  final double startMinute;
  final double endMinute;
  final bool isLive;

  const TimelineCoverageSegment({
    required this.startMinute,
    required this.endMinute,
    this.isLive = false,
  });

  double get durationMinutes => endMinute - startMinute;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is TimelineCoverageSegment &&
          runtimeType == other.runtimeType &&
          startMinute == other.startMinute &&
          endMinute == other.endMinute &&
          isLive == other.isLive;

  @override
  int get hashCode => Object.hash(startMinute, endMinute, isLive);

  @override
  String toString() =>
      'TimelineCoverageSegment([$startMinute..$endMinute], isLive: $isLive)';
}
