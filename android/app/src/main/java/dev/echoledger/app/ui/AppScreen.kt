@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
package dev.echoledger.app.ui

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.echoledger.app.capture.CaptureState
import dev.echoledger.app.data.LocalSession
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val Background = Color(0xFF121416)
private val Panel = Color(0xFF1D2226)
private val PanelBorder = Color(0xFF2B3338)
private val Muted = Color(0xFF909A96)
private val Accent = Color(0xFFA8CA68)
private val Teal = Color(0xFF68BDA9)
private val Danger = Color(0xFFE57373)
private val TextWhite = Color(0xFFEAEAEA)

private fun clock(ms: Long): String =
    Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("HH:mm"))

private fun formatTimestamp(ms: Long): String =
    Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("MM-dd HH:mm:ss"))

private fun formatDuration(samples: Long): String {
    val totalSeconds = samples / 16000
    val m = totalSeconds / 60
    val s = totalSeconds % 60
    return "%02d:%02d".format(m, s)
}

private fun formatClock(samples: Long): String {
    val totalSeconds = samples / 16000
    val h = totalSeconds / 3600
    val m = (totalSeconds / 60) % 60
    val s = totalSeconds % 60
    return "%02d:%02d:%02d".format(h, m, s)
}

private fun color(hex: String): Color = runCatching { Color(android.graphics.Color.parseColor(hex)) }.getOrDefault(Teal)

@Composable
fun AppScreen(vm: AppViewModel, onRecord: (Boolean) -> Unit, onStop: () -> Unit) {
    val recording by vm.recording.collectAsStateWithLifecycle()
    val pending by vm.pending.collectAsStateWithLifecycle()
    val day by vm.day.collectAsStateWithLifecycle()
    val allEvents by vm.events.collectAsStateWithLifecycle()
    val people by vm.people.collectAsStateWithLifecycle()
    val memories by vm.memories.collectAsStateWithLifecycle()
    val types by vm.types.collectAsStateWithLifecycle()
    val statuses by vm.status.collectAsStateWithLifecycle()
    val message by vm.repo.message.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    val detail by vm.detail.collectAsStateWithLifecycle()
    val sessions by vm.localSessions.collectAsStateWithLifecycle()
    val sessionStats by vm.sessionStats.collectAsStateWithLifecycle()
    val playingSessionId by vm.playingSessionId.collectAsStateWithLifecycle()

    var tab by rememberSaveable { mutableIntStateOf(0) }
    val zone = ZoneId.systemDefault()
    val from = day.atStartOfDay(zone).toInstant().toEpochMilli()
    val to = day.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
    val events = allEvents.filter { it.optLong("end_ms") >= from && it.optLong("start_ms") < to }

    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Accent,
            secondary = Teal,
            background = Background,
            surface = Panel,
            onPrimary = Background,
            onSurface = TextWhite,
            outline = PanelBorder
        )
    ) {
        Scaffold(
            containerColor = Background,
            bottomBar = {
                NavigationBar(containerColor = Panel, tonalElevation = 0.dp) {
                    val labels = listOf("时间线", "人物", "记忆", "设置")
                    val icons = listOf(Icons.Outlined.History, Icons.Outlined.People, Icons.Outlined.Layers, Icons.Outlined.Tune)
                    labels.forEachIndexed { i, label ->
                        NavigationBarItem(
                            selected = tab == i,
                            onClick = { tab = i },
                            icon = { Icon(icons[i], label) },
                            label = { Text(label) },
                            colors = NavigationBarItemDefaults.colors(
                                indicatorColor = Accent.copy(alpha = .15f),
                                selectedIconColor = Accent,
                                selectedTextColor = Accent,
                                unselectedIconColor = Muted,
                                unselectedTextColor = Muted
                            )
                        )
                    }
                }
            }
        ) { padding ->
            Column(Modifier.fillMaxSize().padding(padding)) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .statusBarsPadding()
                        .padding(horizontal = 20.dp, vertical = 12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text("听记", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = TextWhite)
                        Text("ECHOLEDGER", color = Muted, fontSize = 10.sp, letterSpacing = 1.2.sp)
                    }
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Surface(
                            shape = RoundedCornerShape(12.dp),
                            color = Accent.copy(alpha = 0.12f),
                            border = BorderStroke(1.dp, Accent.copy(alpha = 0.35f))
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp)
                            ) {
                                Box(
                                    Modifier
                                        .size(6.dp)
                                        .background(if (recording.recording) Color(0xFFFF5252) else Accent, CircleShape)
                                )
                                Spacer(Modifier.width(6.dp))
                                Text("16kHz PCM", color = Accent, fontSize = 11.sp, fontWeight = FontWeight.Medium)
                            }
                        }
                        IconButton(onClick = { vm.refresh() }) {
                            Icon(Icons.Outlined.Sync, "同步", tint = Accent)
                        }
                    }
                }

                if (busy) LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp), color = Accent)
                else Spacer(Modifier.height(2.dp))

                if (message.isNotBlank()) Notice(message, warning = true)
                if (recording.error.isNotBlank()) Notice(recording.error, warning = true)
                if (statuses.firstOrNull()?.optBoolean("is_demo") == true) Notice("演示模式：转写文本是测试数据，未连接真实识别服务。")

                Box(Modifier.weight(1f)) {
                    when (tab) {
                        0 -> Timeline(
                            state = recording,
                            pending = pending,
                            day = day,
                            events = events,
                            sessions = sessions,
                            sessionStats = sessionStats,
                            playingSessionId = playingSessionId,
                            selectDay = vm::selectDay,
                            start = { onRecord(false) },
                            calibrate = { onRecord(true) },
                            stop = onStop,
                            open = vm::openEvent,
                            vm = vm
                        )
                        1 -> People(people, vm)
                        2 -> Memories(memories, vm)
                        else -> Settings(
                            vm = vm,
                            recording = recording,
                            status = statuses.firstOrNull(),
                            types = types,
                            onRecord = onRecord,
                            sessions = sessions,
                            sessionStats = sessionStats,
                            playingSessionId = playingSessionId
                        )
                    }
                }
            }
        }
        detail?.let { EventDialog(it, people, vm) }
    }
}

@Composable
private fun Notice(text: String, warning: Boolean = false) {
    Text(
        text,
        color = if (warning) Color(0xFFE5B58C) else Muted,
        fontSize = 12.sp,
        modifier = Modifier
            .fillMaxWidth()
            .background(if (warning) Color(0xFF2C241E) else Panel)
            .padding(horizontal = 20.dp, vertical = 8.dp)
    )
}

@Composable
private fun Timeline(
    state: CaptureState,
    pending: Int,
    day: LocalDate,
    events: List<JSONObject>,
    sessions: List<LocalSession>,
    sessionStats: Map<String, SessionAudioInfo>,
    playingSessionId: String?,
    selectDay: (LocalDate) -> Unit,
    start: () -> Unit,
    calibrate: () -> Unit,
    stop: () -> Unit,
    open: (String) -> Unit,
    vm: AppViewModel
) {
    val context = LocalContext.current
    var deletingId by remember { mutableStateOf<String?>(null) }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        item {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(day.format(DateTimeFormatter.ofPattern("yyyy / MM")), color = Muted, fontSize = 15.sp, fontWeight = FontWeight.Medium)
                TextButton(onClick = { selectDay(LocalDate.now()) }) { Text("回今天", fontSize = 13.sp) }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                (6 downTo 0).forEach { back ->
                    val date = LocalDate.now().minusDays(back.toLong())
                    val selected = day == date
                    Column(
                        Modifier
                            .width(42.dp)
                            .clip(RoundedCornerShape(10.dp))
                            .clickable { selectDay(date) }
                            .padding(vertical = 6.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text("一二三四五六日"[date.dayOfWeek.value - 1].toString(), color = if (selected) Accent else Muted, fontSize = 12.sp)
                        Text(date.dayOfMonth.toString(), color = if (selected) Accent else TextWhite, fontSize = 20.sp, fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
                        Text(if (selected) "●" else " ", color = Accent, fontSize = 7.sp)
                    }
                }
            }
        }

        item {
            RecordingControlCard(
                state = state,
                pending = pending,
                onStart = start,
                onCalibrate = calibrate,
                onStop = stop
            )
        }

        if (sessions.isNotEmpty()) {
            item {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 2.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text("本机录音与 Chunks 导出", fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        FilledTonalButton(
                            onClick = { vm.saveAllToDownloads() },
                            colors = ButtonDefaults.filledTonalButtonColors(containerColor = Accent.copy(alpha = 0.16f), contentColor = Accent),
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                            shape = RoundedCornerShape(8.dp)
                        ) {
                            Icon(Icons.Outlined.FolderZip, null, modifier = Modifier.size(15.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("导出全部(ZIP)", fontSize = 12.sp)
                        }
                        IconButton(
                            onClick = { vm.shareAllSessions(context) },
                            modifier = Modifier.size(32.dp)
                        ) {
                            Icon(Icons.Outlined.Share, "分享全部", tint = Accent, modifier = Modifier.size(17.dp))
                        }
                    }
                }
            }

            items(sessions.take(6), key = { it.id }) { session ->
                val stats = sessionStats[session.id]
                val isPlaying = playingSessionId == session.id
                SessionChunkCard(
                    session = session,
                    stats = stats,
                    isPlaying = isPlaying,
                    onPlayToggle = { vm.playSession(session.id) },
                    onSaveZip = { vm.saveToDownloads(session.id, asZip = true) },
                    onShareZip = { vm.shareSession(context, session.id, asZip = true) },
                    onSaveWav = { vm.saveToDownloads(session.id, asZip = false) },
                    onDelete = { deletingId = session.id }
                )
            }
        }

        item {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 2.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("当天事件", fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)
                Text("${events.size} 条", fontSize = 12.sp, color = Muted)
            }
        }

        if (events.isEmpty()) {
            item { EmptyState("当天暂无事件", "录音上传并转写后将在此展示。") }
        }

        items(events, key = { it.getString("id") }) { item ->
            EventCard(item) { open(item.getString("id")) }
        }
    }

    deletingId?.let { id ->
        AlertDialog(
            onDismissRequest = { deletingId = null },
            title = { Text("删除该录音？") },
            text = { Text("将移除本机上的录音分块及原始数据。") },
            confirmButton = {
                TextButton(onClick = { vm.deleteSession(id); deletingId = null }) {
                    Text("删除", color = Danger)
                }
            },
            dismissButton = {
                TextButton(onClick = { deletingId = null }) { Text("取消") }
            }
        )
    }
}

@Composable
private fun RecordingControlCard(
    state: CaptureState,
    pending: Int,
    onStart: () -> Unit,
    onCalibrate: () -> Unit,
    onStop: () -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Panel),
        shape = RoundedCornerShape(20.dp),
        border = BorderStroke(1.dp, if (state.recording) Teal.copy(alpha = 0.6f) else PanelBorder)
    ) {
        Column(
            Modifier.fillMaxWidth().padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        if (state.recording) Icons.Outlined.GraphicEq else Icons.Outlined.Mic,
                        contentDescription = null,
                        tint = if (state.recording) Teal else Accent,
                        modifier = Modifier.size(20.dp)
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        if (state.recording) {
                            if (state.purpose == "calibration") "20s 定长采样中" else "正在录音"
                        } else "就绪",
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = if (state.recording) Teal else TextWhite
                    )
                }
                if (pending > 0) {
                    Text("待上传 $pending 块", color = Muted, fontSize = 11.sp)
                }
            }

            Row(
                verticalAlignment = Alignment.Bottom,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    formatClock(state.samples),
                    fontSize = 36.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = if (state.recording) Teal else TextWhite
                )

                Row(
                    horizontalArrangement = Arrangement.spacedBy(3.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.height(34.dp)
                ) {
                    repeat(18) { i ->
                        val factor = 1f + kotlin.math.sin(i * 0.45).toFloat() * 0.5f
                        val barHeight = if (state.recording) {
                            (4f + (state.rms * 110f * factor).coerceIn(0f, 30f)).coerceAtMost(34f)
                        } else 4f
                        Box(
                            Modifier
                                .width(3.dp)
                                .height(barHeight.dp)
                                .background(if (state.recording) Teal else PanelBorder, RoundedCornerShape(2.dp))
                        )
                    }
                }
            }

            if (state.silenced) {
                Text("麦克风已被系统静音，请检查其他应用。", color = Color(0xFFE5B58C), fontSize = 12.sp)
            }

            if (state.recording) {
                Button(
                    onClick = onStop,
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Teal),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Icon(Icons.Outlined.Stop, null, modifier = Modifier.size(20.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("停止并保存", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                }
            } else {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Button(
                        onClick = onStart,
                        modifier = Modifier.weight(1.3f).height(48.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Accent),
                        shape = RoundedCornerShape(12.dp)
                    ) {
                        Icon(Icons.Outlined.Mic, null, modifier = Modifier.size(19.dp), tint = Background)
                        Spacer(Modifier.width(6.dp))
                        Text("连续录音", color = Background, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                    }
                    OutlinedButton(
                        onClick = onCalibrate,
                        modifier = Modifier.weight(1f).height(48.dp),
                        shape = RoundedCornerShape(12.dp),
                        border = BorderStroke(1.dp, PanelBorder)
                    ) {
                        Icon(Icons.Outlined.Timer, null, modifier = Modifier.size(18.dp), tint = TextWhite)
                        Spacer(Modifier.width(6.dp))
                        Text("20s 采样", color = TextWhite, fontSize = 13.sp)
                    }
                }
            }
        }
    }
}

@Composable
private fun SessionChunkCard(
    session: LocalSession,
    stats: SessionAudioInfo?,
    isPlaying: Boolean,
    onPlayToggle: () -> Unit,
    onSaveZip: () -> Unit,
    onShareZip: () -> Unit,
    onSaveWav: () -> Unit,
    onDelete: () -> Unit
) {
    val chunkCount = stats?.chunkCount ?: 0
    val totalSamples = stats?.totalSamples ?: 0L

    Card(
        colors = CardDefaults.cardColors(containerColor = Panel),
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, if (isPlaying) Teal.copy(alpha = 0.5f) else PanelBorder)
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    formatTimestamp(session.startedAtMs),
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Medium,
                    color = TextWhite
                )
                Text(
                    "${formatDuration(totalSamples)} · $chunkCount 块",
                    fontSize = 12.sp,
                    color = Teal,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.SemiBold
                )
            }

            HorizontalDivider(color = PanelBorder.copy(alpha = 0.5f))

            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                FilledTonalButton(
                    onClick = onPlayToggle,
                    colors = ButtonDefaults.filledTonalButtonColors(
                        containerColor = if (isPlaying) Teal else Accent.copy(alpha = 0.14f),
                        contentColor = if (isPlaying) Background else Accent
                    ),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Icon(
                        if (isPlaying) Icons.Outlined.Stop else Icons.Outlined.PlayArrow,
                        null,
                        modifier = Modifier.size(15.dp)
                    )
                    Spacer(Modifier.width(4.dp))
                    Text(if (isPlaying) "停止" else "回听", fontSize = 12.sp)
                }

                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(
                        onClick = onSaveZip,
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)
                    ) {
                        Icon(Icons.Outlined.FolderZip, null, modifier = Modifier.size(15.dp), tint = Accent)
                        Spacer(Modifier.width(3.dp))
                        Text("存ZIP", fontSize = 12.sp, color = Accent)
                    }
                    TextButton(
                        onClick = onShareZip,
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)
                    ) {
                        Icon(Icons.Outlined.Share, null, modifier = Modifier.size(15.dp), tint = TextWhite)
                        Spacer(Modifier.width(3.dp))
                        Text("分享", fontSize = 12.sp, color = TextWhite)
                    }
                    TextButton(
                        onClick = onSaveWav,
                        contentPadding = PaddingValues(horizontal = 6.dp, vertical = 4.dp)
                    ) {
                        Text("WAV", fontSize = 12.sp, color = Muted)
                    }
                    IconButton(onClick = onDelete, modifier = Modifier.size(28.dp)) {
                        Icon(Icons.Outlined.Delete, null, tint = Muted.copy(alpha = 0.7f), modifier = Modifier.size(15.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun EventCard(event: JSONObject, click: () -> Unit) {
    val hue = color(event.optString("color", "#61BBA6"))
    Card(
        onClick = click,
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, PanelBorder),
        colors = CardDefaults.cardColors(containerColor = Panel)
    ) {
        Row(Modifier.fillMaxWidth().heightIn(min = 100.dp)) {
            Box(Modifier.width(4.dp).height(100.dp).background(hue))
            Column(Modifier.weight(1f).padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("${clock(event.getLong("start_ms"))} – ${clock(event.getLong("end_ms"))}", color = Muted, fontSize = 12.sp)
                    Text(event.optString("type_id", "unknown").uppercase(), color = hue, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                }
                Text(event.getString("title"), fontSize = 16.sp, lineHeight = 24.sp, maxLines = 2, overflow = TextOverflow.Ellipsis, color = TextWhite)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    val names = event.optJSONArray("speaker_names")
                    if (names != null) for (i in 0 until names.length()) Text(names.getString(i), color = Accent, fontSize = 12.sp)
                    val unknown = event.optInt("unassigned_labels", 0)
                    if (unknown > 0) Text("$unknown 个未确认声源", color = Muted, fontSize = 12.sp)
                    if (event.optInt("is_demo") == 1) Text("演示", color = Color(0xFFE5B58C), fontSize = 12.sp)
                }
            }
        }
    }
}

@Composable
private fun EmptyState(title: String, text: String) {
    Column(
        Modifier.fillMaxWidth().padding(vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Icon(Icons.Outlined.Forum, null, tint = Muted, modifier = Modifier.size(30.dp))
        Text(title, fontSize = 15.sp, color = TextWhite)
        Text(text, color = Muted, fontSize = 12.sp)
    }
}

@Composable
private fun People(people: List<JSONObject>, vm: AppViewModel) {
    var name by rememberSaveable { mutableStateOf("") }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        item {
            Text("发言人库", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)
        }
        item {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    name,
                    { name = it },
                    label = { Text("姓名或称呼") },
                    singleLine = true,
                    modifier = Modifier.weight(1f)
                )
                Button(
                    enabled = name.isNotBlank(),
                    onClick = { vm.addSpeaker(name.trim()); name = "" },
                    colors = ButtonDefaults.buttonColors(containerColor = Accent)
                ) {
                    Text("添加", color = Background, fontWeight = FontWeight.Bold)
                }
            }
        }
        if (people.isEmpty()) item { EmptyState("人物库为空", "请在上方添加人物并在事件详情中关联发言。") }
        items(people, key = { it.getString("id") }) { person ->
            Card(
                colors = CardDefaults.cardColors(containerColor = Panel),
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, PanelBorder),
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Surface(shape = RoundedCornerShape(12.dp), color = Accent.copy(alpha = .14f)) {
                        Text(
                            person.getString("name").take(1),
                            modifier = Modifier.padding(12.dp),
                            color = Accent,
                            fontSize = 18.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }
                    Column(Modifier.padding(start = 14.dp)) {
                        Text(person.getString("name"), fontSize = 16.sp, fontWeight = FontWeight.Medium, color = TextWhite)
                        Text("声纹样本 ${person.optInt("voiceprint_count")}", color = Muted, fontSize = 12.sp)
                    }
                }
            }
        }
    }
}

@Composable
private fun Memories(memories: List<JSONObject>, vm: AppViewModel) {
    var query by rememberSaveable { mutableStateOf("") }
    val visible = memories.filter { it.getString("text").contains(query, ignoreCase = true) }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        item {
            Text("记忆索引", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)
        }
        item {
            OutlinedTextField(
                query,
                { query = it },
                label = { Text("搜索缓存记忆") },
                leadingIcon = { Icon(Icons.Outlined.Search, null) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
        }
        if (visible.isEmpty()) item { EmptyState("暂无匹配记忆", "待确认或已确认的记忆会在此展示。") }
        items(visible, key = { it.getString("id") }) { memory ->
            Card(
                colors = CardDefaults.cardColors(containerColor = Panel),
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, PanelBorder)
            ) {
                Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        if (memory.optInt("confirmed") == 1) "已确认 · ${clock(memory.getLong("start_ms"))}" else "待确认原文",
                        color = if (memory.optInt("confirmed") == 1) Accent else Muted,
                        fontSize = 12.sp
                    )
                    Text(memory.getString("text"), maxLines = 6, overflow = TextOverflow.Ellipsis, lineHeight = 22.sp, color = TextWhite)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        TextButton(onClick = { vm.openEvent(memory.getString("event_id")) }) { Text("查看出处", fontSize = 12.sp) }
                        TextButton(onClick = { vm.confirm(memory.getString("id"), memory.optInt("confirmed") != 1) }) {
                            Text(if (memory.optInt("confirmed") == 1) "撤销确认" else "确认", fontSize = 12.sp)
                        }
                        TextButton(onClick = { vm.deleteMemory(memory.getString("id")) }) { Text("移除", color = Muted, fontSize = 12.sp) }
                    }
                }
            }
        }
    }
}

@Composable
private fun Settings(
    vm: AppViewModel,
    recording: CaptureState,
    status: JSONObject?,
    types: List<JSONObject>,
    onRecord: (Boolean) -> Unit,
    sessions: List<LocalSession>,
    sessionStats: Map<String, SessionAudioInfo>,
    playingSessionId: String?
) {
    val context = LocalContext.current
    var url by rememberSaveable { mutableStateOf(vm.repo.prefs.baseUrl) }
    var token by rememberSaveable { mutableStateOf("") }
    var threshold by rememberSaveable { mutableFloatStateOf(.35f) }
    var pad by rememberSaveable { mutableFloatStateOf(300f) }
    var silence by rememberSaveable { mutableFloatStateOf(800f) }
    var scene by rememberSaveable { mutableStateOf("quiet") }
    var labels by rememberSaveable { mutableStateOf("5-15") }
    var verified by rememberSaveable { mutableStateOf(false) }
    var typeId by rememberSaveable { mutableStateOf("") }
    var typeName by rememberSaveable { mutableStateOf("") }
    var keywords by rememberSaveable { mutableStateOf("") }
    var deletingId by remember { mutableStateOf<String?>(null) }
    val evaluation by vm.evaluation.collectAsStateWithLifecycle()

    val currentMode by vm.operationMode.collectAsStateWithLifecycle()
    val currentProvider by vm.aiProvider.collectAsStateWithLifecycle()
    val currentApiKey by vm.aiApiKey.collectAsStateWithLifecycle()
    val currentModel by vm.aiModelName.collectAsStateWithLifecycle()
    val currentProjectId by vm.vertexProjectId.collectAsStateWithLifecycle()
    val currentLocation by vm.vertexLocation.collectAsStateWithLifecycle()
    val currentEndpoint by vm.vertexEndpoint.collectAsStateWithLifecycle()
    val aiTesting by vm.aiTesting.collectAsStateWithLifecycle()
    val aiTestResult by vm.aiTestResult.collectAsStateWithLifecycle()
    val aiTestSuccess by vm.aiTestSuccess.collectAsStateWithLifecycle()

    var mode by rememberSaveable { mutableStateOf(currentMode) }
    var provider by rememberSaveable { mutableStateOf(currentProvider) }
    var apiKey by rememberSaveable { mutableStateOf(currentApiKey) }
    var showApiKey by rememberSaveable { mutableStateOf(false) }
    var modelName by rememberSaveable { mutableStateOf(currentModel) }
    var projectId by rememberSaveable { mutableStateOf(currentProjectId) }
    var location by rememberSaveable { mutableStateOf(currentLocation) }
    var endpoint by rememberSaveable { mutableStateOf(currentEndpoint) }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text("独立运行与 AI 配置", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)

        Card(
            colors = CardDefaults.cardColors(containerColor = Panel),
            shape = RoundedCornerShape(16.dp),
            border = BorderStroke(1.dp, PanelBorder),
            modifier = Modifier.fillMaxWidth()
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Text("运行模式", fontSize = 14.sp, fontWeight = FontWeight.Medium, color = TextWhite)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(
                        "standalone" to "端侧独立 (零依赖)",
                        "server" to "专用服务端",
                        "hybrid" to "混合模式 (端侧+远端)"
                    ).forEach { (m, label) ->
                        FilterChip(
                            selected = mode == m,
                            onClick = { mode = m },
                            label = { Text(label, fontSize = 12.sp) }
                        )
                    }
                }

                if (mode == "standalone" || mode == "hybrid") {
                    Text("AI 服务提供方", fontSize = 14.sp, fontWeight = FontWeight.Medium, color = TextWhite)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(
                            selected = provider == "gemini_studio",
                            onClick = { provider = "gemini_studio" },
                            label = { Text("Google AI Studio", fontSize = 12.sp) }
                        )
                        FilterChip(
                            selected = provider == "vertex_ai",
                            onClick = { provider = "vertex_ai" },
                            label = { Text("GCP Vertex AI", fontSize = 12.sp) }
                        )
                    }

                    Text("转写模型", fontSize = 14.sp, fontWeight = FontWeight.Medium, color = TextWhite)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro").forEach { m ->
                            FilterChip(
                                selected = modelName == m,
                                onClick = { modelName = m },
                                label = { Text(m, fontSize = 12.sp) }
                            )
                        }
                    }
                    OutlinedTextField(
                        value = modelName,
                        onValueChange = { modelName = it },
                        label = { Text("模型名称") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = apiKey,
                        onValueChange = { apiKey = it },
                        label = { Text(if (provider == "vertex_ai") "API Key (或留空使用凭据)" else "Gemini API Key") },
                        placeholder = { Text("AIzaSy...") },
                        visualTransformation = if (showApiKey) VisualTransformation.None else PasswordVisualTransformation(),
                        trailingIcon = {
                            IconButton(onClick = { showApiKey = !showApiKey }) {
                                Icon(
                                    if (showApiKey) Icons.Outlined.VisibilityOff else Icons.Outlined.Visibility,
                                    contentDescription = if (showApiKey) "隐藏" else "显示",
                                    tint = Muted
                                )
                            }
                        },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    if (provider == "vertex_ai") {
                        OutlinedTextField(
                            value = projectId,
                            onValueChange = { projectId = it },
                            label = { Text("GCP Project ID (可选)") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        OutlinedTextField(
                            value = location,
                            onValueChange = { location = it },
                            label = { Text("GCP Location (默认: global)") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        OutlinedTextField(
                            value = endpoint,
                            onValueChange = { endpoint = it },
                            label = { Text("自定义 Endpoint (留空默认)") },
                            placeholder = { Text("https://...") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }

                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                        OutlinedButton(
                            enabled = !aiTesting,
                            onClick = { vm.testAiConnection(provider, apiKey, modelName, projectId, location, endpoint) },
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier.weight(1f)
                        ) {
                            if (aiTesting) {
                                CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp, color = Accent)
                                Spacer(Modifier.width(8.dp))
                            }
                            Text(if (aiTesting) "测试中..." else "测试连接")
                        }
                        Button(
                            onClick = {
                                vm.saveAiConfig(mode, provider, apiKey, modelName, projectId, location, endpoint)
                            },
                            colors = ButtonDefaults.buttonColors(containerColor = Accent),
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier.weight(1f)
                        ) {
                            Text("保存配置", color = Background, fontWeight = FontWeight.Bold)
                        }
                    }

                    aiTestResult?.let { result ->
                        val isSuccess = aiTestSuccess == true
                        Surface(
                            shape = RoundedCornerShape(10.dp),
                            color = if (isSuccess) Accent.copy(alpha = 0.12f) else Danger.copy(alpha = 0.12f),
                            border = BorderStroke(1.dp, if (isSuccess) Accent.copy(alpha = 0.4f) else Danger.copy(alpha = 0.4f)),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Row(
                                modifier = Modifier.padding(12.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                Icon(
                                    if (isSuccess) Icons.Outlined.CheckCircle else Icons.Outlined.ErrorOutline,
                                    contentDescription = null,
                                    tint = if (isSuccess) Accent else Danger,
                                    modifier = Modifier.size(18.dp)
                                )
                                Text(
                                    result,
                                    fontSize = 12.sp,
                                    color = if (isSuccess) Accent else Danger
                                )
                            }
                        }
                    }
                } else {
                    Button(
                        onClick = {
                            vm.saveAiConfig(mode, provider, apiKey, modelName, projectId, location, endpoint)
                        },
                        colors = ButtonDefaults.buttonColors(containerColor = Accent),
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text("保存模式", color = Background, fontWeight = FontWeight.Bold)
                    }
                }
            }
        }

        HorizontalDivider(color = PanelBorder)

        Text("连接与服务", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)
        OutlinedTextField(
            url,
            { url = it },
            label = { Text("服务端地址") },
            placeholder = { Text("https://journal.example.com") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth()
        )
        OutlinedTextField(
            token,
            { token = it },
            label = { Text("访问令牌（留空保持现有）") },
            visualTransformation = PasswordVisualTransformation(),
            singleLine = true,
            modifier = Modifier.fillMaxWidth()
        )
        Button(
            onClick = { vm.configure(url, token); token = "" },
            colors = ButtonDefaults.buttonColors(containerColor = Accent),
            shape = RoundedCornerShape(10.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("保存并连接", color = Background, fontWeight = FontWeight.Bold)
        }
        Text(
            "ASR: ${status?.optString("asr_provider") ?: "未连接"}   VAD: ${status?.optString("vad_provider") ?: "未连接"}",
            color = Muted,
            fontSize = 12.sp
        )

        HorizontalDivider(color = PanelBorder)

        Text("VAD 离线检测参数", fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)
        Text("语音阈值  %.2f".format(threshold), fontSize = 13.sp, color = TextWhite)
        Slider(threshold, { threshold = it }, valueRange = .15f.. .75f)
        Text("前后缓冲  ${pad.toInt()} ms", fontSize = 13.sp, color = TextWhite)
        Slider(pad, { pad = it }, valueRange = 100f..600f, steps = 9)
        Text("结束等待  ${silence.toInt()} ms", fontSize = 13.sp, color = TextWhite)
        Slider(silence, { silence = it }, valueRange = 400f..1600f, steps = 11)
        OutlinedButton(
            onClick = {
                vm.saveProfile(
                    "手动保守配置",
                    JSONObject().put("threshold", threshold.toDouble())
                        .put("negative_threshold", minOf(.20, threshold.toDouble() - .05))
                        .put("pad_ms", pad.toInt())
                        .put("min_silence_ms", silence.toInt())
                        .put("retain_uncertain", true)
                        .put("uncertainty_threshold", .12)
                )
            },
            shape = RoundedCornerShape(10.dp)
        ) {
            Text("保存为新批次配置")
        }

        HorizontalDivider(color = PanelBorder)

        Text("场景校准 (20s)", fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("quiet" to "安静房间", "noise" to "背景噪声", "outdoor" to "室外", "distance" to "远处说话").forEach { (key, name) ->
                FilterChip(
                    selected = scene == key,
                    onClick = { scene = key; verified = false },
                    label = { Text(name) }
                )
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(
                enabled = !recording.recording,
                onClick = { verified = false; onRecord(true) },
                colors = ButtonDefaults.buttonColors(containerColor = Accent)
            ) {
                Text("录制 20 秒", color = Background, fontWeight = FontWeight.Bold)
            }
            OutlinedButton(enabled = !recording.recording, onClick = { vm.playCalibration() }) { Text("回听") }
        }
        OutlinedTextField(
            labels,
            { labels = it; verified = false },
            label = { Text("人声区间（秒，例如 5.2-9.8）") },
            modifier = Modifier.fillMaxWidth()
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(verified, { verified = it })
            Text("已标注完毕实际人声区间", fontSize = 12.sp, color = TextWhite, modifier = Modifier.weight(1f))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(enabled = verified && !recording.recording, onClick = { vm.evaluate(scene, labels, false) }) { Text("测量保留率") }
            OutlinedButton(enabled = verified && !recording.recording, onClick = { vm.evaluate(scene, labels, true) }) { Text("试算参数") }
        }
        evaluation?.let { result ->
            val recommendation = result.optJSONObject("recommended")
            val metrics = result.optJSONObject("metrics") ?: recommendation?.optJSONObject("metrics")
            metrics?.let {
                val recall = if (it.isNull("speech_recall")) "样本无人声" else "%.1f%%".format(it.optDouble("speech_recall") * 100)
                Text(
                    "保留率: $recall | 裁剪: %.1f%% | 漏掉: %.0f ms".format(it.optDouble("removed_fraction") * 100, it.optDouble("missed_speech_ms")),
                    color = Accent,
                    fontSize = 13.sp
                )
            }
            recommendation?.optJSONObject("config")?.let { cfg ->
                OutlinedButton(onClick = { vm.saveProfile("场景校准试用参数", cfg) }) { Text("保存试用参数") }
            }
        }

        HorizontalDivider(color = PanelBorder)

        Text("事件类型库", fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
            types.forEach { type -> SuggestionChip(onClick = {}, label = { Text(type.getString("name")) }) }
        }
        OutlinedTextField(typeId, { typeId = it }, label = { Text("类型 ID (例 research)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(typeName, { typeName = it }, label = { Text("显示名称") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(keywords, { keywords = it }, label = { Text("分类关键词 (逗号分隔)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedButton(
            enabled = typeId.isNotBlank() && typeName.isNotBlank(),
            onClick = {
                vm.saveType(typeId, typeName, keywords.split(',', '，').map { it.trim() }.filter { it.isNotBlank() })
            }
        ) {
            Text("保存类型")
        }

        HorizontalDivider(color = PanelBorder)

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("设备上的录音与 Chunks", fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = TextWhite)
            if (sessions.isNotEmpty()) {
                FilledTonalButton(
                    onClick = { vm.saveAllToDownloads() },
                    colors = ButtonDefaults.filledTonalButtonColors(containerColor = Accent.copy(alpha = 0.16f), contentColor = Accent),
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Icon(Icons.Outlined.FolderZip, null, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("导出全部", fontSize = 11.sp)
                }
            }
        }

        sessions.forEach { s ->
            val stats = sessionStats[s.id]
            val isPlaying = playingSessionId == s.id
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        "${formatTimestamp(s.startedAtMs)} · ${formatDuration(stats?.totalSamples ?: 0L)} (${stats?.chunkCount ?: 0}块)",
                        fontSize = 13.sp,
                        color = TextWhite
                    )
                    Text(s.state, color = Muted, fontSize = 11.sp)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = { vm.playSession(s.id) }, modifier = Modifier.size(32.dp)) {
                        Icon(if (isPlaying) Icons.Outlined.Stop else Icons.Outlined.PlayArrow, null, tint = Accent, modifier = Modifier.size(18.dp))
                    }
                    IconButton(onClick = { vm.saveToDownloads(s.id, asZip = true) }, modifier = Modifier.size(32.dp)) {
                        Icon(Icons.Outlined.FolderZip, "ZIP", tint = Accent, modifier = Modifier.size(16.dp))
                    }
                    IconButton(onClick = { vm.shareSession(context, s.id, asZip = true) }, modifier = Modifier.size(32.dp)) {
                        Icon(Icons.Outlined.Share, "分享", tint = TextWhite, modifier = Modifier.size(16.dp))
                    }
                    IconButton(
                        enabled = !(recording.recording && recording.sessionId == s.id),
                        onClick = { deletingId = s.id },
                        modifier = Modifier.size(32.dp)
                    ) {
                        Icon(Icons.Outlined.Delete, "删除", tint = Muted, modifier = Modifier.size(16.dp))
                    }
                }
            }
        }
        Spacer(Modifier.height(20.dp))
    }

    deletingId?.let { id ->
        AlertDialog(
            onDismissRequest = { deletingId = null },
            title = { Text("删除该录音？") },
            text = { Text("将删除服务器与本机的录音数据。") },
            confirmButton = {
                TextButton(onClick = { vm.deleteSession(id); deletingId = null }) { Text("删除", color = Danger) }
            },
            dismissButton = {
                TextButton(onClick = { deletingId = null }) { Text("取消") }
            }
        )
    }
}

private data class Turn(val id: String, val label: String, val name: String, var text: String, val start: Long, var end: Long, val chunkId: String)

private fun turns(event: JSONObject): List<Turn> {
    val segments = event.optJSONArray("segments") ?: return emptyList()
    val result = mutableListOf<Turn>()
    for (i in 0 until segments.length()) {
        val s = segments.getJSONObject(i)
        val label = s.getString("local_speaker")
        val name = if (s.isNull("speaker_name")) "未确认 · ${label.substringAfterLast(':')}" else s.getString("speaker_name")
        val text = s.getString("text")
        val previous = result.lastOrNull()
        if (previous != null && previous.label == label && previous.name == name && s.getLong("start_ms") - previous.end < 1800) {
            val space = if (previous.text.lastOrNull()?.let { it.code < 128 && it.isLetterOrDigit() } == true &&
                text.firstOrNull()?.let { it.code < 128 && it.isLetterOrDigit() } == true) " " else ""
            previous.text += space + text
            previous.end = s.getLong("end_ms")
        } else {
            result.add(
                Turn(
                    id = s.getString("id"),
                    label = label,
                    name = name,
                    text = text,
                    start = s.getLong("start_ms"),
                    end = s.getLong("end_ms"),
                    chunkId = s.optJSONArray("source_spans")?.optJSONObject(0)?.optString("chunk_id").orEmpty()
                )
            )
        }
    }
    return result
}

@Composable
private fun EventDialog(event: JSONObject, people: List<JSONObject>, vm: AppViewModel) {
    var rename by remember(event.getString("id")) { mutableStateOf(event.getString("title")) }
    AlertDialog(
        onDismissRequest = { vm.detail.value = null },
        title = { Text("${clock(event.getLong("start_ms"))} – ${clock(event.getLong("end_ms"))}") },
        text = {
            Column(
                Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(14.dp)
            ) {
                OutlinedTextField(
                    rename,
                    { rename = it },
                    label = { Text("事件标题") },
                    modifier = Modifier.fillMaxWidth()
                )
                TextButton(
                    onClick = {
                        vm.act {
                            vm.repo.api.call("/v1/events/${event.getString("id")}", "PATCH", JSONObject().put("title", rename))
                            vm.repo.refresh(vm.day.value)
                        }
                    }
                ) {
                    Text("保存标题")
                }
                turns(event).forEach { turn ->
                    var menu by remember(turn.id) { mutableStateOf(false) }
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Box {
                            TextButton(onClick = { menu = true }, contentPadding = PaddingValues(0.dp)) {
                                Text(turn.name, color = Teal, fontSize = 13.sp)
                            }
                            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                                people.forEach { person ->
                                    DropdownMenuItem(
                                        text = { Text(person.getString("name")) },
                                        onClick = { vm.assign(turn.id, person.getString("id")); menu = false }
                                    )
                                }
                                DropdownMenuItem(
                                    text = { Text("保持未知") },
                                    onClick = { vm.assign(turn.id, null); menu = false }
                                )
                            }
                        }
                        Text(turn.text, lineHeight = 22.sp, color = TextWhite)
                        if (turn.chunkId.isNotBlank()) {
                            TextButton(onClick = { vm.playChunk(turn.chunkId) }, contentPadding = PaddingValues(0.dp)) {
                                Text("播放原始录音块", fontSize = 11.sp, color = Muted)
                            }
                        }
                    }
                    HorizontalDivider(color = PanelBorder.copy(alpha = 0.5f))
                }
            }
        },
        confirmButton = { TextButton(onClick = { vm.detail.value = null }) { Text("关闭") } }
    )
}
