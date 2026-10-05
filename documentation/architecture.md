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
| Electron main process | IPC boundary, file dialogs, process supervision, scheduling and task mutex |
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
- Custom asset protocol: currently serves local paths produced by the catalog. It should be narrowed
  to the configured assets directory before external distribution.

## Persistence

- Database: `${PPTLIB_HOME}/pages.db`, WAL mode.
- Generated assets: `${PPTLIB_HOME}/assets`.
- Temporary render files: configured `PPTLIB_TEMP_DIR`.
- Export output: user-selected location.
- Schema changes: ordered SQL migrations under `src/pptlib/migrations`.

## Known Risks / Assumptions

- Packaged Electron launches a versioned PyInstaller sidecar from
  `Contents/Resources/runtime/pptlib/pptlib`; development mode continues to use `.venv/bin/pptlib`.
- PDF rasterization is bundled through PDFium. LibreOffice remains an explicit, first-run checked
  system dependency.
- Large catalogs are regenerated and loaded as one JSON document by the desktop app.
- Renderer state is held in global JavaScript objects; feature boundaries are not enforced.
- Task supervision is in Electron memory while run history is in SQLite; a unified persistent job
  state machine remains future work.
- Preview replacement is not yet fully atomic across all rendering paths.
- macOS signing, notarization and update delivery are not configured.

## Conditional Capabilities

- Scheduled work exists; see [cron.md](cron.md).
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
