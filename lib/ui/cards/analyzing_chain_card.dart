/// P1 Timeline Card: AnalyzingChainCard.
/// Visualizes ongoing audio processing pipeline polled from GET /processing-chains.
/// Displays progress percentage and 5 discrete pipeline step chips:
/// slicing -> vad -> transcribing -> diarizing -> summarizing.
library;

import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/hemory_theme.dart';

enum ChainStepState { pending, active, completed }

class AnalyzingChainCard extends StatelessWidget {
  final String chainId;
  final double progress; // 0.0 to 1.0
  final Map<String, ChainStepState> steps; // slicing, vad, transcribing, diarizing, summarizing
  final String? currentPhase;

  const AnalyzingChainCard({
    super.key,
    required this.chainId,
    required this.progress,
    required this.steps,
    this.currentPhase,
  });

  static const List<String> canonicalSteps = [
    'slicing',
    'vad',
    'transcribing',
    'diarizing',
    'summarizing',
  ];

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(16.0),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 18.0, sigmaY: 18.0),
        child: Container(
          decoration: HemoryTheme.frostedBoxDecoration(
            borderColor: HemoryColors.sagePrimary.withValues(alpha: 0.3),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 14.0),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Top Progress Row
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      SizedBox(
                        width: 14.0,
                        height: 14.0,
                        child: CircularProgressIndicator(
                          value: progress > 0 ? progress : null,
                          strokeWidth: 2.0,
                          valueColor: const AlwaysStoppedAnimation(
                            HemoryColors.sagePrimary,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8.0),
                      Text(
                        currentPhase?.toUpperCase() ?? 'ANALYZING',
                        style: const TextStyle(
                          fontSize: 12.0,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 0.8,
                          color: HemoryColors.sageSecondary,
                        ),
                      ),
                    ],
                  ),
                  Text(
                    '${(progress * 100).toInt()}%',
                    style: const TextStyle(
                      fontSize: 13.0,
                      fontWeight: FontWeight.w600,
                      color: HemoryColors.textLightPrimary,
                      fontFeatures: [FontFeature.tabularFigures()],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12.0),
              // 5 Step Chips Row
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                physics: const BouncingScrollPhysics(),
                child: Row(
                  children: canonicalSteps.map((step) {
                    final state = steps[step] ?? ChainStepState.pending;
                    return Padding(
                      padding: const EdgeInsets.only(right: 6.0),
                      child: _buildStepChip(step, state),
                    );
                  }).toList(),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildStepChip(String label, ChainStepState state) {
    Color bg;
    Color border;
    Color text;

    switch (state) {
      case ChainStepState.completed:
        bg = HemoryColors.sagePrimary.withValues(alpha: 0.2);
        border = HemoryColors.sagePrimary.withValues(alpha: 0.6);
        text = HemoryColors.sagePrimary;
        break;
      case ChainStepState.active:
        bg = HemoryColors.darkSurface2;
        border = HemoryColors.sageSecondary;
        text = HemoryColors.textLightPrimary;
        break;
      case ChainStepState.pending:
        bg = HemoryColors.darkSurface1.withValues(alpha: 0.4);
        border = HemoryColors.darkBorder.withValues(alpha: 0.4);
        text = HemoryColors.textLightMuted;
        break;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8.0, vertical: 4.0),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(6.0),
        border: Border.all(color: border, width: 0.8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (state == ChainStepState.completed)
            const Padding(
              padding: EdgeInsets.only(right: 4.0),
              child: Icon(Icons.check, size: 10.0, color: HemoryColors.sagePrimary),
            ),
          Text(
            label,
            style: TextStyle(
              fontSize: 11.0,
              fontWeight: FontWeight.w500,
              color: text,
            ),
          ),
        ],
      ),
    );
  }
}
