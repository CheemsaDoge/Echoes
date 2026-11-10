/// Animated sliding glass pill segmented controller for switching between Calendar and River views.
library;

import 'package:flutter/material.dart';
import '../../core/models/timeline_models.dart';
import '../theme/hemory_theme.dart';

class ModeSwitcher extends StatelessWidget {
  final TimelineViewMode activeMode;
  final ValueChanged<TimelineViewMode> onModeChanged;

  const ModeSwitcher({
    super.key,
    required this.activeMode,
    required this.onModeChanged,
  });

  @override
  Widget build(BuildContext context) {
    const double width = 168.0;
    const double height = 36.0;
    const double pillWidth = (width - 4.0) / 2;

    final isCalendar = activeMode.isCalendar;

    return Container(
      width: width,
      height: height,
      padding: const EdgeInsets.all(2.0),
      decoration: BoxDecoration(
        color: HemoryColors.darkSurface1,
        borderRadius: BorderRadius.circular(18.0),
        border: Border.all(color: HemoryColors.darkBorder, width: 0.8),
      ),
      child: Stack(
        children: [
          // Animated Sliding Pill
          AnimatedAlign(
            duration: const Duration(milliseconds: 280),
            curve: Curves.easeInOutCubic,
            alignment: isCalendar ? Alignment.centerLeft : Alignment.centerRight,
            child: Container(
              width: pillWidth,
              height: height - 4.0,
              decoration: BoxDecoration(
                color: HemoryColors.darkSurface2,
                borderRadius: BorderRadius.circular(16.0),
                border: Border.all(
                  color: HemoryColors.sagePrimary.withOpacity(0.5),
                  width: 0.8,
                ),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x26000000),
                    blurRadius: 6.0,
                    offset: Offset(0, 2),
                  ),
                ],
              ),
            ),
          ),
          // Option Buttons
          Row(
            children: [
              Expanded(
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => onModeChanged(TimelineViewMode.calendar),
                  child: Center(
                    child: Text(
                      'Calendar',
                      style: TextStyle(
                        fontSize: 12.0,
                        fontWeight: FontWeight.w600,
                        color: isCalendar
                            ? HemoryColors.textLightPrimary
                            : HemoryColors.textLightMuted,
                      ),
                    ),
                  ),
                ),
              ),
              Expanded(
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => onModeChanged(TimelineViewMode.river),
                  child: Center(
                    child: Text(
                      'River',
                      style: TextStyle(
                        fontSize: 12.0,
                        fontWeight: FontWeight.w600,
                        color: !isCalendar
                            ? HemoryColors.textLightPrimary
                            : HemoryColors.textLightMuted,
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
