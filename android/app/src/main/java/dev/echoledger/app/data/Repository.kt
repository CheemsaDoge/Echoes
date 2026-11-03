package dev.echoledger.app.data

import android.content.Context
import dev.echoledger.app.capture.RecordingBus
import dev.echoledger.app.network.Api
import dev.echoledger.app.network.ApiFailure
import dev.echoledger.app.network.UploadScheduler
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.ZoneId

class Repository(private val context: Context, val dao: JournalDao, val prefs: SecurePreferences, val local: LocalStore) {
    val api = Api(prefs)
    val message = MutableStateFlow("")
    private val syncMutex = Mutex()
    fun observe(kind: String) = dao.observe(kind)
    suspend fun configure(base: String, token: String) {
        api.validateBase(base.trim())
        require(token.isNotBlank() || prefs.token.isNotBlank()) { "Backend token is required" }
        if (prefs.baseUrl != base.trim().trimEnd('/')) { dao.resetRemote(); prefs.cursor = 0 }
        prefs.baseUrl = base
        if (token.isNotBlank()) prefs.token = token.trim()
        UploadScheduler.enqueue(context)
    }
    private fun entries(kind: String, text: String): List<CacheEntry> {
        val array = JSONArray(text)
        return (0 until array.length()).map {
            val row = array.getJSONObject(it)
            CacheEntry(kind, row.getString("id"), row.optLong("start_ms", row.optLong("created_ms", 0)), row.optLong("end_ms", 0), row.toString())
        }
    }
    suspend fun refresh(day: LocalDate) = syncMutex.withLock {
        if (prefs.operationMode == "standalone" || prefs.token.isBlank()) {
            message.value = ""
            return@withLock
        }
        val zone = ZoneId.systemDefault()
        val from = day.atStartOfDay(zone).toInstant().toEpochMilli()
        val to = day.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
        val events = entries("events", api.call("/v1/events?from_ms=$from&to_ms=$to&limit=500"))
        dao.replaceEvents(from, to, events)
        for ((kind, path) in listOf("speakers" to "/v1/speakers", "memories" to "/v1/memories?confirmed_only=false&limit=200",
            "types" to "/v1/event-types", "profiles" to "/v1/vad/profiles")) {
            dao.replace(kind, entries(kind, api.call(path)))
        }
        val status = JSONObject(api.call("/v1/status")).put("id", "status")
        dao.replace("status", listOf(CacheEntry("status", "status", payload = status.toString())))
        message.value = ""
    }
    suspend fun getEvent(id: String): JSONObject {
        val local = dao.getCacheEntry("events", id)
        if (prefs.operationMode == "standalone" || prefs.token.isBlank() || prefs.baseUrl.isBlank()) {
            return local?.let { JSONObject(it.payload) } ?: error("Event not found")
        }
        return try {
            JSONObject(api.call("/v1/events/$id"))
        } catch (e: Exception) {
            local?.let { JSONObject(it.payload) } ?: throw e
        }
    }
    suspend fun updateEventTitleLocally(id: String, newTitle: String) {
        val entry = dao.getCacheEntry("events", id) ?: return
        val json = JSONObject(entry.payload).put("title", newTitle).put("updated_ms", System.currentTimeMillis())
        dao.putCache(listOf(entry.copy(payload = json.toString())))
    }
    suspend fun createSpeaker(name: String) {
        if (prefs.operationMode == "standalone" || prefs.token.isBlank()) {
            val id = "speaker_" + (System.currentTimeMillis() % 10000)
            val json = JSONObject().put("id", id).put("name", name)
            dao.putCache(listOf(CacheEntry("speakers", id, 0, 0, json.toString())))
            return
        }
        api.call("/v1/speakers", "POST", JSONObject().put("name", name))
    }
    suspend fun assignSpeaker(segmentId: String, speakerId: String?) {
        if (prefs.operationMode == "standalone" || prefs.token.isBlank()) {
            val chunkId = segmentId.substringBefore('_')
            val entry = dao.getCacheEntry("events", chunkId) ?: return
            val json = JSONObject(entry.payload)
            val segments = json.optJSONArray("segments") ?: return
            val speakerName = if (speakerId != null) {
                dao.getCacheEntry("speakers", speakerId)?.let { JSONObject(it.payload).optString("name") } ?: speakerId
            } else null
            for (i in 0 until segments.length()) {
                val seg = segments.getJSONObject(i)
                if (seg.optString("id") == segmentId) {
                    seg.put("speaker_name", speakerName ?: JSONObject.NULL)
                }
            }
            dao.putCache(listOf(entry.copy(payload = json.toString())))
            return
        }
        api.call("/v1/segments/$segmentId/speaker", "PUT", JSONObject().put("speaker_id", speakerId ?: JSONObject.NULL))
    }
    suspend fun confirmMemory(id: String, confirmed: Boolean) {
        if (prefs.operationMode == "standalone" || prefs.token.isBlank()) {
            val entry = dao.getCacheEntry("memories", id) ?: return
            val json = JSONObject(entry.payload).put("confirmed", if (confirmed) 1 else 0)
            dao.putCache(listOf(entry.copy(payload = json.toString())))
            return
        }
        api.call("/v1/memories/$id", "PATCH", JSONObject().put("confirmed", confirmed))
    }
    suspend fun deleteMemory(id: String) {
        if (prefs.operationMode == "standalone" || prefs.token.isBlank()) {
            dao.deleteCacheEntry("memories", id)
            return
        }
        api.call("/v1/memories/$id", "DELETE")
    }
    suspend fun saveProfile(name: String, config: JSONObject) { api.call("/v1/vad/profiles", "POST", JSONObject().put("name", name).put("activate", true).put("config", config)) }
    suspend fun saveType(id: String, name: String, keywords: List<String>) {
        api.call("/v1/event-types/$id", "PUT", JSONObject().put("id", id).put("name", name).put("keywords", JSONArray(keywords)))
    }
    suspend fun evaluate(scenario: String, intervalText: String, sweep: Boolean): JSONObject {
        val chunk = dao.latestCalibration() ?: error("Record a calibration clip first")
        require(chunk.uploadState == "uploaded") { "Wait for the calibration clip to upload" }
        val intervals = JSONArray()
        intervalText.split(';', '\n').map { it.trim() }.filter { it.isNotEmpty() }.forEach { pair ->
            val numbers = pair.split('-').map { it.trim().toDouble() }
            require(numbers.size == 2 && numbers[1] > numbers[0] && numbers[0] >= 0)
            intervals.put(JSONObject().put("start", (numbers[0] * 16000).toLong()).put("end", (numbers[1] * 16000).toLong()))
        }
        val profiles = JSONArray(api.call("/v1/vad/profiles"))
        val active = (0 until profiles.length()).map { profiles.getJSONObject(it) }.firstOrNull { it.optInt("active") == 1 }
        val body = JSONObject().put("chunk_id", chunk.id).put("scenario", scenario).put("speech_intervals", intervals).put("fully_labeled", true)
        active?.optJSONObject("config")?.let { body.put("config", it) }
        return JSONObject(api.call(if (sweep) "/v1/vad/sweep" else "/v1/vad/evaluate", "POST", body))
    }
    suspend fun deleteSession(id: String) {
        require(RecordingBus.state.value.sessionId != id || !RecordingBus.state.value.recording) { "Stop this recording first" }
        if (prefs.token.isNotBlank() && prefs.operationMode != "standalone") {
            try { 
                api.call("/v1/sessions/$id", "DELETE") 
            } catch (e: ApiFailure) { 
                if (e.status != 404 && e.status != 410) throw e 
            } catch (_: Exception) {
                // Ignore network/backend failures for standalone local recordings
            }
        }
        local.remove(id)
        dao.deleteSessionCache(id)
        dao.deleteLocal(id)
    }
}
