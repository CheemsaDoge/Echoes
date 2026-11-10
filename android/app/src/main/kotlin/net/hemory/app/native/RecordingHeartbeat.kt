package net.hemory.app.native

import android.content.Context
import android.util.Log
import kotlinx.coroutines.*
import java.util.concurrent.atomic.AtomicBoolean

class RecordingHeartbeat(
    private val context: Context,
    private val scheduler: AlarmSchedulerHelper,
    private val scope: CoroutineScope
) {
    companion object {
        const val TAG = "RecordingHeartbeat"
        const val RENEW_INTERVAL_MS: Long = 30_000L // 30 seconds
        const val LEASE_SECONDS: Long = 60L          // 60 seconds lease
    }

    private var heartbeatJob: Job? = null
    private var sessionStartEpochMs: Long = 0L
    private val active = AtomicBoolean(false)

    fun start(recordingId: String) {
        stop(recordingId)
        active.set(true)
        sessionStartEpochMs = System.currentTimeMillis()

        // Immediate first lease registration
        scheduler.scheduleHeartbeat(recordingId, 0L, LEASE_SECONDS)
        Log.d(TAG, "Watchdog heartbeat started [rid=$recordingId, lease=${LEASE_SECONDS}s]")

        heartbeatJob = scope.launch(Dispatchers.Default) {
            while (isActive && active.get()) {
                delay(RENEW_INTERVAL_MS)
                if (isActive && active.get()) {
                    val elapsedMs = System.currentTimeMillis() - sessionStartEpochMs
                    scheduler.scheduleHeartbeat(recordingId, elapsedMs, LEASE_SECONDS)
                    Log.d(TAG, "Watchdog heartbeat renewed [rid=$recordingId, elapsed=${elapsedMs}ms]")
                }
            }
        }
    }

    fun stop(recordingId: String) {
        active.set(false)
        heartbeatJob?.cancel()
        heartbeatJob = null
        scheduler.cancelHeartbeat(recordingId)
        Log.d(TAG, "Watchdog heartbeat stopped [rid=$recordingId]")
    }
}
