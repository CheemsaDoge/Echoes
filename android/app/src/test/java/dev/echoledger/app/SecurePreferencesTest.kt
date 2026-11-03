package dev.echoledger.app

import android.content.SharedPreferences
import dev.echoledger.app.data.SecurePreferences
import org.junit.Assert.*
import org.junit.Test

class FakeSharedPreferences : SharedPreferences {
    val map = mutableMapOf<String, Any?>()

    override fun getAll(): MutableMap<String, *> = map
    override fun getString(key: String?, defValue: String?): String? = (map[key] as? String) ?: defValue
    override fun getStringSet(key: String?, defValues: MutableSet<String>?): MutableSet<String>? = (map[key] as? MutableSet<String>) ?: defValues
    override fun getInt(key: String?, defValue: Int): Int = (map[key] as? Int) ?: defValue
    override fun getLong(key: String?, defValue: Long): Long = (map[key] as? Long) ?: defValue
    override fun getFloat(key: String?, defValue: Float): Float = (map[key] as? Float) ?: defValue
    override fun getBoolean(key: String?, defValue: Boolean): Boolean = (map[key] as? Boolean) ?: defValue
    override fun contains(key: String?): Boolean = map.containsKey(key)
    override fun edit(): SharedPreferences.Editor = FakeEditor(map)
    override fun registerOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener?) {}
    override fun unregisterOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener?) {}

    class FakeEditor(private val backing: MutableMap<String, Any?>) : SharedPreferences.Editor {
        private val temp = mutableMapOf<String, Any?>()
        private val removes = mutableSetOf<String>()
        private var clear = false

        override fun putString(key: String?, value: String?): SharedPreferences.Editor { key?.let { temp[it] = value }; return this }
        override fun putStringSet(key: String?, values: MutableSet<String>?): SharedPreferences.Editor { key?.let { temp[it] = values }; return this }
        override fun putInt(key: String?, value: Int): SharedPreferences.Editor { key?.let { temp[it] = value }; return this }
        override fun putLong(key: String?, value: Long): SharedPreferences.Editor { key?.let { temp[it] = value }; return this }
        override fun putFloat(key: String?, value: Float): SharedPreferences.Editor { key?.let { temp[it] = value }; return this }
        override fun putBoolean(key: String?, value: Boolean): SharedPreferences.Editor { key?.let { temp[it] = value }; return this }
        override fun remove(key: String?): SharedPreferences.Editor { key?.let { removes.add(it) }; return this }
        override fun clear(): SharedPreferences.Editor { clear = true; return this }
        override fun commit(): Boolean {
            if (clear) backing.clear()
            removes.forEach { backing.remove(it) }
            backing.putAll(temp)
            return true
        }
        override fun apply() { commit() }
    }
}

class SecurePreferencesTest {
    private val testKey = javax.crypto.spec.SecretKeySpec("test_aes_key_32_bytes_long_1234".toByteArray(Charsets.UTF_8).copyOf(32), "AES")
    private fun createPrefs(fake: FakeSharedPreferences) = SecurePreferences(fake) { testKey }

    @Test
    fun testKeyStoreEncryptionRoundTrip() {
        val fakePrefs = FakeSharedPreferences()
        val securePrefs = createPrefs(fakePrefs)

        val secretKey = "AIzaSy_Secret_Gemini_Key_987654321"
        securePrefs.aiApiKey = secretKey

        // The raw value stored in SharedPreferences must be cipher text (encrypted Base64)
        val rawStored = fakePrefs.getString("ai_api_key", null)
        assertNotNull("Raw preference must be saved", rawStored)
        assertNotEquals("Stored value must not be plain text", secretKey, rawStored)

        // Reading back through SecurePreferences must return the exact plain text
        val decrypted = securePrefs.aiApiKey
        assertEquals(secretKey, decrypted)
    }

    @Test
    fun testEmptyKeyClearsPreference() {
        val fakePrefs = FakeSharedPreferences()
        val securePrefs = createPrefs(fakePrefs)

        securePrefs.aiApiKey = "temp_key"
        assertEquals("temp_key", securePrefs.aiApiKey)

        securePrefs.aiApiKey = ""
        assertEquals("", securePrefs.aiApiKey)
        assertFalse(fakePrefs.contains("ai_api_key"))
    }

    @Test
    fun testOperationModePersistence() {
        val fakePrefs = FakeSharedPreferences()
        val securePrefs = createPrefs(fakePrefs)

        // Default value
        assertEquals("standalone", securePrefs.operationMode)

        securePrefs.operationMode = "hybrid"
        assertEquals("hybrid", securePrefs.operationMode)

        securePrefs.operationMode = "server"
        assertEquals("server", securePrefs.operationMode)
    }

    @Test
    fun testAiModelAndProviderDefaults() {
        val fakePrefs = FakeSharedPreferences()
        val securePrefs = createPrefs(fakePrefs)

        assertEquals("gemini_studio", securePrefs.aiProvider)
        assertEquals("gemini-2.0-flash", securePrefs.aiModelName)
        assertEquals("global", securePrefs.vertexLocation)
        assertEquals("", securePrefs.vertexProjectId)
        assertEquals("", securePrefs.vertexEndpoint)

        securePrefs.aiProvider = "vertex_ai"
        securePrefs.aiModelName = "gemini-1.5-pro"
        securePrefs.vertexProjectId = "proj-123"
        securePrefs.vertexLocation = "asia-east1"
        securePrefs.vertexEndpoint = "https://my-endpoint"

        assertEquals("vertex_ai", securePrefs.aiProvider)
        assertEquals("gemini-1.5-pro", securePrefs.aiModelName)
        assertEquals("proj-123", securePrefs.vertexProjectId)
        assertEquals("asia-east1", securePrefs.vertexLocation)
        assertEquals("https://my-endpoint", securePrefs.vertexEndpoint)
    }
}
