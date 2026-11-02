package dev.echoledger.app.capture

import android.Manifest
import android.app.*
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.*
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import dev.echoledger.app.EchoApp
import dev.echoledger.app.MainActivity
import dev.echoledger.app.R
import dev.echoledger.app.data.LocalSession
import dev.echoledger.app.network.UploadScheduler
import dev.echoledger.core.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs
import kotlin.math.sqrt


data class CaptureState(val recording: Boolean = false, val sessionId: String = "", val purpose: String = "journal",
                        val samples: Long = 0, val rms: Float = 0f, val silenced: Boolean = false, val error: String = "")
object RecordingBus {
    internal val mutable = MutableStateFlow(CaptureState())
    val state: StateFlow<CaptureState> = mutable
}

class RecordingService : Service() {
    companion object { const val STOP = "stop"; const val CALIBRATE = "calibrate" }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val running = AtomicBoolean(false)
    private var job: Job? = null
    private var microphone: MicrophoneSource? = null
    override fun onBind(intent: Intent?) = null
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) { running.set(false); runCatching { microphone?.stop() }; return START_NOT_STICKY }
        if (job?.isActive == true) return START_NOT_STICKY
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            RecordingBus.mutable.value = CaptureState(error = "Microphone permission is required")
            stopSelf(); return START_NOT_STICKY
        }
        try { showForeground() }
        catch (e: Exception) { RecordingBus.mutable.value = CaptureState(error = "Foreground recording could not start: ${e.javaClass.simpleName}"); stopSelf(); return START_NOT_STICKY }
        running.set(true)
        val calibration = intent?.action == CALIBRATE
        RecordingBus.mutable.value = CaptureState(recording = true, purpose = if (calibration) "calibration" else "journal")
        job = scope.launch { capture(calibration) }
        return START_NOT_STICKY
    }
    private fun showForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("recording", "Audio recording", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1, Intent(this, RecordingService::class.java).setAction(STOP), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification = NotificationCompat.Builder(this, "recording").setSmallIcon(R.drawable.ic_wave)
            .setContentTitle("EchoLedger \u6b63\u5728\u5f55\u97f3")
            .setContentText("\u97f3\u9891\u6309 30 \u79d2\u4fdd\u5b58\uff0c\u53ef\u968f\u65f6\u505c\u6b62")
            .setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .addAction(android.R.drawable.ic_media_pause, "\u505c\u6b62", stop).build()
        ServiceCompat.startForeground(this, 41, notification, if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0)
    }
    private suspend fun capture(calibration: Boolean) {
        val graph = (application as EchoApp).graph
        graph.ready.await()
        if (!running.get()) { RecordingBus.mutable.value = CaptureState(); stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); return }
        val local = graph.local
        val id = UUID.randomUUID().toString()
        val purpose = if (calibration) "calibration" else "journal"
        var total = 0L
        var seq = 0
        var finalized = -1
        var writer: WavFile? = null
        var meta: JSONObject? = null
        var interrupted = false
        var needsRecovery = false
        var error = ""
        var wake: PowerManager.WakeLock? = null
        suspend fun finishChunk() {
            val current = writer ?: return
            writer = null
            val wav = current.finish()
            if (wav != null) {
                local.register(wav, meta!!)
                finalized = seq
                UploadScheduler.enqueue(this)
                seq += 1
            }
        }
        try {
            val mic = MicrophoneSource(this) { silenced -> RecordingBus.mutable.update { it.copy(silenced = silenced) } }
            microphone = mic
            mic.start()
            val anchorUtc = System.currentTimeMillis()
            val anchorMono = SystemClock.elapsedRealtimeNanos()
            local.start(LocalSession(id, anchorUtc, TimeZone.getDefault().getOffset(anchorUtc) / 60000, purpose))
            RecordingBus.mutable.value = CaptureState(true, id, purpose)
            wake = (getSystemService(POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "EchoLedger:Capture")
            wake.acquire(15 * 60 * 1000L)
            var renewal = SystemClock.elapsedRealtime() + 10 * 60 * 1000L
            var syncAt = SystemClock.elapsedRealtime() + 5000
            var uiAt = 0L
            val buffer = ShortArray(512)
            val limit = if (calibration) SAMPLE_RATE * 20L else Long.MAX_VALUE
            while (running.get() && total < limit) {
                val n = mic.read(buffer)
                if (n < 0) { if (running.get()) throw IOException("AUDIO_RECORD_ERROR_$n") else break }
                if (n == 0) { delay(10); continue }
                val available = minOf(n.toLong(), limit - total).toInt()
                var offset = 0
                while (offset < available) {
                    if (writer == null) {
                        if (local.root.usableSpace < 128L * 1024 * 1024) throw IOException("LOW_STORAGE_RECORDING_STOPPED")
                        val stem = "%08d".format(seq)
                        meta = JSONObject().put("id", UUID.randomUUID().toString()).put("session_id", id).put("seq", seq)
                            .put("start_sample", total).put("start_monotonic_ns", SampleClock.nanosAt(anchorMono, total))
                            .put("clock_drift_ms", System.currentTimeMillis() - SampleClock.millisAt(anchorUtc, total))
                        local.writeJson(File(local.folder(id), "$stem.chunk.json"), meta!!)
                        writer = WavFile(File(local.folder(id), "$stem.wav.part"))
                    }
                    val count = minOf(available - offset, CHUNK_SAMPLES - writer!!.samples)
                    writer!!.write(buffer, offset, count)
                    total += count; offset += count
                    if (writer!!.samples == CHUNK_SAMPLES) finishChunk()
                }
                val now = SystemClock.elapsedRealtime()
                if (now >= syncAt) { writer?.sync(); syncAt = now + 5000 }
                if (now >= renewal) { if (wake.isHeld) wake.release(); wake.acquire(15 * 60 * 1000L); renewal = now + 10 * 60 * 1000L }
                if (now - uiAt >= 150) {
                    val sum = (0 until available).sumOf { val x = buffer[it].toDouble() / 32768; x * x }
                    RecordingBus.mutable.update { it.copy(samples = total, rms = sqrt(sum / maxOf(1, available)).toFloat()) }
                    uiAt = now
                    val elapsedMs = (SystemClock.elapsedRealtimeNanos() - anchorMono) / 1_000_000
                    val sampleMs = total * 1000 / SAMPLE_RATE
                    if (abs(elapsedMs - sampleMs) > 5000) throw IOException("CAPTURE_CLOCK_GAP_START_NEW_SESSION")
                }
            }
        } catch (e: Exception) {
            if (running.get()) { interrupted = true; error = e.message?.take(160) ?: e.javaClass.simpleName }
        } finally {
            running.set(false)
            runCatching { microphone?.close() }; microphone = null
            if (wake?.isHeld == true) wake.release()
            withContext(NonCancellable) {
                try { finishChunk() } catch (e: Exception) { interrupted = true; needsRecovery = true; error = "Chunk needs recovery: ${e.javaClass.simpleName}" }
                if (graph.database.dao().session(id) != null) graph.database.dao().finishSession(id, finalized, if (needsRecovery) "needs_recovery" else if (interrupted) "interrupted" else "stopped")
                UploadScheduler.enqueue(this@RecordingService)
            }
            RecordingBus.mutable.value = CaptureState(false, id, purpose, total, error = error)
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }
    override fun onDestroy() { running.set(false); runCatching { microphone?.stop() }; super.onDestroy() }
}
