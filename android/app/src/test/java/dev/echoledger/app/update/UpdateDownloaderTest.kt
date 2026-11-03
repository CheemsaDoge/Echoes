package dev.echoledger.app.update

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class UpdateDownloaderTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private fun computeSha256(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256")
        return digest.digest(bytes).joinToString("") { "%02x".format(it) }
    }

    private fun createMockClient(handler: (Request) -> Response): OkHttpClient {
        return OkHttpClient.Builder()
            .addInterceptor(Interceptor { chain ->
                handler(chain.request())
            })
            .build()
    }

    private fun mockStreamResponse(
        request: Request,
        bytes: ByteArray,
        code: Int = 200
    ): Response {
        val mediaType = "application/vnd.android.package-archive".toMediaType()
        val responseBody = bytes.toResponseBody(mediaType)
        return Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(code)
            .message(if (code == 200) "OK" else "Error $code")
            .body(responseBody)
            .build()
    }

    @Test
    fun testProgressFlowEmissionAndFileIntegrity() = runTest {
        val downloadDir = tempFolder.newFolder("updates")
        // Create 64KB dummy test payload
        val testPayload = ByteArray(65536) { i -> (i % 256).toByte() }
        val trueHash = computeSha256(testPayload)

        val client = createMockClient { req ->
            assertEquals("https://example.com/EchoLedger.apk", req.url.toString())
            mockStreamResponse(req, testPayload)
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val apkAsset = ApkAsset(
            name = "EchoLedger.apk",
            sizeBytes = testPayload.size.toLong(),
            downloadUrl = "https://example.com/EchoLedger.apk",
            sha256 = trueHash
        )

        val progressEmissions = mutableListOf<DownloadProgress>()
        downloader.download(apkAsset, "0.2.0").collect { progress ->
            progressEmissions.add(progress)
        }

        // Verify flow progress emissions
        assertTrue("Must emit at least initial and final progress", progressEmissions.size >= 2)
        assertEquals("Initial progress should be 0%", 0, progressEmissions.first().percent)
        assertEquals("Initial bytesRead should be 0", 0L, progressEmissions.first().bytesRead)
        assertEquals("Final progress should be 100%", 100, progressEmissions.last().percent)
        assertEquals("Final bytesRead must equal total payload size", testPayload.size.toLong(), progressEmissions.last().bytesRead)

        // Monotonic progression
        var prevPercent = -1
        for (p in progressEmissions) {
            assertTrue("Percentages must be monotonically non-decreasing", p.percent >= prevPercent)
            prevPercent = p.percent
            assertEquals(testPayload.size.toLong(), p.totalBytes)
        }

        // Verify disk file integrity
        val destFile = downloader.getApkFile("0.2.0")
        assertTrue("Downloaded APK file must exist on disk", destFile.exists())
        assertEquals("File size must match payload size", testPayload.size.toLong(), destFile.length())
        assertArrayEquals("File content must match payload byte-for-byte", testPayload, destFile.readBytes())
        assertTrue("isApkDownloaded must return true", downloader.isApkDownloaded("0.2.0"))
    }

    @Test
    fun testSha256CorruptedMismatchThrowsSecurityExceptionAndDeletesFile() = runTest {
        val downloadDir = tempFolder.newFolder("updates")
        val testPayload = "Corrupted APK content bytes".toByteArray()
        val mismatchHash = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"

        val client = createMockClient { req ->
            mockStreamResponse(req, testPayload)
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val apkAsset = ApkAsset(
            name = "EchoLedger-corrupt.apk",
            sizeBytes = testPayload.size.toLong(),
            downloadUrl = "https://example.com/corrupt.apk",
            sha256 = mismatchHash
        )

        val destFile = downloader.getApkFile("0.2.0")

        var caughtSecurityException: SecurityException? = null
        try {
            downloader.download(apkAsset, "0.2.0").collect()
        } catch (e: SecurityException) {
            caughtSecurityException = e
        }

        assertNotNull("Must throw SecurityException on hash mismatch", caughtSecurityException)
        assertTrue(
            "Exception message must mention SHA-256",
            caughtSecurityException!!.message?.contains("SHA-256 校验失败") == true
        )
        assertFalse("Destination file must be deleted upon checksum failure", destFile.exists())
    }

    @Test
    fun testDownloadWithoutSha256CompletesSuccessfully() = runTest {
        val downloadDir = tempFolder.newFolder("updates")
        val testPayload = "APK payload without provided hash".toByteArray()

        val client = createMockClient { req ->
            mockStreamResponse(req, testPayload)
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val apkAsset = ApkAsset(
            name = "EchoLedger-nohash.apk",
            sizeBytes = testPayload.size.toLong(),
            downloadUrl = "https://example.com/nohash.apk",
            sha256 = null
        )

        val progressList = mutableListOf<DownloadProgress>()
        downloader.download(apkAsset, "0.3.0").collect { progressList.add(it) }

        assertEquals(100, progressList.last().percent)
        val destFile = downloader.getApkFile("0.3.0")
        assertTrue(destFile.exists())
        assertEquals(testPayload.size.toLong(), destFile.length())
    }

    @Test
    fun testHttpErrorThrowsIOExceptionAndCleansUpFile() = runTest {
        val downloadDir = tempFolder.newFolder("updates")

        val client = createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(500)
                .message("Internal Server Error")
                .body("Server error".toResponseBody("text/plain".toMediaType()))
                .build()
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val apkAsset = ApkAsset(
            name = "EchoLedger.apk",
            sizeBytes = 1000,
            downloadUrl = "https://example.com/error.apk"
        )

        val destFile = downloader.getApkFile("0.4.0")

        var caughtIoException: IOException? = null
        try {
            downloader.download(apkAsset, "0.4.0").collect()
        } catch (e: IOException) {
            caughtIoException = e
        }

        assertNotNull("Must throw IOException on HTTP error", caughtIoException)
        assertTrue("Error message must indicate HTTP 500", caughtIoException!!.message?.contains("500") == true)
        assertFalse("Destination file should not be left on disk after error", destFile.exists())
    }

    @Test
    fun testClearUpdatesRemovesAllFiles() {
        val downloadDir = tempFolder.newFolder("updates")
        val downloader = UpdateDownloader(downloadDir = downloadDir)

        // Seed directory with dummy APKs
        val file1 = File(downloadDir, "update_0.1.0.apk").apply { writeText("old1") }
        val file2 = File(downloadDir, "update_0.2.0.apk").apply { writeText("old2") }
        assertTrue(file1.exists())
        assertTrue(file2.exists())

        val cleared = downloader.clearUpdates()
        assertTrue("clearUpdates must succeed", cleared)
        assertTrue("Download directory must still exist", downloadDir.exists())
        assertFalse("Old file 1 must be deleted", file1.exists())
        assertFalse("Old file 2 must be deleted", file2.exists())
        assertEquals("Directory should be empty", 0, downloadDir.listFiles()?.size ?: 0)
    }

    @Test
    fun testDownloadReleaseOverloadCallsApkAssetDownload() = runTest {
        val downloadDir = tempFolder.newFolder("updates")
        val payload = "Overload test bytes".toByteArray()
        val hash = computeSha256(payload)

        val client = createMockClient { req ->
            mockStreamResponse(req, payload)
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val release = UpdateRelease(
            versionName = "v0.5.0",
            semVer = SemVer(0, 5, 0),
            title = "Release 0.5.0",
            changelog = "Changelog",
            publishedAt = "2026-11-03",
            apkAsset = ApkAsset("EchoLedger-v0.5.0.apk", payload.size.toLong(), "https://example.com/v0.5.0.apk", hash)
        )

        val progressList = mutableListOf<DownloadProgress>()
        downloader.download(release).collect { progressList.add(it) }

        assertEquals(100, progressList.last().percent)
        val destFile = downloader.getApkFile("v0.5.0")
        assertTrue(destFile.exists())
    }
}
