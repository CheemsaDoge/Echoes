import 'dart:io';
import '../lib/core/ipc/native_bridge.dart';

void main() {
  print('===============================================================');
  print('EMPIRICAL ADVERSARIAL FUZZING: Auth7Tuple.fromMap');
  print('===============================================================');

  final validAuthMap = {
    'accessToken': 'valid_access_token',
    'refreshToken': 'valid_refresh_token',
    'expiresAt': 1790000000,
    'baseUrl': 'https://api.hemory.app',
    'slotConnectionType': 'direct',
    'slotDpUnitId': 'dp_unit_01',
    'slotTenantId': 'tenant_prod',
  };

  final stringKeys = [
    'accessToken',
    'refreshToken',
    'baseUrl',
    'slotConnectionType',
    'slotDpUnitId',
    'slotTenantId',
  ];

  int totalTests = 0;
  int failures = 0;
  final failedCases = <String>[];

  void testExpectArgumentError(String description, Map<String, dynamic> input) {
    totalTests++;
    try {
      final res = Auth7Tuple.fromMap(input);
      // If we reached here, no exception was thrown!
      failures++;
      final msg = 'FAIL [ACCEPTED]: $description -> Auth7Tuple created with accessToken="${res.accessToken}", expiresAt=${res.expiresAt}';
      print('❌ $msg');
      failedCases.add(msg);
    } on ArgumentError catch (e) {
      // Expected!
      print('✅ PASS: $description -> threw ArgumentError (${e.message})');
    } catch (e) {
      // Threw something else, e.g. _TypeError
      failures++;
      final msg = 'FAIL [WRONG_EXCEPTION]: $description -> threw ${e.runtimeType}: $e';
      print('❌ $msg');
      failedCases.add(msg);
    }
  }

  // 1. Negative expiresAt (-1, -100, -999999999)
  print('\n--- Fuzzing negative expiresAt ---');
  for (final neg in [-1, -100, -999999999]) {
    final map = Map<String, dynamic>.from(validAuthMap)..['expiresAt'] = neg;
    testExpectArgumentError('negative expiresAt ($neg)', map);
  }

  // 2. Zero expiresAt (0)
  print('\n--- Fuzzing zero expiresAt ---');
  {
    final map = Map<String, dynamic>.from(validAuthMap)..['expiresAt'] = 0;
    testExpectArgumentError('zero expiresAt (0)', map);
  }

  // 3. Non-numeric expiresAt ('tomorrow', true, [], {})
  print('\n--- Fuzzing non-numeric expiresAt ---');
  for (final nonNum in ['tomorrow', true, <dynamic>[], <String, dynamic>{}]) {
    final map = Map<String, dynamic>.from(validAuthMap)..['expiresAt'] = nonNum;
    testExpectArgumentError('non-numeric expiresAt (${nonNum.runtimeType}: $nonNum)', map);
  }

  // 4. Invalid types for String fields (int, double, bool, list, map)
  print('\n--- Fuzzing invalid types for String fields ---');
  final invalidStringValues = [
    12345,
    99.9,
    true,
    false,
    <dynamic>[1, 2],
    <String, dynamic>{'foo': 'bar'},
  ];

  for (final key in stringKeys) {
    for (final badVal in invalidStringValues) {
      final map = Map<String, dynamic>.from(validAuthMap)..[key] = badVal;
      testExpectArgumentError('invalid type for $key (${badVal.runtimeType})', map);
    }
  }

  // 5. Empty strings ('') and pure whitespace strings
  print('\n--- Fuzzing empty strings and pure whitespace strings ---');
  final emptyOrWhitespaceValues = [
    '',
    ' ',
    '   ',
    '\t',
    '\n',
    '\r\n',
    ' \t \n \r ',
  ];

  for (final key in stringKeys) {
    for (final wsVal in emptyOrWhitespaceValues) {
      final map = Map<String, dynamic>.from(validAuthMap)..[key] = wsVal;
      final escaped = wsVal
          .replaceAll('\t', '\\t')
          .replaceAll('\n', '\\n')
          .replaceAll('\r', '\\r');
      testExpectArgumentError('empty/whitespace for $key ("$escaped")', map);
    }
  }

  print('\n===============================================================');
  print('FUZZING RESULTS SUMMARY');
  print('Total tests executed: $totalTests');
  print('Passed: ${totalTests - failures}');
  print('Failed: $failures');
  if (failures > 0) {
    print('Failed cases:');
    for (final f in failedCases) {
      print('  - $f');
    }
  }
  print('===============================================================');

  if (failures > 0) {
    exit(1);
  }
}
