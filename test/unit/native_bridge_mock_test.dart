/// Unit tests for Native Bridge IPC and EventChannels DTOs.
library;

import '../../lib/core/ipc/native_bridge.dart';
import '../../lib/core/ipc/event_channels.dart';

void assertEqual<T>(T actual, T expected, String message) {
  if (actual != expected) {
    throw AssertionError('$message: Expected "$expected", got "$actual"');
  }
}

void assertTrue(bool condition, String message) {
  if (!condition) {
    throw AssertionError('Assertion failed: $message');
  }
}

void main() {
  print('Running Native Bridge IPC & EventChannels Tests...');

  // Test 1: Complete Auth7Tuple validation and missing key rejection
  {
    final validMap = {
      'accessToken': 'tok_access_123',
      'refreshToken': 'tok_refresh_456',
      'expiresAt': 1790000000,
      'baseUrl': 'https://api.hemory.app',
      'slotConnectionType': 'direct',
      'slotDpUnitId': 'dp_unit_01',
      'slotTenantId': 'tenant_prod',
    };

    final auth = Auth7Tuple.fromMap(validMap);
    assertEqual(auth.accessToken, 'tok_access_123', 'accessToken');
    assertEqual(auth.slotTenantId, 'tenant_prod', 'slotTenantId');
    assertEqual(auth.expiresAt, 1790000000, 'expiresAt');

    final exported = auth.toMap();
    assertEqual(exported['baseUrl'], 'https://api.hemory.app', 'exported baseUrl');

    // Missing key rejection
    for (final missingKey in validMap.keys) {
      final invalidMap = Map<String, dynamic>.from(validMap)..remove(missingKey);
      bool threw = false;
      try {
        Auth7Tuple.fromMap(invalidMap);
      } on ArgumentError {
        threw = true;
      }
      assertTrue(threw, 'Missing key $missingKey must throw ArgumentError');
    }
    print('  [PASS] Test 1: Complete Auth7Tuple serialization and 7-field guard');
  }

  // Test 2: NativeBridgeChannel operations with mock invoker
  {
    final recordedCalls = <Map<String, dynamic>>[];
    final bridge = NativeBridgeChannel(
      invoker: (method, [args]) async {
        recordedCalls.add({'method': method, 'args': args});
        if (method == 'startRecording') return true;
        if (method == 'stopRecording') return true;
        if (method == 'pauseRecording') return true;
        if (method == 'resumeRecording') return true;
        if (method == 'getRecordingState') return 'recording';
        if (method == 'storeAuth') return true;
        if (method == 'getAuth') {
          return {
            'accessToken': 'a',
            'refreshToken': 'b',
            'expiresAt': 12345,
            'baseUrl': 'http://localhost',
            'slotConnectionType': 'usb',
            'slotDpUnitId': 'u1',
            'slotTenantId': 't1',
          };
        }
        return true;
      },
    );

    bridge.startRecording(recordingId: 'r1', tenantId: 't1', vaultId: 'v1');
    bridge.pauseRecording();
    bridge.resumeRecording();
    bridge.stopRecording(reason: 'userStop');

    assertEqual(recordedCalls.length, 4, '4 calls recorded');
    assertEqual(recordedCalls[0]['method'], 'startRecording', 'call 0');
    assertEqual(recordedCalls[1]['method'], 'pauseRecording', 'call 1');
    assertEqual(recordedCalls[2]['method'], 'resumeRecording', 'call 2');
    assertEqual(recordedCalls[3]['method'], 'stopRecording', 'call 3');
    print('  [PASS] Test 2: MethodChannel IPC method invocations');
  }

  // Test 3: EventChannel DTOs and Normalized Power Math
  {
    // Metering decibel power normalization: [-60.0, 0.0] -> [0.05, 1.0]
    final quiet = MeteringSnapshot.fromMap({
      'rmsDb': -60.0,
      'peakDb': -50.0,
      'timestamp': 1000,
    });
    assertEqual(quiet.normalizedPower, 0.05, 'Quiet floor clamped to 0.05');

    final maxVolume = MeteringSnapshot.fromMap({
      'rmsDb': 0.0,
      'peakDb': 0.0,
      'timestamp': 1000,
    });
    assertEqual(maxVolume.normalizedPower, 1.0, 'Max volume normalized to 1.0');

    final midVolume = MeteringSnapshot.fromMap({
      'rmsDb': -30.0,
      'peakDb': -20.0,
      'timestamp': 1000,
    });
    assertEqual(midVolume.normalizedPower, 0.5, 'Mid volume normalized to 0.5');

    // Segment completion event
    final segEvent = SegmentCompletedEvent.fromMap({
      'recordingId': 'rec_99',
      'segmentIndex': 3,
      'filePath': '/data/files/1003.aac',
      'metaFilePath': '/data/files/1003.meta',
      'durationMs': 1800000,
      'sizeBytes': 10800000,
      'sha256': 'a1b2c3d4e5f6',
      'splitReason': 'silence_window',
    });
    assertEqual(segEvent.recordingId, 'rec_99', 'segEvent recordingId');
    assertEqual(segEvent.segmentIndex, 3, 'segEvent segmentIndex');
    assertEqual(segEvent.durationMs, 1800000, 'segEvent duration');
    assertEqual(segEvent.splitReason, 'silence_window', 'segEvent splitReason');
    print('  [PASS] Test 3: EventChannel DTO parsing and audio power formula');
  }

  print('\nALL NATIVE BRIDGE IPC TESTS PASSED SUCCESSFULLY!');
}
