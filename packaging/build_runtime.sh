#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"
PYINSTALLER_CONFIG_DIR="$ROOT/build/pyinstaller-cache" uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$ROOT/build/runtime" \
  --workpath "$ROOT/build/pyinstaller" \
  "$ROOT/packaging/pptlib.spec"

REPORT="$(
PPTLIB_HOME="$ROOT/build/runtime-smoke/home" \
PPTLIB_OUTPUT_ROOT="$ROOT/build/runtime-smoke/output" \
PPTLIB_LOG_DIR="$ROOT/build/runtime-smoke/logs" \
PPTLIB_TEMP_DIR="$ROOT/build/runtime-smoke/tmp" \
  "$ROOT/build/runtime/pptlib/pptlib" doctor
)"
uv run python -c \
  'import json,sys; report=json.load(sys.stdin); assert report["ready_for_rendering"], report' \
  <<<"$REPORT"
echo "Built Python runtime: $ROOT/build/runtime/pptlib"
