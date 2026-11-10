/// Riverpod state graph wiring Android NativeBridge, dedicated Hemory Server API,
/// real-time audio metering, timeline queries, and speaker/cluster stores.
library;

import 'dart:async';
import 'dart:math';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import '../ipc/event_channels.dart';
import '../ipc/flutter_platform_bridge.dart';
import '../ipc/native_bridge.dart';
import '../models/recording_state.dart';
import '../models/speaker_models.dart';
import '../services/hemory_api_client.dart';
import '../services/recording_service.dart' as rec_svc;
import '../version.dart';

final nativeBridgeProvider = Provider<INativeBridge>((ref) {
  return createFlutterNativeBridge();
});

final recordingEventStreamsProvider = Provider<IRecordingEventStreams>((ref) {
  return createFlutterRecordingEventStreams();
});

/// Bridges pure-Dart `rec_svc.RecordingService` into Riverpod's `StateNotifier`.
class RecordingController extends StateNotifier<RecordingSessionState> {
  final rec_svc.RecordingService _inner;
  final INativeBridge _bridge;
  late final void Function(RecordingSessionState) _listener;

  RecordingController({
    required INativeBridge bridge,
    required IRecordingEventStreams streams,
  })  : _bridge = bridge,
        _inner = rec_svc.RecordingService(bridge: bridge, streams: streams),
        super(const RecordingSessionState()) {
    _listener = (next) {
      if (mounted) state = next;
    };
    _inner.addListener(_listener);
    _hydrateFromNative();
  }

  Future<void> _hydrateFromNative() async {
    try {
      final wire = await _bridge.getRecordingState();
      final current = RecordingCaptureState.fromWire(wire);
      if (mounted && current.isActive && !state.isActive) {
        state = state.copyWith(
          captureState: current,
          runningSinceMs: DateTime.now().millisecondsSinceEpoch,
        );
      }
    } catch (_) {}
  }

  Future<bool> startRecording({
    String? recordingId,
    String tenantId = 'ten_default',
    String vaultId = 'vlt_default',
    String userId = 'usr_default',
  }) async {
    final granted = await _bridge.ensurePermissions();
    if (!granted) {
      state = state.copyWith(
        captureState: RecordingCaptureState.failedTerminal,
        lastError: '需要麦克风权限',
      );
      return false;
    }
    if (state.captureState == RecordingCaptureState.failedTerminal ||
        state.captureState == RecordingCaptureState.stopped) {
      _inner.reset();
    }
    final id = recordingId ??
        'ls_${DateTime.now().millisecondsSinceEpoch}_${Random().nextInt(9999).toString().padLeft(4, '0')}';
    return _inner.startRecording(
      recordingId: id,
      tenantId: tenantId,
      vaultId: vaultId,
      userId: userId,
    );
  }

  Future<bool> pauseRecording() => _inner.pauseRecording();
  Future<bool> resumeRecording() => _inner.resumeRecording();
  Future<bool> stopRecording({String reason = 'userStop'}) =>
      _inner.stopRecording(reason: reason);
  void reset() => _inner.reset();

  @override
  void dispose() {
    _inner.removeListener(_listener);
    _inner.dispose();
    super.dispose();
  }
}

final recordingServiceProvider =
    StateNotifierProvider<RecordingController, RecordingSessionState>((ref) {
  final bridge = ref.watch(nativeBridgeProvider);
  final streams = ref.watch(recordingEventStreamsProvider);
  return RecordingController(bridge: bridge, streams: streams);
});

class LiveMeteringNotifier extends StateNotifier<List<double>> {
  static const int _sampleCount = 36;
  StreamSubscription<MeteringSnapshot>? _sub;

  LiveMeteringNotifier(IRecordingEventStreams streams)
      : super(List<double>.filled(_sampleCount, 0.08)) {
    _sub = streams.meteringStream.listen((snap) {
      if (!mounted) return;
      final next = List<double>.from(state.skip(1))..add(snap.normalizedPower);
      state = next;
    });
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}

final liveMeteringSamplesProvider =
    StateNotifierProvider<LiveMeteringNotifier, List<double>>((ref) {
  final streams = ref.watch(recordingEventStreamsProvider);
  return LiveMeteringNotifier(streams);
});

class AuthSession {
  final bool initialized;
  final bool isConnecting;
  final String baseUrl;
  final String accessToken;
  final String tenantId;
  final String vaultId;
  final String dpInstanceId;
  final String? errorMessage;

  const AuthSession({
    this.initialized = false,
    this.isConnecting = false,
    this.baseUrl = kDefaultServerUrl,
    this.accessToken = '',
    this.tenantId = 'ten_default',
    this.vaultId = 'vlt_default',
    this.dpInstanceId = 'dp_default',
    this.errorMessage,
  });

  bool get isAuthenticated => accessToken.trim().isNotEmpty;

  AuthSession copyWith({
    bool? initialized,
    bool? isConnecting,
    String? baseUrl,
    String? accessToken,
    String? tenantId,
    String? vaultId,
    String? dpInstanceId,
    String? errorMessage,
    bool clearError = false,
  }) {
    return AuthSession(
      initialized: initialized ?? this.initialized,
      isConnecting: isConnecting ?? this.isConnecting,
      baseUrl: baseUrl ?? this.baseUrl,
      accessToken: accessToken ?? this.accessToken,
      tenantId: tenantId ?? this.tenantId,
      vaultId: vaultId ?? this.vaultId,
      dpInstanceId: dpInstanceId ?? this.dpInstanceId,
      errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
    );
  }
}

class AuthSessionNotifier extends StateNotifier<AuthSession> {
  final INativeBridge _bridge;

  AuthSessionNotifier(this._bridge) : super(const AuthSession()) {
    _loadStoredAuth();
  }

  Future<void> _loadStoredAuth() async {
    try {
      final stored = await _bridge.getAuth();
      final vaultId = await _bridge.getStoredVaultId();
      if (stored != null && stored.accessToken.trim().isNotEmpty) {
        final cleanUrl = normalizeServerBaseUrl(stored.baseUrl);
        if (!mounted) return;
        state = AuthSession(
          initialized: true,
          baseUrl: cleanUrl.isNotEmpty ? cleanUrl : kDefaultServerUrl,
          accessToken: stored.accessToken.trim(),
          tenantId: stored.slotTenantId.trim().isNotEmpty
              ? stored.slotTenantId.trim()
              : 'ten_default',
          vaultId: vaultId.isNotEmpty ? vaultId : 'vlt_default',
          dpInstanceId: stored.slotDpUnitId.trim().isNotEmpty
              ? stored.slotDpUnitId.trim()
              : 'dp_default',
        );
        // Ensure primary upload lease in background without blocking UI boot.
        final client = HemoryApiClient(
          baseUrl: state.baseUrl,
          authToken: state.accessToken,
          tenantId: state.tenantId,
          vaultId: state.vaultId,
          dpInstanceId: state.dpInstanceId,
        );
        unawaited(
          client
              .verifyAndEnsurePrimary()
              .catchError((_) => <String, dynamic>{})
              .whenComplete(client.dispose),
        );
        return;
      }
    } catch (_) {}

    if (mounted) {
      state = state.copyWith(initialized: true);
    }
  }

  Future<bool> connect({
    required String baseUrl,
    required String accessToken,
    String tenantId = 'ten_default',
    String vaultId = 'vlt_default',
    String? dpInstanceId,
  }) async {
    final cleanUrl = normalizeServerBaseUrl(baseUrl);
    final cleanToken = accessToken.trim();
    final cleanTenant = tenantId.trim().isNotEmpty ? tenantId.trim() : 'ten_default';
    final cleanVault = vaultId.trim().isNotEmpty ? vaultId.trim() : 'vlt_default';
    final cleanDp = (dpInstanceId != null && dpInstanceId.trim().isNotEmpty)
        ? dpInstanceId.trim()
        : (state.dpInstanceId.isNotEmpty ? state.dpInstanceId : 'dp_default');

    if (cleanUrl.isEmpty || cleanToken.isEmpty) {
      state = state.copyWith(errorMessage: '请填写服务器地址与访问令牌');
      return false;
    }

    state = state.copyWith(isConnecting: true, clearError: true);

    final client = HemoryApiClient(
      baseUrl: cleanUrl,
      authToken: cleanToken,
      tenantId: cleanTenant,
      vaultId: cleanVault,
      dpInstanceId: cleanDp,
    );

    try {
      await client.verifyAndEnsurePrimary();
      final expiresSec =
          (DateTime.now().millisecondsSinceEpoch ~/ 1000) + 315360000; // 10y
      final tuple = Auth7Tuple(
        accessToken: cleanToken,
        refreshToken: cleanToken,
        expiresAt: expiresSec,
        baseUrl: cleanUrl,
        slotConnectionType: 'direct',
        slotDpUnitId: cleanDp,
        slotTenantId: cleanTenant,
      );
      await _bridge.storeAuthWithVault(tuple, vaultId: cleanVault);
      if (!mounted) return false;
      state = AuthSession(
        initialized: true,
        isConnecting: false,
        baseUrl: cleanUrl,
        accessToken: cleanToken,
        tenantId: cleanTenant,
        vaultId: cleanVault,
        dpInstanceId: cleanDp,
      );
      return true;
    } catch (e) {
      if (mounted) {
        state = state.copyWith(
          isConnecting: false,
          errorMessage: e.toString(),
        );
      }
      return false;
    } finally {
      client.dispose();
    }
  }

  Future<void> disconnect() async {
    await _bridge.signOutAuth();
    if (mounted) {
      state = const AuthSession(initialized: true);
    }
  }
}

final authSessionProvider =
    StateNotifierProvider<AuthSessionNotifier, AuthSession>((ref) {
  final bridge = ref.watch(nativeBridgeProvider);
  return AuthSessionNotifier(bridge);
});

final apiClientProvider = Provider<HemoryApiClient?>((ref) {
  final session = ref.watch(authSessionProvider);
  if (!session.isAuthenticated) return null;
  final client = HemoryApiClient(
    baseUrl: session.baseUrl,
    authToken: session.accessToken,
    tenantId: session.tenantId,
    vaultId: session.vaultId,
    dpInstanceId: session.dpInstanceId,
  );
  ref.onDispose(client.dispose);
  return client;
});

final selectedDateProvider = StateProvider<DateTime>((ref) {
  final now = DateTime.now();
  return DateTime(now.year, now.month, now.day);
});

final timelineRefreshTickProvider = StateProvider<int>((ref) => 0);

final timelineDayProvider = FutureProvider<TimelineDayPayload>((ref) async {
  ref.watch(timelineRefreshTickProvider);
  final client = ref.watch(apiClientProvider);
  if (client == null) return TimelineDayPayload.empty;
  final selectedDate = ref.watch(selectedDateProvider);
  final dateStr = DateFormat('yyyy-MM-dd').format(selectedDate);
  return client.fetchTimelineDay(date: dateStr);
});

final speakersRefreshTickProvider = StateProvider<int>((ref) => 0);

final speakersProvider = FutureProvider<List<SpeakerProfile>>((ref) async {
  ref.watch(speakersRefreshTickProvider);
  final client = ref.watch(apiClientProvider);
  if (client == null) return const [];
  return client.fetchSpeakers();
});

final pendingClustersProvider =
    FutureProvider<List<VoiceprintClusterItem>>((ref) async {
  ref.watch(speakersRefreshTickProvider);
  final client = ref.watch(apiClientProvider);
  if (client == null) return const [];
  return client.fetchClusters(status: 'pending');
});

final activeAudioUrlProvider = StateProvider<String?>((ref) => null);

Future<void> toggleAudioPreview(WidgetRef ref, String relativeOrFullUrl) async {
  final client = ref.read(apiClientProvider);
  final bridge = ref.read(nativeBridgeProvider);
  if (client == null) return;

  final fullUrl = client.resolveUrl(relativeOrFullUrl);
  final current = ref.read(activeAudioUrlProvider);
  if (current == fullUrl) {
    await bridge.stopAudioPlayback();
    ref.read(activeAudioUrlProvider.notifier).state = null;
    return;
  }

  ref.read(activeAudioUrlProvider.notifier).state = fullUrl;
  final ok = await bridge.playAudioUrl(
    url: fullUrl,
    authToken: client.authToken,
    headers: {
      'x-tenant-id': client.tenantId,
      'x-vault-id': client.vaultId,
    },
  );
  if (!ok) {
    ref.read(activeAudioUrlProvider.notifier).state = null;
  }
}
