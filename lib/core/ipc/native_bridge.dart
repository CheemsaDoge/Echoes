/// MethodChannel interface to Android NativeBridgePlugin (`hemory/native_bridge`).
/// Implements Contract C1 with resilient fallback for headless execution.
library;

import 'dart:async';

/// 7-Tuple Authentication Configuration required by NativeBridgePlugin.kt.
class Auth7Tuple {
  final String accessToken;
  final String refreshToken;
  final int expiresAt;
  final String baseUrl;
  final String slotConnectionType;
  final String slotDpUnitId;
  final String slotTenantId;

  const Auth7Tuple({
    required this.accessToken,
    required this.refreshToken,
    required this.expiresAt,
    required this.baseUrl,
    required this.slotConnectionType,
    required this.slotDpUnitId,
    required this.slotTenantId,
  });

  factory Auth7Tuple.fromMap(Map<String, dynamic> map) {
    const stringKeys = [
      'accessToken',
      'refreshToken',
      'baseUrl',
      'slotConnectionType',
      'slotDpUnitId',
      'slotTenantId',
    ];

    for (final key in stringKeys) {
      if (!map.containsKey(key) || map[key] == null) {
        throw ArgumentError('Missing required Auth7Tuple key: $key');
      }
      final val = map[key];
      if (val is! String || val.trim().isEmpty) {
        throw ArgumentError(
          'Auth7Tuple field "$key" must be a non-empty String (got: $val)',
        );
      }
    }

    if (!map.containsKey('expiresAt') || map['expiresAt'] == null) {
      throw ArgumentError('Missing required Auth7Tuple key: expiresAt');
    }

    final expiresAtVal = map['expiresAt'];
    if (expiresAtVal is! num || !expiresAtVal.isFinite) {
      throw ArgumentError(
        'Auth7Tuple field "expiresAt" must be a positive number (got: $expiresAtVal)',
      );
    }

    final expiresAt = expiresAtVal.toInt();
    if (expiresAt <= 0) {
      throw ArgumentError(
        'Auth7Tuple field "expiresAt" must be positive > 0 (got: $expiresAt)',
      );
    }

    return Auth7Tuple(
      accessToken: map['accessToken'] as String,
      refreshToken: map['refreshToken'] as String,
      expiresAt: expiresAt,
      baseUrl: map['baseUrl'] as String,
      slotConnectionType: map['slotConnectionType'] as String,
      slotDpUnitId: map['slotDpUnitId'] as String,
      slotTenantId: map['slotTenantId'] as String,
    );
  }

  Map<String, dynamic> toMap() => {
        'accessToken': accessToken,
        'refreshToken': refreshToken,
        'expiresAt': expiresAt,
        'baseUrl': baseUrl,
        'slotConnectionType': slotConnectionType,
        'slotDpUnitId': slotDpUnitId,
        'slotTenantId': slotTenantId,
      };
}

/// Interface defining operations supported by NativeBridge (Contract C1).
abstract class INativeBridge {
  Future<bool> startRecording({
    required String recordingId,
    required String tenantId,
    required String vaultId,
    String userId = '',
  });

  Future<bool> stopRecording({String reason = 'userStop'});
  Future<bool> pauseRecording();
  Future<bool> resumeRecording();
  Future<String> getRecordingState();
  Future<bool> storeAuth(Auth7Tuple auth);
  Future<Auth7Tuple?> getAuth();
  Future<bool> clearAuth();
  Future<bool> signOutAuth();
}

typedef MethodCallHandler = Future<dynamic> Function(
  String method, [
  dynamic arguments,
]);

/// Concrete implementation of INativeBridge supporting injected method callers.
class NativeBridgeChannel implements INativeBridge {
  final MethodCallHandler? _invoker;
  String _state = 'idle';
  Auth7Tuple? _auth;
  String _vaultId = 'vlt_default';

  NativeBridgeChannel({MethodCallHandler? invoker}) : _invoker = invoker;

  @override
  Future<bool> startRecording({
    required String recordingId,
    required String tenantId,
    required String vaultId,
    String userId = '',
  }) async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('startRecording', {
        'recordingId': recordingId,
        'tenantId': tenantId,
        'vaultId': vaultId,
        'userId': userId,
      });
      return (res as bool?) ?? false;
    }
    _state = 'activating';
    return true;
  }

  @override
  Future<bool> stopRecording({String reason = 'userStop'}) async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('stopRecording', {'reason': reason});
      return (res as bool?) ?? false;
    }
    _state = 'stopped';
    return true;
  }

  @override
  Future<bool> pauseRecording() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('pauseRecording');
      return (res as bool?) ?? false;
    }
    _state = 'recovering';
    return true;
  }

  @override
  Future<bool> resumeRecording() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('resumeRecording');
      return (res as bool?) ?? false;
    }
    _state = 'recording';
    return true;
  }

  @override
  Future<String> getRecordingState() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('getRecordingState');
      return (res as String?) ?? 'idle';
    }
    return _state;
  }

  @override
  Future<bool> storeAuth(Auth7Tuple auth) async {
    return storeAuthWithVault(auth);
  }

  Future<bool> storeAuthWithVault(Auth7Tuple auth, {String? vaultId}) async {
    final invoker = _invoker;
    if (invoker != null) {
      final payload = auth.toMap();
      if (vaultId != null && vaultId.trim().isNotEmpty) {
        payload['slotVaultId'] = vaultId.trim();
      }
      final res = await invoker('storeAuth', payload);
      return (res as bool?) ?? false;
    }
    _auth = auth;
    if (vaultId != null && vaultId.trim().isNotEmpty) {
      _vaultId = vaultId.trim();
    }
    return true;
  }

  @override
  Future<Auth7Tuple?> getAuth() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('getAuth');
      if (res is Map) {
        final normalized = <String, dynamic>{
          for (final entry in res.entries)
            if (entry.key != null) entry.key.toString(): entry.value,
        };
        if (normalized.isEmpty || normalized['accessToken'] == null) {
          return null;
        }
        return Auth7Tuple.fromMap(normalized);
      }
      return null;
    }
    return _auth;
  }

  Future<String> getStoredVaultId() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('getAuth');
      if (res is Map) {
        final v = res['slotVaultId']?.toString().trim();
        if (v != null && v.isNotEmpty) return v;
      }
      return 'vlt_default';
    }
    return _vaultId;
  }

  @override
  Future<bool> clearAuth() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('clearAuth');
      return (res as bool?) ?? false;
    }
    _auth = null;
    return true;
  }

  @override
  Future<bool> signOutAuth() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('signOutAuth');
      return (res as bool?) ?? false;
    }
    _auth = null;
    return true;
  }

  Future<bool> ensurePermissions() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('ensurePermissions');
      return (res as bool?) ?? false;
    }
    return true;
  }

  Future<Map<String, int>> getUploadQueueStats() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('getUploadQueueStats');
      if (res is Map) {
        return {
          'pending': (res['pending'] as num?)?.toInt() ?? 0,
          'uploading': (res['uploading'] as num?)?.toInt() ?? 0,
          'failed': (res['failed'] as num?)?.toInt() ?? 0,
          'uploaded': (res['uploaded'] as num?)?.toInt() ?? 0,
        };
      }
    }
    return const {'pending': 0, 'uploading': 0, 'failed': 0, 'uploaded': 0};
  }

  Future<bool> retryFailedUploads() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('retryFailedUploads');
      if (res is num) return res >= 0;
      return (res as bool?) ?? false;
    }
    return true;
  }

  Future<bool> playAudioUrl({
    required String url,
    required String authToken,
    Map<String, String>? headers,
  }) async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('playAudioUrl', {
        'url': url,
        'authToken': authToken,
        if (headers != null) 'headers': headers,
      });
      return (res as bool?) ?? false;
    }
    return true;
  }

  Future<bool> stopAudioPlayback() async {
    final invoker = _invoker;
    if (invoker != null) {
      final res = await invoker('stopAudioPlayback');
      return (res as bool?) ?? false;
    }
    return true;
  }
}

/// Extension methods on INativeBridge so existing mock classes implementing the
/// core 9-method Contract C1 remain 100% compatible without boilerplate stubs.
extension NativeBridgeExtensions on INativeBridge {
  Future<bool> storeAuthWithVault(Auth7Tuple auth, {String? vaultId}) {
    final self = this;
    if (self is NativeBridgeChannel) {
      return self.storeAuthWithVault(auth, vaultId: vaultId);
    }
    return storeAuth(auth);
  }

  Future<String> getStoredVaultId() async {
    final self = this;
    if (self is NativeBridgeChannel) {
      return self.getStoredVaultId();
    }
    return 'vlt_default';
  }

  Future<bool> ensurePermissions() async {
    final self = this;
    if (self is NativeBridgeChannel) {
      return self.ensurePermissions();
    }
    return true;
  }

  Future<Map<String, int>> getUploadQueueStats() async {
    final self = this;
    if (self is NativeBridgeChannel) {
      return self.getUploadQueueStats();
    }
    return const {'pending': 0, 'uploading': 0, 'failed': 0, 'uploaded': 0};
  }

  Future<bool> retryFailedUploads() async {
    final self = this;
    if (self is NativeBridgeChannel) {
      return self.retryFailedUploads();
    }
    return true;
  }

  Future<bool> playAudioUrl({
    required String url,
    required String authToken,
    Map<String, String>? headers,
  }) async {
    final self = this;
    if (self is NativeBridgeChannel) {
      return self.playAudioUrl(
        url: url,
        authToken: authToken,
        headers: headers,
      );
    }
    return true;
  }

  Future<bool> stopAudioPlayback() async {
    final self = this;
    if (self is NativeBridgeChannel) {
      return self.stopAudioPlayback();
    }
    return true;
  }
}
