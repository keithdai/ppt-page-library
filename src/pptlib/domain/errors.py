from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    DATABASE_BUSY = "DATABASE_BUSY"
    LIBREOFFICE_NOT_FOUND = "LIBREOFFICE_NOT_FOUND"
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    SOURCE_CHANGED = "SOURCE_CHANGED"
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
