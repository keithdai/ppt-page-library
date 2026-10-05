# Scheduled Work

## Inventory

| Job | Schedule | Owner | Limits | Retry |
| --- | --- | --- | --- | --- |
| Scan plan | User-defined daily local time and execution window | Electron main process | One global heavy task; one file at a time | Manual failed-item retry |
| Missed-run recorder | Scheduler check after a missed window | Electron main process | One record per scheduled occurrence | Next minute if write failed |

There is no system `cron`, `launchd` agent or hidden daemon.

## Runtime Rules

- Scheduler checks once per minute while PinPage is running.
- Computer sleep pauses execution naturally.
- Wake or launch after the execution window records `missed`; it does not start work in daytime.
- Cross-midnight windows use a persisted `scheduled_for` occurrence to prevent duplicate runs.
- Paused plans never start automatically.
- Missing/unreadable roots pause the plan.
- Import, compose, deduplication, cleanup and automatic update share one Electron task slot.

## Idempotency

- Existing files use size/mtime/ctime as a fast unchanged check.
- Changed or moved files use SHA-256.
- Deck versions are unique by deck and content hash.
- Scheduled execution is deduplicated by plan and scheduled occurrence.
- Every file result is persisted in `scan_run_items`.

## Cancellation

1. Renderer requests stop.
2. Electron marks the task as stopping and sends `SIGTERM`.
3. Python sets a cancellation event and does not start the next file.
4. Electron waits up to 15 seconds during application quit, then sends `SIGKILL`.
5. On next launch, unfinished records become `interrupted`.

## Operations

- Current state: Automatic Update → Running.
- History: Automatic Update → Run History.
- Detailed process output: desktop log drawer and `PPTLIB_LOG_DIR`.
- Kill switch: pause an individual plan; quitting PinPage stops the scheduler.

## Current Limits

- The scheduler is not active when PinPage is fully quit.
- It does not wake the computer.
- Task state is coordinated in Electron memory; only scan-run history is persistent.
- A future worker service should use leases and checkpoints before allowing execution outside the
  desktop process.
