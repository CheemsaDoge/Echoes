package net.hemory.app.data

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Hardware-backed AES-256-GCM encrypted preferences for dedicated server credentials.
 * Retains compatibility with the existing "connection" SharedPreferences and
 * "echoledger_backend_token_v1" AndroidKeyStore alias so upgraded installs preserve auth.
 */
class HemoryPreferences(
    private val prefs: SharedPreferences,
    private val keyProvider: ((alias: String) -> SecretKey)? = null
) {
    constructor(context: Context) : this(
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE),
        null
    )

    companion object {
        const val PREFS_NAME = "connection"
        const val DEFAULT_BASE_URL = "https://record.xmdoge.com"
        private const val TOKEN_ALIAS = "echoledger_backend_token_v1"
        private const val KEY_URL = "url"
        private const val KEY_TOKEN_ENC = "token_enc"
        private const val KEY_TOKEN_LEGACY = "token"
    }

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

    private fun encrypt(plainText: String, alias: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, getSecretKey(alias))
        val iv = Base64.getEncoder().encodeToString(cipher.iv)
        val encrypted = Base64.getEncoder().encodeToString(
            cipher.doFinal(plainText.toByteArray(Charsets.UTF_8))
        )
        return "$iv:$encrypted"
    }

    private fun decrypt(encoded: String, alias: String): String {
        val parts = encoded.split(":", limit = 2)
        if (parts.size != 2) return ""
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            val spec = GCMParameterSpec(128, Base64.getDecoder().decode(parts[0]))
            cipher.init(Cipher.DECRYPT_MODE, getSecretKey(alias), spec)
            String(cipher.doFinal(Base64.getDecoder().decode(parts[1])), Charsets.UTF_8)
        } catch (_: Exception) {
            ""
        }
    }

    var baseUrl: String
        get() = prefs.getString(KEY_URL, DEFAULT_BASE_URL)
            ?.trim()
            ?.trimEnd('/')
            ?.ifEmpty { DEFAULT_BASE_URL }
            ?: DEFAULT_BASE_URL
        set(value) {
            val normalized = value.trim().trimEnd('/').ifEmpty { DEFAULT_BASE_URL }
            prefs.edit().putString(KEY_URL, normalized).commit()
        }

    var token: String
        get() {
            val enc = prefs.getString(KEY_TOKEN_ENC, "") ?: ""
            if (enc.isNotEmpty()) {
                val decrypted = decrypt(enc, TOKEN_ALIAS)
                if (decrypted.isNotEmpty()) return decrypted
            }
            return prefs.getString(KEY_TOKEN_LEGACY, "") ?: ""
        }
        set(value) {
            val trimmed = value.trim()
            val encoded = if (trimmed.isEmpty()) "" else encrypt(trimmed, TOKEN_ALIAS)
            prefs.edit()
                .putString(KEY_TOKEN_ENC, encoded)
                .remove(KEY_TOKEN_LEGACY)
                .commit()
        }

    fun clear() {
        prefs.edit().clear().commit()
    }
}
