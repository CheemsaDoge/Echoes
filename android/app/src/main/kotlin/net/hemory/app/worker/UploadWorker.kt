package net.hemory.app.worker

import android.content.Context
import android.util.Log
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

object UploadScheduler {
    fun enqueueSegment(context: Context, uploadId: String) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        val request = OneTimeWorkRequestBuilder<UploadWorker>()
            .setConstraints(constraints)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .setInputData(workDataOf("uploadId" to uploadId))
            .build()

        WorkManager.getInstance(context).enqueueUniqueWork(
            "upload_$uploadId",
            ExistingWorkPolicy.REPLACE,
            request
        )
    }

    fun schedulePeriodicRecovery(context: Context) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        val request = PeriodicWorkRequestBuilder<UploadWorker>(15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .build()

        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            "upload_periodic_recovery",
            ExistingPeriodicWorkPolicy.KEEP,
            request
        )
    }
}

object MultiTenantGuard {
    fun isAuthorized(
        activeTenantId: String?,
        activeVaultId: String?,
        entryTenantId: String,
        entryVaultId: String
    ): Boolean {
        if (activeTenantId.isNullOrEmpty() || activeVaultId.isNullOrEmpty()) return false
        return activeTenantId == entryTenantId && activeVaultId == entryVaultId
    }

    fun applyMismatchPolicy(
        queue: LocalAtomicUploadQueue,
        uploadId: String,
        entry: UploadQueueEntry,
        activeTenantId: String?,
        activeVaultId: String?
    ) {
        LocalAtomicUploadQueue.safeLog(
            'w',
            "UploadWorker",
            "upload_identity_mismatch_skipped_at_send: uploadId=$uploadId, entryTenant=${entry.tenantId}, activeTenant=$activeTenantId, entryVault=${entry.vaultId}, activeVault=$activeVaultId"
        )
        // Strict File Preservation: Under NO circumstances does the worker delete the audio or meta file.
        queue.update(uploadId) {
            it.copy(
                status = UploadEntryState.FailedPermanent,
                lastError = UploadError(
                    code = "identity_mismatch",
                    message = "条目身份与当前登录态不匹配，暂不发送"
                )
            )
        }
    }
}

class UploadWorker(
    appContext: Context,
    workerParams: WorkerParameters
) : CoroutineWorker(appContext, workerParams) {

    private val queue = LocalAtomicUploadQueue(appContext)
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .build()

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val uploadId = inputData.getString("uploadId")
        if (uploadId != null) {
            processSingleUpload(uploadId)
        } else {
            processPendingQueue()
        }
    }

    suspend fun processSingleUpload(uploadId: String): Result {
        val entry = queue.get(uploadId) ?: run {
            Log.w("UploadWorker", "doWork: Entry not found for uploadId=$uploadId, skipping")
            return Result.success()
        }

        if (entry.status == UploadEntryState.Completed) {
            return Result.success()
        }

        // 1. Multi-Tenant Identity Guard Check
        val activeAuth = getActiveCredentials()
        val activeTenantId = activeAuth?.optString("tenantId") ?: ""
        val activeVaultId = activeAuth?.optString("vaultId") ?: ""
        val accessToken = activeAuth?.optString("accessToken") ?: ""
        val baseUrl = activeAuth?.optString("baseUrl")?.trimEnd('/') ?: ""

        if (!MultiTenantGuard.isAuthorized(activeTenantId, activeVaultId, entry.tenantId, entry.vaultId)) {
            MultiTenantGuard.applyMismatchPolicy(queue, uploadId, entry, activeTenantId, activeVaultId)
            return Result.failure()
        }

        // 2. Validate Audio File Presence
        val audioFile = File(entry.filePath)
        if (!audioFile.exists() || audioFile.length() == 0L) {
            Log.e("UploadWorker", "doWork: Audio file missing or empty: ${entry.filePath}")
            queue.update(uploadId) {
                it.copy(
                    status = UploadEntryState.FailedPermanent,
                    lastError = UploadError("FILE_NOT_FOUND", "Audio segment file is missing")
                )
            }
            return Result.failure()
        }

        // 3. Mark state as Uploading
        queue.update(uploadId) {
            it.copy(
                status = UploadEntryState.Uploading,
                lastAttemptAtMs = System.currentTimeMillis()
            )
        }

        // 4. Construct Multipart Request (Contract C2)
        val uploadUrl = "$baseUrl/tenants/${entry.tenantId}/vaults/${entry.vaultId}/recordings/upload"

        val metaJsonString = if (entry.metaPath.isNotEmpty() && File(entry.metaPath).exists()) {
            File(entry.metaPath).readText(Charsets.UTF_8)
        } else {
            JSONObject().apply {
                put("sample_rate", 16000)
                put("channels", 1)
                put("bitrate_kbps", 48)
                put("split_reason", "silence_window")
            }.toString()
        }

        val requestBody = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("started_at", java.time.Instant.ofEpochMilli(entry.recordingStartedAtMs).toString())
            .addFormDataPart("format", "aac")
            .addFormDataPart("source", entry.source)
            .addFormDataPart("capture_timezone", entry.captureTimezone ?: "Asia/Shanghai")
            .addFormDataPart("capture_metadata", metaJsonString)
            .addFormDataPart(
                "file",
                audioFile.name,
                audioFile.asRequestBody("audio/aac".toMediaType())
            )
            .build()

        val request = Request.Builder()
            .url(uploadUrl)
            .header("Authorization", "Bearer $accessToken")
            .post(requestBody)
            .build()

        try {
            httpClient.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                when (response.code) {
                    in 200..201 -> {
                        val json = runCatching { JSONObject(body) }.getOrDefault(JSONObject())
                        val artifactId = json.optString("asset_id", json.optString("recording_id", ""))
                        Log.i("UploadWorker", "doWork: Successfully uploaded uploadId=$uploadId, assetId=$artifactId")

                        queue.update(uploadId) {
                            it.copy(
                                status = UploadEntryState.Completed,
                                success = UploadSuccess(entry.recordingId, artifactId),
                                sidecarUploadedSynced = true
                            )
                        }

                        // Trigger retention pruning
                        queue.pruneCompleted()
                        return Result.success()
                    }
                    401 -> {
                        Log.w("UploadWorker", "doWork: Auth failed (401) on uploadId=$uploadId")
                        queue.update(uploadId) {
                            it.copy(
                                authRetryCount = it.authRetryCount + 1,
                                status = UploadEntryState.FailedRecoverable,
                                lastError = UploadError("UNAUTHORIZED", "Authentication expired")
                            )
                        }
                        return if (runAttemptCount < 5) Result.retry() else Result.failure()
                    }
                    403, 409 -> {
                        Log.w("UploadWorker", "doWork: Lease rejected (${response.code}) on uploadId=$uploadId: $body")
                        queue.update(uploadId) {
                            it.copy(
                                status = UploadEntryState.FailedPermanent,
                                lastError = UploadError("LEASE_REJECTED", body.take(120))
                            )
                        }
                        return Result.failure()
                    }
                    else -> {
                        Log.w("UploadWorker", "doWork: Server error ${response.code} on uploadId=$uploadId: $body")
                        if (runAttemptCount < 5) {
                            queue.update(uploadId) {
                                it.copy(
                                    retryCount = it.retryCount + 1,
                                    status = UploadEntryState.FailedRecoverable,
                                    lastError = UploadError("HTTP_${response.code}", body.take(120))
                                )
                            }
                            return Result.retry()
                        } else {
                            queue.update(uploadId) {
                                it.copy(
                                    status = UploadEntryState.FailedPermanent,
                                    lastError = UploadError("MAX_RETRIES", "Exceeded max upload attempts")
                                )
                            }
                            return Result.failure()
                        }
                    }
                }
            }
        } catch (e: IOException) {
            Log.w("UploadWorker", "doWork: Network error on uploadId=$uploadId: ${e.message}")
            if (runAttemptCount < 5) {
                queue.update(uploadId) {
                    it.copy(
                        retryCount = it.retryCount + 1,
                        status = UploadEntryState.FailedRecoverable,
                        lastError = UploadError("NETWORK_ERROR", e.message ?: "IOException")
                    )
                }
                return Result.retry()
            } else {
                queue.update(uploadId) {
                    it.copy(
                        status = UploadEntryState.FailedPermanent,
                        lastError = UploadError("MAX_RETRIES", e.message ?: "Network failure")
                    )
                }
                return Result.failure()
            }
        }
    }

    private suspend fun processPendingQueue(): Result {
        val pendingEntries = queue.list().filter {
            it.status == UploadEntryState.Pending || it.status == UploadEntryState.FailedRecoverable
        }

        var anyFailed = false
        for (entry in pendingEntries) {
            val res = processSingleUpload(entry.uploadId)
            if (res == Result.retry()) {
                anyFailed = true
            }
        }

        return if (anyFailed) Result.retry() else Result.success()
    }

    private fun getActiveCredentials(): JSONObject? {
        val prefs = applicationContext.getSharedPreferences("connection", Context.MODE_PRIVATE)
        val securePrefs = runCatching { dev.echoledger.app.data.SecurePreferences(applicationContext) }.getOrNull()
        val token = securePrefs?.token?.takeIf { it.isNotEmpty() } ?: prefs.getString("token", "") ?: ""
        val baseUrl = securePrefs?.baseUrl?.takeIf { it.isNotEmpty() } ?: prefs.getString("url", "") ?: ""
        val tenantId = prefs.getString("active_tenant_id", "") ?: ""
        val vaultId = prefs.getString("active_vault_id", "") ?: ""

        if (token.isEmpty() || baseUrl.isEmpty() || tenantId.isEmpty() || vaultId.isEmpty()) {
            return null
        }

        return JSONObject().apply {
            put("accessToken", token)
            put("baseUrl", baseUrl)
            put("tenantId", tenantId)
            put("vaultId", vaultId)
        }
    }
}
