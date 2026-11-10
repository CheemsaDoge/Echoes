/// Voiceprint cluster review & enrolled speaker directory scene.
/// Layout-first design: zero helper copy, direct audio preview, and 1-tap assignment.
library;

import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/models/speaker_models.dart';
import '../../core/providers/app_providers.dart';
import '../../core/services/hemory_api_client.dart';
import '../theme/hemory_theme.dart';

class PeopleScene extends ConsumerStatefulWidget {
  const PeopleScene({super.key});

  @override
  ConsumerState<PeopleScene> createState() => _PeopleSceneState();
}

class _PeopleSceneState extends ConsumerState<PeopleScene> {
  final Map<String, TextEditingController> _nameControllers = {};

  @override
  void dispose() {
    for (final c in _nameControllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  TextEditingController _controllerFor(String clusterId) {
    return _nameControllers.putIfAbsent(clusterId, TextEditingController.new);
  }

  Future<void> _refresh() async {
    ref.read(speakersRefreshTickProvider.notifier).state++;
    await Future.wait([
      ref.read(pendingClustersProvider.future).catchError((_) => <VoiceprintClusterItem>[]),
      ref.read(speakersProvider.future).catchError((_) => <SpeakerProfile>[]),
    ]);
  }

  Future<void> _assignNewSpeaker(String clusterId) async {
    final name = _controllerFor(clusterId).text.trim();
    if (name.isEmpty) return;
    final client = ref.read(apiClientProvider);
    if (client == null) return;
    await client.patchCluster(
      clusterId: clusterId,
      action: 'mark_new',
      name: name,
    );
    _nameControllers.remove(clusterId)?.dispose();
    ref.read(speakersRefreshTickProvider.notifier).state++;
    ref.read(timelineRefreshTickProvider.notifier).state++;
  }

  Future<void> _mergeIntoSpeaker(String clusterId, String speakerId) async {
    final client = ref.read(apiClientProvider);
    if (client == null) return;
    await client.patchCluster(
      clusterId: clusterId,
      action: 'mark_existing',
      speakerId: speakerId,
    );
    _nameControllers.remove(clusterId)?.dispose();
    ref.read(speakersRefreshTickProvider.notifier).state++;
    ref.read(timelineRefreshTickProvider.notifier).state++;
  }

  Future<void> _dismissCluster(String clusterId) async {
    final client = ref.read(apiClientProvider);
    if (client == null) return;
    await client.patchCluster(
      clusterId: clusterId,
      action: 'dismiss',
    );
    _nameControllers.remove(clusterId)?.dispose();
    ref.read(speakersRefreshTickProvider.notifier).state++;
    ref.read(timelineRefreshTickProvider.notifier).state++;
  }

  Future<void> _showCreateSpeakerDialog() async {
    final ctrl = TextEditingController();
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: HemoryColors.darkSurface1,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20.0)),
      ),
      builder: (ctx) {
        return Padding(
          padding: EdgeInsets.fromLTRB(
            20.0,
            20.0,
            20.0,
            MediaQuery.of(ctx).viewInsets.bottom + 20.0,
          ),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: ctrl,
                  autofocus: true,
                  style: const TextStyle(
                    color: HemoryColors.textLightPrimary,
                    fontSize: 15.0,
                  ),
                  decoration: InputDecoration(
                    hintText: '姓名',
                    hintStyle: const TextStyle(color: HemoryColors.textLightMuted),
                    filled: true,
                    fillColor: HemoryColors.darkSurface2,
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 14.0,
                      vertical: 11.0,
                    ),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10.0),
                      borderSide: BorderSide.none,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 10.0),
              FilledButton(
                style: FilledButton.styleFrom(
                  backgroundColor: HemoryColors.sagePrimary,
                  foregroundColor: Colors.black,
                ),
                onPressed: () async {
                  final name = ctrl.text.trim();
                  if (name.isEmpty) return;
                  final client = ref.read(apiClientProvider);
                  if (client == null) return;
                  await client.createSpeaker(name: name);
                  ref.read(speakersRefreshTickProvider.notifier).state++;
                  if (ctx.mounted) Navigator.of(ctx).pop();
                },
                child: const Text('添加'),
              ),
            ],
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final pendingAsync = ref.watch(pendingClustersProvider);
    final speakersAsync = ref.watch(speakersProvider);
    final activeAudioUrl = ref.watch(activeAudioUrlProvider);
    final client = ref.watch(apiClientProvider);

    final pending = pendingAsync.valueOrNull ?? const <VoiceprintClusterItem>[];
    final speakers = speakersAsync.valueOrNull ?? const <SpeakerProfile>[];

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
                            '声纹',
                            style: TextStyle(
                              fontSize: 17.0,
                              fontWeight: FontWeight.w700,
                              letterSpacing: -0.3,
                              color: HemoryColors.textLightPrimary,
                            ),
                          ),
                          if (pending.isNotEmpty) ...[
                            const SizedBox(width: 8.0),
                            Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 7.0,
                                vertical: 2.0,
                              ),
                              decoration: BoxDecoration(
                                color: const Color(0x26F59E0B),
                                borderRadius: BorderRadius.circular(10.0),
                              ),
                              child: Text(
                                '${pending.length}',
                                style: const TextStyle(
                                  fontSize: 11.0,
                                  fontWeight: FontWeight.w700,
                                  color: Color(0xFFFBBF24),
                                  fontFeatures: [FontFeature.tabularFigures()],
                                ),
                              ),
                            ),
                          ],
                          const Spacer(),
                          IconButton(
                            icon: const Icon(
                              Icons.person_add_alt_1_rounded,
                              size: 20.0,
                              color: HemoryColors.sagePrimary,
                            ),
                            onPressed: _showCreateSpeakerDialog,
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                Expanded(
                  child: RefreshIndicator(
                    color: HemoryColors.sagePrimary,
                    backgroundColor: HemoryColors.darkSurface1,
                    onRefresh: _refresh,
                    child: (pending.isEmpty && speakers.isEmpty)
                        ? ListView(
                            physics: const AlwaysScrollableScrollPhysics(
                              parent: BouncingScrollPhysics(),
                            ),
                            children: [
                              SizedBox(
                                height: 340.0,
                                child: Center(
                                  child: Icon(
                                    Icons.record_voice_over_outlined,
                                    size: 36.0,
                                    color: HemoryColors.textLightMuted
                                        .withValues(alpha: 0.35),
                                  ),
                                ),
                              ),
                            ],
                          )
                        : ListView(
                            physics: const AlwaysScrollableScrollPhysics(
                              parent: BouncingScrollPhysics(),
                            ),
                            padding: const EdgeInsets.all(16.0),
                            children: [
                              ...pending.map(
                                (cluster) => _buildPendingClusterCard(
                                  cluster: cluster,
                                  speakers: speakers,
                                  activeAudioUrl: activeAudioUrl,
                                  client: client,
                                ),
                              ),
                              if (pending.isNotEmpty && speakers.isNotEmpty)
                                const Padding(
                                  padding: EdgeInsets.symmetric(vertical: 8.0),
                                  child: Divider(
                                    color: HemoryColors.darkBorder,
                                    height: 1.0,
                                  ),
                                ),
                              ...speakers.map(_buildSpeakerTile),
                            ],
                          ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildPendingClusterCard({
    required VoiceprintClusterItem cluster,
    required List<SpeakerProfile> speakers,
    required String? activeAudioUrl,
    required HemoryApiClient? client,
  }) {
    final previewRel =
        cluster.audioSampleUrl ?? '/clusters/${cluster.clusterId}/audio_preview';
    final fullAudio = client?.resolveUrl(previewRel);
    final isPlaying = fullAudio != null && activeAudioUrl == fullAudio;
    final ctrl = _controllerFor(cluster.clusterId);

    return Container(
      margin: const EdgeInsets.only(bottom: 12.0),
      padding: const EdgeInsets.all(14.0),
      decoration: HemoryTheme.frostedBoxDecoration(
        borderColor: isPlaying
            ? HemoryColors.sagePrimary
            : const Color(0x55F59E0B),
        radius: 14.0,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              GestureDetector(
                onTap: () => toggleAudioPreview(ref, previewRel),
                child: Container(
                  width: 34.0,
                  height: 34.0,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: isPlaying
                        ? HemoryColors.liveCoral.withValues(alpha: 0.18)
                        : HemoryColors.sagePrimary.withValues(alpha: 0.16),
                  ),
                  child: Icon(
                    isPlaying ? Icons.stop_rounded : Icons.play_arrow_rounded,
                    size: 20.0,
                    color: isPlaying
                        ? HemoryColors.liveCoral
                        : HemoryColors.sagePrimary,
                  ),
                ),
              ),
              const SizedBox(width: 10.0),
              Expanded(
                child: Text(
                  '×${cluster.occurrencesCount}',
                  style: const TextStyle(
                    fontSize: 13.0,
                    fontWeight: FontWeight.w600,
                    color: HemoryColors.textLightPrimary,
                    fontFeatures: [FontFeature.tabularFigures()],
                  ),
                ),
              ),
              IconButton(
                visualDensity: VisualDensity.compact,
                icon: const Icon(
                  Icons.close_rounded,
                  size: 18.0,
                  color: HemoryColors.textLightMuted,
                ),
                onPressed: () => _dismissCluster(cluster.clusterId),
              ),
            ],
          ),
          const SizedBox(height: 10.0),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: ctrl,
                  style: const TextStyle(
                    fontSize: 14.0,
                    color: HemoryColors.textLightPrimary,
                  ),
                  onSubmitted: (_) => _assignNewSpeaker(cluster.clusterId),
                  decoration: InputDecoration(
                    hintText: '姓名',
                    hintStyle: const TextStyle(color: HemoryColors.textLightMuted),
                    filled: true,
                    fillColor: HemoryColors.darkSurface2,
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 12.0,
                      vertical: 10.0,
                    ),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10.0),
                      borderSide: BorderSide.none,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8.0),
              FilledButton(
                style: FilledButton.styleFrom(
                  backgroundColor: HemoryColors.sagePrimary,
                  foregroundColor: Colors.black,
                  padding: const EdgeInsets.symmetric(horizontal: 14.0),
                ),
                onPressed: () => _assignNewSpeaker(cluster.clusterId),
                child: const Text('确认'),
              ),
            ],
          ),
          if (speakers.isNotEmpty) ...[
            const SizedBox(height: 10.0),
            Wrap(
              spacing: 6.0,
              runSpacing: 6.0,
              children: speakers.map((spk) {
                return GestureDetector(
                  onTap: () => _mergeIntoSpeaker(cluster.clusterId, spk.speakerId),
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10.0,
                      vertical: 5.0,
                    ),
                    decoration: BoxDecoration(
                      color: HemoryColors.darkSurface2,
                      borderRadius: BorderRadius.circular(8.0),
                      border: Border.all(
                        color: HemoryColors.darkBorder,
                        width: 0.6,
                      ),
                    ),
                    child: Text(
                      spk.name,
                      style: const TextStyle(
                        fontSize: 12.0,
                        color: HemoryColors.textLightPrimary,
                      ),
                    ),
                  ),
                );
              }).toList(),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildSpeakerTile(SpeakerProfile spk) {
    final initial =
        spk.name.isNotEmpty ? spk.name.substring(0, 1).toUpperCase() : '?';
    return Container(
      margin: const EdgeInsets.only(bottom: 8.0),
      padding: const EdgeInsets.symmetric(horizontal: 14.0, vertical: 11.0),
      decoration: BoxDecoration(
        color: HemoryColors.darkSurface1,
        borderRadius: BorderRadius.circular(12.0),
        border: Border.all(color: HemoryColors.darkBorder, width: 0.6),
      ),
      child: Row(
        children: [
          CircleAvatar(
            radius: 15.0,
            backgroundColor: HemoryColors.darkSurface2,
            child: Text(
              initial,
              style: const TextStyle(
                fontSize: 13.0,
                fontWeight: FontWeight.w700,
                color: HemoryColors.sageSecondary,
              ),
            ),
          ),
          const SizedBox(width: 12.0),
          Expanded(
            child: Text(
              spk.name,
              style: const TextStyle(
                fontSize: 15.0,
                fontWeight: FontWeight.w600,
                color: HemoryColors.textLightPrimary,
              ),
            ),
          ),
          if (spk.roleLabel != null && spk.roleLabel!.trim().isNotEmpty)
            Text(
              spk.roleLabel!,
              style: const TextStyle(
                fontSize: 12.0,
                color: HemoryColors.textLightMuted,
              ),
            ),
        ],
      ),
    );
  }
}
