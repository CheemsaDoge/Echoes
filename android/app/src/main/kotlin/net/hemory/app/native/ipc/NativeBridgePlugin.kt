package net.hemory.app.native.ipc

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.pm.PackageManager
import android.media.MediaPlayer
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.embedding.engine.plugins.activity.ActivityAware
import io.flutter.embedding.engine.plugins.activity.ActivityPluginBinding
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import io.flutter.plugin.common.MethodChannel.MethodCallHandler
import io.flutter.plugin.common.MethodChannel.Result
import io.flutter.plugin.common.PluginRegistry
import net.hemory.app.data.HemoryPreferences
import net.hemory.app.native.RecordingForegroundService
import net.hemory.app.worker.LocalAtomicUploadQueue
import net.hemory.app.worker.UploadEntryState
import net.hemory.app.worker.UploadScheduler
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class NativeBridgePlugin :
    FlutterPlugin,
    MethodCallHandler,
    ActivityAware,
    PluginRegistry.RequestPermissionsResultListener {

    companion object {
        const val CHANNEL_NAME = "hemory/native_bridge"
        private const val PERMISSION_REQUEST_CODE = 4109
        private const val TEN_YEARS_SECONDS = 315_360_000L

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

        internal fun normalizeStoredAuthMap(
            token: String,
            baseUrl: String,
            rawPrefs: Map<String, String?>,
            nowEpochSeconds: Long = System.currentTimeMillis() / 1000L
        ): Map<String, Any>? {
            val cleanToken = token.trim()
            if (cleanToken.isEmpty()) return null

            val cleanBaseUrl = baseUrl.trim().trimEnd('/').ifEmpty { HemoryPreferences.DEFAULT_BASE_URL }
            val expiresAt = rawPrefs["expires_at"]?.trim()?.toLongOrNull()?.takeIf { it > 0L }
                ?: (nowEpochSeconds + TEN_YEARS_SECONDS)
            val refreshToken = rawPrefs["refresh_token"]?.trim()?.takeIf { it.isNotEmpty() } ?: cleanToken
            val connectionType = rawPrefs["slot_connection_type"]?.trim()?.takeIf { it.isNotEmpty() } ?: "direct"
            val dpUnitId = rawPrefs["slot_dp_unit_id"]?.trim()?.takeIf { it.isNotEmpty() } ?: "dp_default"
            val tenantId = rawPrefs["active_tenant_id"]?.trim()?.takeIf { it.isNotEmpty() } ?: "ten_default"
            val vaultId = rawPrefs["active_vault_id"]?.trim()?.takeIf { it.isNotEmpty() } ?: "vlt_default"

            return mapOf(
                "accessToken" to cleanToken,
                "refreshToken" to refreshToken,
                "expiresAt" to expiresAt,
                "baseUrl" to cleanBaseUrl,
                "slotConnectionType" to connectionType,
                "slotDpUnitId" to dpUnitId,
                "slotTenantId" to tenantId,
                "slotVaultId" to vaultId,
                "vaultId" to vaultId
            )
        }
    }

    private var channel: MethodChannel? = null
    private var context: Context? = null
    private var activity: Activity? = null
    private var activityBinding: ActivityPluginBinding? = null
    private var pendingPermissionResult: Result? = null

    private val mainHandler = Handler(Looper.getMainLooper())
    private val ioExecutor = Executors.newSingleThreadExecutor()
    private val audioHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    @Volatile
    private var mediaPlayer: MediaPlayer? = null

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
        stopAudioPlaybackInternal()
        channel?.setMethodCallHandler(null)
        channel = null
        context = null
        Log.i("NativeBridgePlugin", "Detached from FlutterEngine")
    }

    override fun onAttachedToActivity(binding: ActivityPluginBinding) {
        activity = binding.activity
        activityBinding = binding
        binding.addRequestPermissionsResultListener(this)
    }

    override fun onDetachedFromActivityForConfigChanges() {
        activityBinding?.removeRequestPermissionsResultListener(this)
        activityBinding = null
        activity = null
    }

    override fun onReattachedToActivityForConfigChanges(binding: ActivityPluginBinding) {
        activity = binding.activity
        activityBinding = binding
        binding.addRequestPermissionsResultListener(this)
    }

    override fun onDetachedFromActivity() {
        activityBinding?.removeRequestPermissionsResultListener(this)
        activityBinding = null
        activity = null
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ): Boolean {
        if (requestCode != PERMISSION_REQUEST_CODE) return false
        val pending = pendingPermissionResult ?: return true
        pendingPermissionResult = null

        val ctx = context
        val hasMic = if (ctx != null) {
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
        } else {
            permissions.indices.any { idx ->
                permissions[idx] == Manifest.permission.RECORD_AUDIO &&
                    grantResults.getOrNull(idx) == PackageManager.PERMISSION_GRANTED
            }
        }
        pending.success(hasMic)
        return true
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
            "ensurePermissions" -> handleEnsurePermissions(ctx, result)
            "startRecording" -> {
                val hasMic = ContextCompat.checkSelfPermission(
                    ctx,
                    Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED
                if (!hasMic) {
                    result.error("PERMISSION_DENIED", "RECORD_AUDIO permission not granted", null)
                    return
                }

                val prefs = ctx.getSharedPreferences(HemoryPreferences.PREFS_NAME, Context.MODE_PRIVATE)
                val recordingId = call.argument<String>("recordingId")?.takeIf { it.isNotBlank() }
                    ?: "rec_${System.currentTimeMillis()}"
                val tenantId = call.argument<String>("tenantId")?.takeIf { it.isNotBlank() }
                    ?: prefs.getString("active_tenant_id", "ten_default")?.takeIf { it.isNotBlank() }
                    ?: "ten_default"
                val vaultId = call.argument<String>("vaultId")?.takeIf { it.isNotBlank() }
                    ?: prefs.getString("active_vault_id", "vlt_default")?.takeIf { it.isNotBlank() }
                    ?: "vlt_default"
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
            "storeAuthTokens", "storeAuth" -> handleStoreAuth(ctx, call, result)
            "getAuth" -> result.success(getStoredAuth(ctx))
            "clearAuth" -> {
                clearStoredAuth(ctx)
                result.success(true)
            }
            "signOutAuth" -> {
                RecordingForegroundService.stop(ctx, "signOut")
                stopAudioPlaybackInternal()
                clearStoredAuth(ctx)
                result.success(true)
            }
            "getUploadQueueStats" -> {
                val entries = LocalAtomicUploadQueue(ctx).list()
                var pendingCount = 0
                var uploadingCount = 0
                var failedCount = 0
                var uploadedCount = 0
                for (entry in entries) {
                    when (entry.status) {
                        UploadEntryState.Pending -> pendingCount++
                        UploadEntryState.Uploading -> uploadingCount++
                        UploadEntryState.FailedRecoverable, UploadEntryState.FailedPermanent -> failedCount++
                        UploadEntryState.Completed -> uploadedCount++
                    }
                }
                result.success(
                    mapOf(
                        "pending" to pendingCount,
                        "uploading" to uploadingCount,
                        "failed" to failedCount,
                        "uploaded" to uploadedCount
                    )
                )
            }
            "retryFailedUploads" -> {
                val queue = LocalAtomicUploadQueue(ctx)
                var retried = 0
                for (entry in queue.list()) {
                    val canRetry = when (entry.status) {
                        UploadEntryState.Pending, UploadEntryState.FailedRecoverable -> true
                        UploadEntryState.FailedPermanent -> {
                            File(entry.filePath).exists() && File(entry.filePath).length() > 0L
                        }
                        else -> false
                    }
                    if (canRetry) {
                        queue.update(entry.uploadId) {
                            it.copy(
                                status = UploadEntryState.Pending,
                                retryCount = 0,
                                authRetryCount = 0,
                                lastError = null
                            )
                        }
                        runCatching { UploadScheduler.enqueueSegment(ctx, entry.uploadId) }
                        retried++
                    }
                }
                result.success(retried)
            }
            "playAudioUrl" -> handlePlayAudioUrl(ctx, call, result)
            "stopAudioPlayback" -> {
                stopAudioPlaybackInternal()
                result.success(true)
            }
            else -> result.notImplemented()
        }
    }

    private fun handleEnsurePermissions(ctx: Context, result: Result) {
        val missing = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            missing.add(Manifest.permission.RECORD_AUDIO)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) !=
                PackageManager.PERMISSION_GRANTED
            ) {
                missing.add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }

        if (missing.isEmpty()) {
            result.success(true)
            return
        }

        val act = activity
        if (act == null) {
            val hasMic = !missing.contains(Manifest.permission.RECORD_AUDIO)
            result.success(hasMic)
            return
        }

        pendingPermissionResult?.success(false)
        pendingPermissionResult = result
        ActivityCompat.requestPermissions(act, missing.toTypedArray(), PERMISSION_REQUEST_CODE)
    }

    private fun handlePlayAudioUrl(ctx: Context, call: MethodCall, result: Result) {
        val url = call.argument<String>("url")?.trim().orEmpty()
        if (url.isEmpty()) {
            result.error("INVALID_ARGS", "Audio url must not be blank", null)
            return
        }
        val authToken = call.argument<String>("authToken")?.trim().orEmpty()
        val extraHeaders = call.argument<Map<String, Any?>>("headers").orEmpty()

        ioExecutor.execute {
            try {
                val reqBuilder = Request.Builder().url(url)
                if (authToken.isNotEmpty()) {
                    reqBuilder.header("Authorization", "Bearer $authToken")
                }
                for ((k, v) in extraHeaders) {
                    val strVal = v?.toString()?.trim().orEmpty()
                    if (k.isNotBlank() && strVal.isNotEmpty()) {
                        reqBuilder.header(k, strVal)
                    }
                }

                val previewFile = File(ctx.cacheDir, "hemory_voice_preview.aac")
                audioHttpClient.newCall(reqBuilder.build()).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        mainHandler.post {
                            result.error("AUDIO_HTTP_${resp.code}", "Audio fetch failed (${resp.code})", null)
                        }
                        return@execute
                    }
                    val body = resp.body ?: run {
                        mainHandler.post {
                            result.error("AUDIO_EMPTY", "Audio response body was empty", null)
                        }
                        return@execute
                    }
                    FileOutputStream(previewFile).use { out ->
                        body.byteStream().copyTo(out)
                    }
                }

                mainHandler.post {
                    try {
                        stopAudioPlaybackInternal()
                        val mp = MediaPlayer()
                        mp.setDataSource(previewFile.absolutePath)
                        mp.setOnCompletionListener { player ->
                            runCatching { player.release() }
                            if (mediaPlayer === player) {
                                mediaPlayer = null
                            }
                        }
                        mp.setOnErrorListener { player, _, _ ->
                            runCatching { player.release() }
                            if (mediaPlayer === player) {
                                mediaPlayer = null
                            }
                            true
                        }
                        mp.prepare()
                        mp.start()
                        mediaPlayer = mp
                        result.success(true)
                    } catch (e: Exception) {
                        result.error("AUDIO_PLAYBACK_FAILED", e.message, null)
                    }
                }
            } catch (e: Exception) {
                mainHandler.post {
                    result.error("AUDIO_FETCH_FAILED", e.message, null)
                }
            }
        }
    }

    private fun stopAudioPlaybackInternal() {
        val current = mediaPlayer
        mediaPlayer = null
        if (current != null) {
            runCatching {
                if (current.isPlaying) {
                    current.stop()
                }
            }
            runCatching { current.release() }
        }
    }

    private fun handleStoreAuth(ctx: Context, call: MethodCall, result: Result) {
        val args = call.arguments as? Map<*, *> ?: run {
            result.error("INVALID_ARGS", "Expected auth map", null)
            return
        }

        val requiredKeys = listOf(
            "accessToken",
            "refreshToken",
            "expiresAt",
            "baseUrl",
            "slotConnectionType",
            "slotDpUnitId",
            "slotTenantId"
        )

        for (k in requiredKeys) {
            if (!args.containsKey(k) || args[k]?.toString().isNullOrBlank()) {
                result.error("MISSING_TUPLE_FIELD", "Missing required 7-tuple field: $k", null)
                return
            }
        }

        val tokenStr = args["accessToken"]?.toString()?.trim().orEmpty()
        val baseUrlStr = args["baseUrl"]?.toString()?.trim().orEmpty()
        val tenantIdStr = args["slotTenantId"]?.toString()?.trim().orEmpty()
        val vaultIdStr = (args["slotVaultId"] ?: args["vaultId"])
            ?.toString()
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: "vlt_default"

        val securePrefs = HemoryPreferences(ctx)
        securePrefs.token = tokenStr
        securePrefs.baseUrl = baseUrlStr

        val prefs = ctx.getSharedPreferences(HemoryPreferences.PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().apply {
            putString("refresh_token", args["refreshToken"]?.toString()?.trim())
            putString("expires_at", args["expiresAt"]?.toString()?.trim())
            putString("slot_connection_type", args["slotConnectionType"]?.toString()?.trim())
            putString("slot_dp_unit_id", args["slotDpUnitId"]?.toString()?.trim())
            putString("active_tenant_id", tenantIdStr)
            putString("active_vault_id", vaultIdStr)
            commit()
        }

        // Re-schedule any queued segments that now match the authenticated tenant & vault
        runCatching {
            val queue = LocalAtomicUploadQueue(ctx)
            for (entry in queue.list()) {
                if (entry.tenantId == tenantIdStr && entry.vaultId == vaultIdStr &&
                    (entry.status == UploadEntryState.Pending ||
                        entry.status == UploadEntryState.FailedRecoverable ||
                        (entry.status == UploadEntryState.FailedPermanent && entry.lastError?.code == "identity_mismatch"))
                ) {
                    queue.update(entry.uploadId) {
                        it.copy(
                            status = UploadEntryState.Pending,
                            retryCount = 0,
                            authRetryCount = 0,
                            lastError = null
                        )
                    }
                    UploadScheduler.enqueueSegment(ctx, entry.uploadId)
                }
            }
        }

        result.success(true)
    }

    private fun getStoredAuth(ctx: Context): Map<String, Any>? {
        val securePrefs = HemoryPreferences(ctx)
        val prefs = ctx.getSharedPreferences(HemoryPreferences.PREFS_NAME, Context.MODE_PRIVATE)
        val token = securePrefs.token
        if (token.isBlank()) return null

        val rawPrefs = mapOf(
            "refresh_token" to prefs.getString("refresh_token", null),
            "expires_at" to prefs.getString("expires_at", null),
            "slot_connection_type" to prefs.getString("slot_connection_type", null),
            "slot_dp_unit_id" to prefs.getString("slot_dp_unit_id", null),
            "active_tenant_id" to prefs.getString("active_tenant_id", null),
            "active_vault_id" to prefs.getString("active_vault_id", null)
        )
        return normalizeStoredAuthMap(
            token = token,
            baseUrl = securePrefs.baseUrl,
            rawPrefs = rawPrefs
        )
    }

    private fun clearStoredAuth(ctx: Context) {
        runCatching { HemoryPreferences(ctx).clear() }
        val prefs = ctx.getSharedPreferences(HemoryPreferences.PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().clear().commit()
    }
}
