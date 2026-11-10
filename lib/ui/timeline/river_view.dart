/// Chronological ascending river feed (`00:00 -> 23:59`) with fluid cards.
library;

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../../core/models/timeline_models.dart';
import '../cards/activity_card.dart';
import '../cards/analyzing_chain_card.dart';
import '../cards/live_card.dart';
import '../cards/scattered_chat_card.dart';
import '../theme/hemory_theme.dart';
import 'calendar_view.dart';

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
  final Future<void> Function()? onRefresh;

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
    this.onRefresh,
  });

  @override
  Widget build(BuildContext context) {
    final feedItems = intervals
        .where((i) =>
            i.itemType != 'calendar_idle_segment' &&
            i.tier != TimelineTier.p0LiveCard &&
            i.tier < TimelineTier.p4Coverage)
        .toList()
      ..sort((a, b) {
        final cmp = a.startMs.compareTo(b.startMs);
        if (cmp != 0) return cmp;
        return a.tier.compareTo(b.tier);
      });

    final isToday = DateUtils.isSameDay(selectedDate, DateTime.now());
    final showLive = isLiveRecording && isToday;

    if (feedItems.isEmpty && !showLive) {
      final empty = ListView(
        physics: const AlwaysScrollableScrollPhysics(
          parent: BouncingScrollPhysics(),
        ),
        children: [
          SizedBox(
            height: 360.0,
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.graphic_eq_rounded,
                    size: 36.0,
                    color: HemoryColors.textLightMuted.withValues(alpha: 0.35),
                  ),
                  const SizedBox(height: 12.0),
                  Text(
                    DateFormat('yyyy-MM-dd').format(selectedDate),
                    style: const TextStyle(
                      fontSize: 13.0,
                      fontWeight: FontWeight.w500,
                      color: HemoryColors.textLightMuted,
                      fontFeatures: [FontFeature.tabularFigures()],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      );
      if (onRefresh != null) {
        return RefreshIndicator(
          color: HemoryColors.sagePrimary,
          backgroundColor: HemoryColors.darkSurface1,
          onRefresh: onRefresh!,
          child: empty,
        );
      }
      return empty;
    }

    final scrollView = CustomScrollView(
      key: const PageStorageKey('timeline_river_scroll'),
      physics: const AlwaysScrollableScrollPhysics(
        parent: BouncingScrollPhysics(),
      ),
      slivers: [
        if (feedItems.isNotEmpty)
          SliverPadding(
            padding: const EdgeInsets.fromLTRB(16.0, 12.0, 16.0, 4.0),
            sliver: SliverList(
              delegate: SliverChildBuilderDelegate(
                (context, index) {
                  final item = feedItems[index];
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 10.0),
                    child: _buildFeedCard(item),
                  );
                },
                childCount: feedItems.length,
              ),
            ),
          ),
        if (showLive)
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16.0, 4.0, 16.0, 20.0),
              child: TimelineLiveCard(
                elapsedMs: liveElapsedMs,
                meteringSamples: liveMeteringSamples,
                isPaused: isLivePaused,
                onStopPressed: onLiveStopPressed,
                onPauseTogglePressed: onLivePauseTogglePressed,
              ),
            ),
          ),
        const SliverToBoxAdapter(child: SizedBox(height: 24.0)),
      ],
    );

    if (onRefresh != null) {
      return RefreshIndicator(
        color: HemoryColors.sagePrimary,
        backgroundColor: HemoryColors.darkSurface1,
        onRefresh: onRefresh!,
        child: scrollView,
      );
    }
    return scrollView;
  }

  Widget _buildFeedCard(TimelineInterval interval) {
    switch (interval.tier) {
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
          onTap: () => onCardTapped?.call(interval),
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
          onTap: () => onCardTapped?.call(interval),
        );

      default:
        return const SizedBox.shrink();
    }
  }
}
