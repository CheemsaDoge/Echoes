package dev.echoledger.app.data

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import dev.echoledger.app.BuildConfig
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Secure preferences storing backend connection tokens and cloud AI provider keys.
 * Sensitive keys are encrypted using AndroidKeyStore AES-256-GCM.
 */
class SecurePreferences(
    private val prefs: SharedPreferences,
    private val keyProvider: ((alias: String) -> SecretKey)? = null
) {
    constructor(context: Context) : this(
        context.getSharedPreferences("connection", Context.MODE_PRIVATE),
        null
    )
    private val tokenAlias = "echoledger_backend_token_v1"
    private val aiKeyAlias = "echoledger_ai_api_key_v1"
    private val mcpTokenAlias = "echoledger_mcp_token_v1"

    private fun getSecretKey(alias: String): SecretKey {
        if (keyProvider != null) {
            return keyProvider.invoke(alias)
        }
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return generator.generateKey()
    }

    private fun decryptString(prefKey: String, alias: String): String {
        val stored = prefs.getString(prefKey, null) ?: return ""
        return try {
            val packed = Base64.getDecoder().decode(stored)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, getSecretKey(alias), GCMParameterSpec(128, packed.copyOfRange(0, 12)))
            String(cipher.doFinal(packed.copyOfRange(12, packed.size)), Charsets.UTF_8)
        } catch (_: Exception) {
            ""
        }
    }

    private fun encryptString(prefKey: String, alias: String, value: String) {
        if (value.isBlank()) {
            prefs.edit().remove(prefKey).commit()
            return
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, getSecretKey(alias))
        val packed = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        prefs.edit().putString(prefKey, Base64.getEncoder().encodeToString(packed)).commit()
    }

    var baseUrl: String
        get() = prefs.getString("url", if (BuildConfig.DEBUG) "http://10.0.2.2:8000" else "") ?: ""
        set(value) { prefs.edit().putString("url", value.trim().trimEnd('/')).commit() }

    var token: String
        get() = decryptString("token", tokenAlias)
        set(value) { encryptString("token", tokenAlias, value.trim()) }

    var cursor: Long
        get() = prefs.getLong("cursor", 0)
        set(value) { prefs.edit().putLong("cursor", value).apply() }

    var operationMode: String
        get() = prefs.getString("operation_mode", "standalone") ?: "standalone"
        set(value) { prefs.edit().putString("operation_mode", value.trim()).commit() }

    var aiProvider: String
        get() = prefs.getString("ai_provider", "gemini_studio") ?: "gemini_studio"
        set(value) { prefs.edit().putString("ai_provider", value.trim()).commit() }

    var aiApiKey: String
        get() = decryptString("ai_api_key", aiKeyAlias)
        set(value) { encryptString("ai_api_key", aiKeyAlias, value.trim()) }

    var aiModelName: String
        get() = prefs.getString("ai_model_name", "gemini-2.0-flash") ?: "gemini-2.0-flash"
        set(value) { prefs.edit().putString("ai_model_name", value.trim()).commit() }

    var vertexProjectId: String
        get() = prefs.getString("vertex_project_id", "") ?: ""
        set(value) { prefs.edit().putString("vertex_project_id", value.trim()).commit() }

    var vertexLocation: String
        get() = prefs.getString("vertex_location", "global") ?: "global"
        set(value) { prefs.edit().putString("vertex_location", value.trim()).commit() }

    var vertexEndpoint: String
        get() = prefs.getString("vertex_endpoint", "") ?: ""
        set(value) { prefs.edit().putString("vertex_endpoint", value.trim()).commit() }

    var mcpToken: String
        get() = decryptString("mcp_token", mcpTokenAlias)
        set(value) { encryptString("mcp_token", mcpTokenAlias, value.trim()) }

    var transcriptionMode: String
        get() = prefs.getString("transcription_mode", "structured") ?: "structured"
        set(value) { prefs.edit().putString("transcription_mode", value.trim()).commit() }

    var showDemoDataWhenEmpty: Boolean
        get() = prefs.getBoolean("show_demo_data_empty", true)
        set(value) { prefs.edit().putBoolean("show_demo_data_empty", value).commit() }

    var vadProvider: String
        get() = prefs.getString("vad_provider", "silero_v5") ?: "silero_v5"
        set(value) { prefs.edit().putString("vad_provider", value.trim()).commit() }

    var vadThreshold: Float
        get() = prefs.getFloat("vad_threshold", 0.55f)
        set(value) { prefs.edit().putFloat("vad_threshold", value).commit() }

    var vadEndThreshold: Float
        get() = prefs.getFloat("vad_end_threshold", 0.35f)
        set(value) { prefs.edit().putFloat("vad_end_threshold", value).commit() }

    var vadMinSpeechMs: Int
        get() = prefs.getInt("vad_min_speech_ms", 250)
        set(value) { prefs.edit().putInt("vad_min_speech_ms", value).commit() }

    var vadMinSilenceMs: Int
        get() = prefs.getInt("vad_min_silence_ms", 400)
        set(value) { prefs.edit().putInt("vad_min_silence_ms", value).commit() }

    var vadPadMs: Int
        get() = prefs.getInt("vad_pad_ms", 200)
        set(value) { prefs.edit().putInt("vad_pad_ms", value).commit() }

    var vadMaxSegmentMs: Int
        get() = prefs.getInt("vad_max_segment_ms", 25000)
        set(value) { prefs.edit().putInt("vad_max_segment_ms", value).commit() }

    var vadUncertainThreshold: Float
        get() = prefs.getFloat("vad_uncertain_threshold", 0.25f)
        set(value) { prefs.edit().putFloat("vad_uncertain_threshold", value).commit() }

    var vadRmsGate: Float
        get() = prefs.getFloat("vad_rms_gate", 0.003f)
        set(value) { prefs.edit().putFloat("vad_rms_gate", value).commit() }

    var voiceprintEnabled: Boolean
        get() = prefs.getBoolean("voiceprint_enabled", true)
        set(value) { prefs.edit().putBoolean("voiceprint_enabled", value).commit() }

    var voiceprintModelId: String
        get() = prefs.getString("voiceprint_model_id", "wespeaker_ecapa_tdnn512") ?: "wespeaker_ecapa_tdnn512"
        set(value) { prefs.edit().putString("voiceprint_model_id", value.trim()).commit() }

    var voiceprintThreshold: Float
        get() = prefs.getFloat("voiceprint_threshold", 0.80f)
        set(value) { prefs.edit().putFloat("voiceprint_threshold", value).commit() }

    var voiceprintMargin: Float
        get() = prefs.getFloat("voiceprint_margin", 0.10f)
        set(value) { prefs.edit().putFloat("voiceprint_margin", value).commit() }

    var voiceprintMinSpeechMs: Int
        get() = prefs.getInt("voiceprint_min_speech_ms", 1500)
        set(value) { prefs.edit().putInt("voiceprint_min_speech_ms", value).commit() }

    var voiceprintAutoEnroll: Boolean
        get() = prefs.getBoolean("voiceprint_auto_enroll", false)
        set(value) { prefs.edit().putBoolean("voiceprint_auto_enroll", value).commit() }

    var voiceprintConsent: Boolean
        get() = prefs.getBoolean("voiceprint_consent", true)
        set(value) { prefs.edit().putBoolean("voiceprint_consent", value).commit() }
}
