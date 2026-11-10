/// Contract C5: 5-Tier Priority Interval Clipping Engine.
/// Authoritative implementation based on formal mathematical interval geometry.
library;

import 'dart:math';
import '../../core/models/timeline_models.dart';

class TimelineClippingEngine {
  /// Geometric clipping of multi-tier intervals.
  /// Higher-priority intervals subtract from overlapping lower-priority intervals.
  /// Resulting intervals are deterministically sorted by startMs without overlaps.
  static List<TimelineInterval> clipTimelineIntervals(List<TimelineInterval> intervals) {
    if (intervals.isEmpty) return const [];

    // Filter out degenerate zero-duration or negative intervals
    final valid = intervals.where((i) => i.durationMs > 0).toList();
    if (valid.isEmpty) return const [];

    // Sort by tier ascending (P0 first, P4 last), then startMs, then endMs
    valid.sort((a, b) {
      final tierCmp = a.tier.compareTo(b.tier);
      if (tierCmp != 0) return tierCmp;
      final startCmp = a.startMs.compareTo(b.startMs);
      if (startCmp != 0) return startCmp;
      return a.endMs.compareTo(b.endMs);
    });

    final placed = <TimelineInterval>[];

    for (final item in valid) {
      // Current pieces of this interval that have not been subtracted yet
      var currentPieces = <_Piece>[_Piece(item.startMs, item.endMs)];

      for (final higher in placed) {
        final hStart = higher.startMs;
        final hEnd = higher.endMs;
        final nextPieces = <_Piece>[];

        for (final p in currentPieces) {
          // Half-open interval disjoint check: [p.start, p.end) and [hStart, hEnd)
          if (hEnd <= p.start || hStart >= p.end) {
            // Case A: Disjoint or touching borders -> untouched
            nextPieces.add(p);
          } else {
            // Overlap exists: subtract [hStart, hEnd) from [p.start, p.end)
            // Left surviving fragment (Case C or D)
            if (p.start < hStart) {
              nextPieces.add(_Piece(p.start, hStart));
            }
            // Right surviving fragment (Case B or D)
            if (p.end > hEnd) {
              nextPieces.add(_Piece(hEnd, p.end));
            }
          }
        }

        currentPieces = nextPieces;
        if (currentPieces.isEmpty) break; // Completely absorbed (Case E)
      }

      // Instantiate surviving pieces
      for (var idx = 0; idx < currentPieces.length; idx++) {
        final piece = currentPieces[idx];
        if (piece.end > piece.start) {
          final subId = currentPieces.length > 1
              ? '${item.intervalId}_part$idx'
              : item.intervalId;

          placed.add(TimelineInterval(
            intervalId: subId,
            startMs: piece.start,
            endMs: piece.end,
            tier: item.tier,
            itemType: item.itemType,
            payload: item.payload,
            startsBeforePage: item.startsBeforePage,
            endsAfterPage: item.endsAfterPage,
          ));
        }
      }
    }

    // Sort final placed intervals strictly by startMs ascending, then endMs, then tier
    placed.sort((a, b) {
      final startCmp = a.startMs.compareTo(b.startMs);
      if (startCmp != 0) return startCmp;
      final endCmp = a.endMs.compareTo(b.endMs);
      if (endCmp != 0) return endCmp;
      return a.tier.compareTo(b.tier);
    });

    return placed;
  }

  /// Fills unoccupied gaps in [dayStartMs, dayEndMs] with calendar_idle_segment (P4).
  static List<TimelineInterval> fillCalendarIdleSegments({
    required List<TimelineInterval> placedIntervals,
    required int dayStartMs,
    required int dayEndMs,
  }) {
    if (dayStartMs >= dayEndMs) return placedIntervals;

    final result = <TimelineInterval>[];
    var cursor = dayStartMs;

    for (final item in placedIntervals) {
      // Clamped bounds for gap evaluation
      final effectiveStart = max(item.startMs, dayStartMs);
      final effectiveEnd = min(item.endMs, dayEndMs);

      if (effectiveStart > cursor) {
        // Gap exists: insert idle segment
        result.add(TimelineInterval(
          intervalId: 'idle_${cursor}_$effectiveStart',
          startMs: cursor,
          endMs: effectiveStart,
          tier: TimelineTier.p4Coverage,
          itemType: 'calendar_idle_segment',
        ));
      }

      result.add(item);
      cursor = max(cursor, effectiveEnd);
    }

    if (cursor < dayEndMs) {
      result.add(TimelineInterval(
        intervalId: 'idle_${cursor}_$dayEndMs',
        startMs: cursor,
        endMs: dayEndMs,
        tier: TimelineTier.p4Coverage,
        itemType: 'calendar_idle_segment',
      ));
    }

    return result;
  }

  /// Collapses consecutive P3 ScatteredChat items within [maxGapMs] into a single ScatteredChatGroup.
  static List<TimelineInterval> collapseAdjacentFragmentRenderables(
    List<TimelineInterval> intervals, {
    int maxGapMs = 300000, // 5 minutes
  }) {
    if (intervals.length < 2) return intervals;

    final output = <TimelineInterval>[];
    TimelineInterval? currentChatGroup;

    for (final item in intervals) {
      if (item.tier == TimelineTier.p3ScatteredChat) {
        if (currentChatGroup == null) {
          currentChatGroup = item;
        } else {
          final gap = item.startMs - currentChatGroup.endMs;
          if (gap <= maxGapMs) {
            // Merge into group
            currentChatGroup = TimelineInterval(
              intervalId: currentChatGroup.intervalId,
              startMs: currentChatGroup.startMs,
              endMs: max(currentChatGroup.endMs, item.endMs),
              tier: TimelineTier.p3ScatteredChat,
              itemType: 'ScatteredChatGroup',
              payload: currentChatGroup.payload,
            );
          } else {
            output.add(currentChatGroup);
            currentChatGroup = item;
          }
        }
      } else {
        if (currentChatGroup != null) {
          output.add(currentChatGroup);
          currentChatGroup = null;
        }
        output.add(item);
      }
    }

    if (currentChatGroup != null) {
      output.add(currentChatGroup);
    }

    return output;
  }

  /// Clamps interval to day boundary, flagging midnight crossovers.
  static TimelineInterval? clampToDayBoundary(
    TimelineInterval interval,
    int dayStartMs,
    int dayEndMs,
  ) {
    if (interval.endMs <= dayStartMs || interval.startMs >= dayEndMs) {
      return null; // Entirely outside page
    }

    final clampedStart = max(interval.startMs, dayStartMs);
    final clampedEnd = min(interval.endMs, dayEndMs);

    return interval.copyWith(
      startMs: clampedStart,
      endMs: clampedEnd,
      startsBeforePage: interval.startMs < dayStartMs,
      endsAfterPage: interval.endMs > dayEndMs,
    );
  }
}

class _Piece {
  final int start;
  final int end;
  const _Piece(this.start, this.end);
}
