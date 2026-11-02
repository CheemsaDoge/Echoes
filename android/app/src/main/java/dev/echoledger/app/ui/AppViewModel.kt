package dev.echoledger.app.ui

import android.app.Application
import android.media.MediaPlayer
import android.net.Uri
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
        player?.release()
        player = MediaPlayer.create(getApplication(), Uri.fromFile(File(path)))?.apply {
            setOnCompletionListener { it.release(); if (player === it) player = null }
            start()
        }
    }
    fun deleteSession(id: String) = act { repo.deleteSession(id); repo.refresh(day.value) }
    override fun onCleared() { disconnect(); player?.release(); super.onCleared() }
}
