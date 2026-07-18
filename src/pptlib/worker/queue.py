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
    now: datetime | None = None,
) -> JobLease | None:
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(seconds=lease_seconds)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL
            WHERE status = 'retry_wait' AND available_at <= ?
            """,
            (_iso(now),),
        )
        # A worker may have died while holding a lease. Requeue expired work
        # while holding the same write lock used for claiming, preventing two
        # workers from reclaiming the same job.
        connection.execute(
            """
            UPDATE jobs
            SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                available_at = ?
            WHERE status IN ('leased', 'running')
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (_iso(now), _iso(now)),
        )
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
            SET status = 'leased', lease_owner = ?, lease_expires_at = ?, attempt = attempt + 1
            WHERE id = ? AND status = 'queued'
            """,
            (worker_id, _iso(expires_at), row["id"]),
        ).rowcount
        connection.execute("COMMIT")
    except Exception:
        # BEGIN IMMEDIATE itself may fail before a transaction exists (for
        # example while SQLite is locked). Only roll back an active transaction
        # so the original BEGIN/lock exception is never masked.
        if connection.in_transaction:
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


def start_job(
    connection: sqlite3.Connection,
    lease: JobLease,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    changed = connection.execute(
        """
        UPDATE jobs
        SET status = 'running', started_at = COALESCE(started_at, datetime('now'))
        WHERE id = ? AND status = 'leased' AND lease_owner = ?
          AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
        """,
        (lease.job_id, lease.worker_id, _iso(now)),
    ).rowcount
    if changed != 1:
        raise RuntimeError("job lease is no longer valid")


def finish_job(
    connection: sqlite3.Connection,
    lease: JobLease,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    changed = connection.execute(
        """
        UPDATE jobs
        SET status = 'succeeded', finished_at = datetime('now'),
            lease_owner = NULL, lease_expires_at = NULL,
            error_code = NULL, error_message = NULL
        WHERE id = ? AND status = 'running' AND lease_owner = ?
          AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
        """,
        (lease.job_id, lease.worker_id, _iso(now)),
    ).rowcount
    if changed != 1:
        raise RuntimeError("running job is no longer owned by worker")


def renew_lease(
    connection: sqlite3.Connection,
    lease: JobLease,
    lease_seconds: int,
    now: datetime | None = None,
) -> JobLease:
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(seconds=lease_seconds)
    changed = connection.execute(
        """
        UPDATE jobs
        SET lease_expires_at = ?
        WHERE id = ? AND lease_owner = ? AND status IN ('leased', 'running')
          AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
        """,
        (_iso(expires_at), lease.job_id, lease.worker_id, _iso(now)),
    ).rowcount
    if changed != 1:
        raise RuntimeError("job lease is no longer valid")
    return JobLease(
        job_id=lease.job_id,
        kind=lease.kind,
        worker_id=lease.worker_id,
        expires_at=expires_at,
    )


def fail_job(
    connection: sqlite3.Connection,
    lease: JobLease,
    *,
    error_code: str,
    error_message: str,
    now: datetime | None = None,
) -> str:
    """Record a safe failure and release the lease, retrying while attempts remain."""
    now = now or datetime.now(UTC)
    row = connection.execute(
        """
        SELECT attempt, max_attempts
        FROM jobs
        WHERE id = ? AND lease_owner = ? AND status IN ('leased', 'running')
          AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
        """,
        (lease.job_id, lease.worker_id, _iso(now)),
    ).fetchone()
    if row is None:
        raise RuntimeError("job lease is no longer valid")
    next_status = "retry_wait" if row["attempt"] < row["max_attempts"] else "failed"
    retry_delay = 2 ** max(row["attempt"] - 1, 0)
    available_at = now + timedelta(seconds=retry_delay) if next_status == "retry_wait" else now
    changed = connection.execute(
        """
        UPDATE jobs
        SET status = ?, available_at = ?,
            finished_at = CASE WHEN ? = 'failed' THEN datetime('now') ELSE NULL END,
            lease_owner = NULL, lease_expires_at = NULL,
            error_code = ?, error_message = ?
        WHERE id = ? AND lease_owner = ? AND status IN ('leased', 'running')
          AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
        """,
        (
            next_status,
            _iso(available_at),
            next_status,
            error_code,
            error_message,
            lease.job_id,
            lease.worker_id,
            _iso(now),
        ),
    ).rowcount
    if changed != 1:
        raise RuntimeError("job lease is no longer valid")
    return next_status
