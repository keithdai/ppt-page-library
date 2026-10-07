CREATE TABLE desktop_task_state (
    slot TEXT PRIMARY KEY CHECK (slot = 'active'),
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'stopping', 'finished', 'failed', 'interrupted')
    ),
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    progress_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT
);
