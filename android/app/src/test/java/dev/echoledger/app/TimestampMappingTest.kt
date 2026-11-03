package dev.echoledger.app

import dev.echoledger.app.data.StandaloneTranscriber
import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class TimestampMappingTest {

    @Test
    fun testCalculateUtcTimestampsFromStartSample() {
        val sessionStartedAtMs = 1730000000000L
        val chunkStartSample = 480000L // 30 seconds at 16000Hz = 30000ms
        val segmentStartMs = 1200
        val segmentEndMs = 4500

        val (segStartUtc, segEndUtc) = StandaloneTranscriber.calculateUtcTimestamps(
            sessionStartedAtMs = sessionStartedAtMs,
            chunkStartSample = chunkStartSample,
            segmentStartMs = segmentStartMs,
            segmentEndMs = segmentEndMs
        )

        assertEquals(1730000031200L, segStartUtc)
        assertEquals(1730000034500L, segEndUtc)
    }

    @Test
    fun testCalculateUtcTimestampsZeroOffset() {
        val sessionStartedAtMs = 1730000000000L
        val chunkStartSample = 0L
        val segmentStartMs = 0
        val segmentEndMs = 2000

        val (segStartUtc, segEndUtc) = StandaloneTranscriber.calculateUtcTimestamps(
            sessionStartedAtMs = sessionStartedAtMs,
            chunkStartSample = chunkStartSample,
            segmentStartMs = segmentStartMs,
            segmentEndMs = segmentEndMs
        )

        assertEquals(1730000000000L, segStartUtc)
        assertEquals(1730000002000L, segEndUtc)
    }

    @Test
    fun testSilenceEnergyDetectionHeaderOnly() {
        val emptyWav = ByteArray(44)
        val rms = StandaloneTranscriber.computeRms(emptyWav)
        assertEquals(0f, rms, 0.00001f)
    }

    @Test
    fun testSilenceEnergyDetectionZeroAudio() {
        val wavBytes = ByteArray(44 + 3200) // 100ms of silence
        val rms = StandaloneTranscriber.computeRms(wavBytes)
        assertEquals(0f, rms, 0.00001f)
        assertTrue("Zero audio must be below silence threshold", rms < StandaloneTranscriber.SILENCE_RMS_THRESHOLD)
    }

    @Test
    fun testSilenceEnergyDetectionLowNoise() {
        // Create 100ms of low noise (amplitude 20 on 16-bit scale ~ 0.0006)
        val sampleCount = 1600
        val buffer = ByteBuffer.allocate(44 + sampleCount * 2).order(ByteOrder.LITTLE_ENDIAN)
        buffer.put(ByteArray(44)) // Dummy header
        for (i in 0 until sampleCount) {
            buffer.putShort(20.toShort())
        }
        val rms = StandaloneTranscriber.computeRms(buffer.array())
        assertTrue("Low noise RMS should be below silence threshold", rms < StandaloneTranscriber.SILENCE_RMS_THRESHOLD)
    }

    @Test
    fun testSilenceEnergyDetectionAudibleSpeech() {
        // Create 100ms of audible voice (amplitude ~4000 on 16-bit scale ~ 0.12)
        val sampleCount = 1600
        val buffer = ByteBuffer.allocate(44 + sampleCount * 2).order(ByteOrder.LITTLE_ENDIAN)
        buffer.put(ByteArray(44)) // Dummy header
        for (i in 0 until sampleCount) {
            val sample = if (i % 2 == 0) 4000 else -4000
            buffer.putShort(sample.toShort())
        }
        val rms = StandaloneTranscriber.computeRms(buffer.array())
        assertTrue("Audible voice must exceed silence threshold", rms > StandaloneTranscriber.SILENCE_RMS_THRESHOLD)
    }
}
