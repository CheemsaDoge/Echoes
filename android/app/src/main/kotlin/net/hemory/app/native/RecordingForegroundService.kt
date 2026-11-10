package net.hemory.app.native

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import net.hemory.app.native.ipc.RecordingEventStreamRegistry
import net.hemory.app.worker.LocalAtomicUploadQueue
import net.hemory.app.worker.UploadEntryState
import net.hemory.app.worker.UploadQueueEntry
import net.hemory.app.worker.UploadScheduler
import java.util.concurrent.atomic.AtomicBoolean

enum class RecordingState(val wire: String) {
    IDLE("idle"),
    RECORDING("recording"),
    PAUSED("paused"),
    STOPPED("stopped"),
    FAILED("failed");
}

class RecordingForegroundService : Service() {

    companion object {
        private const val TAG = "RecordingFgService"

        // Actions
        const val ACTION_START = "net.hemory.app.recording.ACTION_START"
        const val ACTION_STOP = "net.hemory.app.recording.ACTION_STOP"
        const val ACTION_PAUSE = "net.hemory.app.recording.ACTION_PAUSE"
        const val ACTION_RESUME = "net.hemory.app.recording.ACTION_RESUME"
        const val ACTION_RECOVER = "net.hemory.app.recording.ACTION_RECOVER"

        // Intent Extras
        const val EXTRA_RECORDING_ID = "recording_id"
        const val EXTRA_TENANT_ID = "tenant_id"
        const val EXTRA_VAULT_ID = "vault_id"
        const val EXTRA_USER_ID = "user_id"
        const val EXTRA_SOURCE = "source"
        const val EXTRA_TIMEZONE = "timezone"

        // Notification Specification
        const val NOTIFICATION_CHANNEL_ID = "hemory.recording"
        const val NOTIFICATION_CHANNEL_NAME = "Hemory Audio Recording"
        const val NOTIFICATION_ID = 1001

        // Watchdog & Dead-Man Switch Interlock Flags
        @Volatile
        var isServiceRunning: Boolean = false
            internal set

        @Volatile
        var lastAudioFrameTimestampMs: Long = 0L
            internal set

        @Volatile
        var activeRecordingId: String? = null
            internal set

        @Volatile
        var activeTenantId: String? = null
            internal set

        @Volatile
        var activeVaultId: String? = null
            internal set

        @Volatile
        var currentState: RecordingState = RecordingState.IDLE
            internal set

        fun start(context: Context, recordingId: String, tenantId: String, vaultId: String, userId: String = "") {
            val intent = Intent(context, RecordingForegroundService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_RECORDING_ID, recordingId)
                putExtra(EXTRA_TENANT_ID, tenantId)
                putExtra(EXTRA_VAULT_ID, vaultId)
                putExtra(EXTRA_USER_ID, userId)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context, reason: String = "userStop") {
            val intent = Intent(context, RecordingForegroundService::class.java).apply {
                action = ACTION_STOP
                putExtra("reason", reason)
            }
            context.startService(intent)
        }

        fun pause(context: Context) {
            val intent = Intent(context, RecordingForegroundService::class.java).apply {
                action = ACTION_PAUSE
            }
            context.startService(intent)
        }

        fun resume(context: Context) {
            val intent = Intent(context, RecordingForegroundService::class.java).apply {
                action = ACTION_RESUME
            }
            context.startService(intent)
        }
    }

    private var wakeLock: PowerManager.WakeLock? = null
    private var capture: AudioRecordCapture? = null
    private var encoder: MediaCodecEncoder? = null
    private var slicer: ElasticRolloverSlicer? = null
    private var activeSink: SegmentFileSink? = null
    private var heartbeat: RecordingHeartbeat? = null
    private var serviceScope: CoroutineScope? = null

    private var segmentIndex = 0
    private var sessionStartMs = 0L
    private var userId = ""
    private val isCapturing = AtomicBoolean(false)
    private val isPaused = AtomicBoolean(false)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action ?: return START_NOT_STICKY

        when (action) {
            ACTION_STOP -> {
                Log.d(TAG, "Received ACTION_STOP. Terminating capture pipeline.")
                val reason = intent.getStringExtra("reason") ?: "userStop"
                stopPipeline(reason)
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_PAUSE -> {
                Log.d(TAG, "Received ACTION_PAUSE.")
                isPaused.set(true)
                currentState = RecordingState.PAUSED
                broadcastState()
                return START_NOT_STICKY
            }
            ACTION_RESUME -> {
                Log.d(TAG, "Received ACTION_RESUME.")
                isPaused.set(false)
                currentState = RecordingState.RECORDING
                broadcastState()
                return START_NOT_STICKY
            }
            ACTION_RECOVER -> {
                Log.d(TAG, "Received ACTION_RECOVER. Resetting and restarting capture pipeline.")
                val recordingId = intent.getStringExtra(EXTRA_RECORDING_ID) ?: "rec_recovered_${System.currentTimeMillis()}"
                val tenantId = intent.getStringExtra(EXTRA_TENANT_ID) ?: (activeTenantId ?: "")
                val vaultId = intent.getStringExtra(EXTRA_VAULT_ID) ?: (activeVaultId ?: "")
                val uid = intent.getStringExtra(EXTRA_USER_ID) ?: userId

                stopPipeline("HAL_DEADLOCK_RECOVER")
                startForegroundServiceInternal(recordingId, tenantId, vaultId, uid)
                return START_NOT_STICKY
            }
            ACTION_START -> {
                val recordingId = intent.getStringExtra(EXTRA_RECORDING_ID)
                val tenantId = intent.getStringExtra(EXTRA_TENANT_ID) ?: ""
                val vaultId = intent.getStringExtra(EXTRA_VAULT_ID) ?: ""
                val uid = intent.getStringExtra(EXTRA_USER_ID) ?: ""

                if (recordingId.isNullOrEmpty()) {
                    Log.e(TAG, "ACTION_START invoked without valid recording_id extra.")
                    stopSelf()
                    return START_NOT_STICKY
                }

                if (isCapturing.get()) {
                    Log.w(TAG, "Capture already active for rid=$activeRecordingId; ignoring redundant ACTION_START.")
                    return START_NOT_STICKY
                }

                startForegroundServiceInternal(recordingId, tenantId, vaultId, uid)
                return START_NOT_STICKY
            }
            else -> {
                Log.w(TAG, "Unknown action: $action")
                return START_NOT_STICKY
            }
        }
    }

    private fun startForegroundServiceInternal(
        recordingId: String,
        tenantId: String,
        vaultId: String,
        uid: String
    ) {
        // 1. Post Foreground Notification (ID 1001)
        val notification = buildForegroundNotification()
        val fgType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        } else {
            0
        }
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, fgType)

        // 2. Acquire Continuous Partial WakeLock ("hemory:recording_service")
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "hemory:recording_service").apply {
            setReferenceCounted(false)
            acquire()
        }

        // 3. Initialize Shared State
        activeRecordingId = recordingId
        activeTenantId = tenantId
        activeVaultId = vaultId
        userId = uid
        isServiceRunning = true
        isPaused.set(false)
        lastAudioFrameTimestampMs = SystemClock.elapsedRealtime()
        sessionStartMs = System.currentTimeMillis()
        segmentIndex = 0
        currentState = RecordingState.RECORDING
        isCapturing.set(true)

        // 4. Initialize Slicer & Session Manifest
        val localSlicer = ElasticRolloverSlicer()
        slicer = localSlicer
        val initialEpochMs = System.currentTimeMillis()
        localSlicer.startSegment(initialEpochMs)
        SegmentManifestManager.get(this, recordingId).initialize(initialEpochMs)

        // 5. Open Initial Segment Sink
        activeSink = SegmentFileSink(this, recordingId, initialEpochMs, segmentIndex++)

        // 6. Initialize MediaCodecEncoder
        val encoderInstance = MediaCodecEncoder(object : MediaCodecEncoder.EncoderOutputListener {
            override fun onEncodedAdtsFrame(adtsPacket: ByteArray, presentationTimeUs: Long) {
                activeSink?.writeFrame(adtsPacket)
            }

            override fun onEncoderError(throwable: Throwable) {
                Log.e(TAG, "MediaCodec encoder failure", throwable)
                RecordingEventStreamRegistry.emitEvent("ERROR", mapOf("error" to (throwable.message ?: "Encoder error")))
            }

            override fun onEncoderEndOfStream() {
                Log.d(TAG, "MediaCodec reached End-Of-Stream.")
            }
        })
        encoder = encoderInstance
        encoderInstance.start()

        // 7. Initialize AudioRecordCapture
        val captureInstance = AudioRecordCapture(this, object : AudioRecordCapture.AudioCaptureListener {
            override fun onPcmFrame(samples: ShortArray, length: Int) {
                if (isPaused.get()) return

                // Encode audio frame
                encoderInstance.encode(samples, 0, length)

                // Evaluate Elastic Rollover Slicer
                val decision = localSlicer.onAudioBuffer(samples, length)
                if (decision is RolloverDecision.Cut) {
                    executeSeamlessRollover(encoderInstance, localSlicer, decision)
                }
            }

            override fun onAudioFrameClockAdvanced(monotonicMs: Long) {
                lastAudioFrameTimestampMs = monotonicMs
            }

            override fun onMeteringSample(rmsDb: Float, peakDb: Float) {
                RecordingEventStreamRegistry.emitMetering(rmsDb.toDouble(), peakDb.toDouble(), System.currentTimeMillis())
            }

            override fun onClientSilenced(isSilenced: Boolean) {
                Log.w(TAG, "AudioRecord client silenced: $isSilenced")
                if (isSilenced) {
                    RecordingEventStreamRegistry.emitEvent("INTERRUPTION", mapOf("silenced" to true))
                }
            }

            override fun onCaptureError(throwable: Throwable) {
                Log.e(TAG, "AudioRecord capture failure", throwable)
                stopPipeline("captureError: ${throwable.message}")
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        })
        capture = captureInstance
        captureInstance.start()

        // 8. Launch Watchdog Heartbeat Coroutine
        val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
        serviceScope = scope
        val scheduler = AlarmSchedulerHelper(this)
        heartbeat = RecordingHeartbeat(this, scheduler, scope).apply {
            start(recordingId)
        }

        broadcastState()
        RecordingEventStreamRegistry.emitEvent("STATE_CHANGED", mapOf("state" to currentState.wire, "recordingId" to recordingId))
        Log.d(TAG, "RecordingForegroundService started successfully for rid=$recordingId.")
    }

    /**
     * Seamless zero-drop rotation:
     * Drains MediaCodec encoder, finalizes current segment sink, records manifest and upload queue,
     * opens next segment sink, without stopping AudioRecord or re-initializing hardware.
     */
    @Synchronized
    private fun executeSeamlessRollover(
        encoderInstance: MediaCodecEncoder,
        slicerInstance: ElasticRolloverSlicer,
        decision: RolloverDecision.Cut
    ) {
        val currentRid = activeRecordingId ?: return
        Log.i(TAG, "executeSeamlessRollover: reason=${decision.splitReason}, silence=${decision.silenceMsBeforeCut}ms")

        // 1. Drain pending encoder buffers to current sink
        encoderInstance.drainOutput(false)

        // 2. Finalize current segment sink
        finalizeSegmentSink(decision.splitReason, decision.silenceMsBeforeCut)

        // 3. Open next segment sink
        val nextEpochMs = System.currentTimeMillis()
        slicerInstance.startSegment(nextEpochMs)
        activeSink = SegmentFileSink(this, currentRid, nextEpochMs, segmentIndex++)
    }

    private fun finalizeSegmentSink(splitReason: String, silenceMs: Long?) {
        val sink = activeSink ?: return
        val currentRid = activeRecordingId ?: return
        val currentTenant = activeTenantId ?: ""
        val currentVault = activeVaultId ?: ""

        val artifacts = sink.finalize(
            tenantId = currentTenant,
            vaultId = currentVault,
            userId = userId,
            splitReason = splitReason,
            silenceMsBeforeCut = silenceMs
        )

        // Add to Session Manifest
        SegmentManifestManager.get(this, currentRid).addSegment(artifacts)

        // Enqueue to LocalAtomicUploadQueue
        val uploadQueue = LocalAtomicUploadQueue(this)
        val queueEntry = UploadQueueEntry(
            uploadId = "upl_${artifacts.segmentId}_${artifacts.segmentIndex}",
            recordingId = currentRid,
            tenantId = currentTenant,
            vaultId = currentVault,
            filePath = artifacts.audioFile.absolutePath,
            metaPath = artifacts.metaFile.absolutePath,
            fileSizeBytes = artifacts.sizeBytes,
            createdAtMs = artifacts.segmentId,
            status = UploadEntryState.Pending,
            recordingStartedAtMs = artifacts.segmentId,
            sha256 = artifacts.sha256Hex,
            source = "ambient"
        )
        uploadQueue.enqueue(queueEntry)

        // Dispatch WorkManager upload
        UploadScheduler.enqueueSegment(this, queueEntry.uploadId)

        // Emit EventChannel segment event
        RecordingEventStreamRegistry.emitSegment(mapOf(
            "recordingId" to currentRid,
            "segmentIndex" to artifacts.segmentIndex,
            "filePath" to artifacts.audioFile.absolutePath,
            "metaFilePath" to artifacts.metaFile.absolutePath,
            "durationMs" to artifacts.durationMs,
            "sizeBytes" to artifacts.sizeBytes,
            "sha256" to artifacts.sha256Hex,
            "splitReason" to artifacts.splitReason
        ))
        RecordingEventStreamRegistry.emitEvent("SLICE_COMPLETED", mapOf(
            "recordingId" to currentRid,
            "segmentIndex" to artifacts.segmentIndex,
            "splitReason" to artifacts.splitReason
        ))
    }

    private fun stopPipeline(reason: String = "userStop") {
        if (!isCapturing.getAndSet(false)) return

        val currentRid = activeRecordingId
        isServiceRunning = false
        currentState = RecordingState.STOPPED

        // Stop watchdog heartbeat
        currentRid?.let { heartbeat?.stop(it) }
        heartbeat = null
        serviceScope?.cancel()
        serviceScope = null

        // Stop audio capture thread
        capture?.stop()
        capture = null

        // Drain encoder completely and release
        encoder?.flushAndStop()
        encoder = null

        // Finalize active segment
        finalizeSegmentSink(reason, null)
        activeSink = null

        // Finalize session manifest
        currentRid?.let {
            SegmentManifestManager.get(this, it).finalizeSession(if (reason == "userStop") null else reason)
        }

        // Release WakeLock
        if (wakeLock?.isHeld == true) {
            wakeLock?.release()
        }
        wakeLock = null

        broadcastState()
        RecordingEventStreamRegistry.emitEvent("STATE_CHANGED", mapOf("state" to currentState.wire, "recordingId" to (currentRid ?: "")))

        activeRecordingId = null
        activeTenantId = null
        activeVaultId = null
        Log.d(TAG, "Pipeline cleanly stopped and released.")
    }

    private fun broadcastState() {
        val now = System.currentTimeMillis()
        val elapsed = if (sessionStartMs > 0L) now - sessionStartMs else 0L
        RecordingEventStreamRegistry.emitState(mapOf(
            "state" to currentState.wire,
            "recordingId" to (activeRecordingId ?: ""),
            "elapsedMs" to elapsed,
            "runningSinceMs" to sessionStartMs
        ))
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                NOTIFICATION_CHANNEL_NAME,
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Persistent ambient background voice capture channel"
                setShowBadge(false)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    private fun buildForegroundNotification(): Notification {
        val stopIntent = Intent(this, RecordingForegroundService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPendingIntent = PendingIntent.getService(
            this,
            1001,
            stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Hemory Recording")
            .setContentText("Ambient background capture active")
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .addAction(android.R.drawable.ic_media_pause, "Stop", stopPendingIntent)
            .build()
    }

    override fun onDestroy() {
        stopPipeline("serviceDestroyed")
        super.onDestroy()
    }
}
