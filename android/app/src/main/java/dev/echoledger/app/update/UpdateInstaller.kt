package dev.echoledger.app.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import java.io.File

object UpdateInstaller {

    sealed interface InstallResult {
        data object Launched : InstallResult
        data class PermissionRequired(val intent: Intent) : InstallResult
        data class Error(val message: String) : InstallResult
    }

    /**
     * Checks if the application currently has permission to install unknown packages.
     * On Android 8.0+ (API 26+), this queries PackageManager.canRequestPackageInstalls().
     */
    fun canRequestPackageInstalls(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.packageManager.canRequestPackageInstalls()
        } else {
            true
        }
    }

    /**
     * Builds an Intent directing the user to the system settings page for managing
     * unknown app source installations for EchoLedger.
     */
    fun createManageUnknownAppSourcesIntent(context: Context): Intent {
        return Intent(
            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:${context.packageName}")
        ).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
    }

    /**
     * Creates an Intent to install the given APK file via Android's package installer,
     * resolving the APK path through FileProvider.
     */
    fun createInstallIntent(context: Context, apkFile: File): Intent {
        val authority = "${context.packageName}.fileprovider"
        val contentUri = FileProvider.getUriForFile(context, authority, apkFile)
        return Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(contentUri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
    }

    /**
     * Checks prerequisites, requests unknown app source permission if required,
     * or launches the system package installer.
     */
    fun install(context: Context, apkFile: File): InstallResult {
        if (!apkFile.exists() || apkFile.length() <= 0L) {
            return InstallResult.Error("安装包文件不存在或为空: ${apkFile.absolutePath}")
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !canRequestPackageInstalls(context)) {
            val permissionIntent = createManageUnknownAppSourcesIntent(context)
            try {
                context.startActivity(permissionIntent)
            } catch (_: Exception) {
                // Return PermissionRequired regardless of whether direct launch succeeded
            }
            return InstallResult.PermissionRequired(permissionIntent)
        }

        return try {
            val installIntent = createInstallIntent(context, apkFile)
            context.startActivity(installIntent)
            InstallResult.Launched
        } catch (e: Exception) {
            InstallResult.Error("启动应用安装器失败: ${e.message ?: e.javaClass.simpleName}")
        }
    }
}
