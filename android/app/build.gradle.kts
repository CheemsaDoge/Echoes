plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}
fun gitCommitCount(): Int = try {
    val process = ProcessBuilder("git", "rev-list", "--count", "HEAD")
        .directory(rootDir)
        .redirectErrorStream(true)
        .start()
    val output = process.inputStream.bufferedReader().use { it.readText().trim() }
    if (process.waitFor() == 0) output.toIntOrNull() ?: 44 else 44
} catch (_: Exception) {
    44
}

val versionMajor = 0
val versionMinor = 2
// Tag v0.2.0 (b6a4b2b) is at commit count 43; every commit after it increments patch by 1 (0.2.1, 0.2.2, 0.2.3...)
val v020BaseCommitCount = 43
val totalGitCommits = gitCommitCount()
val versionPatch = if (totalGitCommits >= v020BaseCommitCount) {
    totalGitCommits - v020BaseCommitCount
} else {
    totalGitCommits.coerceAtLeast(1)
}
val computedVersionCode = (project.findProperty("VERSION_CODE") as? String)?.toIntOrNull()
    ?: totalGitCommits.coerceAtLeast(1)
val computedVersionName = (project.findProperty("VERSION_NAME") as? String)?.takeIf { it.isNotBlank() }
    ?: "$versionMajor.$versionMinor.$versionPatch"

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
    val releaseKeystorePath = System.getenv("RELEASE_KEYSTORE_PATH")
        ?: System.getenv("KEYSTORE_PATH")
        ?: (project.findProperty("RELEASE_KEYSTORE_PATH") as? String)
        ?: (project.findProperty("KEYSTORE_PATH") as? String)

    val releaseSigning = if (!releaseKeystorePath.isNullOrBlank()) {
        val ks = file(releaseKeystorePath)
        if (!ks.exists()) {
            error("Configured release keystore file not found at: ${ks.absolutePath}")
        }
        signingConfigs.create("release") {
            storeFile = ks
            storePassword = System.getenv("RELEASE_KEYSTORE_PASSWORD")
                ?: System.getenv("KEYSTORE_PASSWORD")
                ?: (project.findProperty("RELEASE_KEYSTORE_PASSWORD") as? String)
                ?: (project.findProperty("KEYSTORE_PASSWORD") as? String)
                ?: "android"
            keyAlias = System.getenv("RELEASE_KEY_ALIAS")
                ?: System.getenv("KEYSTORE_ALIAS")
                ?: (project.findProperty("RELEASE_KEY_ALIAS") as? String)
                ?: (project.findProperty("KEYSTORE_ALIAS") as? String)
                ?: "release"
            keyPassword = System.getenv("RELEASE_KEY_PASSWORD")
                ?: System.getenv("KEY_PASSWORD")
                ?: (project.findProperty("RELEASE_KEY_PASSWORD") as? String)
                ?: (project.findProperty("KEY_PASSWORD") as? String)
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
