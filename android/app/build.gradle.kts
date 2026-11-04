plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}
fun gitExecOutput(vararg args: String): String? = try {
    val execProvider = providers.exec {
        workingDir = rootDir
        isIgnoreExitValue = true
        commandLine(*args)
    }
    if (execProvider.result.get().exitValue == 0) {
        execProvider.standardOutput.asText.get().trim()
    } else {
        null
    }
} catch (_: Exception) {
    null
}

val versionMajor = 0
val versionMinor = 2
// Tag v0.2.0 (b6a4b2b) is at commit count 43; every commit after it increments patch by 1 (0.2.1, 0.2.2, 0.2.3...)
val v020BaseCommitCount = 43
val overrideVersionCode = project.findProperty("VERSION_CODE")?.toString()?.let { raw ->
    val parsed = raw.trim().toIntOrNull()
    if (parsed == null || parsed < 1) {
        error("Invalid VERSION_CODE override '$raw': must be a positive integer (>= 1).")
    }
    parsed
}
val overrideVersionName = project.findProperty("VERSION_NAME")?.toString()?.let { raw ->
    val trimmed = raw.trim()
    if (trimmed.isEmpty()) {
        error("Invalid VERSION_NAME override: must not be blank.")
    }
    trimmed
}
val needsGitVersion = overrideVersionCode == null || overrideVersionName == null
val totalGitCommits = if (needsGitVersion) {
    gitExecOutput("git", "rev-list", "--count", "HEAD")?.toIntOrNull()
} else {
    null
}
val isShallowRepo = if (needsGitVersion) {
    gitExecOutput("git", "rev-parse", "--is-shallow-repository") == "true"
} else {
    false
}

if (isShallowRepo) {
    error(
        "Shallow git repository detected (commit count=${totalGitCommits ?: "unknown"}). " +
            "Fetch full git history (e.g. 'git fetch --unshallow' or 'fetch-depth: 0') " +
            "or provide explicit -PVERSION_NAME and -PVERSION_CODE overrides."
    )
}

val effectiveCommitCount = (totalGitCommits ?: (v020BaseCommitCount + 1)).coerceAtLeast(v020BaseCommitCount + 1)
val versionPatch = effectiveCommitCount - v020BaseCommitCount
val computedVersionCode = overrideVersionCode ?: effectiveCommitCount
val computedVersionName = overrideVersionName ?: "$versionMajor.$versionMinor.$versionPatch"

fun nonBlankEnvOrProp(vararg keys: String): String? {
    for (key in keys) {
        val envVal = System.getenv(key)?.takeIf { it.isNotBlank() }
        if (envVal != null) return envVal
        val propVal = (project.findProperty(key) as? String)?.takeIf { it.isNotBlank() }
        if (propVal != null) return propVal
    }
    return null
}

android {
    namespace = "dev.echoledger.app"
    compileSdk = 35
    defaultConfig {
        applicationId = "dev.echoledger.app"
        minSdk = 26
        targetSdk = 35
        versionCode = computedVersionCode
        versionName = computedVersionName
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    val releaseKeystorePath = nonBlankEnvOrProp("RELEASE_KEYSTORE_PATH", "KEYSTORE_PATH")

    val releaseSigning = if (releaseKeystorePath != null) {
        val ks = file(releaseKeystorePath)
        if (!ks.exists()) {
            error("Configured release keystore file not found at: ${ks.absolutePath}")
        }
        signingConfigs.create("release") {
            storeFile = ks
            storePassword = nonBlankEnvOrProp("RELEASE_KEYSTORE_PASSWORD", "KEYSTORE_PASSWORD")
                ?: "android"
            keyAlias = nonBlankEnvOrProp("RELEASE_KEY_ALIAS", "KEYSTORE_ALIAS")
                ?: "release"
            keyPassword = nonBlankEnvOrProp("RELEASE_KEY_PASSWORD", "KEY_PASSWORD")
                ?: storePassword
        }
    } else {
        // Fall back cleanly to debug signing config so AGP can auto-generate debug.keystore on fresh environments
        signingConfigs.getByName("debug")
    }

    buildTypes {
        debug { applicationIdSuffix = ".debug" }
        release {
            isMinifyEnabled = false
            signingConfig = releaseSigning
        }
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}
ksp { arg("room.schemaLocation", "$projectDir/schemas") }
dependencies {
    implementation(project(":core"))
    val composeBom = platform("androidx.compose:compose-bom:2025.02.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.activity:activity-compose:1.10.0")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.room:room-runtime:2.7.0")
    implementation("androidx.room:room-ktx:2.7.0")
    ksp("androidx.room:room-compiler:2.7.0")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.1")
}
