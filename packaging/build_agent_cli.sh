#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${PPTLIB_BUILD_ROOT:-$ROOT/build}"

cd "$ROOT"
PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-agent-cache" uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$BUILD_ROOT/agent-runtime" \
  --workpath "$BUILD_ROOT/pyinstaller-agent" \
  "$ROOT/packaging/deckatlas.spec"

REPORT="$(
PPTLIB_HOME="$BUILD_ROOT/agent-smoke/home" \
PPTLIB_OUTPUT_ROOT="$BUILD_ROOT/agent-smoke/output" \
PPTLIB_LOG_DIR="$BUILD_ROOT/agent-smoke/logs" \
PPTLIB_TEMP_DIR="$BUILD_ROOT/agent-smoke/tmp" \
  "$BUILD_ROOT/agent-runtime/deckatlas/deckatlas" capabilities
)"
uv run python -c \
  'import json,sys; result=json.load(sys.stdin); assert result["ok"] and result["protocol"] == "deckatlas.agent/1.0"; assert "import" in result["result"]["commands"]' \
  <<<"$REPORT"
echo "Built Agent CLI: $BUILD_ROOT/agent-runtime/deckatlas"
