PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy

.PHONY: install repo-check format-check lint typecheck test-unit test-integration test-desktop \
	check quality worker agent-runtime

install:
	python3.11 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

repo-check:
	$(PYTHON) scripts/check_repository.py

format-check:
	$(RUFF) format --check scripts

lint:
	$(RUFF) check src tests scripts

typecheck:
	$(MYPY) src/pptlib

test-unit:
	$(PYTEST) tests/unit -v

test-integration:
	$(PYTEST) tests/integration -v

test-desktop:
	npm --prefix desktop test

check: repo-check format-check lint typecheck test-unit test-integration test-desktop

quality:
	bash scripts/quality-gate.sh

worker:
	.venv/bin/pptlib worker --once

agent-runtime:
	./packaging/build_agent_cli.sh
