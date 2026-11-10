package net.hemory.app.data

import android.content.SharedPreferences
import net.hemory.app.native.ipc.NativeBridgePlugin
import org.junit.Assert.*
import org.junit.Test
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.SecretKeySpec

private class InMemorySharedPreferences : SharedPreferences {
    val backing = mutableMapOf<String, Any?>()

    override fun getAll(): MutableMap<String, *> = backing
    override fun getString(key: String?, defValue: String?): String? = (backing[key] as? String) ?: defValue
    @Suppress("UNCHECKED_CAST")
    override fun getStringSet(key: String?, defValues: MutableSet<String>?): MutableSet<String>? =
        (backing[key] as? MutableSet<String>) ?: defValues
    override fun getInt(key: String?, defValue: Int): Int = (backing[key] as? Int) ?: defValue
    override fun getLong(key: String?, defValue: Long): Long = (backing[key] as? Long) ?: defValue
    override fun getFloat(key: String?, defValue: Float): Float = (backing[key] as? Float) ?: defValue
    override fun getBoolean(key: String?, defValue: Boolean): Boolean = (backing[key] as? Boolean) ?: defValue
    override fun contains(key: String?): Boolean = backing.containsKey(key)
    override fun edit(): SharedPreferences.Editor = Editor(backing)
    override fun registerOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener?) {}
    override fun unregisterOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener?) {}

    class Editor(private val map: MutableMap<String, Any?>) : SharedPreferences.Editor {
        private val pending = mutableMapOf<String, Any?>()
        private val removals = mutableSetOf<String>()
        private var clearAll = false

        override fun putString(key: String?, value: String?): SharedPreferences.Editor {
            if (key != null) pending[key] = value
            return this
        }
        override fun putStringSet(key: String?, values: MutableSet<String>?): SharedPreferences.Editor {
            if (key != null) pending[key] = values
            return this
        }
        override fun putInt(key: String?, value: Int): SharedPreferences.Editor {
            if (key != null) pending[key] = value
            return this
        }
        override fun putLong(key: String?, value: Long): SharedPreferences.Editor {
            if (key != null) pending[key] = value
            return this
        }
        override fun putFloat(key: String?, value: Float): SharedPreferences.Editor {
            if (key != null) pending[key] = value
            return this
        }
        override fun putBoolean(key: String?, value: Boolean): SharedPreferences.Editor {
            if (key != null) pending[key] = value
            return this
        }
        override fun remove(key: String?): SharedPreferences.Editor {
            if (key != null) removals.add(key)
            return this
        }
        override fun clear(): SharedPreferences.Editor {
            clearAll = true
            return this
        }
        override fun commit(): Boolean {
            if (clearAll) map.clear()
            removals.forEach { map.remove(it) }
            map.putAll(pending)
            return true
        }
        override fun apply() {
            commit()
        }
    }
}

class HemoryPreferencesAndAuthBridgeTest {
    private val testKey = SecretKeySpec(
        "hemory_aes_256_gcm_test_key_0123".toByteArray(Charsets.UTF_8).copyOf(32),
        "AES"
    )

    @Test
    fun `token encrypts with AES-256-GCM and decrypts transparently`() {
        val raw = InMemorySharedPreferences()
        val prefs = HemoryPreferences(raw) { testKey }

        assertEquals("https://record.xmdoge.com", prefs.baseUrl)
        assertEquals("", prefs.token)

        prefs.token = "tok_live_secret_998877"
        prefs.baseUrl = "https://record.xmdoge.com/"

        val cipherStored = raw.getString("token_enc", null)
        assertNotNull(cipherStored)
        assertNotEquals("tok_live_secret_998877", cipherStored)
        assertTrue(cipherStored!!.contains(":"))

        assertEquals("tok_live_secret_998877", prefs.token)
        assertEquals("https://record.xmdoge.com", prefs.baseUrl)
    }

    @Test
    fun `legacy SecurePreferences packed ciphertext migrates to token_enc and rejects garbage`() {
        val raw = InMemorySharedPreferences()
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, testKey)
        val encrypted = cipher.doFinal("tok_legacy_upgraded_v1".toByteArray(Charsets.UTF_8))
        val legacyPacked = Base64.getEncoder().encodeToString(cipher.iv + encrypted)

        raw.edit().putString("token", legacyPacked).putString("refresh_token", "rt_legacy_plain").commit()
        val prefs = HemoryPreferences(raw) { testKey }

        assertEquals("tok_legacy_upgraded_v1", prefs.token)
        assertNull("legacy 'token' key must be removed after migration", raw.getString("token", null))
        assertNotNull("migrated 'token_enc' must be populated", raw.getString("token_enc", null))

        assertEquals("rt_legacy_plain", prefs.refreshToken)
        assertNull("plaintext 'refresh_token' must be removed after migration", raw.getString("refresh_token", null))
        assertNotNull("encrypted 'refresh_token_enc' must be populated", raw.getString("refresh_token_enc", null))

        // Corrupt legacy value must NOT leak raw ciphertext
        val rawCorrupt = InMemorySharedPreferences()
        rawCorrupt.edit().putString("token", "corrupt_raw_ciphertext_not_valid_gcm").commit()
        val prefsCorrupt = HemoryPreferences(rawCorrupt) { testKey }
        assertEquals("", prefsCorrupt.token)
    }

    @Test
    fun `normalizeStoredAuthMap returns numeric expiresAt and preserves distinct vaultId`() {
        val normalized = NativeBridgePlugin.normalizeStoredAuthMap(
            token = "tok_upgraded_user",
            baseUrl = "",
            rawPrefs = mapOf(
                "expires_at" to "",
                "slot_connection_type" to null,
                "slot_dp_unit_id" to null,
                "active_tenant_id" to "ten_default",
                "active_vault_id" to "vlt_default"
            ),
            encryptedRefreshToken = "",
            nowEpochSeconds = 1_700_000_000L
        )

        assertNotNull(normalized)
        assertEquals("tok_upgraded_user", normalized!!["accessToken"])
        assertEquals("direct_session", normalized["refreshToken"])
        assertTrue("expiresAt must be numeric Long for Auth7Tuple", normalized["expiresAt"] is Long)
        assertTrue((normalized["expiresAt"] as Long) > 1_700_000_000L)
        assertEquals("https://record.xmdoge.com", normalized["baseUrl"])
        assertEquals("direct", normalized["slotConnectionType"])
        assertEquals("dp_default", normalized["slotDpUnitId"])
        assertEquals("ten_default", normalized["slotTenantId"])
        assertEquals("vlt_default", normalized["slotVaultId"])
        assertEquals("vlt_default", normalized["vaultId"])
    }
}
