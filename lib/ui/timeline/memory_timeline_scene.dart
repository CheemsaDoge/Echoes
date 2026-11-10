/// MemoryTimelineScene: Master viewport coordinating DateStrip, ModeSwitcher,
/// and IndexedStack hosting CalendarView and RiverView with zero state loss.
library;

import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import '../../core/models/timeline_models.dart';
import '../../core/services/recording_service.dart';
import '../theme/hemory_theme.dart';
import 'date_strip.dart';
import 'mode_switcher.dart';
import 'calendar_view.dart';
import 'river_view.dart';
import 'clipping_engine.dart';

class MemoryTimelineScene extends ConsumerStatefulWidget {
  final List<TimelineInterval>? initialIntervals;
  final List<TimelineCoverageSegment>? initialCoverage;

  const MemoryTimelineScene({
    super.key,
    this.initialIntervals,
    this.initialCoverage,
  });

  @override
  ConsumerState<MemoryTimelineScene> createState() => _MemoryTimelineSceneState();
}

class _MemoryTimelineSceneState extends ConsumerState<MemoryTimelineScene> {
  DateTime _selectedDate = DateTime.now();
  TimelineViewMode _viewMode = TimelineViewMode.calendar;

  // Local mock/cached intervals for presentation
  late List<TimelineInterval> _rawIntervals;
  late List<TimelineCoverageSegment> _coverageSegments;
  final List<double> _mockWaveform = List.generate(36, (i) => 0.15 + (i % 7) * 0.1);

  @override
  void initState() {
    super.initState();
    _rawIntervals = widget.initialIntervals ?? _buildSampleIntervals();
    _coverageSegments = widget.initialCoverage ?? _buildSampleCoverage();
  }

  List<TimelineInterval> _buildSampleIntervals() {
    return [
      TimelineInterval(
        intervalId: 'act_01',
        startMs: 9 * 60 + 30, // 09:30
        endMs: 10 * 60 + 15,  // 10:15
        tier: TimelineTier.p2Activity,
        itemType: 'Activity',
        payload: {
          'title': 'Sprint Planning & Architecture Review',
          'summary': 'Reviewed 5-tier interval clipping engine and Riverpod state machine.',
          'speakers': ['Dr. Sarah Lin', 'Marcus'],
          'tags': ['planning', 'architecture'],
        },
      ),
      TimelineInterval(
        intervalId: 'chat_01',
        startMs: 11 * 60 + 0, // 11:00
        endMs: 11 * 60 + 25, // 11:25
        tier: TimelineTier.p3ScatteredChat,
        itemType: 'ScatteredChatGroup',
        payload: {
          'snippets': [
            {'speaker': 'Alice', 'text': 'Can we deploy the M5 release to dogfood?', 'time': '11:05'},
            {'speaker': 'Bob', 'text': 'Yes, all unit and E2E suites are passing.', 'time': '11:06'},
          ],
        },
      ),
      TimelineInterval(
        intervalId: 'chain_01',
        startMs: 14 * 60 + 0, // 14:00
        endMs: 14 * 60 + 40, // 14:40
        tier: TimelineTier.p1AnalyzingChain,
        itemType: 'AnalyzingChainCard',
        payload: {
          'progress': 0.72,
          'current_phase': 'diarizing',
        },
      ),
    ];
  }

  List<TimelineCoverageSegment> _buildSampleCoverage() {
    return [
      const TimelineCoverageSegment(startMinute: 9 * 60 + 30, endMinute: 10 * 60 + 15),
      const TimelineCoverageSegment(startMinute: 11 * 60, endMinute: 11 * 60 + 25),
      const TimelineCoverageSegment(startMinute: 14 * 60, endMinute: 14 * 60 + 40),
    ];
  }

  void _onDateSelected(DateTime date) {
    setState(() {
      _selectedDate = date;
    });
  }

  void _onModeChanged(TimelineViewMode mode) {
    setState(() {
      _viewMode = mode;
    });
  }

  @override
  Widget build(BuildContext context) {
    final recordingState = ref.watch(recordingServiceProvider);
    final recordingService = ref.read(recordingServiceProvider.notifier);

    // Apply Contract C5 clipping engine to candidate intervals
    var intervalsToDisplay = List<TimelineInterval>.from(_rawIntervals);

    // If live recording is active, inject P0 LiveCard interval
    if (recordingState.isRecording) {
      final now = DateTime.now();
      final currentMinute = now.hour * 60 + now.minute;
      intervalsToDisplay.add(
        TimelineInterval(
          intervalId: recordingState.activeRecordingId ?? 'live_card',
          startMs: (currentMinute - 15).clamp(0, 1440),
          endMs: currentMinute.clamp(0, 1440),
          tier: TimelineTier.p0LiveCard,
          itemType: 'TimelineLiveCard',
        ),
      );
    }

    // Execute 5-Tier Priority Clipping
    final clippedIntervals = TimelineClippingEngine.clipTimelineIntervals(intervalsToDisplay);
    final finalIntervals = TimelineClippingEngine.fillCalendarIdleSegments(
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
                // Frosted Top Header
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
                      padding: const EdgeInsets.symmetric(vertical: 8.0),
                      child: Column(
                        children: [
                          // Top Controls: Date Strip
                          DateStrip(
                            selectedDate: _selectedDate,
                            onDateSelected: _onDateSelected,
                            onPrefetchRequested: (centerDate) {
                              // Dual-range [-7, +1] prefetch trigger
                            },
                          ),
                          const SizedBox(height: 8.0),
                          // View Mode Switcher
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 16.0),
                            child: Row(
                              mainAxisAlignment: MainAxisAlignment.spaceBetween,
                              children: [
                                Text(
                                  DateFormat('yyyy').format(_selectedDate),
                                  style: const TextStyle(
                                    fontSize: 12.0,
                                    fontWeight: FontWeight.w600,
                                    color: HemoryColors.textLightMuted,
                                    fontFeatures: [FontFeature.tabularFigures()],
                                  ),
                                ),
                                ModeSwitcher(
                                  activeMode: _viewMode,
                                  onModeChanged: _onModeChanged,
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                // Viewport: IndexedStack preserving state across Calendar & River toggles
                Expanded(
                  child: IndexedStack(
                    index: _viewMode.isCalendar ? 0 : 1,
                    children: [
                      CalendarView(
                        selectedDate: _selectedDate,
                        intervals: finalIntervals,
                        coverageSegments: _coverageSegments,
                        liveMeteringSamples: _mockWaveform,
                        liveElapsedMs: recordingState.elapsedMs,
                        isLiveRecording: recordingState.isRecording,
                        isLivePaused: recordingState.isPaused,
                        onLiveStopPressed: () => recordingService.stopRecording(),
                        onLivePauseTogglePressed: () {
                          if (recordingState.isPaused) {
                            recordingService.resumeRecording();
                          } else {
                            recordingService.pauseRecording();
                          }
                        },
                      ),
                      RiverView(
                        selectedDate: _selectedDate,
                        intervals: clippedIntervals,
                        liveMeteringSamples: _mockWaveform,
                        liveElapsedMs: recordingState.elapsedMs,
                        isLiveRecording: recordingState.isRecording,
                        isLivePaused: recordingState.isPaused,
                        onLiveStopPressed: () => recordingService.stopRecording(),
                        onLivePauseTogglePressed: () {
                          if (recordingState.isPaused) {
                            recordingService.resumeRecording();
                          } else {
                            recordingService.pauseRecording();
                          }
                        },
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
}
