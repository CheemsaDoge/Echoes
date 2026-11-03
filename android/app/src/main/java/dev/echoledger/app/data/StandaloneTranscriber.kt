package dev.echoledger.app.data

import dev.echoledger.app.network.AsrSegment
import dev.echoledger.app.network.VertexAiClient
import dev.echoledger.core.SAMPLE_RATE
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import kotlin.math.sqrt

class StandaloneTranscriber(
    private val dao: JournalDao,
    private val prefs: SecurePreferences,
    private val aiClient: VertexAiClient = VertexAiClient()
) {
    companion object {
        const val SILENCE_RMS_THRESHOLD = 0.003f

        fun computeRms(wavBytes: ByteArray): Float {
            if (wavBytes.size <= 44) return 0f
            var sum = 0.0
            val sampleCount = (wavBytes.size - 44) / 2
            if (sampleCount <= 0) return 0f
            var i = 44
            while (i + 1 < wavBytes.size) {
                val low = wavBytes[i].toInt() and 0xFF
                val high = wavBytes[i + 1].toInt()
                val sample = ((high shl 8) or low).toShort()
                val norm = sample.toDouble() / 32768.0
                sum += norm * norm
                i += 2
            }
            return sqrt(sum / sampleCount).toFloat()
        }

        fun calculateUtcTimestamps(
            sessionStartedAtMs: Long,
            chunkStartSample: Long,
            segmentStartMs: Int,
            segmentEndMs: Int
        ): Pair<Long, Long> {
            val chunkStartUtcMs = sessionStartedAtMs + (chunkStartSample * 1000L / SAMPLE_RATE)
            return Pair(chunkStartUtcMs + segmentStartMs, chunkStartUtcMs + segmentEndMs)
        }
    }

    suspend fun processChunk(chunk: LocalChunk): Boolean = withContext(Dispatchers.IO) {
        val session = dao.session(chunk.sessionId) ?: run {
            dao.markUpload(chunk.id, "error", "Session not found")
            return@withContext false
        }

        val file = File(chunk.path)
        if (!file.exists() || file.length() <= 44) {
            dao.markUpload(chunk.id, "error", "Audio file missing or empty")
            return@withContext false
        }

        val wavBytes = file.readBytes()
        val rms = computeRms(wavBytes)
        if (rms < SILENCE_RMS_THRESHOLD) {
            // Audio is silent energy; skip API call to conserve tokens/quota
            dao.markUpload(chunk.id, "completed")
            return@withContext true
        }

        val apiKey = prefs.aiApiKey
        if (apiKey.isBlank()) {
            dao.markUpload(chunk.id, "pending", "AI API Key is not configured")
            return@withContext false
        }

        val segments: List<AsrSegment> = try {
            aiClient.transcribe(
                wavBytes = wavBytes,
                apiKey = apiKey,
                provider = prefs.aiProvider,
                model = prefs.aiModelName,
                projectId = prefs.vertexProjectId,
                location = prefs.vertexLocation,
                endpointOverride = prefs.vertexEndpoint
            )
        } catch (e: Exception) {
            dao.markUpload(chunk.id, "pending", e.message?.take(180) ?: "AI transcription failed")
            throw e
        }

        if (segments.isEmpty()) {
            dao.markUpload(chunk.id, "completed")
            return@withContext true
        }

        val chunkStartUtcMs = session.startedAtMs + (chunk.startSample * 1000L / SAMPLE_RATE)
        val fullText = segments.joinToString(" ") { it.text.trim() }.trim()
        if (fullText.isBlank()) {
            dao.markUpload(chunk.id, "completed")
            return@withContext true
        }

        val eventStartMs = chunkStartUtcMs + segments.first().start_ms
        val eventEndMs = chunkStartUtcMs + segments.last().end_ms
        val title = if (fullText.length > 60) fullText.take(57) + "..." else fullText

        val distinctSpeakers = segments.map { it.speaker_label }.distinct()
        val speakerNamesArray = JSONArray()
        distinctSpeakers.forEach { speakerNamesArray.put("Speaker $it") }

        val segmentsArray = JSONArray()
        for ((idx, seg) in segments.withIndex()) {
            val segStart = chunkStartUtcMs + seg.start_ms
            val segEnd = chunkStartUtcMs + seg.end_ms
            val spanArray = JSONArray().put(JSONObject().put("chunk_id", chunk.id))
            val segObj = JSONObject()
                .put("id", "${chunk.id}_$idx")
                .put("local_speaker", seg.speaker_label)
                .put("speaker_name", JSONObject.NULL)
                .put("text", seg.text)
                .put("start_ms", segStart)
                .put("end_ms", segEnd)
                .put("source_spans", spanArray)
            segmentsArray.put(segObj)
        }

        val eventPayload = JSONObject()
            .put("id", chunk.id)
            .put("session_id", session.id)
            .put("start_ms", eventStartMs)
            .put("end_ms", eventEndMs)
            .put("title", title)
            .put("summary", fullText)
            .put("type_id", "journal")
            .put("color", "#61BBA6")
            .put("is_demo", 0)
            .put("speaker_names", speakerNamesArray)
            .put("unassigned_labels", distinctSpeakers.size)
            .put("segments", segmentsArray)
            .put("updated_ms", System.currentTimeMillis())

        val eventEntry = CacheEntry(
            kind = "events",
            id = chunk.id,
            sortMs = eventStartMs,
            endMs = eventEndMs,
            payload = eventPayload.toString()
        )

        val entriesToSave = mutableListOf(eventEntry)

        if (fullText.length >= 8) {
            val memoryPayload = JSONObject()
                .put("id", chunk.id)
                .put("event_id", chunk.id)
                .put("text", fullText)
                .put("confirmed", 0)
                .put("created_ms", System.currentTimeMillis())

            entriesToSave.add(
                CacheEntry(
                    kind = "memories",
                    id = chunk.id,
                    sortMs = eventStartMs,
                    endMs = 0,
                    payload = memoryPayload.toString()
                )
            )
        }

        for (speakerLabel in distinctSpeakers) {
            val speakerPayload = JSONObject()
                .put("id", speakerLabel)
                .put("name", "Speaker $speakerLabel")
            entriesToSave.add(
                CacheEntry(
                    kind = "speakers",
                    id = speakerLabel,
                    sortMs = 0,
                    endMs = 0,
                    payload = speakerPayload.toString()
                )
            )
        }

        dao.putCache(entriesToSave)
        dao.markUpload(chunk.id, "completed")
        true
    }
}
