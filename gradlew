#!/bin/sh
set -e
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [ -d "$ROOT_DIR/.tools/jdk-17" ] && [ -z "${JAVA_HOME:-}" ]; then
  export JAVA_HOME="$ROOT_DIR/.tools/jdk-17"
  export PATH="$JAVA_HOME/bin:$PATH"
fi

if [ -z "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}" ] && [ ! -f "$ROOT_DIR/android/local.properties" ]; then
  if [ -d "$ROOT_DIR/.tools/android-sdk" ]; then
    export ANDROID_HOME="$ROOT_DIR/.tools/android-sdk"
  fi
fi

exec "$ROOT_DIR/android/gradlew" -p "$ROOT_DIR/android" "$@"
