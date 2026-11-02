package dev.echoledger.app.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import dev.echoledger.app.BuildConfig
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Only the backend access token is stored here. Cloud model keys stay on the server. */
class SecurePreferences(context: Context) {
    private val prefs = context.getSharedPreferences("connection", Context.MODE_PRIVATE)
    private val alias = "echoledger_backend_token_v1"
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        return generator.generateKey()
    }
    var baseUrl: String
        get() = prefs.getString("url", if (BuildConfig.DEBUG) "http://10.0.2.2:8000" else "") ?: ""
        set(value) { prefs.edit().putString("url", value.trim().trimEnd('/')).commit() }
    var token: String
        get() {
            val stored = prefs.getString("token", null) ?: return ""
            return try {
                val packed = Base64.getDecoder().decode(stored)
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, packed.copyOfRange(0, 12)))
                String(cipher.doFinal(packed.copyOfRange(12, packed.size)), Charsets.UTF_8)
            } catch (_: Exception) { "" } // A reset/invalidated key requires entering a new token.
        }
        set(value) {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key())
            val packed = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
            prefs.edit().putString("token", Base64.getEncoder().encodeToString(packed)).commit()
        }
    var cursor: Long
        get() = prefs.getLong("cursor", 0)
        set(value) { prefs.edit().putLong("cursor", value).apply() }
}
