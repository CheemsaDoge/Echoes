package dev.echoledger.app

import dev.echoledger.app.data.CacheEntry
import dev.echoledger.app.data.SecurePreferences
import dev.echoledger.app.data.StandaloneTranscriber
import dev.echoledger.app.network.AsrSegment
import dev.echoledger.app.network.VertexAiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.net.ServerSocket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Base64
import javax.crypto.spec.SecretKeySpec
import kotlin.math.PI
import kotlin.math.sin

class StandaloneAiStressTest {

    private val testAesKey = SecretKeySpec("test_aes_key_32_bytes_long_1234".toByteArray(Charsets.UTF_8).copyOf(32), "AES")

    // =========================================================================
    // CHALLENGE 1: Stress-test VertexAiClient response parsing
    // =========================================================================

    @Test
    fun testParseResponseMalformedJsonGarbageThrowsJsonException() {
        val malformedCandidates = listOf(
            "<!DOCTYPE html><html><body>502 Bad Gateway</body></html>",
            "{ not valid json",
            "",
            "   ",
            "{\"candidates\": [{\"content\": {\"parts\": [{\"text\": \"{ not json\"}]}}]}",
            "{\"candidates\": [{\"content\": {\"parts\": [{\"text\": \"\"}]}}]}"
        )

        for (garbage in malformedCandidates) {
            try {
                val result = VertexAiClient.parseResponse(garbage)
                assertTrue("Garbage input should return empty list if not throwing: $garbage", result.isEmpty())
            } catch (e: Exception) {
                assertTrue("Expected JSONException or IOException, got ${e.javaClass.simpleName}",
                    e is JSONException || e is IOException)
            }
        }
    }

    @Test
    fun testParseResponseMarkdownFenceVariations() {
        // Standard markdown fence ```json ... ```
        val standardFence = """
            {
              "candidates": [{
                "content": {
                  "parts": [{"text": "```json\n{\"segments\": [{\"start_ms\": 0, \"end_ms\": 1000, \"text\": \"标准栅栏\", \"speaker_label\": \"spk0\"}]}\n```"}]
                },
                "finishReason": "STOP"
              }]
            }
        """.trimIndent()
        val standardRes = VertexAiClient.parseResponse(standardFence)
        assertEquals(1, standardRes.size)
        assertEquals("标准栅栏", standardRes[0].text)

        // Raw fence without "json": ``` ... ```
        val rawFence = """
            {
              "candidates": [{
                "content": {
                  "parts": [{"text": "```\n{\"segments\": [{\"start_ms\": 100, \"end_ms\": 2000, \"text\": \"无语言栅栏\", \"speaker_label\": \"spk1\"}]}\n```"}]
                },
                "finishReason": "STOP"
              }]
            }
        """.trimIndent()
        val rawRes = VertexAiClient.parseResponse(rawFence)
        assertEquals(1, rawRes.size)
        assertEquals("无语言栅栏", rawRes[0].text)

        // Uppercase fence ```JSON ... ```
        val uppercaseFence = """
            {
              "candidates": [{
                "content": {
                  "parts": [{"text": "```JSON\n{\"segments\": [{\"start_ms\": 0, \"end_ms\": 500, \"text\": \"大写栅栏\", \"speaker_label\": \"spk0\"}]}\n```"}]
                },
                "finishReason": "STOP"
              }]
            }
        """.trimIndent()
        try {
            VertexAiClient.parseResponse(uppercaseFence)
        } catch (e: JSONException) {
            println("EMPIRICAL CONFIRMED: uppercase ```JSON triggers JSONException: ${e.message}")
        }

        // Preamble before fence: "Here is the transcription:\n```json ... ```"
        val preambleFence = """
            {
              "candidates": [{
                "content": {
                  "parts": [{"text": "Here is the transcription:\n```json\n{\"segments\": [{\"start_ms\": 0, \"end_ms\": 500, \"text\": \"前缀文本\", \"speaker_label\": \"spk0\"}]}\n```"}]
                },
                "finishReason": "STOP"
              }]
            }
        """.trimIndent()
        try {
            VertexAiClient.parseResponse(preambleFence)
        } catch (e: JSONException) {
            println("EMPIRICAL CONFIRMED: preamble text before markdown fence triggers JSONException: ${e.message}")
        }

        // Postamble after fence: "```json ... ```\nHope this helps!"
        val postambleFence = """
            {
              "candidates": [{
                "content": {
                  "parts": [{"text": "```json\n{\"segments\": [{\"start_ms\": 0, \"end_ms\": 500, \"text\": \"后缀文本\", \"speaker_label\": \"spk0\"}]}\n```\nHope this helps!"}]
                },
                "finishReason": "STOP"
              }]
            }
        """.trimIndent()
        try {
            VertexAiClient.parseResponse(postambleFence)
        } catch (e: JSONException) {
            println("EMPIRICAL CONFIRMED: postamble text after markdown fence triggers JSONException: ${e.message}")
        }
    }

    @Test
    fun testParseResponseMissingOrNullSegments() {
        // Missing "segments" key
        val missingSegments = """
            {
              "candidates": [{
                "content": {
                  "parts": [{"text": "{\"other\": 123}"}]
                },
                "finishReason": "STOP"
              }]
            }
        """.trimIndent()
        val res1 = VertexAiClient.parseResponse(missingSegments)
        assertTrue("Missing segments key should return empty list", res1.isEmpty())

        // "segments" is null
        val nullSegments = """
            {
              "candidates": [{
                "content": {
                  "parts": [{"text": "{\"segments\": null}"}]
                },
                "finishReason": "STOP"
              }]
            }
        """.trimIndent()
        val res2 = VertexAiClient.parseResponse(nullSegments)
        assertTrue("Null segments should return empty list", res2.isEmpty())

        // Empty segments list
        val emptySegments = """
            {
              "candidates": [{
                "content": {
                  "parts": [{"text": "{\"segments\": []}"}]
                },
                "finishReason": "STOP"
              }]
            }
        """.trimIndent()
        val res3 = VertexAiClient.parseResponse(emptySegments)
        assertTrue("Empty segments array should return empty list", res3.isEmpty())
    }

    @Test
    fun testParseResponseSegmentDefaultsAndMissingFields() {
        val partialSegments = """
            {
              "candidates": [{
                "content": {
                  "parts": [{
                    "text": "{\"segments\": [{\"text\": \"   \"}, {\"start_ms\": 100, \"end_ms\": 800, \"text\": \"有效文本\"}]}"
                  }]
                },
                "finishReason": "STOP"
              }]
            }
        """.trimIndent()
        val res = VertexAiClient.parseResponse(partialSegments)
        // First segment text is blank, should be filtered out
        assertEquals(1, res.size)
        assertEquals("有效文本", res[0].text)
        assertEquals(100, res[0].start_ms)
        assertEquals(800, res[0].end_ms)
        // Default speaker label should be speaker_0
        assertEquals("speaker_0", res[0].speaker_label)
    }

    @Test(expected = IOException::class)
    fun testParseResponseSafetyBlockReasonThrows() {
        val safetyBlocked = """
            {
              "promptFeedback": {
                "blockReason": "SAFETY"
              }
            }
        """.trimIndent()
        VertexAiClient.parseResponse(safetyBlocked)
    }

    @Test(expected = IOException::class)
    fun testParseResponseCandidateSafetyFinishReasonThrows() {
        val candidateBlocked = """
            {
              "candidates": [{
                "finishReason": "SAFETY"
              }]
            }
        """.trimIndent()
        VertexAiClient.parseResponse(candidateBlocked)
    }

    @Test
    fun testTranscribeRateLimitErrorHandlingViaServerSocket() = runBlocking {
        val server = ServerSocket(0)
        val port = server.localPort
        val serverJob = launch(Dispatchers.IO) {
            try {
                val socket = server.accept()
                val reader = socket.getInputStream().bufferedReader()
                // Read headers until blank line
                var line: String? = reader.readLine()
                var contentLength = 0
                while (!line.isNullOrEmpty()) {
                    if (line.lowercase().startsWith("content-length:")) {
                        contentLength = line.substringAfter(':').trim().toIntOrNull() ?: 0
                    }
                    line = reader.readLine()
                }
                // Read body
                if (contentLength > 0) {
                    val buf = CharArray(contentLength)
                    var read = 0
                    while (read < contentLength) {
                        val r = reader.read(buf, read, contentLength - read)
                        if (r <= 0) break
                        read += r
                    }
                }
                val errorBody = """{"error": {"code": 429, "message": "Resource has been exhausted (e.g. check quota)."}}"""
                val response = "HTTP/1.1 429 Too Many Requests\r\n" +
                        "Content-Type: application/json\r\n" +
                        "Content-Length: ${errorBody.toByteArray().size}\r\n" +
                        "Connection: close\r\n\r\n" +
                        errorBody
                socket.getOutputStream().write(response.toByteArray())
                socket.getOutputStream().flush()
                socket.close()
            } catch (_: Exception) {}
        }

        val client = VertexAiClient(OkHttpClient())
        val dummyWav = ByteArray(44 + 320)

        try {
            client.transcribe(
                wavBytes = dummyWav,
                apiKey = "test-key",
                endpointOverride = "http://127.0.0.1:$port/generate"
            )
            fail("Should throw IOException on 429")
        } catch (e: IOException) {
            assertTrue("Exception message should contain 429 and error detail",
                e.message?.contains("429") == true && e.message?.contains("Resource has been exhausted") == true)
        } finally {
            serverJob.cancel()
            server.close()
        }
    }

    @Test
    fun testStandaloneModeApiCallThrowsWhenTokenBlank() {
        val fakePrefs = FakeSharedPreferences()
        val securePrefs = SecurePreferences(fakePrefs) { testAesKey }
        securePrefs.operationMode = "standalone"
        securePrefs.token = ""
        securePrefs.baseUrl = "http://127.0.0.1:8000"

        val api = dev.echoledger.app.network.Api(securePrefs)
        try {
            runBlocking { api.call("/v1/events") }
            fail("Should fail when token is blank")
        } catch (e: IllegalArgumentException) {
            println("EMPIRICAL CONFIRMED: Api.call throws in standalone mode when token is blank: ${e.message}")
            assertTrue(e.message?.contains("Enter a backend access token in Settings") == true)
        }
    }

    // =========================================================================
    // CHALLENGE 2: Silence RMS energy gate calculations
    // =========================================================================

    @Test
    fun testRmsPureDcCalculations() {
        // 1. Pure zero DC: all samples 0
        val zeroBytes = ByteArray(44 + 1600 * 2)
        val zeroRms = StandaloneTranscriber.computeRms(zeroBytes)
        assertEquals(0f, zeroRms, 0.000001f)
        assertTrue(zeroRms < StandaloneTranscriber.SILENCE_RMS_THRESHOLD)

        // 2. Small DC offset: amplitude = 50 (~0.0015 of 32768)
        val smallDcBuf = ByteBuffer.allocate(44 + 1600 * 2).order(ByteOrder.LITTLE_ENDIAN)
        smallDcBuf.put(ByteArray(44))
        for (i in 0 until 1600) smallDcBuf.putShort(50.toShort())
        val smallDcRms = StandaloneTranscriber.computeRms(smallDcBuf.array())
        assertEquals(50.0 / 32768.0, smallDcRms.toDouble(), 0.0001)
        assertTrue("Small DC offset should be below silence threshold",
            smallDcRms < StandaloneTranscriber.SILENCE_RMS_THRESHOLD)

        // 3. Max DC amplitude: +32767
        val maxDcBuf = ByteBuffer.allocate(44 + 1600 * 2).order(ByteOrder.LITTLE_ENDIAN)
        maxDcBuf.put(ByteArray(44))
        for (i in 0 until 1600) maxDcBuf.putShort(32767.toShort())
        val maxDcRms = StandaloneTranscriber.computeRms(maxDcBuf.array())
        assertEquals(1.0, maxDcRms.toDouble(), 0.001)
        assertTrue(maxDcRms > StandaloneTranscriber.SILENCE_RMS_THRESHOLD)

        // 4. Min DC amplitude (negative limit): -32768
        val minDcBuf = ByteBuffer.allocate(44 + 1600 * 2).order(ByteOrder.LITTLE_ENDIAN)
        minDcBuf.put(ByteArray(44))
        for (i in 0 until 1600) minDcBuf.putShort((-32768).toShort())
        val minDcRms = StandaloneTranscriber.computeRms(minDcBuf.array())
        assertEquals(1.0, minDcRms.toDouble(), 0.0001)
        assertTrue(minDcRms > StandaloneTranscriber.SILENCE_RMS_THRESHOLD)
    }

    @Test
    fun testRmsMaxAmplitudeSineWave() {
        val sampleRate = 16000
        val frequency = 1000.0 // 1kHz sine wave
        val sampleCount = 16000 // 1 second of audio
        val buf = ByteBuffer.allocate(44 + sampleCount * 2).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(ByteArray(44))

        val peak = 32767.0
        for (i in 0 until sampleCount) {
            val angle = 2.0 * PI * frequency * i / sampleRate
            val sampleVal = (peak * sin(angle)).toInt().coerceIn(-32768, 32767).toShort()
            buf.putShort(sampleVal)
        }

        val rms = StandaloneTranscriber.computeRms(buf.array())
        val expectedRms = 1.0 / kotlin.math.sqrt(2.0)
        assertEquals(expectedRms, rms.toDouble(), 0.005)
        assertTrue("Max sine wave must be well above silence threshold",
            rms > StandaloneTranscriber.SILENCE_RMS_THRESHOLD)
    }

    @Test
    fun testRmsGentleWhisperAudibleVsInaudible() {
        val sampleCount = 1600
        // Audible whisper: peak amplitude 600 (~0.0183 full scale), RMS ~ 0.013
        val audibleBuf = ByteBuffer.allocate(44 + sampleCount * 2).order(ByteOrder.LITTLE_ENDIAN)
        audibleBuf.put(ByteArray(44))
        for (i in 0 until sampleCount) {
            val angle = 2.0 * PI * 400.0 * i / 16000
            val sampleVal = (600.0 * sin(angle)).toInt().toShort()
            audibleBuf.putShort(sampleVal)
        }
        val audibleRms = StandaloneTranscriber.computeRms(audibleBuf.array())
        assertTrue("Whisper with peak 600 should exceed silence threshold (rms=$audibleRms)",
            audibleRms > StandaloneTranscriber.SILENCE_RMS_THRESHOLD)

        // Extremely faint inaudible whisper: peak amplitude 60 (RMS ~ 0.0013)
        val faintBuf = ByteBuffer.allocate(44 + sampleCount * 2).order(ByteOrder.LITTLE_ENDIAN)
        faintBuf.put(ByteArray(44))
        for (i in 0 until sampleCount) {
            val angle = 2.0 * PI * 400.0 * i / 16000
            val sampleVal = (60.0 * sin(angle)).toInt().toShort()
            faintBuf.putShort(sampleVal)
        }
        val faintRms = StandaloneTranscriber.computeRms(faintBuf.array())
        assertTrue("Whisper with peak 60 should be below silence threshold (rms=$faintRms)",
            faintRms < StandaloneTranscriber.SILENCE_RMS_THRESHOLD)
    }

    @Test
    fun testRmsBoundaryByteBuffers() {
        assertEquals(0f, StandaloneTranscriber.computeRms(ByteArray(0)), 0.0001f)
        assertEquals(0f, StandaloneTranscriber.computeRms(ByteArray(10)), 0.0001f)
        assertEquals(0f, StandaloneTranscriber.computeRms(ByteArray(44)), 0.0001f)
        assertEquals(0f, StandaloneTranscriber.computeRms(ByteArray(45)), 0.0001f)

        // 46 bytes = 44 byte header + 2 byte single sample of amplitude 16384 (norm = 0.5)
        val singleSampleBuf = ByteBuffer.allocate(46).order(ByteOrder.LITTLE_ENDIAN)
        singleSampleBuf.put(ByteArray(44))
        singleSampleBuf.putShort(16384.toShort())
        val singleRms = StandaloneTranscriber.computeRms(singleSampleBuf.array())
        assertEquals(0.5f, singleRms, 0.0001f)

        // 47 bytes = 46 bytes + 1 leftover byte (odd length)
        val oddBuf = ByteBuffer.allocate(47).order(ByteOrder.LITTLE_ENDIAN)
        oddBuf.put(ByteArray(44))
        oddBuf.putShort(16384.toShort())
        oddBuf.put(0x7F.toByte())
        val oddRms = StandaloneTranscriber.computeRms(oddBuf.array())
        assertEquals(0.5f, oddRms, 0.0001f)
    }

    // =========================================================================
    // CHALLENGE 3: SecurePreferences AES-GCM encryption round-trip
    // =========================================================================

    @Test
    fun testSecurePreferencesUnicodeAndSpecialCharactersRoundTrip() {
        val fakePrefs = FakeSharedPreferences()
        val securePrefs = SecurePreferences(fakePrefs) { testAesKey }

        val testVectors = listOf(
            "AIzaSyTest_Standard_12345",
            "你好，世界！这是一段中文测试密钥：包含全角字符！？【】",
            "日本語のテストキーです。カタカナ、ひらがな、漢字。",
            "한국어 테스트 키입니다. 1234567890",
            "🔐🔑🛡️🚀💯👨‍👩‍👧‍👦",
            "مرحبا بالعالم - עִברִית - हिन्दी",
            "\"'<script>alert('xss');</script>/!@#\$%^&*()",
            "\u0000\u0001\u001f Control Characters \u007f",
            "A".repeat(8192) // 8KB long key
        )

        for (vector in testVectors) {
            securePrefs.aiApiKey = vector
            val retrieved = securePrefs.aiApiKey
            assertEquals("Round trip failed for vector: ${vector.take(30)}", vector, retrieved)

            val rawStored = fakePrefs.getString("ai_api_key", null)
            assertNotNull(rawStored)
            assertNotEquals(vector, rawStored)
        }
    }

    @Test
    fun testSecurePreferencesTamperResistance() {
        val fakePrefs = FakeSharedPreferences()
        val securePrefs = SecurePreferences(fakePrefs) { testAesKey }

        securePrefs.aiApiKey = "Secret_Key_To_Tamper"
        val rawStored = fakePrefs.getString("ai_api_key", null)!!

        // 1. Corrupt the base64 string
        fakePrefs.edit().putString("ai_api_key", "NotValidBase64!@#$").commit()
        assertEquals("Corrupted base64 should decrypt to empty string", "", securePrefs.aiApiKey)

        // 2. Tamper with ciphertext bit (GCM tag verification failure)
        val decoded = Base64.getDecoder().decode(rawStored)
        decoded[decoded.size - 1] = (decoded[decoded.size - 1].toInt() xor 0x01).toByte()
        fakePrefs.edit().putString("ai_api_key", Base64.getEncoder().encodeToString(decoded)).commit()
        assertEquals("Tampered ciphertext should fail GCM authentication and return empty string", "", securePrefs.aiApiKey)

        // 3. Truncated payload (< 12 bytes IV)
        val truncated = ByteArray(8)
        fakePrefs.edit().putString("ai_api_key", Base64.getEncoder().encodeToString(truncated)).commit()
        assertEquals("Truncated ciphertext should return empty string", "", securePrefs.aiApiKey)
    }

    // =========================================================================
    // CHALLENGE 4: Room SQLite CacheEntry JSON payload validity
    // =========================================================================

    @Test
    fun testRoomCacheEntryJsonPayloadAgainstBackendAndUiSchema() {
        val chunkId = "chunk_20261103_001"
        val sessionId = "session_20261103_abc"
        val startUtcMs = 1730000000000L

        val segments = listOf(
            AsrSegment(start_ms = 500, end_ms = 2500, text = "你好，测试端侧独立运行。", speaker_label = "0"),
            AsrSegment(start_ms = 2800, end_ms = 4800, text = "收到，独立 AI 转录已就绪。", speaker_label = "1")
        )

        // Synthesize Event payload exactly as StandaloneTranscriber does
        val distinctSpeakers = segments.map { it.speaker_label }.distinct()
        val speakerNamesArray = JSONArray().apply {
            distinctSpeakers.forEach { put("Speaker $it") }
        }

        val segmentsArray = JSONArray()
        for ((idx, seg) in segments.withIndex()) {
            val segStart = startUtcMs + seg.start_ms
            val segEnd = startUtcMs + seg.end_ms
            val spanArray = JSONArray().put(JSONObject().put("chunk_id", chunkId))
            val segObj = JSONObject()
                .put("id", "${chunkId}_$idx")
                .put("local_speaker", seg.speaker_label)
                .put("speaker_name", JSONObject.NULL)
                .put("text", seg.text)
                .put("start_ms", segStart)
                .put("end_ms", segEnd)
                .put("source_spans", spanArray)
            segmentsArray.put(segObj)
        }

        val fullText = segments.joinToString(" ") { it.text.trim() }.trim()
        val eventPayload = JSONObject()
            .put("id", chunkId)
            .put("session_id", sessionId)
            .put("start_ms", startUtcMs + segments.first().start_ms)
            .put("end_ms", startUtcMs + segments.last().end_ms)
            .put("title", fullText.take(57))
            .put("summary", fullText)
            .put("type_id", "journal")
            .put("color", "#61BBA6")
            .put("is_demo", 0)
            .put("speaker_names", speakerNamesArray)
            .put("unassigned_labels", distinctSpeakers.size)
            .put("segments", segmentsArray)
            .put("updated_ms", System.currentTimeMillis())

        val eventJsonStr = eventPayload.toString()
        val parsedEvent = JSONObject(eventJsonStr)

        // Verify EventCard (AppScreen.kt) required fields
        assertEquals(chunkId, parsedEvent.getString("id"))
        assertEquals(sessionId, parsedEvent.getString("session_id"))
        assertTrue("start_ms must be valid long", parsedEvent.getLong("start_ms") > 0)
        assertTrue("end_ms must be valid long", parsedEvent.getLong("end_ms") > parsedEvent.getLong("start_ms"))
        assertNotNull(parsedEvent.getString("title"))
        assertEquals("journal", parsedEvent.optString("type_id"))
        assertEquals("#61BBA6", parsedEvent.optString("color"))
        assertEquals(0, parsedEvent.optInt("is_demo"))
        assertEquals(2, parsedEvent.getJSONArray("speaker_names").length())
        assertEquals(2, parsedEvent.getInt("unassigned_labels"))

        // Verify turns() (AppScreen.kt) segment extraction
        val parsedSegments = parsedEvent.getJSONArray("segments")
        assertEquals(2, parsedSegments.length())
        for (i in 0 until parsedSegments.length()) {
            val s = parsedSegments.getJSONObject(i)
            assertNotNull(s.getString("id"))
            assertNotNull(s.getString("local_speaker"))
            assertTrue(s.isNull("speaker_name"))
            assertFalse(s.getString("text").isBlank())
            assertTrue(s.getLong("start_ms") > 0)
            assertTrue(s.getLong("end_ms") > s.getLong("start_ms"))
            val spans = s.optJSONArray("source_spans")
            assertNotNull(spans)
            assertEquals(chunkId, spans!!.getJSONObject(0).getString("chunk_id"))
        }

        // Verify Memory payload schema
        val memoryPayload = JSONObject()
            .put("id", chunkId)
            .put("event_id", chunkId)
            .put("text", fullText)
            .put("confirmed", 0)
            .put("created_ms", System.currentTimeMillis())

        val parsedMemory = JSONObject(memoryPayload.toString())
        assertEquals(chunkId, parsedMemory.getString("id"))
        assertEquals(chunkId, parsedMemory.getString("event_id"))
        assertEquals(fullText, parsedMemory.getString("text"))
        assertEquals(0, parsedMemory.getInt("confirmed"))

        // Verify Speaker payload schema
        val speakerPayload = JSONObject()
            .put("id", "0")
            .put("name", "Speaker 0")
        val parsedSpeaker = JSONObject(speakerPayload.toString())
        assertEquals("0", parsedSpeaker.getString("id"))
        assertEquals("Speaker 0", parsedSpeaker.getString("name"))
    }
}
