package dev.echoledger.app.ui.components

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.echoledger.app.ui.model.MemoryEvent
import dev.echoledger.app.ui.model.PersonVoiceProfile
import dev.echoledger.app.ui.theme.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EventDetailScreen(
    event: MemoryEvent,
    knownPeople: List<PersonVoiceProfile>,
    onBack: () -> Unit,
    onOpenAiNote: (MemoryEvent) -> Unit,
    onSwitchToAgentTab: () -> Unit,
    onOpenUnrecognizedVoices: () -> Unit = {},
    onReassignSpeaker: (oldName: String, newName: String) -> Unit
) {
    BackHandler(onBack = onBack)

    var selectedSubTab by remember { mutableIntStateOf(0) } // 0 = 概览, 1 = 逐字稿, 2 = 声音
    var searchQuery by remember { mutableStateOf("") }
    var followUpInput by remember { mutableStateOf("") }
    var editingSpeakerName by remember { mutableStateOf<String?>(null) }
    var customNewSpeakerInput by remember { mutableStateOf("") }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            // Top bar with Back and Trash icon
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                IconButton(
                    onClick = onBack,
                    modifier = Modifier.testTag("detail_back_button")
                ) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                        contentDescription = "返回",
                        tint = TextPrimary
                    )
                }

                IconButton(
                    onClick = onBack,
                    modifier = Modifier.testTag("detail_delete_button")
                ) {
                    Icon(
                        imageVector = Icons.Outlined.DeleteOutline,
                        contentDescription = "删除记录",
                        tint = TextSecondary
                    )
                }
            }

            // Event Title & Time Info
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp)
            ) {
                Text(
                    text = event.title,
                    color = TextPrimary,
                    fontSize = 21.sp,
                    fontWeight = FontWeight.Bold,
                    lineHeight = 29.sp
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    text = event.fullDateLabel,
                    color = TextSecondary,
                    fontSize = 13.sp
                )
                if (event.speakers.isNotEmpty() || event.unrecognizedVoiceCount > 0) {
                    Spacer(modifier = Modifier.height(12.dp))
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        event.speakers.forEach { speaker ->
                            SpeakerChip(
                                name = speaker,
                                onClick = { editingSpeakerName = speaker }
                            )
                        }
                        if (event.unrecognizedVoiceCount > 0) {
                            OutlinedUnrecognizedChip(
                                count = event.unrecognizedVoiceCount,
                                onClick = onOpenUnrecognizedVoices
                            )
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // 3-Segment Tab Row: 概览 | 逐字稿 | 声音
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp)
                    .height(42.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(Color(0xFF1A1E1B))
                    .padding(4.dp),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                val tabs = listOf("概览", "逐字稿", "声音")
                tabs.forEachIndexed { index, title ->
                    val selected = selectedSubTab == index
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight()
                            .clip(RoundedCornerShape(8.dp))
                            .background(if (selected) Color(0xFF2B312D) else Color.Transparent)
                            .clickable { selectedSubTab = index }
                            .testTag("detail_subtab_$index"),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = title,
                            color = if (selected) TextPrimary else TextSecondary,
                            fontSize = 14.sp,
                            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Tab Content
            Box(modifier = Modifier.weight(1f)) {
                when (selectedSubTab) {
                    0 -> OverviewSubTabContent(
                        event = event,
                        onGenerateDetailedNote = { onOpenAiNote(event) },
                        onUseAgent = onSwitchToAgentTab
                    )
                    1 -> TranscriptSubTabContent(
                        event = event,
                        searchQuery = searchQuery,
                        onSearchQueryChange = { searchQuery = it },
                        onSpeakerClick = { speaker -> editingSpeakerName = speaker }
                    )
                    2 -> VoiceSubTabContent()
                }
            }

            // Bottom Follow-up Input Bar ("按你的需要随心追问")
            Surface(
                color = BgDark,
                tonalElevation = 4.dp,
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 12.dp)
                        .clip(RoundedCornerShape(28.dp))
                        .background(Color(0xFF1C211E))
                        .border(1.dp, DividerDark, RoundedCornerShape(28.dp))
                        .padding(horizontal = 16.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    TextField(
                        value = followUpInput,
                        onValueChange = { followUpInput = it },
                        placeholder = {
                            Text("按你的需要随心追问", color = TextMuted, fontSize = 14.sp)
                        },
                        colors = TextFieldDefaults.colors(
                            focusedContainerColor = Color.Transparent,
                            unfocusedContainerColor = Color.Transparent,
                            focusedIndicatorColor = Color.Transparent,
                            unfocusedIndicatorColor = Color.Transparent,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        ),
                        singleLine = true,
                        modifier = Modifier
                            .weight(1f)
                            .testTag("followup_input_field")
                    )

                    Box(
                        modifier = Modifier
                            .size(36.dp)
                            .clip(CircleShape)
                            .background(Color(0xFF2E3630))
                            .clickable {
                                onOpenAiNote(event)
                            }
                            .testTag("followup_send_button"),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.ArrowUpward,
                            contentDescription = "发送追问",
                            tint = TextSecondary,
                            modifier = Modifier.size(18.dp)
                        )
                    }
                }
            }
        }

        // Speaker Reassignment Bottom Sheet
        if (editingSpeakerName != null) {
            ModalBottomSheet(
                onDismissRequest = { editingSpeakerName = null },
                containerColor = Color(0xFF181C19),
                contentColor = TextPrimary
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 20.dp, vertical = 12.dp)
                        .navigationBarsPadding()
                ) {
                    Text(
                        text = "更改归属",
                        color = TextPrimary,
                        fontSize = 17.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.align(Alignment.CenterHorizontally)
                    )
                    Spacer(modifier = Modifier.height(16.dp))
                    Text(
                        text = "当前标为 $editingSpeakerName",
                        color = TextSecondary,
                        fontSize = 13.sp
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = "改成是谁",
                        color = TextSecondary,
                        fontSize = 13.sp
                    )
                    Spacer(modifier = Modifier.height(10.dp))

                    // Flow of existing people chips
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        knownPeople.take(6).forEach { person ->
                            Surface(
                                shape = RoundedCornerShape(8.dp),
                                color = Color(0xFF263029),
                                modifier = Modifier.clickable {
                                    onReassignSpeaker(editingSpeakerName!!, person.name)
                                    editingSpeakerName = null
                                }
                            ) {
                                Text(
                                    text = person.name,
                                    color = TextPrimary,
                                    fontSize = 14.sp,
                                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp)
                                )
                            }
                        }
                    }

                    Spacer(modifier = Modifier.height(20.dp))
                    Text(
                        text = "新建人物",
                        color = TextSecondary,
                        fontSize = 13.sp
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    OutlinedTextField(
                        value = customNewSpeakerInput,
                        onValueChange = { customNewSpeakerInput = it },
                        placeholder = { Text("输入要改成名字", color = TextMuted) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = PrimaryNeonGreen,
                            unfocusedBorderColor = DividerDark,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        )
                    )
                    Spacer(modifier = Modifier.height(14.dp))
                    Button(
                        onClick = {
                            if (customNewSpeakerInput.isNotBlank()) {
                                onReassignSpeaker(editingSpeakerName!!, customNewSpeakerInput.trim())
                                customNewSpeakerInput = ""
                                editingSpeakerName = null
                            }
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(48.dp),
                        shape = RoundedCornerShape(10.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Color(0xFF282E2A),
                            contentColor = TextPrimary
                        )
                    ) {
                        Text("改标为新人物", fontSize = 15.sp, fontWeight = FontWeight.Medium)
                    }
                    Spacer(modifier = Modifier.height(16.dp))
                }
            }
        }
    }
}

@Composable
private fun OutlinedUnrecognizedChip(
    count: Int,
    onClick: () -> Unit
) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(Color(0xFF171C19))
            .border(1.dp, Color(0xFF343E37), RoundedCornerShape(6.dp))
            .clickable { onClick() }
            .padding(horizontal = 10.dp, vertical = 5.dp)
            .testTag("unrecognized_voice_chip")
    ) {
        Text(
            text = "${count}个未识别的声音",
            color = TextSecondary,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium
        )
    }
}

@Composable
private fun OverviewSubTabContent(
    event: MemoryEvent,
    onGenerateDetailedNote: () -> Unit,
    onUseAgent: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 20.dp)
    ) {
        Text(
            text = event.summaryOverview,
            color = TextPrimary,
            fontSize = 15.sp,
            lineHeight = 24.sp
        )

        Spacer(modifier = Modifier.height(24.dp))

        // Card 1: 生成详细整理
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .clickable { onGenerateDetailedNote() }
                .testTag("generate_detailed_note_card"),
            color = Color(0xFF1A1E1B),
            shape = RoundedCornerShape(12.dp)
        ) {
            Row(
                modifier = Modifier.padding(16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = Icons.Outlined.Description,
                    contentDescription = null,
                    tint = TextSecondary,
                    modifier = Modifier.size(20.dp)
                )
                Spacer(modifier = Modifier.width(12.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = "生成详细整理",
                            color = TextPrimary,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                            contentDescription = null,
                            tint = TextSecondary,
                            modifier = Modifier.size(16.dp)
                        )
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = "把这段聆听整理成更详细的要点与待办",
                        color = TextSecondary,
                        fontSize = 13.sp
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        // Card 2: 使用你的 Agent
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .clickable { onUseAgent() }
                .testTag("use_your_agent_card"),
            color = Color(0xFF1A1E1B),
            shape = RoundedCornerShape(12.dp)
        ) {
            Row(
                modifier = Modifier.padding(16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = Icons.Outlined.AutoAwesome,
                    contentDescription = null,
                    tint = TextSecondary,
                    modifier = Modifier.size(20.dp)
                )
                Spacer(modifier = Modifier.width(12.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = "使用你的 Agent",
                            color = TextPrimary,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                            contentDescription = null,
                            tint = TextSecondary,
                            modifier = Modifier.size(16.dp)
                        )
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = "连接记忆，做更深入的整理与追问",
                        color = TextSecondary,
                        fontSize = 13.sp
                    )
                }
            }
        }
    }
}

@Composable
private fun TranscriptSubTabContent(
    event: MemoryEvent,
    searchQuery: String,
    onSearchQueryChange: (String) -> Unit,
    onSpeakerClick: (String) -> Unit
) {
    val filteredTranscripts = remember(event.transcripts, searchQuery) {
        if (searchQuery.isBlank()) event.transcripts
        else event.transcripts.filter {
            it.text.contains(searchQuery, ignoreCase = true) ||
                it.speaker.contains(searchQuery, ignoreCase = true)
        }
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(bottom = 24.dp)
    ) {
        item {
            // Search Box
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .background(Color(0xFF1A1E1B))
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = Icons.Default.Search,
                    contentDescription = null,
                    tint = TextMuted,
                    modifier = Modifier.size(18.dp)
                )
                Spacer(modifier = Modifier.width(8.dp))
                Box(modifier = Modifier.weight(1f)) {
                    if (searchQuery.isEmpty()) {
                        Text("搜索发言人或逐字稿...", color = TextMuted, fontSize = 14.sp)
                    }
                    androidx.compose.foundation.text.BasicTextField(
                        value = searchQuery,
                        onValueChange = onSearchQueryChange,
                        textStyle = androidx.compose.ui.text.TextStyle(
                            color = TextPrimary,
                            fontSize = 14.sp
                        ),
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            }
        }

        item {
            // Tip Banner: 点发言人名字可标注 / 更改归属
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .background(Color(0xFF171B18))
                    .border(1.dp, DividerDark, RoundedCornerShape(10.dp))
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = Icons.Outlined.Info,
                    contentDescription = null,
                    tint = TextSecondary,
                    modifier = Modifier.size(16.dp)
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = "点发言人名字可标注 / 更改归属",
                    color = TextSecondary,
                    fontSize = 13.sp
                )
            }
        }

        if (filteredTranscripts.isEmpty()) {
            item {
                Text(
                    text = "暂无匹配逐字稿片段",
                    color = TextMuted,
                    fontSize = 14.sp,
                    modifier = Modifier.padding(vertical = 24.dp)
                )
            }
        } else {
            items(filteredTranscripts, key = { it.id }) { seg ->
                Column(modifier = Modifier.fillMaxWidth()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = seg.speaker,
                            color = TextSecondary,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier
                                .clip(RoundedCornerShape(4.dp))
                                .clickable { onSpeakerClick(seg.speaker) }
                                .padding(vertical = 2.dp, horizontal = 2.dp)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = seg.time,
                            color = TextMuted,
                            fontSize = 12.sp
                        )
                    }
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = seg.text,
                        color = TextPrimary,
                        fontSize = 15.sp,
                        lineHeight = 24.sp
                    )
                }
            }
        }
    }
}

@Composable
private fun VoiceSubTabContent() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 20.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(Color(0xFF1A1E1B))
                .border(1.dp, DividerDark, RoundedCornerShape(12.dp))
                .padding(16.dp),
            verticalAlignment = Alignment.Top
        ) {
            Icon(
                imageVector = Icons.Outlined.Info,
                contentDescription = null,
                tint = TextSecondary,
                modifier = Modifier.size(18.dp)
            )
            Spacer(modifier = Modifier.width(10.dp))
            Text(
                text = "本段没有待识别的声音 —— 新说话人出现时会在这里等你确认。",
                color = TextSecondary,
                fontSize = 14.sp,
                lineHeight = 21.sp
            )
        }
    }
}

@Composable
fun AiNoteChatScreen(
    event: MemoryEvent,
    onBack: () -> Unit
) {
    BackHandler(onBack = onBack)

    var followUpText by remember { mutableStateOf("") }
    var extraMessages by remember { mutableStateOf(listOf<String>()) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        // Top Header
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            IconButton(
                onClick = onBack,
                modifier = Modifier.testTag("ai_note_back_button")
            ) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = "返回",
                    tint = TextPrimary
                )
            }
            Text(
                text = "请把这次内容整理成一份笔记：先写...",
                color = TextPrimary,
                fontSize = 17.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f)
            )
            IconButton(onClick = {}) {
                Icon(
                    imageVector = Icons.Default.History,
                    contentDescription = "历史对话",
                    tint = TextSecondary
                )
            }
        }

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            // User Prompt Green Bubble
            item {
                Box(
                    modifier = Modifier.fillMaxWidth(),
                    contentAlignment = Alignment.CenterEnd
                ) {
                    Surface(
                        color = Color(0xFF213C24),
                        shape = RoundedCornerShape(16.dp),
                        modifier = Modifier.widthIn(max = 310.dp)
                    ) {
                        Text(
                            text = "请把这次内容整理成一份笔记：先写清楚谈了什么，再挑出值得记住的信息；如果谈到了结论或要办的事，单独列出来（写明负责人和时间）——没有就不用硬凑。",
                            color = TextPrimary,
                            fontSize = 15.sp,
                            lineHeight = 23.sp,
                            modifier = Modifier.padding(16.dp)
                        )
                    }
                }
            }

            // Tool Execution Pill
            item {
                Surface(
                    color = Color(0xFF1A1E1B),
                    shape = RoundedCornerShape(10.dp)
                ) {
                    Text(
                        text = "I'll pull the transcript for this session first.",
                        color = TextSecondary,
                        fontSize = 14.sp,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)
                    )
                }
            }

            // Structured AI Note Result
            item {
                val noteBody = event.detailedNoteMarkdown.ifEmpty {
                    """
                    ${event.title}
                    （${event.fullDateLabel}）

                    一、这次谈了什么
                    ${event.summaryOverview}

                    二、值得记住的要点
                    • 关键时间段：${event.startTime} - ${event.endTime}（共 ${event.durationLabel}）
                    • 参与发言人：${if (event.speakers.isEmpty()) "现场多声源记录" else event.speakers.joinToString("、")}

                    三、结论与待办
                    本次记录已完成自动归档，可随时通过关键词或声纹检索。
                    """.trimIndent()
                }
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(Color(0xFF171B18))
                        .padding(16.dp)
                ) {
                    Text(
                        text = noteBody,
                        color = TextPrimary,
                        fontSize = 15.sp,
                        lineHeight = 24.sp
                    )
                    Spacer(modifier = Modifier.height(16.dp))
                    HorizontalDivider(color = DividerDark)
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = "需要我把它存成一份 Markdown 文件（例如 /notes/ 下）方便日后查阅吗？",
                        color = TextSecondary,
                        fontSize = 14.sp,
                        lineHeight = 21.sp
                    )
                }
            }

            items(extraMessages) { msg ->
                Box(
                    modifier = Modifier.fillMaxWidth(),
                    contentAlignment = Alignment.CenterEnd
                ) {
                    Surface(
                        color = Color(0xFF213C24),
                        shape = RoundedCornerShape(16.dp)
                    ) {
                        Text(
                            text = msg,
                            color = TextPrimary,
                            fontSize = 15.sp,
                            modifier = Modifier.padding(14.dp)
                        )
                    }
                }
            }
        }

        // Bottom Input Bar ("继续追问...")
        Surface(
            color = BgDark,
            modifier = Modifier.fillMaxWidth()
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 12.dp)
                    .clip(RoundedCornerShape(28.dp))
                    .background(Color(0xFF1C211E))
                    .border(1.dp, DividerDark, RoundedCornerShape(28.dp))
                    .padding(horizontal = 16.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                TextField(
                    value = followUpText,
                    onValueChange = { followUpText = it },
                    placeholder = {
                        Text("继续追问...", color = TextMuted, fontSize = 14.sp)
                    },
                    colors = TextFieldDefaults.colors(
                        focusedContainerColor = Color.Transparent,
                        unfocusedContainerColor = Color.Transparent,
                        focusedIndicatorColor = Color.Transparent,
                        unfocusedIndicatorColor = Color.Transparent,
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary
                    ),
                    singleLine = true,
                    modifier = Modifier.weight(1f)
                )

                Box(
                    modifier = Modifier
                        .size(36.dp)
                        .clip(CircleShape)
                        .background(PrimaryNeonGreen)
                        .clickable {
                            if (followUpText.isNotBlank()) {
                                extraMessages = extraMessages + followUpText.trim()
                                followUpText = ""
                            }
                        },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.ArrowUpward,
                        contentDescription = "发送",
                        tint = Color.Black,
                        modifier = Modifier.size(18.dp)
                    )
                }
            }
        }
    }
}
