/// MemoryTimelineScene: Master viewport coordinating DateStrip, ModeSwitcher,
/// live capture controls, real server timeline sync, and Calendar/River views.
library;

import 'dart:async';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import '../../core/ipc/event_channels.dart';
import '../../core/models/timeline_models.dart';
import '../../core/providers/app_providers.dart';
import '../../core/services/hemory_api_client.dart';
import '../theme/hemory_theme.dart';
import 'calendar_view.dart';
import 'clipping_engine.dart';
import 'date_strip.dart';
import 'event_detail_sheet.dart';
import 'mode_switcher.dart';
import 'river_view.dart';

class MemoryTimelineScene extends ConsumerStatefulWidget {
  final List<TimelineInterval>? initialIntervals;
  final List<TimelineCoverageSegment>? initialCoverage;

  const MemoryTimelineScene({
    super.key,
    this.initialIntervals,
    this.initialCoverage,
  });

  @override
  ConsumerState<MemoryTimelineScene> createState() =>
      _MemoryTimelineSceneState();
}

class _MemoryTimelineSceneState extends ConsumerState<MemoryTimelineScene> {
  TimelineViewMode _viewMode = TimelineViewMode.river;
  StreamSubscription<SegmentCompletedEvent>? _segmentSub;
  Timer? _mutationPollTimer;
  int _lastMutationCursor = 0;

  @override
  void initState() {
    super.initState();
    if (widget.initialIntervals == null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _attachLiveSync();
      });
    }
  }

  void _attachLiveSync() {
    if (!mounted) return;
    final streams = ref.read(recordingEventStreamsProvider);
    _segmentSub = streams.segmentsStream.listen((_) {
      if (!mounted) return;
      Future.delayed(const Duration(seconds: 2), () {
        if (mounted) {
          ref.read(timelineRefreshTickProvider.notifier).state++;
        }
      });
    });

    _mutationPollTimer = Timer.periodic(const Duration(seconds: 6), (_) async {
      if (!mounted) return;
      final client = ref.read(apiClientProvider);
      if (client == null) return;
      try {
        final next = await client.fetchLatestMutationCursor(
          after: _lastMutationCursor,
        );
        if (!mounted) return;
        if (_lastMutationCursor == 0) {
          _lastMutationCursor = next;
        } else if (next > _lastMutationCursor) {
          _lastMutationCursor = next;
          ref.read(timelineRefreshTickProvider.notifier).state++;
          ref.read(speakersRefreshTickProvider.notifier).state++;
        }
      } catch (_) {}
    });
  }

  @override
  void dispose() {
    _segmentSub?.cancel();
    _mutationPollTimer?.cancel();
    super.dispose();
  }

  Future<void> _pickDate(DateTime current) async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: current,
      firstDate: DateTime(2024, 1, 1),
      lastDate: now.add(const Duration(days: 7)),
      builder: (context, child) {
        return Theme(
          data: HemoryTheme.darkTheme,
          child: child!,
        );
      },
    );
    if (picked != null && mounted) {
      ref.read(selectedDateProvider.notifier).state =
          DateTime(picked.year, picked.month, picked.day);
    }
  }

  Future<void> _refreshTimeline() async {
    ref.read(timelineRefreshTickProvider.notifier).state++;
    await ref
        .read(timelineDayProvider.future)
        .catchError((_) => TimelineDayPayload.empty);
  }

  String _formatBriefElapsed(int ms) {
    final s = ms ~/ 1000;
    final m = (s ~/ 60).toString().padLeft(2, '0');
    final r = (s % 60).toString().padLeft(2, '0');
    return '$m:$r';
  }

  @override
  Widget build(BuildContext context) {
    final selectedDate = ref.watch(selectedDateProvider);
    final recordingState = ref.watch(recordingServiceProvider);
    final recordingController = ref.read(recordingServiceProvider.notifier);
    final liveWaveform = ref.watch(liveMeteringSamplesProvider);
    final auth = ref.watch(authSessionProvider);

    final dayAsync = widget.initialIntervals != null
        ? null
        : ref.watch(timelineDayProvider);
    final dayPayload = dayAsync?.valueOrNull;

    final rawIntervals =
        widget.initialIntervals ?? dayPayload?.intervals ?? const <TimelineInterval>[];
    final coverageSegments =
        widget.initialCoverage ?? dayPayload?.coverage ?? const <TimelineCoverageSegment>[];
    final densityMap = dayPayload?.density ?? const <String, int>{};

    final now = DateTime.now();
    final isToday = DateUtils.isSameDay(selectedDate, now);

    final intervalsToDisplay = List<TimelineInterval>.from(rawIntervals);
    if (recordingState.isActive && isToday) {
      final currentMinute = (now.hour * 60 + now.minute).clamp(0, 1440);
      final elapsedMinutes = (recordingState.elapsedMs ~/ 60000).clamp(1, 180);
      final startMinute = (currentMinute - elapsedMinutes).clamp(0, currentMinute);
      intervalsToDisplay.add(
        TimelineInterval(
          intervalId: recordingState.activeRecordingId ?? 'live_card',
          startMs: startMinute,
          endMs: currentMinute == startMinute
              ? (startMinute + 1).clamp(1, 1440)
              : currentMinute,
          tier: TimelineTier.p0LiveCard,
          itemType: 'TimelineLiveCard',
        ),
      );
    }

    final clippedIntervals =
        TimelineClippingEngine.clipTimelineIntervals(intervalsToDisplay);
    final finalCalendarIntervals = TimelineClippingEngine.fillCalendarIdleSegments(
      placedIntervals: clippedIntervals,
      dayStartMs: 0,
      dayEndMs: 1440,
    );

    return Scaffold(
      backgroundColor: HemoryColors.darkBg,
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: HemoryTheme.maxWidth),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                ClipRect(
                  child: BackdropFilter(
                    filter: ImageFilter.blur(sigmaX: 18.0, sigmaY: 18.0),
                    child: Container(
                      decoration: const BoxDecoration(
                        color: HemoryColors.darkGlassTint,
                        border: Border(
                          bottom: BorderSide(
                            color: HemoryColors.darkBorder,
                            width: 0.8,
                          ),
                        ),
                      ),
                      padding: const EdgeInsets.only(top: 8.0, bottom: 10.0),
                      child: Column(
                        children: [
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 16.0),
                            child: Row(
                              children: [
                                GestureDetector(
                                  behavior: HitTestBehavior.opaque,
                                  onTap: () => _pickDate(selectedDate),
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Text(
                                        DateFormat('yyyy / MM').format(selectedDate),
                                        style: const TextStyle(
                                          fontSize: 15.0,
                                          fontWeight: FontWeight.w700,
                                          letterSpacing: -0.2,
                                          color: HemoryColors.textLightPrimary,
                                          fontFeatures: [FontFeature.tabularFigures()],
                                        ),
                                      ),
                                      const SizedBox(width: 2.0),
                                      const Icon(
                                        Icons.chevron_right_rounded,
                                        size: 18.0,
                                        color: HemoryColors.textLightMuted,
                                      ),
                                    ],
                                  ),
                                ),
                                if (!isToday) ...[
                                  const SizedBox(width: 8.0),
                                  GestureDetector(
                                    onTap: () {
                                      final t = DateTime.now();
                                      ref.read(selectedDateProvider.notifier).state =
                                          DateTime(t.year, t.month, t.day);
                                    },
                                    child: Container(
                                      padding: const EdgeInsets.symmetric(
                                        horizontal: 8.0,
                                        vertical: 3.0,
                                      ),
                                      decoration: BoxDecoration(
                                        color: HemoryColors.darkSurface2,
                                        borderRadius: BorderRadius.circular(10.0),
                                        border: Border.all(
                                          color: HemoryColors.darkBorder,
                                          width: 0.6,
                                        ),
                                      ),
                                      child: const Text(
                                        '今天',
                                        style: TextStyle(
                                          fontSize: 11.0,
                                          fontWeight: FontWeight.w600,
                                          color: HemoryColors.sageSecondary,
                                        ),
                                      ),
                                    ),
                                  ),
                                ],
                                const Spacer(),
                                _buildCapturePill(
                                  isActive: recordingState.isActive,
                                  elapsedMs: recordingState.elapsedMs,
                                  onTap: () {
                                    if (recordingState.isActive) {
                                      recordingController.stopRecording();
                                    } else {
                                      recordingController.startRecording(
                                        tenantId: auth.tenantId,
                                        vaultId: auth.vaultId,
                                      );
                                    }
                                  },
                                ),
                                const SizedBox(width: 8.0),
                                ModeSwitcher(
                                  activeMode: _viewMode,
                                  onModeChanged: (mode) {
                                    setState(() => _viewMode = mode);
                                  },
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(height: 8.0),
                          DateStrip(
                            selectedDate: selectedDate,
                            activityDensityMap: densityMap,
                            onDateSelected: (d) {
                              ref.read(selectedDateProvider.notifier).state =
                                  DateTime(d.year, d.month, d.day);
                            },
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                if (recordingState.lastError != null)
                  Container(
                    color: const Color(0x26FF4D4D),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16.0,
                      vertical: 6.0,
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            recordingState.lastError!,
                            style: const TextStyle(
                              fontSize: 12.0,
                              color: HemoryColors.liveCoral,
                            ),
                          ),
                        ),
                        GestureDetector(
                          onTap: recordingController.reset,
                          child: const Icon(
                            Icons.close_rounded,
                            size: 16.0,
                            color: HemoryColors.liveCoral,
                          ),
                        ),
                      ],
                    ),
                  ),
                if (dayAsync != null && dayAsync.isLoading && dayPayload == null)
                  const LinearProgressIndicator(
                    minHeight: 1.5,
                    backgroundColor: Colors.transparent,
                    color: HemoryColors.sagePrimary,
                  ),
                Expanded(
                  child: IndexedStack(
                    index: _viewMode.isCalendar ? 0 : 1,
                    children: [
                      CalendarView(
                        selectedDate: selectedDate,
                        intervals: finalCalendarIntervals,
                        coverageSegments: coverageSegments,
                        liveMeteringSamples: liveWaveform,
                        liveElapsedMs: recordingState.elapsedMs,
                        isLiveRecording: recordingState.isActive,
                        isLivePaused: recordingState.isPaused,
                        onLiveStopPressed: () =>
                            recordingController.stopRecording(),
                        onLivePauseTogglePressed: () {
                          if (recordingState.isPaused) {
                            recordingController.resumeRecording();
                          } else {
                            recordingController.pauseRecording();
                          }
                        },
                        onCardTapped: (interval) =>
                            showTimelineDetailSheet(context, interval),
                        onRefresh: widget.initialIntervals == null
                            ? _refreshTimeline
                            : null,
                      ),
                      RiverView(
                        selectedDate: selectedDate,
                        intervals: clippedIntervals,
                        liveMeteringSamples: liveWaveform,
                        liveElapsedMs: recordingState.elapsedMs,
                        isLiveRecording: recordingState.isActive,
                        isLivePaused: recordingState.isPaused,
                        onLiveStopPressed: () =>
                            recordingController.stopRecording(),
                        onLivePauseTogglePressed: () {
                          if (recordingState.isPaused) {
                            recordingController.resumeRecording();
                          } else {
                            recordingController.pauseRecording();
                          }
                        },
                        onCardTapped: (interval) =>
                            showTimelineDetailSheet(context, interval),
                        onRefresh: widget.initialIntervals == null
                            ? _refreshTimeline
                            : null,
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildCapturePill({
    required bool isActive,
    required int elapsedMs,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Container(
        height: 32.0,
        padding: const EdgeInsets.symmetric(horizontal: 10.0),
        decoration: BoxDecoration(
          color: isActive
              ? HemoryColors.liveCoral.withValues(alpha: 0.16)
              : HemoryColors.sagePrimary.withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(16.0),
          border: Border.all(
            color: isActive
                ? HemoryColors.liveCoral.withValues(alpha: 0.55)
                : HemoryColors.sagePrimary.withValues(alpha: 0.55),
            width: 0.8,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 7.5,
              height: 7.5,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: isActive
                    ? HemoryColors.liveCoral
                    : HemoryColors.sagePrimary,
              ),
            ),
            const SizedBox(width: 6.0),
            Text(
              isActive ? _formatBriefElapsed(elapsedMs) : '录音',
              style: TextStyle(
                fontSize: 12.0,
                fontWeight: FontWeight.w600,
                color: isActive
                    ? HemoryColors.liveCoral
                    : HemoryColors.sageSecondary,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
