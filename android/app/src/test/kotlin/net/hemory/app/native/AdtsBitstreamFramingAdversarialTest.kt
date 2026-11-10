package net.hemory.app.native

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Empirical Adversarial Test Suite for ADTS Bitstream Framing.
 *
 * Requirements (§2.1.4.4 & §4.1.1.2):
 * - Exhaustive payload sizes from 1 to 8191.
 * - Bit-level verification of:
 *     * Syncword: 0xFFF (12 bits)
 *     * MPEG-4 ID: 0 (1 bit)
 *     * Layer: 00 (2 bits)
 *     * Protection absent: 1 (1 bit, no CRC)
 *     * Profile: 01 (AAC-LC, 2 bits)
 *     * Sampling frequency index: 8 (16000Hz, 4 bits)
 *     * Channel configuration: 1 (Mono, 3 bits)
 *     * Frame length packing (13 bits = payload + 7)
 *     * Buffer fullness: 0x7FF (11 bits)
 *     * Number of AAC frames: 0 (2 bits)
 * - Boundary rejection for frameLength > 8191 (payload > 8184).
 * - Exhaustive corrupted header rejection.
 */
class AdtsBitstreamFramingAdversarialTest {

    @Test
    fun testExhaustivePayloadSizes1To8184BitExactVerification() {
        for (payload in 1..8184) {
            val header = AdtsHeader.create(payloadLength = payload, sampleRate = 16000, channels = 1)
            assertEquals("Header size must be 7 bytes for payload $payload", 7, header.size)

            val b0 = header[0].toInt() and 0xFF
            val b1 = header[1].toInt() and 0xFF
            val b2 = header[2].toInt() and 0xFF
            val b3 = header[3].toInt() and 0xFF
            val b4 = header[4].toInt() and 0xFF
            val b5 = header[5].toInt() and 0xFF
            val b6 = header[6].toInt() and 0xFF

            // 1. Syncword: bits 0..11 -> 0xFFF
            val syncword = (b0 shl 4) or (b1 ushr 4)
            assertEquals("Syncword must be 0xFFF for payload $payload", 0xFFF, syncword)

            // 2. ID: bit 12 -> 0 (MPEG-4)
            val id = (b1 ushr 3) and 0x01
            assertEquals("MPEG ID must be 0 for payload $payload", 0, id)

            // 3. Layer: bits 13..14 -> 00
            val layer = (b1 ushr 1) and 0x03
            assertEquals("Layer must be 00 for payload $payload", 0, layer)

            // 4. Protection Absent: bit 15 -> 1 (no CRC)
            val protectionAbsent = b1 and 0x01
            assertEquals("Protection absent must be 1 for payload $payload", 1, protectionAbsent)

            // Byte 1 combined value must be 0xF1
            assertEquals("Byte 1 must be 0xF1 for payload $payload", 0xF1, b1)

            // 5. Profile: bits 16..17 -> 1 (AAC-LC)
            val profile = (b2 ushr 6) and 0x03
            assertEquals("Profile must be 1 (AAC-LC) for payload $payload", 1, profile)

            // 6. Sampling frequency index: bits 18..21 -> 8 (16000 Hz)
            val sfi = (b2 ushr 2) and 0x0F
            assertEquals("Sampling frequency index must be 8 for payload $payload", 8, sfi)

            // 7. Private bit: bit 22 -> 0
            val privateBit = (b2 ushr 1) and 0x01
            assertEquals("Private bit must be 0 for payload $payload", 0, privateBit)

            // 8. Channel configuration: 3 bits (bit 23 in b2, bits 24..25 in b3) -> 1 (Mono)
            val channelConfig = ((b2 and 0x01) shl 2) or (b3 ushr 6)
            assertEquals("Channel configuration must be 1 (Mono) for payload $payload", 1, channelConfig)

            // Byte 2 combined value must be 0x60
            assertEquals("Byte 2 must be 0x60 for payload $payload", 0x60, b2)

            // 9. Frame Length packing (13 bits = payload + 7)
            val expectedFrameLength = payload + 7
            val parsedFrameLength = AdtsHeader.parseFrameLength(header)
            assertEquals("Parsed frame length must match payload + 7", expectedFrameLength, parsedFrameLength)

            val packedFrameLength = ((b3 and 0x03) shl 11) or (b4 shl 3) or (b5 ushr 5)
            assertEquals("Packed frame length bits must match expected", expectedFrameLength, packedFrameLength)

            // 10. Buffer Fullness: 11 bits (bits 4..0 in b5, bits 7..2 in b6) -> 0x7FF
            val bufferFullness = ((b5 and 0x1F) shl 6) or (b6 ushr 2)
            assertEquals("Buffer fullness must be 0x7FF (VBR) for payload $payload", 0x7FF, bufferFullness)

            // 11. Number of AAC frames: bits 1..0 in b6 -> 0 (1 frame per ADTS packet)
            val numFrames = b6 and 0x03
            assertEquals("Number of raw data blocks must be 0 for payload $payload", 0, numFrames)

            // Byte 6 combined value must be 0xFC
            assertEquals("Byte 6 must be 0xFC for payload $payload", 0xFC, b6)

            // 12. Validator check
            assertTrue("isValidAdtsHeader must return true for valid payload $payload", AdtsHeader.isValidAdtsHeader(header))
        }
    }

    @Test
    fun testPayloadSizes8185To8191Exceeding13BitLimitMustThrow() {
        for (payload in 8185..8191) {
            val totalFrame = payload + 7
            try {
                AdtsHeader.create(payloadLength = payload)
                fail("Expected IllegalArgumentException for payload=$payload (frameLength=$totalFrame > 8191)")
            } catch (e: IllegalArgumentException) {
                assertTrue("Exception message should indicate 8191 limit: ${e.message}", e.message?.contains("8191") == true)
            }
        }
    }

    @Test
    fun testZeroAndNegativePayloadMustThrow() {
        val invalidPayloads = listOf(0, -1, -7, -100, Int.MIN_VALUE)
        for (payload in invalidPayloads) {
            try {
                AdtsHeader.create(payloadLength = payload)
                fail("Expected IllegalArgumentException for invalid payload=$payload")
            } catch (_: IllegalArgumentException) {}
        }
    }

    @Test
    fun testCorruptedHeaderRejection() {
        val validHeader = AdtsHeader.create(payloadLength = 384, sampleRate = 16000, channels = 1)
        assertTrue(AdtsHeader.isValidAdtsHeader(validHeader))

        // 1. Truncated buffers (< 7 bytes)
        for (len in 0 until 7) {
            val truncated = validHeader.copyOf(len)
            assertFalse("Truncated buffer of length $len must be rejected", AdtsHeader.isValidAdtsHeader(truncated))
        }

        // 2. Corrupt Byte 0 (Syncword bits 0..7 != 0xFF)
        for (badByte in listOf(0x00, 0x7F, 0xFE, 0x55, 0xAA)) {
            val corrupt = validHeader.clone().apply { set(0, badByte.toByte()) }
            assertFalse("Corrupt byte 0 (0x${badByte.toString(16)}) must be rejected", AdtsHeader.isValidAdtsHeader(corrupt))
        }

        // 3. Corrupt Byte 1 Syncword nibble (bits 8..11 != 0xF)
        for (badNibble in 0..14) {
            val badByte1 = ((badNibble shl 4) or (validHeader[1].toInt() and 0x0F)).toByte()
            val corrupt = validHeader.clone().apply { set(1, badByte1) }
            assertFalse("Corrupt syncword nibble ($badNibble) must be rejected", AdtsHeader.isValidAdtsHeader(corrupt))
        }

        // 4. Corrupt Byte 1 ID (bit 12 != 0, e.g. MPEG-2)
        val mpeg2Header = validHeader.clone().apply { set(1, (get(1).toInt() or 0x08).toByte()) }
        assertFalse("MPEG-2 ID=1 must be rejected for MPEG-4 stream", AdtsHeader.isValidAdtsHeader(mpeg2Header))

        // 5. Corrupt Byte 1 Protection Absent (bit 15 == 0, CRC present)
        val crcPresentHeader = validHeader.clone().apply { set(1, (get(1).toInt() and 0xFE).toByte()) }
        assertFalse("Protection absent=0 (CRC present) must be rejected", AdtsHeader.isValidAdtsHeader(crcPresentHeader))

        // 6. Corrupt Byte 2 Profile (bits 16..17 != 01)
        for (badProfile in listOf(0, 2, 3)) {
            val badByte2 = ((badProfile shl 6) or (validHeader[2].toInt() and 0x3F)).toByte()
            val corrupt = validHeader.clone().apply { set(2, badByte2) }
            assertFalse("Bad profile ($badProfile) must be rejected", AdtsHeader.isValidAdtsHeader(corrupt))
        }

        // 7. Corrupt Byte 2 Sampling Frequency Index (bits 18..21 != 8)
        for (badSfi in 0..15) {
            if (badSfi == 8) continue
            val badByte2 = ((validHeader[2].toInt() and 0xC3) or (badSfi shl 2)).toByte()
            val corrupt = validHeader.clone().apply { set(2, badByte2) }
            assertFalse("Bad sampling frequency index ($badSfi) must be rejected", AdtsHeader.isValidAdtsHeader(corrupt))
        }
    }

    @Test
    fun testParseFrameLengthOffsetSupport() {
        val prefix = byteArrayOf(0x01, 0x02, 0x03, 0x04)
        val validHeader = AdtsHeader.create(payloadLength = 500)
        val combined = prefix + validHeader

        val parsed = AdtsHeader.parseFrameLength(combined, offset = 4)
        assertEquals(507, parsed)
        assertTrue(AdtsHeader.isValidAdtsHeader(combined, offset = 4))
    }
}
