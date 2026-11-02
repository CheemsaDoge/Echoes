#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mkdir -p artifacts
PYTHONPATH="$PWD/backend${PYTHONPATH:+:$PYTHONPATH}" python -m pytest backend/tests -q --junitxml=artifacts/backend-tests.xml
if command -v kotlinc >/dev/null 2>&1; then
  kotlinc android/core/src/main/kotlin/dev/echoledger/core/*.kt scripts/CoreSmoke.kt -include-runtime -d artifacts/core-smoke.jar
  java -jar artifacts/core-smoke.jar
else
  echo 'Kotlin smoke tests skipped: kotlinc is not installed.'
fi
