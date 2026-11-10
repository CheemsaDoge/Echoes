import 'dart:async';
import '../lib/core/ipc/native_bridge.dart';
import '../lib/core/ipc/event_channels.dart';

void assertTrue(bool condition, String message) {
  if (!condition) {
    throw AssertionError('Assertion failed: $message');
  }
}

void main() async {
  print('=== Reviewer Adversarial Stress Test: Auth7Tuple & NativeBridge ===');

  final validMap = {
    'accessToken': 'valid_token_123',
    'refreshToken': 'valid_refresh_456',
    'expiresAt': 1750000000,
    'baseUrl': 'https://api.hemory.internal',
    'slotConnectionType': 'direct_wifi',
    'slotDpUnitId': 'dp_unit_secure_01',
    'slotTenantId': 'tenant_isolated_02',
  };

  final stringKeys = [
    'accessToken',
    'refreshToken',
    'baseUrl',
    'slotConnectionType',
    'slotDpUnitId',
    'slotTenantId',
  ];

  int passedChecks = 0;

  // 1. Missing keys
  for (final key in stringKeys) {
    final m = Map<String, dynamic>.from(validMap)..remove(key);
    bool threw = false;
    try {
      Auth7Tuple.fromMap(m);
    } on ArgumentError {
      threw = true;
    }
    assertTrue(threw, 'Missing key $key must throw ArgumentError');
    passedChecks++;
  }

  // Missing expiresAt
  {
    final m = Map<String, dynamic>.from(validMap)..remove('expiresAt');
    bool threw = false;
    try {
      Auth7Tuple.fromMap(m);
    } on ArgumentError {
      threw = true;
    }
    assertTrue(threw, 'Missing expiresAt must throw ArgumentError');
    passedChecks++;
  }

  // 2. Null values
  for (final key in [...stringKeys, 'expiresAt']) {
    final m = Map<String, dynamic>.from(validMap)..[key] = null;
    bool threw = false;
    try {
      Auth7Tuple.fromMap(m);
    } on ArgumentError {
      threw = true;
    }
    assertTrue(threw, 'Null value for $key must throw ArgumentError');
    passedChecks++;
  }

  // 3. Empty string validation
  for (final key in stringKeys) {
    final m = Map<String, dynamic>.from(validMap)..[key] = '';
    bool threw = false;
    try {
      Auth7Tuple.fromMap(m);
    } on ArgumentError {
      threw = true;
    }
    assertTrue(threw, 'Empty string for $key must throw ArgumentError');
    passedChecks++;
  }

  // 4. Non-string types in string fields
  final invalidValues = [
    12345,
    0,
    -1,
    true,
    false,
    3.14159,
    <String>['list', 'element'],
    <String, dynamic>{'nested': 'map'},
    Object(),
  ];

  for (final key in stringKeys) {
    for (final invalidVal in invalidValues) {
      final m = Map<String, dynamic>.from(validMap)..[key] = invalidVal;
      bool threwArgError = false;
      try {
        Auth7Tuple.fromMap(m);
      } on ArgumentError {
        threwArgError = true;
      } catch (e) {
        throw AssertionError('LEAK OF UNCHECKED EXCEPTION ($e) on field $key with value $invalidVal');
      }
      assertTrue(threwArgError, 'Field $key with $invalidVal must throw ArgumentError');
      passedChecks++;
    }
  }

  // 5. Invalid values for expiresAt
  final invalidExpiresAt = [
    0,
    -1,
    -100,
    -999999999,
    0.0,
    0.4,
    0.999,
    -0.5,
    double.nan,
    double.infinity,
    double.negativeInfinity,
    '1750000000',
    '',
    true,
    false,
    [1750000000],
    {'expires': 1750000000},
  ];

  for (final invalidVal in invalidExpiresAt) {
    final m = Map<String, dynamic>.from(validMap)..['expiresAt'] = invalidVal;
    bool threwArgError = false;
    try {
      Auth7Tuple.fromMap(m);
    } on ArgumentError {
      threwArgError = true;
    } catch (e) {
      throw AssertionError('LEAK OF UNCHECKED EXCEPTION ($e) on expiresAt with value $invalidVal');
    }
    assertTrue(threwArgError, 'expiresAt with $invalidVal must throw ArgumentError');
    passedChecks++;
  }

  // 6. Valid float expiresAt truncation (e.g. 1750000000.75 -> 1750000000)
  {
    final m = Map<String, dynamic>.from(validMap)..['expiresAt'] = 1750000000.75;
    final auth = Auth7Tuple.fromMap(m);
    assertTrue(auth.expiresAt == 1750000000, 'expiresAt float should be converted to int');
    passedChecks++;
  }

  // 7. Extra arbitrary fields in map (must not disrupt parsing or leak)
  {
    final mExtra = Map<String, dynamic>.from(validMap)
      ..['extra1'] = 'extra_val'
      ..['malicious_payload'] = {'attack': true}
      ..['__proto__'] = 'pollution';
    final auth = Auth7Tuple.fromMap(mExtra);
    assertTrue(auth.accessToken == 'valid_token_123', 'Parsed correctly with extra fields');
    final exported = auth.toMap();
    assertTrue(!exported.containsKey('extra1'), 'Extra fields must not leak to toMap()');
    assertTrue(!exported.containsKey('malicious_payload'), 'Extra fields must not leak to toMap()');
    passedChecks++;
  }

  // 8. Round-trip integrity
  {
    final auth1 = Auth7Tuple.fromMap(validMap);
    final exported = auth1.toMap();
    final auth2 = Auth7Tuple.fromMap(exported);
    assertTrue(auth1.accessToken == auth2.accessToken, 'Roundtrip accessToken');
    assertTrue(auth1.refreshToken == auth2.refreshToken, 'Roundtrip refreshToken');
    assertTrue(auth1.expiresAt == auth2.expiresAt, 'Roundtrip expiresAt');
    assertTrue(auth1.baseUrl == auth2.baseUrl, 'Roundtrip baseUrl');
    assertTrue(auth1.slotConnectionType == auth2.slotConnectionType, 'Roundtrip slotConnectionType');
    assertTrue(auth1.slotDpUnitId == auth2.slotDpUnitId, 'Roundtrip slotDpUnitId');
    assertTrue(auth1.slotTenantId == auth2.slotTenantId, 'Roundtrip slotTenantId');
    passedChecks++;
  }

  // 9. NativeBridgeChannel in headless mode and mock mode
  {
    // Headless mode
    final headlessBridge = NativeBridgeChannel();
    assertTrue(await headlessBridge.getRecordingState() == 'idle', 'Initial state idle');
    assertTrue(await headlessBridge.startRecording(recordingId: 'r1', tenantId: 't1', vaultId: 'v1'), 'Start');
    assertTrue(await headlessBridge.getRecordingState() == 'activating', 'Activating state');
    assertTrue(await headlessBridge.pauseRecording(), 'Pause');
    assertTrue(await headlessBridge.getRecordingState() == 'paused', 'Paused state');
    assertTrue(await headlessBridge.resumeRecording(), 'Resume');
    assertTrue(await headlessBridge.getRecordingState() == 'recording', 'Recording state');
    assertTrue(await headlessBridge.stopRecording(), 'Stop');
    assertTrue(await headlessBridge.getRecordingState() == 'stopped', 'Stopped state');

    final auth = Auth7Tuple.fromMap(validMap);
    assertTrue(await headlessBridge.storeAuth(auth), 'Store auth');
    final retrieved = await headlessBridge.getAuth();
    assertTrue(retrieved?.accessToken == 'valid_token_123', 'Retrieved auth');
    assertTrue(await headlessBridge.clearAuth(), 'Clear auth');
    assertTrue(await headlessBridge.getAuth() == null, 'Auth cleared');
    passedChecks++;
  }

  // 10. NativeBridgeChannel with mock invoker returning corrupt or wrong types
  {
    // Corrupt getAuth return value (not a Map)
    final bridgeCorrupt = NativeBridgeChannel(
      invoker: (method, [args]) async {
        if (method == 'getAuth') return 'not_a_map';
        return null;
      },
    );
    final authCorrupt = await bridgeCorrupt.getAuth();
    assertTrue(authCorrupt == null, 'Non-map getAuth should return null, not crash');
    passedChecks++;

    // getAuth returning malformed map (should throw ArgumentError due to Auth7Tuple.fromMap)
    final bridgeMalformed = NativeBridgeChannel(
      invoker: (method, [args]) async {
        if (method == 'getAuth') return <String, dynamic>{'invalid': 'data'};
        return null;
      },
    );
    bool threwMalformed = false;
    try {
      await bridgeMalformed.getAuth();
    } on ArgumentError {
      threwMalformed = true;
    }
    assertTrue(threwMalformed, 'Malformed map from getAuth must throw ArgumentError');
    passedChecks++;
  }

  print('ALL $passedChecks REVIEWER ADVERSARIAL CHECKS PASSED CLEANLY!');
}
