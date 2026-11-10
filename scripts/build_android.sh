#!/bin/sh
set -eu
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -d "$ROOT_DIR/.tools/jdk-17" ]; then
  export JAVA_HOME="$ROOT_DIR/.tools/jdk-17"
  export PATH="$JAVA_HOME/bin:$PATH"
fi

if [ -z "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}" ]; then
  if [ -d "$ROOT_DIR/.tools/android-sdk" ]; then
    export ANDROID_HOME="$ROOT_DIR/.tools/android-sdk"
    export ANDROID_SDK_ROOT="$ROOT_DIR/.tools/android-sdk"
  fi
fi

if [ -z "${FLUTTER_ROOT:-}" ] && [ -d "$ROOT_DIR/.tools/flutter" ]; then
  export FLUTTER_ROOT="$ROOT_DIR/.tools/flutter"
  export PATH="$FLUTTER_ROOT/bin:$PATH"
fi

if command -v flutter >/dev/null 2>&1; then
  flutter pub get
fi

if ! command -v gradle >/dev/null 2>&1; then
  if [ -x "$ROOT_DIR/.tools/gradle-8.10.2/bin/gradle" ]; then
    export PATH="$ROOT_DIR/.tools/gradle-8.10.2/bin:$PATH"
  fi
fi

if ! command -v gradle >/dev/null 2>&1; then
  echo 'Install Gradle 8.10.2 and JDK 17, or use the included Android CI workflow.' >&2; exit 2
fi

if [ -z "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}" ] && [ ! -f android/local.properties ]; then
  echo 'Set ANDROID_HOME or android/local.properties to an Android SDK with API 35.' >&2; exit 2
fi

exec gradle -p android --no-daemon -Porg.gradle.java.installations.paths="${JAVA_HOME:-}" :core:test :app:testDebugUnitTest :app:assembleDebug :app:lintDebug
