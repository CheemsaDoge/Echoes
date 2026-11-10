/// Conversational long-memory chat viewport streaming SSE turns.
/// Conforms to Contract C4 and applies Contract C6 inline speaker tag replacer.
library;

import 'dart:ui';
import 'package:flutter/material.dart';
import '../../core/models/speaker_models.dart';
import '../../core/services/sse_chat_service.dart';
import '../theme/hemory_theme.dart';
import 'speaker_resolver.dart';

class ChatMessageItem {
  final String id;
  final String sender; // 'user' | 'assistant' | 'tool'
  final String text;
  final String? toolName;
  final Map<String, dynamic>? toolArgs;
  final bool isStreaming;

  const ChatMessageItem({
    required this.id,
    required this.sender,
    required this.text,
    this.toolName,
    this.toolArgs,
    this.isStreaming = false,
  });

  ChatMessageItem copyWith({
    String? text,
    bool? isStreaming,
  }) {
    return ChatMessageItem(
      id: id,
      sender: sender,
      text: text ?? this.text,
      toolName: toolName,
      toolArgs: toolArgs,
      isStreaming: isStreaming ?? this.isStreaming,
    );
  }
}

class SseChatView extends StatefulWidget {
  final String threadId;
  final String authToken;
  final String baseUrl;
  final SessionSpeakerResolver? speakerResolver;

  const SseChatView({
    super.key,
    required this.threadId,
    required this.authToken,
    required this.baseUrl,
    this.speakerResolver,
  });

  @override
  State<SseChatView> createState() => _SseChatViewState();
}

class _SseChatViewState extends State<SseChatView> {
  final List<ChatMessageItem> _messages = [];
  final TextEditingController _textController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  late final SseChatService _chatService;
  late final SessionSpeakerResolver _resolver;
  bool _isSending = false;

  @override
  void initState() {
    super.initState();
    _chatService = SseChatService(baseUrl: widget.baseUrl);
    _resolver = widget.speakerResolver ?? SessionSpeakerResolver();
  }

  @override
  void dispose() {
    _chatService.dispose();
    _textController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _sendMessage() async {
    final prompt = _textController.text.trim();
    if (prompt.isEmpty || _isSending) return;

    _textController.clear();
    setState(() {
      _isSending = true;
      _messages.add(
        ChatMessageItem(
          id: 'user_${DateTime.now().millisecondsSinceEpoch}',
          sender: 'user',
          text: prompt,
        ),
      );
    });
    _scrollToBottom();

    final assistantMsgId = 'asst_${DateTime.now().millisecondsSinceEpoch}';
    final assistantMsg = ChatMessageItem(
      id: assistantMsgId,
      sender: 'assistant',
      text: '',
      isStreaming: true,
    );

    setState(() {
      _messages.add(assistantMsg);
    });
    _scrollToBottom();

    final buffer = StringBuffer();

    try {
      final stream = _chatService.sendChatTurn(
        threadId: widget.threadId,
        prompt: prompt,
        authToken: widget.authToken,
      );

      await for (final event in stream) {
        if (event.event == 'text_delta') {
          final delta = event.data['delta'] as String? ?? '';
          buffer.write(delta);
          final rawText = buffer.toString();
          final resolvedText = _resolver.replaceSpeakerTags(rawText);

          setState(() {
            final idx = _messages.indexWhere((m) => m.id == assistantMsgId);
            if (idx != -1) {
              _messages[idx] = _messages[idx].copyWith(text: resolvedText);
            }
          });
          _scrollToBottom();
        } else if (event.event == 'tool_call_started') {
          final tool = (event.data['tool_name'] ?? event.data['tool']) as String? ?? 'tool';
          final args = (event.data['arguments'] ?? event.data['args']) as Map<String, dynamic>?;
          setState(() {
            _messages.insert(
              _messages.length - 1,
              ChatMessageItem(
                id: 'tool_${DateTime.now().millisecondsSinceEpoch}',
                sender: 'tool',
                text: 'Calling $tool...',
                toolName: tool,
                toolArgs: args,
              ),
            );
          });
        } else if (event.event == 'turn_completed') {
          setState(() {
            final idx = _messages.indexWhere((m) => m.id == assistantMsgId);
            if (idx != -1) {
              _messages[idx] = _messages[idx].copyWith(isStreaming: false);
            }
            _isSending = false;
          });
        }
      }
    } catch (e) {
      setState(() {
        final idx = _messages.indexWhere((m) => m.id == assistantMsgId);
        if (idx != -1) {
          _messages[idx] = _messages[idx].copyWith(
            text: buffer.isNotEmpty
                ? '${buffer.toString()}\n[Error: $e]'
                : '[Error connecting to chat service: $e]',
            isStreaming: false,
          );
        }
        _isSending = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: HemoryColors.darkBg,
      appBar: AppBar(
        backgroundColor: HemoryColors.darkGlassTint,
        title: const Text(
          'Memory Assistant',
          style: TextStyle(
            fontSize: 16.0,
            fontWeight: FontWeight.w600,
            letterSpacing: -0.2,
          ),
        ),
        elevation: 0,
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: ListView.builder(
                controller: _scrollController,
                physics: const BouncingScrollPhysics(),
                padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 12.0),
                itemCount: _messages.length,
                itemBuilder: (context, index) {
                  return _buildMessageBubble(_messages[index]);
                },
              ),
            ),
            _buildInputBar(),
          ],
        ),
      ),
    );
  }

  Widget _buildMessageBubble(ChatMessageItem msg) {
    final isUser = msg.sender == 'user';
    final isTool = msg.sender == 'tool';

    if (isTool) {
      return Container(
        margin: const EdgeInsets.symmetric(vertical: 4.0),
        padding: const EdgeInsets.symmetric(horizontal: 10.0, vertical: 6.0),
        decoration: BoxDecoration(
          color: HemoryColors.darkSurface1,
          borderRadius: BorderRadius.circular(8.0),
          border: Border.all(color: HemoryColors.darkBorder, width: 0.6),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.build_outlined, size: 12.0, color: HemoryColors.sageSecondary),
            const SizedBox(width: 6.0),
            Text(
              msg.toolName ?? 'Tool invocation',
              style: const TextStyle(
                fontSize: 11.0,
                color: HemoryColors.textLightSecondary,
              ),
            ),
          ],
        ),
      );
    }

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 6.0),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.78,
        ),
        padding: const EdgeInsets.symmetric(horizontal: 14.0, vertical: 10.0),
        decoration: BoxDecoration(
          color: isUser ? HemoryColors.sagePrimary : HemoryColors.darkSurface2,
          borderRadius: BorderRadius.circular(16.0),
        ),
        child: Text(
          msg.text,
          style: TextStyle(
            fontSize: 14.0,
            height: 1.4,
            color: isUser ? Colors.black : HemoryColors.textLightPrimary,
          ),
        ),
      ),
    );
  }

  Widget _buildInputBar() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
      decoration: const BoxDecoration(
        color: HemoryColors.darkSurface1,
        border: Border(
          top: BorderSide(color: HemoryColors.darkBorder, width: 0.8),
        ),
      ),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _textController,
              onSubmitted: (_) => _sendMessage(),
              style: const TextStyle(color: HemoryColors.textLightPrimary, fontSize: 14.0),
              decoration: const InputDecoration(
                hintText: 'Ask your memory...',
                hintStyle: TextStyle(color: HemoryColors.textLightMuted, fontSize: 14.0),
                border: InputBorder.none,
              ),
            ),
          ),
          IconButton(
            icon: Icon(
              Icons.send_rounded,
              color: _isSending ? HemoryColors.textLightMuted : HemoryColors.sagePrimary,
              size: 20.0,
            ),
            onPressed: _isSending ? null : _sendMessage,
          ),
        ],
      ),
    );
  }
}
