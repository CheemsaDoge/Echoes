package dev.echoledger.app.data

import android.content.Context
import android.util.AtomicFile
import dev.echoledger.core.SAMPLE_RATE
import dev.echoledger.core.WavFile
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.UUID
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

        // Clean up internal cached export variants for this session and any aggregate exports containing it
        val exportDir = File(context.cacheDir, "exports")
        val idPrefix = sessionId.take(6)
        exportDir.listFiles()?.forEach { f ->
            if (f.name.contains(idPrefix) || f.name.contains(sessionId) || f.name.startsWith("echo_all_chunks_")) {
                f.delete()
            }
        }
    }

    suspend fun buildSessionManifest(sessionId: String): JSONObject {
        val dir = folder(sessionId)
        val session = dao.session(sessionId)
        val startedAtMs = session?.startedAtMs ?: runCatching {
            JSONObject(File(dir, "session.json").readText()).getLong("started_at_ms")
        }.getOrDefault(System.currentTimeMillis())
        val startedAtIso = Instant.ofEpochMilli(startedAtMs).toString()
        val tzOffset = session?.timezoneOffsetMin ?: runCatching {
            JSONObject(File(dir, "session.json").readText()).getInt("timezone_offset_min")
        }.getOrDefault(0)
        val purpose = session?.purpose ?: runCatching {
            JSONObject(File(dir, "session.json").readText()).getString("purpose")
        }.getOrDefault("journal")
        val state = session?.state ?: "finished"
        val finalSeq = session?.finalSeq

        val sessionObj = JSONObject().apply {
            put("id", sessionId)
            put("started_at_ms", startedAtMs)
            put("started_at_iso", startedAtIso)
            put("timezone_offset_min", tzOffset)
            put("purpose", purpose)
            put("state", state)
            if (finalSeq != null) put("final_seq", finalSeq) else put("final_seq", JSONObject.NULL)
        }

        val chunksArray = JSONArray()
        val dbChunks = dao.chunks(sessionId).sortedBy { it.seq }.filter {
            val f = File(it.path)
            f.exists() && f.length() > 44
        }
        val chunksList = if (dbChunks.isNotEmpty()) {
            dbChunks
        } else {
            val sidecars = dir.listFiles()?.filter { it.name.endsWith(".chunk.json") }?.sortedBy { it.name } ?: emptyList()
            sidecars.mapNotNull { sidecar ->
                runCatching {
                    val meta = JSONObject(sidecar.readText())
                    val stem = sidecar.name.removeSuffix(".chunk.json")
                    val wav = File(dir, "$stem.wav")
                    if (!wav.exists() || wav.length() <= 44) return@runCatching null
                    val count = ((wav.length() - 44) / 2).toInt()
                    LocalChunk(
                        meta.getString("id"),
                        meta.getString("session_id"),
                        meta.getInt("seq"),
                        meta.getLong("start_sample"),
                        count,
                        wav.absolutePath,
                        checksum(wav),
                        meta.getLong("start_monotonic_ns"),
                        meta.optLong("clock_drift_ms", 0)
                    )
                }.getOrNull()
            }
        }

        for (c in chunksList) {
            val seqFormatted = "%08d".format(c.seq)
            val wavFile = "$seqFormatted.wav"
            val metaFile = "$seqFormatted.chunk.json"
            val durationMs = c.sampleCount.toLong() * 1000L / SAMPLE_RATE
            val startUtcMs = startedAtMs + (c.startSample * 1000L / SAMPLE_RATE)
            val endUtcMs = startUtcMs + durationMs

            val cObj = JSONObject().apply {
                put("id", c.id)
                put("seq", c.seq)
                put("wav_file", wavFile)
                put("meta_file", metaFile)
                put("start_sample", c.startSample)
                put("sample_count", c.sampleCount)
                put("duration_ms", durationMs)
                put("start_utc_ms", startUtcMs)
                put("end_utc_ms", endUtcMs)
                put("start_monotonic_ns", c.startMonotonicNs)
                put("clock_drift_ms", c.clockDriftMs)
                put("sha256", c.sha256)
            }
            chunksArray.put(cObj)
        }

        return JSONObject().apply {
            put("session", sessionObj)
            put("chunks", chunksArray)
        }
    }

    suspend fun exportMergedWav(sessionId: String): File = withContext(Dispatchers.IO) {
        val session = dao.session(sessionId)
        val startedAtMs = session?.startedAtMs ?: runCatching {
            JSONObject(File(folder(sessionId), "session.json").readText()).getLong("started_at_ms")
        }.getOrDefault(System.currentTimeMillis())
        val timestamp = Instant.ofEpochMilli(startedAtMs).atZone(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"))
        val idPrefix = sessionId.take(6)
        val exportDir = File(context.cacheDir, "exports").apply { mkdirs() }
        val outputFile = File(exportDir, "echo_${timestamp}_${idPrefix}.wav")

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
        require(totalPcmBytes <= 2_147_483_600L) {
            "录音总大小超过单 WAV 文件大小限制 (~37小时)，请使用分块 ZIP 格式导出"
        }

        val tempFile = File(exportDir, "tmp_${UUID.randomUUID()}.wav")
        try {
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
        } finally {
            if (tempFile.exists()) tempFile.delete()
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
        val idPrefix = sessionId.take(6)
        val exportDir = File(context.cacheDir, "exports").apply { mkdirs() }
        val outputFile = File(exportDir, "echo_${timestamp}_${idPrefix}_chunks.zip")
        val tempFile = File(exportDir, "tmp_${UUID.randomUUID()}.zip")

        val manifest = buildSessionManifest(sessionId)

        val filesToZip = dir.listFiles()?.filter { f ->
            f.isFile && (f.name.endsWith(".wav") || f.name.endsWith(".chunk.json") || f.name == "session.json")
        }?.sortedBy { it.name } ?: emptyList()

        require(filesToZip.isNotEmpty()) { "会话目录下没有可打包的文件" }

        try {
            ZipOutputStream(tempFile.outputStream().buffered()).use { zipOut ->
                // Write manifest.json at ZIP root
                val manifestEntry = ZipEntry("manifest.json")
                manifestEntry.time = System.currentTimeMillis()
                zipOut.putNextEntry(manifestEntry)
                zipOut.write(manifest.toString(2).toByteArray(Charsets.UTF_8))
                zipOut.closeEntry()

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
        } finally {
            if (tempFile.exists()) tempFile.delete()
        }
        outputFile
    }

    suspend fun exportAllSessionsZip(): File = withContext(Dispatchers.IO) {
        val sessions = dao.sessions()
        val exportDir = File(context.cacheDir, "exports").apply { mkdirs() }
        val timestamp = Instant.now().atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"))
        val outputFile = File(exportDir, "echo_all_chunks_${timestamp}.zip")
        val tempFile = File(exportDir, "tmp_${UUID.randomUUID()}.zip")

        val indexObj = JSONObject().apply {
            put("exported_at_ms", System.currentTimeMillis())
            put("exported_at_iso", Instant.now().toString())
            put("total_sessions", sessions.size)
        }
        val sessionSummaries = JSONArray()

        try {
            ZipOutputStream(tempFile.outputStream().buffered()).use { zipOut ->
                for (s in sessions) {
                    val sDir = folder(s.id)
                    if (!sDir.exists()) continue
                    val manifest = buildSessionManifest(s.id)
                    val chunksArray = manifest.optJSONArray("chunks")
                    val chunkCount = chunksArray?.length() ?: 0
                    var totalSamples = 0L
                    if (chunksArray != null) {
                        for (i in 0 until chunksArray.length()) {
                            totalSamples += chunksArray.getJSONObject(i).optLong("sample_count", 0L)
                        }
                    }

                    sessionSummaries.put(JSONObject().apply {
                        put("id", s.id)
                        put("started_at_ms", s.startedAtMs)
                        put("started_at_iso", Instant.ofEpochMilli(s.startedAtMs).toString())
                        put("chunk_count", chunkCount)
                        put("duration_ms", totalSamples * 1000L / SAMPLE_RATE)
                    })

                    // Write s.id/manifest.json
                    val mEntry = ZipEntry("${s.id}/manifest.json")
                    mEntry.time = System.currentTimeMillis()
                    zipOut.putNextEntry(mEntry)
                    zipOut.write(manifest.toString(2).toByteArray(Charsets.UTF_8))
                    zipOut.closeEntry()

                    // Write other files in sDir
                    val files = sDir.listFiles()?.filter { f ->
                        f.isFile && (f.name.endsWith(".wav") || f.name.endsWith(".chunk.json") || f.name == "session.json")
                    }?.sortedBy { it.name } ?: emptyList()

                    for (f in files) {
                        val entry = ZipEntry("${s.id}/${f.name}")
                        entry.time = f.lastModified()
                        zipOut.putNextEntry(entry)
                        f.inputStream().buffered().use { it.copyTo(zipOut) }
                        zipOut.closeEntry()
                    }
                }

                indexObj.put("sessions", sessionSummaries)
                val indexEntry = ZipEntry("export_index.json")
                indexEntry.time = System.currentTimeMillis()
                zipOut.putNextEntry(indexEntry)
                zipOut.write(indexObj.toString(2).toByteArray(Charsets.UTF_8))
                zipOut.closeEntry()

                zipOut.finish()
            }

            if (outputFile.exists()) outputFile.delete()
            if (!tempFile.renameTo(outputFile)) {
                tempFile.copyTo(outputFile, overwrite = true)
                tempFile.delete()
            }
        } finally {
            if (tempFile.exists()) tempFile.delete()
        }
        outputFile
    }
}
