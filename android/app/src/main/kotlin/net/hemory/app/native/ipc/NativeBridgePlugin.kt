package net.hemory.app.native.ipc

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import io.flutter.plugin.common.MethodChannel.MethodCallHandler
import io.flutter.plugin.common.MethodChannel.Result
import net.hemory.app.native.RecordingForegroundService

class NativeBridgePlugin : FlutterPlugin, MethodCallHandler {
    companion object {
        const val CHANNEL_NAME = "hemory/native_bridge"
        val ringBuffer = RingBuffer<NativeBridgeEvent>(256)
        private var sequenceCounter = 0
        @Volatile
        var activeInstance: NativeBridgePlugin? = null
            internal set

        fun emitNativeEvent(event: String, payload: Map<String, Any?>) {
            val seq = synchronized(this) { sequenceCounter++ }
            val item = NativeBridgeEvent(seq, event, payload)

            val plugin = activeInstance
            if (plugin != null && plugin.isEngineAttached) {
                plugin.dispatchToFlutter(item)
            } else {
                ringBuffer.push(item)
                Log.d("NativeBridgePlugin", "Flutter engine detached; buffered event '$event' (seq=$seq, buffered=${ringBuffer.size})")
            }
        }

        fun resetSequence() {
            synchronized(this) {
                sequenceCounter = 0
                ringBuffer.clear()
            }
        }
    }

    private var channel: MethodChannel? = null
    private var context: Context? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    var isEngineAttached = false
        private set

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        context = binding.applicationContext
        channel = MethodChannel(binding.binaryMessenger, CHANNEL_NAME)
        channel?.setMethodCallHandler(this)
        isEngineAttached = true
        activeInstance = this

        drainBufferedEvents()
        Log.i("NativeBridgePlugin", "Attached to FlutterEngine; registered $CHANNEL_NAME")
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        isEngineAttached = false
        if (activeInstance == this) {
            activeInstance = null
        }
        channel?.setMethodCallHandler(null)
        channel = null
        context = null
        Log.i("NativeBridgePlugin", "Detached from FlutterEngine")
    }

    fun drainBufferedEvents() {
        val pending = ringBuffer.drain()
        if (pending.isNotEmpty()) {
            Log.i("NativeBridgePlugin", "Draining ${pending.size} buffered events to Flutter")
            for (event in pending) {
                dispatchToFlutter(event)
            }
        }
    }

    fun dispatchToFlutter(event: NativeBridgeEvent) {
        mainHandler.post {
            val args = mapOf(
                "event" to event.event,
                "payload" to event.payload,
                "sequence" to event.sequence
            )
            channel?.invokeMethod("nativeEvent", args)
        }
    }

    override fun onMethodCall(call: MethodCall, result: Result) {
        val ctx = context ?: run {
            result.error("ILLEGAL_STATE", "Context is null", null)
            return
        }

        when (call.method) {
            "startRecording" -> {
                val recordingId = call.argument<String>("recordingId") ?: ""
                val tenantId = call.argument<String>("tenantId") ?: ""
                val vaultId = call.argument<String>("vaultId") ?: ""
                val userId = call.argument<String>("userId") ?: ""

                try {
                    RecordingForegroundService.start(ctx, recordingId, tenantId, vaultId, userId)
                    result.success(true)
                } catch (e: Exception) {
                    result.error("START_FAILED", e.message, null)
                }
            }
            "stopRecording" -> {
                val reason = call.argument<String>("reason") ?: "userStop"
                RecordingForegroundService.stop(ctx, reason)
                result.success(true)
            }
            "pauseRecording" -> {
                RecordingForegroundService.pause(ctx)
                result.success(true)
            }
            "resumeRecording" -> {
                RecordingForegroundService.resume(ctx)
                result.success(true)
            }
            "getRecordingState" -> {
                val state = RecordingForegroundService.currentState.wire
                result.success(state)
            }
            "storeAuthTokens", "storeAuth" -> {
                handleStoreAuth(call, result)
            }
            "getAuth" -> {
                val auth = getStoredAuth()
                result.success(auth)
            }
            "clearAuth" -> {
                clearStoredAuth()
                result.success(true)
            }
            "signOutAuth" -> {
                RecordingForegroundService.stop(ctx, "signOut")
                clearStoredAuth()
                result.success(true)
            }
            else -> result.notImplemented()
        }
    }

    private fun handleStoreAuth(call: MethodCall, result: Result) {
        val args = call.arguments as? Map<*, *> ?: run {
            result.error("INVALID_ARGS", "Expected auth map", null)
            return
        }

        val requiredKeys = listOf(
            "accessToken", "refreshToken", "expiresAt",
            "baseUrl", "slotConnectionType", "slotDpUnitId", "slotTenantId"
        )

        for (k in requiredKeys) {
            if (!args.containsKey(k) || args[k]?.toString().isNullOrBlank()) {
                result.error("MISSING_TUPLE_FIELD", "Missing required 7-tuple field: $k", null)
                return
            }
        }

        val prefs = context?.getSharedPreferences("connection", Context.MODE_PRIVATE)
        prefs?.edit()?.apply {
            putString("token", args["accessToken"]?.toString())
            putString("refresh_token", args["refreshToken"]?.toString())
            putString("expires_at", args["expiresAt"]?.toString())
            putString("url", args["baseUrl"]?.toString())
            putString("slot_connection_type", args["slotConnectionType"]?.toString())
            putString("slot_dp_unit_id", args["slotDpUnitId"]?.toString())
            putString("active_tenant_id", args["slotTenantId"]?.toString())
            putString("active_vault_id", args["vaultId"]?.toString() ?: args["slotTenantId"]?.toString())
            apply()
        }

        result.success(true)
    }

    private fun getStoredAuth(): Map<String, Any?>? {
        val prefs = context?.getSharedPreferences("connection", Context.MODE_PRIVATE) ?: return null
        val token = prefs.getString("token", null) ?: return null
        return mapOf(
            "accessToken" to token,
            "refreshToken" to prefs.getString("refresh_token", ""),
            "expiresAt" to prefs.getString("expires_at", ""),
            "baseUrl" to prefs.getString("url", ""),
            "slotConnectionType" to prefs.getString("slot_connection_type", ""),
            "slotDpUnitId" to prefs.getString("slot_dp_unit_id", ""),
            "slotTenantId" to prefs.getString("active_tenant_id", ""),
            "vaultId" to prefs.getString("active_vault_id", "")
        )
    }

    private fun clearStoredAuth() {
        val prefs = context?.getSharedPreferences("connection", Context.MODE_PRIVATE)
        prefs?.edit()?.clear()?.apply()
    }
}
