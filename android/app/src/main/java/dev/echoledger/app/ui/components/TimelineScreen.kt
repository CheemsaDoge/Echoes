package dev.echoledger.app.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
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
    isListening: Boolean = false,
    isProcessingSavedListening: Boolean = false,
    onStopListening: () -> Unit = {},
    onEventClick: (MemoryEvent) -> Unit,
    onScatteredFragmentsClick: (List<SubFragmentItem>) -> Unit,
    onUpgradeClick: () -> Unit,
    onQuickAiClick: () -> Unit
) {
    val dayEvents = remember(selectedDay, events) {
        events.filter { it.dayNumber == selectedDay }
    }

    val today = remember {
        try {
            java.time.LocalDate.now()
        } catch (_: Throwable) {
            null
        }
    }
    val todayDayNumber = today?.dayOfMonth ?: 3
    val monthLabel = remember(today) {
        if (today != null) {
            val m = today.monthValue
            "${today.year} / ${if (m < 10) "0$m" else "$m"}"
        } else {
            "2026 / 10"
        }
    }
    val calendarDays = remember(today, events) {
        if (today != null) {
            val dayOfWeek = today.dayOfWeek.value
            val monday = today.minusDays((dayOfWeek - 1).toLong())
            val dayLabels = listOf("一", "二", "三", "四", "五", "六", "日")
            (0..6).map { idx ->
                val d = monday.plusDays(idx.toLong())
                val count = events.count { it.dayNumber == d.dayOfMonth }
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

            // Upgrade Quota Banner
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color(0xFF151C17))
                    .clickable { onUpgradeClick() }
                    .padding(horizontal = 16.dp, vertical = 10.dp)
                    .testTag("quota_upgrade_banner"),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        imageVector = Icons.Outlined.AutoAwesome,
                        contentDescription = null,
                        tint = PrimaryNeonGreen,
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = "转写额度不足 1 小时，仍可聆听",
                        color = TextPrimary,
                        fontSize = 13.sp
                    )
                }
                Text(
                    text = "去升级 >",
                    color = PrimaryNeonGreen,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }

            HorizontalDivider(color = DividerDark, thickness = 1.dp)

            // Timeline Content
            Box(modifier = Modifier.weight(1f)) {
                if (dayEvents.isEmpty() && !isListening && !isProcessingSavedListening) {
                    EmptyDayTimelineView()
                } else {
                    LazyColumn(
                        modifier = Modifier
                            .fillMaxSize()
                            .testTag("timeline_events_list"),
                        contentPadding = PaddingValues(vertical = 12.dp, horizontal = 12.dp),
                        verticalArrangement = Arrangement.spacedBy(16.dp)
                    ) {
                        if (isListening) {
                            item(key = "active_listening_card") {
                                ActiveListeningTimelineCard(onStop = onStopListening)
                            }
                        }
                        if (isProcessingSavedListening) {
                            item(key = "listening_saved_card") {
                                ListeningSavedTimelineCard()
                            }
                        }
                        items(dayEvents, key = { it.id }) { event ->
                            TimelineEventRow(
                                event = event,
                                onClick = { onEventClick(event) },
                                onScatteredClick = {
                                    if (event.subFragments.isNotEmpty()) {
                                        onScatteredFragmentsClick(event.subFragments)
                                    }
                                }
                            )
                        }
                        item {
                            Spacer(modifier = Modifier.height(72.dp))
                        }
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
                // Vertical solid green/dark bar
                Box(
                    modifier = Modifier
                        .width(3.dp)
                        .height(64.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(Color(0xFF2A4030))
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
                        color = Color(0xFF223628),
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
                                .background(Color(0xFF233629))
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
                            .background(Color(0xFF141C16))
                            .border(1.dp, Color(0xFF1E2D22), RoundedCornerShape(8.dp))
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
                            color = Color(0xFF7FA388),
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
                            .background(Color(0xFF141C16))
                            .border(1.dp, Color(0xFF1E2D22), RoundedCornerShape(10.dp))
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
        Column(
            modifier = Modifier.width(46.dp).padding(end = 6.dp),
            horizontalAlignment = Alignment.End
        ) {
            Text("NOW", color = PrimaryNeonGreen, fontSize = 11.sp, fontWeight = FontWeight.Bold)
            Spacer(modifier = Modifier.height(6.dp))
            Box(
                modifier = Modifier
                    .width(3.dp)
                    .height(52.dp)
                    .clip(RoundedCornerShape(1.5.dp))
                    .background(PrimaryNeonGreen)
            )
        }
        Box(
            modifier = Modifier
                .weight(1f)
                .clip(RoundedCornerShape(12.dp))
                .background(Color(0xFF14261B))
                .border(1.5.dp, PrimaryNeonGreen, RoundedCornerShape(12.dp))
                .clickable { onStop() }
                .padding(16.dp)
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
        Column(
            modifier = Modifier.width(46.dp).padding(end = 6.dp),
            horizontalAlignment = Alignment.End
        ) {
            Text("SAVED", color = TextSecondary, fontSize = 10.sp)
            Spacer(modifier = Modifier.height(6.dp))
            Box(
                modifier = Modifier
                    .width(3.dp)
                    .height(46.dp)
                    .clip(RoundedCornerShape(1.5.dp))
                    .background(DividerDark)
            )
        }
        Box(
            modifier = Modifier
                .weight(1f)
                .clip(RoundedCornerShape(12.dp))
                .background(Color(0xFF171F1A))
                .border(1.dp, Color(0xFF283B2E), RoundedCornerShape(12.dp))
                .padding(14.dp)
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

@Composable
private fun MonthCalendarSheet(
    selectedDay: Int,
    monthLabel: String,
    todayDayNumber: Int,
    onSelectDay: (Int) -> Unit,
    onClose: () -> Unit
) {
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
                    text = monthLabel,
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
                            .clickable { onSelectDay(todayDayNumber) }
                            .padding(horizontal = 8.dp, vertical = 4.dp)
                    )
                    IconButton(onClick = onClose, modifier = Modifier.size(32.dp)) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowLeft,
                            contentDescription = "上一月",
                            tint = TextSecondary
                        )
                    }
                    IconButton(onClick = onClose, modifier = Modifier.size(32.dp)) {
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
            val weeks = listOf(
                listOf(27, 28, 29, 30, 1, 2, 3),
                listOf(4, 5, 6, 7, 8, 9, 10),
                listOf(11, 12, 13, 14, 15, 16, 17),
                listOf(18, 19, 20, 21, 22, 23, 24)
            )
            weeks.forEach { week ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 8.dp),
                    horizontalArrangement = Arrangement.SpaceAround
                ) {
                    week.forEach { d ->
                        val isSelected = d == selectedDay
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(if (isSelected) PrimaryNeonGreen.copy(alpha = 0.2f) else Color.Transparent)
                                .clickable { onSelectDay(d) },
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = d.toString(),
                                color = if (isSelected) PrimaryNeonGreen else TextPrimary,
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
