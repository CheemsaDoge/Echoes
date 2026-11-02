package dev.echoledger.app.network

import android.content.Context
import androidx.work.*
import dev.echoledger.app.EchoApp
import kotlinx.coroutines.CancellationException
import org.json.JSONObject
import java.util.concurrent.TimeUnit

object UploadScheduler {
    fun enqueue(context: Context) {
        val request = OneTimeWorkRequestBuilder<UploadWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 15, TimeUnit.SECONDS).build()
        WorkManager.getInstance(context).enqueueUniqueWork("drain-audio", ExistingWorkPolicy.KEEP, request)
    }
    fun scheduleRecovery(context: Context) {
        val request = PeriodicWorkRequestBuilder<UploadWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("repair-upload-queue", ExistingPeriodicWorkPolicy.KEEP, request)
    }
}

class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val graph = (applicationContext as EchoApp).graph
        graph.ready.await()
        val repo = graph.repository
        if (graph.prefs.token.isBlank() || graph.prefs.baseUrl.isBlank()) return Result.success()
        val deadline = android.os.SystemClock.elapsedRealtime() + 8 * 60 * 1000
        try {
            for (session in repo.dao.sessions()) {
                if (isStopped || android.os.SystemClock.elapsedRealtime() > deadline) return Result.retry()
                try {
                    if (!session.remoteCreated) {
                        repo.api.call("/v1/sessions", "POST", JSONObject().put("id", session.id)
                            .put("started_at_ms", session.startedAtMs).put("timezone_offset_min", session.timezoneOffsetMin)
                            .put("purpose", session.purpose).put("device_id", "android"))
                        repo.dao.markCreated(session.id)
                    }
                    for (chunk in repo.dao.pending(session.id)) {
                        if (isStopped || android.os.SystemClock.elapsedRealtime() > deadline) return Result.retry()
                        try { repo.api.upload(chunk); repo.dao.markUpload(chunk.id, "uploaded") }
                        catch (e: ApiFailure) {
                            if (e.status in listOf(409, 413, 422)) {
                                repo.dao.markUpload(chunk.id, "rejected", e.message.orEmpty())
                                repo.message.value = "A chunk needs attention: ${e.message}"
                            } else throw e
                        }
                    }
                    val current = repo.dao.session(session.id) ?: continue
                    if (current.state in listOf("stopped", "interrupted") && !current.remoteClosed && repo.dao.pending(session.id).isEmpty()) {
                        repo.api.call("/v1/sessions/${session.id}/close", "POST", JSONObject()
                            .put("final_seq", current.finalSeq ?: -1).put("interrupted", current.state == "interrupted"))
                        repo.dao.markClosed(session.id)
                    }
                } catch (e: ApiFailure) {
                    if (e.status == 410) repo.dao.tombstone(session.id) else throw e
                }
            }
            return Result.success()
        } catch (cancel: CancellationException) { throw cancel }
        catch (e: Exception) {
            repo.message.value = "Upload pending: ${e.message?.take(160) ?: e.javaClass.simpleName}"
            return Result.retry()
        }
    }
}
