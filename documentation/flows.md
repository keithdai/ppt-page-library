# Critical Flows

## Import And Render

Actor: local desktop user.

Preconditions:

- Selected paths exist and are readable.
- The packaged Python runtime is available, or the development repository and `.venv` are available.
- No other heavy task is running.

Sequence:

1. Renderer invokes the allowlisted import IPC method.
2. Electron acquires the in-process heavy-task slot and starts `pptlib import`.
3. Scanner filters symlinks, temporary files, formats and size limits before hashing.
4. Import service checks metadata, hashes only changed/new sources, parses changed content and
   updates SQLite.
5. LibreOffice renders PPTX to PDF; bundled PDFium creates thumbnails and previews.
6. Progress events return to the originating renderer.

Side effects: SQLite versions/pages, previews, thumbnails and render metadata. Source files remain
unchanged.

Deny/failure cases: missing path, changed-during-read source, unsafe external relationship,
invalid package, renderer failure, active heavy task.

## Automatic Update

Actor: local user for manual runs; Electron scheduler for scheduled runs.

Preconditions:

- Plan is enabled for scheduled execution.
- Current local time is inside the execution window.
- Application is running and device is awake.
- No heavy task owns the task slot.

Sequence:

1. User or scheduler selects a plan.
2. Preview planner returns accepted/skipped files and reasons without changing the library.
3. User confirms manual work; scheduled work uses the saved plan.
4. Electron starts a supervised `scan-plan run` process.
5. Python records a run, processes roots and checkpoints file results.
6. Missing sources remain in the library by default.
7. Completion, partial failure, cancellation or interruption is persisted.

Side effects: plan run history, root status, imported pages and generated assets.

Deny/failure cases: overlapping roots, invalid size/time rules, missing authorization, concurrent
heavy task, stale file metadata, user stop.

## Compose Export

Actor: local desktop user.

Preconditions:

- Selection contains PPTX pages only.
- Source versions still exist.
- Source SHA-256 values match unless the user explicitly disables verification.
- Selected presentations use compatible page dimensions.

Sequence:

1. Renderer writes ordered slide IDs to a local manifest.
2. Electron acquires the heavy-task slot and starts `pptlib compose`.
3. Application resolves each ID to a current source version.
4. Exporter copies selected OOXML pages and their reachable dependencies.
5. Unselected slide parts are removed.
6. Output is validated, atomically renamed and accompanied by a source manifest.

Side effects: output PPTX and adjacent manifest. Source files and library records are unchanged.

Deny/failure cases: stale selection, source change, missing source, mixed page dimensions, HTML
selection, invalid OOXML package.

## Delete From Library

Actor: local desktop user.

Precondition: explicit confirmation in the UI.

Sequence:

1. Renderer sends deck/page IDs, never arbitrary SQL or source paths.
2. Electron serializes the operation with other heavy tasks.
3. Python removes index rows and generated assets.
4. Existing selection references prevent unsafe deletion where required.

Side effects: local index and cache only. Original source files are never deleted.

## Application Exit During Work

1. Electron intercepts quit.
2. It sends `SIGTERM` to the active CLI process.
3. The Python process stops before starting the next file.
4. Electron waits up to the bounded timeout, then escalates to `SIGKILL`.
5. On next launch, unfinished scan runs become `interrupted`.
