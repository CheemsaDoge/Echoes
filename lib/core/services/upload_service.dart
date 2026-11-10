/// Service coordinating multipart audio uploads adhering to Contract C2.
library;

import 'dart:convert';
import 'package:http/http.dart' as http;
import '../db/local_database.dart';

class UploadResult {
  final bool success;
  final int statusCode;
  final String? recordingId;
  final String? assetId;
  final String? errorCode;

  const UploadResult({
    required this.success,
    required this.statusCode,
    this.recordingId,
    this.assetId,
    this.errorCode,
  });
}

class UploadService {
  final String baseUrl;
  final http.Client _client;

  UploadService({
    required this.baseUrl,
    http.Client? client,
  }) : _client = client ?? http.Client();

  /// Executes multipart audio upload per Contract C2.
  /// 6 fields: started_at, format, source, capture_timezone, capture_metadata, file.
  Future<UploadResult> uploadAudioSegment({
    required String tenantId,
    required String vaultId,
    required String authToken,
    required String startedAtIso,
    required String source,
    required String timezone,
    required Map<String, dynamic> metadata,
    required List<int> audioBytes,
    required String filename,
  }) async {
    final uri = Uri.parse('$baseUrl/tenants/$tenantId/vaults/$vaultId/recordings/upload');
    final request = http.MultipartRequest('POST', uri);

    request.headers['Authorization'] = 'Bearer $authToken';

    // 6 Multipart Fields
    request.fields['started_at'] = startedAtIso;
    request.fields['format'] = 'aac';
    request.fields['source'] = source;
    request.fields['capture_timezone'] = timezone;
    request.fields['capture_metadata'] = jsonEncode(metadata);

    request.files.add(
      http.MultipartFile.fromBytes(
        'file',
        audioBytes,
        filename: filename,
      ),
    );

    try {
      final streamedResponse = await _client.send(request);
      final response = await http.Response.fromStream(streamedResponse);

      if (response.statusCode == 201) {
        final body = jsonDecode(response.body) as Map<String, dynamic>;
        return UploadResult(
          success: true,
          statusCode: 201,
          recordingId: body['recording_id'] as String?,
          assetId: body['asset_id'] as String?,
        );
      } else {
        String? errCode;
        try {
          final errBody = jsonDecode(response.body) as Map<String, dynamic>;
          errCode = errBody['detail'] as String? ?? errBody['error_code'] as String?;
        } catch (_) {}

        return UploadResult(
          success: false,
          statusCode: response.statusCode,
          errorCode: errCode ?? 'HTTP_${response.statusCode}',
        );
      }
    } catch (e) {
      return UploadResult(
        success: false,
        statusCode: 0,
        errorCode: 'NETWORK_ERROR: $e',
      );
    }
  }

  void dispose() {
    _client.close();
  }
}
