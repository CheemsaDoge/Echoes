/// Client service for Server-Sent Events (SSE) chat turns conforming to Contract C4.
library;

import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;

class ChatTurnEvent {
  final String? id;
  final String event;
  final Map<String, dynamic> data;

  const ChatTurnEvent({
    this.id,
    required this.event,
    required this.data,
  });
}

class SseChatService {
  final String baseUrl;
  final http.Client _client;

  SseChatService({
    required this.baseUrl,
    http.Client? client,
  }) : _client = client ?? http.Client();

  /// Initiates an SSE chat turn stream.
  Stream<ChatTurnEvent> sendChatTurn({
    required String threadId,
    required String prompt,
    required String authToken,
    String tenantId = 'ten_default',
    String vaultId = 'vlt_default',
    String userId = 'usr_default',
    String? lastEventId,
    Map<String, dynamic>? clientContext,
  }) async* {
    final uri = Uri.parse('$baseUrl/chat/threads/$threadId/turns');
    final request = http.Request('POST', uri);

    request.headers['Authorization'] = 'Bearer $authToken';
    request.headers['Content-Type'] = 'application/json';
    request.headers['Accept'] = 'text/event-stream';
    request.headers['x-tenant-id'] = tenantId;
    request.headers['x-vault-id'] = vaultId;
    request.headers['x-user-id'] = userId;
    if (lastEventId != null) {
      request.headers['Last-Event-ID'] = lastEventId;
    }

    request.body = jsonEncode({
      'prompt': prompt,
      if (clientContext != null) 'client_context': clientContext,
    });

    final response = await _client.send(request);
    final stream = response.stream.transform(utf8.decoder).transform(const LineSplitter());

    String? currentId;
    String currentEvent = 'message';
    final currentData = StringBuffer();

    await for (final line in stream) {
      if (line.isEmpty) {
        if (currentData.isNotEmpty) {
          Map<String, dynamic> parsedData = {};
          try {
            parsedData = jsonDecode(currentData.toString()) as Map<String, dynamic>;
          } catch (_) {
            parsedData = {'raw': currentData.toString()};
          }

          yield ChatTurnEvent(
            id: currentId,
            event: currentEvent,
            data: parsedData,
          );

          currentData.clear();
          currentEvent = 'message';
        }
      } else if (line.startsWith('id:')) {
        currentId = line.substring(3).trim();
      } else if (line.startsWith('event:')) {
        currentEvent = line.substring(6).trim();
      } else if (line.startsWith('data:')) {
        final dataPart = line.substring(5).trim();
        if (currentData.isNotEmpty) currentData.write('\n');
        currentData.write(dataPart);
      }
    }
  }

  void dispose() {
    _client.close();
  }
}
