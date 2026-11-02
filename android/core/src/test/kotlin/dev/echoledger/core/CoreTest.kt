package dev.echoledger.core
import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files
import java.io.File

class CoreTest {
    @Test fun sampleClockDoesNotUseWallClockUpdates() { assertEquals(2000L, SampleClock.millisAt(1000, 16000)) }
    @Test fun syntheticSilenceHasNoOriginalTimestamp() {
        val spans = listOf(PackedSpan(0,16000,0,"a"), PackedSpan(17920,33920,160000,"b"))
        assertTrue(SampleClock.restore(16000,17920,spans).isEmpty())
    }
    @Test fun wavCanBeFinalized() {
        val root = Files.createTempDirectory("test-wav").toFile()
        try {
            val out = WavFile(File(root,"test.wav.part"))
            out.write(ShortArray(320))
            assertEquals(684L, out.finish()!!.length())
        } finally { root.deleteRecursively() }
    }
}
