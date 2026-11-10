/// Continuous vertical 0..1440 minute canvas representing a 24-hour day.
/// Strictly ascending chronological layout with deterministic collision avoidance
/// so closely spaced conversations never overlap or overflow.
library;

import 'dart:math';
import 'package:flutter/material.dart';
import '../../core/models/timeline_models.dart';
import '../cards/activity_card.dart';
import '../cards/analyzing_chain_card.dart';
import '../cards/calendar_idle_segment.dart';
import '../cards/live_card.dart';
import '../cards/scattered_chat_card.dart';
import '../theme/hemory_theme.dart';
import 'coverage_painter.dart';

String formatTimelineRangeLabel({
  required int startMin,
  required int endMin,
  int durationMs = 0,
}) {
  final startH = (startMin ~/ 60).toString().padLeft(2, '0');
  final startM = (startMin % 60).toString().padLeft(2, '0');
  final endH = (endMin ~/ 60).toString().padLeft(2, '0');
  final endM = (endMin % 60).toString().padLeft(2, '0');

  String durText;
  if (durationMs > 0 && durationMs < 60000) {
    final secs = max(1, (durationMs / 1000).round());
    durText = '${secs}s';
  } else {
    final mins = durationMs > 0
        ? max(1, (durationMs / 60000).round())
        : max(1, endMin - startMin);
    if (mins >= 60) {
      final h = mins ~/ 60;
      final m = mins % 60;
      durText = m > 0 ? '${h}h ${m}m' : '${h}h';
    } else {
      durText = '${mins}m';
    }
  }

  if (startMin == endMin || (endMin - startMin <= 1 && durationMs > 0 && durationMs < 60000)) {
    return '$startH:$startM · $durText';
  }
  return '$startH:$startM - $endH:$endM · $durText';
}

Map<String, ChainStepState> buildChainStepStates(String currentPhase) {
  const ordered = AnalyzingChainCard.canonicalSteps;
  final activeIdx = ordered.indexOf(currentPhase.toLowerCase());
  final effectiveIdx = activeIdx == -1 ? 2 : activeIdx;
  return {
    for (var i = 0; i < ordered.length; i++)
      ordered[i]: i < effectiveIdx
          ? ChainStepState.completed
          : (i == effectiveIdx ? ChainStepState.active : ChainStepState.pending),
  };
}

class _PlacedCalendarItem {
  final TimelineInterval interval;
  final double top;
  final double height;

  const _PlacedCalendarItem({
    required this.interval,
    required this.top,
    required this.height,
  });
}

class CalendarView extends StatefulWidget {
  final DateTime selectedDate;
  final List<TimelineInterval> intervals;
  final List<TimelineCoverageSegment> coverageSegments;
  final List<double> liveMeteringSamples;
  final int liveElapsedMs;
  final bool isLiveRecording;
  final bool isLivePaused;
  final VoidCallback? onLiveStopPressed;
  final VoidCallback? onLivePauseTogglePressed;
  final void Function(TimelineInterval interval)? onCardTapped;
  final Future<void> Function()? onRefresh;

  const CalendarView({
    super.key,
    required this.selectedDate,
    required this.intervals,
    required this.coverageSegments,
    this.liveMeteringSamples = const [],
    this.liveElapsedMs = 0,
    this.isLiveRecording = false,
    this.isLivePaused = false,
    this.onLiveStopPressed,
    this.onLivePauseTogglePressed,
    this.onCardTapped,
    this.onRefresh,
  });

  @override
  State<CalendarView> createState() => _CalendarViewState();
}

class _CalendarViewState extends State<CalendarView> {
  static const double baseHourHeight = 76.0;
  static const double pixelsPerMinute = baseHourHeight / 60.0;
  static const double totalCanvasHeight = 1440.0 * pixelsPerMinute;
  static const double cardLeftOffset = 72.0;

  late final ScrollController _scrollController;

  @override
  void initState() {
    super.initState();
    _scrollController = ScrollController();

    WidgetsBinding.instance.addPostFrameCallback((_) {
      _scrollToRelevantTime();
    });
  }

  @override
  void didUpdateWidget(covariant CalendarView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!DateUtils.isSameDay(oldWidget.selectedDate, widget.selectedDate)) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _scrollToRelevantTime();
      });
    }
  }

  void _scrollToRelevantTime() {
    if (!_scrollController.hasClients) return;

    final now = DateTime.now();
    final isToday = DateUtils.isSameDay(widget.selectedDate, now);
    final realEvents = widget.intervals
        .where((i) => i.tier < TimelineTier.p4Coverage)
        .toList();

    double targetMinute = 9 * 60;
    if (realEvents.isNotEmpty) {
      targetMinute = isToday
          ? realEvents.last.startMs.toDouble()
          : realEvents.first.startMs.toDouble();
    } else if (isToday) {
      targetMinute = (now.hour * 60 + now.minute).toDouble();
    }

    final targetOffset = max(0.0, (targetMinute * pixelsPerMinute) - 160.0);
    _scrollController.jumpTo(
      min(targetOffset, _scrollController.position.maxScrollExtent),
    );
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  double _getCurrentMinute() {
    final now = DateTime.now();
    return (now.hour * 60 + now.minute + (now.second / 60.0));
  }

  double _minimumHeightForInterval(TimelineInterval interval) {
    switch (interval.tier) {
      case TimelineTier.p0LiveCard:
        return 112.0;
      case TimelineTier.p1AnalyzingChain:
        return 94.0;
      case TimelineTier.p2Activity:
        final p = interval.payload ?? const {};
        final title = (p['title'] as String? ?? '').trim();
        final summary = (p['summary'] as String? ?? '').trim();
        final hasSummary = summary.isNotEmpty && summary != title;
        return hasSummary ? 132.0 : 96.0;
      case TimelineTier.p3ScatteredChat:
        final p = interval.payload ?? const {};
        final count = ((p['snippets'] as List?)?.length ?? 1).clamp(1, 3);
        final extra = ((p['snippets'] as List?)?.length ?? 0) > 3 ? 20.0 : 0.0;
        return 26.0 + (count * 46.0) + extra;
      case TimelineTier.p4Coverage:
      default:
        return 36.0;
    }
  }

  List<_PlacedCalendarItem> _computeAscendingLayout() {
    final sorted = List<TimelineInterval>.from(widget.intervals)
      ..sort((a, b) {
        final cmp = a.startMs.compareTo(b.startMs);
        if (cmp != 0) return cmp;
        return a.tier.compareTo(b.tier);
      });

    final placed = <_PlacedCalendarItem>[];
    double lastForegroundBottom = -8.0;

    // First pass: lay out P0..P3 foreground cards strictly ascending without overlap.
    final foregroundCards = <_PlacedCalendarItem>[];
    for (final interval in sorted) {
      if (interval.tier >= TimelineTier.p4Coverage) continue;
      final startMin = interval.startMs.toDouble();
      final endMin = interval.endMs.toDouble();
      final naturalTop = startMin * pixelsPerMinute;
      final naturalHeight = (endMin - startMin) * pixelsPerMinute;
      final minHeight = _minimumHeightForInterval(interval);

      final top = max(naturalTop, lastForegroundBottom + 8.0);
      final height = max(naturalHeight, minHeight);
      lastForegroundBottom = top + height;
      foregroundCards.add(
        _PlacedCalendarItem(interval: interval, top: top, height: height),
      );
    }

    // Second pass: include long idle markers ONLY when they don't collide with foreground cards.
    for (final interval in sorted) {
      if (interval.tier < TimelineTier.p4Coverage) continue;
      final durMin = interval.endMs - interval.startMs;
      if (durMin < 90) continue;
      final top = interval.startMs.toDouble() * pixelsPerMinute + 8.0;
      final bottom = interval.endMs.toDouble() * pixelsPerMinute - 8.0;
      final height = bottom - top;
      if (height < 40.0) continue;
      final collides = foregroundCards.any(
        (fg) => fg.top < bottom + 6.0 && (fg.top + fg.height) > top - 6.0,
      );
      if (!collides) {
        placed.add(
          _PlacedCalendarItem(interval: interval, top: top, height: height),
        );
      }
    }

    placed.addAll(foregroundCards);
    placed.sort((a, b) => a.top.compareTo(b.top));
    return placed;
  }

  @override
  Widget build(BuildContext context) {
    final isToday = DateUtils.isSameDay(widget.selectedDate, DateTime.now());
    final currentMinute = _getCurrentMinute();
    final placedItems = _computeAscendingLayout();
    final maxCardBottom = placedItems.isEmpty
        ? 0.0
        : placedItems.map((e) => e.top + e.height).reduce(max);
    final effectiveCanvasHeight = max(totalCanvasHeight, maxCardBottom + 88.0);

    return LayoutBuilder(
      builder: (context, constraints) {
        final canvasWidth = constraints.maxWidth;
        final cardWidth = max(220.0, canvasWidth - cardLeftOffset - 16.0);

        final scrollView = SingleChildScrollView(
          key: const PageStorageKey('timeline_calendar_scroll'),
          controller: _scrollController,
          physics: const AlwaysScrollableScrollPhysics(
            parent: BouncingScrollPhysics(),
          ),
          child: SizedBox(
            height: effectiveCanvasHeight,
            width: canvasWidth,
            child: Stack(
              children: [
                Positioned.fill(
                  child: CustomPaint(
                    painter: CoveragePainter(
                      segments: widget.coverageSegments,
                      pixelsPerMinute: pixelsPerMinute,
                      currentMinute: currentMinute,
                      isToday: isToday,
                    ),
                  ),
                ),
                ...placedItems.map((placed) {
                  if (placed.interval.tier >= TimelineTier.p4Coverage) {
                    return Positioned(
                      top: placed.top,
                      left: cardLeftOffset,
                      width: cardWidth,
                      height: placed.height,
                      child: _buildCardWidget(placed.interval),
                    );
                  }
                  return Positioned(
                    top: placed.top,
                    left: cardLeftOffset,
                    width: cardWidth,
                    child: ConstrainedBox(
                      constraints: BoxConstraints(minHeight: placed.height),
                      child: _buildCardWidget(placed.interval),
                    ),
                  );
                }),
              ],
            ),
          ),
        );

        if (widget.onRefresh != null) {
          return RefreshIndicator(
            color: HemoryColors.sagePrimary,
            backgroundColor: HemoryColors.darkSurface1,
            onRefresh: widget.onRefresh!,
            child: scrollView,
          );
        }
        return scrollView;
      },
    );
  }

  Widget _buildCardWidget(TimelineInterval interval) {
    switch (interval.tier) {
      case TimelineTier.p0LiveCard:
        return TimelineLiveCard(
          elapsedMs: widget.liveElapsedMs,
          meteringSamples: widget.liveMeteringSamples,
          isPaused: widget.isLivePaused,
          onStopPressed: widget.onLiveStopPressed,
          onPauseTogglePressed: widget.onLivePauseTogglePressed,
        );

      case TimelineTier.p1AnalyzingChain:
        final payload = interval.payload ?? const {};
        final progress = (payload['progress'] as num?)?.toDouble() ?? 0.45;
        final currentPhase = payload['current_phase'] as String? ?? 'transcribing';
        return AnalyzingChainCard(
          chainId: interval.intervalId,
          progress: progress,
          currentPhase: currentPhase,
          steps: buildChainStepStates(currentPhase),
        );

      case TimelineTier.p2Activity:
        final payload = interval.payload ?? const {};
        final title = payload['title'] as String? ?? '对话记录';
        final summary = payload['summary'] as String?;
        final category = payload['category'] as String? ?? '';
        final durationMs = (payload['duration_ms'] as num?)?.toInt() ?? 0;
        final speakers = ((payload['speakers'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList();
        final tags = ((payload['tags'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList();
        final pendingIds = (payload['pending_cluster_ids'] as List?) ?? const [];
        final timeRange = formatTimelineRangeLabel(
          startMin: interval.startMs,
          endMin: interval.endMs,
          durationMs: durationMs,
        );

        return ActivityCard(
          eventId: interval.intervalId,
          title: title,
          summary: summary,
          timeRange: timeRange,
          category: category,
          speakers: speakers,
          topicTags: tags,
          pendingClusterCount: pendingIds.length,
          onTap: () => widget.onCardTapped?.call(interval),
        );

      case TimelineTier.p3ScatteredChat:
        final payload = interval.payload ?? const {};
        final snippetsList = (payload['snippets'] as List?) ?? const [];
        final pendingIds = (payload['pending_cluster_ids'] as List?) ?? const [];
        final snippets = snippetsList.whereType<Map>().map((raw) {
          final m = Map<String, dynamic>.from(raw);
          return ChatSnippet(
            speakerName: m['speaker'] as String? ?? 'Speaker 1',
            text: m['text'] as String? ?? '',
            time: m['time'] as String? ?? '',
            audioUrl: m['audio_url'] as String?,
            clusterId: m['cluster_id'] as String?,
          );
        }).toList();

        return ScatteredChatConvoCard(
          groupId: interval.intervalId,
          snippets: snippets,
          pendingClusterCount: pendingIds.length,
          onTap: () => widget.onCardTapped?.call(interval),
        );

      case TimelineTier.p4Coverage:
      default:
        final durationMinutes = interval.endMs - interval.startMs;
        return CalendarIdleSegment(durationMinutes: durationMinutes);
    }
  }
}
