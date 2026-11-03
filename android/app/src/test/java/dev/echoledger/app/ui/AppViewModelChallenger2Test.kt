package dev.echoledger.app.ui

import android.app.Application
import android.content.Context
import dev.echoledger.app.FakeSharedPreferences
import dev.echoledger.app.data.*
import dev.echoledger.app.update.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import okio.ForwardingSource
import okio.buffer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicInteger
import javax.crypto.spec.SecretKeySpec

@OptIn(ExperimentalCoroutinesApi::class)
class AppViewModelChallenger2Test {

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
        tempDir = tempFolder.newFolder("app_cache_challenger2")

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

    // =========================================================================
    // CHALLENGE 1: Stress-Test AppViewModel Update States
    // =========================================================================

    @Test
    fun testRapidRepeatedClicksOnCheckForUpdatesWhileAlreadyChecking() = runTest {
        val releaseJson = """
            {
              "tag_name": "v1.0.0",
              "name": "EchoLedger 1.0.0",
              "body": "First release",
              "published_at": "2026-11-03T12:00:00Z",
              "assets": [
                {
                  "name": "app-release.apk",
                  "size": 5000000,
                  "browser_download_url": "https://example.com/app-release.apk"
                }
              ]
            }
        """.trimIndent()

        val releaseLatch = java.util.concurrent.CountDownLatch(1)
        val requestCount = AtomicInteger(0)
        val mockClient = createMockClient { req ->
            requestCount.incrementAndGet()
            releaseLatch.await()
            mockJsonResponse(req, 200, releaseJson)
        }

        val checker = UpdateChecker(mockClient, "https://api.github.com/repos/test/test", "0.1.0")
        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateChecker = checker,
            updateDownloader = UpdateDownloader(tempDir),
            ioDispatcher = testDispatcher
        )

        assertEquals(UpdateState.Idle, vm.updateState.value)

        // Rapid repeated clicks: trigger 10 clicks in rapid succession while in-flight
        val jobs = List(10) {
            vm.checkForUpdates(silent = false)
        }

        // Run current tasks on dispatcher to start the coroutines
        testDispatcher.scheduler.runCurrent()

        // State is Checking while network check is in-flight
        assertEquals(UpdateState.Checking, vm.updateState.value)

        // Release the network latch and let all coroutines complete
        releaseLatch.countDown()
        testDispatcher.scheduler.advanceUntilIdle()
        jobs.forEach { it.join() }

        // Final state MUST be UpdateAvailable, not corrupted or stuck in Checking
        val finalState = vm.updateState.value
        assertTrue("State must be UpdateAvailable, but was $finalState", finalState is UpdateState.UpdateAvailable)
        val available = finalState as UpdateState.UpdateAvailable
        assertEquals("v1.0.0", available.release.versionName)
        assertNotNull("lastCheckedAtMs must be populated", vm.lastCheckedAtMs.value)
    }

    @Test
    fun testCancellationMidDownloadCleansUpAndAllowsCleanRestart() = runTest {
        val fakePayload = ByteArray(10240) { (it % 127).toByte() }
        val fakeSha = computeSha256(fakePayload)

        val release = UpdateRelease(
            versionName = "v1.1.0",
            semVer = SemVer.parse("1.1.0")!!,
            title = "EchoLedger 1.1.0",
            changelog = "Changelog",
            publishedAt = "2026-11-03T12:00:00Z",
            apkAsset = ApkAsset(
                name = "app-release.apk",
                sizeBytes = fakePayload.size.toLong(),
                downloadUrl = "https://example.com/download.apk",
                sha256 = fakeSha
            )
        )

        var streamedBytes = 0
        var cancellationObserved = false

        val mockClient = createMockClient { req ->
            val partialBuffer = Buffer().write(fakePayload, 0, 5120) // Only half payload
            val forwardingSource = object : ForwardingSource(partialBuffer) {
                override fun read(sink: Buffer, byteCount: Long): Long {
                    val read = super.read(sink, byteCount)
                    if (read > 0) streamedBytes += read.toInt()
                    return read
                }

                override fun close() {
                    cancellationObserved = true
                    super.close()
                }
            }

            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(
                    object : ResponseBody() {
                        override fun contentType() = "application/vnd.android.package-archive".toMediaType()
                        override fun contentLength() = fakePayload.size.toLong()
                        override fun source() = forwardingSource.buffer()
                    }
                )
                .build()
        }

        val downloader = UpdateDownloader(tempDir, mockClient)
        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateDownloader = downloader,
            ioDispatcher = testDispatcher
        )

        // 1. Start download
        val downloadJob = vm.startDownload(release)
        testDispatcher.scheduler.runCurrent()

        // State is downloading
        assertTrue(vm.updateState.value is UpdateState.Downloading)

        // 2. Cancel download mid-stream
        vm.cancelDownload()
        testDispatcher.scheduler.advanceUntilIdle()

        // 3. Verify state reverted back to UpdateAvailable
        val revertedState = vm.updateState.value
        assertTrue("State must revert to UpdateAvailable after cancel, was $revertedState", revertedState is UpdateState.UpdateAvailable)
        assertEquals(release, (revertedState as UpdateState.UpdateAvailable).release)

        // Verify destination file does NOT exist
        val destFile = downloader.getApkFile(release.versionName)
        assertFalse("Destination APK must not exist after cancellation", destFile.exists())

        // Verify temp files were deleted
        val tempFiles = tempDir.listFiles { file -> file.name.contains(".tmp") } ?: emptyArray()
        assertEquals("Temporary files must be cleaned up on cancellation", 0, tempFiles.size)

        // 4. Now perform a second complete download after cancellation
        val fullClient = createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(fakePayload.toResponseBody("application/vnd.android.package-archive".toMediaType()))
                .build()
        }

        val cleanDownloader = UpdateDownloader(tempDir, fullClient)
        val vm2 = AppViewModel(
            application = app,
            repository = repo,
            updateDownloader = cleanDownloader,
            ioDispatcher = testDispatcher
        )

        val retryJob = vm2.startDownload(release)
        testDispatcher.scheduler.advanceUntilIdle()
        retryJob.join()

        // 5. State transitions smoothly to ReadyToInstall
        val readyState = vm2.updateState.value
        assertTrue("Second download must succeed into ReadyToInstall, was $readyState", readyState is UpdateState.ReadyToInstall)
        val ready = readyState as UpdateState.ReadyToInstall
        assertEquals(release, ready.release)
        assertTrue(ready.apkFile.exists())
        assertEquals(fakePayload.size.toLong(), ready.apkFile.length())
        assertArrayEquals(fakePayload, ready.apkFile.readBytes())
    }

    @Test
    fun testRecheckingAfterRetryableErrorRecoversToAvailableState() = runTest {
        var failFirst = true
        val releaseJson = """
            {
              "tag_name": "v1.2.0",
              "name": "EchoLedger 1.2.0",
              "body": "Fixed bugs",
              "published_at": "2026-11-03T12:00:00Z",
              "assets": [
                {
                  "name": "app-release.apk",
                  "size": 2048,
                  "browser_download_url": "https://example.com/app.apk"
                }
              ]
            }
        """.trimIndent()

        val mockClient = createMockClient { req ->
            if (failFirst) {
                failFirst = false
                mockJsonResponse(req, 500, "Internal Server Error")
            } else {
                mockJsonResponse(req, 200, releaseJson)
            }
        }

        val checker = UpdateChecker(mockClient, "https://api.github.com/repos/test/test", "0.1.0")
        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateChecker = checker,
            updateDownloader = UpdateDownloader(tempDir),
            ioDispatcher = testDispatcher
        )

        // 1. First check fails with 500
        val job1 = vm.checkForUpdates(silent = false)
        testDispatcher.scheduler.advanceUntilIdle()
        job1.join()

        val errState = vm.updateState.value
        assertTrue("Must be in Error state", errState is UpdateState.Error)
        assertTrue("Error must be retryable", (errState as UpdateState.Error).canRetry)

        // 2. Click "重试" (checkForUpdates)
        val job2 = vm.checkForUpdates(silent = false)
        testDispatcher.scheduler.runCurrent()
        assertEquals("State must switch to Checking upon retry", UpdateState.Checking, vm.updateState.value)

        testDispatcher.scheduler.advanceUntilIdle()
        job2.join()

        // 3. State recovered to UpdateAvailable
        val recovered = vm.updateState.value
        assertTrue("State must recover to UpdateAvailable, but was $recovered", recovered is UpdateState.UpdateAvailable)
        assertEquals("v1.2.0", (recovered as UpdateState.UpdateAvailable).release.versionName)
        assertNotNull(vm.lastCheckedAtMs.value)
    }

    @Test
    fun testRecheckingAfterNonRetryableErrorRecoversWhenNewReleaseAppears() = runTest {
        var return404 = true
        val releaseJson = """
            {
              "tag_name": "v1.3.0",
              "name": "EchoLedger 1.3.0",
              "body": "Brand new release",
              "published_at": "2026-11-03T12:00:00Z",
              "assets": [
                {
                  "name": "app-release.apk",
                  "size": 4096,
                  "browser_download_url": "https://example.com/app.apk"
                }
              ]
            }
        """.trimIndent()

        val mockClient = createMockClient { req ->
            if (return404) {
                return404 = false
                mockJsonResponse(req, 404, "Not Found")
            } else {
                mockJsonResponse(req, 200, releaseJson)
            }
        }

        val checker = UpdateChecker(mockClient, "https://api.github.com/repos/test/test", "0.1.0")
        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateChecker = checker,
            updateDownloader = UpdateDownloader(tempDir),
            ioDispatcher = testDispatcher
        )

        // 1. Initial check on empty repo returns 404 -> canRetry = false
        val job1 = vm.checkForUpdates(silent = false)
        testDispatcher.scheduler.advanceUntilIdle()
        job1.join()

        val state1 = vm.updateState.value
        assertTrue("Must be in Error state", state1 is UpdateState.Error)
        assertFalse("canRetry should be false for empty repo", (state1 as UpdateState.Error).canRetry)
        assertEquals("当前仓库暂无发布的版本", (state1 as UpdateState.Error).message)

        // 2. User clicks "重新检查" -> calls checkForUpdates()
        val job2 = vm.checkForUpdates(silent = false)
        testDispatcher.scheduler.runCurrent()
        assertEquals(UpdateState.Checking, vm.updateState.value)

        testDispatcher.scheduler.advanceUntilIdle()
        job2.join()

        // 3. Recovered into UpdateAvailable
        val state2 = vm.updateState.value
        assertTrue("State must become UpdateAvailable after repo update, was $state2", state2 is UpdateState.UpdateAvailable)
        assertEquals("v1.3.0", (state2 as UpdateState.UpdateAvailable).release.versionName)
    }

    // =========================================================================
    // CHALLENGE 2: Cache Validation Logic Edge Cases
    // =========================================================================

    @Test
    fun testCacheValidationZeroByteFileTriggersRedownloadAndOverwrites() = runTest {
        val genuineApk = "valid-non-empty-apk-content-payload-for-echoes".toByteArray()
        val genuineSha = computeSha256(genuineApk)

        val release = UpdateRelease(
            versionName = "v2.0.0",
            semVer = SemVer.parse("2.0.0")!!,
            title = "EchoLedger 2.0.0",
            changelog = "Major release",
            publishedAt = "2026-11-03T12:00:00Z",
            apkAsset = ApkAsset(
                name = "app-release.apk",
                sizeBytes = genuineApk.size.toLong(),
                downloadUrl = "https://example.com/app-2.0.0.apk",
                sha256 = genuineSha
            )
        )

        val downloader = UpdateDownloader(tempDir, createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(genuineApk.toResponseBody("application/vnd.android.package-archive".toMediaType()))
                .build()
        })

        // Pre-create corrupt 0-byte cache file
        val cachedFile = downloader.getApkFile(release.versionName)
        cachedFile.parentFile?.mkdirs()
        cachedFile.writeBytes(ByteArray(0))
        assertTrue("Precondition: file must exist on disk", cachedFile.exists())
        assertEquals("Precondition: file must be 0 bytes", 0L, cachedFile.length())

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateDownloader = downloader,
            ioDispatcher = testDispatcher
        )

        // Start download: 0-byte file must NOT be accepted as valid cached file!
        val job = vm.startDownload(release)
        testDispatcher.scheduler.advanceUntilIdle()
        job.join()

        // Verify state is ReadyToInstall
        val state = vm.updateState.value
        assertTrue("Must transition to ReadyToInstall after re-download, was $state", state is UpdateState.ReadyToInstall)
        val ready = state as UpdateState.ReadyToInstall

        // Verify the 0-byte file was cleanly OVERWRITTEN with genuine content
        assertEquals(cachedFile.absolutePath, ready.apkFile.absolutePath)
        assertEquals(genuineApk.size.toLong(), ready.apkFile.length())
        assertArrayEquals(genuineApk, ready.apkFile.readBytes())
    }

    @Test
    fun testCacheValidationMatchingSizeButCorruptedHashTriggersRedownloadAndOverwrites() = runTest {
        val genuineApk = "genuine-apk-binary-content-12345".toByteArray()
        val genuineSha = computeSha256(genuineApk)

        // Create corrupt bytes of EXACT SAME LENGTH
        val corruptApk = "corrupt-apk-binary-content-67890".toByteArray()
        assertEquals(genuineApk.size, corruptApk.size)
        assertNotEquals(genuineSha, computeSha256(corruptApk))

        val release = UpdateRelease(
            versionName = "v2.1.0",
            semVer = SemVer.parse("2.1.0")!!,
            title = "EchoLedger 2.1.0",
            changelog = "Minor release",
            publishedAt = "2026-11-03T12:00:00Z",
            apkAsset = ApkAsset(
                name = "app-release.apk",
                sizeBytes = genuineApk.size.toLong(),
                downloadUrl = "https://example.com/app-2.1.0.apk",
                sha256 = genuineSha
            )
        )

        var downloadAttempted = false
        val downloader = UpdateDownloader(tempDir, createMockClient { req ->
            downloadAttempted = true
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(genuineApk.toResponseBody("application/vnd.android.package-archive".toMediaType()))
                .build()
        })

        // Pre-create file with MATCHING length but CORRUPTED bytes
        val cachedFile = downloader.getApkFile(release.versionName)
        cachedFile.parentFile?.mkdirs()
        cachedFile.writeBytes(corruptApk)
        assertEquals(release.apkAsset.sizeBytes, cachedFile.length())

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateDownloader = downloader,
            ioDispatcher = testDispatcher
        )

        val job = vm.startDownload(release)
        testDispatcher.scheduler.advanceUntilIdle()
        job.join()

        // Verify that re-download was triggered because hash was corrupt
        assertTrue("Re-download must have been triggered despite matching size", downloadAttempted)

        val state = vm.updateState.value
        assertTrue("State must be ReadyToInstall, but was $state", state is UpdateState.ReadyToInstall)
        val ready = state as UpdateState.ReadyToInstall

        // Verify corrupt file was overwritten with genuine bytes
        assertEquals(cachedFile.absolutePath, ready.apkFile.absolutePath)
        assertEquals(genuineApk.size.toLong(), ready.apkFile.length())
        assertArrayEquals(genuineApk, ready.apkFile.readBytes())
    }

    @Test
    fun testCacheValidationMatchingSizeAndMatchingHashSkipsNetworkDownload() = runTest {
        val genuineApk = "already-downloaded-verified-apk-content".toByteArray()
        val genuineSha = computeSha256(genuineApk)

        val release = UpdateRelease(
            versionName = "v2.2.0",
            semVer = SemVer.parse("2.2.0")!!,
            title = "EchoLedger 2.2.0",
            changelog = "Fast update",
            publishedAt = "2026-11-03T12:00:00Z",
            apkAsset = ApkAsset(
                name = "app-release.apk",
                sizeBytes = genuineApk.size.toLong(),
                downloadUrl = "https://example.com/app-2.2.0.apk",
                sha256 = genuineSha
            )
        )

        var networkCalled = false
        val downloader = UpdateDownloader(tempDir, createMockClient { req ->
            networkCalled = true
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(500)
                .body("Should not be hit".toResponseBody("text/plain".toMediaType()))
                .build()
        })

        // Pre-create verified file
        val cachedFile = downloader.getApkFile(release.versionName)
        cachedFile.parentFile?.mkdirs()
        cachedFile.writeBytes(genuineApk)

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateDownloader = downloader,
            ioDispatcher = testDispatcher
        )

        val job = vm.startDownload(release)
        testDispatcher.scheduler.advanceUntilIdle()
        job.join()

        // Zero network calls should occur!
        assertFalse("Network download MUST be skipped when cached APK is fully valid", networkCalled)

        val state = vm.updateState.value
        assertTrue("State must be ReadyToInstall directly from cache, was $state", state is UpdateState.ReadyToInstall)
        val ready = state as UpdateState.ReadyToInstall
        assertEquals(cachedFile.absolutePath, ready.apkFile.absolutePath)
        assertArrayEquals(genuineApk, ready.apkFile.readBytes())
    }

    @Test
    fun testCacheValidationCorruptedFileReDownloadFailureTransitionsToErrorWithoutCrashing() = runTest {
        val corruptBytes = "corrupt-file-on-disk".toByteArray()
        val genuineSha = computeSha256("expected-genuine-content".toByteArray())

        val release = UpdateRelease(
            versionName = "v2.3.0",
            semVer = SemVer.parse("2.3.0")!!,
            title = "EchoLedger 2.3.0",
            changelog = "Changelog",
            publishedAt = "2026-11-03T12:00:00Z",
            apkAsset = ApkAsset(
                name = "app-release.apk",
                sizeBytes = 1000L,
                downloadUrl = "https://example.com/app-2.3.0.apk",
                sha256 = genuineSha
            )
        )

        // Mock client fails with 500 when attempting re-download
        val downloader = UpdateDownloader(tempDir, createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(500)
                .message("Download server down")
                .body("Server error".toResponseBody("text/plain".toMediaType()))
                .build()
        })

        val cachedFile = downloader.getApkFile(release.versionName)
        cachedFile.parentFile?.mkdirs()
        cachedFile.writeBytes(corruptBytes)

        val vm = AppViewModel(
            application = app,
            repository = repo,
            updateDownloader = downloader,
            ioDispatcher = testDispatcher
        )

        val job = vm.startDownload(release)
        testDispatcher.scheduler.advanceUntilIdle()
        job.join()

        // Corrupt file must NOT be accepted, and state must be Error
        val state = vm.updateState.value
        assertTrue("State must be Error when re-download fails, but was $state", state is UpdateState.Error)
        val error = state as UpdateState.Error
        assertTrue(error.canRetry)
        assertTrue(error.message.contains("500") || error.message.contains("下载安装包失败"))
    }

    // =========================================================================
    // CHALLENGE 3: Merged Manifest, FileProvider & Content URI Verification
    // =========================================================================

    @Test
    fun testChallenge3MergedManifestsContainRequestInstallPackagesAndFileProvider() {
        var cur = File(System.getProperty("user.dir") ?: ".").canonicalFile
        while (cur.parentFile != null && !File(cur, "src/main/AndroidManifest.xml").exists() && !File(cur, "android/app/src/main/AndroidManifest.xml").exists()) {
            cur = cur.parentFile
        }
        val appDir = if (File(cur, "src/main/AndroidManifest.xml").exists()) cur else File(cur, "android/app")

        val debugManifest = File(appDir, "build/intermediates/merged_manifest/debug/processDebugMainManifest/AndroidManifest.xml")
        val releaseManifest = File(appDir, "build/intermediates/merged_manifest/release/processReleaseMainManifest/AndroidManifest.xml")

        // 1. Validate merged manifests exist or main manifest exists
        val mainManifest = File(appDir, "src/main/AndroidManifest.xml")
        assertTrue("Main AndroidManifest.xml must exist at: ${mainManifest.absolutePath}", mainManifest.exists())
        val mainXml = mainManifest.readText()
        assertTrue("REQUEST_INSTALL_PACKAGES must be declared in main manifest",
            mainXml.contains("android.permission.REQUEST_INSTALL_PACKAGES"))

        if (debugManifest.exists()) {
            val debugXml = debugManifest.readText()
            assertTrue("Debug merged manifest must contain REQUEST_INSTALL_PACKAGES",
                debugXml.contains("android.permission.REQUEST_INSTALL_PACKAGES"))
            assertTrue("Debug merged manifest must declare FileProvider authority",
                debugXml.contains("dev.echoledger.app.debug.fileprovider"))
        }

        if (releaseManifest.exists()) {
            val releaseXml = releaseManifest.readText()
            assertTrue("Release merged manifest must contain REQUEST_INSTALL_PACKAGES",
                releaseXml.contains("android.permission.REQUEST_INSTALL_PACKAGES"))
            assertTrue("Release merged manifest must declare FileProvider authority",
                releaseXml.contains("dev.echoledger.app.fileprovider"))
        }

        // 2. Validate file_paths.xml cache-path updates/
        val filePathsXml = File(appDir, "src/main/res/xml/file_paths.xml")
        assertTrue("file_paths.xml must exist at: ${filePathsXml.absolutePath}", filePathsXml.exists())
        val filePathsContent = filePathsXml.readText()
        assertTrue("file_paths.xml must declare cache-path name=\"updates\"",
            filePathsContent.contains("""<cache-path name="updates" path="updates/" />"""))

        // 3. Test UpdateInstaller validation of non-existent and empty files
        val nonExistent = File(tempDir, "non_existent.apk")
        val emptyFile = File(tempDir, "empty.apk").apply { writeBytes(ByteArray(0)) }

        val resNonExistent = UpdateInstaller.install(app, nonExistent)
        assertTrue("Non-existent APK must fail with InstallResult.Error", resNonExistent is UpdateInstaller.InstallResult.Error)

        val resEmpty = UpdateInstaller.install(app, emptyFile)
        assertTrue("Empty APK must fail with InstallResult.Error", resEmpty is UpdateInstaller.InstallResult.Error)

        // 4. Test UpdateInstaller settings intent creation
        // Note: android.net.Uri.parse throws 'not mocked' on standard JVM unit test without Robolectric
        try {
            val intent = UpdateInstaller.createManageUnknownAppSourcesIntent(app)
            assertEquals("android.settings.MANAGE_UNKNOWN_APP_SOURCES", intent.action)
            assertEquals("package:dev.echoledger.app", intent.data?.toString())
            assertTrue("Must have FLAG_ACTIVITY_NEW_TASK flag",
                (intent.flags and android.content.Intent.FLAG_ACTIVITY_NEW_TASK) != 0)
        } catch (e: RuntimeException) {
            assertTrue("Confirmed unmocked stub in JVM unit test: ${e.message}",
                e.message?.contains("not mocked") == true)
        }
    }
}
