/// P0 Timeline Card: TimelineLiveCard.
/// Active live recording card rendering real-time audio waveform visualizer,
/// pulsing glowing red recording badge, and tabular elapsed timer.
library;

import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/hemory_theme.dart';

class TimelineLiveCard extends StatefulWidget {
  final int elapsedMs;
  final List<double> meteringSamples; // Last 36 frames, normalized 0.05..1.0
  final VoidCallback? onStopPressed;
  final VoidCallback? onPauseTogglePressed;
  final bool isPaused;

  const TimelineLiveCard({
    super.key,
    required this.elapsedMs,
    required this.meteringSamples,
    this.onStopPressed,
    this.onPauseTogglePressed,
    this.isPaused = false,
  });

  @override
  State<TimelineLiveCard> createState() => _TimelineLiveCardState();
}

class _TimelineLiveCardState extends State<TimelineLiveCard>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulseController;
  late final Animation<double> _pulseAnimation;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);

    _pulseAnimation = CurvedAnimation(
      parent: _pulseController,
      curve: Curves.easeInOut,
    );
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  String _formatElapsed(int ms) {
    final totalSeconds = ms ~/ 1000;
    final hours = totalSeconds ~/ 3600;
    final minutes = (totalSeconds % 3600) ~/ 60;
    final seconds = totalSeconds % 60;

    if (hours > 0) {
      return '${hours.toString().padLeft(2, '0')}:${minutes.toString().padLeft(2, '0')}:${seconds.toString().padLeft(2, '0')}';
    }
    return '${minutes.toString().padLeft(2, '0')}:${seconds.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(16.0),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 18.0, sigmaY: 18.0),
        child: Container(
          decoration: HemoryTheme.frostedBoxDecoration(
            borderColor: widget.isPaused
                ? HemoryColors.darkBorder
                : HemoryColors.liveCoral.withValues(alpha: 0.35),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 14.0),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Top Row: Pulsing Indicator + Tabular Timer + Controls
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      // Pulsing Red Live Dot
                      AnimatedBuilder(
                        animation: _pulseAnimation,
                        builder: (context, child) {
                          final scale = widget.isPaused
                              ? 1.0
                              : 0.85 + (0.35 * _pulseAnimation.value);
                          final glowOpacity = widget.isPaused
                              ? 0.0
                              : 0.3 + (0.4 * _pulseAnimation.value);

                          return Container(
                            width: 14.0,
                            height: 14.0,
                            alignment: Alignment.center,
                            child: Container(
                              width: 10.0 * scale,
                              height: 10.0 * scale,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                color: widget.isPaused
                                    ? HemoryColors.sageSecondary
                                    : HemoryColors.liveCoral,
                                boxShadow: [
                                  if (!widget.isPaused)
                                    BoxShadow(
                                      color: HemoryColors.liveCoral
                                          .withValues(alpha: glowOpacity),
                                      blurRadius: 8.0,
                                      spreadRadius: 2.0,
                                    ),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
                      const SizedBox(width: 10.0),
                      // Tabular Figures Elapsed Timer
                      Text(
                        _formatElapsed(widget.elapsedMs),
                        style: const TextStyle(
                          fontSize: 18.0,
                          fontWeight: FontWeight.w700,
                          letterSpacing: -0.3,
                          color: HemoryColors.textLightPrimary,
                          fontFeatures: [FontFeature.tabularFigures()],
                        ),
                      ),
                    ],
                  ),
                  Row(
                    children: [
                      if (widget.onPauseTogglePressed != null)
                        IconButton(
                          iconSize: 20.0,
                          visualDensity: VisualDensity.compact,
                          icon: Icon(
                            widget.isPaused ? Icons.play_arrow : Icons.pause,
                            color: HemoryColors.textLightPrimary,
                          ),
                          onPressed: widget.onPauseTogglePressed,
                        ),
                      if (widget.onStopPressed != null)
                        IconButton(
                          iconSize: 20.0,
                          visualDensity: VisualDensity.compact,
                          icon: const Icon(
                            Icons.stop_rounded,
                            color: HemoryColors.liveCoral,
                          ),
                          onPressed: widget.onStopPressed,
                        ),
                    ],
                  ),
                ],
              ),
              const SizedBox(height: 12.0),
              // Real-Time Waveform Visualizer
              SizedBox(
                height: 38.0,
                child: CustomPaint(
                  painter: _WaveformPainter(
                    samples: widget.meteringSamples,
                    activeColor: widget.isPaused
                        ? HemoryColors.sageSecondary
                        : HemoryColors.sagePrimary,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _WaveformPainter extends CustomPainter {
  final List<double> samples;
  final Color activeColor;

  _WaveformPainter({required this.samples, required this.activeColor});

  @override
  void paint(Canvas canvas, Size size) {
    const double barWidth = 3.0;
    const double spacing = 2.5;
    final int count = samples.isEmpty ? 36 : samples.length;
    final double totalW = count * barWidth + (count - 1) * spacing;
    final double startX = (size.width - totalW) / 2;
    final double centerY = size.height / 2;

    final paint = Paint()
      ..color = activeColor
      ..strokeCap = StrokeCap.round
      ..strokeWidth = barWidth;

    for (int i = 0; i < count; i++) {
      final amp = (i < samples.length ? samples[i] : 0.08).clamp(0.05, 1.0);
      final halfH = (size.height * 0.9 * amp) / 2;
      final x = startX + i * (barWidth + spacing);
      canvas.drawLine(
        Offset(x, centerY - halfH),
        Offset(x, centerY + halfH),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _WaveformPainter oldDelegate) => true;
}
