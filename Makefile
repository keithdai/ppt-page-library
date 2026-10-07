PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy

.PHONY: install lint typecheck test-unit test-integration check worker

install:
	python3.11 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(RUFF) check src tests

typecheck:
	$(MYPY) src/pptlib

test-unit:
	$(PYTEST) tests/unit -v

test-integration:
	$(PYTEST) tests/integration -v

check: lint typecheck test-unit test-integration

worker:
	.venv/bin/pptlib worker --once
