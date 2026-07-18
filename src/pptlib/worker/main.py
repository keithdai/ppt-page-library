from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from pptlib.application.doctor import run_doctor
from pptlib.config import Settings
from pptlib.infrastructure.db.connection import connect
from pptlib.worker.lease import JobLease
from pptlib.worker.queue import claim_next, fail_job, finish_job, start_job

logger = logging.getLogger("pptlib.worker")


class _UnknownHandler(Exception):
    """Raised only when dispatch has no registered handler for a job kind."""


def _fail_safely(
    connection: sqlite3.Connection,
    lease: JobLease,
    *,
    error_code: str,
    error_message: str,
) -> None:
    try:
        fail_job(
            connection,
            lease,
            error_code=error_code,
            error_message=error_message,
        )
    except RuntimeError:
        # Another worker may have reclaimed an expired lease. Never mutate or
        # surface that stale worker's failure; the current owner is authoritative.
        logger.warning(
            "worker lease lost before failure could be recorded",
            extra={
                "job_id": lease.job_id,
                "worker_id": lease.worker_id,
                "kind": lease.kind,
                "error_code": "WORKER_LOST",
            },
        )


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
        try:
            now = datetime.now(UTC)
            start_job(connection, lease, now=now)
            if lease.kind == "doctor":
                run_doctor(settings)
            else:
                raise _UnknownHandler
            finish_job(connection, lease, now=datetime.now(UTC))
        except _UnknownHandler:
            error_code = "HANDLER_NOT_FOUND"
            error_message = "no handler registered for job kind"
            logger.error(
                "worker handler failed",
                extra={
                    "job_id": lease.job_id,
                    "worker_id": worker_id,
                    "kind": lease.kind,
                    "error_code": error_code,
                },
            )
            _fail_safely(connection, lease, error_code=error_code, error_message=error_message)
        except Exception:
            error_code = "HANDLER_FAILED"
            error_message = "job handler failed"
            logger.exception(
                "worker handler failed",
                extra={
                    "job_id": lease.job_id,
                    "worker_id": worker_id,
                    "kind": lease.kind,
                    "error_code": error_code,
                },
            )
            _fail_safely(connection, lease, error_code=error_code, error_message=error_message)
        return True
    finally:
        connection.close()
