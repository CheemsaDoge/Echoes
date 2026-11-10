/// Memory Q&A Scene hosting multi-thread SSE retrieval chat with speaker resolution.
library;

import 'dart:math';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/models/speaker_models.dart';
import '../../core/providers/app_providers.dart';
import '../../core/services/hemory_api_client.dart';
import '../theme/hemory_theme.dart';
import 'speaker_resolver.dart';
import 'sse_chat_view.dart';

class AskScene extends ConsumerStatefulWidget {
  const AskScene({super.key});

  @override
  ConsumerState<AskScene> createState() => _AskSceneState();
}

class _AskSceneState extends ConsumerState<AskScene> {
  List<ChatThreadSummary> _threads = const [];
  String _activeThreadId = '';
  List<ChatMessageItem> _history = const [];
  bool _loadingHistory = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _loadThreads();
    });
  }

  String _generateLocalThreadId() {
    return 'th_${DateTime.now().millisecondsSinceEpoch}_${Random().nextInt(9999)}';
  }

  Future<void> _loadThreads() async {
    final client = ref.read(apiClientProvider);
    if (client == null) return;
    try {
      final list = await client.fetchChatThreads();
      if (!mounted) return;
      if (list.isNotEmpty) {
        setState(() => _threads = list);
        if (_activeThreadId.isEmpty) {
          await _selectThread(list.first.threadId);
        }
      } else if (_activeThreadId.isEmpty) {
        setState(() => _activeThreadId = _generateLocalThreadId());
      }
    } catch (_) {
      if (mounted && _activeThreadId.isEmpty) {
        setState(() => _activeThreadId = _generateLocalThreadId());
      }
    }
  }

  Future<void> _selectThread(String threadId) async {
    final client = ref.read(apiClientProvider);
    setState(() {
      _activeThreadId = threadId;
      _loadingHistory = true;
    });
    if (client == null) {
      setState(() => _loadingHistory = false);
      return;
    }
    try {
      final turns = await client.fetchChatTurns(threadId);
      if (!mounted) return;
      final spkList = ref.read(speakersProvider).valueOrNull ?? const <SpeakerProfile>[];
      final resolver = SessionSpeakerResolver(
        speakerProfiles: {for (final s in spkList) s.speakerId: s},
      );
      final msgs = <ChatMessageItem>[];
      for (final t in turns) {
        if (t.prompt.trim().isNotEmpty) {
          msgs.add(ChatMessageItem(
            id: '${t.turnId}_u',
            sender: 'user',
            text: t.prompt,
          ));
        }
        if (t.responseText.trim().isNotEmpty) {
          msgs.add(ChatMessageItem(
            id: '${t.turnId}_a',
            sender: 'assistant',
            text: resolver.replaceSpeakerTags(t.responseText),
          ));
        }
      }
      setState(() {
        _history = msgs;
        _loadingHistory = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _history = const [];
          _loadingHistory = false;
        });
      }
    }
  }

  void _startNewThread() {
    setState(() {
      _activeThreadId = _generateLocalThreadId();
      _history = const [];
    });
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authSessionProvider);
    final speakers =
        ref.watch(speakersProvider).valueOrNull ?? const <SpeakerProfile>[];
    final resolver = SessionSpeakerResolver(
      speakerProfiles: {for (final s in speakers) s.speakerId: s},
    );

    final effectiveThreadId =
        _activeThreadId.isNotEmpty ? _activeThreadId : _generateLocalThreadId();

    return Scaffold(
      backgroundColor: HemoryColors.darkBg,
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: HemoryTheme.maxWidth),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                ClipRect(
                  child: BackdropFilter(
                    filter: ImageFilter.blur(sigmaX: 18.0, sigmaY: 18.0),
                    child: Container(
                      height: 54.0,
                      padding: const EdgeInsets.symmetric(horizontal: 16.0),
                      decoration: const BoxDecoration(
                        color: HemoryColors.darkGlassTint,
                        border: Border(
                          bottom: BorderSide(
                            color: HemoryColors.darkBorder,
                            width: 0.8,
                          ),
                        ),
                      ),
                      child: Row(
                        children: [
                          const Text(
                            '问答',
                            style: TextStyle(
                              fontSize: 17.0,
                              fontWeight: FontWeight.w700,
                              letterSpacing: -0.3,
                              color: HemoryColors.textLightPrimary,
                            ),
                          ),
                          const SizedBox(width: 12.0),
                          if (_threads.isNotEmpty)
                            Expanded(
                              child: SingleChildScrollView(
                                scrollDirection: Axis.horizontal,
                                child: Row(
                                  children: _threads.take(6).map((th) {
                                    final selected =
                                        th.threadId == effectiveThreadId;
                                    return Padding(
                                      padding: const EdgeInsets.only(right: 6.0),
                                      child: GestureDetector(
                                        onTap: () => _selectThread(th.threadId),
                                        child: Container(
                                          padding: const EdgeInsets.symmetric(
                                            horizontal: 10.0,
                                            vertical: 4.5,
                                          ),
                                          decoration: BoxDecoration(
                                            color: selected
                                                ? HemoryColors.darkSurface2
                                                : Colors.transparent,
                                            borderRadius:
                                                BorderRadius.circular(12.0),
                                            border: Border.all(
                                              color: selected
                                                  ? HemoryColors.sagePrimary
                                                  : HemoryColors.darkBorder,
                                              width: 0.7,
                                            ),
                                          ),
                                          child: Text(
                                            th.title.length > 12
                                                ? '${th.title.substring(0, 12)}…'
                                                : th.title,
                                            style: TextStyle(
                                              fontSize: 11.5,
                                              fontWeight: FontWeight.w500,
                                              color: selected
                                                  ? HemoryColors.textLightPrimary
                                                  : HemoryColors.textLightMuted,
                                            ),
                                          ),
                                        ),
                                      ),
                                    );
                                  }).toList(),
                                ),
                              ),
                            )
                          else
                            const Spacer(),
                          IconButton(
                            icon: const Icon(
                              Icons.add_comment_outlined,
                              size: 19.0,
                              color: HemoryColors.sagePrimary,
                            ),
                            onPressed: _startNewThread,
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                if (_loadingHistory)
                  const LinearProgressIndicator(
                    minHeight: 1.5,
                    backgroundColor: Colors.transparent,
                    color: HemoryColors.sagePrimary,
                  ),
                Expanded(
                  child: SseChatView(
                    key: ValueKey(effectiveThreadId),
                    threadId: effectiveThreadId,
                    authToken: auth.accessToken,
                    baseUrl: auth.baseUrl,
                    tenantId: auth.tenantId,
                    vaultId: auth.vaultId,
                    speakerResolver: resolver,
                    initialMessages: _history,
                    showAppBar: false,
                    onTurnCompleted: _loadThreads,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
