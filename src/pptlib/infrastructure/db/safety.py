from __future__ import annotations

import contextlib
import fcntl
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class DatabaseIntegrityError(RuntimeError):
    """Raised when SQLite reports that a library database is not healthy."""


@contextmanager
def database_maintenance_lock(home: Path) -> Iterator[None]:
    lock_path = home / ".database-maintenance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def check_database_integrity(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    messages = [str(row[0]) for row in rows]
    if messages != ["ok"]:
        detail = "; ".join(messages[:5]) or "unknown integrity check failure"
        raise DatabaseIntegrityError(f"database integrity check failed: {detail}")

    foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_issues:
        raise DatabaseIntegrityError(
            f"database foreign key check failed: {len(foreign_key_issues)} issue(s)"
        )


def create_database_backup(
    connection: sqlite3.Connection,
    database_path: Path,
    backup_dir: Path,
) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"{database_path.stem}-{timestamp}-pre-migration.db"
    staging = backup_dir / f".{destination.name}.{uuid4().hex}.tmp"
    backup_connection = sqlite3.connect(staging)
    try:
        connection.backup(backup_connection)
        check_database_integrity(backup_connection)
    except Exception:
        backup_connection.close()
        with contextlib.suppress(OSError):
            staging.unlink()
        raise
    finally:
        with contextlib.suppress(sqlite3.ProgrammingError):
            backup_connection.close()
    staging.replace(destination)
    return destination


def restore_database_backup(backup_path: Path, database_path: Path) -> None:
    staging = database_path.with_name(f".{database_path.name}.{uuid4().hex}.restore")
    shutil.copy2(backup_path, staging)
    for suffix in ("-wal", "-shm"):
        with contextlib.suppress(OSError):
            Path(f"{database_path}{suffix}").unlink()
    staging.replace(database_path)
