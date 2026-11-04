@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
package dev.echoledger.app.ui.components

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.PowerManager
import androidx.core.content.ContextCompat
import androidx.activity.compose.BackHandler
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
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.echoledger.app.BuildConfig
import dev.echoledger.app.ui.AppViewModel
import dev.echoledger.app.ui.theme.*
import dev.echoledger.app.update.UpdateRelease
import dev.echoledger.app.update.UpdateState
import org.json.JSONObject

enum class SettingsSubPage {
    MAIN,
    AI_TRANSCRIPTION,
    VAD_SETTINGS,
    VOICEPRINT_SETTINGS,
    AUTO_LISTEN,
    STORAGE_AND_UPDATE
}

@Composable
fun SettingsOverlayScreen(
    vm: AppViewModel,
    initialSubPage: SettingsSubPage = SettingsSubPage.MAIN,
    onRecord: (Boolean) -> Unit = {},
    onClose: () -> Unit
) {
    var currentSubPage by remember(initialSubPage) { mutableStateOf(initialSubPage) }

    BackHandler {
        if (currentSubPage != SettingsSubPage.MAIN) {
            currentSubPage = SettingsSubPage.MAIN
        } else {
            onClose()
        }
    }

    when (currentSubPage) {
        SettingsSubPage.MAIN -> SettingsMainPage(
            vm = vm,
            onClose = onClose,
            onNavigate = { currentSubPage = it }
        )
        SettingsSubPage.AI_TRANSCRIPTION -> AiTranscriptionSettingsPage(
            vm = vm,
            onBack = { currentSubPage = SettingsSubPage.MAIN }
        )
        SettingsSubPage.VAD_SETTINGS -> VadSettingsPage(
            vm = vm,
            onRecord = onRecord,
            onBack = { currentSubPage = SettingsSubPage.MAIN }
        )
        SettingsSubPage.VOICEPRINT_SETTINGS -> VoiceprintSettingsPage(
            vm = vm,
            onBack = { currentSubPage = SettingsSubPage.MAIN }
        )
        SettingsSubPage.AUTO_LISTEN -> AutoListeningPage(
            vm = vm,
            onBack = { currentSubPage = SettingsSubPage.MAIN }
        )
        SettingsSubPage.STORAGE_AND_UPDATE -> StorageAndUpdatePage(
            vm = vm,
            onBack = { currentSubPage = SettingsSubPage.MAIN }
        )
    }
}

@Composable
private fun SettingsMainPage(
    vm: AppViewModel,
    onClose: () -> Unit,
    onNavigate: (SettingsSubPage) -> Unit
) {
    val currentMode by vm.operationMode.collectAsStateWithLifecycle()
    val vadEngine by vm.vadProvider.collectAsStateWithLifecycle()
    val voiceprintOn by vm.voiceprintEnabled.collectAsStateWithLifecycle()
    val pendingCount by vm.pending.collectAsStateWithLifecycle()

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
                text = "设置",
                color = TextPrimary,
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.align(Alignment.Center)
            )
            IconButton(
                onClick = onClose,
                modifier = Modifier
                    .align(Alignment.CenterEnd)
                    .size(36.dp)
                    .testTag("close_settings_button")
            ) {
                Icon(
                    imageVector = Icons.Default.Close,
                    contentDescription = "关闭设置",
                    tint = TextPrimary
                )
            }
        }

        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            // Section: 系统核心设置 (5 项实用功能)
            item {
                SettingsSectionTitle("系统设置")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .border(1.dp, SurfaceCardGreenBorder, RoundedCornerShape(14.dp))
                ) {
                    SettingsRowItem(
                        icon = Icons.Outlined.AutoAwesome,
                        title = "AI 转录与运行模式",
                        trailingValue = when (currentMode) {
                            "server" -> "专用服务端"
                            "hybrid" -> "混合模式"
                            else -> "端侧独立"
                        },
                        onClick = { onNavigate(SettingsSubPage.AI_TRANSCRIPTION) },
                        testTag = "settings_ai_transcription_row"
                    )
                    HorizontalDivider(color = DividerDark)
                    SettingsRowItem(
                        icon = Icons.Outlined.GraphicEq,
                        title = "VAD 语音活动检测",
                        trailingValue = vadEngine,
                        onClick = { onNavigate(SettingsSubPage.VAD_SETTINGS) },
                        testTag = "settings_vad_row"
                    )
                    HorizontalDivider(color = DividerDark)
                    SettingsRowItem(
                        icon = Icons.Outlined.RecordVoiceOver,
                        title = "声纹识别与说话人模型",
                        trailingValue = if (voiceprintOn) "已开启" else "未启用",
                        onClick = { onNavigate(SettingsSubPage.VOICEPRINT_SETTINGS) },
                        testTag = "settings_voiceprint_row"
                    )
                    HorizontalDivider(color = DividerDark)
                    SettingsRowItem(
                        icon = Icons.Outlined.MicNone,
                        title = "聆听权限与后台自检",
                        onClick = { onNavigate(SettingsSubPage.AUTO_LISTEN) },
                        testTag = "settings_auto_listen_row"
                    )
                    HorizontalDivider(color = DividerDark)
                    SettingsRowItem(
                        icon = Icons.Outlined.SystemUpdate,
                        title = "存储管理与应用更新",
                        trailingValue = "v${BuildConfig.VERSION_NAME}${if (pendingCount > 0) " · 待上传 $pendingCount" else ""}",
                        onClick = { onNavigate(SettingsSubPage.STORAGE_AND_UPDATE) },
                        testTag = "settings_storage_update_row"
                    )
                }
                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }
}

@Composable
private fun SettingsSectionTitle(title: String) {
    Text(
        text = title,
        color = TextSecondary,
        fontSize = 13.sp,
        fontWeight = FontWeight.Medium,
        modifier = Modifier.padding(start = 4.dp)
    )
}

@Composable
private fun SettingsRowItem(
    icon: ImageVector,
    title: String,
    trailingValue: String? = null,
    onClick: () -> Unit,
    testTag: String? = null
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onClick() }
            .padding(horizontal = 16.dp, vertical = 15.dp)
            .then(if (testTag != null) Modifier.testTag(testTag) else Modifier),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = PrimaryNeonGreen,
            modifier = Modifier.size(20.dp)
        )
        Spacer(modifier = Modifier.width(14.dp))
        Text(
            text = title,
            color = TextPrimary,
            fontSize = 16.sp,
            modifier = Modifier.weight(1f)
        )
        if (trailingValue != null) {
            Text(
                text = trailingValue,
                color = TextSecondary,
                fontSize = 14.sp
            )
            Spacer(modifier = Modifier.width(6.dp))
        }
        Icon(
            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
            contentDescription = null,
            tint = TextMuted,
            modifier = Modifier.size(18.dp)
        )
    }
}

// -------------------------------------------------------------
// Subpage 1: AI 转录与运行模式
// -------------------------------------------------------------
@Composable
private fun AiTranscriptionSettingsPage(
    vm: AppViewModel,
    onBack: () -> Unit
) {
    val currentMode by vm.operationMode.collectAsStateWithLifecycle()
    val currentProvider by vm.aiProvider.collectAsStateWithLifecycle()
    val currentApiKey by vm.aiApiKey.collectAsStateWithLifecycle()
    val currentModel by vm.aiModelName.collectAsStateWithLifecycle()
    val currentProjectId by vm.vertexProjectId.collectAsStateWithLifecycle()
    val currentLocation by vm.vertexLocation.collectAsStateWithLifecycle()
    val currentEndpoint by vm.vertexEndpoint.collectAsStateWithLifecycle()
    val currentBaseUrl by vm.serverBaseUrl.collectAsStateWithLifecycle()
    val currentServerToken by vm.serverToken.collectAsStateWithLifecycle()
    val currentMcpToken by vm.mcpToken.collectAsStateWithLifecycle()
    val currentTxMode by vm.transcriptionMode.collectAsStateWithLifecycle()

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

    var baseUrl by rememberSaveable { mutableStateOf(currentBaseUrl) }
    var serverToken by rememberSaveable { mutableStateOf(currentServerToken) }
    var mcpToken by rememberSaveable { mutableStateOf(currentMcpToken) }
    var txMode by rememberSaveable { mutableStateOf(currentTxMode) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        SubPageTopBar(title = "AI 转录与运行模式", onBack = onBack)

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            // 运行模式胶囊
            item {
                SettingsSectionTitle("系统运行架构")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    val modes = listOf(
                        "standalone" to "端侧独立 (零依赖)",
                        "server" to "专用服务端 (Server)",
                        "hybrid" to "混合模式 (Hybrid)"
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        modes.forEach { (m, label) ->
                            val selected = mode == m
                            Surface(
                                shape = RoundedCornerShape(8.dp),
                                color = if (selected) TagBg else Color(0xFF1E2124),
                                border = androidx.compose.foundation.BorderStroke(
                                    1.dp,
                                    if (selected) PrimaryNeonGreen else DividerDark
                                ),
                                modifier = Modifier
                                    .weight(1f)
                                    .clickable { mode = m }
                            ) {
                                Text(
                                    text = label,
                                    color = if (selected) PrimaryNeonGreen else TextSecondary,
                                    fontSize = 12.sp,
                                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                    modifier = Modifier.padding(vertical = 10.dp, horizontal = 6.dp),
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                            }
                        }
                    }
                    Text(
                        text = when (mode) {
                            "standalone" -> "端侧独立：音频录制、VAD 切分与 AI 调用全部在设备本地完成，无需依赖私有自建服务器。"
                            "server" -> "专用服务端：音频流同步推送至私有云，依托后端高性能显卡与声纹库集中归档。"
                            else -> "混合模式：端侧实时本地缓存与转写，网络可用时自动与服务端双向对齐同步。"
                        },
                        color = TextMuted,
                        fontSize = 12.sp,
                        lineHeight = 18.sp
                    )
                }
            }

            // 服务端连接配置 (在 server 或 hybrid 时高亮展示)
            if (mode != "standalone") {
                item {
                    SettingsSectionTitle("专用服务端连接")
                    Spacer(modifier = Modifier.height(8.dp))
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(14.dp))
                            .background(SurfaceDark)
                            .padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        OutlinedTextField(
                            value = baseUrl,
                            onValueChange = { baseUrl = it },
                            label = { Text("Base URL (如 https://echoes.internal)") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        OutlinedTextField(
                            value = serverToken,
                            onValueChange = { serverToken = it },
                            label = { Text("后端 API Token") },
                            singleLine = true,
                            visualTransformation = PasswordVisualTransformation(),
                            modifier = Modifier.fillMaxWidth()
                        )
                        OutlinedTextField(
                            value = mcpToken,
                            onValueChange = { mcpToken = it },
                            label = { Text("MCP Token (Agent 互联通信秘钥)") },
                            singleLine = true,
                            visualTransformation = PasswordVisualTransformation(),
                            modifier = Modifier.fillMaxWidth()
                        )
                        Button(
                            onClick = {
                                vm.saveServerConfig(baseUrl, serverToken, mcpToken)
                            },
                            colors = ButtonDefaults.buttonColors(containerColor = SurfaceCardElevated),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Icon(Icons.Outlined.Sync, null, modifier = Modifier.size(16.dp), tint = PrimaryNeonGreen)
                            Spacer(Modifier.width(6.dp))
                            Text("同步服务端状态与配置", color = PrimaryNeonGreen, fontSize = 13.sp)
                        }
                    }
                }
            }

            // AI 服务商选择
            item {
                SettingsSectionTitle("端侧 AI 引擎与模型")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)
                ) {
                    Text("AI 服务商", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        listOf(
                            "gemini_studio" to "Google AI Studio",
                            "vertex_ai" to "GCP Vertex AI"
                        ).forEach { (p, label) ->
                            val selected = provider == p
                            Surface(
                                shape = RoundedCornerShape(8.dp),
                                color = if (selected) TagBg else Color(0xFF1E2124),
                                border = androidx.compose.foundation.BorderStroke(
                                    1.dp,
                                    if (selected) PrimaryNeonGreen else DividerDark
                                ),
                                modifier = Modifier
                                    .weight(1f)
                                    .clickable { provider = p }
                            ) {
                                Text(
                                    text = label,
                                    color = if (selected) PrimaryNeonGreen else TextSecondary,
                                    fontSize = 13.sp,
                                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                    modifier = Modifier.padding(vertical = 10.dp, horizontal = 12.dp)
                                )
                            }
                        }
                    }

                    // 转写模型 Chips
                    Text("转写模型", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro").forEach { m ->
                            val selected = modelName == m
                            Surface(
                                shape = RoundedCornerShape(6.dp),
                                color = if (selected) TagBg else Color(0xFF1E2124),
                                border = androidx.compose.foundation.BorderStroke(1.dp, if (selected) PrimaryNeonGreen else DividerDark),
                                modifier = Modifier.clickable { modelName = m }
                            ) {
                                Text(
                                    text = m,
                                    color = if (selected) PrimaryNeonGreen else TextSecondary,
                                    fontSize = 12.sp,
                                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                                )
                            }
                        }
                    }

                    OutlinedTextField(
                        value = modelName,
                        onValueChange = { modelName = it },
                        label = { Text("自定义模型标识") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    // 输出模式 Chips
                    Text("转写输出模式", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        listOf(
                            "structured" to "结构化事件与分段",
                            "transcribe" to "逐词时间戳"
                        ).forEach { (t, label) ->
                            val selected = txMode == t
                            Surface(
                                shape = RoundedCornerShape(8.dp),
                                color = if (selected) TagBg else Color(0xFF1E2124),
                                border = androidx.compose.foundation.BorderStroke(1.dp, if (selected) PrimaryNeonGreen else DividerDark),
                                modifier = Modifier.weight(1f).clickable { txMode = t }
                            ) {
                                Text(
                                    text = label,
                                    color = if (selected) PrimaryNeonGreen else TextSecondary,
                                    fontSize = 13.sp,
                                    modifier = Modifier.padding(vertical = 8.dp, horizontal = 8.dp)
                                )
                            }
                        }
                    }

                    // API Key
                    OutlinedTextField(
                        value = apiKey,
                        onValueChange = { apiKey = it },
                        label = { Text("API Key") },
                        singleLine = true,
                        visualTransformation = if (showApiKey) VisualTransformation.None else PasswordVisualTransformation(),
                        trailingIcon = {
                            IconButton(onClick = { showApiKey = !showApiKey }) {
                                Icon(
                                    if (showApiKey) Icons.Outlined.VisibilityOff else Icons.Outlined.Visibility,
                                    contentDescription = null,
                                    tint = TextSecondary
                                )
                            }
                        },
                        modifier = Modifier.fillMaxWidth()
                    )
                    Text(
                        text = "🔒 AndroidKeyStore AES-256-GCM 硬件加密安全持久化",
                        color = PrimaryNeonGreen.copy(alpha = 0.8f),
                        fontSize = 11.sp
                    )

                    // Vertex AI 专属配置
                    if (provider == "vertex_ai") {
                        HorizontalDivider(color = DividerDark)
                        Text("Vertex AI 专属参数", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                        OutlinedTextField(
                            value = projectId,
                            onValueChange = { projectId = it },
                            label = { Text("GCP Project ID") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        OutlinedTextField(
                            value = location,
                            onValueChange = { location = it },
                            label = { Text("Region (如 global, us-central1)") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        OutlinedTextField(
                            value = endpoint,
                            onValueChange = { endpoint = it },
                            label = { Text("Endpoint Override (可选)") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }

                    // 测试连通性按钮与结果反馈
                    Button(
                        onClick = {
                            vm.testAiConnection(
                                provider = provider,
                                key = apiKey,
                                model = modelName,
                                projectId = projectId,
                                location = location,
                                endpoint = endpoint
                            )
                        },
                        colors = ButtonDefaults.buttonColors(containerColor = SurfaceCardElevated),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        if (aiTesting) {
                            CircularProgressIndicator(modifier = Modifier.size(16.dp), color = PrimaryNeonGreen, strokeWidth = 2.dp)
                            Spacer(Modifier.width(8.dp))
                            Text("正在测试 AI 连通性...", color = PrimaryNeonGreen, fontSize = 13.sp)
                        } else {
                            Icon(Icons.Outlined.CheckCircle, null, modifier = Modifier.size(16.dp), tint = PrimaryNeonGreen)
                            Spacer(Modifier.width(6.dp))
                            Text("测试 AI 连通性", color = PrimaryNeonGreen, fontSize = 13.sp)
                        }
                    }

                    if (aiTestResult != null) {
                        Surface(
                            shape = RoundedCornerShape(8.dp),
                            color = if (aiTestSuccess == true) Color(0xFF162A1B) else Color(0xFF331A1A),
                            border = androidx.compose.foundation.BorderStroke(
                                1.dp,
                                if (aiTestSuccess == true) PrimaryNeonGreen else DangerRed
                            ),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(
                                text = aiTestResult ?: "",
                                color = if (aiTestSuccess == true) PrimaryNeonGreen else DangerRed,
                                fontSize = 12.sp,
                                modifier = Modifier.padding(12.dp)
                            )
                        }
                    }
                }
            }
        }

        // Bottom CTA Button: 保存全部 AI 转录配置
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
                .navigationBarsPadding(),
            color = Color.Transparent
        ) {
            Button(
                onClick = {
                    vm.saveAiConfig(
                        mode = mode,
                        provider = provider,
                        key = apiKey,
                        model = modelName,
                        projectId = projectId,
                        location = location,
                        endpoint = endpoint
                    )
                    vm.saveTranscriptionMode(txMode)
                    if (mode != "standalone") {
                        vm.saveServerConfig(baseUrl, serverToken, mcpToken)
                    }
                    onBack()
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp)
                    .testTag("save_ai_config_button"),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = PrimaryNeonGreen,
                    contentColor = Color.Black
                )
            ) {
                Text("保存 AI 转录配置", fontSize = 16.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

// -------------------------------------------------------------
// Subpage 2: VAD 语音活动检测设置
// -------------------------------------------------------------
@Composable
private fun VadSettingsPage(
    vm: AppViewModel,
    onRecord: (Boolean) -> Unit = {},
    onBack: () -> Unit
) {
    val currentProvider by vm.vadProvider.collectAsStateWithLifecycle()
    val currentThreshold by vm.vadThreshold.collectAsStateWithLifecycle()
    val currentEndThreshold by vm.vadEndThreshold.collectAsStateWithLifecycle()
    val currentMinSpeech by vm.vadMinSpeechMs.collectAsStateWithLifecycle()
    val currentMinSilence by vm.vadMinSilenceMs.collectAsStateWithLifecycle()
    val currentPad by vm.vadPadMs.collectAsStateWithLifecycle()
    val currentMaxSegment by vm.vadMaxSegmentMs.collectAsStateWithLifecycle()
    val currentUncertain by vm.vadUncertainThreshold.collectAsStateWithLifecycle()
    val currentRmsGate by vm.vadRmsGate.collectAsStateWithLifecycle()
    val profiles by vm.profiles.collectAsStateWithLifecycle()
    val evaluation by vm.evaluation.collectAsStateWithLifecycle()

    var provider by rememberSaveable { mutableStateOf(currentProvider) }
    var threshold by rememberSaveable { mutableFloatStateOf(currentThreshold) }
    var endThreshold by rememberSaveable { mutableFloatStateOf(currentEndThreshold) }
    var minSpeech by rememberSaveable { mutableIntStateOf(currentMinSpeech) }
    var minSilence by rememberSaveable { mutableIntStateOf(currentMinSilence) }
    var padMs by rememberSaveable { mutableIntStateOf(currentPad) }
    var maxSegment by rememberSaveable { mutableIntStateOf(currentMaxSegment) }
    var uncertain by rememberSaveable { mutableFloatStateOf(currentUncertain) }
    var rmsGate by rememberSaveable { mutableFloatStateOf(currentRmsGate) }
    var presetName by rememberSaveable { mutableStateOf("") }

    var calScenario by rememberSaveable { mutableStateOf("quiet") }
    var calIntervals by rememberSaveable { mutableStateOf("5-15") }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        SubPageTopBar(title = "VAD 语音活动检测", onBack = onBack)

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            // VAD 引擎选择
            item {
                SettingsSectionTitle("检测引擎选择")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    val engines = listOf(
                        "silero_v5", "silero_v4", "ten_vad", "ten_vad_int8",
                        "webrtc_3", "energy_demo", "passthrough"
                    )
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        engines.forEach { eng ->
                            val selected = provider == eng
                            Surface(
                                shape = RoundedCornerShape(8.dp),
                                color = if (selected) TagBg else Color(0xFF1E2124),
                                border = androidx.compose.foundation.BorderStroke(1.dp, if (selected) PrimaryNeonGreen else DividerDark),
                                modifier = Modifier.clickable { provider = eng }
                            ) {
                                Text(
                                    text = eng,
                                    color = if (selected) PrimaryNeonGreen else TextSecondary,
                                    fontSize = 13.sp,
                                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp)
                                )
                            }
                        }
                    }
                }
            }

            // VAD 预设库 (Profiles)
            if (profiles.isNotEmpty()) {
                item {
                    SettingsSectionTitle("服务端预设同步")
                    Spacer(modifier = Modifier.height(8.dp))
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(14.dp))
                            .background(SurfaceDark)
                            .padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text("点击一键载入云端预设参数：", color = TextSecondary, fontSize = 13.sp)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            profiles.forEach { prof ->
                                val name = prof.optString("name", "profile")
                                Surface(
                                    shape = RoundedCornerShape(6.dp),
                                    color = Color(0xFF263029),
                                    modifier = Modifier.clickable {
                                        presetName = name
                                        val cfg = prof.optJSONObject("config")
                                        if (cfg != null) {
                                            threshold = cfg.optDouble("threshold", threshold.toDouble()).toFloat()
                                            endThreshold = cfg.optDouble("end_threshold", endThreshold.toDouble()).toFloat()
                                            minSpeech = cfg.optInt("min_speech_ms", minSpeech)
                                            minSilence = cfg.optInt("min_silence_ms", minSilence)
                                            padMs = cfg.optInt("pad_ms", padMs)
                                            maxSegment = cfg.optInt("max_segment_ms", maxSegment)
                                            rmsGate = cfg.optDouble("rms_gate", rmsGate.toDouble()).toFloat()
                                        }
                                    }
                                ) {
                                    Text(
                                        text = name,
                                        color = PrimaryNeonGreen,
                                        fontSize = 12.sp,
                                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                                    )
                                }
                            }
                        }
                    }
                }
            }

            // 参数表单
            item {
                SettingsSectionTitle("切分与灵敏度参数表单")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)
                ) {
                    // start_threshold
                    ParamSliderRow(
                        title = "触发阈值 (start_threshold)",
                        valueText = "%.2f".format(threshold),
                        value = threshold,
                        range = 0.1f..0.9f,
                        onValueChange = { threshold = it }
                    )
                    // end_threshold
                    ParamSliderRow(
                        title = "结束迟滞 (end_threshold)",
                        valueText = "%.2f".format(endThreshold),
                        value = endThreshold,
                        range = 0.1f..0.8f,
                        onValueChange = { endThreshold = it }
                    )
                    // rms_gate
                    ParamSliderRow(
                        title = "RMS 能量门限 (低于此值跳过请求)",
                        valueText = "%.4f".format(rmsGate),
                        value = rmsGate,
                        range = 0.0005f..0.015f,
                        onValueChange = { rmsGate = it }
                    )
                    // min_speech_ms
                    ParamIntRow(
                        title = "最短语音时长 (min_speech_ms)",
                        value = minSpeech,
                        unit = "ms",
                        onValueChange = { minSpeech = it }
                    )
                    // min_silence_ms
                    ParamIntRow(
                        title = "静音切分时长 (min_silence_ms)",
                        value = minSilence,
                        unit = "ms",
                        onValueChange = { minSilence = it }
                    )
                    // pad_ms
                    ParamIntRow(
                        title = "前后音频填充 (pad_ms)",
                        value = padMs,
                        unit = "ms",
                        onValueChange = { padMs = it }
                    )
                    // max_segment_ms
                    ParamIntRow(
                        title = "最大单段时长 (max_segment_ms)",
                        value = maxSegment,
                        unit = "ms",
                        onValueChange = { maxSegment = it }
                    )

                    OutlinedTextField(
                        value = presetName,
                        onValueChange = { presetName = it },
                        label = { Text("保存预设名称 (可选，如 classroom_meeting)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            }

            // 20秒场景校准试算卡片
            item {
                SettingsSectionTitle("20 秒场景校准试算")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Text("场景类别：", color = TextSecondary, fontSize = 13.sp)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("quiet" to "安静", "meeting" to "会议", "noisy" to "嘈杂").forEach { (sc, label) ->
                            val selected = calScenario == sc
                            Surface(
                                shape = RoundedCornerShape(6.dp),
                                color = if (selected) TagBg else Color(0xFF1E2124),
                                border = androidx.compose.foundation.BorderStroke(1.dp, if (selected) PrimaryNeonGreen else DividerDark),
                                modifier = Modifier.weight(1f).clickable { calScenario = sc }
                            ) {
                                Text(
                                    text = label,
                                    color = if (selected) PrimaryNeonGreen else TextSecondary,
                                    fontSize = 12.sp,
                                    modifier = Modifier.padding(vertical = 8.dp, horizontal = 10.dp)
                                )
                            }
                        }
                    }

                    OutlinedTextField(
                        value = calIntervals,
                        onValueChange = { calIntervals = it },
                        label = { Text("标注语音区间 (秒，如 5-15 或 2-6;10-18)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(
                            onClick = { onRecord(true) },
                            colors = ButtonDefaults.buttonColors(containerColor = SurfaceCardElevated),
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Outlined.Mic, null, modifier = Modifier.size(16.dp), tint = PrimaryNeonGreen)
                            Spacer(Modifier.width(4.dp))
                            Text("录制采样", color = PrimaryNeonGreen, fontSize = 12.sp)
                        }
                        Button(
                            onClick = { vm.sweepVad(calScenario, calIntervals) },
                            colors = ButtonDefaults.buttonColors(containerColor = SurfaceCardElevated),
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Outlined.Analytics, null, modifier = Modifier.size(16.dp), tint = PrimaryNeonGreen)
                            Spacer(Modifier.width(4.dp))
                            Text("校准试算", color = PrimaryNeonGreen, fontSize = 12.sp)
                        }
                    }

                    if (evaluation != null) {
                        Surface(
                            shape = RoundedCornerShape(8.dp),
                            color = SurfaceCardGreen,
                            border = androidx.compose.foundation.BorderStroke(1.dp, SurfaceCardGreenBorder),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text("校准试算结果：", color = PrimaryNeonGreen, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                                Text("最佳得分：${evaluation!!.optDouble("best_score", 0.0)}", color = TextPrimary, fontSize = 12.sp)
                                Text("详细分析：${evaluation!!.toString()}", color = TextSecondary, fontSize = 11.sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
            }
        }

        // Bottom CTA Button: 保存并激活 VAD 配置
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
                .navigationBarsPadding(),
            color = Color.Transparent
        ) {
            Button(
                onClick = {
                    vm.saveVadConfig(
                        provider = provider,
                        threshold = threshold,
                        endThreshold = endThreshold,
                        minSpeechMs = minSpeech,
                        minSilenceMs = minSilence,
                        padMs = padMs,
                        maxSegmentMs = maxSegment,
                        uncertainThreshold = uncertain,
                        rmsGate = rmsGate,
                        presetName = presetName
                    )
                    onBack()
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp)
                    .testTag("save_vad_config_button"),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = PrimaryNeonGreen,
                    contentColor = Color.Black
                )
            ) {
                Text("保存并激活 VAD 配置", fontSize = 16.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun ParamSliderRow(
    title: String,
    valueText: String,
    value: Float,
    range: ClosedFloatingPointRange<Float>,
    onValueChange: (Float) -> Unit
) {
    Column {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(title, color = TextPrimary, fontSize = 13.sp)
            Text(valueText, color = PrimaryNeonGreen, fontSize = 14.sp, fontWeight = FontWeight.Bold)
        }
        Slider(
            value = value,
            onValueChange = onValueChange,
            valueRange = range,
            colors = SliderDefaults.colors(
                thumbColor = PrimaryNeonGreen,
                activeTrackColor = PrimaryNeonGreen,
                inactiveTrackColor = DividerDark
            )
        )
    }
}

@Composable
private fun ParamIntRow(
    title: String,
    value: Int,
    unit: String,
    onValueChange: (Int) -> Unit
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(title, color = TextPrimary, fontSize = 13.sp)
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(
                onClick = { onValueChange((value - 50).coerceAtLeast(50)) },
                modifier = Modifier.size(28.dp)
            ) {
                Text("-", color = PrimaryNeonGreen, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            }
            Text("$value $unit", color = PrimaryNeonGreen, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            IconButton(
                onClick = { onValueChange(value + 50) },
                modifier = Modifier.size(28.dp)
            ) {
                Text("+", color = PrimaryNeonGreen, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

// -------------------------------------------------------------
// Subpage 3: 声纹识别设置页
// -------------------------------------------------------------
@Composable
private fun VoiceprintSettingsPage(
    vm: AppViewModel,
    onBack: () -> Unit
) {
    val currentEnabled by vm.voiceprintEnabled.collectAsStateWithLifecycle()
    val currentModel by vm.voiceprintModelId.collectAsStateWithLifecycle()
    val currentThreshold by vm.voiceprintThreshold.collectAsStateWithLifecycle()
    val currentMargin by vm.voiceprintMargin.collectAsStateWithLifecycle()
    val currentMinSpeech by vm.voiceprintMinSpeechMs.collectAsStateWithLifecycle()
    val currentAutoEnroll by vm.voiceprintAutoEnroll.collectAsStateWithLifecycle()
    val currentConsent by vm.voiceprintConsent.collectAsStateWithLifecycle()
    val people by vm.people.collectAsStateWithLifecycle()

    var enabled by rememberSaveable { mutableStateOf(currentEnabled) }
    var modelId by rememberSaveable { mutableStateOf(currentModel) }
    var threshold by rememberSaveable { mutableFloatStateOf(currentThreshold) }
    var margin by rememberSaveable { mutableFloatStateOf(currentMargin) }
    var minSpeech by rememberSaveable { mutableIntStateOf(currentMinSpeech) }
    var autoEnroll by rememberSaveable { mutableStateOf(currentAutoEnroll) }
    var consent by rememberSaveable { mutableStateOf(currentConsent) }
    var newPersonName by rememberSaveable { mutableStateOf("") }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        SubPageTopBar(title = "声纹识别与说话人模型", onBack = onBack)

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            // 总开关
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(horizontal = 16.dp, vertical = 14.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text("声纹自动聚类与说话人识别", color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                        Spacer(modifier = Modifier.height(2.dp))
                        Text("开启后根据声纹特征自动区分不同说话人", color = TextSecondary, fontSize = 12.sp)
                    }
                    Switch(
                        checked = enabled,
                        onCheckedChange = { enabled = it },
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = Color.White,
                            checkedTrackColor = PrimaryNeonGreen
                        )
                    )
                }
            }

            // 模型选择
            item {
                SettingsSectionTitle("特征提取模型")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    val models = listOf("wespeaker_ecapa_tdnn512", "cam++", "3dspeaker_eres2net")
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        models.forEach { m ->
                            val selected = modelId == m
                            Surface(
                                shape = RoundedCornerShape(8.dp),
                                color = if (selected) TagBg else Color(0xFF1E2124),
                                border = androidx.compose.foundation.BorderStroke(1.dp, if (selected) PrimaryNeonGreen else DividerDark),
                                modifier = Modifier.clickable { modelId = m }
                            ) {
                                Text(
                                    text = m,
                                    color = if (selected) PrimaryNeonGreen else TextSecondary,
                                    fontSize = 12.sp,
                                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                                )
                            }
                        }
                    }
                    OutlinedTextField(
                        value = modelId,
                        onValueChange = { modelId = it },
                        label = { Text("自定义声纹模型标识") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            }

            // 匹配参数
            item {
                SettingsSectionTitle("匹配与阈值参数")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)
                ) {
                    ParamSliderRow(
                        title = "余弦相似度匹配阈值 (similarity_threshold)",
                        valueText = "%.2f".format(threshold),
                        value = threshold,
                        range = 0.50f..0.98f,
                        onValueChange = { threshold = it }
                    )
                    ParamSliderRow(
                        title = "置信度判定边际 (margin)",
                        valueText = "%.2f".format(margin),
                        value = margin,
                        range = 0.02f..0.30f,
                        onValueChange = { margin = it }
                    )
                    ParamIntRow(
                        title = "最短有效声纹时长 (min_speech_ms)",
                        value = minSpeech,
                        unit = "ms",
                        onValueChange = { minSpeech = it }
                    )
                }
            }

            // 隐私与自动增量更新
            item {
                SettingsSectionTitle("隐私合规与自动建档")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("声纹采集与建档授权 (Consent)", color = TextPrimary, fontSize = 14.sp)
                            Text("严格遵守本地生物识别数据隐私规范", color = TextMuted, fontSize = 11.sp)
                        }
                        Switch(
                            checked = consent,
                            onCheckedChange = { consent = it },
                            colors = SwitchDefaults.colors(
                                checkedThumbColor = Color.White,
                                checkedTrackColor = PrimaryNeonGreen
                            )
                        )
                    }
                    HorizontalDivider(color = DividerDark)
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("自动增量更新声纹特征库", color = TextPrimary, fontSize = 14.sp)
                            Text("遇到已确认的说话人时自动补充清晰音频特征", color = TextMuted, fontSize = 11.sp)
                        }
                        Switch(
                            checked = autoEnroll,
                            onCheckedChange = { autoEnroll = it },
                            colors = SwitchDefaults.colors(
                                checkedThumbColor = Color.White,
                                checkedTrackColor = PrimaryNeonGreen
                            )
                        )
                    }
                }
            }

            // 声纹库概况与快捷建档
            item {
                SettingsSectionTitle("声纹人物库概览")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Text(
                        text = "已建档人物：${people.size} 位",
                        color = TextPrimary,
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        OutlinedTextField(
                            value = newPersonName,
                            onValueChange = { newPersonName = it },
                            placeholder = { Text("新建人物姓名") },
                            singleLine = true,
                            modifier = Modifier.weight(1f)
                        )
                        Button(
                            onClick = {
                                if (newPersonName.isNotBlank()) {
                                    vm.addSpeaker(newPersonName.trim())
                                    newPersonName = ""
                                }
                            },
                            colors = ButtonDefaults.buttonColors(containerColor = PrimaryNeonGreen)
                        ) {
                            Text("添加", color = Color.Black, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }

        // Bottom CTA Button: 保存声纹配置
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
                .navigationBarsPadding(),
            color = Color.Transparent
        ) {
            Button(
                onClick = {
                    vm.saveVoiceprintConfig(
                        enabled = enabled,
                        modelId = modelId,
                        threshold = threshold,
                        margin = margin,
                        minSpeechMs = minSpeech,
                        autoEnroll = autoEnroll,
                        consent = consent
                    )
                    onBack()
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp)
                    .testTag("save_voiceprint_config_button"),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = PrimaryNeonGreen,
                    contentColor = Color.Black
                )
            ) {
                Text("保存声纹配置", fontSize = 16.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

// -------------------------------------------------------------
// Subpage 4: 存储管理与应用更新 (v0.3.x)
// -------------------------------------------------------------
@Composable
private fun StorageAndUpdatePage(
    vm: AppViewModel,
    onBack: () -> Unit
) {
    val context = LocalContext.current
    val updateState by vm.updateState.collectAsStateWithLifecycle()
    val sessions by vm.localSessions.collectAsStateWithLifecycle()
    val sessionStats by vm.sessionStats.collectAsStateWithLifecycle()
    val pendingCount by vm.pending.collectAsStateWithLifecycle()
    var showChangelogDialog by remember { mutableStateOf<UpdateRelease?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        SubPageTopBar(title = "存储管理与应用更新", onBack = onBack)

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            // 版本卡片
            item {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .border(1.dp, SurfaceCardGreenBorder, RoundedCornerShape(14.dp))
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text("EchoLedger", color = TextPrimary, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                            Text("当前版本: v${BuildConfig.VERSION_NAME}", color = PrimaryNeonGreen, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                        }
                        Surface(
                            shape = RoundedCornerShape(6.dp),
                            color = TagBg,
                            border = androidx.compose.foundation.BorderStroke(1.dp, SurfaceCardGreenBorder)
                        ) {
                            Text(
                                text = "GitHub Channel",
                                color = PrimaryNeonGreen,
                                fontSize = 11.sp,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                            )
                        }
                    }

                    HorizontalDivider(color = DividerDark)

                    // 绑定 updateState
                    when (val state = updateState) {
                        is UpdateState.Idle -> {
                            Button(
                                onClick = { vm.checkForUpdates(silent = false) },
                                colors = ButtonDefaults.buttonColors(containerColor = SurfaceCardElevated),
                                modifier = Modifier.fillMaxWidth().testTag("check_update_button")
                            ) {
                                Icon(Icons.Outlined.Refresh, null, modifier = Modifier.size(16.dp), tint = PrimaryNeonGreen)
                                Spacer(Modifier.width(6.dp))
                                Text("检查新版本", color = PrimaryNeonGreen, fontSize = 14.sp)
                            }
                        }
                        is UpdateState.Checking -> {
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
                                horizontalArrangement = Arrangement.Center,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                CircularProgressIndicator(modifier = Modifier.size(18.dp), color = PrimaryNeonGreen, strokeWidth = 2.dp)
                                Spacer(Modifier.width(10.dp))
                                Text("正在连接 GitHub Releases 检查...", color = TextSecondary, fontSize = 14.sp)
                            }
                        }
                        is UpdateState.UpToDate -> {
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Icon(Icons.Default.Check, null, tint = PrimaryNeonGreen, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("当前已是最新版本 (v${state.currentVersion})", color = TextPrimary, fontSize = 14.sp)
                            }
                        }
                        is UpdateState.UpdateAvailable -> {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("发现新版本 v${state.release.versionName}！", color = PrimaryNeonGreen, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    OutlinedButton(
                                        onClick = { showChangelogDialog = state.release },
                                        modifier = Modifier.weight(1f)
                                    ) {
                                        Text("更新日志", fontSize = 13.sp)
                                    }
                                    Button(
                                        onClick = { vm.startDownload(state.release) },
                                        colors = ButtonDefaults.buttonColors(containerColor = PrimaryNeonGreen),
                                        modifier = Modifier.weight(1f)
                                    ) {
                                        Text("立即下载", color = Color.Black, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                                    }
                                }
                            }
                        }
                        is UpdateState.Downloading -> {
                            val safePercent = state.percent.coerceIn(0, 100)
                            val downloadedMb = state.bytesRead.toFloat() / (1024f * 1024f)
                            val totalMb = state.totalBytes.toFloat() / (1024f * 1024f)
                            val progressLabel = if (state.totalBytes > 0L) {
                                String.format(java.util.Locale.US, "%.1f MB / %.1f MB (%d%%)", downloadedMb, totalMb, safePercent)
                            } else {
                                String.format(java.util.Locale.US, "%.1f MB (%d%%)", downloadedMb, safePercent)
                            }
                            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween
                                ) {
                                    Text("正在下载更新 APK...", color = TextPrimary, fontSize = 13.sp)
                                    Text(progressLabel, color = PrimaryNeonGreen, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                                }
                                LinearProgressIndicator(
                                    progress = { safePercent / 100f },
                                    modifier = Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(3.dp)),
                                    color = PrimaryNeonGreen,
                                    trackColor = DividerDark
                                )
                                TextButton(
                                    onClick = { vm.cancelDownload() },
                                    modifier = Modifier.align(Alignment.End)
                                ) {
                                    Text("取消下载", color = DangerRed, fontSize = 12.sp)
                                }
                            }
                        }
                        is UpdateState.ReadyToInstall -> {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("APK 下载完成并通过 SHA-256 校验！", color = PrimaryNeonGreen, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                                Button(
                                    onClick = { vm.installUpdate(context, state.apkFile) },
                                    colors = ButtonDefaults.buttonColors(containerColor = PrimaryNeonGreen),
                                    modifier = Modifier.fillMaxWidth().height(44.dp)
                                ) {
                                    Text("立即安装更新", color = Color.Black, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                                }
                            }
                        }
                        is UpdateState.Error -> {
                            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text("更新检查失败: ${state.message}", color = DangerRed, fontSize = 13.sp)
                                TextButton(onClick = { vm.checkForUpdates() }) {
                                    Text("重试", color = PrimaryNeonGreen, fontSize = 13.sp)
                                }
                            }
                        }
                    }
                }
            }

            // 本地录音与存储管理
            item {
                SettingsSectionTitle("本地录音与无损音频存储")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    val totalChunks = sessionStats.values.sumOf { it.chunkCount }
                    val totalSamples = sessionStats.values.sumOf { it.totalSamples }
                    val totalSecs = totalSamples / 16000
                    val durationHours = "%.1f".format(totalSecs / 3600f)

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceAround
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("${sessions.size}", color = TextPrimary, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                            Text("录音会话", color = TextMuted, fontSize = 11.sp)
                        }
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("$totalChunks", color = TextPrimary, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                            Text("PCM 分块", color = TextMuted, fontSize = 11.sp)
                        }
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("$durationHours h", color = PrimaryNeonGreen, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                            Text("总录音时长", color = TextMuted, fontSize = 11.sp)
                        }
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("$pendingCount", color = if (pendingCount > 0) Color(0xFFE5B58C) else TextPrimary, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                            Text("待上传块", color = TextMuted, fontSize = 11.sp)
                        }
                    }

                    HorizontalDivider(color = DividerDark)

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Button(
                            onClick = { vm.saveAllToDownloads() },
                            colors = ButtonDefaults.buttonColors(containerColor = SurfaceCardElevated),
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Outlined.FolderZip, null, modifier = Modifier.size(16.dp), tint = PrimaryNeonGreen)
                            Spacer(Modifier.width(6.dp))
                            Text("导出全部 ZIP", color = PrimaryNeonGreen, fontSize = 13.sp)
                        }
                        Button(
                            onClick = { vm.refresh() },
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF222824)),
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Outlined.Sync, null, modifier = Modifier.size(16.dp), tint = TextSecondary)
                            Spacer(Modifier.width(6.dp))
                            Text("立即同步", color = TextPrimary, fontSize = 13.sp)
                        }
                    }
                }
            }
        }
    }

    if (showChangelogDialog != null) {
        val rel = showChangelogDialog!!
        AlertDialog(
            onDismissRequest = { showChangelogDialog = null },
            containerColor = Color(0xFF1A1E1B),
            title = {
                Text("v${rel.versionName} 更新说明", color = TextPrimary, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            },
            text = {
                Text(
                    text = rel.changelog.ifBlank { "包含多项性能优化与界面升级。" },
                    color = TextSecondary,
                    fontSize = 14.sp,
                    lineHeight = 22.sp
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        vm.startDownload(rel)
                        showChangelogDialog = null
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = PrimaryNeonGreen)
                ) {
                    Text("开始下载", color = Color.Black)
                }
            },
            dismissButton = {
                TextButton(onClick = { showChangelogDialog = null }) {
                    Text("关闭", color = TextSecondary)
                }
            }
        )
    }
}

// -------------------------------------------------------------
// Subpage 5: 聆听权限与后台自检
// -------------------------------------------------------------
@Composable
private fun AutoListeningPage(
    vm: AppViewModel,
    onBack: () -> Unit
) {
    val context = LocalContext.current
    var selfCheckTrigger by remember { mutableStateOf(0) }

    val hasMicPermission = remember(selfCheckTrigger) {
        ContextCompat.checkSelfPermission(
            context,
            android.Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
    }
    val hasNotificationPermission = remember(selfCheckTrigger) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ContextCompat.checkSelfPermission(
                context,
                android.Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
        } else {
            true
        }
    }
    val isBatteryOptimizationsIgnored = remember(selfCheckTrigger) {
        val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
        pm?.isIgnoringBatteryOptimizations(context.packageName) == true
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
    ) {
        SubPageTopBar(title = "聆听权限与后台自检", onBack = onBack)

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(vertical = 12.dp)
        ) {
            item {
                SettingsSectionTitle("系统权限与后台诊断")
                Spacer(modifier = Modifier.height(8.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(SurfaceDark)
                        .border(1.dp, SurfaceCardGreenBorder, RoundedCornerShape(14.dp))
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    // Item 1: Mic
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.Top
                    ) {
                        Icon(
                            imageVector = if (hasMicPermission) Icons.Default.CheckCircle else Icons.Default.Warning,
                            contentDescription = null,
                            tint = if (hasMicPermission) PrimaryNeonGreen else DangerRed,
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(modifier = Modifier.width(12.dp))
                        Column {
                            Text(
                                text = "麦克风录音权限 (RECORD_AUDIO)",
                                color = TextPrimary,
                                fontSize = 15.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                            Spacer(modifier = Modifier.height(2.dp))
                            Text(
                                text = if (hasMicPermission) "已授权，支持连续 PCM 硬件音频采集" else "未授权，录音功能无法启动",
                                color = if (hasMicPermission) TextSecondary else DangerRed,
                                fontSize = 13.sp
                            )
                        }
                    }

                    HorizontalDivider(color = DividerDark)

                    // Item 2: Notification
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.Top
                    ) {
                        Icon(
                            imageVector = if (hasNotificationPermission) Icons.Default.CheckCircle else Icons.Default.Warning,
                            contentDescription = null,
                            tint = if (hasNotificationPermission) PrimaryNeonGreen else DangerRed,
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(modifier = Modifier.width(12.dp))
                        Column {
                            Text(
                                text = "时效性通知权限 (POST_NOTIFICATIONS)",
                                color = TextPrimary,
                                fontSize = 15.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                            Spacer(modifier = Modifier.height(2.dp))
                            Text(
                                text = if (hasNotificationPermission) "已开启，前台常驻服务与状态通知正常" else "未授权，通知栏快捷控制可能受限",
                                color = if (hasNotificationPermission) TextSecondary else DangerRed,
                                fontSize = 13.sp
                            )
                        }
                    }

                    HorizontalDivider(color = DividerDark)

                    // Item 3: Battery Optimization
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.Top
                    ) {
                        Icon(
                            imageVector = if (isBatteryOptimizationsIgnored) Icons.Default.CheckCircle else Icons.Default.Info,
                            contentDescription = null,
                            tint = if (isBatteryOptimizationsIgnored) PrimaryNeonGreen else TextMuted,
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(modifier = Modifier.width(12.dp))
                        Column {
                            Text(
                                text = "电池优化与后台保活 (Battery Optimization)",
                                color = TextPrimary,
                                fontSize = 15.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                            Spacer(modifier = Modifier.height(2.dp))
                            Text(
                                text = if (isBatteryOptimizationsIgnored) "已加入电池优化白名单，后台稳定录音不被挂起" else "未加入白名单，息屏录音可能会被系统深度睡眠冻结",
                                color = TextSecondary,
                                fontSize = 13.sp
                            )
                        }
                    }

                    HorizontalDivider(color = DividerDark)

                    Button(
                        onClick = {
                            selfCheckTrigger++
                            vm.repo.message.value = "系统自检诊断已刷新"
                        },
                        colors = ButtonDefaults.buttonColors(containerColor = SurfaceCardElevated),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Icon(Icons.Outlined.Sync, null, modifier = Modifier.size(16.dp), tint = PrimaryNeonGreen)
                        Spacer(Modifier.width(6.dp))
                        Text("重新运行完整自检", color = TextPrimary, fontSize = 13.sp)
                    }
                }
            }
        }
    }
}

@Composable
private fun SubPageTopBar(
    title: String,
    onBack: () -> Unit
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp, vertical = 10.dp)
    ) {
        IconButton(
            onClick = onBack,
            modifier = Modifier.align(Alignment.CenterStart)
        ) {
            Icon(
                imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                contentDescription = "返回",
                tint = TextPrimary
            )
        }
        Text(
            text = title,
            color = TextPrimary,
            fontSize = 17.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.align(Alignment.Center)
        )
    }
}
