#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if ! command -v gradle >/dev/null 2>&1; then
  echo 'Install Gradle 8.10.2 and JDK 17, or use the included Android CI workflow.' >&2; exit 2
fi
if [ -z "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}" ] && [ ! -f android/local.properties ]; then
  echo 'Set ANDROID_HOME or android/local.properties to an Android SDK with API 35.' >&2; exit 2
fi
exec gradle -p android --no-daemon :core:test :app:assembleDebug :app:lintDebug
