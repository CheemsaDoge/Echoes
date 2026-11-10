/// Typed HTTP client for the dedicated Hemory server (`https://record.xmdoge.com`).
library;

import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/speaker_models.dart';
import '../models/timeline_models.dart';

String localUtcOffsetTimezone([DateTime? reference]) {
  final offset = (reference ?? DateTime.now()).timeZoneOffset;
  if (offset.inMinutes == 0) return 'UTC';
  final sign = offset.isNegative ? '-' : '+';
  final totalMin = offset.inMinutes.abs();
  final hh = (totalMin ~/ 60).toString().padLeft(2, '0');
  final mm = (totalMin % 60).toString().padLeft(2, '0');
  return 'UTC$sign$hh:$mm';
}

class HemoryApiException implements Exception {
  final int statusCode;
  final String message;

  const HemoryApiException(this.statusCode, this.message);

  @override
  String toString() => 'HTTP $statusCode: $message';
}

class VoiceprintClusterItem {
  final String clusterId;
  final String status;
  final String? speakerId;
  final String? audioSampleUrl;
  final double confidenceScore;
  final int occurrencesCount;
  final String createdAt;
  final String updatedAt;

  const VoiceprintClusterItem({
    required this.clusterId,
    required this.status,
    this.speakerId,
    this.audioSampleUrl,
    required this.confidenceScore,
    required this.occurrencesCount,
    required this.createdAt,
    required this.updatedAt,
  });

  factory VoiceprintClusterItem.fromJson(Map<String, dynamic> json) {
    return VoiceprintClusterItem(
      clusterId: json['cluster_id'] as String? ?? '',
      status: json['status'] as String? ?? 'pending',
      speakerId: json['speaker_id'] as String?,
      audioSampleUrl: json['audio_sample_url'] as String?,
      confidenceScore: (json['confidence_score'] as num?)?.toDouble() ?? 0.0,
      occurrencesCount: (json['occurrences_count'] as num?)?.toInt() ?? 1,
      createdAt: json['created_at'] as String? ?? '',
      updatedAt: json['updated_at'] as String? ?? '',
    );
  }
}

class TimelineDayPayload {
  final String date;
  final String timezone;
  final List<TimelineInterval> intervals;
  final List<TimelineCoverageSegment> coverage;
  final Map<String, int> density;
  final bool hasActiveChains;

  const TimelineDayPayload({
    required this.date,
    required this.timezone,
    required this.intervals,
    required this.coverage,
    required this.density,
    required this.hasActiveChains,
  });

  static const TimelineDayPayload empty = TimelineDayPayload(
    date: '',
    timezone: 'Asia/Shanghai',
    intervals: [],
    coverage: [],
    density: {},
    hasActiveChains: false,
  );
}

class MemoryUtteranceDetail {
  final String messageId;
  final String? speakerId;
  final String? clusterId;
  final bool isClusterPending;
  final String speakerName;
  final String content;
  final int startEpochMs;
  final int endEpochMs;
  final String time;
  final String? segmentId;
  final String? audioUrl;

  const MemoryUtteranceDetail({
    required this.messageId,
    this.speakerId,
    this.clusterId,
    required this.isClusterPending,
    required this.speakerName,
    required this.content,
    required this.startEpochMs,
    required this.endEpochMs,
    required this.time,
    this.segmentId,
    this.audioUrl,
  });

  factory MemoryUtteranceDetail.fromJson(Map<String, dynamic> json) {
    return MemoryUtteranceDetail(
      messageId: json['message_id'] as String? ?? '',
      speakerId: json['speaker_id'] as String?,
      clusterId: json['cluster_id'] as String?,
      isClusterPending: json['is_cluster_pending'] == true,
      speakerName: json['speaker_name'] as String? ?? 'Speaker 1',
      content: json['content'] as String? ?? '',
      startEpochMs: (json['start_epoch_ms'] as num?)?.toInt() ?? 0,
      endEpochMs: (json['end_epoch_ms'] as num?)?.toInt() ?? 0,
      time: json['time'] as String? ?? '',
      segmentId: json['segment_id'] as String?,
      audioUrl: json['audio_url'] as String?,
    );
  }
}

class MemoryEventDetail {
  final String eventId;
  final String title;
  final String summary;
  final String category;
  final int startEpochMs;
  final int endEpochMs;
  final int durationMs;
  final List<String> speakerNames;
  final List<String> keywords;
  final List<MemoryUtteranceDetail> utterances;

  const MemoryEventDetail({
    required this.eventId,
    required this.title,
    required this.summary,
    required this.category,
    required this.startEpochMs,
    required this.endEpochMs,
    required this.durationMs,
    required this.speakerNames,
    required this.keywords,
    required this.utterances,
  });

  factory MemoryEventDetail.fromJson(Map<String, dynamic> json) {
    final utts = ((json['utterances'] as List?) ?? const [])
        .whereType<Map>()
        .map((m) => MemoryUtteranceDetail.fromJson(Map<String, dynamic>.from(m)))
        .toList();
    return MemoryEventDetail(
      eventId: json['event_id'] as String? ?? '',
      title: json['title'] as String? ?? '',
      summary: json['summary'] as String? ?? '',
      category: json['category'] as String? ?? '',
      startEpochMs: (json['start_epoch_ms'] as num?)?.toInt() ?? 0,
      endEpochMs: (json['end_epoch_ms'] as num?)?.toInt() ?? 0,
      durationMs: (json['duration_ms'] as num?)?.toInt() ?? 0,
      speakerNames: ((json['speaker_names'] as List?) ?? const [])
          .map((e) => e.toString())
          .where((s) => s.isNotEmpty)
          .toList(),
      keywords: ((json['keywords'] as List?) ?? const [])
          .map((e) => e.toString())
          .where((s) => s.isNotEmpty)
          .toList(),
      utterances: utts,
    );
  }
}

class ChatThreadSummary {
  final String threadId;
  final String title;
  final String updatedAt;

  const ChatThreadSummary({
    required this.threadId,
    required this.title,
    required this.updatedAt,
  });

  factory ChatThreadSummary.fromJson(Map<String, dynamic> json) {
    return ChatThreadSummary(
      threadId: json['thread_id'] as String? ?? '',
      title: (json['title'] as String?)?.trim().isNotEmpty == true
          ? (json['title'] as String).trim()
          : '对话',
      updatedAt: json['updated_at'] as String? ?? '',
    );
  }
}

class ChatTurnRecord {
  final String turnId;
  final int turnIndex;
  final String prompt;
  final String responseText;
  final String status;

  const ChatTurnRecord({
    required this.turnId,
    required this.turnIndex,
    required this.prompt,
    required this.responseText,
    required this.status,
  });

  factory ChatTurnRecord.fromJson(Map<String, dynamic> json) {
    return ChatTurnRecord(
      turnId: json['turn_id'] as String? ?? '',
      turnIndex: (json['turn_index'] as num?)?.toInt() ?? 0,
      prompt: json['prompt'] as String? ?? '',
      responseText: json['response_text'] as String? ?? '',
      status: json['status'] as String? ?? 'completed',
    );
  }
}

String normalizeServerBaseUrl(String raw) {
  var u = raw.trim();
  while (u.endsWith('/')) {
    u = u.substring(0, u.length - 1);
  }
  if (u.endsWith('/v1')) {
    u = u.substring(0, u.length - 3);
  }
  return u;
}

class HemoryApiClient {
  final String baseUrl;
  final String authToken;
  final String tenantId;
  final String vaultId;
  final String dpInstanceId;
  final http.Client _client;

  HemoryApiClient({
    required String baseUrl,
    required this.authToken,
    this.tenantId = 'ten_default',
    this.vaultId = 'vlt_default',
    this.dpInstanceId = 'dp_default',
    http.Client? client,
  })  : baseUrl = normalizeServerBaseUrl(baseUrl),
        _client = client ?? http.Client();

  Map<String, String> get defaultHeaders => {
        'Authorization': 'Bearer $authToken',
        'Accept': 'application/json',
        'x-tenant-id': tenantId,
        'x-vault-id': vaultId,
        'x-dp-instance-id': dpInstanceId,
      };

  String resolveUrl(String pathOrUrl) {
    if (pathOrUrl.startsWith('http://') || pathOrUrl.startsWith('https://')) {
      return pathOrUrl;
    }
    final cleanPath = pathOrUrl.startsWith('/') ? pathOrUrl : '/$pathOrUrl';
    return '$baseUrl$cleanPath';
  }

  Future<Map<String, dynamic>> _decodeJsonResponse(http.Response resp) async {
    if (resp.statusCode >= 200 && resp.statusCode < 300) {
      if (resp.bodyBytes.isEmpty) return <String, dynamic>{};
      final decoded = jsonDecode(utf8.decode(resp.bodyBytes));
      if (decoded is Map<String, dynamic>) return decoded;
      return {'data': decoded};
    }
    String detail = resp.reasonPhrase ?? 'Request failed';
    try {
      final err = jsonDecode(utf8.decode(resp.bodyBytes));
      if (err is Map) {
        final d = err['detail'] ?? err['message'] ?? err['error'];
        if (d is Map) {
          detail = (d['message'] ?? d['code'] ?? d.toString()).toString();
        } else if (d != null) {
          detail = d.toString();
        }
      }
    } catch (_) {}
    throw HemoryApiException(resp.statusCode, detail);
  }

  Future<Map<String, dynamic>> verifyAndEnsurePrimary({
    String deviceName = 'Hemory Android',
  }) async {
    final ensureResp = await _client
        .post(
          Uri.parse('$baseUrl/dp-instances/ensure'),
          headers: {...defaultHeaders, 'Content-Type': 'application/json'},
          body: jsonEncode({
            'dp_instance_id': dpInstanceId,
            'dp_instance_name': deviceName,
            'device_kind': 'phone',
            'vault_id': vaultId,
            'make_primary': true,
          }),
        )
        .timeout(const Duration(seconds: 10));
    final ensureJson = await _decodeJsonResponse(ensureResp);

    try {
      final statusResp = await _client
          .get(Uri.parse('$baseUrl/v1/status'), headers: defaultHeaders)
          .timeout(const Duration(seconds: 8));
      if (statusResp.statusCode >= 200 && statusResp.statusCode < 300) {
        return await _decodeJsonResponse(statusResp);
      }
    } catch (_) {}
    return ensureJson;
  }

  Future<TimelineDayPayload> fetchTimelineDay({
    required String date,
    String? tz,
  }) async {
    final effectiveTz = (tz != null && tz.trim().isNotEmpty)
        ? tz.trim()
        : localUtcOffsetTimezone();
    final uri = Uri.parse('$baseUrl/timeline/day').replace(
      queryParameters: {'date': date, 'tz': effectiveTz},
    );
    final resp = await _client
        .get(uri, headers: defaultHeaders)
        .timeout(const Duration(seconds: 12));
    final json = await _decodeJsonResponse(resp);
    return parseTimelineDayJson(json);
  }

  static TimelineDayPayload parseTimelineDayJson(Map<String, dynamic> json) {
    final intervals = <TimelineInterval>[];

    final rawChains = (json['chains'] as List?) ?? const [];
    for (final item in rawChains) {
      if (item is! Map) continue;
      final m = Map<String, dynamic>.from(item);
      final sMin = ((m['start_min'] as num?)?.toInt() ?? 0).clamp(0, 1439);
      final eMin = ((m['end_min'] as num?)?.toInt() ?? (sMin + 1)).clamp(sMin + 1, 1440);
      final pct = ((m['progress_pct'] as num?)?.toDouble() ?? 20.0) / 100.0;
      intervals.add(
        TimelineInterval(
          intervalId: m['chain_id'] as String? ?? 'chain_$sMin',
          startMs: sMin,
          endMs: eMin,
          tier: TimelineTier.p1AnalyzingChain,
          itemType: 'AnalyzingChainCard',
          payload: {
            'progress': pct.clamp(0.05, 0.99),
            'current_phase': m['current_step'] as String? ?? 'transcribing',
            'start_epoch_ms': (m['start_epoch_ms'] as num?)?.toInt() ?? 0,
            'end_epoch_ms': (m['end_epoch_ms'] as num?)?.toInt() ?? 0,
          },
        ),
      );
    }

    final rawActivities = (json['activities'] as List?) ?? const [];
    for (final item in rawActivities) {
      if (item is! Map) continue;
      final m = Map<String, dynamic>.from(item);
      final sMin = ((m['start_min'] as num?)?.toInt() ?? 0).clamp(0, 1439);
      final eMin = ((m['end_min'] as num?)?.toInt() ?? (sMin + 1)).clamp(sMin + 1, 1440);
      final rawCat = (m['category'] as String? ?? '').trim().toLowerCase();
      final cleanCat = (rawCat == 'unknown' || rawCat == 'general' || rawCat == 'uncategorized')
          ? ''
          : rawCat;
      final title = (m['title'] as String? ?? '').trim();
      var summary = (m['summary'] as String? ?? '').trim();
      if (summary == title) summary = '';
      intervals.add(
        TimelineInterval(
          intervalId: m['event_id'] as String? ?? 'ev_$sMin',
          startMs: sMin,
          endMs: eMin,
          tier: TimelineTier.p2Activity,
          itemType: 'Activity',
          payload: {
            'event_id': m['event_id'] as String? ?? '',
            'title': title.isNotEmpty ? title : '对话记录',
            'summary': summary,
            'category': cleanCat,
            'duration_ms': (m['duration_ms'] as num?)?.toInt() ?? 0,
            'start_epoch_ms': (m['start_epoch_ms'] as num?)?.toInt() ?? 0,
            'end_epoch_ms': (m['end_epoch_ms'] as num?)?.toInt() ?? 0,
            'speakers': ((m['speaker_names'] as List?) ?? const [])
                .map((s) => s.toString())
                .where((s) => s.isNotEmpty)
                .toList(),
            'tags': ((m['keywords'] as List?) ?? const [])
                .map((s) => s.toString())
                .where((s) => s.isNotEmpty && s.toLowerCase() != 'unknown')
                .toList(),
            'pending_cluster_ids': ((m['pending_cluster_ids'] as List?) ?? const [])
                .map((s) => s.toString())
                .where((s) => s.isNotEmpty)
                .toList(),
            'utterance_count': (m['utterance_count'] as num?)?.toInt() ?? 0,
          },
        ),
      );
    }

    final rawScattered = (json['scattered_chats'] as List?) ?? const [];
    for (final item in rawScattered) {
      if (item is! Map) continue;
      final m = Map<String, dynamic>.from(item);
      final sMin = ((m['start_min'] as num?)?.toInt() ?? 0).clamp(0, 1439);
      final eMin = ((m['end_min'] as num?)?.toInt() ?? (sMin + 1)).clamp(sMin + 1, 1440);
      final snippets = ((m['snippets'] as List?) ?? const [])
          .whereType<Map>()
          .map((s) => Map<String, dynamic>.from(s))
          .toList();
      if (snippets.isEmpty) continue;
      intervals.add(
        TimelineInterval(
          intervalId: m['group_id'] as String? ?? 'scat_$sMin',
          startMs: sMin,
          endMs: eMin,
          tier: TimelineTier.p3ScatteredChat,
          itemType: 'ScatteredChatGroup',
          payload: {
            'group_id': m['group_id'] as String? ?? '',
            'duration_ms': (m['duration_ms'] as num?)?.toInt() ?? 0,
            'start_epoch_ms': (m['start_epoch_ms'] as num?)?.toInt() ?? 0,
            'end_epoch_ms': (m['end_epoch_ms'] as num?)?.toInt() ?? 0,
            'pending_cluster_ids': ((m['pending_cluster_ids'] as List?) ?? const [])
                .map((s) => s.toString())
                .where((s) => s.isNotEmpty)
                .toList(),
            'snippets': snippets,
          },
        ),
      );
    }

    intervals.sort((a, b) {
      final cmp = a.startMs.compareTo(b.startMs);
      if (cmp != 0) return cmp;
      return a.tier.compareTo(b.tier);
    });

    final coverage = ((json['coverage'] as List?) ?? const [])
        .whereType<Map>()
        .map((c) {
          final s = ((c['start_min'] as num?)?.toDouble() ?? 0.0).clamp(0.0, 1440.0);
          final e = ((c['end_min'] as num?)?.toDouble() ?? s).clamp(s, 1440.0);
          return TimelineCoverageSegment(startMinute: s, endMinute: e);
        })
        .toList();

    final density = <String, int>{};
    final rawDensity = json['density'];
    if (rawDensity is Map) {
      for (final entry in rawDensity.entries) {
        density[entry.key.toString()] = (entry.value as num?)?.toInt() ?? 0;
      }
    }

    return TimelineDayPayload(
      date: json['date'] as String? ?? '',
      timezone: json['timezone'] as String? ?? 'Asia/Shanghai',
      intervals: intervals,
      coverage: coverage,
      density: density,
      hasActiveChains: rawChains.isNotEmpty,
    );
  }

  Future<MemoryEventDetail> fetchMemoryEventDetail(
    String eventId, {
    String? tz,
  }) async {
    final effectiveTz = (tz != null && tz.trim().isNotEmpty)
        ? tz.trim()
        : localUtcOffsetTimezone();
    final uri = Uri.parse('$baseUrl/memory-events/$eventId').replace(
      queryParameters: {'tz': effectiveTz},
    );
    final resp = await _client
        .get(uri, headers: defaultHeaders)
        .timeout(const Duration(seconds: 10));
    final json = await _decodeJsonResponse(resp);
    return MemoryEventDetail.fromJson(json);
  }

  Future<MemoryEventDetail> patchMemoryEvent(
    String eventId, {
    String? title,
    String? summary,
    String? category,
  }) async {
    final body = <String, dynamic>{
      if (title != null) 'title': title,
      if (summary != null) 'summary': summary,
      if (category != null) 'category': category,
    };
    final resp = await _client
        .patch(
          Uri.parse('$baseUrl/memory-events/$eventId'),
          headers: {...defaultHeaders, 'Content-Type': 'application/json'},
          body: jsonEncode(body),
        )
        .timeout(const Duration(seconds: 10));
    final json = await _decodeJsonResponse(resp);
    return MemoryEventDetail.fromJson(json);
  }

  Future<List<SpeakerProfile>> fetchSpeakers() async {
    final resp = await _client
        .get(Uri.parse('$baseUrl/speakers'), headers: defaultHeaders)
        .timeout(const Duration(seconds: 10));
    final json = await _decodeJsonResponse(resp);
    final list = (json['speakers'] as List?) ?? const [];
    return list
        .whereType<Map>()
        .map((m) => SpeakerProfile.fromJson(Map<String, dynamic>.from(m)))
        .toList();
  }

  Future<void> createSpeaker({
    required String name,
    String? roleLabel,
    String? clusterId,
  }) async {
    final resp = await _client
        .post(
          Uri.parse('$baseUrl/speakers'),
          headers: {...defaultHeaders, 'Content-Type': 'application/json'},
          body: jsonEncode({
            'name': name.trim(),
            if (roleLabel != null && roleLabel.trim().isNotEmpty)
              'role_label': roleLabel.trim(),
            if (clusterId != null) 'cluster_id': clusterId,
          }),
        )
        .timeout(const Duration(seconds: 10));
    await _decodeJsonResponse(resp);
  }

  Future<List<VoiceprintClusterItem>> fetchClusters({String? status}) async {
    final uri = Uri.parse('$baseUrl/clusters').replace(
      queryParameters: status != null ? {'status': status} : null,
    );
    final resp = await _client
        .get(uri, headers: defaultHeaders)
        .timeout(const Duration(seconds: 10));
    final json = await _decodeJsonResponse(resp);
    final list = (json['clusters'] as List?) ?? const [];
    return list
        .whereType<Map>()
        .map((m) => VoiceprintClusterItem.fromJson(Map<String, dynamic>.from(m)))
        .toList();
  }

  Future<void> patchCluster({
    required String clusterId,
    required String action,
    String? name,
    String? speakerId,
    String? roleLabel,
  }) async {
    final body = <String, dynamic>{
      'action': action,
      if (name != null) 'name': name.trim(),
      if (speakerId != null) 'speaker_id': speakerId,
      if (roleLabel != null && roleLabel.trim().isNotEmpty)
        'role_label': roleLabel.trim(),
    };
    final resp = await _client
        .patch(
          Uri.parse('$baseUrl/clusters/$clusterId'),
          headers: {...defaultHeaders, 'Content-Type': 'application/json'},
          body: jsonEncode(body),
        )
        .timeout(const Duration(seconds: 10));
    await _decodeJsonResponse(resp);
  }

  Future<List<ChatThreadSummary>> fetchChatThreads() async {
    final resp = await _client
        .get(Uri.parse('$baseUrl/chat/threads'), headers: defaultHeaders)
        .timeout(const Duration(seconds: 10));
    final json = await _decodeJsonResponse(resp);
    final list = (json['threads'] as List?) ?? const [];
    return list
        .whereType<Map>()
        .map((m) => ChatThreadSummary.fromJson(Map<String, dynamic>.from(m)))
        .toList();
  }

  Future<ChatThreadSummary> createChatThread({String? title}) async {
    final resp = await _client
        .post(
          Uri.parse('$baseUrl/chat/threads'),
          headers: {...defaultHeaders, 'Content-Type': 'application/json'},
          body: jsonEncode({
            if (title != null && title.trim().isNotEmpty) 'title': title.trim(),
          }),
        )
        .timeout(const Duration(seconds: 10));
    final json = await _decodeJsonResponse(resp);
    return ChatThreadSummary.fromJson(json);
  }

  Future<List<ChatTurnRecord>> fetchChatTurns(String threadId) async {
    final resp = await _client
        .get(Uri.parse('$baseUrl/chat/threads/$threadId/turns'), headers: defaultHeaders)
        .timeout(const Duration(seconds: 10));
    final json = await _decodeJsonResponse(resp);
    final list = (json['turns'] as List?) ?? const [];
    return list
        .whereType<Map>()
        .map((m) => ChatTurnRecord.fromJson(Map<String, dynamic>.from(m)))
        .toList();
  }

  Future<Map<String, dynamic>> fetchRuntimeConfig() async {
    final resp = await _client
        .get(Uri.parse('$baseUrl/v1/runtime-config'), headers: defaultHeaders)
        .timeout(const Duration(seconds: 10));
    return _decodeJsonResponse(resp);
  }

  Future<Map<String, dynamic>> updateRuntimeConfig(Map<String, dynamic> patch) async {
    final resp = await _client
        .put(
          Uri.parse('$baseUrl/v1/runtime-config'),
          headers: {...defaultHeaders, 'Content-Type': 'application/json'},
          body: jsonEncode(patch),
        )
        .timeout(const Duration(seconds: 15));
    return _decodeJsonResponse(resp);
  }

  Future<int> fetchLatestMutationCursor({int after = 0}) async {
    final uri = Uri.parse('$baseUrl/recording-mutations').replace(
      queryParameters: {'after': after.toString(), 'limit': '1'},
    );
    final resp = await _client
        .get(uri, headers: defaultHeaders)
        .timeout(const Duration(seconds: 8));
    final json = await _decodeJsonResponse(resp);
    return (json['latest_cursor'] as num?)?.toInt() ??
        (json['next_cursor'] as num?)?.toInt() ??
        after;
  }

  void dispose() {
    _client.close();
  }
}
