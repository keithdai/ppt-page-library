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
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
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


def test_unknown_handler_releases_job_with_safe_error(tmp_path: Path) -> None:
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
    job_id = enqueue(connection, "unknown", "worker-unknown")
    connection.close()

    assert run_once(settings, worker_id="worker-test") is True
    connection = connect(settings.database_path)
    row = connection.execute(
        "SELECT status, lease_owner, error_code, error_message FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["status"] == "retry_wait"
    assert row["lease_owner"] is None
    assert row["error_code"] == "HANDLER_NOT_FOUND"
    assert "handler" in row["error_message"]


def test_handler_failure_releases_job_without_exception_details(
    tmp_path: Path, monkeypatch
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
    job_id = enqueue(connection, "doctor", "worker-failure")
    connection.close()

    def explode(_settings):
        raise RuntimeError("secret token should not be persisted")

    monkeypatch.setattr("pptlib.worker.main.run_doctor", explode)
    assert run_once(settings, worker_id="worker-test") is True
    connection = connect(settings.database_path)
    row = connection.execute(
        "SELECT status, lease_owner, error_code, error_message FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["status"] == "retry_wait"
    assert row["lease_owner"] is None
    assert row["error_code"] == "HANDLER_FAILED"
    assert "secret" not in row["error_message"]


def test_registered_handler_lookup_error_is_handler_failed(
    tmp_path: Path, monkeypatch
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
    job_id = enqueue(connection, "doctor", "lookup-error")
    connection.close()

    def raise_lookup(_settings):
        raise LookupError("handler internals")

    monkeypatch.setattr("pptlib.worker.main.run_doctor", raise_lookup)
    assert run_once(settings, worker_id="worker-test") is True
    connection = connect(settings.database_path)
    row = connection.execute(
        "SELECT error_code, error_message FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["error_code"] == "HANDLER_FAILED"
    assert row["error_message"] == "job handler failed"


def test_worker_does_not_bubble_stale_lease_failure(tmp_path: Path, monkeypatch) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
        }
    )
    initialize(settings)
    connection = connect(settings.database_path)
    enqueue(connection, "unknown", "stale-failure")
    connection.close()

    def stale_failure(*_args, **_kwargs):
        raise RuntimeError("stale")

    monkeypatch.setattr("pptlib.worker.main.fail_job", stale_failure)
    assert run_once(settings, worker_id="worker-test") is True
