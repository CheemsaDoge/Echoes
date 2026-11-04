package dev.echoledger.app.update

import java.io.File

/**
 * Semantic Versioning contract supporting standard SemVer 2.0.0 comparisons.
 */
data class SemVer(
    val major: Int,
    val minor: Int,
    val patch: Int,
    val preRelease: String? = null,
    val hotfix: Int = 0
) : Comparable<SemVer> {
    var hasFourSegments: Boolean = hotfix > 0

    override fun compareTo(other: SemVer): Int {
        if (major != other.major) return major.compareTo(other.major)
        if (minor != other.minor) return minor.compareTo(other.minor)
        if (patch != other.patch) return patch.compareTo(other.patch)
        if (hotfix != other.hotfix) return hotfix.compareTo(other.hotfix)

        return comparePreRelease(preRelease, other.preRelease)
    }

    private fun comparePreRelease(pre1: String?, pre2: String?): Int {
        if (pre1 == null && pre2 == null) return 0
        if (pre1 == null && pre2 != null) return 1
        if (pre1 != null && pre2 == null) return -1

        val parts1 = pre1!!.split('.')
        val parts2 = pre2!!.split('.')
        val minSize = minOf(parts1.size, parts2.size)

        for (i in 0 until minSize) {
            val p1 = parts1[i]
            val p2 = parts2[i]
            if (p1 == p2) continue

            val isDigits1 = p1.isNotEmpty() && p1.all { it.isDigit() }
            val isDigits2 = p2.isNotEmpty() && p2.all { it.isDigit() }

            return when {
                isDigits1 && isDigits2 -> java.math.BigInteger(p1).compareTo(java.math.BigInteger(p2))
                isDigits1 && !isDigits2 -> -1
                !isDigits1 && isDigits2 -> 1
                else -> p1.compareTo(p2)
            }
        }

        return parts1.size.compareTo(parts2.size)
    }

    override fun toString(): String {
        val base = if (hotfix > 0 || hasFourSegments) "$major.$minor.$patch.$hotfix" else "$major.$minor.$patch"
        return if (preRelease != null) "$base-$preRelease" else base
    }

    companion object {
        fun parse(version: String): SemVer? {
            val trimmed = version.trim()
            if (trimmed.isEmpty()) return null
            val stripped = if (trimmed.startsWith("v", ignoreCase = true)) {
                trimmed.substring(1).trim()
            } else {
                trimmed
            }
            if (stripped.isEmpty()) return null

            // Strip build metadata after '+'
            val withoutBuild = stripped.substringBefore('+')
            val dashIndex = withoutBuild.indexOf('-')
            val corePart = if (dashIndex >= 0) withoutBuild.substring(0, dashIndex) else withoutBuild
            val preRelease = if (dashIndex >= 0) withoutBuild.substring(dashIndex + 1).takeIf { it.isNotEmpty() } else null

            val parts = corePart.split('.')
            if (parts.size != 3 && parts.size != 4) return null

            val major = parts[0].toIntOrNull() ?: return null
            val minor = parts[1].toIntOrNull() ?: return null
            val patch = parts[2].toIntOrNull() ?: return null
            val hotfix = if (parts.size == 4) (parts[3].toIntOrNull() ?: return null) else 0

            if (major < 0 || minor < 0 || patch < 0 || hotfix < 0) return null

            return SemVer(major, minor, patch, preRelease, hotfix).apply {
                hasFourSegments = (parts.size == 4)
            }
        }
    }
}

/**
 * APK asset contract extracted from GitHub Release assets.
 */
data class ApkAsset(
    val name: String,
    val sizeBytes: Long,
    val downloadUrl: String,
    val sha256: String? = null
)

/**
 * Metadata for a downloadable GitHub Release.
 */
data class UpdateRelease(
    val versionName: String,
    val semVer: SemVer,
    val title: String,
    val changelog: String,
    val publishedAt: String,
    val apkAsset: ApkAsset
)

/**
 * Result returned by UpdateChecker queries.
 */
sealed interface UpdateResult {
    data class Available(val release: UpdateRelease) : UpdateResult
    data class UpToDate(val currentVersion: String) : UpdateResult
    data class Error(val message: String, val canRetry: Boolean = true) : UpdateResult
}

/**
 * Progress payload emitted during APK download streaming.
 */
data class DownloadProgress(
    val bytesRead: Long,
    val totalBytes: Long,
    val percent: Int
)

/**
 * High-level state representing the lifecycle of update checking and downloading.
 */
sealed interface UpdateState {
    data object Idle : UpdateState
    data object Checking : UpdateState
    data class UpToDate(val currentVersion: String, val checkedAtMs: Long = System.currentTimeMillis()) : UpdateState
    data class UpdateAvailable(val release: UpdateRelease) : UpdateState
    data class Downloading(val bytesRead: Long, val totalBytes: Long, val percent: Int) : UpdateState
    data class ReadyToInstall(val apkFile: File, val release: UpdateRelease? = null) : UpdateState
    data class Error(val message: String, val canRetry: Boolean = true) : UpdateState
}
