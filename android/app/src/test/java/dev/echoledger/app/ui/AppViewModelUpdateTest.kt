package dev.echoledger.app.ui

import android.app.Application
import android.content.Context
import dev.echoledger.app.FakeSharedPreferences
import dev.echoledger.app.data.*
import dev.echoledger.app.update.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.security.MessageDigest
import javax.crypto.spec.SecretKeySpec

@OptIn(ExperimentalCoroutinesApi::class)
class AppViewModelUpdateTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private val testDispatcher = StandardTestDispatcher()
    private val testAesKey = SecretKeySpec("test_aes_key_32_bytes_long_1234".toByteArray(Charsets.UTF_8).copyOf(32), "AES")

    private lateinit var tempDir: File
    private lateinit var app: Application
    private lateinit var repo: Repository

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        tempDir = tempFolder.newFolder("app_cache")

        val fakePrefs = FakeSharedPreferences()
        val securePrefs = SecurePreferences(fakePrefs) { testAesKey }
        securePrefs.operationMode = "standalone"

        val dao = object : JournalDao() {
            override suspend fun putCache(entries: List<CacheEntry>) {}
            override suspend fun getCacheEntry(kind: String, id: String): CacheEntry? = null
            override suspend fun deleteCacheEntry(kind: String, id: String) {}
            override suspend fun addSession(session: LocalSession) {}
            override suspend fun addChunk(chunk: LocalChunk) {}
            override suspend fun session(id: String): LocalSession? = null
            override suspend fun sessions(): List<LocalSession> = emptyList()
            override fun observeSessions(): Flow<List<LocalSession>> = emptyFlow()
            override suspend fun finishSession(id: String, seq: Int, state: String) {}
            override suspend fun markCreated(id: String) {}
            override suspend fun markClosed(id: String) {}
            override suspend fun tombstone(id: String) {}
            override suspend fun chunk(id: String): LocalChunk? = null
            override suspend fun chunks(id: String): List<LocalChunk> = emptyList()
            override fun observeAllChunks(): Flow<List<LocalChunk>> = emptyFlow()
            override suspend fun pending(id: String): List<LocalChunk> = emptyList()
            override suspend fun latestCalibration(): LocalChunk? = null
            override fun pendingCount(): Flow<Int> = emptyFlow()
            override suspend fun markUpload(id: String, state: String, error: String) {}
            override suspend fun resetSessionsRemote() {}
            override suspend fun resetChunksRemote() {}
            override fun observe(kind: String): Flow<List<CacheEntry>> = emptyFlow()
            override suspend fun clearKind(kind: String) {}
            override suspend fun clearEventsRange(from: Long, to: Long) {}
            override suspend fun deleteSessionCache(sessionId: String) {}
            override suspend fun clearCache() {}
            override suspend fun deleteChunks(id: String) {}
            override suspend fun deleteSession(id: String) {}
        }

        val contextWrapper = object : android.content.ContextWrapper(null) {
            override fun getCacheDir(): File = tempDir
            override fun getFilesDir(): File = tempDir
            override fun getApplicationContext(): Context = this
            override fun getPackageName(): String = "dev.echoledger.app"
        }

        app = object : Application() {
            override fun getCacheDir(): File = tempDir
            override fun getFilesDir(): File = tempDir
            override fun getApplicationContext(): Context = this
            override fun getPackageName(): String = "dev.echoledger.app"
        }

        val localStore = LocalStore(contextWrapper, dao)
        repo = Repository(contextWrapper, dao, securePrefs, localStore)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun createMockClient(handler: (Request) -> Response): OkHttpClient {
        return OkHttpClient.Builder()
            .addInterceptor(Interceptor { chain -> handler(chain.request()) })
            .build()
    }

    private fun mockJsonResponse(request: Request, code: Int = 200, body: String = ""): Response {
        return Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(code)
            .message(if (code == 200) "OK" else "Error $code")
            .body(body.toResponseBody("application/json".toMediaType()))
            .build()
    }

    private fun computeSha256(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256")
        return digest.digest(bytes).joinToString("") { "%02x".format(it) }
    }

    @Test
    fun testInitialUpdateStateIsIdle() {
        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateDownloader = UpdateDownloader(tempDir)
        )
        assertEquals(UpdateState.Idle, vm.updateState.value)
        assertNull(vm.lastCheckedAtMs.value)
    }

    @Test
    fun testCheckForUpdatesAvailableTransitionsState() = runTest {
        val releaseJson = """
            {
              "tag_name": "v0.5.0",
              "name": "EchoLedger v0.5.0",
              "body": "Exciting new features",
              "published_at": "2026-11-03T12:00:00Z",
              "assets": [
                {
                  "name": "app-release.apk",
                  "size": 1048576,
                  "browser_download_url": "https://example.com/download/app-release.apk"
                }
              ]
            }
        """.trimIndent()

        val mockClient = createMockClient { req -> mockJsonResponse(req, 200, releaseJson) }
        val checker = UpdateChecker(mockClient, "https://api.github.com/repos/test/test", "0.1.0")

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateChecker = checker,
            updateDownloader = UpdateDownloader(tempDir)
        )

        val job = vm.checkForUpdates(silent = false)
        testDispatcher.scheduler.advanceUntilIdle()
        job.join()

        val state = vm.updateState.value
        assertTrue("State must be UpdateAvailable, but was $state", state is UpdateState.UpdateAvailable)
        val available = state as UpdateState.UpdateAvailable
        assertEquals("v0.5.0", available.release.versionName)
        assertEquals("EchoLedger v0.5.0", available.release.title)
        assertNotNull(vm.lastCheckedAtMs.value)
    }

    @Test
    fun testCheckForUpdatesUpToDateTransitionsState() = runTest {
        val releaseJson = """
            {
              "tag_name": "v0.1.0",
              "name": "EchoLedger v0.1.0",
              "body": "Initial release",
              "published_at": "2026-11-03T12:00:00Z",
              "assets": [
                {
                  "name": "app-release.apk",
                  "size": 1048576,
                  "browser_download_url": "https://example.com/download/app-release.apk"
                }
              ]
            }
        """.trimIndent()

        val mockClient = createMockClient { req -> mockJsonResponse(req, 200, releaseJson) }
        val checker = UpdateChecker(mockClient, "https://api.github.com/repos/test/test", "0.1.0")

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateChecker = checker,
            updateDownloader = UpdateDownloader(tempDir)
        )

        val job = vm.checkForUpdates(silent = false)
        testDispatcher.scheduler.advanceUntilIdle()
        job.join()

        val state = vm.updateState.value
        assertTrue("State must be UpToDate, but was $state", state is UpdateState.UpToDate)
        assertEquals("0.1.0", (state as UpdateState.UpToDate).currentVersion)
        assertNotNull(vm.lastCheckedAtMs.value)
    }

    @Test
    fun testCheckForUpdatesErrorNonSilentTransitionsState() = runTest {
        val mockClient = createMockClient { req -> mockJsonResponse(req, 500, "Internal Server Error") }
        val checker = UpdateChecker(mockClient, "https://api.github.com/repos/test/test", "0.1.0")

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateChecker = checker,
            updateDownloader = UpdateDownloader(tempDir)
        )

        val job = vm.checkForUpdates(silent = false)
        testDispatcher.scheduler.advanceUntilIdle()
        job.join()

        val state = vm.updateState.value
        assertTrue("State must be Error, but was $state", state is UpdateState.Error)
        val err = state as UpdateState.Error
        assertTrue(err.canRetry)
        assertTrue(err.message.contains("500") || err.message.contains("检查更新失败"))
    }

    @Test
    fun testCheckForUpdatesErrorSilentKeepsIdleState() = runTest {
        val mockClient = createMockClient { req -> mockJsonResponse(req, 500, "Internal Server Error") }
        val checker = UpdateChecker(mockClient, "https://api.github.com/repos/test/test", "0.1.0")

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateChecker = checker,
            updateDownloader = UpdateDownloader(tempDir)
        )

        val job = vm.checkForUpdates(silent = true)
        testDispatcher.scheduler.advanceUntilIdle()
        job.join()

        assertEquals(UpdateState.Idle, vm.updateState.value)
    }

    @Test
    fun testStartDownloadStreamsAndTransitionsToReadyToInstall() = runTest {
        val fakeApkBytes = "fake-apk-binary-stream-test".toByteArray(Charsets.UTF_8)
        val expectedSha = computeSha256(fakeApkBytes)

        val release = UpdateRelease(
            versionName = "v0.8.0",
            semVer = SemVer.parse("0.8.0")!!,
            title = "EchoLedger 0.8.0",
            changelog = "Test changelog",
            publishedAt = "2026-11-03T12:00:00Z",
            apkAsset = ApkAsset(
                name = "app-release.apk",
                sizeBytes = fakeApkBytes.size.toLong(),
                downloadUrl = "https://example.com/download/app-release.apk",
                sha256 = expectedSha
            )
        )

        val mockClient = createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(fakeApkBytes.toResponseBody("application/vnd.android.package-archive".toMediaType()))
                .build()
        }

        val downloader = UpdateDownloader(tempDir, mockClient)
        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateDownloader = downloader
        )

        val job = vm.startDownload(release)
        testDispatcher.scheduler.advanceUntilIdle()
        job.join()

        val state = vm.updateState.value
        assertTrue("State must be ReadyToInstall, but was $state", state is UpdateState.ReadyToInstall)
        val ready = state as UpdateState.ReadyToInstall
        assertEquals(release, ready.release)
        assertTrue(ready.apkFile.exists())
        assertEquals(fakeApkBytes.size.toLong(), ready.apkFile.length())
    }

    @Test
    fun testStartDownloadWhenAlreadyDownloadedDirectlyReadyToInstall() = runTest {
        val release = UpdateRelease(
            versionName = "v0.9.0",
            semVer = SemVer.parse("0.9.0")!!,
            title = "EchoLedger 0.9.0",
            changelog = "Test changelog",
            publishedAt = "2026-11-03T12:00:00Z",
            apkAsset = ApkAsset(
                name = "app-release.apk",
                sizeBytes = 100L,
                downloadUrl = "https://example.com/download/app-release.apk",
                sha256 = null
            )
        )

        val downloader = UpdateDownloader(tempDir)
        val apkFile = downloader.getApkFile(release.versionName)
        apkFile.parentFile?.mkdirs()
        apkFile.writeText("pre-existing-apk-content")

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateDownloader = downloader
        )

        vm.startDownload(release)
        testDispatcher.scheduler.advanceUntilIdle()

        val state = vm.updateState.value
        assertTrue("State must be ReadyToInstall, but was $state", state is UpdateState.ReadyToInstall)
        assertEquals(release, (state as UpdateState.ReadyToInstall).release)
    }

    @Test
    fun testCancelDownloadRevertsToUpdateAvailable() = runTest {
        val release = UpdateRelease(
            versionName = "v0.9.5",
            semVer = SemVer.parse("0.9.5")!!,
            title = "EchoLedger 0.9.5",
            changelog = "Changelog",
            publishedAt = "2026-11-03T12:00:00Z",
            apkAsset = ApkAsset(
                name = "app-release.apk",
                sizeBytes = 1000000L,
                downloadUrl = "https://example.com/hang",
                sha256 = null
            )
        )

        val releaseJson = """
            {
              "tag_name": "v0.9.5",
              "name": "EchoLedger 0.9.5",
              "body": "Changelog",
              "published_at": "2026-11-03T12:00:00Z",
              "assets": [
                {
                  "name": "app-release.apk",
                  "size": 1000000,
                  "browser_download_url": "https://example.com/hang"
                }
              ]
            }
        """.trimIndent()

        // Create client that returns release for API check and blocks indefinitely for download
        val mockClient = createMockClient { req ->
            if (req.url.encodedPath.contains("releases")) {
                mockJsonResponse(req, 200, releaseJson)
            } else {
                // Sleep / wait to simulate in-flight download
                Thread.sleep(200)
                mockJsonResponse(req, 200, "")
            }
        }

        val checker = UpdateChecker(mockClient, "https://api.github.com/repos/test/test", "0.1.0")
        val downloader = UpdateDownloader(tempDir, mockClient)

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateChecker = checker,
            updateDownloader = downloader
        )

        vm.checkForUpdates().join()
        testDispatcher.scheduler.advanceUntilIdle()
        assertTrue(vm.updateState.value is UpdateState.UpdateAvailable)

        vm.startDownload(release)
        testDispatcher.scheduler.runCurrent()
        assertTrue(vm.updateState.value is UpdateState.Downloading)

        vm.cancelDownload()
        testDispatcher.scheduler.runCurrent()

        val revertedState = vm.updateState.value
        assertTrue("State must revert to UpdateAvailable, but was $revertedState", revertedState is UpdateState.UpdateAvailable)
        assertEquals(release, (revertedState as UpdateState.UpdateAvailable).release)
    }

    @Test
    fun testDismissUpdateResetsToIdle() = runTest {
        val releaseJson = """
            {
              "tag_name": "v0.9.9",
              "name": "EchoLedger 0.9.9",
              "body": "Changelog",
              "published_at": "2026-11-03T12:00:00Z",
              "assets": [
                {
                  "name": "app-release.apk",
                  "size": 1000000,
                  "browser_download_url": "https://example.com/download"
                }
              ]
            }
        """.trimIndent()

        val mockClient = createMockClient { req -> mockJsonResponse(req, 200, releaseJson) }
        val checker = UpdateChecker(mockClient, "https://api.github.com/repos/test/test", "0.1.0")

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateChecker = checker,
            updateDownloader = UpdateDownloader(tempDir)
        )

        vm.checkForUpdates().join()
        testDispatcher.scheduler.advanceUntilIdle()
        assertTrue(vm.updateState.value is UpdateState.UpdateAvailable)

        vm.dismissUpdate()
        assertEquals(UpdateState.Idle, vm.updateState.value)
    }
}
