package net.hemory.app

import android.content.Intent
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import net.hemory.app.native.ipc.NativeBridgePlugin
import net.hemory.app.native.ipc.RecordingEventStreamRegistry

class MainActivity : FlutterActivity() {

    private var eventStreamRegistry: RecordingEventStreamRegistry? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        // Register NativeBridgePlugin (ActivityAware attaches automatically via FlutterEngine)
        if (!flutterEngine.plugins.has(NativeBridgePlugin::class.java)) {
            flutterEngine.plugins.add(NativeBridgePlugin())
        }

        // Register RecordingEventStreamRegistry with BinaryMessenger
        val registry = RecordingEventStreamRegistry(
            flutterEngine.dartExecutor.binaryMessenger,
            applicationContext
        )
        registry.register()
        eventStreamRegistry = registry
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent == null) return
        val source = intent.getStringExtra("hemory.listening.action.source")
        if (source == "deadSwitchNotification") {
            val rid = intent.getStringExtra("recording_id")
            NativeBridgePlugin.emitNativeEvent("DEAD_MAN_ALERT_CLICKED", mapOf(
                "source" to source,
                "recordingId" to (rid ?: "")
            ))
        }
    }

    override fun onDestroy() {
        eventStreamRegistry?.unregister()
        eventStreamRegistry = null
        super.onDestroy()
    }
}
