CREATE TABLE scan_plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    schedule_kind TEXT NOT NULL DEFAULT 'daily'
        CHECK (schedule_kind IN ('manual', 'daily')),
    schedule_time TEXT NOT NULL DEFAULT '02:30',
    window_start TEXT NOT NULL DEFAULT '02:00',
    window_end TEXT NOT NULL DEFAULT '05:00',
    formats_json TEXT NOT NULL DEFAULT '["pptx"]',
    min_file_bytes INTEGER NOT NULL DEFAULT 0 CHECK (min_file_bytes >= 0),
    max_file_bytes INTEGER NOT NULL CHECK (max_file_bytes > 0),
    stability_seconds INTEGER NOT NULL DEFAULT 60 CHECK (stability_seconds >= 0),
    missing_policy TEXT NOT NULL DEFAULT 'keep'
        CHECK (missing_policy IN ('keep', 'remove')),
    repair_previews INTEGER NOT NULL DEFAULT 1 CHECK (repair_previews IN (0, 1)),
    last_preview_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE scan_plan_roots (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES scan_plans(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    recursive INTEGER NOT NULL DEFAULT 1 CHECK (recursive IN (0, 1)),
    authorization_status TEXT NOT NULL DEFAULT 'ok'
        CHECK (authorization_status IN ('ok', 'missing', 'unreadable')),
    last_scanned_at TEXT,
    last_result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(plan_id, path)
);

CREATE INDEX idx_scan_plan_roots_plan
ON scan_plan_roots(plan_id, created_at);

CREATE TABLE scan_runs (
    id TEXT PRIMARY KEY,
    plan_id TEXT REFERENCES scan_plans(id) ON DELETE SET NULL,
    plan_name TEXT NOT NULL,
    trigger_type TEXT NOT NULL
        CHECK (trigger_type IN ('manual', 'scheduled', 'retry')),
    scope_type TEXT NOT NULL
        CHECK (scope_type IN ('plan', 'root')),
    scope_root_id TEXT,
    status TEXT NOT NULL
        CHECK (status IN (
            'preflighting', 'running', 'stopping', 'completed',
            'partial', 'failed', 'cancelled', 'interrupted', 'missed'
        )),
    rules_json TEXT NOT NULL,
    stats_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX idx_scan_runs_history
ON scan_runs(started_at DESC);

CREATE INDEX idx_scan_runs_active
ON scan_runs(status, started_at DESC);

CREATE TABLE scan_run_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    root_id TEXT REFERENCES scan_plan_roots(id) ON DELETE SET NULL,
    path TEXT NOT NULL,
    decision TEXT NOT NULL
        CHECK (decision IN ('accepted', 'skipped', 'failed', 'cancelled', 'missing')),
    action TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    slide_count INTEGER NOT NULL DEFAULT 0,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_scan_run_items_run
ON scan_run_items(run_id, root_id, created_at);
