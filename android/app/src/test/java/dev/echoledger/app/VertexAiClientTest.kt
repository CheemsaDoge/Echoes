package dev.echoledger.app

import dev.echoledger.app.network.AsrSegment
import dev.echoledger.app.network.VertexAiClient
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException

class VertexAiClientTest {

    @Test
    fun testResolveEndpointGoogleAiStudio() {
        val endpoint = VertexAiClient.resolveEndpoint(
            provider = "gemini_studio",
            model = "gemini-2.0-flash",
            apiKey = "AIzaSyTest123"
        )
        assertEquals(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=AIzaSyTest123",
            endpoint
        )
    }

    @Test
    fun testResolveEndpointVertexAiGlobal() {
        val endpoint = VertexAiClient.resolveEndpoint(
            provider = "vertex_ai",
            model = "gemini-2.0-flash",
            apiKey = "test-key",
            projectId = "my-test-proj",
            location = "global"
        )
        assertEquals(
            "https://aiplatform.googleapis.com/v1/projects/my-test-proj/locations/global/publishers/google/models/gemini-2.0-flash:generateContent",
            endpoint
        )
    }

    @Test
    fun testResolveEndpointVertexAiRegional() {
        val endpoint = VertexAiClient.resolveEndpoint(
            provider = "vertex_ai",
            model = "gemini-1.5-pro",
            apiKey = "test-key",
            projectId = "my-test-proj",
            location = "us-central1"
        )
        assertEquals(
            "https://us-central1-aiplatform.googleapis.com/v1/projects/my-test-proj/locations/us-central1/publishers/google/models/gemini-1.5-pro:generateContent",
            endpoint
        )
    }

    @Test
    fun testResolveEndpointCustomOverride() {
        val endpoint = VertexAiClient.resolveEndpoint(
            provider = "vertex_ai",
            model = "gemini-2.0-flash",
            apiKey = "key",
            endpointOverride = "https://custom-gateway.internal/v1/generate"
        )
        assertEquals("https://custom-gateway.internal/v1/generate", endpoint)
    }

    @Test
    fun testBuildRequestBodyContainsAudioAndSchema() {
        val dummyAudioB64 = "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="
        val body = VertexAiClient.buildRequestBody(dummyAudioB64)

        // Contents verification
        val contents = body.getJSONArray("contents")
        assertEquals(1, contents.length())
        val parts = contents.getJSONObject(0).getJSONArray("parts")
        val inlineData = parts.getJSONObject(0).getJSONObject("inlineData")
        assertEquals("audio/wav", inlineData.getString("mimeType"))
        assertEquals(dummyAudioB64, inlineData.getString("data"))

        // Safety settings
        val safetySettings = body.getJSONArray("safetySettings")
        assertEquals(4, safetySettings.length())
        for (i in 0 until safetySettings.length()) {
            assertEquals("BLOCK_NONE", safetySettings.getJSONObject(i).getString("threshold"))
        }

        // Generation config
        val genConfig = body.getJSONObject("generationConfig")
        assertEquals(0.0, genConfig.getDouble("temperature"), 0.001)
        assertEquals("application/json", genConfig.getString("responseMimeType"))
        assertTrue(genConfig.has("responseSchema"))
        assertFalse("Gemini Studio must omit audioTimestamp to prevent HTTP 400", genConfig.has("audioTimestamp"))

        // Vertex AI includeAudioTimestamp
        val vertexBody = VertexAiClient.buildRequestBody(dummyAudioB64, includeAudioTimestamp = true)
        assertTrue(vertexBody.getJSONObject("generationConfig").getBoolean("audioTimestamp"))
    }

    @Test
    fun testParseResponseValidSegments() {
        val sampleJson = """
            {
              "candidates": [
                {
                  "content": {
                    "parts": [
                      {
                        "text": "{\"segments\": [{\"start_ms\": 500, \"end_ms\": 3200, \"text\": \"早安，今天开会讨论产品。\", \"speaker_label\": \"speaker_1\"}, {\"start_ms\": 3500, \"end_ms\": 5100, \"text\": \"好的，先看指标。\", \"speaker_label\": \"speaker_2\"}]}"
                      }
                    ]
                  },
                  "finishReason": "STOP"
                }
              ]
            }
        """.trimIndent()

        val segments = VertexAiClient.parseResponse(sampleJson)
        assertEquals(2, segments.size)

        assertEquals(500, segments[0].start_ms)
        assertEquals(3200, segments[0].end_ms)
        assertEquals("早安，今天开会讨论产品。", segments[0].text)
        assertEquals("speaker_1", segments[0].speaker_label)

        assertEquals(3500, segments[1].start_ms)
        assertEquals(5100, segments[1].end_ms)
        assertEquals("好的，先看指标。", segments[1].text)
        assertEquals("speaker_2", segments[1].speaker_label)
    }

    @Test
    fun testParseResponseMarkdownWrapped() {
        val wrappedJson = """
            {
              "candidates": [
                {
                  "content": {
                    "parts": [
                      {
                        "text": "```json\n{\"segments\": [{\"start_ms\": 0, \"end_ms\": 1500, \"text\": \"测试转写\", \"speaker_label\": \"0\"}]}\n```"
                      }
                    ]
                  },
                  "finishReason": "STOP"
                }
              ]
            }
        """.trimIndent()

        val segments = VertexAiClient.parseResponse(wrappedJson)
        assertEquals(1, segments.size)
        assertEquals("测试转写", segments[0].text)
        assertEquals(0, segments[0].start_ms)
        assertEquals(1500, segments[0].end_ms)
    }

    @Test
    fun testParseResponseEmptyCandidates() {
        val emptyJson = """{"candidates": []}"""
        val segments = VertexAiClient.parseResponse(emptyJson)
        assertTrue(segments.isEmpty())
    }

    @Test(expected = IOException::class)
    fun testParseResponseBlockedPrompt() {
        val blockedJson = """
            {
              "promptFeedback": {
                "blockReason": "SAFETY"
              }
            }
        """.trimIndent()
        VertexAiClient.parseResponse(blockedJson)
    }

    @Test
    fun testExtractJsonVariations() {
        // Uppercase fence
        val upper = "```JSON\n{\"segments\": []}\n```"
        assertEquals("{\"segments\": []}", VertexAiClient.extractJson(upper))

        // Preamble + lowercase fence + postamble
        val preambleAndPostamble = "Here is the result:\n```json\n{\"segments\": [{\"text\": \"hello\"}]}\n```\nHope it helps!"
        assertEquals("{\"segments\": [{\"text\": \"hello\"}]}", VertexAiClient.extractJson(preambleAndPostamble))

        // No fence, embedded in explanation
        val noFence = "Sure thing! {\"segments\": []} is the answer."
        assertEquals("{\"segments\": []}", VertexAiClient.extractJson(noFence))

        // Pure JSON
        val pure = "{\"segments\": []}"
        assertEquals("{\"segments\": []}", VertexAiClient.extractJson(pure))
    }
}
