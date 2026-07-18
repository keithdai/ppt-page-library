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
