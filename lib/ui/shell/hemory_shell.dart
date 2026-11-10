/// Root 4-tab Obsidian shell & zero-fluff Server Connect gate.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/providers/app_providers.dart';
import '../../core/version.dart';
import '../chat/ask_scene.dart';
import '../people/people_scene.dart';
import '../settings/settings_scene.dart';
import '../theme/hemory_theme.dart';
import '../timeline/memory_timeline_scene.dart';

class HemoryShell extends ConsumerStatefulWidget {
  const HemoryShell({super.key});

  @override
  ConsumerState<HemoryShell> createState() => _HemoryShellState();
}

class _HemoryShellState extends ConsumerState<HemoryShell> {
  int _selectedIndex = 0;

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authSessionProvider);
    if (!auth.initialized) {
      return const Scaffold(
        backgroundColor: HemoryColors.darkBg,
        body: Center(
          child: SizedBox(
            width: 24.0,
            height: 24.0,
            child: CircularProgressIndicator(
              strokeWidth: 2.0,
              color: HemoryColors.sagePrimary,
            ),
          ),
        ),
      );
    }

    if (!auth.isAuthenticated) {
      return const _ServerConnectGate();
    }

    final pendingCount =
        ref.watch(pendingClustersProvider).valueOrNull?.length ?? 0;

    return Scaffold(
      backgroundColor: HemoryColors.darkBg,
      body: IndexedStack(
        index: _selectedIndex,
        children: const [
          MemoryTimelineScene(),
          PeopleScene(),
          AskScene(),
          SettingsScene(),
        ],
      ),
      bottomNavigationBar: Container(
        decoration: const BoxDecoration(
          border: Border(
            top: BorderSide(color: HemoryColors.darkBorder, width: 0.8),
          ),
        ),
        child: NavigationBar(
          height: 62.0,
          backgroundColor: HemoryColors.darkSurface1,
          indicatorColor: HemoryColors.sagePrimary.withValues(alpha: 0.16),
          selectedIndex: _selectedIndex,
          onDestinationSelected: (idx) {
            setState(() => _selectedIndex = idx);
          },
          destinations: [
            const NavigationDestination(
              icon: Icon(Icons.schedule_rounded),
              selectedIcon: Icon(
                Icons.schedule_rounded,
                color: HemoryColors.sageSecondary,
              ),
              label: '时间线',
            ),
            NavigationDestination(
              icon: Badge(
                isLabelVisible: pendingCount > 0,
                label: Text('$pendingCount'),
                child: const Icon(Icons.record_voice_over_outlined),
              ),
              selectedIcon: Badge(
                isLabelVisible: pendingCount > 0,
                label: Text('$pendingCount'),
                child: const Icon(
                  Icons.record_voice_over_rounded,
                  color: HemoryColors.sageSecondary,
                ),
              ),
              label: '声纹',
            ),
            const NavigationDestination(
              icon: Icon(Icons.chat_bubble_outline_rounded),
              selectedIcon: Icon(
                Icons.chat_bubble_rounded,
                color: HemoryColors.sageSecondary,
              ),
              label: '问答',
            ),
            const NavigationDestination(
              icon: Icon(Icons.tune_rounded),
              selectedIcon: Icon(
                Icons.tune_rounded,
                color: HemoryColors.sageSecondary,
              ),
              label: '设置',
            ),
          ],
        ),
      ),
    );
  }
}

class _ServerConnectGate extends ConsumerStatefulWidget {
  const _ServerConnectGate();

  @override
  ConsumerState<_ServerConnectGate> createState() => _ServerConnectGateState();
}

class _ServerConnectGateState extends ConsumerState<_ServerConnectGate> {
  final TextEditingController _urlCtrl =
      TextEditingController(text: kDefaultServerUrl);
  final TextEditingController _tokenCtrl = TextEditingController();

  @override
  void dispose() {
    _urlCtrl.dispose();
    _tokenCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authSessionProvider);
    final notifier = ref.read(authSessionProvider.notifier);

    return Scaffold(
      backgroundColor: HemoryColors.darkBg,
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 380.0),
            child: Padding(
              padding: const EdgeInsets.all(24.0),
              child: Container(
                padding: const EdgeInsets.all(20.0),
                decoration: HemoryTheme.frostedBoxDecoration(radius: 18.0),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      'Hemory',
                      style: TextStyle(
                        fontSize: 22.0,
                        fontWeight: FontWeight.w700,
                        letterSpacing: -0.5,
                        color: HemoryColors.textLightPrimary,
                      ),
                    ),
                    const SizedBox(height: 18.0),
                    TextField(
                      controller: _urlCtrl,
                      style: const TextStyle(
                        fontSize: 14.0,
                        color: HemoryColors.textLightPrimary,
                      ),
                      decoration: InputDecoration(
                        hintText: kDefaultServerUrl,
                        hintStyle:
                            const TextStyle(color: HemoryColors.textLightMuted),
                        filled: true,
                        fillColor: HemoryColors.darkSurface2,
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 14.0,
                          vertical: 12.0,
                        ),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12.0),
                          borderSide: BorderSide.none,
                        ),
                      ),
                    ),
                    const SizedBox(height: 10.0),
                    TextField(
                      controller: _tokenCtrl,
                      obscureText: true,
                      style: const TextStyle(
                        fontSize: 14.0,
                        color: HemoryColors.textLightPrimary,
                      ),
                      onSubmitted: (_) {
                        notifier.connect(
                          baseUrl: _urlCtrl.text,
                          accessToken: _tokenCtrl.text,
                        );
                      },
                      decoration: InputDecoration(
                        hintText: 'Token',
                        hintStyle:
                            const TextStyle(color: HemoryColors.textLightMuted),
                        filled: true,
                        fillColor: HemoryColors.darkSurface2,
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 14.0,
                          vertical: 12.0,
                        ),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12.0),
                          borderSide: BorderSide.none,
                        ),
                      ),
                    ),
                    if (auth.errorMessage != null) ...[
                      const SizedBox(height: 10.0),
                      Text(
                        auth.errorMessage!,
                        style: const TextStyle(
                          fontSize: 12.0,
                          color: HemoryColors.liveCoral,
                        ),
                      ),
                    ],
                    const SizedBox(height: 14.0),
                    FilledButton(
                      style: FilledButton.styleFrom(
                        backgroundColor: HemoryColors.sagePrimary,
                        foregroundColor: Colors.black,
                        padding: const EdgeInsets.symmetric(vertical: 12.0),
                      ),
                      onPressed: auth.isConnecting
                          ? null
                          : () {
                              notifier.connect(
                                baseUrl: _urlCtrl.text,
                                accessToken: _tokenCtrl.text,
                              );
                            },
                      child: Text(auth.isConnecting ? '连接中…' : '连接'),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
