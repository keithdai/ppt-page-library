#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Run 'make install' first." >&2
  exit 1
fi

echo "== Repository boundaries and documentation =="
.venv/bin/python scripts/check_repository.py

echo "== Python lint and format =="
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check scripts

echo "== Python type safety =="
.venv/bin/mypy src/pptlib

echo "== Python tests =="
.venv/bin/pytest tests

echo "== Desktop tests =="
npm --prefix desktop test

echo "== Whitespace =="
git diff --check

echo "Quality gate passed."
