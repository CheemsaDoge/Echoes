package net.hemory.app.native

import java.io.OutputStream

/**
 * Pure Kotlin bit-exact synthesizer and parser for 7-byte MPEG-4 ADTS headers.
 * Specification Reference: HEMORY_SYSTEM_SPECIFICATION.md §2.1.4.4 & §4.1.1.2.
 *
 * Bitstream Layout (56 bits / 7 bytes):
 * - Byte 0 (8 bits): Syncword 0xFF (bits 0..7)
 * - Byte 1 (8 bits): Syncword 0xF (bits 8..11), MPEG-4 ID=0 (bit 12), Layer=00 (bits 13..14), ProtectionAbsent=1 (bit 15) -> 0xF1
 * - Byte 2 (8 bits): Profile AAC-LC=01 (bits 16..17), SamplingFreq 16kHz=0x8 (bits 18..21), Private=0 (bit 22), Channel Mono=0x1 high bit (bit 23) -> 0x60
 * - Byte 3 (8 bits): Channel Mono low bits (bits 24..25), Original=0, Home=0, FrameLength high 2 bits (bits 28..29)
 * - Byte 4 (8 bits): FrameLength middle 8 bits (bits 30..37)
 * - Byte 5 (8 bits): FrameLength low 3 bits (bits 38..40), BufferFullness high 5 bits (bits 41..45 = 0x1F)
 * - Byte 6 (8 bits): BufferFullness low 6 bits (0x3F << 2), AAC Frames count=0 (bits 54..55) -> 0xFC
 */
object AdtsHeader {

    const val HEADER_SIZE = 7

    // Sampling frequency index mapping per ISO/IEC 13818-7 / 14496-3
    val SAMPLE_RATES = intArrayOf(
        96000, 88200, 64000, 48000, 44100, 32000,
        24000, 22050, 16000, 12000, 11025, 8000, 7350
    )

    /**
     * Synthesize a 7-byte MPEG-4 AAC-LC ADTS header for a given raw AAC payload length.
     *
     * @param payloadLength Length in bytes of the raw AAC frame payload (excluding header).
     * @param sampleRate Audio sample rate in Hz (default: 16,000 Hz, index = 8).
     * @param channels Channel count (default: 1 for mono).
     * @return 7-byte ByteArray containing the ADTS frame header.
     */
    fun create(
        payloadLength: Int,
        sampleRate: Int = 16000,
        channels: Int = 1
    ): ByteArray {
        require(payloadLength > 0) { "Payload length must be positive: $payloadLength" }
        val frameLength = payloadLength + HEADER_SIZE
        require(frameLength <= 8191) { "Total frame length exceeds 13-bit ADTS limit (8191 bytes): $frameLength" }

        val sampleRateIndex = SAMPLE_RATES.indexOf(sampleRate)
        require(sampleRateIndex >= 0) { "Unsupported sample rate: $sampleRate" }
        require(channels in 1..7) { "Unsupported channel configuration: $channels" }

        // MPEG-4 AAC-LC: profile index 1 (ObjectType 2 - 1)
        val profile = 1

        val header = ByteArray(HEADER_SIZE)

        // Byte 0: Syncword bits 0..7 (0xFF)
        header[0] = 0xFF.toByte()

        // Byte 1: Syncword bits 8..11 (0xF) | ID:0 (MPEG-4) | Layer:00 | ProtectionAbsent:1 (no CRC)
        // 0xF0 | (0 << 3) | (0 << 1) | 1 = 0xF1
        header[1] = 0xF1.toByte()

        // Byte 2: Profile (2 bits) | Sample Rate Index (4 bits) | Private:0 (1 bit) | Channel bit 2 (1 bit)
        header[2] = ((profile shl 6) or (sampleRateIndex shl 2) or (channels shr 2)).toByte()

        // Byte 3: Channel bits 0..1 (2 bits) | Original/Copy:0 | Home:0 | FrameLength bits 11..12 (2 bits)
        header[3] = (((channels and 3) shl 6) or ((frameLength shr 11) and 0x03)).toByte()

        // Byte 4: FrameLength bits 3..10 (8 bits)
        header[4] = ((frameLength shr 3) and 0xFF).toByte()

        // Byte 5: FrameLength bits 0..2 (3 bits) | BufferFullness bits 6..10 (5 bits = 0x1F)
        header[5] = (((frameLength and 7) shl 5) or 0x1F).toByte()

        // Byte 6: BufferFullness bits 0..5 (6 bits = 0x3F) | AAC frames count - 1 (2 bits = 0)
        // (0x3F shl 2) or 0 = 0xFC
        header[6] = 0xFC.toByte()

        return header
    }

    /** Alias for create */
    fun createHeader(payloadLength: Int, sampleRate: Int = 16000, channels: Int = 1): ByteArray {
        return create(payloadLength, sampleRate, channels)
    }

    /**
     * Inspects a 7-byte header and extracts the total frame length (header + payload).
     */
    fun parseFrameLength(header: ByteArray, offset: Int = 0): Int {
        require(header.size >= offset + HEADER_SIZE) { "Insufficient bytes for ADTS header" }
        val b3 = header[offset + 3].toInt() and 0xFF
        val b4 = header[offset + 4].toInt() and 0xFF
        val b5 = header[offset + 5].toInt() and 0xFF
        return ((b3 and 0x03) shl 11) or (b4 shl 3) or ((b5 and 0xE0) ushr 5)
    }

    /**
     * Verifies that the given byte array begins with a valid MPEG-4 AAC-LC 16kHz mono ADTS header.
     */
    fun isValidAdtsHeader(header: ByteArray, offset: Int = 0): Boolean {
        if (header.size < offset + HEADER_SIZE) return false
        val b0 = header[offset].toInt() and 0xFF
        val b1 = header[offset + 1].toInt() and 0xFF
        val b2 = header[offset + 2].toInt() and 0xFF

        val isSyncword = (b0 == 0xFF) && ((b1 and 0xF0) == 0xF0)
        val isMpeg4 = (b1 and 0x08) == 0
        val isNoCrc = (b1 and 0x01) == 1
        val isAacLc = ((b2 ushr 6) and 0x03) == 1
        val is16kHz = ((b2 ushr 2) and 0x0F) == 8

        return isSyncword && isMpeg4 && isNoCrc && isAacLc && is16kHz
    }

    fun writeTo(outputStream: OutputStream, payloadLength: Int, sampleRate: Int = 16000, channels: Int = 1) {
        outputStream.write(create(payloadLength, sampleRate, channels))
    }
}
