package dev.echoledger.app.network

import android.content.Context
import androidx.work.*
import dev.echoledger.app.EchoApp
import dev.echoledger.app.data.StandaloneTranscriber
import kotlinx.coroutines.CancellationException
import java.util.concurrent.TimeUnit

object StandaloneScheduler {
    fun enqueue(context: Context) {
        val request = OneTimeWorkRequestBuilder<StandaloneWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 15, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork("standalone-drain-audio", ExistingWorkPolicy.KEEP, request)
    }

    fun scheduleRecovery(context: Context) {
        val request = PeriodicWorkRequestBuilder<StandaloneWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("standalone-recovery", ExistingPeriodicWorkPolicy.KEEP, request)
    }
}

class StandaloneWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val graph = (applicationContext as EchoApp).graph
        graph.ready.await()
        val prefs = graph.prefs
        if (prefs.operationMode == "server") return Result.success()
        if (prefs.aiApiKey.isBlank()) return Result.success()

        val transcriber = StandaloneTranscriber(graph.database.dao(), prefs)
        val deadline = android.os.SystemClock.elapsedRealtime() + 8 * 60 * 1000L
        try {
            for (session in graph.database.dao().sessions()) {
                if (isStopped || android.os.SystemClock.elapsedRealtime() > deadline) return Result.retry()
                val pendingChunks = graph.database.dao().pending(session.id)
                for (chunk in pendingChunks) {
                    if (isStopped || android.os.SystemClock.elapsedRealtime() > deadline) return Result.retry()
                    try {
                        transcriber.processChunk(chunk)
                    } catch (e: Exception) {
                        if (isStopped) return Result.retry()
                        graph.database.dao().markUpload(chunk.id, "pending", e.message?.take(180) ?: "Transcription error")
                        return Result.retry()
                    }
                }
            }
            return Result.success()
        } catch (c: CancellationException) {
            throw c
        } catch (e: Exception) {
            return Result.retry()
        }
    }
}
