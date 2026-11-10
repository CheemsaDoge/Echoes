import 'dart:io';
import '../lib/core/ipc/native_bridge.dart';

void main() {
  print('===============================================================');
  print('EMPIRICAL ADVERSARIAL STRESS TEST: EXTREME WHITESPACE & UNICODE');
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

  // Catalog of extreme whitespace & formatting characters
  final extremeWhitespaceCatalog = <String, String>{
    'ASCII Space (U+0020)': '\u0020',
    'ASCII Tab (U+0009)': '\u0009',
    'ASCII Line Feed (U+000A)': '\u000A',
    'ASCII Vertical Tab (U+000B)': '\u000B',
    'ASCII Form Feed (U+000C)': '\u000C',
    'ASCII Carriage Return (U+000D)': '\u000D',
    'ASCII Next Line / NEL (U+0085)': '\u0085',
    'Non-Breaking Space (U+00A0)': '\u00A0',
    'Ogham Space Mark (U+1680)': '\u1680',
    'En Quad (U+2000)': '\u2000',
    'Em Quad (U+2001)': '\u2001',
    'En Space (U+2002)': '\u2002',
    'Em Space (U+2003)': '\u2003',
    'Three-Per-Em Space (U+2004)': '\u2004',
    'Four-Per-Em Space (U+2005)': '\u2005',
    'Six-Per-Em Space (U+2006)': '\u2006',
    'Figure Space (U+2007)': '\u2007',
    'Punctuation Space (U+2008)': '\u2008',
    'Thin Space (U+2009)': '\u2009',
    'Hair Space (U+200A)': '\u200A',
    'Line Separator (U+2028)': '\u2028',
    'Paragraph Separator (U+2029)': '\u2029',
    'Narrow No-Break Space (U+202F)': '\u202F',
    'Medium Mathematical Space (U+205F)': '\u205F',
    'Ideographic Space / Fullwidth (U+3000)': '\u3000',
    'BOM / Zero-Width No-Break Space (U+FEFF)': '\uFEFF',
    // Zero-width & control formatting characters:
    'Zero-Width Space (U+200B)': '\u200B',
    'Zero-Width Non-Joiner (U+200C)': '\u200C',
    'Zero-Width Joiner (U+200D)': '\u200D',
    'Left-to-Right Mark (U+200E)': '\u200E',
    'Right-to-Left Mark (U+200F)': '\u200F',
    'Word Joiner (U+2060)': '\u2060',
    // Mixed multi-character combinations:
    'Mixed ASCII Whitespace': '  \t \r\n \t  ',
    'Mixed Unicode Spaces': '\u00A0\u2000\u2002\u2003\u3000',
    'Spaces + Zero-Width': '  \u200B \u200C  ',
    'All Control + Space combo': '\u200E \u200F \t \uFEFF \n \u200B',
  };

  print('Testing ${extremeWhitespaceCatalog.length} distinct extreme whitespace/control vectors across ${stringKeys.length} string keys:');
  print('Total permutations to test: ${extremeWhitespaceCatalog.length * stringKeys.length}\n');

  int totalTests = 0;
  int rejectedCount = 0;
  int acceptedCount = 0;
  final acceptedCases = <String>[];

  for (final entry in extremeWhitespaceCatalog.entries) {
    final label = entry.key;
    final wsChar = entry.value;

    // First, inspect what Dart's trim() does to this character:
    final trimmed = wsChar.trim();
    final isTrimmedEmpty = trimmed.isEmpty;
    print('Testing vector: "$label" | raw codepoints: ${wsChar.runes.map((r) => 'U+${r.toRadixString(16).padLeft(4, '0').toUpperCase()}').join(' ')} | .trim().isEmpty == $isTrimmedEmpty');

    for (final key in stringKeys) {
      totalTests++;
      final testMap = Map<String, dynamic>.from(validAuthMap)..[key] = wsChar;

      try {
        final tuple = Auth7Tuple.fromMap(testMap);
        // Accepted!
        acceptedCount++;
        final detail = '$key with "$label" (codepoints: ${wsChar.runes.map((r) => 'U+${r.toRadixString(16).padLeft(4, '0').toUpperCase()}').join(' ')})';
        acceptedCases.add(detail);
      } on ArgumentError {
        rejectedCount++;
      } catch (e) {
        print('  Unexpected exception for $key with "$label": $e');
      }
    }
  }

  print('\n===============================================================');
  print('EXTREME FUZZING RESULTS:');
  print('Total tests: $totalTests');
  print('Rejected with ArgumentError: $rejectedCount');
  print('Accepted without error: $acceptedCount');
  if (acceptedCases.isNotEmpty) {
    print('Cases accepted:');
    for (final c in acceptedCases) {
      print('  - $c');
    }
  }
  print('===============================================================');
}
