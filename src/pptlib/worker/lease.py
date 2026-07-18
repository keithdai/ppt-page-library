from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: str
    kind: str
    worker_id: str
    expires_at: datetime
