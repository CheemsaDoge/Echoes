package dev.echoledger.app.data

import android.content.Context
import android.util.AtomicFile
import dev.echoledger.core.SAMPLE_RATE
import dev.echoledger.core.WavFile
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class LocalStore(val context: Context, val dao: JournalDao) {
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

    fun getSceneTag(sessionId: String): String {
        val tagFile = File(folder(sessionId), "scene_tag.txt")
        if (tagFile.exists()) {
            val text = tagFile.readText().trim()
            if (text.isNotBlank()) return text
        }
        return "未分类"
    }

    fun setSceneTag(sessionId: String, tag: String) {
        val tagFile = File(folder(sessionId), "scene_tag.txt")
        tagFile.writeText(tag.trim())
    }

    suspend fun exportMergedWav(sessionId: String): File = withContext(Dispatchers.IO) {
        val session = dao.session(sessionId)
        val startedAtMs = session?.startedAtMs ?: runCatching {
            JSONObject(File(folder(sessionId), "session.json").readText()).getLong("started_at_ms")
        }.getOrDefault(System.currentTimeMillis())
        val timestamp = Instant.ofEpochMilli(startedAtMs).atZone(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"))
        val sceneTag = getSceneTag(sessionId)
        val idPrefix = sessionId.take(6)
        val exportDir = File(context.cacheDir, "exports").apply { mkdirs() }
        val outputFile = File(exportDir, "echo_${timestamp}_${sceneTag}_${idPrefix}.wav")

        val chunks = dao.chunks(sessionId)
        val wavFiles = if (chunks.isNotEmpty()) {
            chunks.map { File(it.path) }
        } else {
            val dir = folder(sessionId)
            dir.listFiles()?.filter { it.extension == "wav" && !it.name.endsWith(".wav.part") }
                ?.sortedBy { it.name } ?: emptyList()
        }

        require(wavFiles.isNotEmpty()) { "没有可合并导出的录音分块" }
        for (f in wavFiles) {
            require(f.exists() && f.length() > 44) { "音频分块无效或损坏: ${f.name}" }
        }

        val totalPcmBytes = wavFiles.sumOf { it.length() - 44 }
        val tempFile = File(exportDir, "${outputFile.name}.tmp")
        tempFile.outputStream().buffered().use { out ->
            val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
            header.put("RIFF".toByteArray(Charsets.US_ASCII))
            header.putInt((36 + totalPcmBytes).toInt())
            header.put("WAVEfmt ".toByteArray(Charsets.US_ASCII))
            header.putInt(16)
            header.putShort(1.toShort())
            header.putShort(1.toShort())
            header.putInt(SAMPLE_RATE)
            header.putInt(SAMPLE_RATE * 2)
            header.putShort(2.toShort())
            header.putShort(16.toShort())
            header.put("data".toByteArray(Charsets.US_ASCII))
            header.putInt(totalPcmBytes.toInt())
            out.write(header.array())

            val buffer = ByteArray(65536)
            for (wav in wavFiles) {
                wav.inputStream().buffered().use { input ->
                    var skipped = 0L
                    while (skipped < 44) {
                        val n = input.skip(44 - skipped)
                        if (n <= 0) break
                        skipped += n
                    }
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        out.write(buffer, 0, read)
                    }
                }
            }
            out.flush()
        }

        if (outputFile.exists()) outputFile.delete()
        if (!tempFile.renameTo(outputFile)) {
            tempFile.copyTo(outputFile, overwrite = true)
            tempFile.delete()
        }
        outputFile
    }

    suspend fun exportSessionZip(sessionId: String): File = withContext(Dispatchers.IO) {
        val dir = folder(sessionId)
        require(dir.exists() && dir.isDirectory) { "会话目录不存在" }
        val session = dao.session(sessionId)
        val startedAtMs = session?.startedAtMs ?: runCatching {
            JSONObject(File(dir, "session.json").readText()).getLong("started_at_ms")
        }.getOrDefault(System.currentTimeMillis())
        val timestamp = Instant.ofEpochMilli(startedAtMs).atZone(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"))
        val sceneTag = getSceneTag(sessionId)
        val idPrefix = sessionId.take(6)
        val exportDir = File(context.cacheDir, "exports").apply { mkdirs() }
        val outputFile = File(exportDir, "echo_${timestamp}_${sceneTag}_${idPrefix}_chunks.zip")
        val tempFile = File(exportDir, "${outputFile.name}.tmp")

        val filesToZip = dir.listFiles()?.filter { f ->
            f.isFile && (f.name.endsWith(".wav") || f.name.endsWith(".chunk.json") ||
                         f.name == "session.json" || f.name == "scene_tag.txt")
        }?.sortedBy { it.name } ?: emptyList()

        require(filesToZip.isNotEmpty()) { "会话目录下没有可打包的文件" }

        ZipOutputStream(tempFile.outputStream().buffered()).use { zipOut ->
            for (f in filesToZip) {
                val entry = ZipEntry(f.name)
                entry.time = f.lastModified()
                zipOut.putNextEntry(entry)
                f.inputStream().buffered().use { it.copyTo(zipOut) }
                zipOut.closeEntry()
            }
            zipOut.finish()
        }

        if (outputFile.exists()) outputFile.delete()
        if (!tempFile.renameTo(outputFile)) {
            tempFile.copyTo(outputFile, overwrite = true)
            tempFile.delete()
        }
        outputFile
    }
}
