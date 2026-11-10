package net.hemory.app.native

import android.content.Context
import android.os.BatteryManager
import android.content.Intent
import android.content.IntentFilter
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.util.TimeZone

data class SegmentArtifacts(
    val recordingId: String,
    val segmentIndex: Int,
    val segmentId: Long,
    val audioFile: File,
    val metaFile: File,
    val durationMs: Long,
    val sizeBytes: Long,
    val sha256Hex: String,
    val splitReason: String
)

class SegmentFileSink(
    private val context: Context,
    val recordingId: String,
    val epochMilli: Long,
    val segmentIndex: Int
) {
    private val dir = File(context.filesDir, "Recordings/$recordingId/segments").apply { mkdirs() }
    val audioFile = File(dir, "$epochMilli.aac")
    val metaFile = File(dir, "$epochMilli.meta")
    private val fos = FileOutputStream(audioFile, true)
    private val sha256 = MessageDigest.getInstance("SHA-256")
    private var totalBytes: Long = 0L
    private var aacFrameCount: Long = 0L
    private val startedAtInstant = Instant.now()

    @Synchronized
    fun writeFrame(adtsPacket: ByteArray) {
        fos.write(adtsPacket)
        sha256.update(adtsPacket)
        totalBytes += adtsPacket.size
        aacFrameCount++
    }

    @Synchronized
    fun finalize(
        tenantId: String,
        vaultId: String,
        userId: String,
        splitReason: String,
        silenceMsBeforeCut: Long?
    ): SegmentArtifacts {
        fos.flush()
        runCatching { fos.fd.sync() }
        fos.close()

        val finalizedAtInstant = Instant.now()
        // AAC frame is 1024 samples = 64ms at 16kHz
        val durationMs = if (aacFrameCount > 0) {
            (aacFrameCount * 1024L * 1000L) / 16000L
        } else {
            0L
        }
        val sha256Hex = sha256.digest().joinToString("") { "%02x".format(it) }

        // Read battery state
        val (batteryLevel, isCharging) = getBatteryState(context)

        // Construct TransferMetadata JSON (schemaVersion: 1)
        val iso = DateTimeFormatter.ISO_INSTANT
        val metaJson = JSONObject().apply {
            put("schemaVersion", 1)
            put("tenantId", tenantId)
            put("vaultId", vaultId)
            put("captureTimezone", TimeZone.getDefault().id)
            put("source", "app_default")
            put("captureMode", "continuous")
            put("splitReason", splitReason)
            put("silenceMsBeforeCut", silenceMsBeforeCut ?: JSONObject.NULL)
            put("sessionStartedAt", iso.format(startedAtInstant))
            put("userId", userId)
            put("liveSessionId", recordingId)
            put("segmentIndex", segmentIndex)
            put("segmentState", "upload_ready")
            put("segmentStartedAt", iso.format(startedAtInstant))
            put("segmentFinalizedAt", iso.format(finalizedAtInstant))
            put("sourceDeviceId", getDeviceId(context))
            put("localGrantRevision", 1L)
            // Enhanced hardware metadata fields
            put("sample_rate", 16000)
            put("bitrate_kbps", 48)
            put("channels", 1)
            put("battery_level", batteryLevel)
            put("is_charging", isCharging)
            put("duration_ms", durationMs)
            put("size_bytes", totalBytes)
            put("sha256", sha256Hex)
        }

        // Atomically write .meta file via .tmp
        val tmpMeta = File(metaFile.parentFile, "${metaFile.name}.tmp")
        FileOutputStream(tmpMeta).use { out ->
            out.write(metaJson.toString(2).toByteArray(Charsets.UTF_8))
            out.flush()
            runCatching { out.fd.sync() }
        }
        if (metaFile.exists()) {
            metaFile.delete()
        }
        tmpMeta.renameTo(metaFile)

        return SegmentArtifacts(
            recordingId = recordingId,
            segmentIndex = segmentIndex,
            segmentId = epochMilli,
            audioFile = audioFile,
            metaFile = metaFile,
            durationMs = durationMs,
            sizeBytes = totalBytes,
            sha256Hex = sha256Hex,
            splitReason = splitReason
        )
    }

    fun close() {
        runCatching { fos.close() }
    }

    private fun getBatteryState(context: Context): Pair<Float, Boolean> {
        val filter = IntentFilter(Intent.ACTION_BATTERY_CHANGED)
        val batteryIntent = runCatching { context.registerReceiver(null, filter) }.getOrNull()
        val level = batteryIntent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = batteryIntent?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val pct = if (level >= 0 && scale > 0) level / scale.toFloat() else 1.0f
        val status = batteryIntent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val isCharging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        return Pair(pct, isCharging)
    }

    private fun getDeviceId(context: Context): String {
        return runCatching {
            android.provider.Settings.Secure.getString(
                context.contentResolver,
                android.provider.Settings.Secure.ANDROID_ID
            )
        }.getOrNull() ?: "android_device"
    }
}

class SegmentManifestManager private constructor(
    private val context: Context,
    private val recordingId: String
) {
    companion object {
        private val instances = mutableMapOf<String, SegmentManifestManager>()

        @Synchronized
        fun get(context: Context, recordingId: String): SegmentManifestManager {
            return instances.getOrPut(recordingId) { SegmentManifestManager(context.applicationContext, recordingId) }
        }
    }

    private val manifestFile = File(context.filesDir, "Recordings/$recordingId/manifest.json")
    private val iso = DateTimeFormatter.ISO_INSTANT
    private val root = JSONObject()

    @Synchronized
    fun initialize(createdAtEpochMs: Long) {
        manifestFile.parentFile?.mkdirs()
        root.put("schemaVersion", 1)
        root.put("recordingId", recordingId)
        root.put("createdAt", iso.format(Instant.ofEpochMilli(createdAtEpochMs)))
        root.put("endedAt", JSONObject.NULL)
        root.put("currentState", "recording")
        root.put("failureReason", JSONObject.NULL)
        root.put("lastWriteAt", iso.format(Instant.now()))
        root.put("segments", JSONArray())
        persistAtomic()
    }

    @Synchronized
    fun addSegment(artifacts: SegmentArtifacts) {
        val segments = root.optJSONArray("segments") ?: JSONArray().also { root.put("segments", it) }
        val segObj = JSONObject().apply {
            put("segmentId", artifacts.segmentId)
            put("startAt", iso.format(Instant.ofEpochMilli(artifacts.segmentId)))
            put("endAt", iso.format(Instant.ofEpochMilli(artifacts.segmentId + artifacts.durationMs)))
            put("startReason", if (artifacts.segmentIndex == 0) "initial" else "rollover_elastic")
            put("closeReason", artifacts.splitReason)
            put("terminal", true)
            put("fileRelativePath", "segments/${artifacts.audioFile.name}")
            put("durationMs", artifacts.durationMs)
            put("sizeBytes", artifacts.sizeBytes)
            put("sha256", artifacts.sha256Hex)
        }
        segments.put(segObj)
        root.put("lastWriteAt", iso.format(Instant.now()))
        persistAtomic()
    }

    @Synchronized
    fun finalizeSession(reason: String?) {
        root.put("endedAt", iso.format(Instant.now()))
        root.put("currentState", if (reason == null) "stopped" else "failed")
        root.put("failureReason", reason ?: JSONObject.NULL)
        root.put("lastWriteAt", iso.format(Instant.now()))
        persistAtomic()
    }

    private fun persistAtomic() {
        val parent = manifestFile.parentFile
        if (parent != null && !parent.exists()) {
            parent.mkdirs()
        }
        val tmpFile = File(manifestFile.parentFile, "${manifestFile.name}.tmp")
        FileOutputStream(tmpFile).use { fos ->
            fos.write(root.toString(2).toByteArray(Charsets.UTF_8))
            fos.flush()
            runCatching { fos.fd.sync() }
        }
        if (manifestFile.exists()) {
            manifestFile.delete()
        }
        tmpFile.renameTo(manifestFile)
    }
}
