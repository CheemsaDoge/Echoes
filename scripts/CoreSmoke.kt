import dev.echoledger.core.*
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files

fun main() {
    var checks = 0
    fun verify(ok: Boolean) { check(ok); checks++ }
    verify(SampleClock.millisAt(1000, 16000) == 2000L)
    verify(SampleClock.nanosAt(1000, 16000L * 60 * 60 * 14) == 1000L + 14L * 60 * 60 * 1_000_000_000L)
    val spans = listOf(PackedSpan(0, 16000, 16000, "a"), PackedSpan(17920, 33920, 128000, "b"))
    verify(SampleClock.restore(16000, 17920, spans).isEmpty())
    verify(SampleClock.restore(0, 33920, spans) == listOf(SpeechSpan(16000, 32000), SpeechSpan(128000, 144000)))
    val dir = Files.createTempDirectory("echoledger-core-").toFile()
    try {
        val writer = WavFile(File(dir, "normal.wav.part"))
        val samples = ShortArray(16000) { (it % 100).toShort() }
        writer.write(samples, 0, 8000); writer.write(samples, 8000, 8000)
        val wav = writer.finish()!!
        verify(wav.length() == 32044L)
        val header = ByteBuffer.wrap(wav.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
        verify(header.getInt(24) == 16000 && header.getInt(40) == 32000)
        verify(!File(dir, "normal.wav.part").exists())
        // Simulate power loss with a stale header and an incomplete final sample.
        val part = File(dir, "crashed.wav.part")
        RandomAccessFile(part, "rw").use { it.write(ByteArray(44)); it.write(ByteArray(201)) }
        val recovered = WavFile.recover(part)!!
        verify(recovered.second == 100 && recovered.first.length() == 244L)
        val recoveredHeader = ByteBuffer.wrap(recovered.first.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
        verify(recoveredHeader.getInt(40) == 200)
        val empty = File(dir, "empty.wav.part").apply { writeBytes(ByteArray(44)) }
        verify(WavFile.recover(empty) == null && !empty.exists())
        println("Kotlin core smoke checks: $checks passed")
    } finally { dir.deleteRecursively() }
}
