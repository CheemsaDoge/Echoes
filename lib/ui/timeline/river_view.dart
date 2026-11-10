/// Chronological river feed stream with sticky date headers and fluid cards.
library;

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../../core/models/timeline_models.dart';
import '../theme/hemory_theme.dart';
import '../cards/live_card.dart';
import '../cards/analyzing_chain_card.dart';
import '../cards/activity_card.dart';
import '../cards/scattered_chat_card.dart';

class RiverView extends StatelessWidget {
  final DateTime selectedDate;
  final List<TimelineInterval> intervals;
  final List<double> liveMeteringSamples;
  final int liveElapsedMs;
  final bool isLiveRecording;
  final bool isLivePaused;
  final VoidCallback? onLiveStopPressed;
  final VoidCallback? onLivePauseTogglePressed;
  final void Function(TimelineInterval interval)? onCardTapped;

  const RiverView({
    super.key,
    required this.selectedDate,
    required this.intervals,
    this.liveMeteringSamples = const [],
    this.liveElapsedMs = 0,
    this.isLiveRecording = false,
    this.isLivePaused = false,
    this.onLiveStopPressed,
    this.onLivePauseTogglePressed,
    this.onCardTapped,
  });

  @override
  Widget build(BuildContext context) {
    // Filter out idle filler segments in River view; River is an event feed
    final feedItems = intervals
        .where((i) => i.itemType != 'calendar_idle_segment')
        .toList();

    if (feedItems.isEmpty && !isLiveRecording) {
      return _buildEmptyState();
    }

    final dateHeader = DateFormat('EEEE, MMMM d').format(selectedDate);

    return CustomScrollView(
      key: const PageStorageKey('timeline_river_scroll'),
      physics: const BouncingScrollPhysics(),
      slivers: [
        // Sticky Date Header
        SliverPersistentHeader(
          pinned: true,
          delegate: _DateHeaderDelegate(title: dateHeader),
        ),
        // Live Card (if recording)
        if (isLiveRecording)
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
              child: TimelineLiveCard(
                elapsedMs: liveElapsedMs,
                meteringSamples: liveMeteringSamples,
                isPaused: isLivePaused,
                onStopPressed: onLiveStopPressed,
                onPauseTogglePressed: onLivePauseTogglePressed,
              ),
            ),
          ),
        // Chronological Items
        SliverPadding(
          padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
          sliver: SliverList(
            delegate: SliverChildBuilderDelegate(
              (context, index) {
                final item = feedItems[index];
                return Padding(
                  padding: const EdgeInsets.only(bottom: 12.0),
                  child: _buildFeedCard(item),
                );
              },
              childCount: feedItems.length,
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.mic_none_outlined,
            size: 40.0,
            color: HemoryColors.textLightMuted.withOpacity(0.4),
          ),
          const SizedBox(height: 12.0),
          Text(
            DateFormat('MMMM d, yyyy').format(selectedDate),
            style: const TextStyle(
              fontSize: 14.0,
              fontWeight: FontWeight.w500,
              color: HemoryColors.textLightMuted,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFeedCard(TimelineInterval interval) {
    switch (interval.tier) {
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
          onTap: () => onCardTapped?.call(interval),
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
          onTap: () => onCardTapped?.call(interval),
        );

      case TimelineTier.p0LiveCard:
      case TimelineTier.p4Coverage:
      default:
        return const SizedBox.shrink();
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

class _DateHeaderDelegate extends SliverPersistentHeaderDelegate {
  final String title;

  _DateHeaderDelegate({required this.title});

  @override
  double get minExtent => 42.0;

  @override
  double get maxExtent => 42.0;

  @override
  Widget build(
    BuildContext context,
    double shrinkOffset,
    bool overlapsContent,
  ) {
    return Container(
      color: HemoryColors.darkBg,
      padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
      alignment: Alignment.centerLeft,
      child: Text(
        title,
        style: const TextStyle(
          fontSize: 13.0,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.2,
          color: HemoryColors.textLightSecondary,
        ),
      ),
    );
  }

  @override
  bool shouldRebuild(covariant _DateHeaderDelegate oldDelegate) {
    return oldDelegate.title != title;
  }
}
