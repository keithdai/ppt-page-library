# Maintainer Documentation

`documentation/` is the single source of truth for the current DeckAtlas implementation. It is
written for maintainers, reviewers and the next coding agent.

## Current System Map

| Document | Purpose |
| --- | --- |
| [architecture.md](architecture.md) | Product boundary, code map, runtime components and risks |
| [flows.md](flows.md) | Security- and integrity-sensitive runtime flows |
| [permissions.md](permissions.md) | Filesystem, IPC and Agent capability matrix |
| [variables.md](variables.md) | Runtime configuration and secret boundaries |
| [cron.md](cron.md) | In-app scheduled scanning and cancellation rules |
| [automation.md](automation.md) | Agent CLI and deterministic automation guardrails |
| [seo.md](seo.md) | Static website public-content and deployment boundary |
| [tests.md](tests.md) | Existing verification, release gates and unverified risks |
| [html-archive.md](html-archive.md) | HTML import, URL archive and composition contract |
| [agent-cli.md](agent-cli.md) | Versioned local interface for AI agents |

DeckAtlas sends no transactional email, so there is no `emails.md`. The static marketing site under
`website/` is documented separately from the local application in `seo.md`.

## Documentation Layers

- `documentation/`: stable current facts and contracts.
- `docs/`: current product requirements, roadmap and user guidance.
- `docs/acceptance/`: frozen reports and reproducible release evidence.
- `docs/_archive/`: superseded plans, old mockups and historical designs.
- `README.md`: product overview and common development entry points.
- `AGENTS.md`: repository operating rules derived from the current architecture.

Plans and status notes do not belong in `documentation/`. Historical files remain available for
traceability but must not be used as the implementation baseline.

## Update Matrix

| Code change | Update with it |
| --- | --- |
| Service, adapter, storage or trust boundary | `architecture.md`, `flows.md` |
| IPC, filesystem or Agent capability | `permissions.md`, `tests.md` |
| Environment variable or default path | `variables.md` |
| Scheduler, queue, retry or cancellation | `cron.md`, `tests.md` |
| Agent/automation command or guardrail | `automation.md`, `agent-cli.md`, `tests.md` |
| Public website route, asset or metadata | `seo.md` |
| HTML format/input/output behavior | `html-archive.md`, `tests.md` |
| Agent CLI schema or command | `agent-cli.md`, `permissions.md`, `tests.md` |
| Release verification | New immutable file under `docs/acceptance/` |

Run `make repo-check` after moving or adding documentation. It rejects broken current-document
links and tracked runtime/delivery artifacts.
