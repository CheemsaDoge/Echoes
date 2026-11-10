/// P4 Timeline Card: CalendarIdleSegment.
/// Visualizes non-recording idle gaps on the continuous calendar timeline.
library;

import 'package:flutter/material.dart';
import '../theme/hemory_theme.dart';

class CalendarIdleSegment extends StatelessWidget {
  final int durationMinutes;

  const CalendarIdleSegment({
    super.key,
    required this.durationMinutes,
  });

  @override
  Widget build(BuildContext context) {
    if (durationMinutes < 15) {
      return const SizedBox.shrink(); // Hide tiny gaps to reduce noise
    }

    final hours = durationMinutes ~/ 60;
    final mins = durationMinutes % 60;
    final durationLabel = hours > 0
        ? (mins > 0 ? '${hours}h ${mins}m' : '${hours}h')
        : '${mins}m';

    return Center(
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10.0, vertical: 4.0),
        decoration: BoxDecoration(
          color: HemoryColors.idleMuted,
          borderRadius: BorderRadius.circular(12.0),
        ),
        child: Text(
          durationLabel,
          style: const TextStyle(
            fontSize: 10.0,
            fontWeight: FontWeight.w500,
            letterSpacing: 0.2,
            color: HemoryColors.textLightMuted,
            fontFeatures: [FontFeature.tabularFigures()],
          ),
        ),
      ),
    );
  }
}
