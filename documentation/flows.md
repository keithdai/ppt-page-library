# Critical Flows

Only flows that cross a trust boundary, mutate durable state or affect user-owned files belong
here. Feature navigation and presentation-only interactions are intentionally omitted.

## Local Import And Render

Actor: local desktop user.

Preconditions: source selected by a system dialog; supported extension/size; no heavy task active.

1. Renderer invokes an allowlisted preload method.
2. Electron verifies the main window/frame and normalizes the selected source list.
3. Task coordinator durably records the running task and starts `pptlib import` without a shell.
4. Scanner rejects symlinks, temporary files, unsupported formats and size-limit violations.
5. Import service uses metadata for unchanged checks and SHA-256 for changed/new content.
6. Parser rejects unsafe PPTX external relationships or invalid HTML structure.
7. SQLite updates and generated assets commit only for an accepted version.
8. Progress and result events return to the originating renderer.

Side effects: library rows, FTS rows, thumbnails, previews and render metadata. Original sources are
not changed.

Failure/deny: foreign IPC sender, invalid path/format, changed-during-read source, unsafe package,
render failure, cancellation or active heavy task.

## Agent File Import

Actor: explicitly authorized local Agent CLI caller.

1. `deckatlas import` requires `--allow-write` and 1–100 local PPTX/HTML/ZIP files.
2. File validation and the existing diagnostic scanner reject missing, symlink, directory,
   unsupported, temporary and oversized sources before initialization.
3. A per-library process lock excludes concurrent private CLI/Agent heavy work.
4. Initialization uses existing migrations; the importer parses, classifies, indexes and renders
   files in place with missing-source policy KEEP.
5. A single JSON response returns current deck/version IDs and per-file status.
6. Later `search` reads the same index. Re-import reuses unchanged versions and retries previews.

Side effects: index, FTS, classification and generated assets. Original files remain read-only and
must be retained; unrelated missing sources are never removed.

Failure/deny: missing consent, invalid file, busy task, parser or renderer failure. Incomplete
imports return exit 7 with results: some pages may already be searchable. SIGINT/SIGTERM stop
before the next file and release the lock when work returns.

## Online HTML Archive

Actor: local desktop user.

Preconditions: HTML feature enabled; public HTTP(S) URL; no heavy task active.

1. Renderer sends a typed URL, not an arbitrary command.
2. Electron routes URLs only to `pptlib import-url`.
3. Downloader validates scheme, resolved public address, redirects, total time and byte limits.
4. Static HTML and allowed dependencies are written to a temporary archive.
5. Source scripts, handlers, forms, external frames and network dependencies are removed/blocked.
6. Content hash identifies an immutable snapshot; identical content reuses the archive.
7. Sanitized pages render in isolated Chromium with network blocked.
8. Archive is published under `assets/html-archives` and indexed in SQLite.

Side effects: immutable online snapshot, origin record, library rows and previews.

Failure/deny: private/local address, redirect to private address, TLS/network failure, oversized
content, script-only shell, no recognized pages or archive publish failure. Partial dependency
downloads are recorded explicitly rather than hidden.

## Automatic Update

Actors: local user for manual runs; Electron scheduler for scheduled runs.

Preconditions: plan enabled; app running and device awake; occurrence inside the execution window;
no heavy task active.

1. Preview planner evaluates roots and rules without changing the library.
2. User confirms manual work; scheduled work uses the saved plan and scheduled occurrence.
3. Electron verifies sender for manual requests and starts `scan-plan run`.
4. Python creates a run, processes files serially and checkpoints each result.
5. Missing sources stay indexed by default; moves are resolved by content identity.
6. Completion, partial failure, cancellation, missed window or interruption is persisted.

Side effects: plan/run history, root state, imported versions and generated assets.

Failure/deny: overlapping roots, invalid schedule/rules, missing root, concurrent task, stale file,
duplicate occurrence or cancellation.

## Compose And Export

Actors: desktop user or explicitly authorized local Agent CLI caller.

Preconditions: ordered selection; sources and dependencies available; output format compatible;
preflight state still current.

1. Desktop writes an ordered ID manifest in `PPTLIB_HOME`; Agent CLI accepts only
   `deckatlas.selection/1.0`.
2. Preflight resolves IDs, checks source hashes, dimensions, dependencies, output collision and
   format compatibility, then returns a token bound to that state.
3. Desktop confirmation or Agent `--allow-write` supplies the exact token.
4. Exporter revalidates all state before writing to a temporary location.
5. PPTX output clones reachable OOXML dependencies. HTML/ZIP output sanitizes and namespaces pages;
   PPTX pages in mixed output use existing full-resolution previews.
6. Package validation completes before atomic rename.
7. A provenance manifest is written beside the output.

Side effects: output presentation and provenance manifest only.

Failure/deny: stale token/selection, changed source, missing preview/dependency, source overwrite,
incompatible dimensions/format, output outside `PPTLIB_OUTPUT_ROOT` for Agent callers, existing
output without explicit overwrite, invalid package or cancellation.

## Delete From Library

Actor: local desktop user after explicit UI confirmation.

1. Renderer sends deck/page IDs, never SQL or a deletion path.
2. Electron verifies sender and serializes the task.
3. Application checks saved-selection references.
4. SQLite transaction removes index/FTS rows and prunes unreferenced versions.
5. Application deletes only generated assets associated with removed records.

Side effects: local library and generated cache only.

Failure/deny: foreign IPC sender, empty/invalid IDs, active task or saved selection dependency.
Original source files are never deleted.

## Dynamic HTML Preview

Actor: local desktop user for an already indexed slide.

1. Renderer requests a valid slide ID through named IPC.
2. Electron verifies sender and confirms the slide came from the current library response.
3. Python opens a short-lived loopback server for the sanitized archived page.
4. Electron creates a sandbox window with a unique nonpersistent session and no preload.
5. Preview navigation is restricted to the exact loopback origin/capability.
6. Close, crash, timeout, redirect or load failure tears down the server and window.

Side effects: ephemeral process/window only; no library mutation.

## Application Exit During Work

1. Electron intercepts quit and marks the active task stopping.
2. It sends `SIGTERM` to the supervised CLI process.
3. Python stops before starting the next file and cleans temporary work.
4. Electron waits up to 15 seconds, then escalates to `SIGKILL`.
5. On next launch, unfinished scan/task records are reconciled as interrupted.

Side effects: durable interrupted state; no silent retry or background continuation after quit.
