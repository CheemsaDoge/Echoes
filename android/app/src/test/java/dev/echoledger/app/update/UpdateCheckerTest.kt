package dev.echoledger.app.update

import kotlinx.coroutines.test.runTest
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import java.io.IOException

class UpdateCheckerTest {

    private fun createMockClient(handler: (Request) -> Response): OkHttpClient {
        return OkHttpClient.Builder()
            .addInterceptor(Interceptor { chain ->
                handler(chain.request())
            })
            .build()
    }

    private fun mockResponse(
        request: Request,
        code: Int = 200,
        body: String = "",
        headers: Map<String, String> = emptyMap()
    ): Response {
        val builder = Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(code)
            .message(
                when (code) {
                    200 -> "OK"
                    403 -> "Forbidden"
                    404 -> "Not Found"
                    else -> "Status $code"
                }
            )
            .body(body.toResponseBody("application/json".toMediaType()))

        headers.forEach { (k, v) -> builder.addHeader(k, v) }
        return builder.build()
    }

    @Test
    fun testCheckUpdateAvailableWhenRemoteNewer() = runTest {
        val releaseJson = """
            {
              "tag_name": "v0.2.0",
              "name": "EchoLedger v0.2.0",
              "body": "Bug fixes and performance improvements\n\nSHA-256: 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
              "published_at": "2026-11-03T12:00:00Z",
              "assets": [
                {
                  "name": "EchoLedger-v0.2.0.apk",
                  "size": 15000000,
                  "browser_download_url": "https://github.com/CheemsaDoge/Echoes/releases/download/v0.2.0/EchoLedger-v0.2.0.apk"
                }
              ]
            }
        """.trimIndent()

        val client = createMockClient { req ->
            assertEquals("https://api.github.com/repos/CheemsaDoge/Echoes/releases/latest", req.url.toString())
            assertEquals("EchoLedger-Android/0.1.0", req.header("User-Agent"))
            assertEquals("application/vnd.github+json", req.header("Accept"))
            mockResponse(req, 200, releaseJson)
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue("Expected Available, got $result", result is UpdateResult.Available)
        val available = result as UpdateResult.Available
        assertEquals("v0.2.0", available.release.versionName)
        assertEquals(SemVer(0, 2, 0), available.release.semVer)
        assertEquals("EchoLedger v0.2.0", available.release.title)
        assertEquals("EchoLedger-v0.2.0.apk", available.release.apkAsset.name)
        assertEquals(15000000L, available.release.apkAsset.sizeBytes)
        assertEquals("https://github.com/CheemsaDoge/Echoes/releases/download/v0.2.0/EchoLedger-v0.2.0.apk", available.release.apkAsset.downloadUrl)
        assertEquals("1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef", available.release.apkAsset.sha256)
    }

    @Test
    fun testCheckUpdateUpToDateWhenVersionSameOrOlder() = runTest {
        val sameVersionJson = """
            {
              "tag_name": "v0.1.0",
              "name": "EchoLedger v0.1.0",
              "body": "Initial release",
              "assets": [
                {
                  "name": "app-release.apk",
                  "size": 12000000,
                  "browser_download_url": "https://example.com/app.apk"
                }
              ]
            }
        """.trimIndent()

        val client = createMockClient { req ->
            mockResponse(req, 200, sameVersionJson)
        }

        // Exact same version
        val checkerSame = UpdateChecker(client = client, currentVersion = "0.1.0")
        val resultSame = checkerSame.checkForUpdate()
        assertTrue("Expected UpToDate for 0.1.0 == 0.1.0, got $resultSame", resultSame is UpdateResult.UpToDate)
        assertEquals("0.1.0", (resultSame as UpdateResult.UpToDate).currentVersion)

        // Local version is newer than remote (e.g. dev build 0.2.0 vs released 0.1.0)
        val checkerNewer = UpdateChecker(client = client, currentVersion = "0.2.0")
        val resultNewer = checkerNewer.checkForUpdate()
        assertTrue("Expected UpToDate for local 0.2.0 > remote 0.1.0", resultNewer is UpdateResult.UpToDate)
    }

    @Test
    fun testCheckUpdateNoApkAssetEmitsDescriptiveError() = runTest {
        val noApkJson = """
            {
              "tag_name": "v0.2.0",
              "name": "EchoLedger v0.2.0",
              "body": "No APK in this release",
              "assets": [
                {
                  "name": "source-code.tar.gz",
                  "size": 500000,
                  "browser_download_url": "https://example.com/source.tar.gz"
                }
              ]
            }
        """.trimIndent()

        val client = createMockClient { req ->
            mockResponse(req, 200, noApkJson)
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue("Expected Error, got $result", result is UpdateResult.Error)
        val error = result as UpdateResult.Error
        assertTrue("Error must describe missing APK asset", error.message.contains("最新版本中未找到适用于 Android 的安装包"))
        assertFalse("Missing APK is a non-retryable release error", error.canRetry)
    }

    @Test
    fun testCheckUpdate404NotFoundEmitsCleanNotice() = runTest {
        val client = createMockClient { req ->
            mockResponse(req, 404, """{"message": "Not Found"}""")
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue("Expected Error, got $result", result is UpdateResult.Error)
        val error = result as UpdateResult.Error
        assertEquals("当前仓库暂无发布的版本", error.message)
        assertFalse("404 with no releases should not retry immediately", error.canRetry)
    }

    @Test
    fun testCheckUpdate403RateLimitWithResetHeader() = runTest {
        val resetEpoch = (System.currentTimeMillis() / 1000) + 1800 // 30 minutes from now
        val client = createMockClient { req ->
            mockResponse(
                request = req,
                code = 403,
                body = """{"message": "API rate limit exceeded for 1.2.3.4"}""",
                headers = mapOf(
                    "X-RateLimit-Limit" to "60",
                    "X-RateLimit-Remaining" to "0",
                    "X-RateLimit-Reset" to resetEpoch.toString()
                )
            )
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue("Expected Error on rate limit, got $result", result is UpdateResult.Error)
        val error = result as UpdateResult.Error
        assertTrue("Error message must mention rate limit", error.message.contains("GitHub API 访问受限"))
        assertTrue("Error message must mention reset Epoch or minutes", error.message.contains(resetEpoch.toString()))
        assertTrue("Rate limit errors are retryable after reset", error.canRetry)
    }

    @Test
    fun testSha256ExtractionFromCompanionAsset() = runTest {
        val companionSha = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
        val releaseJson = """
            {
              "tag_name": "v0.3.0",
              "name": "Release 0.3.0",
              "body": "Companion checksum test",
              "assets": [
                {
                  "name": "EchoLedger.apk",
                  "size": 10000,
                  "browser_download_url": "https://example.com/EchoLedger.apk"
                },
                {
                  "name": "EchoLedger.apk.sha256",
                  "size": 90,
                  "browser_download_url": "https://example.com/EchoLedger.apk.sha256"
                }
              ]
            }
        """.trimIndent()

        val client = createMockClient { req ->
            val url = req.url.toString()
            if (url.endsWith(".sha256")) {
                mockResponse(req, 200, "$companionSha  EchoLedger.apk\n")
            } else {
                mockResponse(req, 200, releaseJson)
            }
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue(result is UpdateResult.Available)
        val available = result as UpdateResult.Available
        assertEquals(companionSha, available.release.apkAsset.sha256)
    }

    @Test
    fun testMultipleApksPrioritizeReleaseBuild() = runTest {
        val multiApkJson = """
            {
              "tag_name": "v0.4.0",
              "name": "EchoLedger v0.4.0",
              "body": "",
              "assets": [
                {
                  "name": "app-debug.apk",
                  "size": 18000000,
                  "browser_download_url": "https://example.com/app-debug.apk"
                },
                {
                  "name": "app-release.apk",
                  "size": 16000000,
                  "browser_download_url": "https://example.com/app-release.apk"
                }
              ]
            }
        """.trimIndent()

        val client = createMockClient { req ->
            mockResponse(req, 200, multiApkJson)
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue(result is UpdateResult.Available)
        val available = result as UpdateResult.Available
        assertEquals("app-release.apk", available.release.apkAsset.name)
        assertEquals("https://example.com/app-release.apk", available.release.apkAsset.downloadUrl)
    }

    @Test
    fun testCompanionChecksumMatchesSelectedApkWhenMultipleApksExist() = runTest {
        val debugHash = "0000000000000000000000000000000000000000000000000000000000000000"
        val releaseHash = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        val multiApkJson = """
            {
              "tag_name": "v0.4.5",
              "name": "EchoLedger v0.4.5",
              "body": "",
              "assets": [
                {
                  "name": "app-debug.apk.sha256",
                  "browser_download_url": "https://example.com/app-debug.apk.sha256"
                },
                {
                  "name": "app-debug.apk",
                  "size": 18000000,
                  "browser_download_url": "https://example.com/app-debug.apk"
                },
                {
                  "name": "app-release.apk.sha256",
                  "browser_download_url": "https://example.com/app-release.apk.sha256"
                },
                {
                  "name": "app-release.apk",
                  "size": 16000000,
                  "browser_download_url": "https://example.com/app-release.apk"
                }
              ]
            }
        """.trimIndent()

        val client = createMockClient { req ->
            val url = req.url.toString()
            when {
                url.endsWith("app-debug.apk.sha256") -> mockResponse(req, 200, "$debugHash  app-debug.apk\n")
                url.endsWith("app-release.apk.sha256") -> mockResponse(req, 200, "$releaseHash  app-release.apk\n")
                else -> mockResponse(req, 200, multiApkJson)
            }
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue(result is UpdateResult.Available)
        val available = result as UpdateResult.Available
        assertEquals("app-release.apk", available.release.apkAsset.name)
        assertEquals("Selected APK checksum must match release APK hash, not debug hash", releaseHash, available.release.apkAsset.sha256)
    }

    @Test
    fun testCollectiveChecksumsFileExtractsTargetApkWithNormalizedPath() = runTest {
        val debugHash = "1111111111111111111111111111111111111111111111111111111111111111"
        val releaseHash = "2222222222222222222222222222222222222222222222222222222222222222"
        val releaseJson = """
            {
              "tag_name": "v0.6.0",
              "name": "EchoLedger v0.6.0",
              "body": "",
              "assets": [
                {
                  "name": "sha256sums.txt",
                  "browser_download_url": "https://example.com/sha256sums.txt"
                },
                {
                  "name": "app-debug.apk",
                  "size": 18000000,
                  "browser_download_url": "https://example.com/app-debug.apk"
                },
                {
                  "name": "app-release.apk",
                  "size": 16000000,
                  "browser_download_url": "https://example.com/app-release.apk"
                }
              ]
            }
        """.trimIndent()

        val checksumsFileContent = """
            # Generated checksums
            $debugHash  ./build/outputs/apk/debug/app-debug.apk
            $releaseHash  *./build/outputs/apk/release/app-release.apk
        """.trimIndent()

        val client = createMockClient { req ->
            val url = req.url.toString()
            if (url.endsWith("sha256sums.txt")) {
                mockResponse(req, 200, checksumsFileContent)
            } else {
                mockResponse(req, 200, releaseJson)
            }
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue(result is UpdateResult.Available)
        val available = result as UpdateResult.Available
        assertEquals("app-release.apk", available.release.apkAsset.name)
        assertEquals(releaseHash, available.release.apkAsset.sha256)
    }

    @Test
    fun testFallbackToReleasesListWhenLatestReturns404() = runTest {
        val releasesListJson = """
            [
              {
                "tag_name": "v0.2.5",
                "name": "EchoLedger v0.2.5 Pre-release",
                "body": "Fallback release found",
                "assets": [
                  {
                    "name": "app-release.apk",
                    "size": 14000000,
                    "browser_download_url": "https://example.com/fallback.apk"
                  }
                ]
              }
            ]
        """.trimIndent()

        val client = createMockClient { req ->
            val url = req.url.toString()
            if (url.endsWith("/releases/latest")) {
                mockResponse(req, 404, """{"message": "Not Found"}""")
            } else if (url.contains("/releases?per_page=1")) {
                mockResponse(req, 200, releasesListJson)
            } else {
                mockResponse(req, 404, "Not Found")
            }
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue("Expected Available from fallback /releases, got $result", result is UpdateResult.Available)
        val available = result as UpdateResult.Available
        assertEquals("v0.2.5", available.release.versionName)
        assertEquals(SemVer(0, 2, 5), available.release.semVer)
    }

    @Test
    fun testNetworkExceptionHandling() = runTest {
        val client = createMockClient {
            throw IOException("Failed to connect to api.github.com:443")
        }

        val checker = UpdateChecker(client = client, currentVersion = "0.1.0")
        val result = checker.checkForUpdate()

        assertTrue("Expected Error on network failure, got $result", result is UpdateResult.Error)
        val error = result as UpdateResult.Error
        assertTrue("Error message must indicate failure", error.message.contains("检查更新失败"))
        assertTrue("Network errors should be retryable", error.canRetry)
    }
}
