/// Interactive detail sheet for P2 Memory Events and P3 Scattered Chat groups.
/// Supports audio replay, inline title/category editing, and instant voiceprint naming.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/models/speaker_models.dart';
import '../../core/models/timeline_models.dart';
import '../../core/providers/app_providers.dart';
import '../../core/services/hemory_api_client.dart';
import '../cards/activity_card.dart';
import '../theme/hemory_theme.dart';
import 'calendar_view.dart';

Future<void> showTimelineDetailSheet(
  BuildContext context,
  TimelineInterval interval,
) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => EventDetailSheet(interval: interval),
  );
}

class EventDetailSheet extends ConsumerStatefulWidget {
  final TimelineInterval interval;

  const EventDetailSheet({
    super.key,
    required this.interval,
  });

  @override
  ConsumerState<EventDetailSheet> createState() => _EventDetailSheetState();
}

class _EventDetailSheetState extends ConsumerState<EventDetailSheet> {
  static const List<MapEntry<String, String>> _categories = [
    MapEntry('meeting', '会议'),
    MapEntry('work', '工作'),
    MapEntry('learning', '学习'),
    MapEntry('meal', '用餐'),
    MapEntry('social', '社交'),
    MapEntry('life', '日常'),
  ];

  MemoryEventDetail? _detail;
  bool _loading = false;
  bool _editingTitle = false;
  late final TextEditingController _titleController;

  @override
  void initState() {
    super.initState();
    final initTitle = (widget.interval.payload?['title'] as String?) ?? '';
    _titleController = TextEditingController(text: initTitle);
    if (widget.interval.tier == TimelineTier.p2Activity) {
      _loadEventDetail();
    }
  }

  @override
  void dispose() {
    _titleController.dispose();
    super.dispose();
  }

  String get _canonicalEventId {
    final fromPayload = (widget.interval.payload?['event_id'] as String?)?.trim();
    if (fromPayload != null && fromPayload.isNotEmpty) {
      return fromPayload;
    }
    return widget.interval.intervalId.replaceFirst(RegExp(r'_part\d+$'), '');
  }

  Future<void> _loadEventDetail() async {
    final client = ref.read(apiClientProvider);
    if (client == null) return;
    setState(() => _loading = true);
    try {
      final detail = await client.fetchMemoryEventDetail(
        _canonicalEventId,
        tz: localUtcOffsetTimezone(ref.read(selectedDateProvider)),
      );
      if (!mounted) return;
      setState(() {
        _detail = detail;
        _titleController.text = detail.title;
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _savePatch({String? title, String? category}) async {
    final client = ref.read(apiClientProvider);
    if (client == null) return;
    try {
      final updated = await client.patchMemoryEvent(
        _canonicalEventId,
        title: title,
        category: category,
      );
      if (!mounted) return;
      setState(() {
        _detail = updated;
        _editingTitle = false;
      });
      ref.read(timelineRefreshTickProvider.notifier).state++;
    } catch (_) {}
  }

  Future<void> _promptAssignCluster(
    String clusterId,
    String currentLabel,
    String? previewAudioUrl,
  ) async {
    final speakers = ref.read(speakersProvider).valueOrNull ?? const <SpeakerProfile>[];
    final nameCtrl = TextEditingController();

    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: HemoryColors.darkSurface1,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20.0)),
      ),
      builder: (sheetCtx) {
        return Padding(
          padding: EdgeInsets.fromLTRB(
            20.0,
            16.0,
            20.0,
            MediaQuery.of(sheetCtx).viewInsets.bottom + 20.0,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      currentLabel,
                      style: const TextStyle(
                        fontSize: 16.0,
                        fontWeight: FontWeight.w600,
                        color: HemoryColors.textLightPrimary,
                      ),
                    ),
                  ),
                  IconButton(
                    icon: const Icon(
                      Icons.volume_up_rounded,
                      color: HemoryColors.sagePrimary,
                      size: 20.0,
                    ),
                    onPressed: () {
                      toggleAudioPreview(
                        ref,
                        previewAudioUrl ?? '/clusters/$clusterId/audio_preview',
                      );
                    },
                  ),
                ],
              ),
              const SizedBox(height: 10.0),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: nameCtrl,
                      autofocus: true,
                      style: const TextStyle(
                        color: HemoryColors.textLightPrimary,
                        fontSize: 14.0,
                      ),
                      decoration: InputDecoration(
                        hintText: '姓名',
                        hintStyle: const TextStyle(color: HemoryColors.textLightMuted),
                        filled: true,
                        fillColor: HemoryColors.darkSurface2,
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 12.0,
                          vertical: 10.0,
                        ),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(10.0),
                          borderSide: BorderSide.none,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8.0),
                  FilledButton(
                    style: FilledButton.styleFrom(
                      backgroundColor: HemoryColors.sagePrimary,
                      foregroundColor: Colors.black,
                    ),
                    onPressed: () async {
                      final name = nameCtrl.text.trim();
                      if (name.isEmpty) return;
                      final client = ref.read(apiClientProvider);
                      if (client == null) return;
                      await client.patchCluster(
                        clusterId: clusterId,
                        action: 'mark_new',
                        name: name,
                      );
                      ref.read(speakersRefreshTickProvider.notifier).state++;
                      ref.read(timelineRefreshTickProvider.notifier).state++;
                      if (sheetCtx.mounted) Navigator.of(sheetCtx).pop();
                      if (widget.interval.tier == TimelineTier.p2Activity) {
                        _loadEventDetail();
                      }
                    },
                    child: const Text('保存'),
                  ),
                ],
              ),
              if (speakers.isNotEmpty) ...[
                const SizedBox(height: 12.0),
                Wrap(
                  spacing: 8.0,
                  runSpacing: 8.0,
                  children: speakers.map((spk) {
                    return ActionChip(
                      backgroundColor: HemoryColors.darkSurface2,
                      side: const BorderSide(color: HemoryColors.darkBorder, width: 0.6),
                      label: Text(
                        spk.name,
                        style: const TextStyle(
                          fontSize: 12.0,
                          color: HemoryColors.textLightPrimary,
                        ),
                      ),
                      onPressed: () async {
                        final client = ref.read(apiClientProvider);
                        if (client == null) return;
                        await client.patchCluster(
                          clusterId: clusterId,
                          action: 'mark_existing',
                          speakerId: spk.speakerId,
                        );
                        ref.read(speakersRefreshTickProvider.notifier).state++;
                        ref.read(timelineRefreshTickProvider.notifier).state++;
                        if (sheetCtx.mounted) Navigator.of(sheetCtx).pop();
                        if (widget.interval.tier == TimelineTier.p2Activity) {
                          _loadEventDetail();
                        }
                      },
                    );
                  }).toList(),
                ),
              ],
            ],
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final activeAudioUrl = ref.watch(activeAudioUrlProvider);
    final client = ref.watch(apiClientProvider);
    final isActivity = widget.interval.tier == TimelineTier.p2Activity;

    return DraggableScrollableSheet(
      initialChildSize: 0.68,
      minChildSize: 0.42,
      maxChildSize: 0.92,
      expand: false,
      builder: (context, scrollController) {
        return Container(
          decoration: const BoxDecoration(
            color: HemoryColors.darkSurface1,
            borderRadius: BorderRadius.vertical(top: Radius.circular(22.0)),
            border: Border(
              top: BorderSide(color: HemoryColors.darkBorder, width: 0.8),
            ),
          ),
          child: Column(
            children: [
              const SizedBox(height: 10.0),
              Container(
                width: 36.0,
                height: 4.0,
                decoration: BoxDecoration(
                  color: HemoryColors.darkBorder,
                  borderRadius: BorderRadius.circular(2.0),
                ),
              ),
              Expanded(
                child: isActivity
                    ? _buildActivityBody(scrollController, activeAudioUrl, client)
                    : _buildScatteredBody(scrollController, activeAudioUrl, client),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildActivityBody(
    ScrollController scrollController,
    String? activeAudioUrl,
    HemoryApiClient? client,
  ) {
    final payload = widget.interval.payload ?? const {};
    final title = _detail?.title ?? (payload['title'] as String? ?? '对话记录');
    final summary = _detail?.summary ?? (payload['summary'] as String? ?? '');
    final category = _detail?.category ?? (payload['category'] as String? ?? '');
    final durationMs =
        _detail?.durationMs ?? ((payload['duration_ms'] as num?)?.toInt() ?? 0);
    final timeLabel = formatTimelineRangeLabel(
      startMin: widget.interval.startMs,
      endMin: widget.interval.endMs,
      durationMs: durationMs,
    );
    final utterances = _detail?.utterances ?? const <MemoryUtteranceDetail>[];

    return ListView(
      controller: scrollController,
      padding: const EdgeInsets.fromLTRB(20.0, 14.0, 20.0, 28.0),
      children: [
        Text(
          timeLabel,
          style: const TextStyle(
            fontSize: 12.0,
            fontWeight: FontWeight.w600,
            color: HemoryColors.textLightMuted,
            fontFeatures: [FontFeature.tabularFigures()],
          ),
        ),
        const SizedBox(height: 6.0),
        if (_editingTitle)
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _titleController,
                  autofocus: true,
                  style: const TextStyle(
                    fontSize: 18.0,
                    fontWeight: FontWeight.w600,
                    color: HemoryColors.textLightPrimary,
                  ),
                  onSubmitted: (val) {
                    if (val.trim().isNotEmpty) {
                      _savePatch(title: val.trim());
                    }
                  },
                ),
              ),
              IconButton(
                icon: const Icon(Icons.check_rounded, color: HemoryColors.sagePrimary),
                onPressed: () {
                  final val = _titleController.text.trim();
                  if (val.isNotEmpty) _savePatch(title: val);
                },
              ),
            ],
          )
        else
          GestureDetector(
            onTap: () => setState(() => _editingTitle = true),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    title,
                    style: const TextStyle(
                      fontSize: 19.0,
                      fontWeight: FontWeight.w700,
                      letterSpacing: -0.3,
                      color: HemoryColors.textLightPrimary,
                    ),
                  ),
                ),
                const Icon(
                  Icons.edit_outlined,
                  size: 16.0,
                  color: HemoryColors.textLightMuted,
                ),
              ],
            ),
          ),
        const SizedBox(height: 10.0),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            children: _categories.map((entry) {
              final selected = category == entry.key;
              final accent = ActivityCard.accentForCategory(entry.key);
              return Padding(
                padding: const EdgeInsets.only(right: 6.0),
                child: GestureDetector(
                  onTap: () => _savePatch(category: selected ? '' : entry.key),
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10.0,
                      vertical: 5.0,
                    ),
                    decoration: BoxDecoration(
                      color: selected
                          ? accent.withValues(alpha: 0.2)
                          : HemoryColors.darkSurface2,
                      borderRadius: BorderRadius.circular(8.0),
                      border: Border.all(
                        color: selected ? accent : HemoryColors.darkBorder,
                        width: 0.8,
                      ),
                    ),
                    child: Text(
                      entry.value,
                      style: TextStyle(
                        fontSize: 11.5,
                        fontWeight: FontWeight.w600,
                        color: selected ? accent : HemoryColors.textLightSecondary,
                      ),
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
        ),
        if (summary.trim().isNotEmpty && summary.trim() != title.trim()) ...[
          const SizedBox(height: 14.0),
          Container(
            padding: const EdgeInsets.all(12.0),
            decoration: BoxDecoration(
              color: HemoryColors.darkSurface2,
              borderRadius: BorderRadius.circular(12.0),
              border: Border.all(color: HemoryColors.darkBorder, width: 0.6),
            ),
            child: Text(
              summary.trim(),
              style: const TextStyle(
                fontSize: 13.5,
                height: 1.48,
                color: HemoryColors.textLightSecondary,
              ),
            ),
          ),
        ],
        const SizedBox(height: 18.0),
        if (_loading)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 24.0),
            child: Center(
              child: SizedBox(
                width: 22.0,
                height: 22.0,
                child: CircularProgressIndicator(
                  strokeWidth: 2.0,
                  color: HemoryColors.sagePrimary,
                ),
              ),
            ),
          )
        else
          ...utterances.map((u) {
            final fullAudio = (u.audioUrl != null && client != null)
                ? client.resolveUrl(u.audioUrl!)
                : null;
            final isPlaying = fullAudio != null && activeAudioUrl == fullAudio;
            return _buildUtteranceTile(
              speakerName: u.speakerName,
              time: u.time,
              content: u.content,
              clusterId: u.clusterId,
              isPendingCluster: u.isClusterPending,
              audioUrl: u.audioUrl,
              isPlaying: isPlaying,
            );
          }),
      ],
    );
  }

  Widget _buildScatteredBody(
    ScrollController scrollController,
    String? activeAudioUrl,
    HemoryApiClient? client,
  ) {
    final payload = widget.interval.payload ?? const {};
    final rawSnippets = (payload['snippets'] as List?) ?? const [];
    final pendingIds = ((payload['pending_cluster_ids'] as List?) ?? const [])
        .map((e) => e.toString())
        .toSet();
    final durationMs = (payload['duration_ms'] as num?)?.toInt() ?? 0;
    final timeLabel = formatTimelineRangeLabel(
      startMin: widget.interval.startMs,
      endMin: widget.interval.endMs,
      durationMs: durationMs,
    );

    return ListView(
      controller: scrollController,
      padding: const EdgeInsets.fromLTRB(20.0, 14.0, 20.0, 28.0),
      children: [
        Text(
          timeLabel,
          style: const TextStyle(
            fontSize: 12.0,
            fontWeight: FontWeight.w600,
            color: HemoryColors.textLightMuted,
            fontFeatures: [FontFeature.tabularFigures()],
          ),
        ),
        const SizedBox(height: 14.0),
        ...rawSnippets.whereType<Map>().map((raw) {
          final m = Map<String, dynamic>.from(raw);
          final cid = m['cluster_id'] as String?;
          final relAudio = m['audio_url'] as String?;
          final fullAudio = (relAudio != null && client != null)
              ? client.resolveUrl(relAudio)
              : null;
          final isPlaying = fullAudio != null && activeAudioUrl == fullAudio;
          return _buildUtteranceTile(
            speakerName: m['speaker'] as String? ?? 'Speaker 1',
            time: m['time'] as String? ?? '',
            content: m['text'] as String? ?? '',
            clusterId: cid,
            isPendingCluster: cid != null && pendingIds.contains(cid),
            audioUrl: relAudio,
            isPlaying: isPlaying,
          );
        }),
      ],
    );
  }

  Widget _buildUtteranceTile({
    required String speakerName,
    required String time,
    required String content,
    required String? clusterId,
    required bool isPendingCluster,
    required String? audioUrl,
    required bool isPlaying,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12.0),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12.0, vertical: 10.0),
        decoration: BoxDecoration(
          color: HemoryColors.darkBg,
          borderRadius: BorderRadius.circular(12.0),
          border: Border.all(
            color: isPlaying
                ? HemoryColors.sagePrimary
                : HemoryColors.darkBorder.withValues(alpha: 0.7),
            width: 0.8,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                GestureDetector(
                  onTap: clusterId != null
                      ? () => _promptAssignCluster(clusterId, speakerName, audioUrl)
                      : null,
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8.0,
                      vertical: 3.0,
                    ),
                    decoration: BoxDecoration(
                      color: isPendingCluster
                          ? const Color(0x26F59E0B)
                          : HemoryColors.darkSurface2,
                      borderRadius: BorderRadius.circular(6.0),
                      border: Border.all(
                        color: isPendingCluster
                            ? const Color(0x66F59E0B)
                            : HemoryColors.darkBorder,
                        width: 0.6,
                      ),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          speakerName,
                          style: TextStyle(
                            fontSize: 11.5,
                            fontWeight: FontWeight.w600,
                            color: isPendingCluster
                                ? const Color(0xFFFBBF24)
                                : HemoryColors.textLightPrimary,
                          ),
                        ),
                        if (clusterId != null) ...[
                          const SizedBox(width: 4.0),
                          Icon(
                            Icons.person_add_alt_1_rounded,
                            size: 12.0,
                            color: isPendingCluster
                                ? const Color(0xFFFBBF24)
                                : HemoryColors.textLightMuted,
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: 8.0),
                Text(
                  time,
                  style: const TextStyle(
                    fontSize: 11.0,
                    color: HemoryColors.textLightMuted,
                    fontFeatures: [FontFeature.tabularFigures()],
                  ),
                ),
                const Spacer(),
                if (audioUrl != null)
                  GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: () => toggleAudioPreview(ref, audioUrl),
                    child: Padding(
                      padding: const EdgeInsets.all(4.0),
                      child: Icon(
                        isPlaying
                            ? Icons.stop_circle_outlined
                            : Icons.play_circle_outline_rounded,
                        size: 20.0,
                        color: isPlaying
                            ? HemoryColors.liveCoral
                            : HemoryColors.sagePrimary,
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 6.0),
            Text(
              content,
              style: const TextStyle(
                fontSize: 14.0,
                height: 1.45,
                color: HemoryColors.textLightPrimary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
