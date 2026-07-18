CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'queued', 'leased', 'running', 'retry_wait',
            'succeeded', 'failed', 'cancelled', 'abandoned'
        )
    ),
    priority INTEGER NOT NULL DEFAULT 100,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner TEXT,
    lease_expires_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(kind, idempotency_key)
);

CREATE INDEX idx_jobs_claim
ON jobs(status, available_at, priority, created_at);

CREATE TABLE job_stages (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    checkpoint_json TEXT,
    output_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY(job_id, stage_name)
);
