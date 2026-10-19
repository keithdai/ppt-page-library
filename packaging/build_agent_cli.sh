#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"
PYINSTALLER_CONFIG_DIR="$ROOT/build/pyinstaller-agent-cache" uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$ROOT/build/agent-runtime" \
  --workpath "$ROOT/build/pyinstaller-agent" \
  "$ROOT/packaging/deckatlas.spec"

REPORT="$(
PPTLIB_HOME="$ROOT/build/agent-smoke/home" \
PPTLIB_OUTPUT_ROOT="$ROOT/build/agent-smoke/output" \
PPTLIB_LOG_DIR="$ROOT/build/agent-smoke/logs" \
PPTLIB_TEMP_DIR="$ROOT/build/agent-smoke/tmp" \
  "$ROOT/build/agent-runtime/deckatlas/deckatlas" capabilities
)"
uv run python -c \
  'import json,sys; result=json.load(sys.stdin); assert result["protocol"] == "deckatlas.agent/1.0"' \
  <<<"$REPORT"
echo "Built Agent CLI: $ROOT/build/agent-runtime/deckatlas"
