package dev.echoledger.core

const val SAMPLE_RATE = 16_000
const val CHUNK_SECONDS = 30
const val CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_SECONDS

/** Audio input can later be supplied by a BLE device or file import. */
interface PcmSource : AutoCloseable {
    fun start()
    fun read(target: ShortArray): Int
    fun stop()
}

data class SpeechSpan(val startSample: Long, val endSample: Long)
data class PackedSpan(val packedStart: Long, val packedEnd: Long, val originalStart: Long, val chunkId: String)
data class TranscriptPiece(val startMs: Long, val endMs: Long, val text: String, val localSpeaker: String)
data class IdentityMatch(val speakerId: String?, val similarity: Double?, val modelVersion: String)

/** Ports are independent of microphone, ASR vendor and persistence implementation. */
interface AudioPreprocessor { fun detect(pcm: ShortArray): List<SpeechSpan> }
interface TranscriptionGateway { suspend fun transcribe(wav: ByteArray): List<TranscriptPiece> }
interface SpeakerIdentifier { suspend fun identify(pcm: ShortArray): IdentityMatch }
interface MemorySearch { suspend fun search(query: String, fromMs: Long?, toMs: Long?): List<String> }

object SampleClock {
    fun millisAt(anchorUtcMs: Long, sample: Long): Long {
        require(sample >= 0)
        return anchorUtcMs + sample / SAMPLE_RATE * 1000 + (sample % SAMPLE_RATE) * 1000 / SAMPLE_RATE
    }
    fun nanosAt(anchorMonoNs: Long, sample: Long): Long {
        require(sample >= 0)
        return anchorMonoNs + sample / SAMPLE_RATE * 1_000_000_000L + (sample % SAMPLE_RATE) * 1_000_000_000L / SAMPLE_RATE
    }
    fun restore(start: Long, end: Long, spans: List<PackedSpan>): List<SpeechSpan> {
        require(start >= 0 && end > start)
        return spans.mapNotNull { span ->
            val a = maxOf(start, span.packedStart)
            val b = minOf(end, span.packedEnd)
            if (a >= b) null else SpeechSpan(span.originalStart + a - span.packedStart, span.originalStart + b - span.packedStart)
        }
    }
}
