package dev.echoledger.app.ui

import android.app.Application
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.media.MediaPlayer
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.core.content.FileProvider
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.echoledger.app.EchoApp
import dev.echoledger.app.capture.RecordingBus
import dev.echoledger.app.data.Repository
import dev.echoledger.app.network.UploadScheduler
import dev.echoledger.app.update.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.json.JSONObject
import java.io.File
import java.time.LocalDate

data class SessionAudioInfo(val chunkCount: Int, val totalSamples: Long)

class AppViewModel @JvmOverloads constructor(
    application: Application,
    repository: Repository? = null,
    val updateChecker: UpdateChecker = UpdateChecker(),
    val updateDownloader: UpdateDownloader = UpdateDownloader(application),
    val updateInstaller: UpdateInstaller = UpdateInstaller,
    val ioDispatcher: CoroutineDispatcher = Dispatchers.IO
) : AndroidViewModel(application) {
    val graph = (application as? EchoApp)?.graph
    val repo = repository ?: graph?.repository ?: error("EchoApp graph or repository required")
    val day = MutableStateFlow(LocalDate.now())
    val busy = MutableStateFlow(false)
    val detail = MutableStateFlow<JSONObject?>(null)
    val evaluation = MutableStateFlow<JSONObject?>(null)
    val recording = RecordingBus.state
    val pending = repo.dao.pendingCount().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)
    val localSessions = repo.dao.observeSessions().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    private val _updateState = MutableStateFlow<UpdateState>(UpdateState.Idle)
    val updateState: StateFlow<UpdateState> = _updateState.asStateFlow()
    val lastCheckedAtMs = MutableStateFlow<Long?>(null)

    private var downloadJob: Job? = null
    private var activeRelease: UpdateRelease? = null

    val sessionStats: StateFlow<Map<String, SessionAudioInfo>> = combine(
        repo.dao.observeSessions(),
        repo.dao.observeAllChunks()
    ) { sessions, chunks ->
        val chunksBySession = chunks.groupBy { it.sessionId }
        sessions.associate { s ->
            val sessionChunks = chunksBySession[s.id] ?: emptyList()
            val count = sessionChunks.size
            val samples = sessionChunks.sumOf { it.sampleCount.toLong() }
            s.id to SessionAudioInfo(count, samples)
        }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyMap())

    private val _playingSessionId = MutableStateFlow<String?>(null)
    val playingSessionId: StateFlow<String?> = _playingSessionId.asStateFlow()

    private fun cached(kind: String) = repo.observe(kind).map { entries -> entries.map { JSONObject(it.payload) } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val events = cached("events")
    val people = cached("speakers")
    val memories = cached("memories")
    val types = cached("types")
    val profiles = cached("profiles")
    val status = cached("status")
    val aiClient = dev.echoledger.app.network.VertexAiClient()
    val operationMode = MutableStateFlow(repo.prefs.operationMode)
    val aiProvider = MutableStateFlow(repo.prefs.aiProvider)
    val aiApiKey = MutableStateFlow(repo.prefs.aiApiKey)
    val aiModelName = MutableStateFlow(repo.prefs.aiModelName)
    val vertexProjectId = MutableStateFlow(repo.prefs.vertexProjectId)
    val vertexLocation = MutableStateFlow(repo.prefs.vertexLocation)
    val vertexEndpoint = MutableStateFlow(repo.prefs.vertexEndpoint)
    val aiTesting = MutableStateFlow(false)
    val aiTestResult = MutableStateFlow<String?>(null)
    val aiTestSuccess = MutableStateFlow<Boolean?>(null)

    fun saveAiConfig(
        mode: String,
        provider: String,
        key: String,
        model: String,
        projectId: String,
        location: String,
        endpoint: String
    ) {
        repo.prefs.operationMode = mode
        repo.prefs.aiProvider = provider
        if (key.isNotBlank()) repo.prefs.aiApiKey = key
        repo.prefs.aiModelName = model
        repo.prefs.vertexProjectId = projectId
        repo.prefs.vertexLocation = location
        repo.prefs.vertexEndpoint = endpoint

        operationMode.value = repo.prefs.operationMode
        aiProvider.value = repo.prefs.aiProvider
        aiApiKey.value = repo.prefs.aiApiKey
        aiModelName.value = repo.prefs.aiModelName
        vertexProjectId.value = repo.prefs.vertexProjectId
        vertexLocation.value = repo.prefs.vertexLocation
        vertexEndpoint.value = repo.prefs.vertexEndpoint
        repo.message.value = "AI 配置已保存"
    }

    fun testAiConnection(
        provider: String,
        key: String,
        model: String,
        projectId: String = "",
        location: String = "global",
        endpoint: String = ""
    ) = viewModelScope.launch {
        aiTesting.value = true
        aiTestResult.value = null
        aiTestSuccess.value = null
        val effectiveKey = if (key.isNotBlank()) key else repo.prefs.aiApiKey
        val res = aiClient.testConnection(
            apiKey = effectiveKey,
            provider = provider,
            model = model,
            projectId = projectId,
            location = location,
            endpointOverride = endpoint
        )
        aiTesting.value = false
        if (res.isSuccess) {
            aiTestSuccess.value = true
            aiTestResult.value = res.getOrNull() ?: "连接成功"
        } else {
            aiTestSuccess.value = false
            aiTestResult.value = res.exceptionOrNull()?.message ?: "连接失败"
        }
    }

    private var realtime: Job? = null
    private var player: MediaPlayer? = null

    fun connect() {
        if (realtime?.isActive == true) return
        UploadScheduler.enqueue(getApplication())
        if (repo.prefs.token.isBlank()) return
        refresh()
        realtime = viewModelScope.launch {
            graph?.ready?.await()
            repo.api.changes().conflate().onEach { cursor ->
                repo.refresh(day.value)
                repo.prefs.cursor = cursor
            }.retryWhen { _, attempt ->
                delay(minOf(30000L, 1500L * (attempt + 1))); true
            }.collect()
        }
    }
    fun disconnect() { realtime?.cancel(); realtime = null }
    fun selectDay(value: LocalDate) { day.value = value; refresh() }
    fun refresh() = act { repo.refresh(day.value); UploadScheduler.enqueue(getApplication()) }
    fun act(block: suspend CoroutineScope.() -> Unit): Job = viewModelScope.launch {
        busy.value = true
        try { graph?.ready?.await(); block() }
        catch (cancel: CancellationException) { throw cancel }
        catch (e: Exception) { repo.message.value = e.message?.take(220) ?: e.javaClass.simpleName }
        finally { busy.value = false }
    }
    fun configure(url: String, token: String) = act { disconnect(); repo.configure(url, token); connect(); repo.refresh(day.value) }
    fun openEvent(id: String) = act { detail.value = repo.getEvent(id) }
    fun addSpeaker(name: String) = act { repo.createSpeaker(name); repo.refresh(day.value) }
    fun assign(segmentId: String, speakerId: String?) = act {
        repo.assignSpeaker(segmentId, speakerId)
        detail.value?.let { detail.value = repo.getEvent(it.getString("id")) }
        repo.refresh(day.value)
    }
    fun confirm(id: String, value: Boolean) = act { repo.confirmMemory(id, value); repo.refresh(day.value) }
    fun deleteMemory(id: String) = act { repo.deleteMemory(id); repo.refresh(day.value) }
    fun saveProfile(name: String, config: JSONObject) = act { repo.saveProfile(name, config); repo.refresh(day.value) }
    fun saveType(id: String, name: String, words: List<String>) = act { repo.saveType(id, name, words); repo.refresh(day.value) }
    fun evaluate(scenario: String, intervals: String, sweep: Boolean) = act { evaluation.value = repo.evaluate(scenario, intervals, sweep) }
    fun playCalibration() = act {
        val clip = repo.dao.latestCalibration() ?: error("Record a calibration clip first")
        playFile(clip.path)
    }
    fun playChunk(chunkId: String) = act {
        val clip = repo.dao.chunk(chunkId) ?: error("This source audio is not stored on this device. Use the authenticated audio API.")
        playFile(clip.path)
    }
    private fun playFile(path: String) {
        require(!recording.value.recording) { "Stop recording before playback to avoid recording the playback" }
        require(File(path).exists()) { "Source audio is unavailable" }
        stopPlayback()
        player = MediaPlayer.create(getApplication(), Uri.fromFile(File(path)))?.apply {
            setOnCompletionListener { it.release(); if (player === it) { player = null; _playingSessionId.value = null } }
            start()
        }
    }

    private var playbackJob: Job? = null

    fun stopPlayback() {
        playbackJob?.cancel()
        playbackJob = null
        player?.release()
        player = null
        _playingSessionId.value = null
    }

    fun playSession(sessionId: String) {
        require(!recording.value.recording) { "正在录音中，无法播放回听" }
        if (_playingSessionId.value == sessionId) {
            stopPlayback()
            return
        }
        stopPlayback()
        playbackJob = act {
            val wav = repo.local.exportMergedWav(sessionId)
            require(wav.exists() && wav.length() > 44) { "录音文件不存在或为空" }
            if (!isActive) return@act
            val p = MediaPlayer.create(getApplication(), Uri.fromFile(wav)) ?: error("无法初始化播放器")
            if (!isActive) {
                p.release()
                return@act
            }
            player = p
            _playingSessionId.value = sessionId
            p.setOnCompletionListener {
                it.release()
                if (player === it) {
                    player = null
                    _playingSessionId.value = null
                }
            }
            p.start()
        }
    }

    fun saveAllToDownloads() = act {
        val app = getApplication<Application>()
        val savedName = withContext(Dispatchers.IO) {
            val file = repo.local.exportAllSessionsZip()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, file.name)
                    put(MediaStore.MediaColumns.MIME_TYPE, "application/zip")
                    put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/EchoLedger")
                }
                val uri = app.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: error("无法在系统 Downloads 目录创建条目")
                app.contentResolver.openOutputStream(uri)?.use { out ->
                    file.inputStream().buffered().use { it.copyTo(out) }
                } ?: error("无法写入系统 Downloads 文件")
            } else {
                @Suppress("DEPRECATION")
                val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "EchoLedger").apply { mkdirs() }
                val dest = File(dir, file.name)
                try {
                    file.copyTo(dest, overwrite = true)
                } catch (_: Exception) {
                    val fallbackDir = app.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: app.filesDir
                    file.copyTo(File(fallbackDir, file.name), overwrite = true)
                }
            }
            file.name
        }
        repo.message.value = "已保存全部 Chunks 至 Download/EchoLedger/$savedName"
    }

    fun shareAllSessions(context: Context) = act {
        val file = repo.local.exportAllSessionsZip()
        val authority = "${context.packageName}.fileprovider"
        val uri = FileProvider.getUriForFile(context, authority, file)
        val shareIntent = Intent(Intent.ACTION_SEND).apply {
            type = "application/zip"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        val chooser = Intent.createChooser(shareIntent, "分享全部录音 Chunks 包").apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(chooser)
    }

    fun saveToDownloads(sessionId: String, asZip: Boolean = false) = act {
        val app = getApplication<Application>()
        val savedName = withContext(Dispatchers.IO) {
            val file = if (asZip) repo.local.exportSessionZip(sessionId) else repo.local.exportMergedWav(sessionId)
            val mimeType = if (asZip) "application/zip" else "audio/wav"
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, file.name)
                    put(MediaStore.MediaColumns.MIME_TYPE, mimeType)
                    put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/EchoLedger")
                }
                val uri = app.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: error("无法在系统 Downloads 目录创建条目")
                app.contentResolver.openOutputStream(uri)?.use { out ->
                    file.inputStream().buffered().use { it.copyTo(out) }
                } ?: error("无法写入系统 Downloads 文件")
            } else {
                @Suppress("DEPRECATION")
                val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "EchoLedger").apply { mkdirs() }
                val dest = File(dir, file.name)
                try {
                    file.copyTo(dest, overwrite = true)
                } catch (_: Exception) {
                    val fallbackDir = app.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: app.filesDir
                    file.copyTo(File(fallbackDir, file.name), overwrite = true)
                }
            }
            file.name
        }
        repo.message.value = "已保存至 Download/EchoLedger/$savedName"
    }

    fun shareSession(context: Context, sessionId: String, asZip: Boolean = false) = act {
        val file = if (asZip) repo.local.exportSessionZip(sessionId) else repo.local.exportMergedWav(sessionId)
        val authority = "${context.packageName}.fileprovider"
        val uri = FileProvider.getUriForFile(context, authority, file)
        val shareIntent = Intent(Intent.ACTION_SEND).apply {
            type = if (asZip) "application/zip" else "audio/wav"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        val chooser = Intent.createChooser(shareIntent, if (asZip) "分享录音分块包" else "分享无损录音").apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(chooser)
    }

    fun deleteSession(id: String) = act {
        if (_playingSessionId.value == id) stopPlayback()
        repo.deleteSession(id)
        if (repo.prefs.token.isNotBlank()) {
            runCatching { repo.refresh(day.value) }
        }
    }

    fun checkForUpdates(silent: Boolean = false): Job = viewModelScope.launch {
        if (!silent) {
            _updateState.value = UpdateState.Checking
        }
        when (val result = updateChecker.checkForUpdate()) {
            is UpdateResult.Available -> {
                lastCheckedAtMs.value = System.currentTimeMillis()
                activeRelease = result.release
                _updateState.value = UpdateState.UpdateAvailable(result.release)
            }
            is UpdateResult.UpToDate -> {
                lastCheckedAtMs.value = System.currentTimeMillis()
                _updateState.value = UpdateState.UpToDate(result.currentVersion)
            }
            is UpdateResult.Error -> {
                if (!silent) {
                    _updateState.value = UpdateState.Error(result.message, result.canRetry)
                } else if (_updateState.value is UpdateState.Checking) {
                    _updateState.value = UpdateState.Idle
                }
            }
        }
    }

    private suspend fun isCachedApkValid(release: UpdateRelease): Boolean = withContext(ioDispatcher) {
        if (!updateDownloader.isApkDownloaded(release.versionName)) return@withContext false
        val file = updateDownloader.getApkFile(release.versionName)
        if (release.apkAsset.sizeBytes > 0L && file.length() != release.apkAsset.sizeBytes) {
            return@withContext false
        }
        if (!release.apkAsset.sha256.isNullOrBlank()) {
            val fileHash = runCatching {
                val digest = java.security.MessageDigest.getInstance("SHA-256")
                file.inputStream().use { input ->
                    val buffer = ByteArray(8192)
                    while (true) {
                        currentCoroutineContext().ensureActive()
                        val read = input.read(buffer)
                        if (read == -1) break
                        digest.update(buffer, 0, read)
                    }
                }
                digest.digest().joinToString("") { "%02x".format(it) }
            }.getOrNull()
            if (!fileHash.equals(release.apkAsset.sha256.trim(), ignoreCase = true)) {
                return@withContext false
            }
        }
        true
    }

    fun startDownload(release: UpdateRelease): Job {
        activeRelease = release
        downloadJob?.cancel()

        _updateState.value = UpdateState.Downloading(0L, release.apkAsset.sizeBytes, 0)
        val job = viewModelScope.launch {
            if (isCachedApkValid(release)) {
                val apkFile = updateDownloader.getApkFile(release.versionName)
                _updateState.value = UpdateState.ReadyToInstall(apkFile, release)
                return@launch
            }

            try {
                updateDownloader.download(release).collect { progress ->
                    _updateState.value = UpdateState.Downloading(
                        bytesRead = progress.bytesRead,
                        totalBytes = progress.totalBytes,
                        percent = progress.percent
                    )
                }
                val apkFile = updateDownloader.getApkFile(release.versionName)
                _updateState.value = UpdateState.ReadyToInstall(apkFile, release)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _updateState.value = UpdateState.Error(
                    message = e.message ?: "下载更新失败: ${e.javaClass.simpleName}",
                    canRetry = true
                )
            }
        }
        downloadJob = job
        return job
    }

    fun cancelDownload() {
        downloadJob?.cancel()
        downloadJob = null
        val current = _updateState.value
        if (current is UpdateState.Downloading) {
            _updateState.value = activeRelease?.let { UpdateState.UpdateAvailable(it) } ?: UpdateState.Idle
        }
    }

    fun installUpdate(context: Context, apkFile: File) {
        when (val result = updateInstaller.install(context, apkFile)) {
            is UpdateInstaller.InstallResult.Launched -> {
                // Launched package installer
            }
            is UpdateInstaller.InstallResult.PermissionRequired -> {
                repo.message.value = "请在系统设置中允许 EchoLedger 安装未知应用"
            }
            is UpdateInstaller.InstallResult.Error -> {
                _updateState.value = UpdateState.Error(result.message, canRetry = true)
                repo.message.value = result.message
            }
        }
    }

    fun dismissUpdate() {
        downloadJob?.cancel()
        downloadJob = null
        _updateState.value = UpdateState.Idle
    }

    override fun onCleared() {
        disconnect()
        stopPlayback()
        downloadJob?.cancel()
        super.onCleared()
    }
}
