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
      if (val is! String || val.isEmpty) {
        throw ArgumentError('Auth7Tuple field "$key" must be a non-empty String (got: $val)');
      }
    }

    if (!map.containsKey('expiresAt') || map['expiresAt'] == null) {
      throw ArgumentError('Missing required Auth7Tuple key: expiresAt');
    }

    final expiresAtVal = map['expiresAt'];
    if (expiresAtVal is! num || !expiresAtVal.isFinite) {
      throw ArgumentError('Auth7Tuple field "expiresAt" must be a positive number (got: $expiresAtVal)');
    }

    final expiresAt = expiresAtVal.toInt();
    if (expiresAt <= 0) {
      throw ArgumentError('Auth7Tuple field "expiresAt" must be positive > 0 (got: $expiresAt)');
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

/// Interface defining operations supported by NativeBridge.
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

typedef MethodCallHandler = Future<dynamic> Function(String method, [dynamic arguments]);

/// Concrete implementation of INativeBridge supporting injected method callers.
class NativeBridgeChannel implements INativeBridge {
  final MethodCallHandler? _invoker;
  String _state = 'idle';
  Auth7Tuple? _auth;

  NativeBridgeChannel({MethodCallHandler? invoker}) : _invoker = invoker;

  @override
  Future<bool> startRecording({
    required String recordingId,
    required String tenantId,
    required String vaultId,
    String userId = '',
  }) async {
    if (_invoker != null) {
      final res = await _invoker('startRecording', {
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
    if (_invoker != null) {
      final res = await _invoker('stopRecording', {'reason': reason});
      return (res as bool?) ?? false;
    }
    _state = 'stopped';
    return true;
  }

  @override
  Future<bool> pauseRecording() async {
    if (_invoker != null) {
      final res = await _invoker('pauseRecording');
      return (res as bool?) ?? false;
    }
    _state = 'paused';
    return true;
  }

  @override
  Future<bool> resumeRecording() async {
    if (_invoker != null) {
      final res = await _invoker('resumeRecording');
      return (res as bool?) ?? false;
    }
    _state = 'recording';
    return true;
  }

  @override
  Future<String> getRecordingState() async {
    if (_invoker != null) {
      final res = await _invoker('getRecordingState');
      return (res as String?) ?? 'idle';
    }
    return _state;
  }

  @override
  Future<bool> storeAuth(Auth7Tuple auth) async {
    if (_invoker != null) {
      final res = await _invoker('storeAuth', auth.toMap());
      return (res as bool?) ?? false;
    }
    _auth = auth;
    return true;
  }

  @override
  Future<Auth7Tuple?> getAuth() async {
    if (_invoker != null) {
      final res = await _invoker('getAuth');
      if (res is Map<String, dynamic>) {
        return Auth7Tuple.fromMap(res);
      }
      return null;
    }
    return _auth;
  }

  @override
  Future<bool> clearAuth() async {
    if (_invoker != null) {
      final res = await _invoker('clearAuth');
      return (res as bool?) ?? false;
    }
    _auth = null;
    return true;
  }

  @override
  Future<bool> signOutAuth() async {
    if (_invoker != null) {
      final res = await _invoker('signOutAuth');
      return (res as bool?) ?? false;
    }
    _auth = null;
    return true;
  }
}
