#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DART_BIN="$REPO_ROOT/.tools/dart-sdk/bin/dart"

if [ ! -f "$DART_BIN" ]; then
  if command -v dart >/dev/null 2>&1; then
    DART_BIN="$(command -v dart)"
  else
    echo "ERROR: Dart executable not found at $DART_BIN or in PATH"
    exit 1
  fi
fi

echo "============================================================"
echo "  Hemory M5 Flutter / Client Unit Test Suite"
echo "  Dart SDK: $("$DART_BIN" --version)"
echo "============================================================"

echo ""
echo ">>> [1/4] Running Contract C5 5-Tier Priority Clipping Engine Tests..."
"$DART_BIN" "$REPO_ROOT/test/unit/clipping_engine_test.dart"

echo ""
echo ">>> [2/4] Running Contract C6 3-Tier Dynamic Speaker Fallback Tests..."
"$DART_BIN" "$REPO_ROOT/test/unit/speaker_fallback_test.dart"

echo ""
echo ">>> [3/4] Running Riverpod 7-State Business State Machine Tests (hf.i)..."
"$DART_BIN" "$REPO_ROOT/test/unit/state_machine_test.dart"

echo ""
echo ">>> [4/4] Running Native Bridge IPC and Drift Database Tests..."
"$DART_BIN" "$REPO_ROOT/test/unit/native_bridge_mock_test.dart"
"$DART_BIN" "$REPO_ROOT/test/unit/drift_database_test.dart"

echo ""
echo "============================================================"
echo "  ALL M5 CLIENT TESTS PASSED SUCCESSFULLY!"
echo "============================================================"
