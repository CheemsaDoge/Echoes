@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
package dev.echoledger.app.ui

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.echoledger.app.capture.CaptureState
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val Background = Color(0xFF141718)
private val Panel = Color(0xFF202529)
private val Muted = Color(0xFF98A39E)
private val Accent = Color(0xFFA8CA68)
private val Teal = Color(0xFF68BDA9)
private val Line = Color(0xFF363E3C)
private fun clock(ms: Long): String = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("HH:mm"))
private fun duration(samples: Long): String {
    val s = samples / 16000
    return "%02d:%02d:%02d".format(s / 3600, (s / 60) % 60, s % 60)
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
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val zone = ZoneId.systemDefault()
    val from = day.atStartOfDay(zone).toInstant().toEpochMilli()
    val to = day.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
    val events = allEvents.filter { it.optLong("end_ms") >= from && it.optLong("start_ms") < to }
    MaterialTheme(colorScheme = darkColorScheme(primary = Accent, secondary = Teal, background = Background,
        surface = Panel, onPrimary = Background, onSurface = Color(0xFFE5E9E5), outline = Line)) {
        Scaffold(containerColor = Background, bottomBar = {
            NavigationBar(containerColor = Panel, tonalElevation = 0.dp) {
                val labels = listOf("时间线", "人物", "记忆", "设置")
                val icons = listOf(Icons.Outlined.History, Icons.Outlined.People, Icons.Outlined.Layers, Icons.Outlined.Tune)
                labels.forEachIndexed { i, label ->
                    NavigationBarItem(selected = tab == i, onClick = { tab = i }, icon = { Icon(icons[i], label) },
                        label = { Text(label) }, colors = NavigationBarItemDefaults.colors(indicatorColor = Accent.copy(alpha = .12f), selectedIconColor = Accent, selectedTextColor = Accent))
                }
            }
        }) { padding ->
            Column(Modifier.fillMaxSize().padding(padding)) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 22.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("听记", fontSize = 27.sp, fontWeight = FontWeight.SemiBold)
                        Text("ECHOLEDGER  /  个人录音日志", color = Muted, fontSize = 10.sp, letterSpacing = 1.3.sp)
                    }
                    IconButton(onClick = { vm.refresh() }) { Icon(Icons.Outlined.Sync, "同步", tint = Accent) }
                }
                if (busy) LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp), color = Accent)
                else Spacer(Modifier.height(2.dp))
                if (message.isNotBlank()) Notice(message, warning = true)
                if (recording.error.isNotBlank()) Notice(recording.error, warning = true)
                if (statuses.firstOrNull()?.optBoolean("is_demo") == true) Notice("演示模式：转写文本是测试数据，没有执行真实语音识别。")
                Box(Modifier.weight(1f)) {
                    when (tab) {
                        0 -> Timeline(recording, pending, day, events, vm::selectDay, { onRecord(false) }, onStop, vm::openEvent)
                        1 -> People(people, vm)
                        2 -> Memories(memories, vm)
                        else -> Settings(vm, recording, statuses.firstOrNull(), types, onRecord)
                    }
                }
            }
        }
        detail?.let { EventDialog(it, people, vm) }
    }
}

@Composable
private fun Notice(text: String, warning: Boolean = false) {
    Text(text, color = if (warning) Color(0xFFE5B58C) else Muted, fontSize = 12.sp,
        modifier = Modifier.fillMaxWidth().background(if (warning) Color(0xFF30261F) else Panel).padding(horizontal = 20.dp, vertical = 9.dp))
}

@Composable
private fun Timeline(state: CaptureState, pending: Int, day: LocalDate, events: List<JSONObject>, selectDay: (LocalDate) -> Unit,
                     start: () -> Unit, stop: () -> Unit, open: (String) -> Unit) {
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        item {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text(day.format(DateTimeFormatter.ofPattern("yyyy / MM")), color = Muted, fontSize = 16.sp)
                TextButton(onClick = { selectDay(LocalDate.now()) }) { Text("回今天") }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                (6 downTo 0).forEach { back ->
                    val date = LocalDate.now().minusDays(back.toLong())
                    val selected = day == date
                    Column(Modifier.width(39.dp).clipShape().clickable { selectDay(date) }.padding(vertical = 8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("一二三四五六日"[date.dayOfWeek.value - 1].toString(), color = if (selected) Accent else Muted, fontSize = 12.sp)
                        Text(date.dayOfMonth.toString(), color = if (selected) Accent else Color(0xFFE5E9E5), fontSize = 24.sp, fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
                        Text(if (selected) "●" else " ", color = Accent, fontSize = 8.sp)
                    }
                }
            }
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(20.dp), border = BorderStroke(1.dp, if (state.recording) Teal.copy(alpha = .55f) else Line)) {
                Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(13.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(if (state.recording) Icons.Outlined.GraphicEq else Icons.Outlined.Mic, "录音状态", tint = Teal)
                        Spacer(Modifier.width(9.dp))
                        Text(if (state.recording) (if (state.purpose == "calibration") "校准录音中" else "正在聆听") else "开始记录这一刻", fontSize = 20.sp, color = Teal)
                    }
                    Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
                        Text(duration(state.samples), fontSize = 34.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 2.sp)
                        Row(horizontalArrangement = Arrangement.spacedBy(3.dp), verticalAlignment = Alignment.CenterVertically, modifier = Modifier.height(34.dp)) {
                            repeat(15) { i ->
                                val h = if (state.recording) (4 + state.rms.coerceAtMost(.25f) * 105 * (1 + (i * 7 % 5) / 5f)).coerceAtMost(34f) else 4f
                                Box(Modifier.width(3.dp).height(h.dp).background(if (state.recording) Teal else Line, RoundedCornerShape(2.dp)))
                            }
                        }
                    }
                    Text(if (state.recording) "每 30 秒保存原始录音；待上传 $pending 块" else "原始录音保存在设备中，联网后上传处理。", color = Muted, fontSize = 12.sp)
                    if (state.silenced) Text("系统暂时静音了麦克风，请检查其他录音或通话应用。", color = Color(0xFFE5B58C))
                    Button(onClick = if (state.recording) stop else start, modifier = Modifier.fillMaxWidth(), colors = ButtonDefaults.buttonColors(containerColor = if (state.recording) Teal else Accent)) {
                        Icon(if (state.recording) Icons.Outlined.Stop else Icons.Outlined.Mic, null, modifier = Modifier.size(19.dp))
                        Spacer(Modifier.width(8.dp)); Text(if (state.recording) "停止并保存" else "开始录音")
                    }
                    if (!state.recording) Text("请先取得参与者同意，避免在不适合录音的场所开启。", color = Muted, fontSize = 11.sp)
                }
            }
        }
        item { Text("当天事件  ·  ${events.size}", color = Muted, fontSize = 13.sp) }
        if (events.isEmpty()) item { EmptyState("还没有事件", "录音上传并完成转写后，会在这里出现。关闭应用后仍可通过通知停止录音。") }
        items(events, key = { it.getString("id") }) { item -> EventCard(item) { open(item.getString("id")) } }
        item { Text("事件按原始录音时间排列。当前版本按处理批次生成事件，支持手动改名与分类。", color = Muted, fontSize = 11.sp) }
    }
}
private fun Modifier.clipShape() = this.then(Modifier.background(Color.Transparent, RoundedCornerShape(10.dp)))

@Composable
private fun EventCard(event: JSONObject, click: () -> Unit) {
    val hue = color(event.optString("color", "#61BBA6"))
    Card(onClick = click, shape = RoundedCornerShape(16.dp), border = BorderStroke(1.dp, Line), colors = CardDefaults.cardColors(containerColor = Panel)) {
        Row(Modifier.fillMaxWidth().heightIn(min = 135.dp)) {
            Box(Modifier.width(4.dp).height(135.dp).background(hue))
            Column(Modifier.weight(1f).padding(17.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("${clock(event.getLong("start_ms"))} – ${clock(event.getLong("end_ms"))}", color = Muted, fontSize = 12.sp)
                    Text(event.optString("type_id", "unknown").uppercase(), color = hue, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                }
                Text(event.getString("title"), fontSize = 18.sp, lineHeight = 27.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    val names = event.optJSONArray("speaker_names")
                    if (names != null) for (i in 0 until names.length()) Text(names.getString(i), color = Accent, fontSize = 12.sp)
                    val unknown = event.optInt("unassigned_labels", 0)
                    if (unknown > 0) Text("$unknown 个未确认的声源标签", color = Muted, fontSize = 12.sp)
                    if (event.optInt("is_demo") == 1) Text("演示", color = Color(0xFFE5B58C), fontSize = 12.sp)
                }
            }
        }
    }
}

@Composable
private fun EmptyState(title: String, text: String) {
    Column(Modifier.fillMaxWidth().padding(vertical = 32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Icon(Icons.Outlined.Forum, null, tint = Muted, modifier = Modifier.size(32.dp))
        Text(title, fontSize = 17.sp); Text(text, color = Muted, fontSize = 12.sp, lineHeight = 20.sp)
    }
}

@Composable
private fun People(people: List<JSONObject>, vm: AppViewModel) {
    var name by rememberSaveable { mutableStateOf("") }
    LazyColumn(contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        item { Text("发言人库", fontSize = 24.sp, fontWeight = FontWeight.SemiBold) }
        item { Text("先创建人物，再在事件详情中关联发言。跨录音自动声纹识别尚未启用；外部声纹接入见接口文档。", color = Muted, fontSize = 13.sp, lineHeight = 21.sp) }
        item {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(name, { name = it }, label = { Text("姓名或称呼") }, singleLine = true, modifier = Modifier.weight(1f))
                Button(enabled = name.isNotBlank(), onClick = { vm.addSpeaker(name.trim()); name = "" }) { Text("添加") }
            }
        }
        if (people.isEmpty()) item { EmptyState("人物库还是空的", "声源标签需要你确认后，才会关联到具体人物。") }
        items(people, key = { it.getString("id") }) { person ->
            Card(colors = CardDefaults.cardColors(containerColor = Panel), modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                    Surface(shape = RoundedCornerShape(16.dp), color = Accent.copy(alpha = .14f)) { Text(person.getString("name").take(1), modifier = Modifier.padding(15.dp), color = Accent, fontSize = 22.sp) }
                    Column(Modifier.padding(start = 15.dp)) { Text(person.getString("name"), fontSize = 19.sp); Text("外部声纹样本 ${person.optInt("voiceprint_count")}", color = Muted, fontSize = 12.sp) }
                }
            }
        }
    }
}

@Composable
private fun Memories(memories: List<JSONObject>, vm: AppViewModel) {
    var query by rememberSaveable { mutableStateOf("") }
    val visible = memories.filter { it.getString("text").contains(query, ignoreCase = true) }
    LazyColumn(contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        item { Text("有出处的记忆", fontSize = 24.sp, fontWeight = FontWeight.SemiBold) }
        item { Text("候选记忆保留原文摘录。确认后，MCP 才会将它们用于记忆检索。", color = Muted, fontSize = 13.sp, lineHeight = 21.sp) }
        item { OutlinedTextField(query, { query = it }, label = { Text("搜索已缓存的记忆") }, leadingIcon = { Icon(Icons.Outlined.Search, null) }, modifier = Modifier.fillMaxWidth()) }
        if (visible.isEmpty()) item { EmptyState("暂无匹配记忆", "这里显示最近 200 条缓存；完整历史可通过记忆检索 API 或 MCP 查询。") }
        items(visible, key = { it.getString("id") }) { memory ->
            Card(colors = CardDefaults.cardColors(containerColor = Panel)) {
                Column(Modifier.fillMaxWidth().padding(17.dp), verticalArrangement = Arrangement.spacedBy(11.dp)) {
                    Text(if (memory.optInt("confirmed") == 1) "已确认 · ${clock(memory.getLong("start_ms"))}" else "待确认的原文摘录", color = if (memory.optInt("confirmed") == 1) Accent else Muted, fontSize = 12.sp)
                    Text(memory.getString("text"), maxLines = 7, overflow = TextOverflow.Ellipsis, lineHeight = 23.sp)
                    Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                        TextButton(onClick = { vm.openEvent(memory.getString("event_id")) }) { Text("查看出处") }
                        TextButton(onClick = { vm.confirm(memory.getString("id"), memory.optInt("confirmed") != 1) }) { Text(if (memory.optInt("confirmed") == 1) "撤销确认" else "确认") }
                        TextButton(onClick = { vm.deleteMemory(memory.getString("id")) }) { Text("移除", color = Muted) }
                    }
                }
            }
        }
    }
}

@Composable
private fun Settings(vm: AppViewModel, recording: CaptureState, status: JSONObject?, types: List<JSONObject>, onRecord: (Boolean) -> Unit) {
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
    var deleting by remember { mutableStateOf<String?>(null) }
    val evaluation by vm.evaluation.collectAsStateWithLifecycle()
    val sessions by vm.localSessions.collectAsStateWithLifecycle()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(15.dp)) {
        Text("连接与处理", fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
        OutlinedTextField(url, { url = it }, label = { Text("服务端地址") }, placeholder = { Text("https://journal.example.com") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(token, { token = it }, label = { Text("访问令牌；留空保留现有令牌") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, modifier = Modifier.fillMaxWidth())
        Button(onClick = { vm.configure(url, token); token = "" }, modifier = Modifier.fillMaxWidth()) { Text("保存并连接") }
        Text("ASR：${status?.optString("asr_provider") ?: "未连接"}    VAD：${status?.optString("vad_provider") ?: "未连接"}", color = Muted, fontSize = 12.sp)
        Text("云端模型密钥只配置在服务器。Release 构建要求 HTTPS。原始录音默认保留，空间不足时停止录音。", color = Muted, fontSize = 12.sp, lineHeight = 20.sp)
        HorizontalDivider(color = Line)
        Text("VAD 参数", fontSize = 20.sp)
        Text("参数仅对新处理批次生效。passthrough 会保留全部录音；energy_demo 仅供测试，不能据此评估人声检测。", color = Muted, fontSize = 12.sp, lineHeight = 20.sp)
        Text("语音阈值  %.2f".format(threshold), fontSize = 13.sp)
        Slider(threshold, { threshold = it }, valueRange = .15f.. .75f)
        Text("前后缓冲  ${pad.toInt()} 毫秒", fontSize = 13.sp)
        Slider(pad, { pad = it }, valueRange = 100f..600f, steps = 9)
        Text("结束等待  ${silence.toInt()} 毫秒", fontSize = 13.sp)
        Slider(silence, { silence = it }, valueRange = 400f..1600f, steps = 11)
        OutlinedButton(onClick = {
            vm.saveProfile("手动保守配置", JSONObject().put("threshold", threshold.toDouble()).put("negative_threshold", minOf(.20, threshold.toDouble() - .05))
                .put("pad_ms", pad.toInt()).put("min_silence_ms", silence.toInt()).put("retain_uncertain", true).put("uncertainty_threshold", .12))
        }) { Text("保存并用于新批次") }
        HorizontalDivider(color = Line)
        Text("场景校准", fontSize = 20.sp)
        Text("选择场景，录制 20 秒。建议先安静 5 秒，再正常说话、轻声说话和短回应，最后留一段安静。请回听后标注实际的人声区间。", color = Muted, fontSize = 13.sp, lineHeight = 21.sp)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
            listOf("quiet" to "安静房间", "noise" to "背景噪声", "outdoor" to "室外", "distance" to "远处说话").forEach { (key, name) ->
                FilterChip(selected = scene == key, onClick = { scene = key; verified = false }, label = { Text(name) })
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(enabled = !recording.recording, onClick = { verified = false; onRecord(true) }) { Text("录制 20 秒") }
            OutlinedButton(enabled = !recording.recording, onClick = { vm.playCalibration() }) { Text("回听") }
        }
        Text("校准音频上传后只运行检测，不进入转写和记忆流程。", color = Muted, fontSize = 12.sp)
        OutlinedTextField(labels, { labels = it; verified = false }, label = { Text("人声区间（秒）") }, supportingText = { Text("例如 5.2-9.8; 11-14.6。纯静音样本留空。") }, modifier = Modifier.fillMaxWidth())
        Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(verified, { verified = it }); Text("我已回听并标出全部人声，其他时段均无语音。", fontSize = 12.sp, modifier = Modifier.weight(1f)) }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(enabled = verified && !recording.recording, onClick = { vm.evaluate(scene, labels, false) }) { Text("测量保留率") }
            OutlinedButton(enabled = verified && !recording.recording, onClick = { vm.evaluate(scene, labels, true) }) { Text("试算参数") }
        }
        evaluation?.let { result ->
            val recommendation = result.optJSONObject("recommended")
            val metrics = result.optJSONObject("metrics") ?: recommendation?.optJSONObject("metrics")
            metrics?.let {
                val recall = if (it.isNull("speech_recall")) "样本无人声" else "%.1f%%".format(it.optDouble("speech_recall") * 100)
                Text("人声时长保留率：$recall\n裁剪比例：%.1f%%\n漏掉人声：%.0f 毫秒".format(it.optDouble("removed_fraction") * 100, it.optDouble("missed_speech_ms")), color = Accent, fontSize = 13.sp)
            }
            Text("这些结果只反映当前样本。换时间和场景复测，不能把同一段音频的调参结果当作验证结果。", color = Muted, fontSize = 12.sp, lineHeight = 20.sp)
            recommendation?.optJSONObject("config")?.let { cfg -> OutlinedButton(onClick = { vm.saveProfile("场景校准试用参数", cfg) }) { Text("保存为试用参数") } }
        }
        HorizontalDivider(color = Line)
        Text("事件类型库", fontSize = 20.sp)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp)) { types.forEach { type -> SuggestionChip(onClick = {}, label = { Text(type.getString("name")) }) } }
        OutlinedTextField(typeId, { typeId = it }, label = { Text("类型 ID，例如 research") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(typeName, { typeName = it }, label = { Text("显示名称") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(keywords, { keywords = it }, label = { Text("分类关键词，用逗号分隔") }, modifier = Modifier.fillMaxWidth())
        OutlinedButton(enabled = typeId.isNotBlank() && typeName.isNotBlank(), onClick = { vm.saveType(typeId, typeName, keywords.split(',', '，').map { it.trim() }.filter { it.isNotBlank() }) }) { Text("保存类型") }
        HorizontalDivider(color = Line)
        Text("MCP", fontSize = 20.sp)
        Text("${vm.repo.prefs.baseUrl.trimEnd('/')}/mcp", color = Accent, fontSize = 13.sp)
        Text("仅开放记忆检索、事件读取和人物列表。MCP 使用服务器单独配置的只读令牌；公网 OAuth 尚未实现。", color = Muted, fontSize = 12.sp, lineHeight = 20.sp)
        HorizontalDivider(color = Line)
        Text("设备上的录音", fontSize = 20.sp)
        sessions.take(12).forEach { session ->
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) { Text("${clock(session.startedAtMs)} · ${session.purpose}", fontSize = 13.sp); Text(session.state, color = Muted, fontSize = 11.sp) }
                TextButton(enabled = !(recording.recording && recording.sessionId == session.id), onClick = { deleting = session.id }) { Text("删除", color = Muted) }
            }
        }
        Spacer(Modifier.height(24.dp))
    }
    deleting?.let { id -> AlertDialog(onDismissRequest = { deleting = null }, title = { Text("删除这次录音？") },
        text = { Text("将删除服务器上的音频、相关事件和记忆，并移除本机副本。需要联网；备份的保留周期由服务器管理员管理。") },
        confirmButton = { TextButton(onClick = { vm.deleteSession(id); deleting = null }) { Text("删除") } },
        dismissButton = { TextButton(onClick = { deleting = null }) { Text("取消") } }) }
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
            val space = if (previous.text.lastOrNull()?.let { it.code < 128 && it.isLetterOrDigit() } == true && text.firstOrNull()?.let { it.code < 128 && it.isLetterOrDigit() } == true) " " else ""
            previous.text += space + text; previous.end = s.getLong("end_ms")
        } else result.add(Turn(s.getString("id"), label, name, text, s.getLong("start_ms"), s.getLong("end_ms"), s.optJSONArray("source_spans")?.optJSONObject(0)?.optString("chunk_id").orEmpty()))
    }
    return result
}

@Composable
private fun EventDialog(event: JSONObject, people: List<JSONObject>, vm: AppViewModel) {
    var rename by remember(event.getString("id")) { mutableStateOf(event.getString("title")) }
    AlertDialog(onDismissRequest = { vm.detail.value = null }, title = { Text("${clock(event.getLong("start_ms"))} – ${clock(event.getLong("end_ms"))}") },
        text = {
            Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                OutlinedTextField(rename, { rename = it }, label = { Text("事件标题") }, modifier = Modifier.fillMaxWidth())
                TextButton(onClick = { vm.act { vm.repo.api.call("/v1/events/${event.getString("id")}", "PATCH", JSONObject().put("title", rename)); vm.repo.refresh(vm.day.value) } }) { Text("保存标题") }
                Text("发言人标签仅在当前批次内有效。点击人物标签可手动关联。", color = Muted, fontSize = 11.sp)
                turns(event).forEach { turn ->
                    var menu by remember(turn.id) { mutableStateOf(false) }
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Box {
                            TextButton(onClick = { menu = true }, contentPadding = PaddingValues(0.dp)) { Text(turn.name, color = Teal, fontSize = 13.sp) }
                            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                                people.forEach { person -> DropdownMenuItem(text = { Text(person.getString("name")) }, onClick = { vm.assign(turn.id, person.getString("id")); menu = false }) }
                                DropdownMenuItem(text = { Text("保持未知") }, onClick = { vm.assign(turn.id, null); menu = false })
                            }
                        }
                        Text(turn.text, lineHeight = 23.sp)
                        if (turn.chunkId.isNotBlank()) TextButton(onClick = { vm.playChunk(turn.chunkId) }, contentPadding = PaddingValues(0.dp)) { Text("播放所在的原始录音块", fontSize = 11.sp, color = Muted) }
                    }
                    HorizontalDivider(color = Line)
                }
            }
        }, confirmButton = { TextButton(onClick = { vm.detail.value = null }) { Text("关闭") } })
}