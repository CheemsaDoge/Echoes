package net.hemory.app.native.ipc

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RingBufferTest {

    @Test
    fun testRingBufferUnderCapacity() {
        val buffer = RingBuffer<Int>(256)
        for (i in 0 until 100) {
            buffer.push(i)
        }

        assertEquals(100, buffer.size)
        assertEquals(0L, buffer.droppedEventsCount)

        val drained = buffer.drain()
        assertEquals(100, drained.size)
        assertEquals(0, buffer.size)

        for (i in 0 until 100) {
            assertEquals(i, drained[i])
        }
    }

    @Test
    fun testRingBufferOverflowDropsOldestAndPreservesFIFO() {
        val buffer = RingBuffer<NativeBridgeEvent>(256)

        // Push 300 sequential events
        for (i in 0 until 300) {
            buffer.push(NativeBridgeEvent(
                sequence = i,
                event = "TEST_EVENT",
                payload = mapOf("index" to i)
            ))
        }

        assertEquals("Size must cap at 256", 256, buffer.size)
        assertEquals("Dropped count must be exactly 300 - 256 = 44", 44L, buffer.droppedEventsCount)

        val drained = buffer.drain()
        assertEquals(256, drained.size)
        assertEquals("Drain must empty the buffer", 0, buffer.size)

        // First event drained should be sequence 44
        assertEquals(44, drained.first().sequence)
        assertEquals(44, drained.first().payload["index"])

        // Last event drained should be sequence 299
        assertEquals(299, drained.last().sequence)
        assertEquals(299, drained.last().payload["index"])

        // Verify strict contiguous ascending order
        for (idx in drained.indices) {
            val expectedSeq = 44 + idx
            assertEquals(expectedSeq, drained[idx].sequence)
        }
    }

    @Test
    fun testRingBufferClear() {
        val buffer = RingBuffer<String>(10)
        for (i in 0 until 15) {
            buffer.push("item_$i")
        }
        assertEquals(10, buffer.size)
        assertEquals(5L, buffer.droppedEventsCount)

        buffer.clear()
        assertEquals(0, buffer.size)
        assertEquals(0L, buffer.droppedEventsCount)
        assertTrue(buffer.drain().isEmpty())
    }
}
