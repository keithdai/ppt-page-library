# Architecture

## Product

PinPage is a local-first macOS desktop application for indexing presentation pages, searching and
selecting them, and composing selected PPTX pages into a new native OOXML presentation.

Key assumptions:

- Source PPTX/HTML files remain in their original local locations.
- SQLite and generated previews are local application data.
- PPTX preview rendering uses headless LibreOffice by default.
- The Electron application must be running for scheduled work.

## Runtime Components

| Component | Responsibility |
| --- | --- |
| Electron renderer | User workflows, local page browsing, task status and configuration |
| Electron main process | IPC boundary and composition root for security, task and scheduling modules |
| Desktop state service | Paginated SQLite reads, persisted selection and desktop task snapshots |
| `pptlib` CLI | Stable process interface used by Electron |
| Python application services | Import, preview planning, compose, deduplication and storage behavior |
| SQLite | Deck/version/page index, selections, plans and run history |
| Local assets | Thumbnails, previews, HTML manifests and render metadata |
| Source folders | User-owned PPTX/HTML sources; treated as read-only |

## Main Data Flow

```text
Renderer action
  -> allowlisted preload method
  -> validated Electron IPC handler
  -> supervised pptlib child process
  -> application service
  -> SQLite + local assets + read-only source files
  -> JSON result/progress events
  -> renderer state refresh
```

## Trust Boundaries

- Renderer to Electron: context isolation is enabled; only named preload methods are exposed.
- Electron to CLI: arguments are passed without a shell; heavy commands are globally serialized.
- CLI to source files: source files are read and hashed but must not be changed or deleted.
- CLI to LibreOffice: rendering is headless and uses isolated profile/cache directories.
- HTML previews use a dedicated, short-lived loopback server rather than a general application API.
- Custom asset protocol: serves only allowlisted image files below the configured assets directory;
  traversal and symlink escapes fail closed.

## Persistence

- Database: `${PPTLIB_HOME}/pages.db`, WAL mode.
- Page browsing is paginated (100 pages per desktop request); the renderer keeps only loaded pages.
- The default ordered selection and desktop task lifecycle are stored in SQLite.
- Generated assets: `${PPTLIB_HOME}/assets`.
- Temporary render files: configured `PPTLIB_TEMP_DIR`.
- Export output: user-selected location.
- Schema changes: ordered SQL migrations under `src/pptlib/migrations`.

## Known Risks / Assumptions

- Packaged Electron launches a versioned PyInstaller sidecar from
  `Contents/Resources/runtime/pptlib/pptlib`; development mode continues to use `.venv/bin/pptlib`.
- PDF rasterization is bundled through PDFium. LibreOffice remains an explicit, first-run checked
  system dependency.
- Electron still launches one short-lived CLI process per desktop database operation. A persistent
  sidecar can be considered only if measured interaction latency becomes a problem.
- Renderer view state remains in a single JavaScript module; security, task coordination and
  scheduling boundaries are enforced in separate main-process modules.
- Preview replacement is not yet fully atomic across all rendering paths.
- macOS signing, notarization and update delivery are not configured.

## Conditional Capabilities

- Scheduled work exists; see [cron.md](cron.md).
- No general-purpose HTTP application server.
- No transactional email.
- No public/indexable routes requiring SEO.
- No embedded LLM agent or autonomous external tool-calling workflow.

## Related Documents

- [Product and engineering roadmap](../docs/PinPage-productization-engineering-roadmap.md)
- [Critical flows](flows.md)
- [Permissions](permissions.md)
- [Variables](variables.md)
- [Scheduled work](cron.md)
- [Test coverage](tests.md)
