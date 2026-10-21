# Permissions And Local Data Access

## Current Model

DeckAtlas is a single-user local application. It has no remote accounts, application roles or
multi-tenant data plane. Authority comes from the operating-system user, Electron capability
boundaries and explicit file dialogs/CLI flags.

| Resource | Desktop renderer | Electron main / `pptlib` | Agent CLI |
| --- | --- | --- | --- |
| Local source files | Receives selected paths | Read/hash only | Not exposed |
| Online HTML URL | Submit public URL | Bounded static archive | Not exposed for import |
| SQLite library | No direct access | Read/write through use cases | Query-only for search/detail |
| Generated assets | Opaque asset URLs | Create/read/delete cache | Existing preview paths may be returned |
| Saved selection | Named IPC | Read/write | Caller provides strict manifest |
| Export destination | System dialog | Explicit write | Write only below `PPTLIB_OUTPUT_ROOT` |
| Source deletion | Never | Never | Never |
| Library deletion | Confirmation UI | IDs only; cache/index only | Never |
| Maintenance/import/sync | Named IPC/CLI | Explicit command | Not exposed |
| Dynamic preview | Request known slide ID | Short-lived sandbox server/window | Not exposed |

There is no database row-level security. All protection is local process and code-enforced.

## Enforcement

- Electron uses `contextIsolation: true`, `nodeIntegration: false` and an explicit preload bridge.
- Every registered IPC handler calls `requireMainSender`; unit tests cover foreign sender/frame
  rejection.
- CLI subprocesses use argument arrays with `shell: false`.
- Heavy mutations share one task coordinator.
- Asset protocol accepts only fixed thumbnail/preview relative names under the resolved assets root.
- HTML preview uses a sandbox window without preload and an exact loopback capability boundary.
- Scanner rejects symlinks and temporary/incomplete sources.
- Import/cleanup code never deletes source PPTX/HTML/ZIP files.
- `deckatlas` omits import, archive, delete, repair, sync and plan-maintenance commands.
- Agent reads open SQLite with `mode=ro`/query-only behavior.
- Agent export requires a strict selection schema, `--allow-write`, matching preflight token and
  destination below `PPTLIB_OUTPUT_ROOT`.

## Filesystem Grants

System dialogs establish user intent for source, folder, manifest and output paths. Current Electron
code verifies the IPC sender but does not persist a formal path-grant ledger. Import, reveal and
open-path handlers therefore rely on the renderer returning paths originally supplied by the main
process.

Before claiming hardened external distribution:

- Persist dialog grants in the main process and reject renderer paths outside active grants.
- Restrict reveal/open-path to known source, export and generated-asset identities.
- Add negative tests for stale grants, symlink swaps and renderer-supplied absolute paths.
- Define signed update provenance and rollback policy.

## Data Classification

- Source presentations may contain confidential business information.
- Extracted text, notes, absolute paths, origin URLs, thumbnails and previews inherit source
  sensitivity.
- SQLite and archived online resources must stay local unless the user deliberately exports them.
- Logs and support bundles must redact user names, absolute path prefixes, URL query/fragment values
  and extracted presentation text before leaving the device.
- The optional Miaoda sync command uploads thumbnails and selected metadata only; it must use the
  external CLI login context and never copy tokens into DeckAtlas files.

## Future Shared Editions

A shared/cloud edition would require authenticated tenants, encrypted transport/storage,
resource-scoped authorization, audit logs and a new permission matrix. This local permission model
must not be reused or described as sufficient for multi-user deployment.
