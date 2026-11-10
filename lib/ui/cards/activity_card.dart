/// P2 Timeline Card: ActivityCard.
/// Structured conversational memory card rendering timestamps, title, summary,
/// speaker chips (resolved via Contract C6), and topic tags.
library;

import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/hemory_theme.dart';

class ActivityCard extends StatelessWidget {
  final String eventId;
  final String title;
  final String? summary;
  final String timeRange; // e.g. "09:30 - 10:15"
  final List<String> speakers; // Resolved display names
  final List<String> topicTags;
  final Color categoryColor;
  final VoidCallback? onTap;

  const ActivityCard({
    super.key,
    required this.eventId,
    required this.title,
    this.summary,
    required this.timeRange,
    this.speakers = const [],
    this.topicTags = const [],
    this.categoryColor = HemoryColors.sagePrimary,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(16.0),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 18.0, sigmaY: 18.0),
          child: Container(
            decoration: HemoryTheme.frostedBoxDecoration(
              borderColor: HemoryColors.darkBorder,
            ),
            child: IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Left Accent Bar (Category Color)
                  Container(
                    width: 4.0,
                    decoration: BoxDecoration(
                      color: categoryColor,
                      borderRadius: const BorderRadius.only(
                        topLeft: Radius.circular(16.0),
                        bottomLeft: Radius.circular(16.0),
                      ),
                    ),
                  ),
                  // Main Content
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14.0,
                        vertical: 12.0,
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          // Time Range Header
                          Text(
                            timeRange,
                            style: const TextStyle(
                              fontSize: 11.0,
                              fontWeight: FontWeight.w600,
                              color: HemoryColors.textLightMuted,
                              fontFeatures: [FontFeature.tabularFigures()],
                            ),
                          ),
                          const SizedBox(height: 4.0),
                          // Title
                          Text(
                            title,
                            style: const TextStyle(
                              fontSize: 15.0,
                              fontWeight: FontWeight.w600,
                              letterSpacing: -0.2,
                              color: HemoryColors.textLightPrimary,
                            ),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                          if (summary != null && summary!.isNotEmpty) ...[
                            const SizedBox(height: 6.0),
                            Text(
                              summary!,
                              style: const TextStyle(
                                fontSize: 13.0,
                                fontWeight: FontWeight.w400,
                                height: 1.4,
                                color: HemoryColors.textLightSecondary,
                              ),
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ],
                          const SizedBox(height: 10.0),
                          // Speakers and Topic Tags Row
                          Wrap(
                            spacing: 6.0,
                            runSpacing: 4.0,
                            children: [
                              ...speakers.map((s) => _buildSpeakerChip(s)),
                              ...topicTags.map((t) => _buildTagChip(t)),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildSpeakerChip(String name) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7.0, vertical: 3.0),
      decoration: BoxDecoration(
        color: HemoryColors.darkSurface2,
        borderRadius: BorderRadius.circular(6.0),
        border: Border.all(color: HemoryColors.darkBorder, width: 0.6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 6.0,
            height: 6.0,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              color: HemoryColors.sageSecondary,
            ),
          ),
          const SizedBox(width: 5.0),
          Text(
            name,
            style: const TextStyle(
              fontSize: 11.0,
              fontWeight: FontWeight.w500,
              color: HemoryColors.textLightPrimary,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTagChip(String tag) {
    final displayTag = tag.startsWith('#') ? tag : '#$tag';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6.0, vertical: 3.0),
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
