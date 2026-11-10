package net.hemory.app.native

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.pow

/**
 * Empirical Adversarial Test Suite for Elastic Rollover Boundary Values.
 *
 * Requirements (§2.1.4.5 & §4.1.2):
 * - 25m59.999s with 1000ms silence at -50 dBFS -> MUST NOT cut.
 * - 26m00.001s with 799ms silence at -50 dBFS -> MUST NOT cut.
 * - 26m00.001s with 800ms silence at -40.1 dBFS -> MUST cut (`silence_window`).
 * - 26m00.001s with 800ms silence at -39.9 dBFS -> MUST NOT cut.
 * - 29m59.999s with no silence -> MUST NOT cut.
 * - 30m00.000s with loud speech -> MUST cut (`hard_limit`).
 */
class ElasticRolloverBoundaryAdversarialTest {

    private val sampleRate = 16000

    private fun createBufferWithDbfs(targetDbfs: Double, durationMs: Long): ShortArray {
        val sampleCount = ((durationMs * sampleRate) / 1000L).toInt()
        val amplitude = (32768.0 * 10.0.pow(targetDbfs / 20.0)).toInt().coerceIn(-32768, 32767).toShort()
        return ShortArray(sampleCount) { amplitude }
    }

    private fun createConstantBuffer(amplitude: Short, durationMs: Long): ShortArray {
        val sampleCount = ((durationMs * sampleRate) / 1000L).toInt()
        return ShortArray(sampleCount) { amplitude }
    }

    @Test
    fun testBoundary_25m59s999ms_With1000msSilence_MustNotCut() {
        var clockMs = 10_000_000L
        val slicer = ElasticRolloverSlicer(clock = { clockMs }, sampleRate = sampleRate)
        slicer.startSegment(epochMs = clockMs)

        // 25m59.999s = 1,559,999 ms
        clockMs += 1_559_999L

        // Feed 1000ms of silence at -50 dBFS (amplitude ~103)
        val silenceBuf = createBufferWithDbfs(-50.0, 1000L)
        val decision = slicer.onAudioBuffer(silenceBuf, silenceBuf.size)

        assertTrue(
            "At 25m59.999s, even with 1000ms silence, slicer MUST NOT cut (sub-26m window)",
            decision is RolloverDecision.None
        )
        assertEquals(1000L, slicer.getContinuousSilenceMs())
    }

    @Test
    fun testBoundary_26m00s001ms_With799msSilence_MustNotCut() {
        var clockMs = 10_000_000L
        val slicer = ElasticRolloverSlicer(clock = { clockMs }, sampleRate = sampleRate)
        slicer.startSegment(epochMs = clockMs)

        // 26m00.001s = 1,560,001 ms
        clockMs += 1_560_001L

        // Feed 799ms of silence at -50 dBFS
        // 799ms at 16kHz = 12784 samples
        val silenceBuf = createBufferWithDbfs(-50.0, 799L)
        val decision = slicer.onAudioBuffer(silenceBuf, silenceBuf.size)

        assertTrue(
            "At 26m00.001s with 799ms silence (<800ms required), slicer MUST NOT cut",
            decision is RolloverDecision.None
        )
        assertEquals(799L, slicer.getContinuousSilenceMs())
    }

    @Test
    fun testBoundary_26m00s001ms_With800msSilenceAtMinus40_1dBFS_MustCutSilenceWindow() {
        var clockMs = 10_000_000L
        val slicer = ElasticRolloverSlicer(clock = { clockMs }, sampleRate = sampleRate)
        slicer.startSegment(epochMs = clockMs)

        // 26m00.001s = 1,560,001 ms
        clockMs += 1_560_001L

        // -40.1 dBFS: amplitude 323 -> RMS 323 / 32768 = 0.009857 -> 20*log10(0.009857) = -40.125 dBFS
        val silenceBuf = createConstantBuffer(amplitude = 323, durationMs = 800L)
        val decision = slicer.onAudioBuffer(silenceBuf, silenceBuf.size)

        assertTrue(
            "At 26m00.001s with 800ms silence at -40.1 dBFS, slicer MUST CUT with silence_window",
            decision is RolloverDecision.Cut
        )
        val cut = decision as RolloverDecision.Cut
        assertEquals("silence_window", cut.splitReason)
        assertEquals(800L, cut.silenceMsBeforeCut)
    }

    @Test
    fun testBoundary_26m00s001ms_With800msSilenceAtMinus39_9dBFS_MustNotCut() {
        var clockMs = 10_000_000L
        val slicer = ElasticRolloverSlicer(clock = { clockMs }, sampleRate = sampleRate)
        slicer.startSegment(epochMs = clockMs)

        // 26m00.001s = 1,560,001 ms
        clockMs += 1_560_001L

        // -39.9 dBFS: amplitude 332 -> RMS 332 / 32768 = 0.010131 -> 20*log10(0.010131) = -39.886 dBFS (> -40.0 dBFS)
        val notSilentBuf = createConstantBuffer(amplitude = 332, durationMs = 800L)
        val decision = slicer.onAudioBuffer(notSilentBuf, notSilentBuf.size)

        assertTrue(
            "At -39.9 dBFS (> -40.0 dBFS silence threshold), slicer MUST NOT cut",
            decision is RolloverDecision.None
        )
        assertEquals("Continuous silence accumulator must be reset to 0L", 0L, slicer.getContinuousSilenceMs())
    }

    @Test
    fun testBoundary_29m59s999ms_WithNoSilence_MustNotCut() {
        var clockMs = 10_000_000L
        val slicer = ElasticRolloverSlicer(clock = { clockMs }, sampleRate = sampleRate)
        slicer.startSegment(epochMs = clockMs)

        // 29m59.999s = 1,799,999 ms
        clockMs += 1_799_999L

        // Active speech: amplitude 5000 -> -16.3 dBFS
        val speechBuf = createConstantBuffer(amplitude = 5000, durationMs = 100L)
        val decision = slicer.onAudioBuffer(speechBuf, speechBuf.size)

        assertTrue(
            "At 29m59.999s without silence, slicer MUST NOT cut (has not reached 30m hard limit)",
            decision is RolloverDecision.None
        )
        assertEquals(0L, slicer.getContinuousSilenceMs())
    }

    @Test
    fun testBoundary_30m00s000ms_WithLoudSpeech_MustCutHardLimit() {
        var clockMs = 10_000_000L
        val slicer = ElasticRolloverSlicer(clock = { clockMs }, sampleRate = sampleRate)
        slicer.startSegment(epochMs = clockMs)

        // 30m00.000s = 1,800,000 ms
        clockMs += 1_800_000L

        // Screaming loud speech: amplitude 30,000 -> -0.76 dBFS
        val loudBuf = createConstantBuffer(amplitude = 30000, durationMs = 50L)
        val decision = slicer.onAudioBuffer(loudBuf, loudBuf.size)

        assertTrue(
            "At exactly 30m00.000s, slicer MUST trigger forced hard_limit cut even during loud speech",
            decision is RolloverDecision.Cut
        )
        val cut = decision as RolloverDecision.Cut
        assertEquals("hard_limit", cut.splitReason)
        assertNull("silenceMsBeforeCut must be null on hard_limit cut", cut.silenceMsBeforeCut)
    }

    @Test
    fun testInterleavedSpeechAndSilenceResetAccumulator() {
        var clockMs = 10_000_000L
        val slicer = ElasticRolloverSlicer(clock = { clockMs }, sampleRate = sampleRate)
        slicer.startSegment(epochMs = clockMs)

        // Set clock to 27 minutes (1,620,000 ms) inside elastic window
        clockMs += 1_620_000L

        val silence700ms = createBufferWithDbfs(-50.0, 700L)
        val loud10ms = createConstantBuffer(amplitude = 15000, durationMs = 10L)
        val silence799ms = createBufferWithDbfs(-50.0, 799L)
        val silence1ms = createBufferWithDbfs(-50.0, 1L)

        // Feed 700ms silence -> continuousSilence = 700ms
        assertEquals(RolloverDecision.None, slicer.onAudioBuffer(silence700ms, silence700ms.size))
        assertEquals(700L, slicer.getContinuousSilenceMs())

        // Loud spike of 10ms -> continuousSilence must reset to 0L
        assertEquals(RolloverDecision.None, slicer.onAudioBuffer(loud10ms, loud10ms.size))
        assertEquals(0L, slicer.getContinuousSilenceMs())

        // Feed 799ms silence -> continuousSilence = 799ms -> still no cut
        assertEquals(RolloverDecision.None, slicer.onAudioBuffer(silence799ms, silence799ms.size))
        assertEquals(799L, slicer.getContinuousSilenceMs())

        // Feed 1ms silence -> reaches 800ms -> MUST CUT
        val cutDecision = slicer.onAudioBuffer(silence1ms, silence1ms.size)
        assertTrue(cutDecision is RolloverDecision.Cut)
        val cut = cutDecision as RolloverDecision.Cut
        assertEquals("silence_window", cut.splitReason)
        assertEquals(800L, cut.silenceMsBeforeCut)
    }

    @Test
    fun testMultiChunkGradualAccumulationReaching800ms() {
        var clockMs = 10_000_000L
        val slicer = ElasticRolloverSlicer(clock = { clockMs }, sampleRate = sampleRate)
        slicer.startSegment(epochMs = clockMs)
        clockMs += 1_560_001L // 26m00.001s

        // Feed 16 chunks of 50ms (800 samples each) = 800ms total
        val chunk50ms = createBufferWithDbfs(-60.0, 50L)
        for (i in 1..15) {
            val res = slicer.onAudioBuffer(chunk50ms, chunk50ms.size)
            assertTrue("Chunk $i (total ${i * 50}ms) must not cut yet", res is RolloverDecision.None)
            assertEquals((i * 50).toLong(), slicer.getContinuousSilenceMs())
        }

        // 16th chunk brings total to 800ms
        val finalRes = slicer.onAudioBuffer(chunk50ms, chunk50ms.size)
        assertTrue("16th chunk (800ms) must cut", finalRes is RolloverDecision.Cut)
        val cut = finalRes as RolloverDecision.Cut
        assertEquals("silence_window", cut.splitReason)
        assertEquals(800L, cut.silenceMsBeforeCut)
    }
}
