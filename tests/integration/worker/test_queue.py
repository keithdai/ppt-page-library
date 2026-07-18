import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.migrations import migrate
from pptlib.worker.queue import (
    claim_next,
    enqueue,
    fail_job,
    finish_job,
    renew_lease,
    start_job,
)


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
    now = datetime(2026, 7, 16, tzinfo=UTC)
    connection.execute(
        "UPDATE jobs SET available_at = ? WHERE id = ?",
        (now.isoformat(), job_id),
    )

    lease = claim_next(
        connection,
        worker_id="worker-test",
        lease_seconds=120,
        now=now,
    )

    assert lease is not None
    assert lease.job_id == job_id
    start_job(connection, lease, now=now)
    finish_job(connection, lease, now=now)
    row = connection.execute(
        "SELECT status, error_code, error_message FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["status"] == "succeeded"
    assert row["error_code"] is None
    assert row["error_message"] is None


def test_expired_lease_is_reclaimed_by_another_worker(tmp_path: Path) -> None:
    connection = prepared_connection(tmp_path)
    job_id = enqueue(connection, "doctor", "reclaim")
    first_now = datetime(2026, 7, 16, tzinfo=UTC)
    connection.execute(
        "UPDATE jobs SET available_at = ? WHERE id = ?", (first_now.isoformat(), job_id)
    )
    first = claim_next(connection, "worker-1", lease_seconds=10, now=first_now)
    assert first is not None
    start_job(connection, first, now=first_now)

    second_now = datetime(2026, 7, 16, 0, 1, tzinfo=UTC)
    second = claim_next(connection, "worker-2", lease_seconds=10, now=second_now)
    assert second is not None
    assert second.job_id == job_id
    assert second.worker_id == "worker-2"


def test_expired_finish_is_rejected(tmp_path: Path) -> None:
    connection = prepared_connection(tmp_path)
    job_id = enqueue(connection, "doctor", "expired-finish")
    now = datetime(2026, 7, 16, tzinfo=UTC)
    connection.execute("UPDATE jobs SET available_at = ? WHERE id = ?", (now.isoformat(), job_id))
    lease = claim_next(connection, "worker-1", lease_seconds=10, now=now)
    assert lease is not None
    start_job(connection, lease, now=now)
    with pytest.raises(RuntimeError, match="no longer owned"):
        finish_job(connection, lease, now=datetime(2026, 7, 16, 0, 1, tzinfo=UTC))


def test_renew_lease_extends_expiry_for_current_owner(tmp_path: Path) -> None:
    connection = prepared_connection(tmp_path)
    job_id = enqueue(connection, "doctor", "renew")
    now = datetime(2026, 7, 16, tzinfo=UTC)
    connection.execute("UPDATE jobs SET available_at = ? WHERE id = ?", (now.isoformat(), job_id))
    lease = claim_next(connection, "worker-1", lease_seconds=10, now=now)
    assert lease is not None
    renewed = renew_lease(connection, lease, lease_seconds=60, now=now)
    assert renewed.expires_at == datetime(2026, 7, 16, 0, 1, tzinfo=UTC)
    stored = connection.execute(
        "SELECT lease_expires_at FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert stored["lease_expires_at"] == renewed.expires_at.isoformat()


def test_expired_fail_is_rejected_without_mutating_job(tmp_path: Path) -> None:
    connection = prepared_connection(tmp_path)
    job_id = enqueue(connection, "doctor", "expired-fail")
    now = datetime(2026, 7, 16, tzinfo=UTC)
    connection.execute("UPDATE jobs SET available_at = ? WHERE id = ?", (now.isoformat(), job_id))
    lease = claim_next(connection, "worker-1", lease_seconds=10, now=now)
    assert lease is not None
    start_job(connection, lease, now=now)
    with pytest.raises(RuntimeError, match="no longer valid"):
        fail_job(
            connection,
            lease,
            error_code="HANDLER_FAILED",
            error_message="safe failure",
            now=datetime(2026, 7, 16, 0, 1, tzinfo=UTC),
        )
    row = connection.execute(
        "SELECT status, lease_owner, error_code FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["status"] == "running"
    assert row["lease_owner"] == "worker-1"
    assert row["error_code"] is None


def test_expired_renew_is_rejected(tmp_path: Path) -> None:
    connection = prepared_connection(tmp_path)
    job_id = enqueue(connection, "doctor", "expired-renew")
    now = datetime(2026, 7, 16, tzinfo=UTC)
    connection.execute("UPDATE jobs SET available_at = ? WHERE id = ?", (now.isoformat(), job_id))
    lease = claim_next(connection, "worker-1", lease_seconds=10, now=now)
    assert lease is not None
    with pytest.raises(RuntimeError, match="no longer valid"):
        renew_lease(
            connection,
            lease,
            lease_seconds=60,
            now=datetime(2026, 7, 16, 0, 1, tzinfo=UTC),
        )


def test_fail_job_uses_backoff_and_fails_at_max_attempts(tmp_path: Path) -> None:
    connection = prepared_connection(tmp_path)
    first_id = enqueue(connection, "doctor", "retry-backoff")
    now = datetime(2026, 7, 16, tzinfo=UTC)
    connection.execute("UPDATE jobs SET available_at = ? WHERE id = ?", (now.isoformat(), first_id))
    first_lease = claim_next(connection, "worker-1", lease_seconds=10, now=now)
    assert first_lease is not None
    start_job(connection, first_lease, now=now)
    assert (
        fail_job(
            connection,
            first_lease,
            error_code="HANDLER_FAILED",
            error_message="safe failure",
            now=now,
        )
        == "retry_wait"
    )
    first_row = connection.execute(
        "SELECT status, available_at FROM jobs WHERE id = ?", (first_id,)
    ).fetchone()
    assert first_row["status"] == "retry_wait"
    assert first_row["available_at"] == (now + timedelta(seconds=1)).isoformat()
    assert claim_next(connection, "worker-2", lease_seconds=10, now=now) is None
    retry_lease = claim_next(
        connection, "worker-2", lease_seconds=10, now=now + timedelta(seconds=1)
    )
    assert retry_lease is not None
    assert retry_lease.job_id == first_id

    final_id = enqueue(connection, "doctor", "max-attempts")
    connection.execute(
        "UPDATE jobs SET available_at = ?, max_attempts = 1 WHERE id = ?",
        (now.isoformat(), final_id),
    )
    final_lease = claim_next(connection, "worker-1", lease_seconds=10, now=now)
    assert final_lease is not None
    start_job(connection, final_lease, now=now)
    assert (
        fail_job(
            connection,
            final_lease,
            error_code="HANDLER_FAILED",
            error_message="safe failure",
            now=now,
        )
        == "failed"
    )
    assert (
        connection.execute("SELECT status FROM jobs WHERE id = ?", (final_id,)).fetchone()[0]
        == "failed"
    )


def test_claim_begin_failure_preserves_original_exception(tmp_path: Path) -> None:
    connection = prepared_connection(tmp_path)

    class BeginFailureConnection:
        in_transaction = False

        def execute(self, sql, parameters=()):
            if sql == "BEGIN IMMEDIATE":
                raise sqlite3.OperationalError("database is locked")
            return connection.execute(sql, parameters)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        claim_next(BeginFailureConnection(), "worker-1", lease_seconds=10)
