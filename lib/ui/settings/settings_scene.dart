/// Settings & Dedicated Server Control Scene.
/// Zero-tagline layout: server auth, upload queue recovery, and runtime AI config.
library;

import 'dart:async';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/ipc/native_bridge.dart';
import '../../core/providers/app_providers.dart';
import '../../core/version.dart';
import '../theme/hemory_theme.dart';

class SettingsScene extends ConsumerStatefulWidget {
  const SettingsScene({super.key});

  @override
  ConsumerState<SettingsScene> createState() => _SettingsSceneState();
}

class _SettingsSceneState extends ConsumerState<SettingsScene> {
  late final TextEditingController _urlController;
  late final TextEditingController _tokenController;
  Map<String, int> _queueStats = const {
    'pending': 0,
    'uploading': 0,
    'failed': 0,
    'uploaded': 0,
  };
  String _asrProvider = 'vertexai';
  String _asrModel = 'gemini-3.8-flash';
  double _similarityThreshold = 0.72;
  bool _autoMatchEnabled = true;
  bool _configLoaded = false;
  bool _savingConfig = false;
  Timer? _statsTimer;

  @override
  void initState() {
    super.initState();
    final auth = ref.read(authSessionProvider);
    _urlController = TextEditingController(text: auth.baseUrl);
    _tokenController = TextEditingController(text: auth.accessToken);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _refreshStatus();
      _statsTimer = Timer.periodic(
        const Duration(seconds: 4),
        (_) => _refreshQueueOnly(),
      );
    });
  }

  @override
  void dispose() {
    _statsTimer?.cancel();
    _urlController.dispose();
    _tokenController.dispose();
    super.dispose();
  }

  Future<void> _refreshQueueOnly() async {
    final bridge = ref.read(nativeBridgeProvider);
    try {
      final q = await bridge.getUploadQueueStats();
      if (mounted) setState(() => _queueStats = q);
    } catch (_) {}
  }

  Future<void> _refreshStatus() async {
    await _refreshQueueOnly();
    final client = ref.read(apiClientProvider);
    if (client == null) return;
    try {
      final cfg = await client.fetchRuntimeConfig();
      if (!mounted) return;
      final asr = (cfg['asr'] as Map?) ?? const {};
      final vp = (cfg['voiceprint'] as Map?) ?? const {};
      final prov = (asr['asr_provider'] as String?) ?? 'vertexai';
      final mod = prov == 'vertexai'
          ? ((asr['vertex_model'] as String?) ?? 'gemini-3.8-flash')
          : ((asr['gemini_model'] as String?) ?? 'gemini-2.5-flash');
      setState(() {
        _asrProvider = prov;
        _asrModel = mod;
        _similarityThreshold =
            ((vp['similarity_threshold'] as num?)?.toDouble() ?? 0.72)
                .clamp(0.50, 0.95);
        _autoMatchEnabled = vp['auto_match_enabled'] != false;
        _configLoaded = true;
      });
    } catch (_) {}
  }

  Future<void> _saveRuntimeConfig() async {
    final client = ref.read(apiClientProvider);
    if (client == null) return;
    setState(() => _savingConfig = true);
    try {
      await client.updateRuntimeConfig({
        'asr': {
          'asr_provider': _asrProvider,
          if (_asrProvider == 'vertexai') 'vertex_model': _asrModel.trim(),
          if (_asrProvider == 'gemini') 'gemini_model': _asrModel.trim(),
        },
        'voiceprint': {
          'similarity_threshold': double.parse(
            _similarityThreshold.toStringAsFixed(2),
          ),
          'auto_match_enabled': _autoMatchEnabled,
        },
      });
    } catch (_) {
    } finally {
      if (mounted) setState(() => _savingConfig = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authSessionProvider);
    final authNotifier = ref.read(authSessionProvider.notifier);
    final bridge = ref.read(nativeBridgeProvider);

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
                            '设置',
                            style: TextStyle(
                              fontSize: 17.0,
                              fontWeight: FontWeight.w700,
                              letterSpacing: -0.3,
                              color: HemoryColors.textLightPrimary,
                            ),
                          ),
                          const Spacer(),
                          Text(
                            'v$kAppVersion',
                            style: const TextStyle(
                              fontSize: 12.0,
                              fontWeight: FontWeight.w600,
                              color: HemoryColors.textLightMuted,
                              fontFeatures: [FontFeature.tabularFigures()],
                            ),
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
                    onRefresh: _refreshStatus,
                    child: ListView(
                      physics: const AlwaysScrollableScrollPhysics(
                        parent: BouncingScrollPhysics(),
                      ),
                      padding: const EdgeInsets.all(16.0),
                      children: [
                        _buildSectionCard(
                          children: [
                            Row(
                              children: [
                                Container(
                                  width: 8.0,
                                  height: 8.0,
                                  decoration: BoxDecoration(
                                    shape: BoxShape.circle,
                                    color: auth.isAuthenticated
                                        ? HemoryColors.sageSecondary
                                        : HemoryColors.liveCoral,
                                  ),
                                ),
                                const SizedBox(width: 8.0),
                                const Text(
                                  '服务端',
                                  style: TextStyle(
                                    fontSize: 14.0,
                                    fontWeight: FontWeight.w600,
                                    color: HemoryColors.textLightPrimary,
                                  ),
                                ),
                                const Spacer(),
                                if (auth.isAuthenticated)
                                  TextButton(
                                    style: TextButton.styleFrom(
                                      visualDensity: VisualDensity.compact,
                                      foregroundColor: HemoryColors.liveCoral,
                                    ),
                                    onPressed: authNotifier.disconnect,
                                    child: const Text('断开'),
                                  ),
                              ],
                            ),
                            const SizedBox(height: 10.0),
                            _buildField(
                              controller: _urlController,
                              hint: kDefaultServerUrl,
                            ),
                            const SizedBox(height: 8.0),
                            _buildField(
                              controller: _tokenController,
                              hint: 'Token',
                              obscure: true,
                            ),
                            if (auth.errorMessage != null) ...[
                              const SizedBox(height: 8.0),
                              Text(
                                auth.errorMessage!,
                                style: const TextStyle(
                                  fontSize: 12.0,
                                  color: HemoryColors.liveCoral,
                                ),
                              ),
                            ],
                            const SizedBox(height: 10.0),
                            FilledButton(
                              style: FilledButton.styleFrom(
                                backgroundColor: HemoryColors.sagePrimary,
                                foregroundColor: Colors.black,
                              ),
                              onPressed: auth.isConnecting
                                  ? null
                                  : () async {
                                      final ok = await authNotifier.connect(
                                        baseUrl: _urlController.text,
                                        accessToken: _tokenController.text,
                                      );
                                      if (ok) {
                                        ref
                                            .read(timelineRefreshTickProvider
                                                .notifier)
                                            .state++;
                                        _refreshStatus();
                                      }
                                    },
                              child: Text(auth.isConnecting ? '连接中…' : '保存并连接'),
                            ),
                          ],
                        ),
                        const SizedBox(height: 12.0),
                        _buildSectionCard(
                          children: [
                            Row(
                              children: [
                                const Text(
                                  '上传队列',
                                  style: TextStyle(
                                    fontSize: 14.0,
                                    fontWeight: FontWeight.w600,
                                    color: HemoryColors.textLightPrimary,
                                  ),
                                ),
                                const Spacer(),
                                if ((_queueStats['failed'] ?? 0) > 0 ||
                                    (_queueStats['pending'] ?? 0) > 0)
                                  TextButton(
                                    style: TextButton.styleFrom(
                                      visualDensity: VisualDensity.compact,
                                      foregroundColor: HemoryColors.sagePrimary,
                                    ),
                                    onPressed: () async {
                                      await bridge.retryFailedUploads();
                                      await _refreshQueueOnly();
                                    },
                                    child: const Text('立即同步'),
                                  ),
                              ],
                            ),
                            const SizedBox(height: 10.0),
                            Row(
                              children: [
                                _buildStatCell(
                                  '待传',
                                  (_queueStats['pending'] ?? 0) +
                                      (_queueStats['uploading'] ?? 0),
                                ),
                                const SizedBox(width: 8.0),
                                _buildStatCell(
                                  '失败',
                                  _queueStats['failed'] ?? 0,
                                  highlightError:
                                      (_queueStats['failed'] ?? 0) > 0,
                                ),
                                const SizedBox(width: 8.0),
                                _buildStatCell(
                                  '已传',
                                  _queueStats['uploaded'] ?? 0,
                                ),
                              ],
                            ),
                          ],
                        ),
                        if (_configLoaded) ...[
                          const SizedBox(height: 12.0),
                          _buildSectionCard(
                            children: [
                              const Text(
                                '识别与声纹',
                                style: TextStyle(
                                  fontSize: 14.0,
                                  fontWeight: FontWeight.w600,
                                  color: HemoryColors.textLightPrimary,
                                ),
                              ),
                              const SizedBox(height: 10.0),
                              SegmentedButton<String>(
                                segments: const [
                                  ButtonSegment(
                                    value: 'vertexai',
                                    label: Text('Vertex AI'),
                                  ),
                                  ButtonSegment(
                                    value: 'gemini',
                                    label: Text('Gemini API'),
                                  ),
                                ],
                                selected: {_asrProvider},
                                onSelectionChanged: (s) {
                                  setState(() => _asrProvider = s.first);
                                },
                              ),
                              const SizedBox(height: 12.0),
                              Row(
                                mainAxisAlignment:
                                    MainAxisAlignment.spaceBetween,
                                children: [
                                  const Text(
                                    '声纹阈值',
                                    style: TextStyle(
                                      fontSize: 13.0,
                                      color: HemoryColors.textLightSecondary,
                                    ),
                                  ),
                                  Text(
                                    _similarityThreshold.toStringAsFixed(2),
                                    style: const TextStyle(
                                      fontSize: 13.0,
                                      fontWeight: FontWeight.w600,
                                      color: HemoryColors.textLightPrimary,
                                      fontFeatures: [
                                        FontFeature.tabularFigures()
                                      ],
                                    ),
                                  ),
                                ],
                              ),
                              Slider(
                                value: _similarityThreshold,
                                min: 0.50,
                                max: 0.95,
                                divisions: 45,
                                activeColor: HemoryColors.sagePrimary,
                                onChanged: (v) {
                                  setState(() => _similarityThreshold = v);
                                },
                              ),
                              SwitchListTile(
                                contentPadding: EdgeInsets.zero,
                                dense: true,
                                title: const Text(
                                  '自动声纹匹配',
                                  style: TextStyle(
                                    fontSize: 13.5,
                                    color: HemoryColors.textLightPrimary,
                                  ),
                                ),
                                activeThumbColor: HemoryColors.sagePrimary,
                                value: _autoMatchEnabled,
                                onChanged: (v) {
                                  setState(() => _autoMatchEnabled = v);
                                },
                              ),
                              const SizedBox(height: 6.0),
                              FilledButton(
                                style: FilledButton.styleFrom(
                                  backgroundColor: HemoryColors.darkSurface2,
                                  foregroundColor:
                                      HemoryColors.textLightPrimary,
                                ),
                                onPressed:
                                    _savingConfig ? null : _saveRuntimeConfig,
                                child: Text(_savingConfig ? '保存中…' : '应用配置'),
                              ),
                            ],
                          ),
                        ],
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

  Widget _buildSectionCard({required List<Widget> children}) {
    return Container(
      padding: const EdgeInsets.all(14.0),
      decoration: HemoryTheme.frostedBoxDecoration(radius: 14.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: children,
      ),
    );
  }

  Widget _buildField({
    required TextEditingController controller,
    required String hint,
    bool obscure = false,
  }) {
    return TextField(
      controller: controller,
      obscureText: obscure,
      style: const TextStyle(
        fontSize: 13.5,
        color: HemoryColors.textLightPrimary,
      ),
      decoration: InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(color: HemoryColors.textLightMuted),
        filled: true,
        fillColor: HemoryColors.darkSurface2,
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 12.0,
          vertical: 11.0,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10.0),
          borderSide: BorderSide.none,
        ),
      ),
    );
  }

  Widget _buildStatCell(
    String label,
    int count, {
    bool highlightError = false,
  }) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 10.0),
        decoration: BoxDecoration(
          color: HemoryColors.darkSurface2,
          borderRadius: BorderRadius.circular(10.0),
          border: Border.all(
            color: highlightError
                ? HemoryColors.liveCoral.withValues(alpha: 0.6)
                : HemoryColors.darkBorder,
            width: 0.6,
          ),
        ),
        child: Column(
          children: [
            Text(
              '$count',
              style: TextStyle(
                fontSize: 17.0,
                fontWeight: FontWeight.w700,
                color: highlightError
                    ? HemoryColors.liveCoral
                    : HemoryColors.textLightPrimary,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
            const SizedBox(height: 2.0),
            Text(
              label,
              style: const TextStyle(
                fontSize: 11.0,
                color: HemoryColors.textLightMuted,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
