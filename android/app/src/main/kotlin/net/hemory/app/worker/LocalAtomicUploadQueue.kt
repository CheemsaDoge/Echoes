package net.hemory.app.worker

import android.content.Context
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

class LocalAtomicUploadQueue(
    val queueFile: File,
    private val clock: () -> Long = { System.currentTimeMillis() }
) {
    constructor(context: Context) : this(
        File(context.filesDir, "upload/upload_queue.json")
    )

    private val lock = Any()
    private val entriesMap = LinkedHashMap<String, UploadQueueEntry>()

    init {
        load()
    }

    fun get(uploadId: String): UploadQueueEntry? = synchronized(lock) {
        entriesMap[uploadId]
    }

    fun list(): List<UploadQueueEntry> = synchronized(lock) {
        entriesMap.values.toList()
    }

    fun enqueue(entry: UploadQueueEntry): Boolean = synchronized(lock) {
        entriesMap[entry.uploadId] = entry
        persistLocked()
    }

    fun update(uploadId: String, block: (UploadQueueEntry) -> UploadQueueEntry): UploadQueueEntry? = synchronized(lock) {
        val existing = entriesMap[uploadId] ?: return null
        val updated = block(existing)
        entriesMap[uploadId] = updated
        persistLocked()
        updated
    }

    fun remove(uploadId: String): Boolean = synchronized(lock) {
        if (entriesMap.remove(uploadId) != null) {
            persistLocked()
            true
        } else {
            false
        }
    }

    /**
     * 7-day retention pruning:
     * Removes completed entries older than 7 days and purges physical audio and meta files.
     */
    fun pruneCompleted(cutoffMs: Long = clock() - (7 * 86_400_000L)): List<String> = synchronized(lock) {
        val toPrune = entriesMap.values.filter { entry ->
            val timestamp = if (entry.lastAttemptAtMs > 0L) entry.lastAttemptAtMs else entry.createdAtMs
            entry.status == UploadEntryState.Completed && timestamp < cutoffMs
        }

        if (toPrune.isEmpty()) return emptyList()

        val prunedIds = ArrayList<String>()
        for (entry in toPrune) {
            // Delete physical .aac audio file
            val audioFile = File(entry.filePath)
            if (audioFile.exists()) {
                audioFile.delete()
            }
            // Delete companion .meta sidecar file
            if (entry.metaPath.isNotEmpty()) {
                val metaFile = File(entry.metaPath)
                if (metaFile.exists()) {
                    metaFile.delete()
                }
            }
            entriesMap.remove(entry.uploadId)
            prunedIds.add(entry.uploadId)
        }

        persistLocked()
        safeLog('i', "LocalAtomicUploadQueue", "pruneCompleted: Purged ${prunedIds.size} completed entries older than 7 days")
        prunedIds
    }

    fun load() {
        synchronized(lock) {
            entriesMap.clear()
            if (!queueFile.exists()) {
                val tmpFile = File(queueFile.parentFile, "${queueFile.name}.tmp")
                if (tmpFile.exists()) {
                    if (!tmpFile.renameTo(queueFile)) {
                        queueFile.delete()
                        tmpFile.renameTo(queueFile)
                    }
                } else {
                    return
                }
            }

            try {
                val content = queueFile.readText(Charsets.UTF_8).trim()
                if (content.isEmpty()) return

                val rootJson = JSONObject(content)
                val entriesArray = rootJson.optJSONArray("entries") ?: JSONArray()
                for (i in 0 until entriesArray.length()) {
                    val item = entriesArray.getJSONObject(i)
                    val entry = UploadQueueEntry.fromJson(item)
                    entriesMap[entry.uploadId] = entry
                }
                safeLog('d', "LocalAtomicUploadQueue", "load: Successfully loaded ${entriesMap.size} queue entries")
            } catch (e: Exception) {
                safeLog('e', "LocalAtomicUploadQueue", "load: Queue file corrupted, backing up and resetting: ${e.message}")
                try {
                    val corruptBackup = File(queueFile.parentFile, "${queueFile.name}.corrupt.${clock()}")
                    queueFile.renameTo(corruptBackup)
                } catch (_: Exception) {}
                entriesMap.clear()
                persistLocked()
            }
        }
    }

    private fun persistLocked(): Boolean {
        val parent = queueFile.parentFile
        if (parent != null && !parent.exists()) {
            parent.mkdirs()
        }

        val tmpFile = File(parent, "${queueFile.name}.tmp")
        val rootJson = JSONObject().apply {
            put("schemaVersion", 1)
            val array = JSONArray()
            for (entry in entriesMap.values) {
                array.put(entry.toJson())
            }
            put("entries", array)
        }

        try {
            FileOutputStream(tmpFile).use { fos ->
                val bytes = rootJson.toString(2).toByteArray(Charsets.UTF_8)
                fos.write(bytes)
                fos.flush()
                // Force sync down to physical flash storage barrier
                runCatching { fos.fd.sync() }
            }

            // Atomically replace target file without pre-delete race window
            if (!tmpFile.renameTo(queueFile)) {
                if (queueFile.exists()) {
                    queueFile.delete()
                }
                if (!tmpFile.renameTo(queueFile)) {
                    throw IOException("renameTo failed: ${tmpFile.path} -> ${queueFile.path}")
                }
            }
            return true
        } catch (e: Exception) {
            safeLog('e', "LocalAtomicUploadQueue", "persist: Failed to atomically save queue: ${e.message}", e)
            if (tmpFile.exists()) tmpFile.delete()
            return false
        }
    }

    companion object {
        fun safeLog(level: Char, tag: String, msg: String, t: Throwable? = null) {
            try {
                when (level) {
                    'd' -> Log.d(tag, msg, t)
                    'i' -> Log.i(tag, msg, t)
                    'w' -> Log.w(tag, msg, t)
                    'e' -> Log.e(tag, msg, t)
                    else -> Log.i(tag, msg, t)
                }
            } catch (_: RuntimeException) {
                println("[$level/$tag] $msg ${t?.message ?: ""}")
            }
        }
    }
}
