package dev.echoledger.app.update

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.job
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.MessageDigest

class UpdateDownloader(
    val downloadDir: File,
    private val client: OkHttpClient = OkHttpClient()
) {
    constructor(context: Context, client: OkHttpClient = OkHttpClient()) : this(
        File(context.cacheDir, "updates"),
        client
    )

    fun getApkFile(version: String): File = File(downloadDir, "update_${version}.apk")

    fun isApkDownloaded(version: String): Boolean {
        val file = getApkFile(version)
        return file.exists() && file.length() > 0
    }

    fun clearUpdates(): Boolean {
        return downloadDir.deleteRecursively().also { downloadDir.mkdirs() }
    }

    fun download(release: UpdateRelease): Flow<DownloadProgress> =
        download(release.apkAsset, release.versionName)

    fun download(
        apkAsset: ApkAsset,
        version: String = "latest"
    ): Flow<DownloadProgress> = flow {
        if (!downloadDir.exists()) {
            downloadDir.mkdirs()
        }

        val destinationFile = getApkFile(version)
        if (destinationFile.exists()) {
            destinationFile.delete()
        }

        val request = Request.Builder()
            .url(apkAsset.downloadUrl)
            .build()

        val call = client.newCall(request)

        currentCoroutineContext().job.invokeOnCompletion { cause ->
            if (cause is CancellationException) {
                call.cancel()
            }
        }

        try {
            call.execute().use { response ->
                if (!response.isSuccessful) {
                    throw IOException("下载安装包失败: HTTP ${response.code} ${response.message}")
                }

                val body = response.body ?: throw IOException("下载安装包失败: 响应体为空")
                val totalBytes = if (body.contentLength() > 0) body.contentLength() else apkAsset.sizeBytes

                // Emit initial 0% progress
                emit(DownloadProgress(0, totalBytes, 0))

                val digest = MessageDigest.getInstance("SHA-256")
                var bytesReadTotal = 0L
                var lastPercent = 0

                body.byteStream().use { input ->
                    FileOutputStream(destinationFile).use { output ->
                        val buffer = ByteArray(8192)
                        while (true) {
                            currentCoroutineContext().ensureActive()
                            val read = input.read(buffer)
                            if (read == -1) break

                            output.write(buffer, 0, read)
                            digest.update(buffer, 0, read)
                            bytesReadTotal += read

                            val percent = if (totalBytes > 0) {
                                ((bytesReadTotal * 100) / totalBytes).toInt().coerceIn(0, 100)
                            } else {
                                -1
                            }

                            if (percent != lastPercent) {
                                lastPercent = percent
                                emit(DownloadProgress(bytesReadTotal, totalBytes, percent))
                            }
                        }
                        output.flush()
                    }
                }

                // Verify SHA-256 checksum if provided
                val calculatedHash = digest.digest().joinToString("") { "%02x".format(it) }
                if (!apkAsset.sha256.isNullOrBlank()) {
                    val expectedHash = apkAsset.sha256.trim().lowercase()
                    if (!calculatedHash.equals(expectedHash, ignoreCase = true)) {
                        destinationFile.delete()
                        throw SecurityException("SHA-256 校验失败: 期望 $expectedHash, 实际 $calculatedHash")
                    }
                }

                if (lastPercent < 100 && totalBytes > 0) {
                    emit(DownloadProgress(bytesReadTotal, totalBytes, 100))
                }
            }
        } catch (e: Throwable) {
            destinationFile.delete()
            throw e
        }
    }.flowOn(Dispatchers.IO)
}
