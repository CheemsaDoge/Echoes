package dev.echoledger.app.ui.components

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.DateRange
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.echoledger.app.ui.model.*
import dev.echoledger.app.ui.theme.*

@Composable
fun ScatteredDialoguesListScreen(
    fragments: List<SubFragmentItem>,
    onBack: () -> Unit,
    onFragmentClick: (SubFragmentItem) -> Unit
) {
    BackHandler(onBack = onBack)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        // Top Header: "<" on left, centered "${fragments.size} 段零散对话"
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 10.dp)
        ) {
            IconButton(
                onClick = onBack,
                modifier = Modifier
                    .align(Alignment.CenterStart)
                    .testTag("scattered_list_back_button")
            ) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = "返回",
                    tint = TextPrimary
                )
            }
            Text(
                text = "${fragments.size} 段零散对话",
                color = TextPrimary,
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.align(Alignment.Center)
            )
        }

        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp)
                .testTag("scattered_dialogues_list"),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            itemsIndexed(fragments, key = { idx, frag -> "frag_${idx}_${frag.id}" }) { _, frag ->
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .border(1.dp, DividerDark, RoundedCornerShape(14.dp))
                        .clickable { onFragmentClick(frag) }
                        .padding(16.dp)
                        .testTag("scattered_item_${frag.id}")
                ) {
                    Text(
                        text = frag.title,
                        color = TextPrimary,
                        fontSize = 17.sp,
                        fontWeight = FontWeight.Bold,
                        lineHeight = 24.sp
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = "${frag.startTime} - ${frag.endTime} · ${frag.detailDuration.ifEmpty { frag.duration }}",
                        color = TextMuted,
                        fontSize = 12.sp
                    )
                    Spacer(modifier = Modifier.height(10.dp))
                    Text(
                        text = frag.listDescription,
                        color = TextSecondary,
                        fontSize = 14.sp,
                        lineHeight = 21.sp,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PersonDetailScreen(
    person: PersonVoiceProfile,
    events: List<MemoryEvent>,
    onBack: () -> Unit,
    onEventClick: (MemoryEvent) -> Unit,
    onRenamePerson: (oldName: String, newName: String) -> Unit,
    onDeletePerson: (String) -> Unit
) {
    BackHandler(onBack = onBack)

    var followUpInput by remember { mutableStateOf("") }
    var showRenameDialog by remember { mutableStateOf(false) }
    var renameText by remember(person.name) { mutableStateOf(person.name) }

    val personEvents = remember(person.name, events) {
        events.filter { it.speakers.contains(person.name) }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        // Top Header with Back, Edit Icon, and Delete Icon
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            IconButton(
                onClick = onBack,
                modifier = Modifier.testTag("person_detail_back_button")
            ) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = "返回",
                    tint = TextPrimary
                )
            }

            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(
                    onClick = { showRenameDialog = true },
                    modifier = Modifier.testTag("person_detail_edit_button")
                ) {
                    Icon(
                        imageVector = Icons.Outlined.Edit,
                        contentDescription = "重命名人物",
                        tint = TextSecondary,
                        modifier = Modifier.size(20.dp)
                    )
                }
                IconButton(
                    onClick = {
                        onDeletePerson(person.name)
                        onBack()
                    },
                    modifier = Modifier.testTag("person_detail_delete_button")
                ) {
                    Icon(
                        imageVector = Icons.Outlined.DeleteOutline,
                        contentDescription = "删除人物",
                        tint = TextSecondary,
                        modifier = Modifier.size(21.dp)
                    )
                }
            }
        }

        // Scrollable Person Header + Dialogue Cards List
        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(horizontal = 16.dp)
                .testTag("person_detail_events_list"),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(bottom = 20.dp)
        ) {
            item {
                Column(modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp)) {
                    Text(
                        text = person.name,
                        color = TextPrimary,
                        fontSize = 24.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = "${personEvents.size.coerceAtLeast(person.dialogueCount)} 段对话",
                        color = TextSecondary,
                        fontSize = 13.sp
                    )
                }
            }

            itemsIndexed(personEvents, key = { idx, event -> "person_ev_${idx}_${event.id}" }) { _, event ->
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .border(1.dp, DividerDark, RoundedCornerShape(14.dp))
                        .clickable { onEventClick(event) }
                        .padding(16.dp)
                        .testTag("person_event_card_${event.id}")
                ) {
                    Text(
                        text = event.title,
                        color = TextPrimary,
                        fontSize = 17.sp,
                        fontWeight = FontWeight.Bold,
                        lineHeight = 24.sp
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "“${event.summaryOverview}”",
                        color = TextSecondary,
                        fontSize = 14.sp,
                        lineHeight = 21.sp,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis
                    )
                    Spacer(modifier = Modifier.height(10.dp))
                    Text(
                        text = "${event.datePrefixForPerson} · ${event.startTime} - ${event.endTime} · ${event.durationLabel.uppercase()}",
                        color = TextMuted,
                        fontSize = 12.sp
                    )
                }
            }
        }

        // Bottom Input Bar: "追问关于「杨宇明」..."
        Surface(
            color = BgDark,
            tonalElevation = 4.dp,
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
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
                        Text("追问关于「${person.name}」...", color = TextMuted, fontSize = 14.sp)
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
                        .testTag("person_followup_input")
                )

                Box(
                    modifier = Modifier
                        .size(36.dp)
                        .clip(CircleShape)
                        .background(Color(0xFF2E3630))
                        .clickable {
                            if (personEvents.isNotEmpty()) {
                                onEventClick(personEvents.first())
                            }
                        }
                        .testTag("person_followup_send"),
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

    if (showRenameDialog) {
        AlertDialog(
            onDismissRequest = { showRenameDialog = false },
            containerColor = Color(0xFF1A1E1B),
            title = {
                Text("修改人物名称", color = TextPrimary, fontSize = 17.sp, fontWeight = FontWeight.Bold)
            },
            text = {
                OutlinedTextField(
                    value = renameText,
                    onValueChange = { renameText = it },
                    singleLine = true,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = PrimaryNeonGreen,
                        unfocusedBorderColor = DividerDark,
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary
                    )
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        if (renameText.isNotBlank()) {
                            onRenamePerson(person.name, renameText.trim())
                            showRenameDialog = false
                        }
                    }
                ) {
                    Text("保存", color = PrimaryNeonGreen)
                }
            },
            dismissButton = {
                TextButton(onClick = { showRenameDialog = false }) {
                    Text("取消", color = TextSecondary)
                }
            }
        )
    }
}

@Composable
fun ActivitiesScreen(
    events: List<MemoryEvent>,
    onEventClick: (MemoryEvent) -> Unit
) {
    if (events.isEmpty()) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(BgDark)
                .padding(32.dp),
            contentAlignment = Alignment.Center
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(
                    imageVector = Icons.Outlined.DateRange,
                    contentDescription = null,
                    tint = TextMuted,
                    modifier = Modifier.size(48.dp)
                )
                Spacer(modifier = Modifier.height(16.dp))
                Text("暂无活动记录", color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(modifier = Modifier.height(6.dp))
                Text("开启全时聆听后，系统将自动汇总记忆与活动", color = TextSecondary, fontSize = 13.sp)
            }
        }
        return
    }

    val grouped = remember(events) {
        events.groupBy { it.dayGroupLabel }
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
            .padding(horizontal = 16.dp)
            .testTag("activities_list"),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding = PaddingValues(vertical = 12.dp)
    ) {
        grouped.forEach { (groupLabel, groupEvents) ->
            item(key = "header_$groupLabel") {
                Text(
                    text = groupLabel,
                    color = TextSecondary,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier.padding(top = 6.dp, bottom = 2.dp)
                )
            }
            itemsIndexed(groupEvents, key = { idx, event -> "act_${idx}_${event.id}" }) { _, event ->
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .border(1.dp, DividerDark, RoundedCornerShape(14.dp))
                        .clickable { onEventClick(event) }
                        .padding(16.dp)
                        .testTag("activity_item_${event.id}")
                ) {
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
                        fontSize = 14.sp,
                        lineHeight = 21.sp,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis
                    )
                    Spacer(modifier = Modifier.height(10.dp))
                    Text(
                        text = "${event.startTime} - ${event.endTime} · ${event.durationLabel.uppercase()}",
                        color = TextMuted,
                        fontSize = 12.sp
                    )
                }
            }
        }
    }
}

@Composable
fun PeopleScreen(
    people: List<PersonVoiceProfile>,
    unrecognizedCount: Int,
    onOpenUnrecognizedVoices: () -> Unit,
    onPersonClick: (PersonVoiceProfile) -> Unit
) {
    if (people.isEmpty() && unrecognizedCount == 0) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(BgDark)
                .padding(32.dp),
            contentAlignment = Alignment.Center
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(
                    imageVector = Icons.Outlined.Person,
                    contentDescription = null,
                    tint = TextMuted,
                    modifier = Modifier.size(48.dp)
                )
                Spacer(modifier = Modifier.height(16.dp))
                Text("暂无声纹人物档案", color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(modifier = Modifier.height(6.dp))
                Text("录音中识别到的说话人将在此建档，支持更名与声纹标记", color = TextSecondary, fontSize = 13.sp)
            }
        }
        return
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp)
                .testTag("people_list"),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            // Top Unrecognized Voices Entry Card
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(SurfaceDark)
                        .border(1.dp, DividerDark, RoundedCornerShape(12.dp))
                        .clickable { onOpenUnrecognizedVoices() }
                        .padding(horizontal = 16.dp, vertical = 16.dp)
                        .testTag("unrecognized_voices_card"),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text(
                            text = "${unrecognizedCount}个待识别的声音",
                            color = TextPrimary,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = "需要你确认归属",
                            color = TextSecondary,
                            fontSize = 13.sp
                        )
                    }
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                        contentDescription = "查看待识别声音",
                        tint = TextSecondary
                    )
                }
            }

            // Speaker Cards with Huge Number Watermark on the Right
            itemsIndexed(people, key = { idx, person -> "person_${idx}_${person.name}" }) { _, person ->
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(SurfaceDark)
                        .border(1.dp, DividerDark, RoundedCornerShape(12.dp))
                        .clickable { onPersonClick(person) }
                        .padding(horizontal = 16.dp, vertical = 14.dp)
                        .testTag("person_card_${person.name}")
                ) {
                    Column(modifier = Modifier.align(Alignment.CenterStart)) {
                        Text(
                            text = person.name,
                            color = TextPrimary,
                            fontSize = 17.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                        Spacer(modifier = Modifier.height(6.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                imageVector = Icons.Outlined.ChatBubbleOutline,
                                contentDescription = null,
                                tint = TextMuted,
                                modifier = Modifier.size(14.dp)
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text(
                                text = person.dialogueCount.toString(),
                                color = TextSecondary,
                                fontSize = 13.sp
                            )
                        }
                    }

                    Text(
                        text = person.dialogueCount.toString(),
                        color = Color(0xFF2B312D),
                        fontSize = 44.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.align(Alignment.CenterEnd)
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun UnrecognizedVoicesScreen(
    clips: List<UnrecognizedVoiceClip>,
    knownPeople: List<PersonVoiceProfile>,
    onBack: () -> Unit,
    onAssignClip: (UnrecognizedVoiceClip, String) -> Unit,
    onIgnoreClip: (UnrecognizedVoiceClip) -> Unit
) {
    BackHandler(onBack = onBack)

    var taggingClip by remember { mutableStateOf<UnrecognizedVoiceClip?>(null) }
    var newPersonName by remember { mutableStateOf("") }
    var playingClipId by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        // Header
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            IconButton(
                onClick = onBack,
                modifier = Modifier.testTag("unrecognized_back_button")
            ) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = "返回",
                    tint = TextPrimary
                )
            }
            Column(
                modifier = Modifier.weight(1f),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = "待识别的声音",
                    color = TextPrimary,
                    fontSize = 17.sp,
                    fontWeight = FontWeight.Bold
                )
                Text(
                    text = "共 ${clips.size} 条 · 确认后归入「已识别人物」",
                    color = TextSecondary,
                    fontSize = 12.sp
                )
            }
            Spacer(modifier = Modifier.width(48.dp))
        }

        if (clips.isEmpty()) {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                Text("暂无待识别的声音", color = TextMuted, fontSize = 14.sp)
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                contentPadding = PaddingValues(vertical = 12.dp)
            ) {
                itemsIndexed(clips, key = { idx, clip -> "clip_${idx}_${clip.id}" }) { _, clip ->
                    val isPlaying = playingClipId == clip.id
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(14.dp))
                            .background(SurfaceDark)
                            .border(1.dp, DividerDark, RoundedCornerShape(14.dp))
                            .padding(16.dp)
                    ) {
                        Row(verticalAlignment = Alignment.Top) {
                            Box(
                                modifier = Modifier
                                    .size(40.dp)
                                    .clip(CircleShape)
                                    .background(if (isPlaying) PrimaryNeonGreen else SurfaceCardElevated)
                                    .clickable {
                                        playingClipId = if (isPlaying) null else clip.id
                                    },
                                contentAlignment = Alignment.Center
                            ) {
                                Icon(
                                    imageVector = Icons.Default.PlayArrow,
                                    contentDescription = "播放语音样本",
                                    tint = if (isPlaying) Color.Black else PrimaryNeonGreen
                                )
                            }
                            Spacer(modifier = Modifier.width(12.dp))
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = clip.quote,
                                    color = TextPrimary,
                                    fontSize = 15.sp,
                                    fontWeight = FontWeight.Medium,
                                    lineHeight = 22.sp
                                )
                                Spacer(modifier = Modifier.height(6.dp))
                                Text(
                                    text = "${clip.timestamp} · 出现 ${clip.occurrences} 次 · ${clip.sampleCount} 个样本",
                                    color = TextSecondary,
                                    fontSize = 12.sp
                                )
                            }
                        }

                        Spacer(modifier = Modifier.height(14.dp))

                        // Primary Button: 标记说话人
                        Button(
                            onClick = { taggingClip = clip },
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(42.dp)
                                .testTag("tag_speaker_btn_${clip.id}"),
                            shape = RoundedCornerShape(8.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Color(0xFF272C29),
                                contentColor = TextPrimary
                            )
                        ) {
                            Text("标记说话人", fontSize = 14.sp, fontWeight = FontWeight.Medium)
                        }

                        Spacer(modifier = Modifier.height(8.dp))

                        // Secondary Row: 忽略 | 换一个
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            OutlinedButton(
                                onClick = { onIgnoreClip(clip) },
                                modifier = Modifier
                                    .weight(1f)
                                    .height(38.dp),
                                shape = RoundedCornerShape(8.dp),
                                border = androidx.compose.foundation.BorderStroke(1.dp, DividerDark),
                                colors = ButtonDefaults.outlinedButtonColors(contentColor = TextSecondary)
                            ) {
                                Text("忽略", fontSize = 13.sp)
                            }
                            if (clip.sampleCount > 1) {
                                OutlinedButton(
                                    onClick = {},
                                    modifier = Modifier
                                        .weight(1f)
                                        .height(38.dp),
                                    shape = RoundedCornerShape(8.dp),
                                    border = androidx.compose.foundation.BorderStroke(1.dp, DividerDark),
                                    colors = ButtonDefaults.outlinedButtonColors(contentColor = TextSecondary)
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.Refresh,
                                        contentDescription = null,
                                        modifier = Modifier.size(14.dp)
                                    )
                                    Spacer(modifier = Modifier.width(4.dp))
                                    Text("换一个", fontSize = 13.sp)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    if (taggingClip != null) {
        ModalBottomSheet(
            onDismissRequest = { taggingClip = null },
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
                    text = "标记为...",
                    color = TextPrimary,
                    fontSize = 17.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.align(Alignment.CenterHorizontally)
                )
                Spacer(modifier = Modifier.height(16.dp))
                Text("已识别人物", color = TextSecondary, fontSize = 13.sp)
                Spacer(modifier = Modifier.height(10.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    knownPeople.take(6).forEach { person ->
                        Surface(
                            shape = RoundedCornerShape(8.dp),
                            color = Color(0xFF263029),
                            modifier = Modifier.clickable {
                                onAssignClip(taggingClip!!, person.name)
                                taggingClip = null
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
                Text("新建人物", color = TextSecondary, fontSize = 13.sp)
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = newPersonName,
                    onValueChange = { newPersonName = it },
                    placeholder = { Text("输入新人物名字", color = TextMuted) },
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
                        val targetName = if (newPersonName.isNotBlank()) newPersonName.trim() else "其它"
                        onAssignClip(taggingClip!!, targetName)
                        newPersonName = ""
                        taggingClip = null
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(48.dp),
                    shape = RoundedCornerShape(10.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = PrimaryNeonGreen,
                        contentColor = Color.Black
                    )
                ) {
                    Text("保存为新人物", fontSize = 15.sp, fontWeight = FontWeight.Bold)
                }
                Spacer(modifier = Modifier.height(16.dp))
            }
        }
    }
}

@Composable
fun AgentScreen(
    serverBaseUrl: String = "",
    mcpToken: String = "",
    onOpenAgentGuide: (String) -> Unit
) {
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
            .padding(horizontal = 16.dp)
            .testTag("agent_screen_list"),
        horizontalAlignment = Alignment.CenterHorizontally,
        contentPadding = PaddingValues(vertical = 20.dp)
    ) {
        item {
            Text(
                text = "让你的 AI 助手连接记忆，生成新产物",
                color = TextPrimary,
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold
            )
            Spacer(modifier = Modifier.height(6.dp))
            Text(
                text = "请选择你常用的 Agent 并点击。",
                color = TextSecondary,
                fontSize = 14.sp
            )
            Spacer(modifier = Modifier.height(20.dp))
        }

        // 3-column Grid of 10 Agents
        item {
            val rows = SampleMemoryData.agentList.chunked(3)
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                rows.forEach { rowItems ->
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        rowItems.forEach { agent ->
                            AgentGridCard(
                                agent = agent,
                                modifier = Modifier.weight(1f),
                                onClick = { onOpenAgentGuide(agent.name) }
                            )
                        }
                        repeat(3 - rowItems.size) {
                            Spacer(modifier = Modifier.weight(1f))
                        }
                    }
                }
            }
        }

        item {
            Spacer(modifier = Modifier.height(28.dp))
            Column(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = "MCP 服务端连接状态",
                    color = TextSecondary,
                    fontSize = 13.sp
                )
                Spacer(modifier = Modifier.height(10.dp))
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(SurfaceDark)
                        .border(
                            1.dp,
                            if (mcpToken.isNotBlank()) PrimaryNeonGreen.copy(alpha = 0.5f) else DividerDark,
                            RoundedCornerShape(12.dp)
                        )
                        .clickable { onOpenAgentGuide("MCP Server") }
                        .padding(16.dp)
                        .testTag("created_agent_antigravity"),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "MCP Server (Model Context Protocol)",
                            color = TextPrimary,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = if (mcpToken.isNotBlank()) {
                                "安全令牌已配置 · 记忆上下文通道就绪"
                            } else {
                                "未配置安全令牌 · 点击前往配置 Claude / Cursor 提示词"
                            },
                            color = if (mcpToken.isNotBlank()) PrimaryNeonGreen else TextSecondary,
                            fontSize = 12.sp
                        )
                    }
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                        contentDescription = null,
                        tint = TextSecondary
                    )
                }
            }
        }

        item {
            Spacer(modifier = Modifier.height(36.dp))
            Text(
                text = "基于 MCP 开放协议 · 主流 AI 工具通用",
                color = TextMuted,
                fontSize = 12.sp
            )
        }
    }
}

@Composable
private fun AgentGridCard(
    agent: AgentItem,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    Box(
        modifier = modifier
            .height(94.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(SurfaceDark)
            .border(1.dp, DividerDark, RoundedCornerShape(12.dp))
            .clickable { onClick() }
            .padding(10.dp)
            .testTag("agent_card_${agent.id}")
    ) {
        Icon(
            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
            contentDescription = null,
            tint = TextMuted,
            modifier = Modifier
                .size(14.dp)
                .align(Alignment.TopEnd)
        )

        Column(
            modifier = Modifier.align(Alignment.Center),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            AgentBrandSymbol(agent.id)
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = agent.name,
                color = TextPrimary,
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
    }
}

@Composable
private fun AgentBrandSymbol(id: String) {
    val (symbol, tint) = when (id) {
        "claude" -> "✳" to Color(0xFFD97757)
        "codex" -> "◎" to TextPrimary
        "hermes" -> "🏛" to Color(0xFFE0B060)
        "cursor" -> "❖" to TextPrimary
        "antigravity" -> "▲" to Color(0xFF4285F4)
        "openclaw" -> "🦀" to Color(0xFFE54D42)
        "grok" -> "𝕏" to TextPrimary
        "workbuddy" -> "🐾" to PrimaryNeonGreen
        "deepseek" -> "🐋" to Color(0xFF4D6BFE)
        else -> "•••" to TextSecondary
    }
    Text(
        text = symbol,
        color = tint,
        fontSize = 22.sp,
        fontWeight = FontWeight.Bold
    )
}

@Composable
fun AgentIntegrationSheetScreen(
    agentName: String,
    onClose: () -> Unit,
    onCopyPrompt: () -> Unit
) {
    BackHandler(onBack = onClose)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        // Top Header
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp)
        ) {
            Text(
                text = agentName,
                color = TextSecondary,
                fontSize = 16.sp,
                modifier = Modifier.align(Alignment.Center)
            )
            IconButton(
                onClick = onClose,
                modifier = Modifier
                    .align(Alignment.CenterEnd)
                    .size(32.dp)
                    .testTag("close_agent_guide_button")
            ) {
                Icon(
                    imageVector = Icons.Default.Close,
                    contentDescription = "关闭",
                    tint = TextSecondary
                )
            }
        }

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 24.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
            contentPadding = PaddingValues(vertical = 16.dp)
        ) {
            item {
                Text(
                    text = "把 Memory 接入\n$agentName",
                    color = TextPrimary,
                    fontSize = 30.sp,
                    fontWeight = FontWeight.Bold,
                    lineHeight = 38.sp
                )
                Spacer(modifier = Modifier.height(10.dp))
                Text(
                    text = "$agentName 的 agent 能自己动手完成配置,把提示词交给它就行。",
                    color = TextSecondary,
                    fontSize = 15.sp,
                    lineHeight = 22.sp
                )
            }

            // Step 1
            item {
                StepHeaderRow(
                    stepNumber = "1",
                    title = "打开 $agentName,点左上角 New Conversation 开一个新对话。",
                    subtitle = "对话框就在窗口正中,直接用当前这个也行。"
                )
                Spacer(modifier = Modifier.height(12.dp))
                Surface(
                    color = Color(0xFF171A18),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .border(1.dp, DividerDark, RoundedCornerShape(12.dp))
                ) {
                    Row(modifier = Modifier.padding(14.dp)) {
                        Column(modifier = Modifier.width(110.dp)) {
                            Surface(
                                color = Color(0xFF282E2A),
                                shape = RoundedCornerShape(6.dp)
                            ) {
                                Text(
                                    text = "New Conversation",
                                    color = TextPrimary,
                                    fontSize = 10.sp,
                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 5.dp)
                                )
                            }
                            Spacer(modifier = Modifier.height(8.dp))
                            Text("Conversation History", color = TextMuted, fontSize = 9.sp)
                            Spacer(modifier = Modifier.height(4.dp))
                            Text("Scheduled Tasks", color = TextMuted, fontSize = 9.sp)
                            Spacer(modifier = Modifier.height(8.dp))
                            Text("Projects", color = TextMuted, fontSize = 9.sp)
                            Text("Website redesign", color = TextSecondary, fontSize = 9.sp)
                        }
                        Spacer(modifier = Modifier.width(12.dp))
                        Column(
                            modifier = Modifier
                                .weight(1f)
                                .clip(RoundedCornerShape(8.dp))
                                .background(Color(0xFF1F2421))
                                .padding(10.dp)
                        ) {
                            Text("Website redesign", color = TextPrimary, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                            Spacer(modifier = Modifier.height(6.dp))
                            Text("Ask anything, @ to mention, / for actions", color = TextMuted, fontSize = 10.sp)
                            Spacer(modifier = Modifier.height(12.dp))
                            Text("+  Gemini 3.1 Pro  ·  Local", color = PrimaryNeonGreen, fontSize = 9.sp)
                        }
                    }
                }
            }

            // Step 2
            item {
                StepHeaderRow(
                    stepNumber = "2",
                    title = "回到 Memory,点底部「复制提示词」,粘贴进对话框发送。",
                    subtitle = null
                )
                Spacer(modifier = Modifier.height(12.dp))
                Surface(
                    color = Color(0xFF171A18),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .border(1.dp, DividerDark, RoundedCornerShape(12.dp))
                ) {
                    Column(modifier = Modifier.padding(14.dp)) {
                        Text(
                            text = "Please connect Memory as my memory vault (add an MCP server) with the config below. When done, call search_memory once to confirm it works:\nURL: https://mcp.memory.app/mcp\nKey: hnk_live_••••••••••••Dyvn",
                            color = TextPrimary,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                            lineHeight = 17.sp
                        )
                    }
                }
            }

            // Step 3
            item {
                StepHeaderRow(
                    stepNumber = "3",
                    title = "它会自己接好,并搜一条你的记忆做验证。",
                    subtitle = "输入框下面那排会显示 MCP 的连接状态,接好后不再报错。"
                )
                Spacer(modifier = Modifier.height(12.dp))
                Surface(
                    color = Color(0xFF171A18),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .border(1.dp, DividerDark, RoundedCornerShape(12.dp))
                ) {
                    Column(modifier = Modifier.padding(14.dp)) {
                        Text(
                            text = "✓ Connected. Verified with one memory: \"Last Wednesday you confirmed 5-day Japan pricing with Mike...\"",
                            color = PrimaryNeonGreen,
                            fontSize = 12.sp,
                            lineHeight = 18.sp
                        )
                    }
                }
            }
        }

        // Bottom CTA
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 14.dp)
                .navigationBarsPadding(),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Button(
                onClick = onCopyPrompt,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp)
                    .testTag("copy_agent_prompt_button"),
                shape = RoundedCornerShape(10.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = PrimaryNeonGreen,
                    contentColor = Color.Black
                )
            ) {
                Text("复制提示词", fontSize = 16.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(modifier = Modifier.height(10.dp))
            Text(
                text = "查看配置和接入信息",
                color = TextSecondary,
                fontSize = 13.sp
            )
        }
    }
}

@Composable
private fun StepHeaderRow(
    stepNumber: String,
    title: String,
    subtitle: String?
) {
    Row(verticalAlignment = Alignment.Top) {
        Box(
            modifier = Modifier
                .size(24.dp)
                .clip(CircleShape)
                .background(Color(0xFF262D28)),
            contentAlignment = Alignment.Center
        ) {
            Text(
                text = stepNumber,
                color = TextSecondary,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold
            )
        }
        Spacer(modifier = Modifier.width(12.dp))
        Column {
            Text(
                text = title,
                color = TextPrimary,
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                lineHeight = 23.sp
            )
            if (subtitle != null) {
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = subtitle,
                    color = TextSecondary,
                    fontSize = 13.sp,
                    lineHeight = 19.sp
                )
            }
        }
    }
}
