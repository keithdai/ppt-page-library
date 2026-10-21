# PPT Page Library M0.0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a locally runnable macOS foundation with configuration, domain primitives, SQLite migrations, a leased background job queue, FastAPI health/doctor endpoints, a server-rendered page shell, CLI commands, and a repeatable verification gate.

**Architecture:** One Python package exposes browser, CLI, and worker entry points. FastAPI handles only short requests; the worker leases durable SQLite jobs. Domain code stays independent from FastAPI and sqlite3, while infrastructure modules own configuration, migrations, database access, and environment checks.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, Jinja2, sqlite3/FTS5, pytest, HTTPX, Ruff, mypy.

## Global Constraints

- Target macOS 13 or newer; support Intel and Apple Silicon Python environments.
- Runtime must not require Node.js, Docker, Redis, Celery, PostgreSQL, or a CDN.
- Source PPT files are always read-only.
- Long work must run outside the Web request process.
- Database transactions must not wrap long file operations.
- All JSON responses use the shared ok/data/request_id or ok/error/request_id envelope.
- All new behavior follows red-green-refactor TDD.
- The current directory is not a Git repository; Task 1 initializes Git before the first commit.

---

## Delivery Sequence

This foundation is the first of five independently testable plans:

1. M0.0 Foundation — this plan.
2. M0.1 OOXML risk spike — package graph, transplant plan, support matrix, golden validation.
3. M0.2 Ingestion and search — source roots, stage ledger, artifacts, rendering, FTS, library UI.
4. M0.3 Selection and export — revisions, preflight, cross-file export, evidence, manifest.
5. M0.4 Reliability and release — reconcile, failure injection, E2E, performance and packaging notes.

Do not start plans 3–5 until the M0.1 spike confirms that package-level copying remains viable.

---

### Task 1: Package, configuration, and repository baseline

**Files:**
- Create: .gitignore
- Create: pyproject.toml
- Create: README.md
- Create: src/pptlib/default.toml
- Create: src/pptlib/__init__.py
- Create: src/pptlib/config.py
- Create: tests/unit/test_config.py

**Interfaces:**
- Produces: pptlib.config.Settings
- Produces: pptlib.config.load_settings(env: Mapping[str, str] | None = None) -> Settings
- Produces: pptlib.__version__: str

- [ ] **Step 1: Initialize Git and write the failing configuration test**

Run:

```bash
git init
mkdir -p config src/pptlib tests/unit
```

Create tests/unit/test_config.py:

```python
from pathlib import Path

from pptlib.config import load_settings


def test_load_settings_uses_local_paths_and_environment_override(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_PORT": "9321",
            "PPTLIB_MAX_WORKERS": "1",
        }
    )

    assert settings.home == (tmp_path / "home").resolve()
    assert settings.database_path == settings.home / "pages.db"
    assert settings.assets_dir == settings.home / "assets"
    assert settings.port == 9321
    assert settings.max_workers == 1
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install pytest
PYTHONPATH=src .venv/bin/pytest tests/unit/test_config.py -v
```

Expected: collection fails with ModuleNotFoundError for pptlib.config.

- [ ] **Step 3: Add packaging and the minimal configuration implementation**

Create pyproject.toml:

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "ppt-page-library"
version = "0.1.0"
description = "Local macOS PPT page index and composition tool"
readme = "README.md"
requires-python = ">=3.11,<3.14"
dependencies = [
  "fastapi>=0.115,<1",
  "uvicorn>=0.30,<1",
  "jinja2>=3.1,<4",
]

[project.optional-dependencies]
dev = [
  "httpx>=0.27,<1",
  "mypy>=1.11,<2",
  "pytest>=8,<9",
  "pytest-cov>=5,<7",
  "pytest-timeout>=2.3,<3",
  "ruff>=0.6,<1",
]

[project.scripts]
pptlib = "pptlib.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
packages = ["pptlib"]
```

Create README.md:

```markdown
# PPT Page Library
```

Create src/pptlib/default.toml:

```toml
host = "127.0.0.1"
port = 8765
max_workers = 1
job_lease_seconds = 120
thumbnail_long_edge = 640
preview_long_edge = 1440
max_file_bytes = 524288000
max_uncompressed_package_bytes = 2147483648
max_parts_per_package = 20000
analyzer_version = "cjk-bigram-v1"
```

Create src/pptlib/__init__.py:

```python
__version__ = "0.1.0"
```

Create src/pptlib/config.py:

```python
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Settings:
    home: Path
    database_path: Path
    assets_dir: Path
    temp_dir: Path
    log_dir: Path
    output_root: Path
    host: str
    port: int
    max_workers: int
    job_lease_seconds: int
    thumbnail_long_edge: int
    preview_long_edge: int
    max_file_bytes: int
    max_uncompressed_package_bytes: int
    max_parts_per_package: int
    analyzer_version: str


def _default_home() -> Path:
    return Path.home() / "Library" / "Application Support" / "PPT Page Library"


def _read_defaults() -> dict[str, object]:
    with (Path(__file__).resolve().parent / "default.toml").open("rb") as handle:
        return tomllib.load(handle)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    values = os.environ if env is None else env
    defaults = _read_defaults()
    home = Path(values.get("PPTLIB_HOME", str(_default_home()))).expanduser().resolve()
    output_root = Path(
        values.get(
            "PPTLIB_OUTPUT_ROOT",
            str(Path.home() / "Documents" / "PPT Page Library Exports"),
        )
    ).expanduser().resolve()
    log_dir = Path(
        values.get(
            "PPTLIB_LOG_DIR",
            str(Path.home() / "Library" / "Logs" / "PPT Page Library"),
        )
    ).expanduser().resolve()

    return Settings(
        home=home,
        database_path=home / "pages.db",
        assets_dir=home / "assets",
        temp_dir=home / "tmp",
        log_dir=log_dir,
        output_root=output_root,
        host=values.get("PPTLIB_HOST", str(defaults["host"])),
        port=int(values.get("PPTLIB_PORT", str(defaults["port"]))),
        max_workers=int(values.get("PPTLIB_MAX_WORKERS", str(defaults["max_workers"]))),
        job_lease_seconds=int(defaults["job_lease_seconds"]),
        thumbnail_long_edge=int(defaults["thumbnail_long_edge"]),
        preview_long_edge=int(defaults["preview_long_edge"]),
        max_file_bytes=int(defaults["max_file_bytes"]),
        max_uncompressed_package_bytes=int(defaults["max_uncompressed_package_bytes"]),
        max_parts_per_package=int(defaults["max_parts_per_package"]),
        analyzer_version=str(defaults["analyzer_version"]),
    )
```

Create .gitignore:

```gitignore
.DS_Store
.venv/
.mypy_cache/
.pytest_cache/
.ruff_cache/
__pycache__/
*.py[cod]
*.egg-info/
.coverage
htmlcov/
var/*
!var/.gitkeep
```

- [ ] **Step 4: Install the package and verify the test passes**

Run:

```bash
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/pytest tests/unit/test_config.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit the baseline**

Run:

```bash
git add .gitignore README.md pyproject.toml src/pptlib tests/unit/test_config.py
git commit -m "chore: initialize lightweight Python application"
```

Expected: root commit succeeds.

---

### Task 2: Domain IDs, errors, and state transitions

**Files:**
- Create: src/pptlib/domain/__init__.py
- Create: src/pptlib/domain/ids.py
- Create: src/pptlib/domain/errors.py
- Create: src/pptlib/domain/states.py
- Create: tests/unit/domain/test_ids.py
- Create: tests/unit/domain/test_states.py

**Interfaces:**
- Produces: new_id(prefix: str) -> str
- Produces: AppError(code: ErrorCode, message: str, retryable: bool, details: Mapping[str, object])
- Produces: assert_job_transition(current: JobStatus, target: JobStatus) -> None

- [ ] **Step 1: Write failing tests for stable IDs and legal transitions**

Create tests/unit/domain/test_ids.py:

```python
from pptlib.domain.ids import new_id


def test_new_id_has_prefix_and_is_unique() -> None:
    first = new_id("job")
    second = new_id("job")

    assert first.startswith("job_")
    assert second.startswith("job_")
    assert first != second
```

Create tests/unit/domain/test_states.py:

```python
import pytest

from pptlib.domain.errors import AppError, ErrorCode
from pptlib.domain.states import JobStatus, assert_job_transition


def test_job_can_move_from_queued_to_leased() -> None:
    assert_job_transition(JobStatus.QUEUED, JobStatus.LEASED)


def test_job_cannot_move_from_succeeded_back_to_running() -> None:
    with pytest.raises(AppError) as raised:
        assert_job_transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)

    assert raised.value.code is ErrorCode.INVALID_STATE_TRANSITION
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/pytest tests/unit/domain -v
```

Expected: collection fails because pptlib.domain does not exist.

- [ ] **Step 3: Implement IDs, typed errors, and the job transition table**

Create src/pptlib/domain/__init__.py:

```python
"""Domain primitives with no framework or database dependencies."""
```

Create src/pptlib/domain/ids.py:

```python
from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    if not prefix or not prefix.isascii() or not prefix.replace("_", "").isalnum():
        raise ValueError("prefix must contain ASCII letters, numbers, or underscores")
    return f"{prefix}_{uuid.uuid4().hex}"
```

Create src/pptlib/domain/errors.py:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class ErrorCode(StrEnum):
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    DATABASE_BUSY = "DATABASE_BUSY"
    LIBREOFFICE_NOT_FOUND = "LIBREOFFICE_NOT_FOUND"
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    NOT_FOUND = "NOT_FOUND"
    REQUEST_INVALID = "REQUEST_INVALID"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(slots=True)
class AppError(Exception):
    code: ErrorCode
    message: str
    retryable: bool = False
    details: Mapping[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message
```

Create src/pptlib/domain/states.py:

```python
from __future__ import annotations

from enum import StrEnum

from pptlib.domain.errors import AppError, ErrorCode


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.LEASED, JobStatus.CANCELLED}),
    JobStatus.LEASED: frozenset({JobStatus.RUNNING, JobStatus.ABANDONED}),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.SUCCEEDED,
            JobStatus.RETRY_WAIT,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.ABANDONED,
        }
    ),
    JobStatus.RETRY_WAIT: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.ABANDONED: frozenset({JobStatus.QUEUED, JobStatus.FAILED}),
}


def assert_job_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in _JOB_TRANSITIONS[current]:
        raise AppError(
            code=ErrorCode.INVALID_STATE_TRANSITION,
            message=f"job cannot move from {current.value} to {target.value}",
            details={"current": current.value, "target": target.value},
        )
```

- [ ] **Step 4: Run tests and static checks**

Run:

```bash
.venv/bin/pytest tests/unit/domain -v
.venv/bin/ruff check src/pptlib/domain tests/unit/domain
.venv/bin/mypy src/pptlib/domain
```

Expected: all tests pass; Ruff and mypy report no errors.

- [ ] **Step 5: Commit domain primitives**

Run:

```bash
git add src/pptlib/domain tests/unit/domain
git commit -m "feat: add domain ids errors and job states"
```

---

### Task 3: SQLite connection and migration runner

**Files:**
- Create: src/pptlib/migrations/0001_foundation.sql
- Create: src/pptlib/infrastructure/__init__.py
- Create: src/pptlib/infrastructure/db/__init__.py
- Create: src/pptlib/infrastructure/db/connection.py
- Create: src/pptlib/infrastructure/db/migrations.py
- Create: tests/integration/db/test_migrations.py

**Interfaces:**
- Consumes: Settings.database_path
- Produces: connect(database_path: Path) -> sqlite3.Connection
- Produces: migrate(connection: sqlite3.Connection, migrations_dir: Path) -> list[str]

- [ ] **Step 1: Write a failing migration test**

Create tests/integration/db/test_migrations.py:

```python
from pathlib import Path

from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.migrations import migrate


def test_migrate_creates_foundation_schema_and_is_idempotent(tmp_path: Path) -> None:
    connection = connect(tmp_path / "pages.db")
    migrations_dir = Path(__file__).resolve().parents[3] / "src" / "pptlib" / "migrations"

    first = migrate(connection, migrations_dir)
    second = migrate(connection, migrations_dir)
    table_names = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    assert first == ["0001_foundation.sql"]
    assert second == []
    assert {"schema_migrations", "jobs", "job_stages"}.issubset(table_names)
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
.venv/bin/pytest tests/integration/db/test_migrations.py -v
```

Expected: collection fails because pptlib.infrastructure.db does not exist.

- [ ] **Step 3: Add the foundation migration**

Create src/pptlib/migrations/0001_foundation.sql:

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'queued', 'leased', 'running', 'retry_wait',
            'succeeded', 'failed', 'cancelled', 'abandoned'
        )
    ),
    priority INTEGER NOT NULL DEFAULT 100,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner TEXT,
    lease_expires_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(kind, idempotency_key)
);

CREATE INDEX idx_jobs_claim
ON jobs(status, available_at, priority, created_at);

CREATE TABLE job_stages (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    checkpoint_json TEXT,
    output_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY(job_id, stage_name)
);
```

- [ ] **Step 4: Implement connection policy and migration runner**

Create src/pptlib/infrastructure/__init__.py:

```python
"""Adapters for databases, files, and local applications."""
```

Create src/pptlib/infrastructure/db/__init__.py:

```python
"""SQLite infrastructure."""
```

Create src/pptlib/infrastructure/db/connection.py:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection
```

Create src/pptlib/infrastructure/db/migrations.py:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def migrate(connection: sqlite3.Connection, migrations_dir: Path) -> list[str]:
    _ensure_migration_table(connection)
    applied = {
        row["name"]
        for row in connection.execute("SELECT name FROM schema_migrations").fetchall()
    }
    completed: list[str] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in applied:
            continue
        script = path.read_text(encoding="utf-8")
        escaped_name = path.name.replace("'", "''")
        transactional_script = (
            "BEGIN IMMEDIATE;\n"
            f"{script}\n"
            "INSERT INTO schema_migrations(name, applied_at) "
            f"VALUES ('{escaped_name}', datetime('now'));\n"
            "COMMIT;"
        )
        try:
            connection.executescript(transactional_script)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        completed.append(path.name)
    return completed
```

- [ ] **Step 5: Run migration and database tests**

Run:

```bash
.venv/bin/pytest tests/integration/db/test_migrations.py -v
.venv/bin/ruff check src/pptlib/infrastructure tests/integration/db
.venv/bin/mypy src/pptlib/infrastructure
```

Expected: test passes; Ruff and mypy report no errors.

- [ ] **Step 6: Commit database foundation**

Run:

```bash
git add src/pptlib/migrations src/pptlib/infrastructure tests/integration/db
git commit -m "feat: add SQLite migration foundation"
```

---

### Task 4: Durable leased job queue

**Files:**
- Create: src/pptlib/worker/__init__.py
- Create: src/pptlib/worker/queue.py
- Create: src/pptlib/worker/lease.py
- Create: tests/integration/worker/test_queue.py

**Interfaces:**
- Consumes: sqlite3.Connection
- Produces: enqueue(connection, kind, idempotency_key, target_type=None, target_id=None) -> str
- Produces: claim_next(connection, worker_id, lease_seconds, now) -> JobLease | None
- Produces: start_job(connection, lease: JobLease) -> None
- Produces: finish_job(connection, lease: JobLease) -> None

- [ ] **Step 1: Write failing idempotency and lease tests**

Create tests/integration/worker/test_queue.py:

```python
from datetime import UTC, datetime
from pathlib import Path

from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.migrations import migrate
from pptlib.worker.queue import claim_next, enqueue, finish_job, start_job


def prepared_connection(tmp_path: Path):
    connection = connect(tmp_path / "pages.db")
    migrate(
        connection,
        Path(__file__).resolve().parents[3] / "src" / "pptlib" / "migrations",
    )
    return connection


def test_enqueue_is_idempotent_for_kind_and_key(tmp_path: Path) -> None:
    connection = prepared_connection(tmp_path)

    first = enqueue(connection, "doctor", "startup")
    second = enqueue(connection, "doctor", "startup")

    assert first == second
    assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_worker_claims_and_finishes_one_job(tmp_path: Path) -> None:
    connection = prepared_connection(tmp_path)
    job_id = enqueue(connection, "doctor", "startup")

    lease = claim_next(
        connection,
        worker_id="worker-test",
        lease_seconds=120,
        now=datetime(2026, 7, 16, tzinfo=UTC),
    )

    assert lease is not None
    assert lease.job_id == job_id
    start_job(connection, lease)
    finish_job(connection, lease)
    status = connection.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    assert status == "succeeded"
```

- [ ] **Step 2: Run tests and verify the missing queue failure**

Run:

```bash
.venv/bin/pytest tests/integration/worker/test_queue.py -v
```

Expected: collection fails because pptlib.worker.queue does not exist.

- [ ] **Step 3: Implement the lease value object**

Create src/pptlib/worker/__init__.py:

```python
"""Durable local worker."""
```

Create src/pptlib/worker/lease.py:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: str
    kind: str
    worker_id: str
    expires_at: datetime
```

- [ ] **Step 4: Implement idempotent enqueue and atomic claim**

Create src/pptlib/worker/queue.py:

```python
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from pptlib.domain.ids import new_id
from pptlib.worker.lease import JobLease


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def enqueue(
    connection: sqlite3.Connection,
    kind: str,
    idempotency_key: str,
    target_type: str | None = None,
    target_id: str | None = None,
) -> str:
    now = _iso(datetime.now(UTC))
    job_id = new_id("job")
    connection.execute(
        """
        INSERT INTO jobs(
            id, kind, target_type, target_id, idempotency_key,
            status, created_at, available_at
        )
        VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
        ON CONFLICT(kind, idempotency_key) DO NOTHING
        """,
        (job_id, kind, target_type, target_id, idempotency_key, now, now),
    )
    row = connection.execute(
        "SELECT id FROM jobs WHERE kind = ? AND idempotency_key = ?",
        (kind, idempotency_key),
    ).fetchone()
    if row is None:
        raise RuntimeError("job insert did not produce a row")
    return str(row["id"])


def claim_next(
    connection: sqlite3.Connection,
    worker_id: str,
    lease_seconds: int,
    now: datetime,
) -> JobLease | None:
    expires_at = now + timedelta(seconds=lease_seconds)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """
            SELECT id, kind
            FROM jobs
            WHERE status = 'queued' AND available_at <= ?
            ORDER BY priority ASC, created_at ASC
            LIMIT 1
            """,
            (_iso(now),),
        ).fetchone()
        if row is None:
            connection.execute("COMMIT")
            return None
        changed = connection.execute(
            """
            UPDATE jobs
            SET status = 'leased', lease_owner = ?, lease_expires_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (worker_id, _iso(expires_at), row["id"]),
        ).rowcount
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    if changed != 1:
        return None
    return JobLease(
        job_id=str(row["id"]),
        kind=str(row["kind"]),
        worker_id=worker_id,
        expires_at=expires_at,
    )


def start_job(connection: sqlite3.Connection, lease: JobLease) -> None:
    changed = connection.execute(
        """
        UPDATE jobs
        SET status = 'running', started_at = COALESCE(started_at, datetime('now'))
        WHERE id = ? AND status = 'leased' AND lease_owner = ?
        """,
        (lease.job_id, lease.worker_id),
    ).rowcount
    if changed != 1:
        raise RuntimeError("job lease is no longer valid")


def finish_job(connection: sqlite3.Connection, lease: JobLease) -> None:
    changed = connection.execute(
        """
        UPDATE jobs
        SET status = 'succeeded', finished_at = datetime('now'),
            lease_owner = NULL, lease_expires_at = NULL
        WHERE id = ? AND status = 'running' AND lease_owner = ?
        """,
        (lease.job_id, lease.worker_id),
    ).rowcount
    if changed != 1:
        raise RuntimeError("running job is no longer owned by worker")
```

- [ ] **Step 5: Run queue tests and full regression**

Run:

```bash
.venv/bin/pytest tests/integration/worker/test_queue.py -v
.venv/bin/pytest tests/unit tests/integration -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit the durable queue**

Run:

```bash
git add src/pptlib/worker tests/integration/worker
git commit -m "feat: add idempotent leased job queue"
```

---

### Task 5: Environment doctor and JSON health contract

**Files:**
- Create: src/pptlib/application/__init__.py
- Create: src/pptlib/application/doctor.py
- Create: src/pptlib/web/__init__.py
- Create: src/pptlib/web/app.py
- Create: src/pptlib/web/errors.py
- Create: src/pptlib/web/routes/__init__.py
- Create: src/pptlib/web/routes/api.py
- Create: tests/contract/web/test_health.py
- Create: tests/unit/application/test_doctor.py

**Interfaces:**
- Consumes: Settings
- Produces: run_doctor(settings: Settings) -> DoctorReport
- Produces: create_app(settings: Settings | None = None) -> FastAPI
- Produces: GET /api/v1/health
- Produces: GET /api/v1/doctor

- [ ] **Step 1: Write failing doctor and health tests**

Create tests/unit/application/test_doctor.py:

```python
from pathlib import Path

from pptlib.application.doctor import run_doctor
from pptlib.config import load_settings


def test_doctor_reports_sqlite_fts5_and_missing_libreoffice(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
            "PATH": "",
        }
    )

    report = run_doctor(settings, executable_finder=lambda _: None)

    assert report.sqlite.ok is True
    assert report.fts5.ok is True
    assert report.libreoffice.ok is False
    assert report.ready_for_text_search is True
    assert report.ready_for_rendering is False
```

Create tests/contract/web/test_health.py:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from pptlib.config import load_settings
from pptlib.web.app import create_app


def test_health_uses_success_envelope(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["version"] == "0.1.0"
    assert payload["request_id"].startswith("req_")


def test_unknown_api_route_uses_error_envelope(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"
    assert payload["error"]["retryable"] is False
    assert payload["request_id"].startswith("req_")
```

- [ ] **Step 2: Run tests and verify missing application/web failures**

Run:

```bash
.venv/bin/pytest tests/unit/application/test_doctor.py tests/contract/web/test_health.py -v
```

Expected: collection fails because application.doctor and web.app do not exist.

- [ ] **Step 3: Implement the doctor report**

Create src/pptlib/application/__init__.py:

```python
"""Application use cases."""
```

Create src/pptlib/application/doctor.py:

```python
from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from pptlib.config import Settings


@dataclass(frozen=True, slots=True)
class Check:
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    sqlite: Check
    fts5: Check
    libreoffice: Check
    data_directory: Check
    output_directory: Check

    @property
    def ready_for_text_search(self) -> bool:
        return self.sqlite.ok and self.fts5.ok and self.data_directory.ok

    @property
    def ready_for_rendering(self) -> bool:
        return self.ready_for_text_search and self.libreoffice.ok

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["ready_for_text_search"] = self.ready_for_text_search
        value["ready_for_rendering"] = self.ready_for_rendering
        return value


def _directory_check(path: Path) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".pptlib-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Check(True, str(path))
    except OSError as error:
        return Check(False, str(error))


def run_doctor(
    settings: Settings,
    executable_finder: Callable[[str], str | None] = shutil.which,
) -> DoctorReport:
    connection = sqlite3.connect(":memory:")
    sqlite_check = Check(True, sqlite3.sqlite_version)
    try:
        connection.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(body)")
        fts_check = Check(True, "FTS5 available")
    except sqlite3.OperationalError as error:
        fts_check = Check(False, str(error))
    finally:
        connection.close()

    soffice = executable_finder("soffice")
    if soffice is None:
        app_binary = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        soffice = str(app_binary) if app_binary.exists() else None

    return DoctorReport(
        sqlite=sqlite_check,
        fts5=fts_check,
        libreoffice=Check(soffice is not None, soffice or "LibreOffice not found"),
        data_directory=_directory_check(settings.home),
        output_directory=_directory_check(settings.output_root),
    )
```

- [ ] **Step 4: Implement the FastAPI app and envelope middleware**

Create src/pptlib/web/__init__.py:

```python
"""Local Web application."""
```

Create src/pptlib/web/routes/__init__.py:

```python
"""HTTP routes."""
```

Create src/pptlib/web/routes/api.py:

```python
from __future__ import annotations

from fastapi import APIRouter, Request

from pptlib import __version__
from pptlib.application.doctor import run_doctor

router = APIRouter(prefix="/api/v1")


def _success(request: Request, data: object) -> dict[str, object]:
    return {
        "ok": True,
        "data": data,
        "request_id": request.state.request_id,
    }


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    return _success(request, {"status": "ok", "version": __version__})


@router.get("/doctor")
def doctor(request: Request) -> dict[str, object]:
    report = run_doctor(request.app.state.settings)
    return _success(request, report.to_dict())
```

Create src/pptlib/web/errors.py:

```python
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from pptlib.domain.errors import AppError, ErrorCode
from pptlib.domain.ids import new_id


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", new_id("req")))


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool = False,
    details: object | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {
                "code": code.value,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            },
            "request_id": _request_id(request),
        },
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, error: AppError) -> JSONResponse:
        return _error_response(
            request,
            status_code=409,
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            details=dict(error.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code=ErrorCode.REQUEST_INVALID,
            message="request validation failed",
            details=error.errors(),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND if error.status_code == 404 else ErrorCode.INTERNAL_ERROR
        return _error_response(
            request,
            status_code=error.status_code,
            code=code,
            message=str(error.detail),
        )
```

Create src/pptlib/web/app.py:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from starlette.responses import Response

from pptlib.config import Settings, load_settings
from pptlib.domain.ids import new_id
from pptlib.web.errors import install_error_handlers
from pptlib.web.routes.api import router as api_router


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="PPT Page Library", version="0.1.0")
    app.state.settings = settings or load_settings()
    install_error_handlers(app)

    @app.middleware("http")
    async def request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = new_id("req")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.include_router(api_router)
    return app
```

- [ ] **Step 5: Run doctor and contract tests**

Run:

```bash
.venv/bin/pytest tests/unit/application/test_doctor.py tests/contract/web/test_health.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit doctor and Web contract**

Run:

```bash
git add src/pptlib/application src/pptlib/web tests/unit/application tests/contract/web
git commit -m "feat: add environment doctor and health API"
```

---

### Task 6: Server-rendered application shell

**Files:**
- Create: src/pptlib/web/routes/pages.py
- Create: src/pptlib/web/templates/base.html
- Create: src/pptlib/web/templates/setup.html
- Create: src/pptlib/web/static/app.css
- Create: src/pptlib/web/static/app.js
- Modify: src/pptlib/web/app.py
- Create: tests/contract/web/test_pages.py

**Interfaces:**
- Consumes: create_app
- Produces: GET /
- Produces: mounted /static assets

- [ ] **Step 1: Write a failing HTML contract test**

Create tests/contract/web/test_pages.py:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from pptlib.config import load_settings
from pptlib.web.app import create_app


def test_setup_page_has_local_navigation_and_doctor_action(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    client = TestClient(create_app(settings))

    response = client.get("/")

    assert response.status_code == 200
    assert "PPT 页库" in response.text
    assert 'href="/static/app.css"' in response.text
    assert 'src="/static/app.js"' in response.text
    assert 'data-doctor-url="/api/v1/doctor"' in response.text
    assert "cdn" not in response.text.lower()
```

- [ ] **Step 2: Run the test and verify it fails with 404**

Run:

```bash
.venv/bin/pytest tests/contract/web/test_pages.py -v
```

Expected: assertion fails because GET / returns 404.

- [ ] **Step 3: Add templates and local assets**

Create src/pptlib/web/templates/base.html:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% block title %}PPT 页库{% endblock %}</title>
    <link rel="stylesheet" href="/static/app.css">
    <script src="/static/app.js" defer></script>
  </head>
  <body>
    <header class="topbar">
      <a class="brand" href="/">PPT 页库</a>
      <nav aria-label="主导航">
        <a href="/">设置</a>
        <span aria-disabled="true">页面库</span>
        <span aria-disabled="true">选片单</span>
        <span aria-disabled="true">任务</span>
      </nav>
    </header>
    <main>{% block content %}{% endblock %}</main>
  </body>
</html>
```

Create src/pptlib/web/templates/setup.html:

```html
{% extends "base.html" %}
{% block title %}开始使用 · PPT 页库{% endblock %}
{% block content %}
<section class="hero">
  <p class="eyebrow">LOCAL · READ ONLY SOURCES</p>
  <h1>建立你的本地 PPT 页面资产库</h1>
  <p>先检查本机环境，再配置允许扫描的来源目录。</p>
  <button type="button" id="doctor" data-doctor-url="/api/v1/doctor">检查环境</button>
  <pre id="doctor-result" aria-live="polite">尚未检查</pre>
</section>
{% endblock %}
```

Create src/pptlib/web/static/app.js:

```javascript
document.addEventListener("DOMContentLoaded", () => {
  const button = document.querySelector("#doctor");
  const output = document.querySelector("#doctor-result");
  if (!(button instanceof HTMLButtonElement) || !(output instanceof HTMLElement)) {
    return;
  }
  button.addEventListener("click", async () => {
    button.disabled = true;
    output.textContent = "检查中…";
    try {
      const response = await fetch(button.dataset.doctorUrl, {
        headers: { Accept: "application/json" },
      });
      const payload = await response.json();
      output.textContent = JSON.stringify(payload.data, null, 2);
    } catch (error) {
      output.textContent = `检查失败：${String(error)}`;
    } finally {
      button.disabled = false;
    }
  });
});
```

Create src/pptlib/web/static/app.css:

```css
:root {
  color-scheme: light;
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
  color: #172033;
  background: #f4f2ed;
}
* { box-sizing: border-box; }
body { margin: 0; }
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 28px;
  border-bottom: 1px solid #d8d4ca;
  background: rgba(255, 255, 255, .8);
}
.brand { color: inherit; font-weight: 700; text-decoration: none; }
nav { display: flex; gap: 18px; font-size: 14px; }
nav a { color: #244c3f; }
nav span { color: #8a8f99; }
main { max-width: 960px; margin: 0 auto; padding: 72px 28px; }
.hero { max-width: 680px; }
.eyebrow { color: #27644f; font-size: 12px; letter-spacing: .18em; }
h1 { margin: 12px 0; font-size: clamp(36px, 6vw, 64px); line-height: 1.04; }
button {
  margin-top: 24px;
  border: 0;
  border-radius: 8px;
  padding: 11px 16px;
  color: white;
  background: #193b31;
  cursor: pointer;
}
button:disabled { opacity: .55; cursor: wait; }
pre {
  margin-top: 18px;
  min-height: 72px;
  overflow: auto;
  border: 1px solid #d8d4ca;
  border-radius: 10px;
  padding: 16px;
  background: white;
}
```

- [ ] **Step 4: Mount the assets and page router**

Create src/pptlib/web/routes/pages.py:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


@router.get("/", response_class=HTMLResponse)
def setup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="setup.html", context={})
```

Modify src/pptlib/web/app.py to add imports:

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from pptlib.web.routes.pages import router as page_router
```

Add these lines immediately after app.state.settings is assigned:

```python
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
```

Add this line before app.include_router(api_router):

```python
    app.include_router(page_router)
```

- [ ] **Step 5: Run page and API contract tests**

Run:

```bash
.venv/bin/pytest tests/contract/web -v
```

Expected: all contract tests pass.

- [ ] **Step 6: Commit the local page shell**

Run:

```bash
git add src/pptlib/web tests/contract/web/test_pages.py
git commit -m "feat: add server rendered application shell"
```

---

### Task 7: CLI commands and one-shot worker

**Files:**
- Create: src/pptlib/bootstrap.py
- Create: src/pptlib/cli.py
- Create: src/pptlib/worker/main.py
- Create: tests/unit/test_cli.py
- Create: tests/integration/worker/test_worker_main.py

**Interfaces:**
- Consumes: load_settings, migrate, claim_next
- Produces: pptlib doctor
- Produces: pptlib init
- Produces: pptlib serve
- Produces: pptlib worker --once
- Produces: run_once(settings: Settings, worker_id: str) -> bool

- [ ] **Step 1: Write failing CLI and worker tests**

Create tests/unit/test_cli.py:

```python
from pptlib.cli import build_parser


def test_cli_exposes_required_foundation_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["init"]).command == "init"
    assert parser.parse_args(["serve"]).command == "serve"
    assert parser.parse_args(["worker", "--once"]).once is True
```

Create tests/integration/worker/test_worker_main.py:

```python
from pathlib import Path

from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect
from pptlib.worker.main import run_once
from pptlib.worker.queue import enqueue


def test_run_once_executes_doctor_job(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    initialize(settings)
    connection = connect(settings.database_path)
    job_id = enqueue(connection, "doctor", "worker-smoke")
    connection.close()

    assert run_once(settings, worker_id="worker-test") is True

    connection = connect(settings.database_path)
    status = connection.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    assert status == "succeeded"
```

- [ ] **Step 2: Run tests and verify missing modules**

Run:

```bash
.venv/bin/pytest tests/unit/test_cli.py tests/integration/worker/test_worker_main.py -v
```

Expected: collection fails because pptlib.cli, bootstrap, or worker.main does not exist.

- [ ] **Step 3: Implement initialization**

Create src/pptlib/bootstrap.py:

```python
from __future__ import annotations

from pathlib import Path

from pptlib.config import Settings
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.migrations import migrate


def initialize(settings: Settings) -> list[str]:
    for directory in (
        settings.home,
        settings.assets_dir,
        settings.temp_dir,
        settings.log_dir,
        settings.output_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    connection = connect(settings.database_path)
    try:
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        return migrate(connection, migrations_dir)
    finally:
        connection.close()
```

- [ ] **Step 4: Implement one-shot worker dispatch**

Create src/pptlib/worker/main.py:

```python
from __future__ import annotations

from datetime import UTC, datetime

from pptlib.application.doctor import run_doctor
from pptlib.config import Settings
from pptlib.infrastructure.db.connection import connect
from pptlib.worker.queue import claim_next, finish_job, start_job


def run_once(settings: Settings, worker_id: str) -> bool:
    connection = connect(settings.database_path)
    try:
        lease = claim_next(
            connection,
            worker_id=worker_id,
            lease_seconds=settings.job_lease_seconds,
            now=datetime.now(UTC),
        )
        if lease is None:
            return False
        start_job(connection, lease)
        if lease.kind == "doctor":
            run_doctor(settings)
        else:
            raise RuntimeError(f"no handler registered for job kind {lease.kind}")
        finish_job(connection, lease)
        return True
    finally:
        connection.close()
```

- [ ] **Step 5: Implement the argparse CLI**

Create src/pptlib/cli.py:

```python
from __future__ import annotations

import argparse
import json
import socket
import uuid
import webbrowser

import uvicorn

from pptlib.application.doctor import run_doctor
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.web.app import create_app
from pptlib.worker.main import run_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pptlib")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor")
    subparsers.add_parser("init")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--no-open", action="store_true")
    worker = subparsers.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    return parser


def _available_port(host: str, preferred: int) -> int:
    with socket.socket() as probe:
        try:
            probe.bind((host, preferred))
            return preferred
        except OSError:
            probe.bind((host, 0))
            return int(probe.getsockname()[1])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    if args.command == "doctor":
        print(json.dumps(run_doctor(settings).to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "init":
        applied = initialize(settings)
        print(json.dumps({"applied_migrations": applied}, ensure_ascii=False))
        return 0
    if args.command == "worker":
        if not args.once:
            raise SystemExit("M0.0 worker requires --once")
        initialize(settings)
        run_once(settings, worker_id=f"worker-{uuid.uuid4().hex[:8]}")
        return 0
    if args.command == "serve":
        initialize(settings)
        port = _available_port(settings.host, settings.port)
        if not args.no_open:
            webbrowser.open(f"http://{settings.host}:{port}")
        uvicorn.run(create_app(settings), host=settings.host, port=port)
        return 0
    raise AssertionError("unreachable command")
```

- [ ] **Step 6: Run CLI and worker tests**

Run:

```bash
.venv/bin/pytest tests/unit/test_cli.py tests/integration/worker/test_worker_main.py -v
.venv/bin/pptlib init
.venv/bin/pptlib doctor
```

Expected: tests pass; init prints migration list; doctor prints JSON.

- [ ] **Step 7: Commit executable entry points**

Run:

```bash
git add src/pptlib/bootstrap.py src/pptlib/cli.py src/pptlib/worker/main.py tests
git commit -m "feat: add CLI and one-shot worker"
```

---

### Task 8: Verification commands and developer handoff

**Files:**
- Create: Makefile
- Modify: README.md
- Create: var/.gitkeep
- Modify: pyproject.toml
- Test: all tests under tests

**Interfaces:**
- Consumes: all earlier tasks
- Produces: make lint, make typecheck, make test-unit, make test-integration, make test-contract, make check

- [ ] **Step 1: Add Makefile commands**

Create Makefile:

```make
PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy

.PHONY: install lint typecheck test-unit test-integration test-contract check run worker

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

test-contract:
	$(PYTEST) tests/contract -v

check: lint typecheck test-unit test-integration test-contract

run:
	.venv/bin/pptlib serve

worker:
	.venv/bin/pptlib worker --once
```

- [ ] **Step 2: Add the developer README**

Create README.md:

```markdown
# PPT Page Library

Local macOS PPT page indexing and composition tool.

## Requirements

- macOS 13 or newer.
- Python 3.11.
- LibreOffice is optional for the M0.0 shell and required for rendering/export.

## Start

    make install
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib init
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib doctor
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib serve

The server binds only to 127.0.0.1. Use --no-open when running without a desktop session.

## Worker smoke test

    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib worker --once

## Verify

    make check

Runtime does not require Node.js, Docker, Redis, an external database, or CDN assets.
```

Create var/.gitkeep as an empty file.

- [ ] **Step 3: Include templates, migrations, and static assets in built packages**

Verify that pyproject.toml contains this package-data block (Task 1 adds the first entry; this task adds the remaining entries):

```toml
[tool.setuptools.package-data]
pptlib = [
  "default.toml",
  "migrations/*.sql",
  "web/templates/*.html",
  "web/templates/fragments/*.html",
  "web/static/*.css",
  "web/static/*.js",
  "web/static/vendor/*",
]
```

- [ ] **Step 4: Run the complete verification gate**

Run:

```bash
make check
PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib init
PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib doctor
```

Expected:

- Ruff reports no violations.
- mypy reports Success: no issues found.
- all unit, integration, and contract tests pass.
- init exits 0.
- doctor reports SQLite and FTS5 available.
- doctor may report LibreOffice missing without failing text-search readiness.

- [ ] **Step 5: Perform the local server smoke test**

Run in one terminal:

```bash
PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib serve --no-open
```

Run in another terminal:

```bash
curl -fsS http://127.0.0.1:8765/api/v1/health
curl -fsS http://127.0.0.1:8765/
```

Expected: health returns the success envelope; root HTML contains PPT 页库.

- [ ] **Step 6: Commit the verified foundation**

Run:

```bash
git add Makefile README.md pyproject.toml var/.gitkeep
git commit -m "docs: add foundation developer workflow"
git status --short
```

Expected: commit succeeds and git status is clean.

---

## Plan Self-Review

### Spec coverage

- Lightweight runtime and macOS paths: Tasks 1, 5, 7, 8.
- Domain isolation and explicit state transitions: Task 2.
- SQLite WAL, migrations, and durable jobs: Tasks 3 and 4.
- Short Web requests and separate worker entry point: Tasks 5 and 7.
- JSON envelope and local server-rendered UI: Tasks 5 and 6.
- No Node, Docker, Redis, external database, or CDN: Tasks 1, 6, and 8.
- TDD and verification gate: every task plus Task 8.

### Deferred by design

The following are not foundation gaps; each belongs to a later independently reviewed plan:

- Full source, slide, artifact, selection, export, evidence, and FTS migrations.
- OOXML package graph and transplant.
- LibreOffice rendering.
- Source-root management and search UI.
- Selection UI and export workflow.
- Reconcile, golden, E2E, and performance suites.

### Type and naming consistency

- Settings is defined in Task 1 and consumed unchanged by Tasks 3, 5, and 7.
- JobStatus database strings match the Task 2 enum and Task 3 CHECK constraint.
- JobLease fields match Task 4 queue functions and Task 7 worker dispatch.
- create_app always accepts Settings | None and is used consistently in tests and CLI.
