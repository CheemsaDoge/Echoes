package dev.echoledger.app.capture

import android.annotation.SuppressLint
import android.content.Context
import android.media.*
import android.os.Build
import dev.echoledger.core.PcmSource
import dev.echoledger.core.SAMPLE_RATE

@SuppressLint("MissingPermission")
class MicrophoneSource(context: Context, onSilenced: (Boolean) -> Unit) : PcmSource {
    private val record = AudioRecord.Builder()
        .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
        .setAudioFormat(AudioFormat.Builder().setSampleRate(SAMPLE_RATE)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_IN_MONO).build())
        .setBufferSizeInBytes(maxOf(16384, AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT) * 4))
        .build()
    private val callback = if (Build.VERSION.SDK_INT >= 29) object : AudioManager.AudioRecordingCallback() {
        override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>) {
            onSilenced(configs.any { it.isClientSilenced })
        }
    } else null
    init {
        check(record.state == AudioRecord.STATE_INITIALIZED) { "Microphone initialization failed" }
        if (Build.VERSION.SDK_INT >= 29 && callback != null) record.registerAudioRecordingCallback(context.mainExecutor, callback)
    }
    override fun start() { record.startRecording(); check(record.recordingState == AudioRecord.RECORDSTATE_RECORDING) }
    override fun read(target: ShortArray): Int = record.read(target, 0, target.size, AudioRecord.READ_BLOCKING)
    override fun stop() { if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) record.stop() }
    override fun close() {
        runCatching { stop() }
        if (Build.VERSION.SDK_INT >= 29 && callback != null) runCatching { record.unregisterAudioRecordingCallback(callback) }
        record.release()
    }
}
