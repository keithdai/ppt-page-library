# DeckAtlas Agent CLI

`deckatlas` is the stable, machine-readable interface for local AI agents. The existing `pptlib`
command remains the private process interface used by the Electron application.

## Contract

- Protocol: `deckatlas.agent/1.0`
- One invocation produces exactly one compact JSON response on stdout.
- Errors use `{code, message, retryable, details}` and a non-zero exit code.
- Search and slide inspection are read-only and never initialize or migrate the library.
- `import` and `compose run` require `--allow-write`. Composition also requires a successful
  preflight token.
- Export destinations must be inside `PPTLIB_OUTPUT_ROOT`.
- Source presentations are never modified.

## Commands

```bash
deckatlas capabilities

deckatlas import "/path/to/AI-generated.pptx" --allow-write
deckatlas import "/path/to/deck.html" "/path/to/offline-deck.zip" --allow-write

deckatlas search --query "AI Native" --page-size 20
deckatlas search --topic "组织与人才" --page-type "观点与结论"
deckatlas search --expected-revision <revision> --page 2

deckatlas slide get <slide_id>
```

`search` returns a compact text excerpt and absolute paths to existing thumbnail and preview files.
`slide get` returns the full extracted text. Neither command exposes the source presentation path.

Every response can be correlated with a caller-generated identifier:

```bash
deckatlas --request-id task-42 search --query "供应链"
```

## Import AI-Generated Files

`import` accepts up to 100 explicit local `.pptx`, `.html`, `.htm` or `.zip` files. It automatically
enables the local HTML adapter for this operation. Directories, URLs, symlinks, hidden/temporary
files and oversized files are rejected before library initialization.

The command reuses desktop ingestion: source hashing, parsing, classification, FTS indexing and
thumbnail/preview generation. Sources stay in their original locations; retain the files and all
local HTML dependencies for future preview and export. ZIP inputs must contain a single HTML entry.
HTML must have recognized slides, such as `.slide` sections or Reveal leaf sections; script-only
pages and ordinary web pages are not accepted. Source scripts are never executed.

On success, `result.imported` contains `deck_id`, `version_id`, `slide_count`, `action`,
`searchable` and `preview_ready`. Search can immediately use `--deck-id` or a title keyword.
An unchanged file at the same path reuses its version and repairs missing previews. An updated
file creates a new version; importing an identical file at another path does not deduplicate decks.
Import never removes other library records when a source disappears.

Partial failures or cancellation return `IMPORT_INCOMPLETE` / exit 7. `error.details.result`
retains all imported and failed entries: preview generation can fail after indexing succeeds, so
check `searchable` separately from `preview_ready`. Re-importing the retained source retries
missing previews. PPTX preview requires LibreOffice; HTML preview requires local Chrome.
`cancellation_requested` also records an interrupt received during the final file, even when that
file finished indexing and rendering. Files rejected by a later scan still receive failure entries.

Import and compose share a nonblocking per-library process lock with private `pptlib` heavy
commands. Contention returns `DATABASE_BUSY` / exit 5 without starting import. SIGINT/SIGTERM stop
import before the next file; completed files remain indexed. Search stays available during work.
Database initialization and desktop startup recovery wait for active heavy work rather than
skipping migrations or interrupted-task recovery.
Desktop reads and progress persistence on an initialized library remain available during import.

## Controlled Export

Create a strict, versioned selection manifest:

```json
{
  "schema_version": "deckatlas.selection/1.0",
  "slide_ids": [
    "ver_example_s00001",
    "ver_example_s00003"
  ]
}
```

Preflight the exact manifest and destination:

```bash
deckatlas compose preflight selection.json "$PPTLIB_OUTPUT_ROOT/proposal.pptx"
```

When `result.ready` is `true`, pass its `preflight_token` to the write command:

```bash
deckatlas compose run selection.json "$PPTLIB_OUTPUT_ROOT/proposal.pptx" \
  --preflight-token <token> \
  --allow-write
```

If the selection, source files, existing output, or generated manifest changes after preflight, the
write is rejected. Replacing an existing output additionally requires `--overwrite`.

## Exit Codes

| Code | Meaning |
| --- | --- |
| `0` | Request completed, including a preflight with `ready: false` |
| `2` | Invalid request or manifest |
| `3` | Library, slide, manifest, or other required file not found |
| `4` | Path denied, stale revision, changed source, or failed precondition |
| `5` | Database busy; retry may succeed |
| `6` | Required local capability unavailable |
| `7` | Import incomplete; inspect `error.details.result` for indexed/failed/cancelled files |
| `10` | Internal error |

## Installation And Packaging

Development install:

```bash
make install
PPTLIB_HOME="$PWD/var/dev" .venv/bin/deckatlas capabilities
```

Standalone macOS runtime:

```bash
make agent-runtime
./build/agent-runtime/deckatlas/deckatlas capabilities
```

The runtime keeps the legacy `PPTLIB_HOME` and related environment variable names so it reads the
same local library as the DeckAtlas desktop application.

## HTML preview branch

With `PPTLIB_ENABLE_HTML=1`, capabilities and search results advertise `html` and
`zip` export formats. `compose preflight` / `compose run` accept those suffixes
under the same write permission, output-root and preflight-token checks.
HTML pages require HTML/ZIP output; mixed PPTX pages use high-resolution images
with an explicit preflight warning. Ingestion remains available through the
desktop and `pptlib import` / `pptlib import-url`. Agent callers can import local
files with `deckatlas import --allow-write`; URL archival remains private to the desktop interface.
