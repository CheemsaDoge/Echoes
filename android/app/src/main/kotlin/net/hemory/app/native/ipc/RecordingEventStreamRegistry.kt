package net.hemory.app.native.ipc

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Handler
import android.os.Looper
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.EventChannel

class RecordingEventStreamRegistry(
    private val messenger: BinaryMessenger,
    private val context: Context
) {
    companion object {
        const val STATE_CHANNEL    = "hemory/recording/state"
        const val EVENTS_CHANNEL   = "hemory/recording/events"
        const val METERING_CHANNEL = "hemory/recording/metering"
        const val SEGMENTS_CHANNEL = "hemory/recording/segments"

        @Volatile private var stateSink: EventChannel.EventSink? = null
        @Volatile private var eventsSink: EventChannel.EventSink? = null
        @Volatile private var meteringSink: EventChannel.EventSink? = null
        @Volatile private var segmentsSink: EventChannel.EventSink? = null

        private val mainHandler = Handler(Looper.getMainLooper())
        private var isScreenOn = true

        fun emitState(stateMap: Map<String, Any?>) {
            mainHandler.post { runCatching { stateSink?.success(stateMap) } }
        }

        fun emitEvent(type: String, details: Map<String, Any?>) {
            val payload = HashMap(details).apply { put("type", type) }
            mainHandler.post { runCatching { eventsSink?.success(payload) } }
        }

        fun emitMetering(rmsDb: Double, peakDb: Double, timestampMs: Long) {
            if (!isScreenOn) return // Throttled when screen is off
            mainHandler.post {
                runCatching {
                    meteringSink?.success(mapOf(
                        "rmsDb" to rmsDb,
                        "peakDb" to peakDb,
                        "timestamp" to timestampMs
                    ))
                }
            }
        }

        fun emitSegment(segmentMap: Map<String, Any?>) {
            mainHandler.post { runCatching { segmentsSink?.success(segmentMap) } }
        }
    }

    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context?, intent: Intent?) {
            when (intent?.action) {
                Intent.ACTION_SCREEN_ON -> isScreenOn = true
                Intent.ACTION_SCREEN_OFF -> isScreenOn = false
            }
        }
    }

    fun register() {
        // Register Screen receiver for power optimization
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
        }
        runCatching { context.registerReceiver(screenReceiver, filter) }

        // 1. hemory/recording/state
        EventChannel(messenger, STATE_CHANNEL).setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                stateSink = events
            }
            override fun onCancel(arguments: Any?) {
                stateSink = null
            }
        })

        // 2. hemory/recording/events
        EventChannel(messenger, EVENTS_CHANNEL).setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                eventsSink = events
            }
            override fun onCancel(arguments: Any?) {
                eventsSink = null
            }
        })

        // 3. hemory/recording/metering
        EventChannel(messenger, METERING_CHANNEL).setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                meteringSink = events
            }
            override fun onCancel(arguments: Any?) {
                meteringSink = null
            }
        })

        // 4. hemory/recording/segments
        EventChannel(messenger, SEGMENTS_CHANNEL).setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                segmentsSink = events
            }
            override fun onCancel(arguments: Any?) {
                segmentsSink = null
            }
        })
    }

    fun unregister() {
        runCatching { context.unregisterReceiver(screenReceiver) }
        stateSink = null
        eventsSink = null
        meteringSink = null
        segmentsSink = null
    }
}
