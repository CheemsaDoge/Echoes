/// P3 Timeline Card: ScatteredChatConvoCard.
/// Visualizes ambient dialog turns, chat snippets, and quick exchanges.
library;

import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/hemory_theme.dart';

class ChatSnippet {
  final String speakerName;
  final String text;
  final String time;

  const ChatSnippet({
    required this.speakerName,
    required this.text,
    required this.time,
  });
}

class ScatteredChatConvoCard extends StatelessWidget {
  final String groupId;
  final List<ChatSnippet> snippets;
  final VoidCallback? onTap;

  const ScatteredChatConvoCard({
    super.key,
    required this.groupId,
    required this.snippets,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(14.0),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 18.0, sigmaY: 18.0),
          child: Container(
            decoration: HemoryTheme.frostedBoxDecoration(
              borderColor: HemoryColors.darkBorder.withOpacity(0.6),
            ),
            padding: const EdgeInsets.symmetric(horizontal: 14.0, vertical: 10.0),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: snippets.map((snip) => _buildSnippetRow(snip)).toList(),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildSnippetRow(ChatSnippet snip) {
    final initial = snip.speakerName.isNotEmpty
        ? snip.speakerName.substring(0, 1).toUpperCase()
        : '?';

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Speaker Initial Badge
          CircleAvatar(
            radius: 10.0,
            backgroundColor: HemoryColors.darkSurface2,
            child: Text(
              initial,
              style: const TextStyle(
                fontSize: 10.0,
                fontWeight: FontWeight.w700,
                color: HemoryColors.sageSecondary,
              ),
            ),
          ),
          const SizedBox(width: 8.0),
          // Speaker Name and Message Text
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      snip.speakerName,
                      style: const TextStyle(
                        fontSize: 11.0,
                        fontWeight: FontWeight.w600,
                        color: HemoryColors.textLightPrimary,
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
                const SizedBox(height: 2.0),
                Text(
                  snip.text,
                  style: const TextStyle(
                    fontSize: 12.0,
                    fontWeight: FontWeight.w400,
                    height: 1.35,
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
