package net.hemory.app.native

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream

/**
 * Empirical Adversarial Test Suite for Seamless Zero-Drop Sink Swap.
 *
 * Requirements (§2.1.4.5 & §4.1.2):
 * - Seamless zero-drop sink swap: Continuous PCM stream maintains monotonically
 *   advancing samples across rotation boundaries without gap, duplication, or drift.
 * - Slicer state lifecycle: total samples and duration tracked accurately across segments.
 * - ADTS packet stream continuity across rotation boundaries.
 */
class SeamlessZeroDropSinkSwapAdversarialTest {

    private val sampleRate = 16000

    /**
     * Simulated Segment Sink that records all ingested sample IDs and raw byte stream.
     */
    class MockSegmentSink(
        val segmentIndex: Int,
        val startEpochMs: Long
    ) {
        val ingestedSampleIds = mutableListOf<Long>()
        val outputStream = ByteArrayOutputStream()
        var isFinalized = false
        var splitReason: String? = null
        var silenceMsBeforeCut: Long? = null

        fun writeSamples(sampleIds: List<Long>, adtsPacket: ByteArray) {
            check(!isFinalized) { "Cannot write to finalized sink $segmentIndex" }
            ingestedSampleIds.addAll(sampleIds)
            outputStream.write(adtsPacket)
        }

        fun finalize(reason: String, silenceMs: Long?) {
            isFinalized = true
            splitReason = reason
            silenceMsBeforeCut = silenceMs
        }

        val sampleCount: Long get() = ingestedSampleIds.size.toLong()
        val durationMs: Long get() = (sampleCount * 1000L) / 16000L
    }

    /**
     * Simulates the exact capture-to-sink rotation coordinator in RecordingForegroundService.
     */
    class AudioSinkRouter(
        private val sampleRate: Int = 16000,
        private val clock: () -> Long
    ) {
        val sinks = mutableListOf<MockSegmentSink>()
        val slicer = ElasticRolloverSlicer(clock = clock, sampleRate = sampleRate)
        var activeSink: MockSegmentSink? = null
        var nextSegmentIndex = 0

        fun start(initialEpochMs: Long) {
            slicer.startSegment(initialEpochMs)
            val sink = MockSegmentSink(nextSegmentIndex++, initialEpochMs)
            activeSink = sink
            sinks.add(sink)
        }

        fun onPcmFrame(samples: ShortArray, sampleIds: List<Long>) {
            val count = samples.size
            require(count == sampleIds.size)

            // 1. Synthesize ADTS packet for this frame (simulating MediaCodec output)
            val adtsFrame = AdtsHeader.create(payloadLength = count / 2, sampleRate = sampleRate, channels = 1)
            val fullPacket = adtsFrame + ByteArray(count / 2) { 0x55.toByte() }

            // 2. Deliver frame to active sink
            activeSink?.writeSamples(sampleIds, fullPacket)

            // 3. Evaluate slicer decision
            val decision = slicer.onAudioBuffer(samples, count)
            if (decision is RolloverDecision.Cut) {
                executeSeamlessRollover(decision)
            }
        }

        private fun executeSeamlessRollover(decision: RolloverDecision.Cut) {
            // Finalize current sink
            activeSink?.finalize(decision.splitReason, decision.silenceMsBeforeCut)

            // Open next sink immediately with advancing clock
            val nextEpochMs = clock()
            slicer.startSegment(nextEpochMs)
            val nextSink = MockSegmentSink(nextSegmentIndex++, nextEpochMs)
            activeSink = nextSink
            sinks.add(nextSink)
        }

        fun stop() {
            activeSink?.finalize("manual_stop", null)
            activeSink = null
        }
    }

    @Test
    fun testContinuousAudioStreamMaintainsMonotonicSampleAdvancementAcrossRotations() {
        var clockMs = 1_000_000L
        val router = AudioSinkRouter(sampleRate = sampleRate, clock = { clockMs })
        router.start(initialEpochMs = clockMs)

        var globalSampleCounter = 0L
        val bufferSize = 1024 // 64ms frame (1024 samples)

        // Helper to produce next tagged audio buffer
        fun produceBuffer(amplitude: Short, durationMsToAdvanceClock: Long): Pair<ShortArray, List<Long>> {
            clockMs += durationMsToAdvanceClock
            val pcm = ShortArray(bufferSize) { amplitude }
            val ids = (0 until bufferSize).map { globalSampleCounter + it }
            globalSampleCounter += bufferSize
            return Pair(pcm, ids)
        }

        // Phase 1: 25 minutes of continuous loud speech (amplitude 5000 = -16.3 dBFS)
        // 25 minutes = 1,500,000 ms -> 1500000 / 64 = 23,437 frames
        for (i in 0 until 50) { // Test with 50 frames to simulate progress up to 25m50s
            val (pcm, ids) = produceBuffer(amplitude = 5000, durationMsToAdvanceClock = 64L)
            router.onPcmFrame(pcm, ids)
        }
        assertEquals("Sub-26m speech must remain in segment 0", 1, router.sinks.size)
        assertFalse("Segment 0 must still be active", router.sinks[0].isFinalized)

        // Fast-forward clock to 26m01s (1,561,000 ms) inside elastic window
        clockMs = 1_000_000L + 1_561_000L

        // Phase 2: Feed 700ms silence at -50 dBFS (amplitude 100) -> 11 frames of 64ms = 704ms
        for (i in 0 until 11) {
            val (pcm, ids) = produceBuffer(amplitude = 100, durationMsToAdvanceClock = 64L)
            router.onPcmFrame(pcm, ids)
        }
        assertEquals("704ms silence is < 800ms; must NOT trigger rollover yet", 1, router.sinks.size)

        // Phase 3: Feed 2 more frames (128ms) of silence -> reaches 832ms silence -> MUST ROTATE TO SEGMENT 1
        for (i in 0 until 2) {
            val (pcm, ids) = produceBuffer(amplitude = 100, durationMsToAdvanceClock = 64L)
            router.onPcmFrame(pcm, ids)
        }
        assertEquals("832ms continuous silence in elastic window MUST trigger rollover to segment 1", 2, router.sinks.size)
        assertTrue("Segment 0 must be finalized", router.sinks[0].isFinalized)
        assertEquals("silence_window", router.sinks[0].splitReason)
        assertFalse("Segment 1 must be active", router.sinks[1].isFinalized)

        // Phase 4: Advance Segment 1 to 30 minutes hard limit with continuous loud speech
        clockMs = router.sinks[1].startEpochMs + 1_800_000L // 30 min later
        val (pcmHard, idsHard) = produceBuffer(amplitude = 25000, durationMsToAdvanceClock = 64L)
        router.onPcmFrame(pcmHard, idsHard)

        assertEquals("Reaching 30m forced limit MUST trigger rollover to segment 2", 3, router.sinks.size)
        assertTrue("Segment 1 must be finalized", router.sinks[1].isFinalized)
        assertEquals("hard_limit", router.sinks[1].splitReason)
        assertEquals(null, router.sinks[1].silenceMsBeforeCut)

        // Phase 5: Feed 10 more frames in Segment 2 and stop cleanly
        for (i in 0 until 10) {
            val (pcm, ids) = produceBuffer(amplitude = 3000, durationMsToAdvanceClock = 64L)
            router.onPcmFrame(pcm, ids)
        }
        router.stop()
        assertTrue("Segment 2 must be finalized on stop", router.sinks[2].isFinalized)
        assertEquals("manual_stop", router.sinks[2].splitReason)

        // =========================================================================
        // EMPIRICAL VERIFICATION OF SEAMLESS ZERO-DROP INVARIANTS
        // =========================================================================

        val totalSamplesProduced = globalSampleCounter
        val allIngestedSamples = mutableListOf<Long>()

        for (i in 0 until router.sinks.size) {
            val sink = router.sinks[i]
            assertEquals("Segment index must be strictly sequential", i, sink.segmentIndex)
            assertTrue("Each segment must contain positive samples", sink.sampleCount > 0)
            allIngestedSamples.addAll(sink.ingestedSampleIds)
        }

        // Invariant 1: Zero lost samples (Total Ingested == Total Produced)
        assertEquals(
            "CRITICAL: Total ingested samples must exactly equal total generated samples (zero lost)",
            totalSamplesProduced,
            allIngestedSamples.size.toLong()
        )

        // Invariant 2: No duplicate samples
        val uniqueSamples = allIngestedSamples.toSet()
        assertEquals(
            "CRITICAL: No samples may be duplicated across segment swap boundaries",
            allIngestedSamples.size,
            uniqueSamples.size
        )

        // Invariant 3: Strict Monotonic Sample Advancement (Sample[k] == Sample[k-1] + 1)
        for (k in 0 until allIngestedSamples.size) {
            assertEquals(
                "CRITICAL: Audio samples must advance strictly monotonically: sample[$k] == $k",
                k.toLong(),
                allIngestedSamples[k]
            )
        }

        // Invariant 4: Exact boundary continuity across Segment 0 -> Segment 1
        val seg0LastSample = router.sinks[0].ingestedSampleIds.last()
        val seg1FirstSample = router.sinks[1].ingestedSampleIds.first()
        assertEquals(
            "Boundary sample between Seg 0 and Seg 1 must be consecutive without gap",
            seg0LastSample + 1,
            seg1FirstSample
        )

        // Invariant 5: Exact boundary continuity across Segment 1 -> Segment 2
        val seg1LastSample = router.sinks[1].ingestedSampleIds.last()
        val seg2FirstSample = router.sinks[2].ingestedSampleIds.first()
        assertEquals(
            "Boundary sample between Seg 1 and Seg 2 must be consecutive without gap",
            seg1LastSample + 1,
            seg2FirstSample
        )

        // Invariant 6: ADTS Bitstream validity of all written bytes across all segments
        for (sink in router.sinks) {
            val bytes = sink.outputStream.toByteArray()
            assertTrue("Segment output stream must not be empty", bytes.isNotEmpty())
            // Validate first ADTS packet header
            assertTrue("Segment file must begin with valid ADTS header", AdtsHeader.isValidAdtsHeader(bytes, offset = 0))
        }
    }

    @Test
    fun testElasticRolloverSlicerPerSegmentSampleCountAndReset() {
        val slicer = ElasticRolloverSlicer()
        slicer.startSegment(epochMs = 1000L)

        val buf100 = ShortArray(100) { 0 }
        slicer.onAudioBuffer(buf100, 100)
        assertEquals(100L, slicer.getSegmentSamples())

        slicer.onAudioBuffer(buf100, 100)
        assertEquals(200L, slicer.getSegmentSamples())

        // Start next segment: sample count must reset to 0
        slicer.startSegment(epochMs = 2000L)
        assertEquals(0L, slicer.getSegmentSamples())
        assertEquals(0L, slicer.getContinuousSilenceMs())
    }
}
