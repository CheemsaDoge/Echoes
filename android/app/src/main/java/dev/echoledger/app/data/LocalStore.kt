package dev.echoledger.app.data

import android.content.Context
import android.util.AtomicFile
import dev.echoledger.core.WavFile
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

class LocalStore(context: Context, val dao: JournalDao) {
    val root = File(context.filesDir, "recordings").apply { mkdirs() }
    fun folder(sessionId: String) = File(root, sessionId).apply { mkdirs() }
    fun writeJson(file: File, json: JSONObject) {
        val atomic = AtomicFile(file)
        val stream = atomic.startWrite()
        try { stream.write(json.toString().toByteArray()); atomic.finishWrite(stream) }
        catch (e: Exception) { atomic.failWrite(stream); throw e }
    }
    suspend fun start(session: LocalSession) {
        writeJson(File(folder(session.id), "session.json"), JSONObject()
            .put("id", session.id).put("started_at_ms", session.startedAtMs)
            .put("timezone_offset_min", session.timezoneOffsetMin).put("purpose", session.purpose))
        dao.addSession(session)
    }
    fun checksum(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(65536)
            while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
        }
        return digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
    }
    suspend fun register(wav: File, meta: JSONObject): LocalChunk {
        val count = ((wav.length() - 44) / 2).toInt()
        require(count > 0)
        val chunk = LocalChunk(meta.getString("id"), meta.getString("session_id"), meta.getInt("seq"),
            meta.getLong("start_sample"), count, wav.absolutePath, checksum(wav),
            meta.getLong("start_monotonic_ns"), meta.optLong("clock_drift_ms", 0))
        dao.addChunk(chunk)
        return chunk
    }
    /** Recovery runs once per process before capture/upload. Never restart the microphone here. */
    suspend fun recover() {
        root.listFiles()?.filter { it.isDirectory }?.forEach { dir ->
            val sessionFile = File(dir, "session.json")
            if (!sessionFile.exists()) return@forEach
            val info = JSONObject(sessionFile.readText())
            val id = info.getString("id")
            dao.addSession(LocalSession(id, info.getLong("started_at_ms"), info.getInt("timezone_offset_min"), info.getString("purpose")))
            if (dao.session(id)?.state == "deleted") return@forEach
            dir.listFiles()?.filter { it.name.endsWith(".chunk.json") }?.sortedBy { it.name }?.forEach { sidecar ->
                val meta = JSONObject(sidecar.readText())
                val stem = sidecar.name.removeSuffix(".chunk.json")
                val wav = File(dir, "$stem.wav")
                val part = File(dir, "$stem.wav.part")
                if (part.exists()) {
                    if (wav.exists()) part.delete() else WavFile.recover(part)
                }
                if (wav.exists() && wav.length() > 44) register(wav, meta)
            }
            if (dao.session(id)?.state in listOf("recording", "needs_recovery")) {
                val last = dao.chunks(id).maxOfOrNull { it.seq } ?: -1
                dao.finishSession(id, last, "interrupted")
            }
        }
    }
    suspend fun remove(sessionId: String) {
        // Tombstone before deleting files so process death cannot resurrect a queued upload.
        dao.tombstone(sessionId)
        val directory = File(root, sessionId)
        check(!directory.exists() || directory.deleteRecursively()) { "Could not remove all local audio files" }
        dao.deleteChunks(sessionId) // Keep the local tombstone to prevent recovery/re-upload.
    }
}
