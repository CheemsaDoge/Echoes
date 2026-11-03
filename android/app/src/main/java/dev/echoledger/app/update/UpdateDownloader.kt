package dev.echoledger.app.update

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.launch
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
        return file.isFile && file.length() > 0
    }

    fun clearUpdates(): Boolean {
        if (!downloadDir.exists()) return true
        val files = downloadDir.listFiles() ?: return true
        var allSuccess = true
        for (f in files) {
            if (!f.delete()) allSuccess = false
        }
        return allSuccess
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
        val tempFile = File(downloadDir, "update_${version}.apk.tmp.${System.nanoTime()}")
        if (tempFile.exists()) {
            tempFile.delete()
        }

        val request = Request.Builder()
            .url(apkAsset.downloadUrl)
            .build()

        val call = client.newCall(request)

        coroutineScope {
            val watcher = launch(Dispatchers.Default) {
                try {
                    awaitCancellation()
                } finally {
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
                        FileOutputStream(tempFile).use { output ->
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

                    // Verify SHA-256 checksum on downloaded file before publishing
                    val calculatedHash = digest.digest().joinToString("") { "%02x".format(it) }
                    if (!apkAsset.sha256.isNullOrBlank()) {
                        val expectedHash = apkAsset.sha256.trim().lowercase()
                        if (!calculatedHash.equals(expectedHash, ignoreCase = true)) {
                            tempFile.delete()
                            destinationFile.delete()
                            throw SecurityException("SHA-256 校验失败: 期望 $expectedHash, 实际 $calculatedHash")
                        }
                    }

                    // Publish atomically only after full verification
                    var published = false
                    try {
                        if (destinationFile.exists()) {
                            destinationFile.delete()
                        }
                        if (!tempFile.renameTo(destinationFile)) {
                            tempFile.copyTo(destinationFile, overwrite = true)
                            tempFile.delete()
                        }
                        published = true
                    } catch (e: Throwable) {
                        destinationFile.delete()
                        throw e
                    }

                    if (lastPercent < 100 && totalBytes > 0) {
                        emit(DownloadProgress(bytesReadTotal, totalBytes, 100))
                    }
                }
            } catch (e: Throwable) {
                tempFile.delete()
                destinationFile.delete()
                throw e
            } finally {
                watcher.cancel()
                if (tempFile.exists()) {
                    tempFile.delete()
                }
            }
        }
    }.flowOn(Dispatchers.IO)
}
