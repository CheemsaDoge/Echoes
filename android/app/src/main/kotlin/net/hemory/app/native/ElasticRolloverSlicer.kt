package net.hemory.app.native

import kotlin.math.log10
import kotlin.math.sqrt

/**
 * Result of the rollover decision evaluation for an audio buffer.
 */
sealed class RolloverDecision {
    object None : RolloverDecision()
    data class Cut(
        val splitReason: String, // "silence_window" | "hard_limit"
        val silenceMsBeforeCut: Long?
    ) : RolloverDecision()
}

/**
 * ElasticRolloverSlicer implements the 26m-30m elastic silence rollover window.
 * Reference: HEMORY_SYSTEM_SPECIFICATION.md §2.1.4 & §4.1.1.
 *
 * Rules:
 * - t < 26m (1,560,000 ms): Continuous write, no silence cuts.
 * - 26m <= t < 30m (1,560,000 ms <= t < 1,800,000 ms): Continuous silence >= 800ms
 *   at <= -40.0 dBFS triggers cut (split_reason = "silence_window").
 * - t >= 30m (t >= 1,800,000 ms): Forced cut (split_reason = "hard_limit").
 *
 * All time calculations are parameterized with clock lambda for deterministic unit testing.
 */
class ElasticRolloverSlicer(
    private val clock: () -> Long = { System.currentTimeMillis() },
    private val sampleRate: Int = 16000
) {
    companion object {
        const val SOFT_LIMIT_MS: Long = 1_560_000L    // 26 minutes
        const val HARD_LIMIT_MS: Long = 1_800_000L    // 30 minutes
        const val SILENCE_THRESHOLD_DBFS: Double = -40.0
        const val REQUIRED_SILENCE_MS: Long = 800L
        const val FULL_SCALE_AMPLITUDE: Double = 32768.0
        const val MIN_DBFS_FLOOR: Double = -160.0
    }

    private var segmentStartEpochMs: Long = 0L
    private var continuousSilenceMs: Long = 0L
    private var totalSamplesInSegment: Long = 0L

    fun startSegment(epochMs: Long = clock()) {
        segmentStartEpochMs = epochMs
        continuousSilenceMs = 0L
        totalSamplesInSegment = 0L
    }

    /**
     * Ingests a PCM16 buffer, calculates RMS dBFS energy, tracks continuous silence,
     * and evaluates whether an elastic cut must occur.
     *
     * @param pcm Array of 16-bit PCM samples
     * @param count Number of valid samples in [pcm]
     * @return RolloverDecision indicating whether to cut or continue
     */
    fun onAudioBuffer(pcm: ShortArray, count: Int): RolloverDecision {
        if (count <= 0) return RolloverDecision.None

        // 1. Calculate duration of this buffer chunk in milliseconds
        val frameDurationMs = (count.toLong() * 1000L) / sampleRate
        totalSamplesInSegment += count

        // 2. Compute RMS energy and dBFS
        var sumSquares = 0.0
        for (i in 0 until count) {
            val sample = pcm[i].toDouble()
            sumSquares += sample * sample
        }
        val rms = sqrt(sumSquares / count)
        val dbfs = if (rms > 0.0) {
            20.0 * log10(rms / FULL_SCALE_AMPLITUDE)
        } else {
            MIN_DBFS_FLOOR
        }

        // 3. Update continuous silence accumulator (resets immediately on loud speech)
        if (dbfs <= SILENCE_THRESHOLD_DBFS) {
            continuousSilenceMs += frameDurationMs
        } else {
            continuousSilenceMs = 0L
        }

        // 4. Calculate segment elapsed time
        val now = clock()
        val elapsedMs = now - segmentStartEpochMs

        // 5. Evaluate Elastic Window Decisions
        return when {
            // Rule 3: Forced hard ceiling at 30 minutes
            elapsedMs >= HARD_LIMIT_MS -> {
                RolloverDecision.Cut(
                    splitReason = "hard_limit",
                    silenceMsBeforeCut = null
                )
            }
            // Rule 2: Elastic window between 26m and 30m with continuous silence >= 800ms
            elapsedMs >= SOFT_LIMIT_MS && continuousSilenceMs >= REQUIRED_SILENCE_MS -> {
                RolloverDecision.Cut(
                    splitReason = "silence_window",
                    silenceMsBeforeCut = continuousSilenceMs
                )
            }
            // Rule 1: Sub-26 minutes or no silence qualification
            else -> {
                RolloverDecision.None
            }
        }
    }

    fun getContinuousSilenceMs(): Long = continuousSilenceMs
    fun getSegmentElapsedMs(): Long = clock() - segmentStartEpochMs
    fun getSegmentSamples(): Long = totalSamplesInSegment
}
