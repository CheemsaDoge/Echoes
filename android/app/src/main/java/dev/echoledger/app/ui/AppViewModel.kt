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
import dev.echoledger.app.network.UploadScheduler
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.json.JSONObject
import java.io.File
import java.time.LocalDate

data class SessionAudioInfo(val chunkCount: Int, val totalSamples: Long, val sceneTag: String)

class AppViewModel(application: Application) : AndroidViewModel(application) {
    private val graph = (application as EchoApp).graph
    val repo = graph.repository
    val day = MutableStateFlow(LocalDate.now())
    val busy = MutableStateFlow(false)
    val detail = MutableStateFlow<JSONObject?>(null)
    val evaluation = MutableStateFlow<JSONObject?>(null)
    val recording = RecordingBus.state
    val pending = repo.dao.pendingCount().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)
    val localSessions = repo.dao.observeSessions().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val selectedSceneTag = MutableStateFlow("谈话")
    val tagVersion = MutableStateFlow(0)

    val sessionStats: StateFlow<Map<String, SessionAudioInfo>> = combine(
        repo.dao.observeSessions(),
        repo.dao.observeAllChunks(),
        tagVersion
    ) { sessions, chunks, _ ->
        val chunksBySession = chunks.groupBy { it.sessionId }
        sessions.associate { s ->
            val sessionChunks = chunksBySession[s.id] ?: emptyList()
            val count = sessionChunks.size
            val samples = sessionChunks.sumOf { it.sampleCount.toLong() }
            val tag = repo.local.getSceneTag(s.id)
            s.id to SessionAudioInfo(count, samples, tag)
        }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyMap())

    private val _playingSessionId = MutableStateFlow<String?>(null)
    val playingSessionId: StateFlow<String?> = _playingSessionId.asStateFlow()

    init {
        viewModelScope.launch {
            recording.collect { state ->
                if (state.recording && state.sessionId.isNotBlank()) {
                    val currentTag = repo.local.getSceneTag(state.sessionId)
                    if (currentTag == "未分类") {
                        repo.local.setSceneTag(state.sessionId, selectedSceneTag.value)
                        tagVersion.value++
                    }
                }
            }
        }
    }

    private fun cached(kind: String) = repo.observe(kind).map { entries -> entries.map { JSONObject(it.payload) } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val events = cached("events")
    val people = cached("speakers")
    val memories = cached("memories")
    val types = cached("types")
    val profiles = cached("profiles")
    val status = cached("status")
    private var realtime: Job? = null
    private var player: MediaPlayer? = null

    fun connect() {
        if (realtime?.isActive == true) return
        UploadScheduler.enqueue(getApplication())
        if (repo.prefs.token.isBlank()) return
        refresh()
        realtime = viewModelScope.launch {
            graph.ready.await()
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
    fun act(block: suspend () -> Unit) {
        viewModelScope.launch {
            busy.value = true
            try { graph.ready.await(); block() }
            catch (cancel: CancellationException) { throw cancel }
            catch (e: Exception) { repo.message.value = e.message?.take(220) ?: e.javaClass.simpleName }
            finally { busy.value = false }
        }
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

    fun stopPlayback() {
        player?.release()
        player = null
        _playingSessionId.value = null
    }

    fun playSession(sessionId: String) = act {
        require(!recording.value.recording) { "正在录音中，无法播放回听" }
        if (_playingSessionId.value == sessionId) {
            stopPlayback()
            return@act
        }
        stopPlayback()
        val wav = repo.local.exportMergedWav(sessionId)
        require(wav.exists() && wav.length() > 44) { "录音文件不存在或为空" }
        val p = MediaPlayer.create(getApplication(), Uri.fromFile(wav)) ?: error("无法初始化播放器")
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

    fun setSessionSceneTag(sessionId: String, tag: String) {
        repo.local.setSceneTag(sessionId, tag)
        tagVersion.value++
    }

    fun saveToDownloads(sessionId: String, asZip: Boolean = false) = act {
        val file = if (asZip) repo.local.exportSessionZip(sessionId) else repo.local.exportMergedWav(sessionId)
        val mimeType = if (asZip) "application/zip" else "audio/wav"
        val app = getApplication<Application>()
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
            }
        } else {
            @Suppress("DEPRECATION")
            val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "EchoLedger").apply { mkdirs() }
            val dest = File(dir, file.name)
            file.copyTo(dest, overwrite = true)
        }
        repo.message.value = "已保存至 Download/EchoLedger/${file.name}"
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
        tagVersion.value++
        repo.refresh(day.value)
    }

    override fun onCleared() {
        disconnect()
        stopPlayback()
        super.onCleared()
    }
}
