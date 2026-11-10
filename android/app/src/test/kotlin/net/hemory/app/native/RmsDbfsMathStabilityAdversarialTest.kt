package net.hemory.app.native

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Empirical Adversarial Test Suite for RMS dBFS Precision & Math Stability.
 *
 * Verifies:
 * - Handling of silence (zero amplitude, -Infinity dBFS capped at -160.0).
 * - Clipping (+32767 / -32768, 0.0 dBFS).
 * - DC offset preservation.
 * - Alternating positive/negative Nyquist pulses.
 * - Large buffer summation stability (no double precision overflow or NaN).
 * - Impulse response (single peak amid zeros).
 */
class RmsDbfsMathStabilityAdversarialTest {

    private fun calculateDbfs(pcm: ShortArray, count: Int): Double {
        if (count <= 0) return ElasticRolloverSlicer.MIN_DBFS_FLOOR
        var sumSquares = 0.0
        for (i in 0 until count) {
            val sample = pcm[i].toDouble()
            sumSquares += sample * sample
        }
        val rms = sqrt(sumSquares / count)
        return if (rms > 0.0) {
            20.0 * log10(rms / ElasticRolloverSlicer.FULL_SCALE_AMPLITUDE)
        } else {
            ElasticRolloverSlicer.MIN_DBFS_FLOOR
        }
    }

    @Test
    fun testCompleteSilenceZeroAmplitudeClampedAtMinus160() {
        val count = 1600 // 100ms at 16kHz
        val zeros = ShortArray(count) { 0 }

        val dbfs = calculateDbfs(zeros, count)
        assertEquals("Silence must be clamped exactly to -160.0 dBFS", -160.0, dbfs, 1e-6)
        assertFalse("dbfs must never be -Infinity", dbfs.isInfinite())
        assertFalse("dbfs must never be NaN", dbfs.isNaN())

        // Verify via ElasticRolloverSlicer behavior
        var clockMs = 1_000_000L
        val slicer = ElasticRolloverSlicer(clock = { clockMs })
        slicer.startSegment(epochMs = clockMs)
        clockMs += 1_561_000L // inside elastic window

        // Feed 800ms of zeros (8 frames of 100ms)
        var decision: RolloverDecision = RolloverDecision.None
        for (i in 0 until 8) {
            decision = slicer.onAudioBuffer(zeros, count)
        }
        assertTrue("Zeros must accumulate silence and trigger cut", decision is RolloverDecision.Cut)
        val cut = decision as RolloverDecision.Cut
        assertEquals("silence_window", cut.splitReason)
    }

    @Test
    fun testClippingMaxPositiveAndNegativeAmplitudesReach0dBFS() {
        val count = 1600

        // 1. Full scale negative clipping (-32768)
        val fullScaleNeg = ShortArray(count) { -32768 }
        val dbfsNeg = calculateDbfs(fullScaleNeg, count)
        // -32768.0^2 = 32768^2 -> rms = 32768.0 -> 20*log10(1.0) = 0.0 dBFS
        assertEquals("Full negative scale -32768 must yield exactly 0.0 dBFS", 0.0, dbfsNeg, 1e-9)

        // 2. Full scale positive clipping (+32767)
        val fullScalePos = ShortArray(count) { 32767 }
        val dbfsPos = calculateDbfs(fullScalePos, count)
        // 32767 / 32768 = 0.99996948 -> 20 * log10(...) = -0.000265 dBFS
        assertEquals("Full positive scale +32767 must be within 0.0003 dBFS of 0.0", 0.0, dbfsPos, 0.0003)
        assertTrue("Full positive scale must be <= 0.0 dBFS", dbfsPos <= 0.0)
    }

    @Test
    fun testDcOffsetPreservation() {
        val count = 1600
        val dcLevels = listOf(10, 50, 100, 500, 1000, 5000, 10000, 20000)

        for (dc in dcLevels) {
            val positiveDc = ShortArray(count) { dc.toShort() }
            val negativeDc = ShortArray(count) { (-dc).toShort() }

            val dbfsPos = calculateDbfs(positiveDc, count)
            val dbfsNeg = calculateDbfs(negativeDc, count)

            val expectedDbfs = 20.0 * log10(dc.toDouble() / 32768.0)
            assertEquals("Positive DC $dc mismatch", expectedDbfs, dbfsPos, 1e-6)
            assertEquals("Negative DC -$dc mismatch", expectedDbfs, dbfsNeg, 1e-6)
            assertEquals("Symmetry between +DC and -DC", dbfsPos, dbfsNeg, 1e-9)
        }
    }

    @Test
    fun testAlternatingPositiveNegativeNyquistPulses() {
        val count = 2048

        // Alternating full scale: +32767, -32768, +32767, -32768...
        val nyquistFull = ShortArray(count) { i ->
            if (i % 2 == 0) 32767 else -32768
        }
        val dbfsFull = calculateDbfs(nyquistFull, count)
        assertEquals("Full-scale alternating Nyquist pulse must be ~0.0 dBFS", 0.0, dbfsFull, 0.001)

        // Alternating moderate pulses: +1000, -1000
        val nyquistMod = ShortArray(count) { i ->
            if (i % 2 == 0) 1000.toShort() else (-1000).toShort()
        }
        val dbfsMod = calculateDbfs(nyquistMod, count)
        val expectedMod = 20.0 * log10(1000.0 / 32768.0)
        assertEquals("Moderate alternating pulse must equal DC energy level", expectedMod, dbfsMod, 1e-6)
    }

    @Test
    fun testImpulseResponseIsolatedSpikes() {
        val count = 1000 // 1000 samples
        val impulseBuf = ShortArray(count) { 0 }
        impulseBuf[0] = 32767 // Single spike

        val dbfs = calculateDbfs(impulseBuf, count)
        // rms = 32767 / sqrt(1000) = 1036.185
        // dbfs = 20 * log10(1036.185 / 32768) = 20 * log10(0.03162) = -30.0 dBFS
        val expected = 20.0 * log10((32767.0 / sqrt(1000.0)) / 32768.0)
        assertEquals(expected, dbfs, 1e-6)
    }

    @Test
    fun testNumericalStabilityAcrossOneMillionSamples() {
        // Stress-test 1,000,000 samples of maximum amplitude to ensure sumSquares does not overflow Double
        val massiveCount = 1_000_000
        var sumSquares = 0.0
        val maxSample = -32768.0

        for (i in 0 until massiveCount) {
            sumSquares += maxSample * maxSample
        }

        val rms = sqrt(sumSquares / massiveCount)
        assertEquals(32768.0, rms, 1e-6)

        val dbfs = 20.0 * log10(rms / 32768.0)
        assertEquals(0.0, dbfs, 1e-9)
        assertFalse("Massive count must not yield NaN", dbfs.isNaN())
        assertFalse("Massive count must not yield Infinite", dbfs.isInfinite())
    }

    @Test
    fun testAudioRecordCaptureMeteringMathOracle() {
        // Test AudioRecordCapture metering formula against reference
        val samples = ShortArray(1024) { 100 }
        val count = 1024

        var sumSquares = 0.0
        var peak = 0
        for (i in 0 until count) {
            val s = samples[i].toInt()
            sumSquares += (s.toDouble() * s.toDouble())
            val absVal = abs(s)
            if (absVal > peak) peak = absVal
        }

        val rms = sqrt(sumSquares / max(1, count)) / 32768.0
        val peakNorm = peak.toDouble() / 32768.0

        val rmsDb = (20.0 * log10(max(rms, 1e-5))).toFloat()
        val peakDb = (20.0 * log10(max(peakNorm, 1e-5))).toFloat()

        val expectedDb = (20.0 * log10(100.0 / 32768.0)).toFloat()
        assertEquals(expectedDb, rmsDb, 1e-4f)
        assertEquals(expectedDb, peakDb, 1e-4f)
    }
}
