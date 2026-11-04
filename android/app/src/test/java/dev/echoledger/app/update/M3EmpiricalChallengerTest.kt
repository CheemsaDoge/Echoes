package dev.echoledger.app.update

import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.test.runTest
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import okio.ForwardingSource
import okio.buffer
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException
import java.security.MessageDigest

/**
 * Empirical stress tests and adversarial verification harness for Milestone M3.
 */
class M3EmpiricalChallengerTest {

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

    private fun mockJsonResponse(
        request: Request,
        code: Int = 200,
        body: String = ""
    ): Response {
        return Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(code)
            .message(if (code == 200) "OK" else "Status $code")
            .body(body.toResponseBody("application/json".toMediaType()))
            .build()
    }

    // =========================================================================
    // CHALLENGE 1: SemVer Parser Boundary & Stress Testing
    // =========================================================================

    @Test
    fun testSemVerMultiplePreReleaseDotSeparatedPrecedence() {
        // SemVer 2.0.0 specification section 11 precedence chain:
        // 1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0
        val vAlpha = SemVer.parse("1.0.0-alpha")!!
        val vAlpha1 = SemVer.parse("1.0.0-alpha.1")!!
        val vAlphaBeta = SemVer.parse("1.0.0-alpha.beta")!!
        val vBeta = SemVer.parse("1.0.0-beta")!!
        val vBeta2 = SemVer.parse("1.0.0-beta.2")!!
        val vBeta11 = SemVer.parse("1.0.0-beta.11")!!
        val vRc1 = SemVer.parse("1.0.0-rc.1")!!
        val vFinal = SemVer.parse("1.0.0")!!

        val sortedList = listOf(vAlpha, vAlpha1, vAlphaBeta, vBeta, vBeta2, vBeta11, vRc1, vFinal)
        for (i in 0 until sortedList.size - 1) {
            val curr = sortedList[i]
            val next = sortedList[i + 1]
            assertTrue("Expected $curr < $next", curr < next)
            assertTrue("Expected $next > $curr", next > curr)
            assertNotEquals("Expected $curr != $next", curr, next)
        }
    }

    @Test
    fun testSemVerNumericVsAlphanumericDotComponents() {
        // Numeric identifiers always have lower precedence than non-numeric identifiers:
        // 1.0.0-alpha.1 < 1.0.0-alpha.a
        val numComp = SemVer.parse("1.0.0-alpha.1")!!
        val alphaComp = SemVer.parse("1.0.0-alpha.a")!!
        assertTrue("Numeric component must precede alphanumeric", numComp < alphaComp)

        // 1.0.0-beta.99 < 1.0.0-beta.alpha
        val numBeta = SemVer.parse("1.0.0-beta.99")!!
        val alphaBeta = SemVer.parse("1.0.0-beta.alpha")!!
        assertTrue("Numeric component 99 must precede alphanumeric alpha", numBeta < alphaBeta)

        // Same component count but one side has extra components:
        // 1.0.0-alpha < 1.0.0-alpha.1
        val shorter = SemVer.parse("1.0.0-alpha")!!
        val longer = SemVer.parse("1.0.0-alpha.1")!!
        assertTrue("Shorter pre-release must precede longer when prefix is identical", shorter < longer)
    }

    @Test
    fun testSemVerSingleDigitAndMalformedInputsSafeHandling() {
        // Single digits and non-three-part versions must safely return null without throwing exceptions
        val inputs = listOf(
            "1",
            "v1",
            "V1",
            "1.0",
            "v1.0",
            "0.1",
            "1.",
            "1.0.",
            ".1.0.0",
            "1..0",
            "1.0.0.0.0",
            "v1.0.0.1.2",
            "-1.0.0",
            "1.-1.0",
            "1.0.-1",
            "abc",
            "v.abc",
            "..."
        )

        for (input in inputs) {
            val parsed = SemVer.parse(input)
            assertNull("Input '$input' must return null safely without crashing", parsed)
        }
    }

    @Test
    fun testSemVerWhitespacePermutations() {
        val cases = listOf(
            "\t  1.2.3  \n" to SemVer(1, 2, 3),
            "\r\nv1.2.3\r\n" to SemVer(1, 2, 3),
            "   V1.2.3-alpha.1   " to SemVer(1, 2, 3, "alpha.1"),
            "   v  1.2.3   " to SemVer(1, 2, 3),
            "  v0.0.1+build.10  " to SemVer(0, 0, 1),
            " \n\t v2.0.0-rc.1+sha.abcdef \n " to SemVer(2, 0, 0, "rc.1")
        )

        for ((index, pair) in cases.withIndex()) {
            val (raw, expected) = pair
            val parsed = SemVer.parse(raw)
            assertEquals("Whitespace test case #$index ('$raw') failed", expected, parsed)
        }
    }

    @Test
    fun testSemVerVeryLargeNumbersRobustness() {
        // Core parts that exceed standard 32-bit Integer range must return null safely without throwing
        val overflowInputs = listOf(
            "999999999999999999999999999999999999.0.0",
            "0.999999999999999999999999999999999999.0",
            "0.0.999999999999999999999999999999999999",
            "2147483648.0.0" // Int.MAX_VALUE + 1
        )
        for (overflow in overflowInputs) {
            assertNull("Overflow input '$overflow' must return null safely", SemVer.parse(overflow))
        }

        // Pre-release numeric components with large numbers compared with BigInteger
        val bigNum1 = SemVer.parse("1.0.0-rc.999999999999999999999999999999999999999999999999")!!
        val bigNum2 = SemVer.parse("1.0.0-rc.1000000000000000000000000000000000000000000000000")!!
        assertTrue("Arbitrary precision numbers in pre-release must compare correctly", bigNum1 < bigNum2)
    }

    // =========================================================================
    // CHALLENGE 2: UpdateChecker Adversarial Payloads
    // =========================================================================

    @Test
    fun testUpdateCheckerAdversarialEmptyAssets() = runTest {
        val releaseJson = """
            {
              "tag_name": "v0.9.0",
              "name": "Release with empty assets",
              "body": "No files here",
              "assets": []
            }
        """.trimIndent()

        val client = createMockClient { req -> mockJsonResponse(req, 200, releaseJson) }
        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue("Empty assets must yield UpdateResult.Error", result is UpdateResult.Error)
        val error = result as UpdateResult.Error
        assertEquals("最新版本中未找到适用于 Android 的安装包", error.message)
        assertFalse("Non-existent APK cannot be retried immediately", error.canRetry)
    }

    @Test
    fun testUpdateCheckerAdversarialMultipleNonApkAssets() = runTest {
        val releaseJson = """
            {
              "tag_name": "v0.9.1",
              "name": "Release with only non-APK assets",
              "body": "Source only",
              "assets": [
                { "name": "EchoLedger.zip", "size": 1000, "browser_download_url": "https://example.com/a.zip" },
                { "name": "EchoLedger.tar.gz", "size": 2000, "browser_download_url": "https://example.com/b.tar.gz" },
                { "name": "EchoLedger.exe", "size": 3000, "browser_download_url": "https://example.com/c.exe" },
                { "name": "checksums.sha256", "size": 100, "browser_download_url": "https://example.com/checksums.sha256" }
              ]
            }
        """.trimIndent()

        val client = createMockClient { req -> mockJsonResponse(req, 200, releaseJson) }
        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue(result is UpdateResult.Error)
        val error = result as UpdateResult.Error
        assertEquals("最新版本中未找到适用于 Android 的安装包", error.message)
        assertFalse(error.canRetry)
    }

    @Test
    fun testUpdateCheckerReleaseTagMissingVPrefix() = runTest {
        val releaseJson = """
            {
              "tag_name": "0.9.5",
              "name": "0.9.5",
              "body": "",
              "assets": [
                {
                  "name": "EchoLedger-0.9.5.apk",
                  "size": 12000000,
                  "browser_download_url": "https://example.com/EchoLedger-0.9.5.apk"
                }
              ]
            }
        """.trimIndent()

        val client = createMockClient { req -> mockJsonResponse(req, 200, releaseJson) }
        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue("Release tag without 'v' must be parsed cleanly", result is UpdateResult.Available)
        val available = result as UpdateResult.Available
        assertEquals("0.9.5", available.release.versionName)
        assertEquals(SemVer(0, 9, 5), available.release.semVer)
        assertEquals("EchoLedger-0.9.5.apk", available.release.apkAsset.name)
    }

    @Test
    fun testUpdateCheckerUppercaseSha256InCompanionAndBody() = runTest {
        val upperHash = "A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90"
        val expectedLowerHash = upperHash.lowercase()

        // 1. Uppercase in companion file
        val companionReleaseJson = """
            {
              "tag_name": "v1.1.0",
              "name": "Release 1.1.0",
              "body": "",
              "assets": [
                {
                  "name": "app-release.apk",
                  "size": 15000000,
                  "browser_download_url": "https://example.com/app-release.apk"
                },
                {
                  "name": "app-release.apk.sha256",
                  "size": 100,
                  "browser_download_url": "https://example.com/app-release.apk.sha256"
                }
              ]
            }
        """.trimIndent()

        val clientCompanion = createMockClient { req ->
            if (req.url.toString().endsWith(".sha256")) {
                Response.Builder()
                    .request(req)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body("$upperHash  app-release.apk\n".toResponseBody("text/plain".toMediaType()))
                    .build()
            } else {
                mockJsonResponse(req, 200, companionReleaseJson)
            }
        }

        val checker1 = UpdateChecker(client = clientCompanion, currentVersion = "0.1.0")
        val res1 = checker1.checkForUpdate()
        assertTrue(res1 is UpdateResult.Available)
        assertEquals(expectedLowerHash, (res1 as UpdateResult.Available).release.apkAsset.sha256)

        // 2. Uppercase in release body markdown
        val bodyReleaseJson = """
            {
              "tag_name": "v1.2.0",
              "name": "Release 1.2.0",
              "body": "## Checksum\nSHA-256: $upperHash",
              "assets": [
                {
                  "name": "EchoLedger.apk",
                  "size": 15000000,
                  "browser_download_url": "https://example.com/EchoLedger.apk"
                }
              ]
            }
        """.trimIndent()

        val clientBody = createMockClient { req -> mockJsonResponse(req, 200, bodyReleaseJson) }
        val checker2 = UpdateChecker(client = clientBody, currentVersion = "0.1.0")
        val res2 = checker2.checkForUpdate()
        assertTrue(res2 is UpdateResult.Available)
        assertEquals(expectedLowerHash, (res2 as UpdateResult.Available).release.apkAsset.sha256)
    }

    @Test
    fun testUpdateCheckerCompanionSha256WithCommentsAndMultiLine() = runTest {
        val targetHash = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        val otherHash = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

        val releaseJson = """
            {
              "tag_name": "v1.3.0",
              "name": "Release 1.3.0",
              "body": "",
              "assets": [
                {
                  "name": "sha256sums.txt",
                  "browser_download_url": "https://example.com/sha256sums.txt"
                },
                {
                  "name": "EchoLedger-v1.3.0.apk",
                  "size": 14000000,
                  "browser_download_url": "https://example.com/EchoLedger-v1.3.0.apk"
                }
              ]
            }
        """.trimIndent()

        val multiLineCompanionContent = """
            # Automated build sha256 checksums
            # Generated on 2026-11-03
            
            $otherHash  linux-x86_64.tar.gz
            $otherHash  windows-x64.zip
            
            # Target Android APK binary mode with directory path
            $targetHash  *./outputs/apk/release/EchoLedger-v1.3.0.apk
            
            # EOF
        """.trimIndent()

        val client = createMockClient { req ->
            if (req.url.toString().endsWith("sha256sums.txt")) {
                Response.Builder()
                    .request(req)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(multiLineCompanionContent.toResponseBody("text/plain".toMediaType()))
                    .build()
            } else {
                mockJsonResponse(req, 200, releaseJson)
            }
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue(result is UpdateResult.Available)
        val available = result as UpdateResult.Available
        assertEquals("EchoLedger-v1.3.0.apk", available.release.apkAsset.name)
        assertEquals(targetHash, available.release.apkAsset.sha256)
    }

    // =========================================================================
    // CHALLENGE 3: UpdateDownloader Integrity & Corrupted Payload Testing
    // =========================================================================

    @Test
    fun testUpdateDownloaderCorruptedSingleByteFlipThrowsSecurityExceptionAndCleansUp() = runTest {
        val downloadDir = tempFolder.newFolder("updates_corrupt_test")
        val originalPayload = ByteArray(16384) { (it * 3).toByte() }
        val originalHash = computeSha256(originalPayload)

        // Deliberately flip 1 bit in payload
        val corruptedPayload = originalPayload.clone()
        corruptedPayload[100] = (corruptedPayload[100].toInt() xor 0x01).toByte()

        val client = createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(corruptedPayload.toResponseBody("application/vnd.android.package-archive".toMediaType()))
                .build()
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val apkAsset = ApkAsset(
            name = "EchoLedger.apk",
            sizeBytes = originalPayload.size.toLong(),
            downloadUrl = "https://example.com/EchoLedger.apk",
            sha256 = originalHash // Expect original hash
        )

        val destFile = downloader.getApkFile("1.4.0")

        var securityExceptionThrown = false
        try {
            downloader.download(apkAsset, "1.4.0").collect()
        } catch (e: SecurityException) {
            securityExceptionThrown = true
            assertTrue("Message must indicate SHA-256 failure", e.message?.contains("SHA-256 校验失败") == true)
        }

        assertTrue("SecurityException MUST be thrown for single flipped byte", securityExceptionThrown)
        assertFalse("Destination APK file must NOT exist after corrupt download", destFile.exists())

        // Verify no leftover .tmp files
        val tmpFiles = downloadDir.listFiles { file -> file.name.contains(".tmp") } ?: emptyArray()
        assertEquals("All temporary files must be cleaned up", 0, tmpFiles.size)
    }

    @Test
    fun testUpdateDownloaderTruncatedStreamThrowsAndCleansUp() = runTest {
        val downloadDir = tempFolder.newFolder("updates_truncated_test")
        val fullPayload = ByteArray(32768) { (it % 128).toByte() }
        val fullHash = computeSha256(fullPayload)

        // Server cuts stream after 8192 bytes with IOException
        val client = createMockClient { req ->
            val partialBuffer = Buffer().write(fullPayload, 0, 8192)
            val forwardingSource = object : ForwardingSource(partialBuffer) {
                override fun read(sink: Buffer, byteCount: Long): Long {
                    val bytesRead = super.read(sink, byteCount)
                    if (bytesRead <= 0L) {
                        throw IOException("Connection abruptly terminated mid-stream")
                    }
                    return bytesRead
                }
            }
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(
                    object : okhttp3.ResponseBody() {
                        override fun contentType() = "application/vnd.android.package-archive".toMediaType()
                        override fun contentLength() = fullPayload.size.toLong()
                        override fun source() = forwardingSource.buffer()
                    }
                )
                .build()
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val apkAsset = ApkAsset(
            name = "EchoLedger.apk",
            sizeBytes = fullPayload.size.toLong(),
            downloadUrl = "https://example.com/EchoLedger.apk",
            sha256 = fullHash
        )

        val destFile = downloader.getApkFile("1.5.0")

        var ioExceptionThrown = false
        try {
            downloader.download(apkAsset, "1.5.0").collect()
        } catch (e: IOException) {
            ioExceptionThrown = true
            assertTrue("Must contain mid-stream disconnect reason", e.message?.contains("abruptly terminated") == true)
        }

        assertTrue("IOException MUST be thrown on truncated socket stream", ioExceptionThrown)
        assertFalse("Destination APK file must NOT be published", destFile.exists())

        // Verify temp files cleaned up
        val tmpFiles = downloadDir.listFiles { file -> file.name.contains(".tmp") } ?: emptyArray()
        assertEquals("Temporary files must be deleted", 0, tmpFiles.size)
    }

    @Test
    fun testSemVerMultipleHyphensAndLeadingZeroes() {
        val multiHyphen = SemVer.parse("1.0.0-rc-1-final.2")
        assertNotNull(multiHyphen)
        assertEquals(1, multiHyphen!!.major)
        assertEquals(0, multiHyphen.minor)
        assertEquals(0, multiHyphen.patch)
        assertEquals("rc-1-final.2", multiHyphen.preRelease)

        // Multiple hyphens ordering
        val h1 = SemVer.parse("1.0.0-rc-1")!!
        val h2 = SemVer.parse("1.0.0-rc-2")!!
        assertTrue(h1 < h2)

        // Leading zeroes in pre-release numeric identifiers
        val v01 = SemVer.parse("1.0.0-rc.01")!!
        val v1 = SemVer.parse("1.0.0-rc.1")!!
        assertEquals("Numeric value 01 and 1 evaluate to equal BigInteger", 0, v01.compareTo(v1))
    }

    @Test
    fun testUpdateCheckerAssetWithApkInMiddleIsNotSelected() = runTest {
        val releaseJson = """
            {
              "tag_name": "v1.4.0",
              "name": "Release 1.4.0",
              "body": "",
              "assets": [
                {
                  "name": "app.apk.backup",
                  "size": 1000,
                  "browser_download_url": "https://example.com/app.apk.backup"
                },
                {
                  "name": "app.apk.sha256",
                  "size": 64,
                  "browser_download_url": "https://example.com/app.apk.sha256"
                }
              ]
            }
        """.trimIndent()

        val client = createMockClient { req -> mockJsonResponse(req, 200, releaseJson) }
        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue(result is UpdateResult.Error)
        val error = result as UpdateResult.Error
        assertEquals("最新版本中未找到适用于 Android 的安装包", error.message)
    }

    @Test
    fun testUpdateCheckerCollectiveChecksumWindowsBackslashes() = runTest {
        val targetHash = "3333333333333333333333333333333333333333333333333333333333333333"
        val releaseJson = """
            {
              "tag_name": "v1.5.0",
              "name": "Release 1.5.0",
              "body": "",
              "assets": [
                {
                  "name": "checksums.txt",
                  "browser_download_url": "https://example.com/checksums.txt"
                },
                {
                  "name": "app-release.apk",
                  "size": 16000000,
                  "browser_download_url": "https://example.com/app-release.apk"
                }
              ]
            }
        """.trimIndent()

        val windowsChecksums = """
            # Windows CI output
            $targetHash  *D:\a\Echoes\build\app-release.apk
        """.trimIndent()

        val client = createMockClient { req ->
            if (req.url.toString().endsWith("checksums.txt")) {
                Response.Builder()
                    .request(req)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(windowsChecksums.toResponseBody("text/plain".toMediaType()))
                    .build()
            } else {
                mockJsonResponse(req, 200, releaseJson)
            }
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue(result is UpdateResult.Available)
        val available = result as UpdateResult.Available
        assertEquals("app-release.apk", available.release.apkAsset.name)
        assertEquals(targetHash, available.release.apkAsset.sha256)
    }

    @Test
    fun testUpdateCheckerMalformedRateLimitHeaderDoesNotCrash() = runTest {
        val client = createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(403)
                .message("Forbidden")
                .header("X-RateLimit-Reset", "invalid-non-number")
                .body("""{"message": "rate limit"}""".toResponseBody("application/json".toMediaType()))
                .build()
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue(result is UpdateResult.Error)
        val error = result as UpdateResult.Error
        assertTrue("Message must contain fallback text '稍后'", error.message.contains("稍后"))
        assertTrue("Rate limit is retryable", error.canRetry)
    }

    @Test
    fun testUpdateDownloaderBlankSha256CompletesWithoutCheck() = runTest {
        val downloadDir = tempFolder.newFolder("updates_blank_sha")
        val payload = "TEST_PAYLOAD".toByteArray()
        val client = createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(payload.toResponseBody("application/vnd.android.package-archive".toMediaType()))
                .build()
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val apkAsset = ApkAsset(
            name = "app.apk",
            sizeBytes = payload.size.toLong(),
            downloadUrl = "https://example.com/app.apk",
            sha256 = "   " // blank string
        )

        downloader.download(apkAsset, "1.7.0").collect()
        assertTrue("APK file should be downloaded successfully when sha256 is blank", downloader.isApkDownloaded("1.7.0"))
    }

    @Test
    fun testUpdateDownloaderIndeterminateProgressWhenContentLengthNegative() = runTest {
        val downloadDir = tempFolder.newFolder("updates_indeterminate")
        val payload = "CHUNKED_DATA_BYTES".toByteArray()
        val client = createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(
                    object : okhttp3.ResponseBody() {
                        override fun contentType() = "application/vnd.android.package-archive".toMediaType()
                        override fun contentLength() = -1L // chunked / unknown length
                        override fun source() = Buffer().write(payload)
                    }
                )
                .build()
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val apkAsset = ApkAsset(
            name = "app.apk",
            sizeBytes = 0L, // unknown size
            downloadUrl = "https://example.com/app.apk",
            sha256 = null
        )

        val progressList = mutableListOf<DownloadProgress>()
        downloader.download(apkAsset, "1.8.0").collect { progressList.add(it) }

        assertTrue(progressList.isNotEmpty())
        // When totalBytes <= 0, percent is -1
        assertTrue(progressList.any { it.percent == -1 })
        assertTrue(downloader.isApkDownloaded("1.8.0"))
    }

    @Test
    fun testUpdateDownloaderCorruptRetryPreservesExistingVerifiedFile() = runTest {
        val downloadDir = tempFolder.newFolder("updates_preserve_test")
        val destFile = File(downloadDir, "update_1.6.0.apk").apply {
            writeText("VALID_PREEXISTING_APK_CONTENT")
        }
        assertTrue("Pre-existing file must exist initially", destFile.exists())

        // A retry download fails with corrupt hash
        val badPayload = "CORRUPT_BYTES".toByteArray()
        val client = createMockClient { req ->
            Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(badPayload.toResponseBody("application/vnd.android.package-archive".toMediaType()))
                .build()
        }

        val downloader = UpdateDownloader(downloadDir = downloadDir, client = client)
        val apkAsset = ApkAsset(
            name = "app.apk",
            sizeBytes = badPayload.size.toLong(),
            downloadUrl = "https://example.com/bad.apk",
            sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
        )

        try {
            downloader.download(apkAsset, "1.6.0").collect()
            fail("Must fail on hash mismatch")
        } catch (_: SecurityException) {}

        // Pre-existing verified file must NOT be touched or deleted
        assertTrue("Pre-existing verified APK file must still exist", destFile.exists())
        assertEquals("VALID_PREEXISTING_APK_CONTENT", destFile.readText())
    }
}
