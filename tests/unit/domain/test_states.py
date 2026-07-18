import pytest

from pptlib.domain.errors import AppError, ErrorCode
from pptlib.domain.states import JobStatus, assert_job_transition


def test_job_can_move_from_queued_to_leased() -> None:
    assert_job_transition(JobStatus.QUEUED, JobStatus.LEASED)


def test_job_cannot_move_from_succeeded_back_to_running() -> None:
    with pytest.raises(AppError) as raised:
        assert_job_transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)

    assert raised.value.code is ErrorCode.INVALID_STATE_TRANSITION
