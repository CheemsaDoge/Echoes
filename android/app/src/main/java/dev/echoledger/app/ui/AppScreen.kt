@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.echoledger.app.data.LocalSession
import java.time.Instant
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

private fun formatTimestamp(ms: Long): String {
    return Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern("MM-dd HH:mm:ss"))
}

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

@Composable
fun AppScreen(vm: AppViewModel, onRecord: (Boolean) -> Unit, onStop: () -> Unit) {
    val context = LocalContext.current
    val recording by vm.recording.collectAsStateWithLifecycle()
    val sessions by vm.localSessions.collectAsStateWithLifecycle()
    val sessionStats by vm.sessionStats.collectAsStateWithLifecycle()
    val playingSessionId by vm.playingSessionId.collectAsStateWithLifecycle()
    val selectedSceneTag by vm.selectedSceneTag.collectAsStateWithLifecycle()
    val message by vm.repo.message.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    var deletingSessionId by remember { mutableStateOf<String?>(null) }

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
            topBar = {
                Column(Modifier.fillMaxWidth().statusBarsPadding()) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 20.dp, vertical = 14.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            "听记 · 录音采样",
                            fontSize = 21.sp,
                            fontWeight = FontWeight.Bold,
                            color = TextWhite
                        )
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
                                        .background(
                                            if (recording.recording) Color(0xFFFF5252) else Accent,
                                            CircleShape
                                        )
                                )
                                Spacer(Modifier.width(6.dp))
                                Text(
                                    "16kHz PCM",
                                    color = Accent,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Medium
                                )
                            }
                        }
                    }
                    if (busy) {
                        LinearProgressIndicator(
                            Modifier.fillMaxWidth().height(2.dp),
                            color = Accent
                        )
                    } else {
                        Spacer(Modifier.height(2.dp))
                    }
                }
            }
        ) { padding ->
            Column(
                Modifier
                    .fillMaxSize()
                    .padding(padding)
            ) {
                if (message.isNotBlank()) {
                    Text(
                        message,
                        color = Color(0xFFE5B58C),
                        fontSize = 12.sp,
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(Color(0xFF2C241E))
                            .padding(horizontal = 20.dp, vertical = 8.dp)
                    )
                }
                if (recording.error.isNotBlank()) {
                    Text(
                        recording.error,
                        color = Danger,
                        fontSize = 12.sp,
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(Danger.copy(alpha = 0.15f))
                            .padding(horizontal = 20.dp, vertical = 8.dp)
                    )
                }

                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    item {
                        RecordingControlCard(
                            isRecording = recording.recording,
                            purpose = recording.purpose,
                            samples = recording.samples,
                            rms = recording.rms,
                            silenced = recording.silenced,
                            selectedSceneTag = selectedSceneTag,
                            onTagSelected = { tag ->
                                vm.selectedSceneTag.value = tag
                                if (recording.recording && recording.sessionId.isNotBlank()) {
                                    vm.setSessionSceneTag(recording.sessionId, tag)
                                }
                            },
                            onStart = { onRecord(false) },
                            onCalibrate = { onRecord(true) },
                            onStop = onStop
                        )
                    }

                    item {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 2.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                "录音样本",
                                fontSize = 15.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = TextWhite
                            )
                            Text(
                                "${sessions.size} 条",
                                fontSize = 12.sp,
                                color = Muted
                            )
                        }
                    }

                    if (sessions.isEmpty()) {
                        item {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 40.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Text("暂无录音样本，请在上方开始采集", color = Muted, fontSize = 13.sp)
                            }
                        }
                    }

                    items(sessions, key = { it.id }) { session ->
                        val stats = sessionStats[session.id]
                        val isPlaying = playingSessionId == session.id
                        SessionCard(
                            session = session,
                            stats = stats,
                            isPlaying = isPlaying,
                            onPlayToggle = { vm.playSession(session.id) },
                            onSceneTagChange = { tag -> vm.setSessionSceneTag(session.id, tag) },
                            onSaveWav = { vm.saveToDownloads(session.id, asZip = false) },
                            onSaveZip = { vm.saveToDownloads(session.id, asZip = true) },
                            onShareWav = { vm.shareSession(context, session.id, asZip = false) },
                            onShareZip = { vm.shareSession(context, session.id, asZip = true) },
                            onDelete = { deletingSessionId = session.id }
                        )
                    }
                }
            }
        }

        deletingSessionId?.let { id ->
            AlertDialog(
                onDismissRequest = { deletingSessionId = null },
                title = { Text("删除该录音样本？") },
                text = { Text("将永久移除该次录音的全部原始分块与数据文件。") },
                confirmButton = {
                    TextButton(onClick = {
                        vm.deleteSession(id)
                        deletingSessionId = null
                    }) { Text("删除", color = Danger) }
                },
                dismissButton = {
                    TextButton(onClick = { deletingSessionId = null }) { Text("取消") }
                }
            )
        }
    }
}

@Composable
private fun RecordingControlCard(
    isRecording: Boolean,
    purpose: String,
    samples: Long,
    rms: Float,
    silenced: Boolean,
    selectedSceneTag: String,
    onTagSelected: (String) -> Unit,
    onStart: () -> Unit,
    onCalibrate: () -> Unit,
    onStop: () -> Unit
) {
    val scenes = listOf("谈话", "喧闹", "安静", "声音小")
    Card(
        colors = CardDefaults.cardColors(containerColor = Panel),
        shape = RoundedCornerShape(20.dp),
        border = BorderStroke(1.dp, if (isRecording) Teal.copy(alpha = 0.6f) else PanelBorder)
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        if (isRecording) Icons.Outlined.GraphicEq else Icons.Outlined.Mic,
                        contentDescription = null,
                        tint = if (isRecording) Teal else Accent,
                        modifier = Modifier.size(20.dp)
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        if (isRecording) {
                            if (purpose == "calibration") "20s 定长采样中" else "正在录音"
                        } else "就绪",
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = if (isRecording) Teal else TextWhite
                    )
                }
            }

            Row(
                verticalAlignment = Alignment.Bottom,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    formatClock(samples),
                    fontSize = 36.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = if (isRecording) Teal else TextWhite
                )

                Row(
                    horizontalArrangement = Arrangement.spacedBy(3.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.height(34.dp)
                ) {
                    repeat(18) { i ->
                        val factor = 1f + kotlin.math.sin(i * 0.45).toFloat() * 0.5f
                        val barHeight = if (isRecording) {
                            (4f + (rms * 110f * factor).coerceIn(0f, 30f)).coerceAtMost(34f)
                        } else 4f
                        Box(
                            Modifier
                                .width(3.dp)
                                .height(barHeight.dp)
                                .background(
                                    if (isRecording) Teal else PanelBorder,
                                    RoundedCornerShape(2.dp)
                                )
                        )
                    }
                }
            }

            if (silenced) {
                Text(
                    "系统暂时静音了麦克风，请检查其他应用。",
                    color = Color(0xFFE5B58C),
                    fontSize = 12.sp
                )
            }

            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                scenes.forEach { tag ->
                    val selected = selectedSceneTag == tag
                    FilterChip(
                        selected = selected,
                        onClick = { onTagSelected(tag) },
                        label = { Text(tag, fontSize = 12.sp) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = Accent.copy(alpha = 0.2f),
                            selectedLabelColor = Accent,
                            containerColor = Color.Transparent,
                            labelColor = Muted
                        ),
                        border = FilterChipDefaults.filterChipBorder(
                            enabled = true,
                            selected = selected,
                            borderColor = PanelBorder,
                            selectedBorderColor = Accent,
                            borderWidth = 1.dp
                        )
                    )
                }
            }

            if (isRecording) {
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
private fun SessionCard(
    session: LocalSession,
    stats: SessionAudioInfo?,
    isPlaying: Boolean,
    onPlayToggle: () -> Unit,
    onSceneTagChange: (String) -> Unit,
    onSaveWav: () -> Unit,
    onSaveZip: () -> Unit,
    onShareWav: () -> Unit,
    onShareZip: () -> Unit,
    onDelete: () -> Unit
) {
    val sceneTags = listOf("谈话", "喧闹", "安静", "声音小")
    val currentTag = stats?.sceneTag ?: "未分类"
    val chunkCount = stats?.chunkCount ?: 0
    val totalSamples = stats?.totalSamples ?: 0L

    Card(
        colors = CardDefaults.cardColors(containerColor = Panel),
        shape = RoundedCornerShape(16.dp),
        border = BorderStroke(1.dp, if (isPlaying) Teal.copy(alpha = 0.5f) else PanelBorder)
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    formatTimestamp(session.startedAtMs),
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    color = TextWhite
                )
                Text(
                    "${formatDuration(totalSamples)} · $chunkCount 块",
                    fontSize = 13.sp,
                    color = Teal,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.SemiBold
                )
            }

            Row(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth()
            ) {
                sceneTags.forEach { tag ->
                    val selected = currentTag == tag
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .background(if (selected) Accent.copy(alpha = 0.18f) else Color.Transparent)
                            .border(1.dp, if (selected) Accent else PanelBorder, RoundedCornerShape(8.dp))
                            .clickable { onSceneTagChange(tag) }
                            .padding(horizontal = 9.dp, vertical = 4.dp)
                    ) {
                        Text(
                            tag,
                            color = if (selected) Accent else Muted,
                            fontSize = 11.sp,
                            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal
                        )
                    }
                }
            }

            HorizontalDivider(color = PanelBorder.copy(alpha = 0.6f))

            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                FilledTonalButton(
                    onClick = onPlayToggle,
                    colors = ButtonDefaults.filledTonalButtonColors(
                        containerColor = if (isPlaying) Teal else Accent.copy(alpha = 0.15f),
                        contentColor = if (isPlaying) Background else Accent
                    ),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Icon(
                        if (isPlaying) Icons.Outlined.Stop else Icons.Outlined.PlayArrow,
                        null,
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(Modifier.width(4.dp))
                    Text(if (isPlaying) "停止" else "回听", fontSize = 12.sp)
                }

                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(
                        onClick = onSaveWav,
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)
                    ) {
                        Icon(Icons.Outlined.Download, null, modifier = Modifier.size(15.dp), tint = TextWhite)
                        Spacer(Modifier.width(3.dp))
                        Text("存WAV", fontSize = 12.sp, color = TextWhite)
                    }
                    TextButton(
                        onClick = onShareWav,
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)
                    ) {
                        Icon(Icons.Outlined.Share, null, modifier = Modifier.size(15.dp), tint = TextWhite)
                        Spacer(Modifier.width(3.dp))
                        Text("分享", fontSize = 12.sp, color = TextWhite)
                    }
                    TextButton(
                        onClick = onSaveZip,
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)
                    ) {
                        Icon(Icons.Outlined.FolderZip, null, modifier = Modifier.size(15.dp), tint = Muted)
                        Spacer(Modifier.width(3.dp))
                        Text("ZIP", fontSize = 12.sp, color = Muted)
                    }
                    IconButton(
                        onClick = onDelete,
                        modifier = Modifier.size(32.dp)
                    ) {
                        Icon(
                            Icons.Outlined.Delete,
                            null,
                            tint = Muted.copy(alpha = 0.7f),
                            modifier = Modifier.size(16.dp)
                        )
                    }
                }
            }
        }
    }
}