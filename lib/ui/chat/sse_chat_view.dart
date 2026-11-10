/// Conversational long-memory chat viewport streaming SSE turns.
/// Conforms to Contract C4 and applies Contract C6 inline speaker tag replacer.
library;

import 'package:flutter/material.dart';
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
  final String tenantId;
  final String vaultId;
  final SessionSpeakerResolver? speakerResolver;
  final List<ChatMessageItem> initialMessages;
  final bool showAppBar;
  final VoidCallback? onTurnCompleted;

  const SseChatView({
    super.key,
    required this.threadId,
    required this.authToken,
    required this.baseUrl,
    this.tenantId = 'ten_default',
    this.vaultId = 'vlt_default',
    this.speakerResolver,
    this.initialMessages = const [],
    this.showAppBar = true,
    this.onTurnCompleted,
  });

  @override
  State<SseChatView> createState() => _SseChatViewState();
}

class _SseChatViewState extends State<SseChatView> {
  late List<ChatMessageItem> _messages;
  final TextEditingController _textController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  late SseChatService _chatService;
  late SessionSpeakerResolver _resolver;
  bool _isSending = false;

  @override
  void initState() {
    super.initState();
    _messages = List<ChatMessageItem>.from(widget.initialMessages);
    _chatService = SseChatService(baseUrl: widget.baseUrl);
    _resolver = widget.speakerResolver ?? SessionSpeakerResolver();
  }

  @override
  void didUpdateWidget(covariant SseChatView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.baseUrl != widget.baseUrl) {
      _chatService.dispose();
      _chatService = SseChatService(baseUrl: widget.baseUrl);
    }
    if (widget.speakerResolver != null) {
      _resolver = widget.speakerResolver!;
    }
    if (oldWidget.threadId != widget.threadId ||
        (_messages.isEmpty && widget.initialMessages.isNotEmpty)) {
      _messages = List<ChatMessageItem>.from(widget.initialMessages);
    }
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
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _sendMessage() async {
    final prompt = _textController.text.trim();
    if (prompt.isEmpty || _isSending) return;

    _textController.clear();
    final assistantMsgId = 'asst_${DateTime.now().millisecondsSinceEpoch}';

    setState(() {
      _isSending = true;
      _messages.add(
        ChatMessageItem(
          id: 'user_${DateTime.now().millisecondsSinceEpoch}',
          sender: 'user',
          text: prompt,
        ),
      );
      _messages.add(
        ChatMessageItem(
          id: assistantMsgId,
          sender: 'assistant',
          text: '',
          isStreaming: true,
        ),
      );
    });
    _scrollToBottom();

    final buffer = StringBuffer();

    try {
      final stream = _chatService.sendChatTurn(
        threadId: widget.threadId,
        prompt: prompt,
        authToken: widget.authToken,
        tenantId: widget.tenantId,
        vaultId: widget.vaultId,
      );

      await for (final event in stream) {
        if (!mounted) return;
        if (event.event == 'text_delta') {
          final delta = event.data['delta'] as String? ?? '';
          buffer.write(delta);
          final resolvedText = _resolver.replaceSpeakerTags(buffer.toString());

          setState(() {
            final idx = _messages.indexWhere((m) => m.id == assistantMsgId);
            if (idx != -1) {
              _messages[idx] = _messages[idx].copyWith(text: resolvedText);
            }
          });
          _scrollToBottom();
        } else if (event.event == 'tool_call_started') {
          final tool =
              (event.data['tool_name'] ?? event.data['tool']) as String? ?? 'search';
          final args =
              (event.data['arguments'] ?? event.data['args']) as Map<String, dynamic>?;
          setState(() {
            _messages.insert(
              _messages.length - 1,
              ChatMessageItem(
                id: 'tool_${DateTime.now().millisecondsSinceEpoch}',
                sender: 'tool',
                text: tool,
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
          widget.onTurnCompleted?.call();
        } else if (event.event == 'error') {
          final errMsg = event.data['message']?.toString() ?? '请求失败';
          setState(() {
            final idx = _messages.indexWhere((m) => m.id == assistantMsgId);
            if (idx != -1) {
              _messages[idx] = _messages[idx].copyWith(
                text: errMsg,
                isStreaming: false,
              );
            }
            _isSending = false;
          });
        }
      }
      if (mounted && _isSending) {
        setState(() => _isSending = false);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        final idx = _messages.indexWhere((m) => m.id == assistantMsgId);
        if (idx != -1) {
          _messages[idx] = _messages[idx].copyWith(
            text: buffer.isNotEmpty ? '${buffer.toString()}\n[$e]' : '$e',
            isStreaming: false,
          );
        }
        _isSending = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final content = Column(
      children: [
        Expanded(
          child: _messages.isEmpty
              ? Center(
                  child: Icon(
                    Icons.forum_outlined,
                    size: 34.0,
                    color: HemoryColors.textLightMuted.withValues(alpha: 0.35),
                  ),
                )
              : ListView.builder(
                  controller: _scrollController,
                  physics: const BouncingScrollPhysics(),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16.0,
                    vertical: 12.0,
                  ),
                  itemCount: _messages.length,
                  itemBuilder: (context, index) {
                    return _buildMessageBubble(_messages[index]);
                  },
                ),
        ),
        _buildInputBar(),
      ],
    );

    if (!widget.showAppBar) {
      return content;
    }

    return Scaffold(
      backgroundColor: HemoryColors.darkBg,
      appBar: AppBar(
        backgroundColor: HemoryColors.darkGlassTint,
        title: const Text(
          '问答',
          style: TextStyle(
            fontSize: 16.0,
            fontWeight: FontWeight.w600,
            letterSpacing: -0.2,
          ),
        ),
        elevation: 0,
      ),
      body: SafeArea(child: content),
    );
  }

  Widget _buildMessageBubble(ChatMessageItem msg) {
    final isUser = msg.sender == 'user';
    final isTool = msg.sender == 'tool';

    if (isTool) {
      return Align(
        alignment: Alignment.centerLeft,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 3.0),
          padding: const EdgeInsets.symmetric(horizontal: 9.0, vertical: 4.5),
          decoration: BoxDecoration(
            color: HemoryColors.darkSurface1,
            borderRadius: BorderRadius.circular(8.0),
            border: Border.all(color: HemoryColors.darkBorder, width: 0.6),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(
                Icons.manage_search_rounded,
                size: 13.0,
                color: HemoryColors.sageSecondary,
              ),
              const SizedBox(width: 5.0),
              Text(
                msg.toolName ?? 'search',
                style: const TextStyle(
                  fontSize: 11.0,
                  color: HemoryColors.textLightSecondary,
                ),
              ),
            ],
          ),
        ),
      );
    }

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 5.0),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.80,
        ),
        padding: const EdgeInsets.symmetric(horizontal: 14.0, vertical: 10.0),
        decoration: BoxDecoration(
          color: isUser ? HemoryColors.sagePrimary : HemoryColors.darkSurface2,
          borderRadius: BorderRadius.circular(16.0),
        ),
        child: msg.text.isEmpty && msg.isStreaming
            ? const SizedBox(
                width: 16.0,
                height: 16.0,
                child: CircularProgressIndicator(
                  strokeWidth: 1.8,
                  color: HemoryColors.sagePrimary,
                ),
              )
            : Text(
                msg.text,
                style: TextStyle(
                  fontSize: 14.0,
                  height: 1.44,
                  color: isUser ? Colors.black : HemoryColors.textLightPrimary,
                ),
              ),
      ),
    );
  }

  Widget _buildInputBar() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14.0, vertical: 8.0),
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
              style: const TextStyle(
                color: HemoryColors.textLightPrimary,
                fontSize: 14.0,
              ),
              decoration: const InputDecoration(
                hintText: '提问…',
                hintStyle: TextStyle(
                  color: HemoryColors.textLightMuted,
                  fontSize: 14.0,
                ),
                border: InputBorder.none,
              ),
            ),
          ),
          IconButton(
            icon: Icon(
              Icons.arrow_upward_rounded,
              color: _isSending
                  ? HemoryColors.textLightMuted
                  : HemoryColors.sagePrimary,
              size: 20.0,
            ),
            onPressed: _isSending ? null : _sendMessage,
          ),
        ],
      ),
    );
  }
}
