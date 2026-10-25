#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${PPTLIB_BUILD_ROOT:-$ROOT/build}"

cd "$ROOT"
PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-cache" uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$BUILD_ROOT/runtime" \
  --workpath "$BUILD_ROOT/pyinstaller" \
  "$ROOT/packaging/pptlib.spec"

REPORT="$(
PPTLIB_HOME="$BUILD_ROOT/runtime-smoke/home" \
PPTLIB_OUTPUT_ROOT="$BUILD_ROOT/runtime-smoke/output" \
PPTLIB_LOG_DIR="$BUILD_ROOT/runtime-smoke/logs" \
PPTLIB_TEMP_DIR="$BUILD_ROOT/runtime-smoke/tmp" \
  "$BUILD_ROOT/runtime/pptlib/pptlib" doctor
)"
uv run python -c \
  'import json,sys; report=json.load(sys.stdin); assert report["ready_for_rendering"], report' \
  <<<"$REPORT"
echo "Built Python runtime: $BUILD_ROOT/runtime/pptlib"
