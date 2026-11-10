/// Continuous vertical 0..1440 minute canvas representing a 24-hour day.
/// Hosts CoveragePainter, hour markers, clock needle, and dynamically positioned cards.
library;

import 'dart:math';
import 'package:flutter/material.dart';
import '../../core/models/timeline_models.dart';
import '../cards/live_card.dart';
import '../cards/analyzing_chain_card.dart';
import '../cards/activity_card.dart';
import '../cards/scattered_chat_card.dart';
import '../cards/calendar_idle_segment.dart';
import 'coverage_painter.dart';

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
  });

  @override
  State<CalendarView> createState() => _CalendarViewState();
}

class _CalendarViewState extends State<CalendarView> {
  static const double baseHourHeight = 72.0;
  static const double pixelsPerMinute = baseHourHeight / 60.0; // 1.2 dp/min
  static const double totalCanvasHeight = 1440.0 * pixelsPerMinute; // 1728.0 dp
  static const double cardLeftOffset = 76.0; // After hour labels and coverage bar
  static const double minCardHeight = 64.0;

  late final ScrollController _scrollController;

  @override
  void initState() {
    super.initState();
    _scrollController = ScrollController();

    // Scroll to current time or first event on initial load
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _scrollToRelevantTime();
    });
  }

  void _scrollToRelevantTime() {
    if (!_scrollController.hasClients) return;

    final now = DateTime.now();
    final isToday = DateUtils.isSameDay(widget.selectedDate, now);

    double targetMinute = 9 * 60; // Default to 09:00 AM
    if (isToday) {
      targetMinute = (now.hour * 60 + now.minute).toDouble();
    } else if (widget.intervals.isNotEmpty) {
      targetMinute = widget.intervals.first.startMs.toDouble();
    }

    final targetOffset = max(0.0, (targetMinute * pixelsPerMinute) - 180.0);
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

  @override
  Widget build(BuildContext context) {
    final isToday = DateUtils.isSameDay(widget.selectedDate, DateTime.now());
    final currentMinute = _getCurrentMinute();

    return LayoutBuilder(
      builder: (context, constraints) {
        final canvasWidth = constraints.maxWidth;
        final cardWidth = max(240.0, canvasWidth - cardLeftOffset - 16.0);

        return SingleChildScrollView(
          key: const PageStorageKey('timeline_calendar_scroll'),
          controller: _scrollController,
          physics: const BouncingScrollPhysics(),
          child: SizedBox(
            height: totalCanvasHeight,
            width: canvasWidth,
            child: Stack(
              children: [
                // 1. Coverage & Axis Background Canvas
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
                // 2. Positioned Cards
                ...widget.intervals.map((interval) {
                  final startMin = interval.startMs.toDouble();
                  final endMin = interval.endMs.toDouble();
                  final top = startMin * pixelsPerMinute;
                  final rawHeight = (endMin - startMin) * pixelsPerMinute;
                  final height = max(rawHeight, minCardHeight);

                  return Positioned(
                    top: top,
                    left: cardLeftOffset,
                    width: cardWidth,
                    height: height,
                    child: _buildCardWidget(interval),
                  );
                }),
              ],
            ),
          ),
        );
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
        final payload = interval.payload ?? {};
        final progress = (payload['progress'] as num?)?.toDouble() ?? 0.65;
        final currentPhase = payload['current_phase'] as String? ?? 'transcribing';
        return AnalyzingChainCard(
          chainId: interval.intervalId,
          progress: progress,
          currentPhase: currentPhase,
          steps: {
            'slicing': ChainStepState.completed,
            'vad': ChainStepState.completed,
            'transcribing': ChainStepState.active,
            'diarizing': ChainStepState.pending,
            'summarizing': ChainStepState.pending,
          },
        );

      case TimelineTier.p2Activity:
        final payload = interval.payload ?? {};
        final title = payload['title'] as String? ?? 'Memory Event';
        final summary = payload['summary'] as String? ?? interval.itemType;
        final speakers = (payload['speakers'] as List?)?.cast<String>() ?? [];
        final tags = (payload['tags'] as List?)?.cast<String>() ?? [];
        final timeRange = _formatMinuteRange(interval.startMs, interval.endMs);

        return ActivityCard(
          eventId: interval.intervalId,
          title: title,
          summary: summary,
          timeRange: timeRange,
          speakers: speakers,
          topicTags: tags,
          onTap: () => widget.onCardTapped?.call(interval),
        );

      case TimelineTier.p3ScatteredChat:
        final payload = interval.payload ?? {};
        final snippetsList = (payload['snippets'] as List?) ?? [];
        final snippets = snippetsList.map((s) {
          final m = s as Map<String, dynamic>;
          return ChatSnippet(
            speakerName: m['speaker'] as String? ?? 'Speaker 1',
            text: m['text'] as String? ?? '',
            time: m['time'] as String? ?? '',
          );
        }).toList();

        return ScatteredChatConvoCard(
          groupId: interval.intervalId,
          snippets: snippets.isNotEmpty
              ? snippets
              : [
                  ChatSnippet(
                    speakerName: 'Speaker 1',
                    text: 'Conversational fragment captured.',
                    time: _formatMinuteRange(interval.startMs, interval.endMs),
                  ),
                ],
          onTap: () => widget.onCardTapped?.call(interval),
        );

      case TimelineTier.p4Coverage:
      default:
        final durationMinutes = interval.endMs - interval.startMs;
        return CalendarIdleSegment(durationMinutes: durationMinutes);
    }
  }

  String _formatMinuteRange(int startMin, int endMin) {
    final startH = (startMin ~/ 60).toString().padLeft(2, '0');
    final startM = (startMin % 60).toString().padLeft(2, '0');
    final endH = (endMin ~/ 60).toString().padLeft(2, '0');
    final endM = (endMin % 60).toString().padLeft(2, '0');
    return '$startH:$startM - $endH:$endM';
  }
}
