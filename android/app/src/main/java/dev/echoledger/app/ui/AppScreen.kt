@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
package dev.echoledger.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AccessTime
import androidx.compose.material.icons.outlined.Layers
import androidx.compose.material.icons.outlined.PeopleOutline
import androidx.compose.material.icons.outlined.SmartToy
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.echoledger.app.ui.components.*
import dev.echoledger.app.ui.model.*
import dev.echoledger.app.ui.theme.*
import kotlinx.coroutines.delay
import java.time.LocalDate

enum class MainBottomTab(val label: String, val icon: ImageVector, val tag: String) {
    TIMELINE("时间线", Icons.Outlined.AccessTime, "nav_tab_timeline"),
    ACTIVITIES("活动", Icons.Outlined.Layers, "nav_tab_activities"),
    PEOPLE("人物", Icons.Outlined.PeopleOutline, "nav_tab_people"),
    AGENT("Agent", Icons.Outlined.SmartToy, "nav_tab_agent")
}

@Composable
fun AppScreen(
    vm: AppViewModel,
    onRecord: (Boolean) -> Unit,
    onStop: () -> Unit
) {
    val recordingState by vm.recording.collectAsStateWithLifecycle()
    val repoMessage by vm.repo.message.collectAsStateWithLifecycle()
    val rawEvents by vm.events.collectAsStateWithLifecycle()
    val rawPeople by vm.people.collectAsStateWithLifecycle()
    val showDemoDataWhenEmpty by vm.showDemoDataWhenEmpty.collectAsStateWithLifecycle()

    var currentTab by remember { mutableStateOf(MainBottomTab.TIMELINE) }
    var selectedDay by remember { mutableIntStateOf(LocalDate.now().dayOfMonth) }
    var isCalendarExpanded by remember { mutableStateOf(false) }

    // Convert raw backend events / people to UI models
    val convertedRealEvents = remember(rawEvents) {
        rawEvents.map { jsonToMemoryEvent(it) }
    }
    val convertedRealPeople = remember(rawPeople) {
        rawPeople.map { jsonToPersonVoiceProfile(it) }
    }

    // Merge with sample memory data when empty or configured
    var events by remember(convertedRealEvents, showDemoDataWhenEmpty) {
        val list = if (convertedRealEvents.isNotEmpty()) {
            if (showDemoDataWhenEmpty) convertedRealEvents + SampleMemoryData.events else convertedRealEvents
        } else {
            if (showDemoDataWhenEmpty) SampleMemoryData.events else emptyList()
        }
        mutableStateOf(list)
    }

    var people by remember(convertedRealPeople, showDemoDataWhenEmpty) {
        val list = if (convertedRealPeople.isNotEmpty()) {
            if (showDemoDataWhenEmpty) convertedRealPeople + SampleMemoryData.initialPeople else convertedRealPeople
        } else {
            if (showDemoDataWhenEmpty) SampleMemoryData.initialPeople else emptyList()
        }
        mutableStateOf(list)
    }

    var unrecognizedClips by remember {
        mutableStateOf(SampleMemoryData.initialUnrecognizedClips)
    }

    // Secondary navigation overlays
    var activeScatteredFragments by remember { mutableStateOf<List<SubFragmentItem>?>(null) }
    var activePersonDetail by remember { mutableStateOf<PersonVoiceProfile?>(null) }
    var activeEventDetail by remember { mutableStateOf<MemoryEvent?>(null) }
    var activeAiNoteEvent by remember { mutableStateOf<MemoryEvent?>(null) }
    var showUnrecognizedVoicesScreen by remember { mutableStateOf(false) }
    var activeAgentGuideName by remember { mutableStateOf<String?>(null) }
    var activeSettingsSubPage by remember { mutableStateOf<SettingsSubPage?>(null) }

    // Top pill toast
    var toastMessage by remember { mutableStateOf<String?>("已是最新的") }

    LaunchedEffect(toastMessage) {
        if (toastMessage != null) {
            delay(2200)
            toastMessage = null
        }
    }

    LaunchedEffect(repoMessage) {
        if (repoMessage.isNotBlank()) {
            toastMessage = repoMessage
        }
    }

    // Back handler when on non-home tab and no overlay is active
    BackHandler(
        enabled = currentTab != MainBottomTab.TIMELINE &&
            activeScatteredFragments == null &&
            activePersonDetail == null &&
            activeEventDetail == null &&
            activeAiNoteEvent == null &&
            !showUnrecognizedVoicesScreen &&
            activeAgentGuideName == null &&
            activeSettingsSubPage == null
    ) {
        currentTab = MainBottomTab.TIMELINE
    }

    EchoesTheme {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(BgDark)
                .windowInsetsPadding(WindowInsets.statusBars)
        ) {
            // Primary 4-tab scaffold
            Scaffold(
                modifier = Modifier.fillMaxSize(),
                containerColor = BgDark,
                topBar = {
                    TopListeningBar(
                        isListening = recordingState.recording,
                        onToggleListening = {
                            if (recordingState.recording) {
                                onStop()
                                toastMessage = "已暂停聆听"
                            } else {
                                onRecord(false)
                                toastMessage = "已开启聆听"
                            }
                        },
                        onOpenSettings = {
                            activeSettingsSubPage = SettingsSubPage.MAIN
                        },
                        toastBannerMessage = toastMessage
                    )
                },
                bottomBar = {
                    MemoryBottomNavigationBar(
                        currentTab = currentTab,
                        onSelectTab = { tab ->
                            currentTab = tab
                            isCalendarExpanded = false
                        }
                    )
                }
            ) { innerPadding ->
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding)
                ) {
                    when (currentTab) {
                        MainBottomTab.TIMELINE -> TimelineScreen(
                            selectedDay = selectedDay,
                            onSelectDay = { dayNum ->
                                selectedDay = dayNum
                                if (dayNum == 2 || dayNum == 3 || dayNum == LocalDate.now().dayOfMonth) {
                                    toastMessage = "已是最新的"
                                }
                            },
                            isCalendarExpanded = isCalendarExpanded,
                            onToggleCalendarExpanded = { isCalendarExpanded = !isCalendarExpanded },
                            events = events,
                            onEventClick = { activeEventDetail = it },
                            onScatteredFragmentsClick = { fragments ->
                                activeScatteredFragments = fragments
                            },
                            onUpgradeClick = { activeSettingsSubPage = SettingsSubPage.SUBSCRIPTION },
                            onQuickAiClick = {
                                val firstForDay = events.firstOrNull { it.dayNumber == selectedDay }
                                    ?: events.firstOrNull()
                                if (firstForDay != null) {
                                    activeAiNoteEvent = firstForDay
                                } else {
                                    toastMessage = "暂无今日事件可整理"
                                }
                            }
                        )

                        MainBottomTab.ACTIVITIES -> ActivitiesScreen(
                            events = events,
                            onEventClick = { activeEventDetail = it }
                        )

                        MainBottomTab.PEOPLE -> PeopleScreen(
                            people = people,
                            unrecognizedCount = unrecognizedClips.size,
                            onOpenUnrecognizedVoices = { showUnrecognizedVoicesScreen = true },
                            onPersonClick = { person ->
                                activePersonDetail = person
                            }
                        )

                        MainBottomTab.AGENT -> AgentScreen(
                            onOpenAgentGuide = { agentName ->
                                activeAgentGuideName = agentName
                            }
                        )
                    }
                }
            }

            // 1. Scattered Dialogues List Screen ("4 段零散对话")
            if (activeScatteredFragments != null) {
                ScatteredDialoguesListScreen(
                    fragments = activeScatteredFragments!!,
                    onBack = { activeScatteredFragments = null },
                    onFragmentClick = { frag ->
                        activeEventDetail = frag.toMemoryEvent(selectedDay)
                    }
                )
            }

            // 2. Person Detail Screen
            if (activePersonDetail != null) {
                PersonDetailScreen(
                    person = activePersonDetail!!,
                    events = events,
                    onBack = { activePersonDetail = null },
                    onEventClick = { ev -> activeEventDetail = ev },
                    onRenamePerson = { oldName, newName ->
                        people = people.map {
                            if (it.name == oldName) it.copy(name = newName) else it
                        }
                        events = events.map { ev ->
                            ev.copy(
                                speakers = ev.speakers.map { if (it == oldName) newName else it },
                                transcripts = ev.transcripts.map {
                                    if (it.speaker == oldName) it.copy(speaker = newName) else it
                                }
                            )
                        }
                        activePersonDetail = activePersonDetail!!.copy(name = newName)
                        vm.renameSpeaker(oldName, newName)
                        toastMessage = "已重命名为「$newName」"
                    },
                    onDeletePerson = { name ->
                        people = people.filterNot { it.name == name }
                        vm.deleteSpeaker(name)
                        toastMessage = "已删除人物「$name」"
                    }
                )
            }

            // 3. Event Detail Screen
            if (activeEventDetail != null) {
                EventDetailScreen(
                    event = activeEventDetail!!,
                    knownPeople = people,
                    onBack = { activeEventDetail = null },
                    onOpenAiNote = { ev -> activeAiNoteEvent = ev },
                    onSwitchToAgentTab = {
                        activeEventDetail = null
                        activeScatteredFragments = null
                        activePersonDetail = null
                        currentTab = MainBottomTab.AGENT
                    },
                    onOpenUnrecognizedVoices = {
                        showUnrecognizedVoicesScreen = true
                    },
                    onReassignSpeaker = { oldName, newName ->
                        val updatedEvent = activeEventDetail!!.copy(
                            speakers = activeEventDetail!!.speakers.map { if (it == oldName) newName else it },
                            transcripts = activeEventDetail!!.transcripts.map {
                                if (it.speaker == oldName) it.copy(speaker = newName) else it
                            }
                        )
                        activeEventDetail = updatedEvent
                        events = events.map { if (it.id == updatedEvent.id) updatedEvent else it }
                        if (people.none { it.name == newName }) {
                            people = listOf(PersonVoiceProfile(newName, 1)) + people
                            vm.addSpeaker(newName)
                            toastMessage = "已创建新人物「$newName」"
                        } else {
                            toastMessage = "已标记为「$newName」"
                        }
                    }
                )
            }

            // 4. AI Note Chat Screen
            if (activeAiNoteEvent != null) {
                AiNoteChatScreen(
                    event = activeAiNoteEvent!!,
                    onBack = { activeAiNoteEvent = null }
                )
            }

            // 5. Unrecognized Voices Screen
            if (showUnrecognizedVoicesScreen) {
                UnrecognizedVoicesScreen(
                    clips = unrecognizedClips,
                    knownPeople = people,
                    onBack = { showUnrecognizedVoicesScreen = false },
                    onAssignClip = { clip, personName ->
                        unrecognizedClips = unrecognizedClips.filterNot { it.id == clip.id }
                        val existing = people.find { it.name == personName }
                        if (existing != null) {
                            people = people.map {
                                if (it.name == personName) it.copy(dialogueCount = it.dialogueCount + 1)
                                else it
                            }
                            toastMessage = "已标记为「$personName」"
                        } else {
                            people = listOf(PersonVoiceProfile(personName, 1)) + people
                            vm.addSpeaker(personName)
                            toastMessage = "已创建新人物「$personName」"
                        }
                    },
                    onIgnoreClip = { clip ->
                        unrecognizedClips = unrecognizedClips.filterNot { it.id == clip.id }
                    }
                )
            }

            // 6. Agent Integration Guide Screen
            if (activeAgentGuideName != null) {
                AgentIntegrationSheetScreen(
                    agentName = activeAgentGuideName!!,
                    onClose = { activeAgentGuideName = null },
                    onCopyPrompt = {
                        toastMessage = "已复制 MCP 接入提示词"
                        activeAgentGuideName = null
                    }
                )
            }

            // 7. Settings Overlay Screen
            if (activeSettingsSubPage != null) {
                SettingsOverlayScreen(
                    vm = vm,
                    initialSubPage = activeSettingsSubPage!!,
                    onClose = { activeSettingsSubPage = null }
                )
            }
        }
    }
}

@Composable
private fun MemoryBottomNavigationBar(
    currentTab: MainBottomTab,
    onSelectTab: (MainBottomTab) -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(BgDark)
            .navigationBarsPadding()
    ) {
        HorizontalDivider(color = DividerDark, thickness = 1.dp)
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(64.dp),
            horizontalArrangement = Arrangement.SpaceAround,
            verticalAlignment = Alignment.CenterVertically
        ) {
            MainBottomTab.entries.forEach { tab ->
                val selected = tab == currentTab
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxHeight()
                        .clickable { onSelectTab(tab) }
                        .testTag(tab.tag),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center
                ) {
                    Icon(
                        imageVector = tab.icon,
                        contentDescription = tab.label,
                        tint = if (selected) PrimaryNeonGreen else TextSecondary,
                        modifier = Modifier.size(24.dp)
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = tab.label,
                        color = if (selected) PrimaryNeonGreen else TextSecondary,
                        fontSize = 11.sp,
                        fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal
                    )
                }
            }
        }
    }
}
