/// Horizontal DateStrip with magnetic snap-to-center physics,
/// edge fade mask, and dual-range [-7, +1] prefetching callbacks.
library;

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../theme/hemory_theme.dart';

class DateStrip extends StatefulWidget {
  final DateTime selectedDate;
  final ValueChanged<DateTime> onDateSelected;
  final void Function(DateTime centerDate)? onPrefetchRequested;
  final Map<String, int> activityDensityMap; // 'YYYY-MM-DD' -> eventCount

  const DateStrip({
    super.key,
    required this.selectedDate,
    required this.onDateSelected,
    this.onPrefetchRequested,
    this.activityDensityMap = const {},
  });

  @override
  State<DateStrip> createState() => _DateStripState();
}

class _DateStripState extends State<DateStrip> {
  late final ScrollController _scrollController;
  final double cellWidth = 52.0;
  final double cellMargin = 8.0;
  late final double stride;

  // Render a range of days around today (-30 to +7)
  final int pastDays = 30;
  final int futureDays = 7;
  late final List<DateTime> _dates;

  @override
  void initState() {
    super.initState();
    stride = cellWidth + cellMargin;
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);

    _dates = List.generate(
      pastDays + futureDays + 1,
      (i) => today.subtract(Duration(days: pastDays - i)),
    );

    _scrollController = ScrollController();

    // Trigger initial [-7, +1] prefetch
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _scrollToDate(widget.selectedDate, animate: false);
      widget.onPrefetchRequested?.call(widget.selectedDate);
    });
  }

  @override
  void didUpdateWidget(covariant DateStrip oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!DateUtils.isSameDay(oldWidget.selectedDate, widget.selectedDate)) {
      _scrollToDate(widget.selectedDate, animate: true);
      widget.onPrefetchRequested?.call(widget.selectedDate);
    }
  }

  void _scrollToDate(DateTime date, {bool animate = true}) {
    final index = _dates.indexWhere((d) => DateUtils.isSameDay(d, date));
    if (index != -1 && _scrollController.hasClients) {
      final screenWidth = MediaQuery.of(context).size.width;
      final targetOffset = (index * stride) - (screenWidth / 2) + (stride / 2);
      final clamped = targetOffset.clamp(
        0.0,
        _scrollController.position.maxScrollExtent,
      );

      if (animate) {
        _scrollController.animateTo(
          clamped,
          duration: const Duration(milliseconds: 320),
          curve: Curves.easeOutCubic,
        );
      } else {
        _scrollController.jumpTo(clamped);
      }
    }
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ShaderMask(
      shaderCallback: (Rect bounds) {
        return const LinearGradient(
          begin: Alignment.centerLeft,
          end: Alignment.centerRight,
          colors: [
            Colors.transparent,
            Colors.black,
            Colors.black,
            Colors.transparent,
          ],
          stops: [0.0, 0.08, 0.92, 1.0],
        ).createShader(bounds);
      },
      blendMode: BlendMode.dstIn,
      child: SizedBox(
        height: 64.0,
        child: ListView.builder(
          controller: _scrollController,
          scrollDirection: Axis.horizontal,
          physics: const BouncingScrollPhysics(),
          padding: const EdgeInsets.symmetric(horizontal: 24.0),
          itemCount: _dates.length,
          itemBuilder: (context, index) {
            final date = _dates[index];
            final isSelected = DateUtils.isSameDay(date, widget.selectedDate);
            final isToday = DateUtils.isSameDay(date, DateTime.now());
            final dayKey = DateFormat('yyyy-MM-dd').format(date);
            final eventCount = widget.activityDensityMap[dayKey] ?? 0;

            return Padding(
              padding: EdgeInsets.only(right: cellMargin),
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () => widget.onDateSelected(date),
                child: _buildDateCell(date, isSelected, isToday, eventCount),
              ),
            );
          },
        ),
      ),
    );
  }

  Widget _buildDateCell(
    DateTime date,
    bool isSelected,
    bool isToday,
    int eventCount,
  ) {
    final dayOfWeek = DateFormat('E').format(date).substring(0, 1); // e.g. M, T, W
    final dayOfMonth = date.day.toString();

    final dotsCount = eventCount > 3 ? 3 : eventCount;

    return Container(
      width: cellWidth,
      padding: const EdgeInsets.symmetric(vertical: 6.0),
      decoration: BoxDecoration(
        color: isSelected ? HemoryColors.darkSurface2 : Colors.transparent,
        borderRadius: BorderRadius.circular(14.0),
        border: Border.all(
          color: isSelected
              ? HemoryColors.sagePrimary
              : (isToday ? HemoryColors.darkBorder : Colors.transparent),
          width: isSelected ? 1.2 : 0.8,
        ),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            dayOfWeek,
            style: TextStyle(
              fontSize: 10.0,
              fontWeight: FontWeight.w600,
              color: isSelected
                  ? HemoryColors.sageSecondary
                  : HemoryColors.textLightMuted,
            ),
          ),
          const SizedBox(height: 2.0),
          Text(
            dayOfMonth,
            style: TextStyle(
              fontSize: 15.0,
              fontWeight: FontWeight.w700,
              color: isSelected
                  ? HemoryColors.textLightPrimary
                  : HemoryColors.textLightSecondary,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
          const SizedBox(height: 3.0),
          // Micro Activity Dots
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: List.generate(
              dotsCount,
              (i) => Container(
                width: 3.5,
                height: 3.5,
                margin: const EdgeInsets.symmetric(horizontal: 1.0),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: isSelected
                      ? HemoryColors.sagePrimary
                      : HemoryColors.textLightMuted.withOpacity(0.6),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
