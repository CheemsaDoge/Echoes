import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:hemory/core/ipc/event_channels.dart';
import 'package:hemory/core/ipc/native_bridge.dart';
import 'package:hemory/core/models/recording_state.dart';
import 'package:hemory/core/models/timeline_models.dart';
import 'package:hemory/core/providers/app_providers.dart';
import 'package:hemory/core/services/hemory_api_client.dart';
import 'package:hemory/core/services/sse_chat_service.dart';
import 'package:hemory/ui/cards/activity_card.dart';
import 'package:hemory/ui/cards/scattered_chat_card.dart';
import 'package:hemory/ui/timeline/event_detail_sheet.dart';
import 'package:hemory/ui/timeline/memory_timeline_scene.dart';

void main() {
  test('HemoryApiClient.parseTimelineDayJson strips UNKNOWN, deduplicates summary, and sorts ascending', () {
    final payload = HemoryApiClient.parseTimelineDayJson({
      'date': '2026-11-10',
      'timezone': 'Asia/Shanghai',
      'chains': [
        {
          'chain_id': 'chn_late',
          'current_step': 'diarizing',
          'progress_pct': 65.0,
          'start_min': 22 * 60 + 5,
          'end_min': 22 * 60 + 10,
        },
      ],
      'activities': [
        {
          'event_id': 'ev_2126',
          'title': '工作交流：排期评审',
          'summary': '工作交流：排期评审', // duplicate of title -> must be stripped
          'category': 'UNKNOWN', // must become ''
          'duration_ms': 42000,
          'start_min': 21 * 60 + 26,
          'end_min': 21 * 60 + 27,
          'speaker_names': ['张工'],
          'keywords': ['UNKNOWN', '排期'],
          'pending_cluster_ids': [],
        },
        {
          'event_id': 'ev_0930',
          'title': '项目晨会',
          'summary': '确认本周服务端流水线与客户端时间线改造方案。',
          'category': 'meeting',
          'duration_ms': 900000,
          'start_min': 9 * 60 + 30,
          'end_min': 9 * 60 + 45,
          'speaker_names': ['陈林', '王明'],
          'keywords': ['会议'],
          'pending_cluster_ids': ['clu_1'],
        },
      ],
      'scattered_chats': [
        {
          'group_id': 'scat_2113',
          'duration_ms': 6000,
          'start_min': 21 * 60 + 13,
          'end_min': 21 * 60 + 14,
          'pending_cluster_ids': [],
          'snippets': [
            {
              'speaker': 'Speaker 1',
              'text': '大姐，打火机呢？',
              'time': '21:13',
            },
            {
              'speaker': 'Speaker 2',
              'text': '在茶几抽屉里。',
              'time': '21:14',
            },
          ],
        },
      ],
      'coverage': [
        {'start_min': 570, 'end_min': 585},
      ],
      'density': {'2026-11-10': 3},
    });

    // Strictly ascending order: 09:30 -> 21:13 -> 21:26 -> 22:05
    expect(
      payload.intervals.map((e) => e.intervalId).toList(),
      equals(['ev_0930', 'scat_2113', 'ev_2126', 'chn_late']),
    );

    final ev2126 = payload.intervals[2].payload!;
    expect(ev2126['category'], equals(''));
    expect(ev2126['summary'], equals(''));
    expect(ev2126['tags'], equals(['排期']));
  });

  test('NativeBridgeChannel.getAuth handles untyped Map<Object?, Object?> from StandardMethodCodec', () async {
    final bridge = NativeBridgeChannel(
      invoker: (method, [args]) async {
        if (method == 'getAuth') {
          return <Object?, Object?>{
            'accessToken': 'tok_real_123456',
            'refreshToken': 'tok_real_123456',
            'expiresAt': 1900000000,
            'baseUrl': 'https://record.xmdoge.com',
            'slotConnectionType': 'direct',
            'slotDpUnitId': 'dp_default',
            'slotTenantId': 'ten_default',
            'slotVaultId': 'vlt_default',
          };
        }
        return null;
      },
    );

    final auth = await bridge.getAuth();
    expect(auth, isNotNull);
    expect(auth!.baseUrl, equals('https://record.xmdoge.com'));
    expect(await bridge.getStoredVaultId(), equals('vlt_default'));
  });

  testWidgets('RiverView and CalendarView render dense evening items strictly ascending with zero overlap or UNKNOWN', (tester) async {
    final intervals = [
      TimelineInterval(
        intervalId: 'ev_2126',
        startMs: 21 * 60 + 26,
        endMs: 21 * 60 + 27,
        tier: TimelineTier.p2Activity,
        itemType: 'Activity',
        payload: {
          'title': '晚间工作沟通',
          'summary': '晚间工作沟通', // duplicate -> should not render twice
          'category': '',
          'duration_ms': 8000,
          'speakers': ['李华'],
          'tags': <String>[],
          'pending_cluster_ids': <String>[],
        },
      ),
      TimelineInterval(
        intervalId: 'scat_2113',
        startMs: 21 * 60 + 13,
        endMs: 21 * 60 + 14,
        tier: TimelineTier.p3ScatteredChat,
        itemType: 'ScatteredChatGroup',
        payload: {
          'duration_ms': 5000,
          'snippets': [
            {'speaker': 'Speaker 1', 'text': '打火机放哪了', 'time': '21:13'},
            {'speaker': 'Speaker 2', 'text': '桌角上', 'time': '21:14'},
          ],
        },
      ),
      TimelineInterval(
        intervalId: 'ev_2100',
        startMs: 21 * 60,
        endMs: 21 * 60 + 5,
        tier: TimelineTier.p2Activity,
        itemType: 'Activity',
        payload: {
          'title': '接口联调确认',
          'summary': '核对分段上传租约头与声纹预览接口。',
          'category': 'work',
          'duration_ms': 300000,
          'speakers': ['张敏'],
          'tags': ['联调'],
          'pending_cluster_ids': <String>[],
        },
      ),
    ];

    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          home: MemoryTimelineScene(
            initialIntervals: intervals,
            initialCoverage: const [
              TimelineCoverageSegment(startMinute: 1260, endMinute: 1290),
            ],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    // Default RiverView: no UNKNOWN text anywhere, duplicate summary rendered only once
    expect(find.text('UNKNOWN'), findsNothing);
    expect(find.text('晚间工作沟通'), findsOneWidget);
    expect(find.text('21:26 · 8s'), findsOneWidget);

    // Verify ascending vertical order in RiverView: 21:00 -> 21:13 -> 21:26
    final y2100 = tester.getTopLeft(find.text('接口联调确认')).dy;
    final y2113 = tester.getTopLeft(find.text('打火机放哪了')).dy;
    final y2126 = tester.getTopLeft(find.text('晚间工作沟通')).dy;
    expect(y2100, lessThan(y2113));
    expect(y2113, lessThan(y2126));

    // Switch to CalendarView and verify non-overlapping ascending geometry
    await tester.tap(find.text('Calendar'));
    await tester.pumpAndSettle();

    final rectFirst = tester.getRect(find.byType(ActivityCard).first);
    final rectScattered = tester.getRect(find.byType(ScatteredChatConvoCard).first);
    final rectLast = tester.getRect(find.byType(ActivityCard).last);

    expect(rectFirst.bottom, lessThanOrEqualTo(rectScattered.top));
    expect(rectScattered.bottom, lessThanOrEqualTo(rectLast.top));
  });

  test('RecordingController hydrates underlying RecordingService and allows stopRecording + integer retryFailedUploads', () async {
    var stopCalled = false;
    final bridge = NativeBridgeChannel(
      invoker: (method, [args]) async {
        switch (method) {
          case 'getRecordingState':
            return 'recording';
          case 'stopRecording':
            stopCalled = true;
            return true;
          case 'retryFailedUploads':
            return 3; // Android NativeBridgePlugin returns Int count
          default:
            return null;
        }
      },
    );
    final streams = RecordingEventStreams(
      stateStream: const Stream<RecordingStateSnapshot>.empty(),
      eventsStream: const Stream<RecordingLifecycleEvent>.empty(),
      meteringStream: const Stream<MeteringSnapshot>.empty(),
      segmentsStream: const Stream<SegmentCompletedEvent>.empty(),
    );

    final controller = RecordingController(bridge: bridge, streams: streams);
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(controller.state.captureState, equals(RecordingCaptureState.recording));

    final stopped = await controller.stopRecording();
    expect(stopped, isTrue);
    expect(stopCalled, isTrue);
    expect(controller.state.captureState, equals(RecordingCaptureState.stopping));

    expect(await bridge.retryFailedUploads(), isTrue);
    controller.dispose();
  });

  test('SseChatService omits x-user-id by default and surfaces non-200 HTTP errors as error events', () async {
    Map<String, String>? capturedHeaders;
    final mockHttp = MockClient((request) async {
      capturedHeaders = request.headers;
      return http.Response(
        jsonEncode({'detail': 'Header x-user-id mismatch'}),
        403,
        headers: {'content-type': 'application/json'},
      );
    });

    final service = SseChatService(baseUrl: 'https://record.xmdoge.com', client: mockHttp);
    final events = await service
        .sendChatTurn(
          threadId: 'th_1',
          prompt: '昨天聊了什么',
          authToken: 'tok_binding',
        )
        .toList();

    expect(capturedHeaders, isNotNull);
    expect(capturedHeaders!.containsKey('x-user-id'), isFalse);
    expect(events, hasLength(1));
    expect(events.first.event, equals('error'));
    expect(events.first.data['message'], contains('403'));
    service.dispose();
  });

  testWidgets('EventDetailSheet resolves clipped _part0 intervalId back to canonical payload event_id', (tester) async {
    String? requestedPath;
    final mockHttp = MockClient((request) async {
      requestedPath = request.url.path;
      return http.Response(
        jsonEncode({
          'event_id': 'ev_real_99',
          'title': '跨时段裁剪事件',
          'summary': '验证裁剪后缀不会导致404',
          'category': 'work',
          'start_time': '21:00',
          'end_time': '21:15',
          'duration_ms': 900000,
          'utterances': [],
        }),
        200,
        headers: {'content-type': 'application/json; charset=utf-8'},
      );
    });
    final apiClient = HemoryApiClient(
      baseUrl: 'https://record.xmdoge.com',
      authToken: 'tok_test',
      client: mockHttp,
    );
    addTearDown(apiClient.dispose);

    final clippedInterval = TimelineInterval(
      intervalId: 'ev_real_99_part0',
      startMs: 1260,
      endMs: 1270,
      tier: TimelineTier.p2Activity,
      itemType: 'Activity',
      payload: {
        'event_id': 'ev_real_99',
        'title': '跨时段裁剪事件',
      },
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          apiClientProvider.overrideWithValue(apiClient),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: EventDetailSheet(interval: clippedInterval),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(requestedPath, equals('/memory-events/ev_real_99'));
    expect(find.text('验证裁剪后缀不会导致404'), findsOneWidget);
  });
}
