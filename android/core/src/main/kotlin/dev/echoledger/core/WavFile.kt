package dev.echoledger.core

import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.nio.file.AtomicMoveNotSupportedException

/** Writes PCM incrementally to .part, then fsyncs and atomically publishes a WAV. */
class WavFile(val part: File) {
    private var file: RandomAccessFile? = RandomAccessFile(part, "rw")
    var samples: Int = 0
        private set
    init {
        require(part.name.endsWith(".wav.part"))
        require(file!!.length() == 0L) { "Refusing to overwrite an existing recording" }
        writeHeader(file!!, 0)
        file!!.fd.sync()
    }
    fun write(pcm: ShortArray, offset: Int = 0, count: Int = pcm.size - offset) {
        require(offset >= 0 && count >= 0 && offset + count <= pcm.size)
        val bytes = ByteBuffer.allocate(count * 2).order(ByteOrder.LITTLE_ENDIAN)
        for (i in offset until offset + count) bytes.putShort(pcm[i])
        file!!.write(bytes.array())
        samples += count
    }
    fun sync() { file?.fd?.sync() }
    fun finish(): File? {
        val f = file ?: return null
        file = null
        if (samples == 0) {
            f.close(); part.delete(); return null
        }
        writeHeader(f, samples)
        f.fd.sync(); f.close()
        return publish(part)
    }
    companion object {
        private fun writeHeader(f: RandomAccessFile, samples: Int) {
            val bytes = samples * 2
            val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
            header.put("RIFF".toByteArray(Charsets.US_ASCII)); header.putInt(36 + bytes)
            header.put("WAVEfmt ".toByteArray(Charsets.US_ASCII)); header.putInt(16)
            header.putShort(1); header.putShort(1); header.putInt(SAMPLE_RATE)
            header.putInt(SAMPLE_RATE * 2); header.putShort(2); header.putShort(16)
            header.put("data".toByteArray(Charsets.US_ASCII)); header.putInt(bytes)
            f.seek(0); f.write(header.array()); f.seek(44L + bytes)
        }
        private fun publish(part: File): File {
            val target = File(part.parentFile, part.name.removeSuffix(".part"))
            try {
                Files.move(part.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE)
            } catch (_: AtomicMoveNotSupportedException) {
                Files.move(part.toPath(), target.toPath())
            }
            return target
        }
        /** Only call for our own PCM .part files with an associated metadata sidecar. */
        fun recover(part: File): Pair<File, Int>? {
            if (part.length() < 46) { part.delete(); return null }
            val samples = ((part.length() - 44) / 2).toInt()
            require(samples <= CHUNK_SAMPLES) { "Unexpected oversized partial chunk" }
            RandomAccessFile(part, "rw").use {
                it.setLength(44L + samples * 2)
                writeHeader(it, samples)
                it.fd.sync()
            }
            return publish(part) to samples
        }
    }
}
