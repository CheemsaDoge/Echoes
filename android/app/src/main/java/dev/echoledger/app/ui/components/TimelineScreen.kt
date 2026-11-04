package dev.echoledger.app.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.VpnKey
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.echoledger.app.ui.model.CalendarDayItem
import dev.echoledger.app.ui.model.MemoryEvent
import dev.echoledger.app.ui.model.SampleMemoryData
import dev.echoledger.app.ui.model.SubFragmentItem
import dev.echoledger.app.ui.theme.*

@Composable
fun TopListeningBar(
    isListening: Boolean,
    onToggleListening: () -> Unit,
    onOpenSettings: () -> Unit,
    toastBannerMessage: String?
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 10.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            IconButton(
                onClick = onOpenSettings,
                modifier = Modifier
                    .size(48.dp)
                    .testTag("open_settings_button")
            ) {
                Icon(
                    imageVector = Icons.Default.Menu,
                    contentDescription = "打开设置",
                    tint = TextPrimary
                )
            }

            Spacer(modifier = Modifier.width(4.dp))

            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(24.dp))
                    .clickable { onToggleListening() }
                    .padding(horizontal = 8.dp, vertical = 8.dp)
                    .testTag("toggle_listening_button"),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .size(12.dp)
                        .clip(CircleShape)
                        .background(if (isListening) PrimaryNeonGreen else DangerRed)
                )
                Spacer(modifier = Modifier.width(10.dp))
                Text(
                    text = if (isListening) "正在聆听记忆" else "点击开始聆听",
                    color = TextPrimary,
                    fontSize = 18.sp,
                    fontWeight = FontWeight.SemiBold
                )
                Spacer(modifier = Modifier.width(10.dp))
                Text(
                    text = if (isListening) "实时转写已开启" else "暂无上次开启时间",
                    color = TextMuted,
                    fontSize = 13.sp
                )
            }
        }

        if (toastBannerMessage != null) {
            Surface(
                modifier = Modifier.align(Alignment.Center),
                shape = RoundedCornerShape(24.dp),
                color = Color(0xFF1F2A22),
                tonalElevation = 6.dp
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        modifier = Modifier
                            .size(16.dp)
                            .clip(CircleShape)
                            .border(1.5.dp, PrimaryNeonGreen, CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Text("i", color = PrimaryNeonGreen, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                    }
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = toastBannerMessage,
                        color = TextPrimary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium
                    )
                }
            }
        }
    }
}

@Composable
fun TimelineScreen(
    selectedDay: Int,
    onSelectDay: (Int) -> Unit,
    isCalendarExpanded: Boolean,
    onToggleCalendarExpanded: () -> Unit,
    events: List<MemoryEvent>,
    selectedDate: java.time.LocalDate? = null,
    onSelectDate: ((java.time.LocalDate) -> Unit)? = null,
    needsConfigGuide: Boolean = false,
    isListening: Boolean = false,
    isProcessingSavedListening: Boolean = false,
    onStopListening: () -> Unit = {},
    onEventClick: (MemoryEvent) -> Unit,
    onScatteredFragmentsClick: (List<SubFragmentItem>) -> Unit,
    onUpgradeClick: () -> Unit,
    onQuickAiClick: () -> Unit
) {
    val dayEvents = remember(selectedDate, selectedDay, events) {
        if (selectedDate != null) {
            val targetDateIso = selectedDate.toString()
            events.filter { ev ->
                if (ev.dateIso.isNotBlank()) ev.dateIso == targetDateIso
                else ev.dayNumber == selectedDate.dayOfMonth
            }
        } else {
            events.filter { it.dayNumber == selectedDay }
        }
    }

    val today = remember {
        try {
            java.time.LocalDate.now()
        } catch (_: Throwable) {
            null
        }
    }
    val todayDayNumber = today?.dayOfMonth ?: 3
    val monthLabel = remember(selectedDate, today) {
        val target = selectedDate ?: today
        if (target != null) {
            val m = target.monthValue
            "${target.year} / ${if (m < 10) "0$m" else "$m"}"
        } else {
            "2026 / 10"
        }
    }
    val calendarDays = remember(today, selectedDate, events) {
        if (today != null) {
            val baseDate = selectedDate ?: today
            val dayOfWeek = baseDate.dayOfWeek.value
            val monday = baseDate.minusDays((dayOfWeek - 1).toLong())
            val dayLabels = listOf("一", "二", "三", "四", "五", "六", "日")
            (0..6).map { idx ->
                val d = monday.plusDays(idx.toLong())
                val dIso = d.toString()
                val count = events.count { ev ->
                    if (ev.dateIso.isNotBlank()) ev.dateIso == dIso
                    else ev.dayNumber == d.dayOfMonth
                }
                CalendarDayItem(dayLabels[idx], d.dayOfMonth, count)
            }
        } else {
            SampleMemoryData.calendarDaysWeek1
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize()) {
            // Month Header & Week Strip
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(BgDark)
                    .padding(horizontal = 16.dp, vertical = 4.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        modifier = Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .clickable { onToggleCalendarExpanded() }
                            .padding(vertical = 4.dp, horizontal = 4.dp)
                            .testTag("toggle_month_calendar"),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = monthLabel,
                            color = TextSecondary,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Medium
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                            contentDescription = "展开日历",
                            tint = TextMuted,
                            modifier = Modifier.size(18.dp)
                        )
                    }

                    Text(
                        text = "回今天",
                        color = TextSecondary,
                        fontSize = 14.sp,
                        modifier = Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .clickable { onSelectDay(todayDayNumber) }
                            .padding(horizontal = 8.dp, vertical = 4.dp)
                            .testTag("back_to_today_button")
                    )
                }

                Spacer(modifier = Modifier.height(8.dp))

                // Week Strip
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    calendarDays.forEach { item ->
                        val isSelected = item.dayNumber == selectedDay
                        CalendarDayCell(
                            item = item,
                            isSelected = isSelected,
                            onClick = { onSelectDay(item.dayNumber) }
                        )
                    }
                }

                Spacer(modifier = Modifier.height(6.dp))
            }

            HorizontalDivider(color = DividerDark, thickness = 1.dp)

            if (needsConfigGuide) {
                HorizontalDivider(color = DividerDark, thickness = 1.dp)
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(SurfaceCardElevated)
                        .clickable { onUpgradeClick() }
                        .padding(horizontal = 16.dp, vertical = 10.dp)
                        .testTag("config_guide_banner"),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = Icons.Outlined.VpnKey,
                            contentDescription = null,
                            tint = PrimaryNeonGreen,
                            modifier = Modifier.size(16.dp)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = "尚未配置 AI 转录密钥或服务端",
                            color = TextPrimary,
                            fontSize = 13.sp
                        )
                    }
                    Text(
                        text = "去配置 >",
                        color = PrimaryBrightGreen,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }

            HorizontalDivider(color = DividerDark, thickness = 1.dp)

            // Timeline Content: Persistent 24h Timeline
            Box(modifier = Modifier.weight(1f)) {
                val listState = rememberLazyListState()
                val isToday = remember(selectedDate, today) {
                    if (selectedDate != null && today != null) selectedDate == today
                    else selectedDay == todayDayNumber
                }
                val currentHour by produceState(
                    initialValue = try { java.time.LocalTime.now().hour } catch (_: Throwable) { 12 },
                    key1 = isListening,
                    key2 = isProcessingSavedListening
                ) {
                    value = try { java.time.LocalTime.now().hour } catch (_: Throwable) { 12 }
                    while (true) {
                        kotlinx.coroutines.delay(60_000L)
                        value = try { java.time.LocalTime.now().hour } catch (_: Throwable) { 12 }
                    }
                }
                val eventsByHour = remember(dayEvents) {
                    dayEvents.groupBy { parseHour(it.startTime) ?: 12 }
                }

                LaunchedEffect(selectedDay, selectedDate) {
                    val firstEventHour = dayEvents.mapNotNull { parseHour(it.startTime) }.minOrNull()
                    val target = firstEventHour ?: if (isToday) currentHour else 8
                    listState.scrollToItem((target - 1).coerceAtLeast(0))
                }

                LazyColumn(
                    state = listState,
                    modifier = Modifier
                        .fillMaxSize()
                        .testTag("timeline_events_list"),
                    contentPadding = PaddingValues(vertical = 12.dp, horizontal = 12.dp)
                ) {
                    items(24, key = { "hour_$it" }) { h ->
                        val hourLabel = String.format("%02d:00", h)
                        val hourEvents = eventsByHour[h].orEmpty()
                        val hasListeningNow = isToday && isListening && h == currentHour
                        val hasProcessingNow = isToday && isProcessingSavedListening && h == currentHour
                        val hasContent = hourEvents.isNotEmpty() || hasListeningNow || hasProcessingNow

                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = if (hasContent) 4.dp else 1.dp),
                            verticalAlignment = Alignment.Top
                        ) {
                            // Left Time Axis Column
                            Column(
                                modifier = Modifier
                                    .width(46.dp)
                                    .padding(end = 8.dp),
                                horizontalAlignment = Alignment.End
                            ) {
                                Text(
                                    text = hourLabel,
                                    color = if (hasContent) PrimaryNeonGreen else Color(0xFF434A45),
                                    fontSize = 11.sp,
                                    fontWeight = if (hasContent) FontWeight.Bold else FontWeight.Normal
                                )
                                Spacer(modifier = Modifier.height(4.dp))
                                Box(
                                    modifier = Modifier
                                        .width(2.dp)
                                        .height(if (hasContent) 32.dp else 20.dp)
                                        .clip(RoundedCornerShape(1.dp))
                                        .background(if (hasContent) PrimaryNeonGreen.copy(alpha = 0.5f) else Color(0xFF1F2521))
                                )
                            }

                            // Right Content Column
                            Column(
                                modifier = Modifier
                                    .weight(1f)
                                    .padding(start = 2.dp)
                            ) {
                                if (!hasContent) {
                                    Box(
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .padding(top = 7.dp, bottom = 12.dp)
                                            .height(1.dp)
                                            .background(Color(0xFF1B201D))
                                    )
                                } else {
                                    Box(
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .padding(top = 7.dp, bottom = 8.dp)
                                            .height(1.dp)
                                            .background(Color(0xFF293B2F))
                                    )

                                    if (hasListeningNow) {
                                        ActiveListeningTimelineCard(onStop = onStopListening)
                                        Spacer(modifier = Modifier.height(10.dp))
                                    }
                                    if (hasProcessingNow) {
                                        ListeningSavedTimelineCard()
                                        Spacer(modifier = Modifier.height(10.dp))
                                    }

                                    hourEvents.forEach { event ->
                                        TimelineEventCard(
                                            event = event,
                                            onClick = { onEventClick(event) },
                                            onScatteredClick = {
                                                if (event.subFragments.isNotEmpty()) {
                                                    onScatteredFragmentsClick(event.subFragments)
                                                }
                                            }
                                        )
                                        Spacer(modifier = Modifier.height(10.dp))
                                    }
                                }
                            }
                        }
                    }

                    item {
                        Spacer(modifier = Modifier.height(72.dp))
                    }
                }

                // Bottom Expandable Month Calendar Sheet Overlay
                androidx.compose.animation.AnimatedVisibility(
                    visible = isCalendarExpanded,
                    modifier = Modifier.align(Alignment.BottomCenter)
                ) {
                    MonthCalendarSheet(
                        selectedDay = selectedDay,
                        monthLabel = monthLabel,
                        todayDayNumber = todayDayNumber,
                        selectedDate = selectedDate,
                        onSelectDate = { date ->
                            onSelectDate?.invoke(date)
                            onSelectDay(date.dayOfMonth)
                            onToggleCalendarExpanded()
                        },
                        onSelectDay = {
                            onSelectDay(it)
                            onToggleCalendarExpanded()
                        },
                        onClose = onToggleCalendarExpanded
                    )
                }
            }
        }

        // Floating Chat / Quick Note Action Button at bottom right
        FloatingActionButton(
            onClick = onQuickAiClick,
            containerColor = Color(0xFF232825),
            contentColor = TextPrimary,
            shape = CircleShape,
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(end = 20.dp, bottom = 20.dp)
                .size(52.dp)
                .testTag("timeline_fab_button")
        ) {
            Icon(
                imageVector = Icons.Outlined.ChatBubbleOutline,
                contentDescription = "快速追问记忆",
                modifier = Modifier.size(22.dp)
            )
        }
    }
}

@Composable
private fun CalendarDayCell(
    item: CalendarDayItem,
    isSelected: Boolean,
    onClick: () -> Unit
) {
    Column(
        modifier = Modifier
            .width(44.dp)
            .clip(RoundedCornerShape(8.dp))
            .clickable { onClick() }
            .padding(vertical = 4.dp)
            .testTag("calendar_day_${item.dayNumber}"),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = item.weekDayLabel,
            color = if (isSelected) PrimaryNeonGreen else TextMuted,
            fontSize = 12.sp
        )
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = item.dayNumber.toString(),
            color = if (isSelected) PrimaryNeonGreen else TextPrimary,
            fontSize = 20.sp,
            fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Medium
        )
        Spacer(modifier = Modifier.height(4.dp))
        // Indicator bar + dots
        Box(
            modifier = Modifier
                .width(24.dp)
                .height(3.dp)
                .clip(RoundedCornerShape(2.dp))
                .background(if (isSelected) PrimaryNeonGreen else Color(0xFF2A302C))
        )
        Spacer(modifier = Modifier.height(4.dp))
        Row(
            horizontalArrangement = Arrangement.spacedBy(3.dp),
            modifier = Modifier.height(5.dp)
        ) {
            repeat(item.dotCount) {
                Box(
                    modifier = Modifier
                        .size(4.dp)
                        .clip(CircleShape)
                        .background(if (isSelected) PrimaryNeonGreen else TextSecondary)
                )
            }
        }
    }
}

private fun parseHour(timeStr: String): Int? {
    val part = timeStr.trim().split(":").firstOrNull() ?: return null
    return part.toIntOrNull()
}

@Composable
private fun TimelineEventCard(
    event: MemoryEvent,
    onClick: () -> Unit,
    onScatteredClick: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("event_card_${event.id}")
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(SurfaceCardGreen)
                .border(1.dp, SurfaceCardGreenBorder, RoundedCornerShape(12.dp))
                .clickable { onClick() }
                .padding(16.dp)
        ) {
            if (event.categoryWatermark.isNotEmpty()) {
                Text(
                    text = event.categoryWatermark,
                    color = Color(0xFF24282C),
                    fontSize = 26.sp,
                    fontWeight = FontWeight.Black,
                    fontStyle = FontStyle.Italic,
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .offset(x = 4.dp, y = (-6).dp)
                )
            }

            Column(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = "${event.startTime} - ${event.endTime} · ${event.durationLabel}",
                    color = TextSecondary,
                    fontSize = 12.sp
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    text = event.title,
                    color = TextPrimary,
                    fontSize = 17.sp,
                    fontWeight = FontWeight.SemiBold,
                    lineHeight = 24.sp
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    text = "“${event.summaryOverview}”",
                    color = TextSecondary,
                    fontSize = 13.sp,
                    lineHeight = 20.sp,
                    maxLines = 4,
                    overflow = TextOverflow.Ellipsis
                )
                Spacer(modifier = Modifier.height(12.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    event.speakers.forEach { speaker ->
                        SpeakerChip(speaker)
                    }
                    if (event.unrecognizedVoiceCount > 0) {
                        Surface(
                            shape = RoundedCornerShape(6.dp),
                            color = Color(0xFF2D2619)
                        ) {
                            Text(
                                text = "待确认声音",
                                color = Color(0xFFE5A93C),
                                fontSize = 11.sp,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp)
                            )
                        }
                    }
                }
            }
        }

        if (event.subFragments.isNotEmpty()) {
            Spacer(modifier = Modifier.height(8.dp))
            ScatteredDialoguesCard(
                startTime = event.subFragmentsStartTime,
                endTime = event.subFragmentsEndTime,
                fragmentsCount = event.subFragments.size,
                onClick = onScatteredClick
            )
        }
    }
}

@Composable
private fun ScatteredDialoguesCard(
    startTime: String,
    endTime: String,
    fragmentsCount: Int,
    onClick: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(SurfaceCardElevated)
            .border(1.dp, SurfaceCardGreenBorder, RoundedCornerShape(8.dp))
            .clickable { onClick() }
            .padding(horizontal = 14.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(6.dp)
                .clip(CircleShape)
                .background(PrimaryNeonGreen)
        )
        Spacer(modifier = Modifier.width(8.dp))
        Text(
            text = "零散对话 ($fragmentsCount 段)",
            color = TextSecondary,
            fontSize = 13.sp,
            modifier = Modifier.weight(1f)
        )
        if (startTime.isNotEmpty() && endTime.isNotEmpty()) {
            Text(
                text = "$startTime - $endTime",
                color = TextMuted,
                fontSize = 11.sp
            )
        }
    }
}

@Composable
private fun TimelineEventRow(
    event: MemoryEvent,
    onClick: () -> Unit,
    onScatteredClick: () -> Unit
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        // Main Event Row
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .testTag("event_card_${event.id}"),
            verticalAlignment = Alignment.Top
        ) {
            // Left Time Axis Column
            Column(
                modifier = Modifier
                    .width(46.dp)
                    .padding(end = 6.dp),
                horizontalAlignment = Alignment.End
            ) {
                Text(
                    text = event.startTime,
                    color = TextSecondary,
                    fontSize = 11.sp
                )
                Spacer(modifier = Modifier.height(10.dp))
                // Vertical solid bar
                Box(
                    modifier = Modifier
                        .width(3.dp)
                        .height(64.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(Color(0xFF384048))
                )
                Spacer(modifier = Modifier.height(10.dp))
                Text(
                    text = event.endTime,
                    color = TextSecondary,
                    fontSize = 11.sp
                )
            }

            // Right Event Card
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(12.dp))
                    .background(SurfaceCardGreen)
                    .border(1.dp, SurfaceCardGreenBorder, RoundedCornerShape(12.dp))
                    .clickable { onClick() }
                    .padding(16.dp)
            ) {
                // Background Watermark (LEARNING, MEETING, WORK, MEAL, SOCIAL)
                if (event.categoryWatermark.isNotEmpty()) {
                    Text(
                        text = event.categoryWatermark,
                        color = Color(0xFF24282C),
                        fontSize = 26.sp,
                        fontWeight = FontWeight.Black,
                        fontStyle = FontStyle.Italic,
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .offset(x = 4.dp, y = (-6).dp)
                    )
                }

                Column(modifier = Modifier.fillMaxWidth()) {
                    Text(
                        text = "${event.startTime} - ${event.endTime} · ${event.durationLabel}",
                        color = TextSecondary,
                        fontSize = 12.sp
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = event.title,
                        color = TextPrimary,
                        fontSize = 17.sp,
                        fontWeight = FontWeight.SemiBold,
                        lineHeight = 24.sp
                    )
                    if (event.speakers.isNotEmpty() || event.unrecognizedVoiceCount > 0) {
                        Spacer(modifier = Modifier.height(12.dp))
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            event.speakers.forEach { speaker ->
                                SpeakerChip(name = speaker)
                            }
                            if (event.unrecognizedVoiceCount > 0) {
                                Text(
                                    text = "${event.unrecognizedVoiceCount}个未识别的声音",
                                    color = TextSecondary,
                                    fontSize = 12.sp
                                )
                            }
                        }
                    }
                }
            }
        }

        // Optional Sub-fragments ("零散对话") below main card with its own time axis
        if (event.subFragments.isNotEmpty()) {
            Spacer(modifier = Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.Top
            ) {
                Column(
                    modifier = Modifier
                        .width(46.dp)
                        .padding(end = 6.dp),
                    horizontalAlignment = Alignment.End
                ) {
                    if (event.subFragmentsStartTime.isNotEmpty()) {
                        Text(
                            text = event.subFragmentsStartTime,
                            color = TextSecondary,
                            fontSize = 11.sp
                        )
                    }
                    Spacer(modifier = Modifier.height(6.dp))
                    repeat(event.subFragments.size.coerceAtLeast(2)) {
                        Box(
                            modifier = Modifier
                                .padding(vertical = 4.dp)
                                .width(3.dp)
                                .height(10.dp)
                                .clip(RoundedCornerShape(1.5.dp))
                                .background(DividerDark)
                        )
                    }
                    Spacer(modifier = Modifier.height(6.dp))
                    if (event.subFragmentsEndTime.isNotEmpty()) {
                        Text(
                            text = event.subFragmentsEndTime,
                            color = TextSecondary,
                            fontSize = 11.sp
                        )
                    }
                }

                if (event.subFragments.size == 1) {
                    val frag = event.subFragments.first()
                    Row(
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(8.dp))
                            .background(SurfaceCardElevated)
                            .border(1.dp, SurfaceCardGreenBorder, RoundedCornerShape(8.dp))
                            .clickable { onScatteredClick() }
                            .padding(horizontal = 14.dp, vertical = 8.dp)
                            .testTag("scattered_fragments_${event.id}"),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            modifier = Modifier
                                .size(6.dp)
                                .clip(CircleShape)
                                .background(PrimaryNeonGreen)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = frag.title,
                            color = TextSecondary,
                            fontSize = 13.sp,
                            modifier = Modifier.weight(1f)
                        )
                        Text(
                            text = frag.duration,
                            color = TextMuted,
                            fontSize = 11.sp
                        )
                    }
                } else {
                    Column(
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(10.dp))
                            .background(SurfaceCardElevated)
                            .border(1.dp, SurfaceCardGreenBorder, RoundedCornerShape(10.dp))
                            .clickable { onScatteredClick() }
                            .padding(horizontal = 16.dp, vertical = 12.dp)
                            .testTag("scattered_fragments_${event.id}"),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        event.subFragments.forEach { frag ->
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    text = frag.title,
                                    color = Color(0xFF7FA388),
                                    fontSize = 14.sp
                                )
                                Text(
                                    text = frag.duration,
                                    color = TextMuted,
                                    fontSize = 12.sp
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ActiveListeningTimelineCard(
    onStop: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.Top
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(Color(0xFF14261B))
                .border(1.5.dp, PrimaryNeonGreen, RoundedCornerShape(12.dp))
                .clickable { onStop() }
                .padding(16.dp)
                .testTag("active_listening_card")
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.weight(1f)) {
                    Box(
                        modifier = Modifier
                            .size(10.dp)
                            .clip(CircleShape)
                            .background(PrimaryNeonGreen)
                    )
                    Spacer(modifier = Modifier.width(10.dp))
                    Column {
                        Text(
                            text = "正在全时聆听记忆...",
                            color = TextPrimary,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Bold
                        )
                        Spacer(modifier = Modifier.height(2.dp))
                        Text(
                            text = "麦克风实时采集活跃 · 点击停止并整理",
                            color = Color(0xFF7FA388),
                            fontSize = 12.sp
                        )
                    }
                }
                Icon(
                    imageVector = Icons.Default.Stop,
                    contentDescription = "停止聆听",
                    tint = DangerRed,
                    modifier = Modifier.size(24.dp)
                )
            }
        }
    }
}

@Composable
fun ListeningSavedTimelineCard() {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.Top
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(Color(0xFF171F1A))
                .border(1.dp, Color(0xFF283B2E), RoundedCornerShape(12.dp))
                .padding(14.dp)
                .testTag("listening_saved_card")
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    color = PrimaryNeonGreen,
                    strokeWidth = 2.dp
                )
                Spacer(modifier = Modifier.width(12.dp))
                Column {
                    Text(
                        text = "音频已安全保存在本机设备上",
                        color = TextPrimary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium
                    )
                    Spacer(modifier = Modifier.height(2.dp))
                    Text(
                        text = "后台正在安全生成记忆结构化摘要与活动切分…",
                        color = TextMuted,
                        fontSize = 12.sp
                    )
                }
            }
        }
    }
}

@Composable
fun SpeakerChip(
    name: String,
    onClick: (() -> Unit)? = null
) {
    Surface(
        shape = RoundedCornerShape(6.dp),
        color = TagBg,
        modifier = if (onClick != null) Modifier.clickable { onClick() } else Modifier
    ) {
        Text(
            text = name,
            color = TagText,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp)
        )
    }
}

@Composable
private fun EmptyDayTimelineView() {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 12.dp)
    ) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.SpaceBetween
        ) {
            val hours = listOf(
                "01:00", "02:00", "03:00", "04:00", "05:00", "06:00",
                "07:00", "08:00", "09:00", "10:00", "11:00", "12:00",
                "13:00", "14:00", "15:00", "16:00", "17:00", "18:00",
                "19:00", "20:00"
            )
            hours.forEach { hour ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = hour,
                        color = Color(0xFF434A45),
                        fontSize = 10.sp,
                        modifier = Modifier.width(36.dp)
                    )
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .height(1.dp)
                            .background(Color(0xFF1A1F1C))
                    )
                }
            }
        }

        Text(
            text = "这一天没有聆听记录",
            color = TextMuted,
            fontSize = 14.sp,
            modifier = Modifier.align(Alignment.Center)
        )
    }
}

private data class CalendarMonthCell(
    val dayNumber: Int,
    val date: java.time.LocalDate,
    val isCurrentMonth: Boolean
)

@Composable
private fun MonthCalendarSheet(
    selectedDay: Int,
    monthLabel: String,
    todayDayNumber: Int,
    selectedDate: java.time.LocalDate? = null,
    onSelectDate: ((java.time.LocalDate) -> Unit)? = null,
    onSelectDay: (Int) -> Unit,
    onClose: () -> Unit
) {
    var displayYearMonth by remember {
        mutableStateOf(
            selectedDate?.let { java.time.YearMonth.from(it) }
                ?: try {
                    java.time.YearMonth.now()
                } catch (_: Throwable) {
                    java.time.YearMonth.of(2026, 11)
                }
        )
    }

    val dynamicMonthLabel = remember(displayYearMonth) {
        val m = displayYearMonth.monthValue
        "${displayYearMonth.year} / ${if (m < 10) "0$m" else "$m"}"
    }

    val weeks = remember(displayYearMonth) {
        val firstDay = displayYearMonth.atDay(1)
        val startDow = firstDay.dayOfWeek.value % 7 // 0 = Sunday, 1 = Monday ...
        val daysInMonth = displayYearMonth.lengthOfMonth()
        val prevMonth = displayYearMonth.minusMonths(1)
        val daysInPrevMonth = prevMonth.lengthOfMonth()

        val list = mutableListOf<CalendarMonthCell>()
        for (i in (startDow - 1) downTo 0) {
            val d = daysInPrevMonth - i
            list.add(CalendarMonthCell(d, prevMonth.atDay(d), isCurrentMonth = false))
        }
        for (d in 1..daysInMonth) {
            list.add(CalendarMonthCell(d, displayYearMonth.atDay(d), isCurrentMonth = true))
        }
        val remaining = (7 - (list.size % 7)) % 7
        val nextMonth = displayYearMonth.plusMonths(1)
        for (d in 1..remaining) {
            list.add(CalendarMonthCell(d, nextMonth.atDay(d), isCurrentMonth = false))
        }
        list.chunked(7)
    }

    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp),
        color = Color(0xFF181C19),
        tonalElevation = 8.dp
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
        ) {
            Box(
                modifier = Modifier
                    .width(36.dp)
                    .height(4.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(TextMuted)
                    .align(Alignment.CenterHorizontally)
                    .clickable { onClose() }
            )
            Spacer(modifier = Modifier.height(16.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = dynamicMonthLabel,
                    color = TextPrimary,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "今天",
                        color = TextSecondary,
                        fontSize = 14.sp,
                        modifier = Modifier
                            .clickable {
                                val today = try { java.time.LocalDate.now() } catch (_: Throwable) { null }
                                if (today != null) {
                                    displayYearMonth = java.time.YearMonth.from(today)
                                    onSelectDate?.invoke(today)
                                    onSelectDay(today.dayOfMonth)
                                } else {
                                    onSelectDay(todayDayNumber)
                                }
                            }
                            .padding(horizontal = 8.dp, vertical = 4.dp)
                    )
                    IconButton(
                        onClick = { displayYearMonth = displayYearMonth.minusMonths(1) },
                        modifier = Modifier.size(32.dp)
                    ) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowLeft,
                            contentDescription = "上一月",
                            tint = TextSecondary
                        )
                    }
                    IconButton(
                        onClick = { displayYearMonth = displayYearMonth.plusMonths(1) },
                        modifier = Modifier.size(32.dp)
                    ) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                            contentDescription = "下一月",
                            tint = TextSecondary
                        )
                    }
                }
            }
            Spacer(modifier = Modifier.height(12.dp))
            val weekHeaders = listOf("周日", "周一", "周二", "周三", "周四", "周五", "周六")
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceAround
            ) {
                weekHeaders.forEach { w ->
                    Text(text = w, color = TextMuted, fontSize = 12.sp)
                }
            }
            Spacer(modifier = Modifier.height(12.dp))
            weeks.forEach { week ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 6.dp),
                    horizontalArrangement = Arrangement.SpaceAround
                ) {
                    week.forEach { cell ->
                        val isSelected = if (selectedDate != null) {
                            cell.date == selectedDate
                        } else {
                            cell.isCurrentMonth && cell.dayNumber == selectedDay
                        }
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(if (isSelected) PrimaryNeonGreen.copy(alpha = 0.2f) else Color.Transparent)
                                .clickable {
                                    onSelectDate?.invoke(cell.date)
                                    onSelectDay(cell.dayNumber)
                                },
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = cell.dayNumber.toString(),
                                color = when {
                                    isSelected -> PrimaryNeonGreen
                                    !cell.isCurrentMonth -> TextMuted.copy(alpha = 0.4f)
                                    else -> TextPrimary
                                },
                                fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                                fontSize = 15.sp
                            )
                        }
                    }
                }
            }
        }
    }
}
