from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from pptlib.domain.errors import AppError, ErrorCode

_held_locks: ContextVar[frozenset[tuple[int, Path]]] = ContextVar(
    "deckatlas_held_locks", default=frozenset()
)


@contextmanager
def heavy_operation_lock(
    home: Path, *, wait: bool = False, allow_reentry: bool = False
) -> Iterator[None]:
    home = home.resolve()
    key = (os.getpid(), home)
    held = _held_locks.get()
    if allow_reentry and key in held:
        yield
        return
    home.mkdir(parents=True, exist_ok=True)
    with (home / ".heavy-operation.lock").open("a+") as handle:
        try:
            mode = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(handle.fileno(), mode)
        except BlockingIOError as error:
            raise AppError(
                ErrorCode.DATABASE_BUSY,
                "another DeckAtlas import, render, export or maintenance task is running",
                retryable=True,
            ) from error
        token = _held_locks.set(held | {key})
        try:
            yield
        finally:
            _held_locks.reset(token)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
