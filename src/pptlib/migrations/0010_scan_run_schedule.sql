ALTER TABLE scan_runs
ADD COLUMN scheduled_for TEXT;

CREATE INDEX idx_scan_runs_schedule
ON scan_runs(plan_id, trigger_type, scheduled_for);
