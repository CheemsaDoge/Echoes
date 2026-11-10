package net.hemory.app.worker

import org.json.JSONObject

enum class UploadEntryState(val wire: String) {
    Pending("pending"),
    Uploading("uploading"),
    Completed("completed"),
    FailedRecoverable("failed_recoverable"),
    FailedPermanent("failed_permanent");

    companion object {
        fun fromWire(wire: String): UploadEntryState =
            values().firstOrNull { it.wire.equals(wire, ignoreCase = true) } ?: Pending
    }
}

data class UploadError(
    val code: String,
    val message: String
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("code", code)
        put("message", message)
    }

    companion object {
        fun fromJson(json: JSONObject?): UploadError? {
            if (json == null) return null
            return UploadError(
                code = json.optString("code", "UNKNOWN"),
                message = json.optString("message", "")
            )
        }
    }
}

data class UploadSuccess(
    val recordingId: String,
    val artifactId: String
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("recordingId", recordingId)
        put("artifactId", artifactId)
    }

    companion object {
        fun fromJson(json: JSONObject?): UploadSuccess? {
            if (json == null) return null
            return UploadSuccess(
                recordingId = json.optString("recordingId", ""),
                artifactId = json.optString("artifactId", "")
            )
        }
    }
}

data class UploadQueueEntry(
    val uploadId: String,
    val recordingId: String,
    val tenantId: String,
    val vaultId: String,
    val filePath: String,
    val metaPath: String,
    val fileSizeBytes: Long,
    val createdAtMs: Long,
    val status: UploadEntryState = UploadEntryState.Pending,
    val retryCount: Int = 0,
    val authRetryCount: Int = 0,
    val nextRetryAtMs: Long? = null,
    val lastAttemptAtMs: Long = 0L,
    val recordingStartedAtMs: Long = 0L,
    val sha256: String? = null,
    val captureTimezone: String? = null,
    val source: String = "ambient",
    val clientUploadId: String? = null,
    val lastError: UploadError? = null,
    val success: UploadSuccess? = null,
    val sidecarUploadedSynced: Boolean = false
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("uploadId", uploadId)
        put("recordingId", recordingId)
        put("tenantId", tenantId)
        put("vaultId", vaultId)
        put("filePath", filePath)
        put("metaPath", metaPath)
        put("fileSizeBytes", fileSizeBytes)
        put("createdAt", createdAtMs)
        put("state", status.wire)
        put("retryCount", retryCount)
        put("authRetryCount", authRetryCount)
        nextRetryAtMs?.let { put("nextRetryAt", it) }
        put("lastAttemptAt", lastAttemptAtMs)
        put("recordingStartedAt", recordingStartedAtMs)
        sha256?.let { put("sha256", it) }
        captureTimezone?.let { put("captureTimezone", it) }
        put("source", source)
        clientUploadId?.let { put("clientUploadId", it) }
        lastError?.let { put("lastError", it.toJson()) }
        success?.let { put("success", it.toJson()) }
        put("sidecarUploadedSynced", sidecarUploadedSynced)
    }

    companion object {
        fun fromJson(json: JSONObject): UploadQueueEntry {
            return UploadQueueEntry(
                uploadId = json.getString("uploadId"),
                recordingId = json.optString("recordingId", ""),
                tenantId = json.getString("tenantId"),
                vaultId = json.getString("vaultId"),
                filePath = json.getString("filePath"),
                metaPath = json.optString("metaPath", ""),
                fileSizeBytes = json.optLong("fileSizeBytes", 0L),
                createdAtMs = json.optLong("createdAt", System.currentTimeMillis()),
                status = UploadEntryState.fromWire(json.optString("state", "pending")),
                retryCount = json.optInt("retryCount", 0),
                authRetryCount = json.optInt("authRetryCount", 0),
                nextRetryAtMs = if (json.has("nextRetryAt")) json.getLong("nextRetryAt") else null,
                lastAttemptAtMs = json.optLong("lastAttemptAt", 0L),
                recordingStartedAtMs = json.optLong("recordingStartedAt", 0L),
                sha256 = json.optString("sha256").takeIf { it.isNotEmpty() },
                captureTimezone = json.optString("captureTimezone").takeIf { it.isNotEmpty() },
                source = json.optString("source", "ambient"),
                clientUploadId = json.optString("clientUploadId").takeIf { it.isNotEmpty() },
                lastError = UploadError.fromJson(json.optJSONObject("lastError")),
                success = UploadSuccess.fromJson(json.optJSONObject("success")),
                sidecarUploadedSynced = json.optBoolean("sidecarUploadedSynced", false)
            )
        }
    }
}
