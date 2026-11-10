/// P3 Timeline Card: ScatteredChatConvoCard.
/// Compact folded card for brief/scattered ambient remarks (never blown up into giant Activity cards).
library;

import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/hemory_theme.dart';

class ChatSnippet {
  final String speakerName;
  final String text;
  final String time;
  final String? audioUrl;
  final String? clusterId;

  const ChatSnippet({
    required this.speakerName,
    required this.text,
    required this.time,
    this.audioUrl,
    this.clusterId,
  });
}

class ScatteredChatConvoCard extends StatelessWidget {
  final String groupId;
  final List<ChatSnippet> snippets;
  final int pendingClusterCount;
  final VoidCallback? onTap;

  const ScatteredChatConvoCard({
    super.key,
    required this.groupId,
    required this.snippets,
    this.pendingClusterCount = 0,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final visible = snippets.take(3).toList();
    final overflowCount = snippets.length - visible.length;

    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(14.0),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 16.0, sigmaY: 16.0),
          child: Container(
            decoration: HemoryTheme.frostedBoxDecoration(
              color: const Color(0x99161B22),
              borderColor: HemoryColors.darkBorder.withValues(alpha: 0.65),
              radius: 14.0,
            ),
            padding: const EdgeInsets.symmetric(horizontal: 12.0, vertical: 9.0),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                ...visible.map(_buildSnippetRow),
                if (overflowCount > 0 || pendingClusterCount > 0)
                  Padding(
                    padding: const EdgeInsets.only(top: 4.0, left: 26.0),
                    child: Row(
                      children: [
                        if (overflowCount > 0)
                          Text(
                            '+$overflowCount 条片段',
                            style: const TextStyle(
                              fontSize: 10.5,
                              fontWeight: FontWeight.w500,
                              color: HemoryColors.textLightMuted,
                            ),
                          ),
                        if (overflowCount > 0 && pendingClusterCount > 0)
                          const SizedBox(width: 8.0),
                        if (pendingClusterCount > 0)
                          Text(
                            pendingClusterCount > 1
                                ? '新声纹 ×$pendingClusterCount'
                                : '新声纹',
                            style: const TextStyle(
                              fontSize: 10.5,
                              fontWeight: FontWeight.w600,
                              color: Color(0xFFFBBF24),
                            ),
                          ),
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

  Widget _buildSnippetRow(ChatSnippet snip) {
    final initial = snip.speakerName.trim().isNotEmpty
        ? snip.speakerName.trim().substring(0, 1).toUpperCase()
        : 'S';

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          CircleAvatar(
            radius: 9.0,
            backgroundColor: HemoryColors.darkSurface2,
            child: Text(
              initial,
              style: const TextStyle(
                fontSize: 9.5,
                fontWeight: FontWeight.w700,
                color: HemoryColors.sageSecondary,
              ),
            ),
          ),
          const SizedBox(width: 8.0),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        snip.speakerName,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 11.0,
                          fontWeight: FontWeight.w600,
                          color: HemoryColors.textLightPrimary,
                        ),
                      ),
                    ),
                    Text(
                      snip.time,
                      style: const TextStyle(
                        fontSize: 10.0,
                        fontWeight: FontWeight.w400,
                        color: HemoryColors.textLightMuted,
                        fontFeatures: [FontFeature.tabularFigures()],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 1.5),
                Text(
                  snip.text,
                  style: const TextStyle(
                    fontSize: 12.0,
                    fontWeight: FontWeight.w400,
                    height: 1.32,
                    color: HemoryColors.textLightSecondary,
                  ),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
