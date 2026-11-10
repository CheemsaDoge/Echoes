package net.hemory.app.native

import org.junit.Assert.*
import org.junit.Test

class AdtsHeaderTest {

    @Test
    fun testSynthesizeStandardAdtsHeaderNominal16kHzMono() {
        val payloadLength = 384 // Typical AAC-LC frame size
        val header = AdtsHeader.create(payloadLength = payloadLength, sampleRate = 16000, channels = 1)

        assertEquals("Header must be exactly 7 bytes", 7, header.size)
        // Byte 0: 0xFF
        assertEquals(0xFF.toByte(), header[0])
        // Byte 1: 0xF1 (Sync 0xF, MPEG-4 ID=0, Layer=00, No CRC=1)
        assertEquals(0xF1.toByte(), header[1])
        // Byte 2: 0x60 (Profile AAC-LC=1 << 6, 16kHz index=8 << 2, Mono=0)
        assertEquals(0x60.toByte(), header[2])

        // Verify total frame length = payloadLength + 7 = 391
        val parsedFrameLength = AdtsHeader.parseFrameLength(header)
        assertEquals(391, parsedFrameLength)

        assertTrue(AdtsHeader.isValidAdtsHeader(header))
    }

    @Test
    fun testAdtsBitPackingAcrossVariousPayloadLengths() {
        val testPayloads = listOf(1, 10, 100, 250, 384, 512, 1024, 2048, 4096, 8184)
        for (payload in testPayloads) {
            val header = AdtsHeader.create(payloadLength = payload)
            val parsedLen = AdtsHeader.parseFrameLength(header)
            assertEquals("Mismatch for payload $payload", payload + 7, parsedLen)
            assertTrue("Validation failed for payload $payload", AdtsHeader.isValidAdtsHeader(header))
        }
    }

    @Test
    fun testAdtsPayloadExceeding13BitLimitThrows() {
        // Max frame length is 8191 -> max payload is 8191 - 7 = 8184
        try {
            AdtsHeader.create(payloadLength = 8185)
            fail("Expected IllegalArgumentException for payloadLength = 8185")
        } catch (_: IllegalArgumentException) {}
    }

    @Test
    fun testAdtsZeroOrNegativePayloadThrows() {
        try {
            AdtsHeader.create(payloadLength = 0)
            fail("Expected IllegalArgumentException for zero payload")
        } catch (_: IllegalArgumentException) {}

        try {
            AdtsHeader.create(payloadLength = -10)
            fail("Expected IllegalArgumentException for negative payload")
        } catch (_: IllegalArgumentException) {}
    }

    @Test
    fun testAdtsHeaderBufferFullnessFixedBits() {
        val header = AdtsHeader.create(payloadLength = 100)
        // Byte 5 low 5 bits must be 0x1F
        assertEquals(0x1F, header[5].toInt() and 0x1F)
        // Byte 6 must be 0xFC (fullness low bits 0x3F << 2 | 00)
        assertEquals(0xFC.toByte(), header[6])
    }

    @Test
    fun testAdtsHeaderInvalidSyncwordDetection() {
        val header = AdtsHeader.create(payloadLength = 100)
        assertTrue(AdtsHeader.isValidAdtsHeader(header))

        // Corrupt syncword in byte 0
        val corrupt0 = header.clone().apply { set(0, 0xFE.toByte()) }
        assertFalse(AdtsHeader.isValidAdtsHeader(corrupt0))

        // Corrupt syncword nibble in byte 1
        val corrupt1 = header.clone().apply { set(1, 0xE1.toByte()) }
        assertFalse(AdtsHeader.isValidAdtsHeader(corrupt1))

        // Corrupt sample rate index in byte 2
        val corrupt2 = header.clone().apply { set(2, 0x40.toByte()) } // index 0 instead of 8
        assertFalse(AdtsHeader.isValidAdtsHeader(corrupt2))
    }
}
