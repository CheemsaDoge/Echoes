pluginManagement {
    val flutterSdkPath = run {
        val properties = java.util.Properties()
        val localPropsFile = file("local.properties")
        if (localPropsFile.exists()) {
            localPropsFile.inputStream().use { properties.load(it) }
        }
        properties.getProperty("flutter.sdk")?.takeIf { it.isNotBlank() }
            ?: System.getenv("FLUTTER_ROOT")?.takeIf { it.isNotBlank() }
            ?: file("../.tools/flutter").takeIf { it.isDirectory }?.absolutePath
            ?: error(
                "Flutter SDK not found. Define flutter.sdk in android/local.properties or set FLUTTER_ROOT."
            )
    }
    settings.extra["flutterSdkPath"] = flutterSdkPath
    includeBuild("$flutterSdkPath/packages/flutter_tools/gradle")

    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "8.8.2" apply false
    id("org.jetbrains.kotlin.android") version "2.1.10" apply false
    id("org.jetbrains.kotlin.jvm") version "2.1.10" apply false
}

val resolvedFlutterSdkPath = settings.extra["flutterSdkPath"] as String
gradle.beforeProject {
    if (!hasProperty("flutter.sdk")) {
        extensions.extraProperties["flutter.sdk"] = resolvedFlutterSdkPath
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.PREFER_PROJECT)
    repositories {
        google()
        mavenCentral()
        maven { url = uri("https://storage.googleapis.com/download.flutter.io") }
    }
}

rootProject.name = "EchoLedger"
include(":app", ":core")
