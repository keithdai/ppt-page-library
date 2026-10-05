import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import pptlib.bootstrap as bootstrap
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.safety import database_maintenance_lock


def test_initialize_surfaces_unusable_temp_dir(tmp_path: Path, monkeypatch) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    original_mkdir = Path.mkdir

    def deny_temp(path: Path, *args, **kwargs) -> None:
        if path == settings.temp_dir:
            raise PermissionError("temporary directory is not writable")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_temp)
    with pytest.raises(PermissionError, match="not writable"):
        initialize(settings)


def test_initialize_backs_up_and_restores_after_migration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    initialize(settings)
    connection = connect(settings.database_path)
    try:
        connection.execute("CREATE TABLE recovery_probe(value TEXT NOT NULL)")
        connection.execute("INSERT INTO recovery_probe(value) VALUES ('before')")
    finally:
        connection.close()

    monkeypatch.setattr(
        bootstrap,
        "pending_migrations",
        lambda _connection, _directory: [tmp_path / "9999_failure.sql"],
    )

    def fail_after_write(connection, _directory):
        connection.execute("UPDATE recovery_probe SET value = 'after'")
        connection.execute("CREATE TABLE migration_partial(id INTEGER)")
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(bootstrap, "migrate", fail_after_write)

    with pytest.raises(RuntimeError, match="migration exploded"):
        initialize(settings)

    restored = connect(settings.database_path)
    try:
        assert restored.execute("SELECT value FROM recovery_probe").fetchone()[0] == "before"
        assert (
            restored.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'migration_partial'"
            ).fetchone()
            is None
        )
    finally:
        restored.close()
    backups = list((settings.home / "backups").glob("pages-*-pre-migration.db"))
    assert len(backups) == 1


def test_database_maintenance_lock_serializes_initialization(tmp_path: Path) -> None:
    active = 0
    peak = 0
    guard = threading.Lock()

    def hold_lock() -> None:
        nonlocal active, peak
        with database_maintenance_lock(tmp_path):
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with guard:
                active -= 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _index: hold_lock(), range(2)))

    assert peak == 1
