package dev.echoledger.app.update

import dev.echoledger.app.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

class UpdateChecker(
    private val client: OkHttpClient = OkHttpClient(),
    private val baseUrl: String = DEFAULT_BASE_URL,
    private val currentVersion: String = BuildConfig.VERSION_NAME
) {
    companion object {
        const val DEFAULT_BASE_URL = "https://api.github.com/repos/CheemsaDoge/Echoes"
    }

    suspend fun checkUpdate(): UpdateResult = checkForUpdate()

    suspend fun checkForUpdate(): UpdateResult = withContext(Dispatchers.IO) {
        try {
            val releaseJson = fetchLatestReleaseJson()
                ?: return@withContext UpdateResult.Error("当前仓库暂无发布的版本", canRetry = false)

            val tagName = releaseJson.optString("tag_name", "").ifEmpty {
                releaseJson.optString("name", "")
            }
            if (tagName.isEmpty()) {
                return@withContext UpdateResult.Error("当前仓库暂无发布的版本", canRetry = false)
            }

            val remoteSemVer = SemVer.parse(tagName)
                ?: return@withContext UpdateResult.Error("无法解析远端版本号: $tagName", canRetry = false)

            val localSemVer = SemVer.parse(currentVersion)
            if (localSemVer != null && remoteSemVer <= localSemVer) {
                return@withContext UpdateResult.UpToDate(currentVersion)
            }

            // Parse APK assets
            val assetsArray = releaseJson.optJSONArray("assets")
            val apkAssets = mutableListOf<JSONObject>()
            if (assetsArray != null) {
                for (i in 0 until assetsArray.length()) {
                    val asset = assetsArray.getJSONObject(i)
                    val name = asset.optString("name", "")
                    if (name.endsWith(".apk", ignoreCase = true)) {
                        apkAssets.add(asset)
                    }
                }
            }

            if (apkAssets.isEmpty()) {
                return@withContext UpdateResult.Error("最新版本中未找到适用于 Android 的安装包", canRetry = false)
            }

            // Prioritize release APK if multiple are found
            val selectedAsset = apkAssets.firstOrNull {
                it.optString("name").contains("release", ignoreCase = true)
            } ?: apkAssets.first()

            val assetName = selectedAsset.optString("name")
            val sizeBytes = selectedAsset.optLong("size", 0L)
            val downloadUrl = selectedAsset.optString("browser_download_url")

            val sha256 = extractSha256(releaseJson, assetsArray, assetName)

            val release = UpdateRelease(
                versionName = tagName,
                semVer = remoteSemVer,
                title = releaseJson.optString("name", tagName),
                changelog = releaseJson.optString("body", ""),
                publishedAt = releaseJson.optString("published_at", ""),
                apkAsset = ApkAsset(
                    name = assetName,
                    sizeBytes = sizeBytes,
                    downloadUrl = downloadUrl,
                    sha256 = sha256
                )
            )

            UpdateResult.Available(release)
        } catch (e: RateLimitException) {
            UpdateResult.Error(
                "GitHub API 访问受限 (Rate Limit Exceeded): ${e.message}",
                canRetry = true
            )
        } catch (e: NotFoundException) {
            UpdateResult.Error("当前仓库暂无发布的版本", canRetry = false)
        } catch (e: Exception) {
            UpdateResult.Error(
                "检查更新失败: ${e.message ?: e.javaClass.simpleName}",
                canRetry = true
            )
        }
    }

    private class RateLimitException(message: String) : Exception(message)
    private class NotFoundException(message: String = "Not Found") : Exception(message)

    private fun fetchLatestReleaseJson(): JSONObject? {
        val request = Request.Builder()
            .url("$baseUrl/releases/latest")
            .header("Accept", "application/vnd.github+json")
            .header("User-Agent", "EchoLedger-Android/$currentVersion")
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                when (response.code) {
                    200 -> {
                        val body = response.body?.string().orEmpty()
                        if (body.trim().isEmpty() || body.trim() == "{}") null else JSONObject(body)
                    }
                    403 -> {
                        val resetHeader = response.header("X-RateLimit-Reset")
                        val resetText = formatRateLimitReset(resetHeader)
                        throw RateLimitException("请求频率超限，重置时间: $resetText (X-RateLimit-Reset: $resetHeader)")
                    }
                    404 -> {
                        fetchFallbackReleases()
                    }
                    else -> {
                        throw IOException("HTTP ${response.code}: ${response.message}")
                    }
                }
            }
        } catch (e: RateLimitException) {
            throw e
        } catch (e: NotFoundException) {
            throw e
        } catch (e: IOException) {
            throw e
        } catch (e: Exception) {
            throw e
        }
    }

    private fun fetchFallbackReleases(): JSONObject? {
        val request = Request.Builder()
            .url("$baseUrl/releases?per_page=1")
            .header("Accept", "application/vnd.github+json")
            .header("User-Agent", "EchoLedger-Android/$currentVersion")
            .build()

        return try {
            client.newCall(request).execute().use { response ->
                when (response.code) {
                    200 -> {
                        val body = response.body?.string().orEmpty()
                        if (body.trim().isEmpty() || body.trim() == "[]") {
                            null
                        } else {
                            val array = JSONArray(body)
                            if (array.length() > 0) array.getJSONObject(0) else null
                        }
                    }
                    403 -> {
                        val resetHeader = response.header("X-RateLimit-Reset")
                        val resetText = formatRateLimitReset(resetHeader)
                        throw RateLimitException("请求频率超限，重置时间: $resetText (X-RateLimit-Reset: $resetHeader)")
                    }
                    404 -> null
                    else -> null
                }
            }
        } catch (e: RateLimitException) {
            throw e
        } catch (_: Exception) {
            null
        }
    }

    private fun formatRateLimitReset(resetHeader: String?): String {
        val resetEpochSec = resetHeader?.toLongOrNull() ?: return "稍后"
        val currentSec = System.currentTimeMillis() / 1000
        val diffSec = resetEpochSec - currentSec
        return if (diffSec > 0) {
            val mins = (diffSec + 59) / 60
            "$mins 分钟后 (Epoch: $resetEpochSec)"
        } else {
            "即刻 (Epoch: $resetEpochSec)"
        }
    }

    private fun extractSha256(
        releaseJson: JSONObject,
        assetsArray: JSONArray?,
        apkAssetName: String
    ): String? {
        val hex64Strict = Regex("""(?i)(?:sha[-_]?256|checksum)[\s:=]+([a-f0-9]{64})""")
        val hex64Any = Regex("""\b([a-fA-F0-9]{64})\b""")

        // 1. Try companion .sha256 asset first
        if (assetsArray != null) {
            for (i in 0 until assetsArray.length()) {
                val asset = assetsArray.getJSONObject(i)
                val name = asset.optString("name", "")
                val isShaAsset = name.equals("$apkAssetName.sha256", ignoreCase = true) ||
                        name.endsWith(".sha256", ignoreCase = true) ||
                        name.equals("sha256sums.txt", ignoreCase = true)

                if (isShaAsset) {
                    val shaUrl = asset.optString("browser_download_url")
                    if (shaUrl.isNotEmpty()) {
                        val downloadedSha = tryFetchCompanionSha(shaUrl)
                        if (downloadedSha != null) {
                            return downloadedSha
                        }
                    }
                }
            }
        }

        // 2. Scan release body markdown for 64-char hex SHA-256
        val body = releaseJson.optString("body", "")
        if (body.isNotEmpty()) {
            val match = hex64Strict.find(body) ?: hex64Any.find(body)
            if (match != null) {
                return match.groupValues[1].lowercase()
            }
        }

        return null
    }

    private fun tryFetchCompanionSha(url: String): String? {
        return try {
            val request = Request.Builder()
                .url(url)
                .header("User-Agent", "EchoLedger-Android/$currentVersion")
                .build()
            client.newCall(request).execute().use { resp ->
                if (resp.isSuccessful) {
                    val text = resp.body?.string().orEmpty()
                    val match = Regex("""\b([a-fA-F0-9]{64})\b""").find(text)
                    match?.groupValues?.get(1)?.lowercase()
                } else null
            }
        } catch (_: Exception) {
            null
        }
    }
}
