package net.hemory.app.native

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.util.Log
import java.io.IOException
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Hardware MediaCodec AAC-LC Compression Engine.
 * Configuration: "audio/mp4a-latm", profile AACObjectLC (2), 48,000 bps, 16000 Hz, 1 channel.
 * Outputs stream of self-contained ADTS frames (7-byte header + AAC payload).
 */
class MediaCodecEncoder(
    private val listener: EncoderOutputListener
) {
    companion object {
        private const val TAG = "MediaCodecEncoder"
        private const val MIME_TYPE = "audio/mp4a-latm"
        private const val SAMPLE_RATE = 16000
        private const val CHANNEL_COUNT = 1
        private const val BIT_RATE = 48000
        private const val TIMEOUT_US = 10_000L // 10ms poll timeout
    }

    interface EncoderOutputListener {
        fun onEncodedAdtsFrame(adtsPacket: ByteArray, presentationTimeUs: Long)
        fun onEncoderError(throwable: Throwable)
        fun onEncoderEndOfStream()
    }

    private var codec: MediaCodec? = null
    private val isRunning = AtomicBoolean(false)
    private var totalSamplesQueued: Long = 0L

    fun start() {
        if (isRunning.getAndSet(true)) return

        try {
            val format = MediaFormat.createAudioFormat(MIME_TYPE, SAMPLE_RATE, CHANNEL_COUNT).apply {
                setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC)
                setInteger(MediaFormat.KEY_BIT_RATE, BIT_RATE)
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 8192)
            }

            val encoder = MediaCodec.createEncoderByType(MIME_TYPE)
            encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            encoder.start()
            codec = encoder
            totalSamplesQueued = 0L
            Log.d(TAG, "Hardware MediaCodec AAC-LC encoder started [16kHz, 48kbps, mono].")
        } catch (e: Exception) {
            isRunning.set(false)
            listener.onEncoderError(e)
            throw e
        }
    }

    /**
     * Feeds PCM16 samples to the encoder and drains available encoded output.
     */
    @Synchronized
    fun encode(samples: ShortArray, offset: Int, count: Int) {
        if (!isRunning.get()) return
        val activeCodec = codec ?: return

        var sampleIndex = offset
        var remainingSamples = count

        while (remainingSamples > 0 && isRunning.get()) {
            val inputIndex = activeCodec.dequeueInputBuffer(TIMEOUT_US)
            if (inputIndex >= 0) {
                val inputBuffer = activeCodec.getInputBuffer(inputIndex) ?: continue
                inputBuffer.clear()

                val samplesToQueue = minOf(remainingSamples, inputBuffer.remaining() / 2)
                for (i in 0 until samplesToQueue) {
                    val s = samples[sampleIndex + i]
                    inputBuffer.put((s.toInt() and 0xFF).toByte())
                    inputBuffer.put(((s.toInt() shr 8) and 0xFF).toByte())
                }

                val ptsUs = totalSamplesQueued * 1_000_000L / SAMPLE_RATE
                totalSamplesQueued += samplesToQueue
                sampleIndex += samplesToQueue
                remainingSamples -= samplesToQueue

                activeCodec.queueInputBuffer(
                    inputIndex,
                    0,
                    samplesToQueue * 2,
                    ptsUs,
                    0
                )
            }

            drainOutput(false)
        }
    }

    /**
     * Drains encoded AAC packets from MediaCodec output buffers.
     */
    @Synchronized
    fun drainOutput(endOfStream: Boolean) {
        val activeCodec = codec ?: return
        val bufferInfo = MediaCodec.BufferInfo()

        while (isRunning.get()) {
            val outputIndex = activeCodec.dequeueOutputBuffer(bufferInfo, TIMEOUT_US)
            when {
                outputIndex == MediaCodec.INFO_TRY_AGAIN_LATER -> {
                    if (!endOfStream) break
                }
                outputIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    Log.d(TAG, "MediaCodec output format updated: ${activeCodec.outputFormat}")
                }
                outputIndex >= 0 -> {
                    val outputBuffer = activeCodec.getOutputBuffer(outputIndex)
                    if (outputBuffer != null && bufferInfo.size > 0) {
                        // Bypass out-of-band codec config data (csd-0) as ADTS encapsulates each frame
                        val isConfig = (bufferInfo.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0
                        if (!isConfig) {
                            outputBuffer.position(bufferInfo.offset)
                            outputBuffer.limit(bufferInfo.offset + bufferInfo.size)

                            val payload = ByteArray(bufferInfo.size)
                            outputBuffer.get(payload)

                            // Synthesize bit-exact 7-byte ADTS header
                            val header = AdtsHeader.create(
                                payloadLength = payload.size,
                                sampleRate = SAMPLE_RATE,
                                channels = CHANNEL_COUNT
                            )

                            // Composite ADTS frame = [Header (7B)] + [Payload (NB)]
                            val packet = ByteArray(header.size + payload.size)
                            System.arraycopy(header, 0, packet, 0, header.size)
                            System.arraycopy(payload, 0, packet, header.size, payload.size)

                            listener.onEncodedAdtsFrame(packet, bufferInfo.presentationTimeUs)
                        }
                    }

                    activeCodec.releaseOutputBuffer(outputIndex, false)

                    if ((bufferInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        listener.onEncoderEndOfStream()
                        break
                    }
                }
            }
        }
    }

    /**
     * Signals End-Of-Stream, drains all remaining packets, and releases codec resources.
     */
    @Synchronized
    fun flushAndStop() {
        if (!isRunning.getAndSet(false)) return
        val activeCodec = codec ?: return

        try {
            // Queue empty buffer with BUFFER_FLAG_END_OF_STREAM
            val inputIndex = activeCodec.dequeueInputBuffer(TIMEOUT_US)
            if (inputIndex >= 0) {
                val ptsUs = totalSamplesQueued * 1_000_000L / SAMPLE_RATE
                activeCodec.queueInputBuffer(inputIndex, 0, 0, ptsUs, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
            }
            drainOutput(true)
        } catch (e: Exception) {
            Log.w(TAG, "Exception during MediaCodec drain and stop", e)
        } finally {
            runCatching { activeCodec.stop() }
            runCatching { activeCodec.release() }
            codec = null
            Log.d(TAG, "Hardware MediaCodec cleanly released.")
        }
    }
}
