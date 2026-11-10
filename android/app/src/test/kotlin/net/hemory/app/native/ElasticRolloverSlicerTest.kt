package net.hemory.app.native

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ElasticRolloverSlicerTest {

    private fun createPcmBuffer(amplitude: Short, count: Int = 320): ShortArray {
        return ShortArray(count) { amplitude }
    }

    @Test
    fun testSub26MinutesNeverCutsEvenDuringSilence() {
        var currentTimeMs = 1_000_000L
        val slicer = ElasticRolloverSlicer(clock = { currentTimeMs })
        slicer.startSegment(epochMs = currentTimeMs)

        // Advance 25 minutes (1,500,000 ms)
        currentTimeMs += 1_500_000L

        // Feed 2000ms of complete silence (amplitude = 0)
        val silentBuffer = createPcmBuffer(0, count = 320) // 20ms chunks at 16kHz
        var decision: RolloverDecision = RolloverDecision.None
        for (i in 0 until 100) { // 100 * 20ms = 2000ms
            decision = slicer.onAudioBuffer(silentBuffer, 320)
        }

        assertTrue("Must NOT cut before 26 minutes", decision is RolloverDecision.None)
    }

    @Test
    fun test26mTo30mWindowCutsAfter800msSilenceAtMinus40dBFS() {
        var currentTimeMs = 1_000_000L
        val slicer = ElasticRolloverSlicer(clock = { currentTimeMs })
        slicer.startSegment(epochMs = currentTimeMs)

        // Advance to 26 minutes + 1 second (1,561,000 ms)
        currentTimeMs += 1_561_000L

        // Amplitude 100: RMS = 100 / 32768 = 0.00305 -> 20 * log10(0.00305) = -50.3 dBFS (< -40 dBFS)
        val quietBuffer = createPcmBuffer(100, count = 320) // 20ms chunks

        // Feed 780ms of silence (39 frames of 20ms) -> should NOT cut yet
        for (i in 0 until 39) {
            val decision = slicer.onAudioBuffer(quietBuffer, 320)
            assertTrue("Should not cut before reaching 800ms silence", decision is RolloverDecision.None)
        }

        // Feed 40th frame (800ms reached) -> MUST CUT
        val cutDecision = slicer.onAudioBuffer(quietBuffer, 320)
        assertTrue("Must cut upon reaching 800ms silence", cutDecision is RolloverDecision.Cut)
        val cut = cutDecision as RolloverDecision.Cut
        assertEquals("silence_window", cut.splitReason)
        assertEquals(800L, cut.silenceMsBeforeCut)
    }

    @Test
    fun testLoudSpeechResetsSilenceAccumulatorInElasticWindow() {
        var currentTimeMs = 1_000_000L
        val slicer = ElasticRolloverSlicer(clock = { currentTimeMs })
        slicer.startSegment(epochMs = currentTimeMs)
        currentTimeMs += 1_561_000L

        val quietBuffer = createPcmBuffer(100, count = 320)
        // Feed 600ms of quiet
        for (i in 0 until 30) {
            slicer.onAudioBuffer(quietBuffer, 320)
        }
        assertEquals(600L, slicer.getContinuousSilenceMs())

        // Feed loud speech (amplitude 10,000 = -10.3 dBFS)
        val loudBuffer = createPcmBuffer(10000, count = 320)
        val decision = slicer.onAudioBuffer(loudBuffer, 320)
        assertTrue(decision is RolloverDecision.None)
        assertEquals(0L, slicer.getContinuousSilenceMs())
    }

    @Test
    fun test30MinutesForcesHardLimitCutRegardlessOfAudioEnergy() {
        var currentTimeMs = 1_000_000L
        val slicer = ElasticRolloverSlicer(clock = { currentTimeMs })
        slicer.startSegment(epochMs = currentTimeMs)

        // Advance to 30 minutes exact (1,800,000 ms)
        currentTimeMs += 1_800_000L

        // Continuous screaming loud audio (amplitude 30,000)
        val screamingBuffer = createPcmBuffer(30000, count = 320)
        val decision = slicer.onAudioBuffer(screamingBuffer, 320)

        assertTrue("Must force hard limit cut at 30 minutes", decision is RolloverDecision.Cut)
        val cut = decision as RolloverDecision.Cut
        assertEquals("hard_limit", cut.splitReason)
        assertEquals(null, cut.silenceMsBeforeCut)
    }

    @Test
    fun testEmptyOrInvalidBufferReturnsNone() {
        val slicer = ElasticRolloverSlicer()
        slicer.startSegment()
        assertEquals(RolloverDecision.None, slicer.onAudioBuffer(ShortArray(0), 0))
        assertEquals(RolloverDecision.None, slicer.onAudioBuffer(ShortArray(10), -1))
    }
}
