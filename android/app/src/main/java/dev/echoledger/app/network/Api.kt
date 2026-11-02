package dev.echoledger.app.network

import dev.echoledger.app.BuildConfig
import dev.echoledger.app.data.LocalChunk
import dev.echoledger.app.data.SecurePreferences
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

class ApiFailure(val status: Int, message: String) : IOException(message)

class Api(private val prefs: SecurePreferences) {
    private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(150, TimeUnit.SECONDS).writeTimeout(60, TimeUnit.SECONDS).build()
    private val streamHttp = http.newBuilder().readTimeout(0, TimeUnit.SECONDS).build()
    fun validateBase(value: String) {
        val url = value.toHttpUrlOrNull() ?: error("Invalid server URL")
        require(url.username.isEmpty() && url.password.isEmpty() && url.query == null && url.fragment == null)
        require(BuildConfig.DEBUG || url.isHttps) { "Release builds require HTTPS" }
    }
    private fun request(path: String): Request.Builder {
        validateBase(prefs.baseUrl)
        require(prefs.token.isNotEmpty()) { "Enter a backend access token in Settings" }
        return Request.Builder().url(prefs.baseUrl.trimEnd('/') + path).header("Authorization", "Bearer " + prefs.token)
    }
    private fun read(response: Response): String {
        response.use {
            val body = it.body?.string().orEmpty()
            if (!it.isSuccessful) {
                val message = runCatching { JSONObject(body).optString("detail") }.getOrDefault("Server request failed")
                throw ApiFailure(it.code, message.take(180))
            }
            return body
        }
    }
    suspend fun call(path: String, method: String = "GET", body: JSONObject? = null): String = withContext(Dispatchers.IO) {
        val req = request(path)
        if (method == "GET") req.get()
        else if (method == "DELETE") req.delete()
        else req.method(method, (body?.toString() ?: "{}").toRequestBody("application/json".toMediaType()))
        read(http.newCall(req.build()).execute())
    }
    suspend fun upload(chunk: LocalChunk): String = withContext(Dispatchers.IO) {
        val metadata = JSONObject().put("id", chunk.id).put("start_sample", chunk.startSample)
            .put("sample_count", chunk.sampleCount).put("sha256", chunk.sha256)
            .put("start_monotonic_ns", chunk.startMonotonicNs).put("clock_drift_ms", chunk.clockDriftMs)
        val req = request("/v1/sessions/${chunk.sessionId}/chunks/${chunk.seq}")
            .header("X-Chunk-Metadata", metadata.toString())
            .post(File(chunk.path).asRequestBody("audio/wav".toMediaType())).build()
        read(http.newCall(req).execute())
    }
    fun changes(): Flow<Long> = callbackFlow {
        val req = request("/v1/updates?after=${prefs.cursor}").header("Accept", "text/event-stream").build()
        val source = EventSources.createFactory(streamHttp).newEventSource(req, object : EventSourceListener() {
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                id?.toLongOrNull()?.let { trySend(it) }
            }
            override fun onClosed(eventSource: EventSource) { close(IOException("Event stream closed")) }
            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                close(IOException("Event stream unavailable"))
            }
        })
        awaitClose { source.cancel() }
    }
}
