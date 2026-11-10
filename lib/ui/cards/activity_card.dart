/// P2 Timeline Card: ActivityCard.
/// Structured conversational memory card rendering timestamps, title, summary,
/// resolved speaker chips, and clean category accents (never UNKNOWN).
library;

import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/hemory_theme.dart';

class ActivityCard extends StatelessWidget {
  final String eventId;
  final String title;
  final String? summary;
  final String timeRange;
  final String category;
  final List<String> speakers;
  final List<String> topicTags;
  final int pendingClusterCount;
  final Color? categoryColor;
  final VoidCallback? onTap;

  const ActivityCard({
    super.key,
    required this.eventId,
    required this.title,
    this.summary,
    required this.timeRange,
    this.category = '',
    this.speakers = const [],
    this.topicTags = const [],
    this.pendingClusterCount = 0,
    this.categoryColor,
    this.onTap,
  });

  static Color accentForCategory(String category) {
    switch (category.trim().toLowerCase()) {
      case 'meeting':
        return const Color(0xFF6366F1);
      case 'work':
        return HemoryColors.sagePrimary;
      case 'learning':
        return const Color(0xFF38BDF8);
      case 'meal':
        return const Color(0xFFF59E0B);
      case 'social':
        return const Color(0xFFA855F7);
      case 'life':
        return HemoryColors.sageSecondary;
      default:
        return HemoryColors.sagePrimary;
    }
  }

  static String? labelForCategory(String category) {
    switch (category.trim().toLowerCase()) {
      case 'meeting':
        return '会议';
      case 'work':
        return '工作';
      case 'learning':
        return '学习';
      case 'meal':
        return '用餐';
      case 'social':
        return '社交';
      case 'life':
        return '日常';
      default:
        return null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final trimmedTitle = title.trim();
    final trimmedSummary = summary?.trim();
    final showSummary = trimmedSummary != null &&
        trimmedSummary.isNotEmpty &&
        trimmedSummary != trimmedTitle;
    final accent = categoryColor ?? accentForCategory(category);
    final catLabel = labelForCategory(category);
    final cleanTags = topicTags
        .map((t) => t.trim())
        .where((t) =>
            t.isNotEmpty &&
            t.toLowerCase() != 'unknown' &&
            t.toLowerCase() != 'general')
        .take(3)
        .toList();

    final hasFooter = speakers.isNotEmpty ||
        cleanTags.isNotEmpty ||
        pendingClusterCount > 0;

    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(16.0),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 16.0, sigmaY: 16.0),
          child: Container(
            decoration: HemoryTheme.frostedBoxDecoration(
              borderColor: HemoryColors.darkBorder,
            ),
            child: Stack(
              children: [
                Positioned(
                  left: 0,
                  top: 0,
                  bottom: 0,
                  width: 3.5,
                  child: ColoredBox(color: accent),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(17.5, 10.0, 14.0, 10.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              timeRange,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 11.0,
                                fontWeight: FontWeight.w600,
                                color: HemoryColors.textLightMuted,
                                fontFeatures: [FontFeature.tabularFigures()],
                              ),
                            ),
                          ),
                          if (catLabel != null)
                            Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 6.0,
                                vertical: 2.0,
                              ),
                              decoration: BoxDecoration(
                                color: accent.withValues(alpha: 0.14),
                                borderRadius: BorderRadius.circular(5.0),
                              ),
                              child: Text(
                                catLabel,
                                style: TextStyle(
                                  fontSize: 10.0,
                                  fontWeight: FontWeight.w600,
                                  color: accent,
                                ),
                              ),
                            ),
                        ],
                      ),
                      const SizedBox(height: 4.0),
                      Text(
                        trimmedTitle,
                        style: const TextStyle(
                          fontSize: 15.0,
                          fontWeight: FontWeight.w600,
                          letterSpacing: -0.2,
                          color: HemoryColors.textLightPrimary,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      if (showSummary) ...[
                        const SizedBox(height: 4.0),
                        Text(
                          trimmedSummary,
                          style: const TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w400,
                            height: 1.38,
                            color: HemoryColors.textLightSecondary,
                          ),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                      if (hasFooter) ...[
                        const SizedBox(height: 8.0),
                        Wrap(
                          spacing: 6.0,
                          runSpacing: 4.0,
                          children: [
                            ...speakers.take(3).map(_buildSpeakerChip),
                            if (pendingClusterCount > 0)
                              _buildPendingVoiceChip(pendingClusterCount),
                            ...cleanTags.map(_buildTagChip),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildSpeakerChip(String name) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7.0, vertical: 2.5),
      decoration: BoxDecoration(
        color: HemoryColors.darkSurface2,
        borderRadius: BorderRadius.circular(6.0),
        border: Border.all(color: HemoryColors.darkBorder, width: 0.6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 5.5,
            height: 5.5,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              color: HemoryColors.sageSecondary,
            ),
          ),
          const SizedBox(width: 4.5),
          Text(
            name,
            style: const TextStyle(
              fontSize: 10.5,
              fontWeight: FontWeight.w500,
              color: HemoryColors.textLightPrimary,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPendingVoiceChip(int count) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6.5, vertical: 2.5),
      decoration: BoxDecoration(
        color: const Color(0x26F59E0B),
        borderRadius: BorderRadius.circular(6.0),
        border: Border.all(color: const Color(0x55F59E0B), width: 0.6),
      ),
      child: Text(
        count > 1 ? '新声纹 ×$count' : '新声纹',
        style: const TextStyle(
          fontSize: 10.0,
          fontWeight: FontWeight.w600,
          color: Color(0xFFFBBF24),
        ),
      ),
    );
  }

  Widget _buildTagChip(String tag) {
    final displayTag = tag.startsWith('#') ? tag : '#$tag';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6.0, vertical: 2.5),
      decoration: BoxDecoration(
        color: HemoryColors.darkSurface1,
        borderRadius: BorderRadius.circular(6.0),
      ),
      child: Text(
        displayTag,
        style: const TextStyle(
          fontSize: 10.0,
          fontWeight: FontWeight.w500,
          color: HemoryColors.textLightMuted,
        ),
      ),
    );
  }
}
