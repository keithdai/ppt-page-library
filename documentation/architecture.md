# DeckAtlas Architecture

## Product And Boundary

DeckAtlas is a local-first macOS presentation library. It indexes user-selected PPTX and HTML
sources, generates searchable page metadata and previews, supports ordered page selection, and
composes selected pages into native PPTX or offline HTML/ZIP outputs.

Product invariants:

- Imported local files remain in their original locations and are treated as read-only.
- Online HTML must be archived into an immutable local snapshot before it enters the library.
- SQLite, generated previews, archived web resources and selections remain under `PPTLIB_HOME`.
- Deleting library content never deletes an original PPTX/HTML/ZIP source.
- DeckAtlas is the external brand. Existing `pptlib`, `PPTLIB_*`, app ID, database and cache names
  remain stable for upgrade compatibility.
- There is no remote DeckAtlas account, shared tenant data plane or general-purpose application API.

## Repository Map

| Area | Current source of truth |
| --- | --- |
| Python domain rules | `src/pptlib/domain/` |
| Use-case orchestration | `src/pptlib/application/` |
| SQLite and migrations | `src/pptlib/infrastructure/db/`, `src/pptlib/migrations/` |
| PPTX parse/render/export | `src/pptlib/ingestion/`, `rendering/`, `export/ooxml.py` |
| HTML parse/archive/render/export | `src/pptlib/html/`, `application/import_url.py`, `export/html.py` |
| Private desktop CLI | `src/pptlib/cli.py` |
| Versioned Agent CLI | `src/pptlib/agent_cli.py`, `application/agent.py` |
| Electron trust boundary | `desktop/main.js`, `preload.js`, `security.js` |
| Desktop UI | `desktop/renderer/` |
| In-app task/schedule control | `desktop/task-coordinator.js`, `scheduler.js` |
| Runtime and app packaging | `packaging/`, `desktop/package.json` |
| Static product website | `website/`, `.github/workflows/pages.yml` |
| Quality gate | `scripts/quality-gate.sh`, `scripts/check_repository.py`, `.github/workflows/quality.yml` |
| Current system documentation | `documentation/` |
| Product documents and evidence | `docs/`, `docs/acceptance/`, `docs/_archive/` |

Generated `build/`, `desktop/dist/`, `dist-app/`, `var/`, local worktrees, user `sources/` and
delivery packages are not source code and must not be tracked.

## Runtime Components

| Component | Responsibility |
| --- | --- |
| Electron renderer | Local workflows, library browsing, selection and status presentation |
| Preload bridge | Small allowlist of named renderer-to-main capabilities |
| Electron main process | IPC authorization, dialogs, process supervision and composition root |
| Task coordinator | One global heavy-task slot, cancellation and durable task snapshots |
| Process operation lock | Per-library mutual exclusion across Agent and private CLI heavy tasks |
| Scheduler | Minute-level in-app scan-plan evaluation while the app is running |
| `pptlib` CLI | Private JSON/progress process protocol used by Electron |
| `deckatlas` CLI | Versioned, machine-readable, default-read-only interface for local agents |
| Application services | Import, archive, search, compose, delete, deduplicate and plan use cases |
| SQLite | Library index, FTS, selections, scan plans, runs and desktop task state |
| Local assets | Thumbnails, previews, HTML snapshots, manifests and render metadata |
| Render adapters | Headless LibreOffice/PDFium for PPTX; isolated Chromium for HTML |
| Source folders | User-owned PPTX/HTML/ZIP files; read and hashed, never mutated |

## Dependency Direction

DeckAtlas is a modular monolith, not a network of services:

```text
Electron / CLI delivery adapters
              |
        application use cases
              |
 domain policy + adapter interfaces
              |
 SQLite / filesystem / render / archive / export adapters
```

Business decisions belong in application/domain modules, not Electron handlers or argument
parsers. Infrastructure adapters may return established application DTOs, but may not invoke
desktop or CLI delivery code. Cross-module behavior is exercised through public functions and
versioned JSON contracts rather than duplicate implementations.

## Primary Data Flows

Local source:

```text
User dialog -> validated IPC -> supervised pptlib command
  -> scan/hash -> parse -> SQLite transaction -> generated previews
  -> progress/result events -> renderer refresh
```

Online HTML:

```text
Public HTTPS URL -> public-address validation on every redirect
  -> bounded download -> immutable local archive -> sanitized page model
  -> isolated browser rendering -> SQLite + generated previews
```

Composition:

```text
Ordered slide IDs -> preflight and state token -> source/hash/dependency validation
  -> temporary output -> package validation -> atomic rename + provenance manifest
```

Agent access:

```text
Local agent -> deckatlas CLI -> query-only SQLite for reads
  -> explicit allow-write + validated local files for import
  -> shared process lock -> existing import service -> SQLite + previews
  -> explicit preflight token + allow-write for compose
  -> output restricted to PPTLIB_OUTPUT_ROOT
```

## Trust Boundaries

- Renderer to Electron: `contextIsolation` is on, Node integration is off, preload exports named
  methods only, and every IPC handler verifies the main window and main frame.
- Electron to Python: commands are spawned with `shell: false`; heavy commands are serialized and
  receive bounded cancellation.
- Application to source files: import reads and hashes sources but never modifies or removes them.
- Agent to library: search/detail use SQLite query-only mode; local file import requires
  `--allow-write` and uses the existing importer with missing-source policy KEEP. Delete, URL
  archival, repair and sync commands remain private.
- Heavy operations acquire `${PPTLIB_HOME}/.heavy-operation.lock` before initialization, rendering
  or export. A competing command fails with a retryable busy error. Lock order is operation,
  database maintenance, then SQLite. Database initialization and startup recovery wait for the
  operation lock; initialization inside a heavy task reuses that task's lock. Desktop search and
  task persistence only check migration readiness when the schema is current, so they remain
  available during heavy work. A desktop request requiring initialization fails busy rather than
  blocking progress persistence.
- Agent to filesystem: composition requires explicit write consent, a state-bound preflight token
  and a target below `PPTLIB_OUTPUT_ROOT`.
- PPTX to renderer: external media relationships are rejected before rendering.
- HTML URL to network: loopback, private, link-local and other non-public destinations are rejected
  before every connection and redirect.
- HTML content to browser: source scripts, events, forms, third-party frames and external network
  are removed or blocked; the browser uses an isolated profile and mock keychain settings.
- Custom asset protocol: only allowlisted thumbnail/preview image names below
  `${PPTLIB_HOME}/assets` are served; traversal and symlink escapes fail closed.
- Dynamic preview server: short-lived loopback-only capability URL, exact origin/token checks and
  bounded teardown; it is not a general application API.

## Persistence And Ownership

| Data | Location | Owner / lifecycle |
| --- | --- | --- |
| Library database | `${PPTLIB_HOME}/pages.db` | Application; WAL and ordered SQL migrations |
| Thumbnails/previews | `${PPTLIB_HOME}/assets` | Derived cache; removable through library flows |
| Online HTML snapshots | `${PPTLIB_HOME}/assets/html-archives` | Immutable application-managed archive |
| Selections | SQLite plus `${PPTLIB_HOME}/selections` manifests | Application-managed |
| Logs | `PPTLIB_LOG_DIR` | Local diagnostics; may contain sensitive paths |
| Temporary rendering | `PPTLIB_TEMP_DIR` | Must be cleaned after success, failure or cancellation |
| Exports | User-selected path or `PPTLIB_OUTPUT_ROOT` | User-owned output |
| Local PPTX/HTML/ZIP | Original user path | User-owned, read-only to DeckAtlas |

Migrations are append-only. A migration failure restores a verified database backup before the
application continues.

## Distribution

- Development: Python 3.11 virtual environment plus Electron/Node 20. The main process treats a
  run as packaged only when both `app.isPackaged` is true and `process.defaultApp` is unset, because
  macOS dev launches can otherwise misreport `app.isPackaged` and hide the development sidecar.
- Desktop package: Electron app plus version-matched PyInstaller `pptlib` sidecar.
- Agent runtime: separate PyInstaller `deckatlas` executable reading the same local library.
- Beta.2 distribution includes desktop and complete Agent runtime ZIPs, SHA-256 checksums,
  a source/version manifest and license texts. Runtime builders accept `PPTLIB_BUILD_ROOT`;
  release intermediates stay outside the repository. HTML remains off in packaged desktop by default.
- Website: static assets deployed independently through GitHub Pages.
- Release signing, notarization and a signed update channel are not yet production-ready.

## Known Risks And Assumptions

- `desktop/main.js` and `desktop/renderer/app.js` remain large composition/state modules. Security,
  scheduling and task control are separated, but future feature work should continue extracting
  cohesive modules instead of adding more global state.
- Dialog paths are returned to the renderer and later accepted again by import/open handlers.
  Sender checks are complete, but path-grant tracking is not; renderer compromise could request an
  existing local path. External distribution should close this before claiming hardened sandboxing.
- Online HTML support is intentionally static. Login-required pages, script-only applications,
  Canvas/WebGL and custom players are rejected or degraded rather than executed.
- Electron starts one short-lived Python process per operation. This is simple and recoverable, but
  a measured latency problem may justify a supervised persistent sidecar later.
- Preview replacement is not fully atomic in every legacy rendering path.
- Source paths, extracted text, notes, thumbnails, URLs and logs inherit presentation sensitivity.
- Desktop release version (`desktop/package.json`) and Python/Agent component version
  (`pyproject.toml`, `src/pptlib/__init__.py`) are aligned at `0.3.0-beta.2` today but remain
  separate declarations. Release automation must keep them in lockstep and define their
  compatibility explicitly before using one number as the whole-product version.
- macOS Developer ID signing, notarization, update trust and upgrade testing remain release gaps.

## Conditional Capabilities

- Scheduled work exists only inside the running Electron app; see [cron.md](cron.md).
- Agent automation exists as a bounded local CLI; see [automation.md](automation.md) and
  [agent-cli.md](agent-cli.md).
- HTML network archival and isolated rendering exist; see [html-archive.md](html-archive.md).
- No transactional email.
- No public/indexable application routes. The separate static marketing website contains only
  public product content; see [seo.md](seo.md).

## Related Documents

- [Documentation index](README.md)
- [Critical flows](flows.md)
- [Permissions](permissions.md)
- [Variables](variables.md)
- [Scheduled work](cron.md)
- [Automation boundary](automation.md)
- [Static website and public content](seo.md)
- [Test coverage](tests.md)
- [HTML archive and composition](html-archive.md)
- [Agent CLI contract](agent-cli.md)
- [Product and engineering roadmap](../docs/DeckAtlas-productization-engineering-roadmap.md)
