package net.hemory.app.native.ipc

import java.util.ArrayDeque

data class NativeBridgeEvent(
    val sequence: Int,
    val event: String,
    val payload: Map<String, Any?>
)

class RingBuffer<T>(val capacity: Int = 256) {
    private val lock = Any()
    private val buffer = ArrayDeque<T>(capacity)
    var droppedEventsCount = 0L
        private set

    fun push(item: T): Unit = synchronized(lock) {
        if (buffer.size >= capacity) {
            buffer.removeFirst()
            droppedEventsCount++
        }
        buffer.addLast(item)
    }

    fun drain(): List<T> = synchronized(lock) {
        val list = buffer.toList()
        buffer.clear()
        list
    }

    val size: Int
        get() = synchronized(lock) { buffer.size }

    fun clear(): Unit = synchronized(lock) {
        buffer.clear()
        droppedEventsCount = 0L
    }
}
