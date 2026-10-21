# DeckAtlas Engineering Guide

This file defines the repository rules for maintainers and coding agents.

## Product Boundary

DeckAtlas is a local-first macOS presentation library. It indexes user-selected PPTX and HTML
sources, generates local previews, supports page-level search and selection, and composes new
presentations. Source files are user-owned and read-only.

The current architecture and code map live in
[`documentation/architecture.md`](documentation/architecture.md). Read that file before changing
runtime behavior.

## Single Sources Of Truth

- Active Python product code: `src/pptlib/`.
- Active desktop code: `desktop/`.
- Packaging definitions: `packaging/`.
- Stable system facts: `documentation/`.
- Current product specifications and user guides: `docs/`.
- Frozen verification evidence: `docs/acceptance/`.
- Superseded plans and historical designs: `docs/_archive/`; never use them as implementation truth.
- User data and generated assets: `${PPTLIB_HOME}`; never commit them.
- Delivery packages: local `交付/`; never treat them as source code.

Do not create parallel implementations, source snapshots, or `*.bak` / `*.old` files. Use Git
history and worktrees.

## Compatibility Rules

- Keep internal identifiers stable: `pptlib`, `PPTLIB_*`, `com.pptlib.desktop`, SQLite paths and
  existing migration history. DeckAtlas is the external product name.
- Never modify, move, overwrite, or delete imported PPTX/HTML source files.
- Library deletion removes only SQLite records and generated cache. Saved selections must block
  unsafe deletion.
- Do not add cross-file page deduplication without an explicit product decision.
- Schema changes are append-only migrations under `src/pptlib/migrations/`.
- Agent-facing behavior belongs to the versioned `deckatlas` CLI. Electron continues to use the
  private `pptlib` process protocol.
- Browser automation must use an isolated profile plus `--password-store=basic` and
  `--use-mock-keychain`, and must be stopped after verification.

## Architecture Rules

- `domain/` contains policy and value rules without infrastructure dependencies.
- `application/` orchestrates use cases.
- `infrastructure/`, `rendering/`, `html/`, `ingestion/` and `export/` implement adapters.
- `cli.py`, `agent_cli.py` and Electron IPC are delivery adapters; business rules do not belong
  there.
- Electron renderer code never receives Node.js access. Every IPC handler verifies the main
  window and main frame, validates identifiers or dialog-granted paths, and spawns without a shell.
- Heavy import, render, compose, delete and maintenance operations remain globally serialized.
- HTML URL archival rejects private/local network destinations and never executes source scripts.

## Documentation Rules

Update documentation in the same change as behavior:

| Change | Required documentation |
| --- | --- |
| Runtime component, trust boundary or persistent data | `documentation/architecture.md` |
| Protected workflow or side effect | `documentation/flows.md` |
| IPC, filesystem or Agent permission | `documentation/permissions.md` |
| Environment variable or secret | `documentation/variables.md` |
| Scheduled/background behavior | `documentation/cron.md` |
| Agent or deterministic automation boundary | `documentation/automation.md` |
| Public website route, metadata or asset | `documentation/seo.md` |
| User-visible HTML archive contract | `documentation/html-archive.md` |
| Agent CLI contract | `documentation/agent-cli.md` |
| Verification coverage or gap | `documentation/tests.md` |

Completed implementation plans move to `docs/_archive/`. Test evidence goes to
`docs/acceptance/`; do not rewrite frozen evidence.

## Quality Gate

Run before committing:

```bash
make quality
```

This checks repository boundaries, documentation links, formatting, type safety, all Python tests,
desktop tests and whitespace errors. Release candidates also require real PPTX/HTML import,
compose, re-import, package, signing and notarization checks described in `documentation/tests.md`.

## Closeout

Answer these before declaring work complete:

1. Were one-off scripts, browser profiles, caches, source snapshots and test outputs removed?
2. Did this change create a second source of truth or bypass an existing boundary?
3. Were architecture, contracts, risks and verification maps updated where behavior changed?
