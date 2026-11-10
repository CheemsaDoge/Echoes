/// CoveragePainter: High-performance CustomPainter rendering 24h continuous audio canvas.
/// Paints 24 hour markers, horizontal grid lines, green/coral audio bars, and live clock needle.
library;

import 'package:flutter/material.dart';
import '../../core/models/timeline_models.dart';
import '../theme/hemory_theme.dart';

class CoveragePainter extends CustomPainter {
  final List<TimelineCoverageSegment> segments;
  final double pixelsPerMinute;
  final double currentMinute;
  final bool isToday;

  CoveragePainter({
    required this.segments,
    required this.pixelsPerMinute,
    required this.currentMinute,
    required this.isToday,
  });

  @override
  void paint(Canvas canvas, Size size) {
    const double gutterWidth = 56.0;
    const double barX = 60.0;
    const double barWidth = 4.0;

    final gridPaint = Paint()
      ..color = HemoryColors.darkBorder.withValues(alpha: 0.35)
      ..strokeWidth = 0.8;

    final textStyle = TextStyle(
      color: HemoryColors.textLightSecondary.withValues(alpha: 0.7),
      fontSize: 11.0,
      fontWeight: FontWeight.w500,
      fontFeatures: const [FontFeature.tabularFigures()],
    );

    // 1. Draw 24-Hour Markers and Horizontal Grid Lines
    for (int hour = 0; hour < 24; hour++) {
      final y = hour * 60.0 * pixelsPerMinute;
      canvas.drawLine(Offset(gutterWidth, y), Offset(size.width, y), gridPaint);

      final textSpan = TextSpan(
        text: '${hour.toString().padLeft(2, '0')}:00',
        style: textStyle,
      );
      final tp = TextPainter(
        text: textSpan,
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, Offset(8.0, y - (tp.height / 2)));
    }

    // 2. Draw Audio Coverage Bars
    for (final seg in segments) {
      final yStart = seg.startMinute * pixelsPerMinute;
      final yEnd = seg.endMinute * pixelsPerMinute;

      final paint = Paint()
        ..color = seg.isLive
            ? HemoryColors.liveCoral
            : HemoryColors.sagePrimary.withValues(alpha: 0.45);

      final rrect = RRect.fromRectAndRadius(
        Rect.fromLTRB(barX, yStart, barX + barWidth, yEnd),
        const Radius.circular(2.0),
      );
      canvas.drawRRect(rrect, paint);
    }

    // 3. Draw Current Time Needle (if today and within 0..1440m)
    if (isToday && currentMinute >= 0 && currentMinute <= 1440) {
      final yNow = currentMinute * pixelsPerMinute;
      final needlePaint = Paint()
        ..color = HemoryColors.liveCoral
        ..strokeWidth = 1.5;

      final dotPaint = Paint()..color = HemoryColors.liveCoral;
      final glowPaint = Paint()
        ..color = HemoryColors.liveCoralGlow
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 5.0);

      canvas.drawCircle(Offset(barX + (barWidth / 2), yNow), 7.0, glowPaint);
      canvas.drawCircle(Offset(barX + (barWidth / 2), yNow), 4.0, dotPaint);
      canvas.drawLine(
        Offset(barX + barWidth + 4.0, yNow),
        Offset(size.width, yNow),
        needlePaint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant CoveragePainter oldDelegate) {
    return oldDelegate.segments != segments ||
        oldDelegate.currentMinute != currentMinute ||
        oldDelegate.pixelsPerMinute != pixelsPerMinute ||
        oldDelegate.isToday != isToday;
  }
}
