/// Horizontal DateStrip with magnetic centering, dynamic date-window anchoring,
/// edge fade mask, and real daily density indicators.
library;

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../theme/hemory_theme.dart';

class DateStrip extends StatefulWidget {
  final DateTime selectedDate;
  final ValueChanged<DateTime> onDateSelected;
  final void Function(DateTime centerDate)? onPrefetchRequested;
  final Map<String, int> activityDensityMap;

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
  static const List<String> _weekdayLabels = ['一', '二', '三', '四', '五', '六', '日'];
  late final ScrollController _scrollController;
  final double cellWidth = 48.0;
  final double cellMargin = 6.0;
  late final double stride;

  final int pastDays = 21;
  final int futureDays = 7;
  late List<DateTime> _dates;

  @override
  void initState() {
    super.initState();
    stride = cellWidth + cellMargin;
    _dates = _buildWindowAround(widget.selectedDate);
    _scrollController = ScrollController();

    WidgetsBinding.instance.addPostFrameCallback((_) {
      _scrollToDate(widget.selectedDate, animate: false);
      widget.onPrefetchRequested?.call(widget.selectedDate);
    });
  }

  List<DateTime> _buildWindowAround(DateTime anchor) {
    final center = DateTime(anchor.year, anchor.month, anchor.day);
    return List.generate(
      pastDays + futureDays + 1,
      (i) => center.subtract(Duration(days: pastDays - i)),
    );
  }

  @override
  void didUpdateWidget(covariant DateStrip oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!DateUtils.isSameDay(oldWidget.selectedDate, widget.selectedDate)) {
      final idx = _dates.indexWhere((d) => DateUtils.isSameDay(d, widget.selectedDate));
      if (idx <= 2 || idx >= _dates.length - 3) {
        _dates = _buildWindowAround(widget.selectedDate);
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _scrollToDate(widget.selectedDate, animate: false);
        });
      } else {
        _scrollToDate(widget.selectedDate, animate: true);
      }
      widget.onPrefetchRequested?.call(widget.selectedDate);
    }
  }

  void _scrollToDate(DateTime date, {bool animate = true}) {
    final index = _dates.indexWhere((d) => DateUtils.isSameDay(d, date));
    if (index != -1 && _scrollController.hasClients) {
      final viewport = _scrollController.position.viewportDimension;
      final targetOffset = (index * stride) - (viewport / 2) + (stride / 2) + 16.0;
      final clamped = targetOffset.clamp(
        0.0,
        _scrollController.position.maxScrollExtent,
      );

      if (animate) {
        _scrollController.animateTo(
          clamped,
          duration: const Duration(milliseconds: 280),
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
          stops: [0.0, 0.06, 0.94, 1.0],
        ).createShader(bounds);
      },
      blendMode: BlendMode.dstIn,
      child: SizedBox(
        height: 60.0,
        child: ListView.builder(
          controller: _scrollController,
          scrollDirection: Axis.horizontal,
          physics: const BouncingScrollPhysics(),
          padding: const EdgeInsets.symmetric(horizontal: 16.0),
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
    final dayOfWeek = _weekdayLabels[(date.weekday - 1).clamp(0, 6)];
    final dayOfMonth = date.day.toString();
    final dotsCount = eventCount > 3 ? 3 : eventCount;

    return Container(
      width: cellWidth,
      padding: const EdgeInsets.symmetric(vertical: 5.0),
      decoration: BoxDecoration(
        color: isSelected ? HemoryColors.darkSurface2 : Colors.transparent,
        borderRadius: BorderRadius.circular(13.0),
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
          SizedBox(
            height: 4.0,
            child: Row(
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
                        : HemoryColors.textLightMuted.withValues(alpha: 0.65),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
