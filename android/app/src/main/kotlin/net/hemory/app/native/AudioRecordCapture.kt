package net.hemory.app.native

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioRecordingConfiguration
import android.media.MediaRecorder
import android.os.Build
import android.os.Process
import android.os.SystemClock
import android.util.Log
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Dedicated hardware AudioRecord capture thread.
 * Thread Name: "hemory-audio-capture"
 * Priority: Process.THREAD_PRIORITY_URGENT_AUDIO (-19)
 */
class AudioRecordCapture(
    private val context: Context,
    private val listener: AudioCaptureListener
) {
    companion object {
        private const val TAG = "AudioRecordCapture"
        const val SAMPLE_RATE = 16000
        const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
        const val SAMPLES_PER_FRAME = 1024 // 64ms frame matching AAC-LC block
    }

    interface AudioCaptureListener {
        fun onPcmFrame(samples: ShortArray, length: Int)
        fun onAudioFrameClockAdvanced(monotonicMs: Long)
        fun onMeteringSample(rmsDb: Float, peakDb: Float)
        fun onClientSilenced(isSilenced: Boolean)
        fun onCaptureError(throwable: Throwable)
    }

    private val isRunning = AtomicBoolean(false)
    private var captureThread: Thread? = null
    private var audioRecord: AudioRecord? = null
    private var recordingCallback: Any? = null

    @Volatile
    var lastAudioFrameTimestampMs: Long = 0L
        private set

    @SuppressLint("MissingPermission")
    fun start() {
        if (isRunning.getAndSet(true)) {
            Log.w(TAG, "AudioRecordCapture is already running.")
            return
        }

        val minBufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        if (minBufferSize == AudioRecord.ERROR || minBufferSize == AudioRecord.ERROR_BAD_VALUE || minBufferSize <= 0) {
            isRunning.set(false)
            val error = IllegalStateException("AudioRecord.getMinBufferSize failed with code: $minBufferSize")
            listener.onCaptureError(error)
            throw error
        }

        // Buffer allocation: getMinBufferSize * 2 (HEMORY_SYSTEM_SPECIFICATION.md §2.1.4.2)
        val bufferSizeBytes = minBufferSize * 2

        try {
            audioRecord = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, // Source 6: hardware AEC + AGC
                SAMPLE_RATE,
                CHANNEL_CONFIG,
                AUDIO_FORMAT,
                bufferSizeBytes
            )
        } catch (e: Exception) {
            isRunning.set(false)
            listener.onCaptureError(e)
            throw e
        }

        check(audioRecord?.state == AudioRecord.STATE_INITIALIZED) {
            isRunning.set(false)
            audioRecord?.release()
            audioRecord = null
            "AudioRecord failed to initialize (state != STATE_INITIALIZED)"
        }

        // Register silence callback on Android 10+ (API 29+)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val cb = object : AudioManager.AudioRecordingCallback() {
                override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>) {
                    val silenced = configs.any { it.isClientSilenced }
                    listener.onClientSilenced(silenced)
                }
            }
            audioRecord?.registerAudioRecordingCallback(context.mainExecutor, cb)
            recordingCallback = cb
        }

        captureThread = Thread(::runCaptureLoop, "hemory-audio-capture").apply {
            isDaemon = true
            start()
        }
    }

    private fun runCaptureLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        val record = audioRecord ?: return

        try {
            record.startRecording()
            check(record.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                "AudioRecord failed to enter RECORDSTATE_RECORDING"
            }
            Log.d(TAG, "hemory-audio-capture thread active (priority -19).")

            val pcmBuffer = ShortArray(SAMPLES_PER_FRAME)
            var lastMeteringEmitMs = 0L

            while (isRunning.get()) {
                val readSamples = record.read(pcmBuffer, 0, pcmBuffer.size)

                if (readSamples > 0) {
                    val nowMono = SystemClock.elapsedRealtime()
                    lastAudioFrameTimestampMs = nowMono
                    listener.onAudioFrameClockAdvanced(nowMono)

                    // Dispatch PCM frame
                    listener.onPcmFrame(pcmBuffer, readSamples)

                    // Periodic metering check (~150ms window)
                    if (nowMono - lastMeteringEmitMs >= 150L) {
                        lastMeteringEmitMs = nowMono
                        computeAndDispatchMetering(pcmBuffer, readSamples)
                    }
                } else if (readSamples < 0) {
                    when (readSamples) {
                        AudioRecord.ERROR_INVALID_OPERATION -> throw IOException("AudioRecord read: ERROR_INVALID_OPERATION")
                        AudioRecord.ERROR_BAD_VALUE -> throw IOException("AudioRecord read: ERROR_BAD_VALUE")
                        AudioRecord.ERROR_DEAD_OBJECT -> throw IOException("AudioRecord read: ERROR_DEAD_OBJECT (MediaServer crashed)")
                        else -> throw IOException("AudioRecord read unknown error code: $readSamples")
                    }
                }
            }
        } catch (t: Throwable) {
            if (isRunning.get()) {
                Log.e(TAG, "Fatal audio capture loop error", t)
                listener.onCaptureError(t)
            }
        } finally {
            runCatching {
                if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                    record.stop()
                }
            }
            Log.d(TAG, "Audio capture loop terminated.")
        }
    }

    private fun computeAndDispatchMetering(samples: ShortArray, count: Int) {
        var sumSquares = 0.0
        var peak = 0

        for (i in 0 until count) {
            val s = samples[i].toInt()
            sumSquares += (s.toDouble() * s.toDouble())
            val absVal = kotlin.math.abs(s)
            if (absVal > peak) peak = absVal
        }

        val rms = sqrt(sumSquares / max(1, count)) / 32768.0
        val peakNorm = peak.toDouble() / 32768.0

        val rmsDb = (20.0 * log10(max(rms, 1e-5))).toFloat()
        val peakDb = (20.0 * log10(max(peakNorm, 1e-5))).toFloat()

        listener.onMeteringSample(rmsDb, peakDb)
    }

    fun stop() {
        if (!isRunning.getAndSet(false)) return

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && recordingCallback != null) {
            runCatching {
                audioRecord?.unregisterAudioRecordingCallback(
                    recordingCallback as AudioManager.AudioRecordingCallback
                )
            }
            recordingCallback = null
        }

        runCatching {
            audioRecord?.stop()
        }
        runCatching {
            captureThread?.join(1000L)
        }
        audioRecord?.release()
        audioRecord = null
        captureThread = null
        Log.d(TAG, "AudioRecordCapture cleanly stopped and released.")
    }
}
