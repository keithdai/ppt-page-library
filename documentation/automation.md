# Automation And Agent Boundary

DeckAtlas does not embed an LLM or run autonomous decision-making. It exposes a deterministic local
CLI that an external AI agent may call under the operating-system user's authority.

## Agent Surface

| Command | Reads | Writes | Hard guardrail |
| --- | --- | --- | --- |
| `capabilities` | Effective local capability/configuration | Nothing | Fixed versioned response |
| `search` | Query-only library metadata and preview paths | Nothing | Length, page and page-size limits |
| `slide get` | One current slide's extracted text/metadata | Nothing | Slide ID and current-version check |
| `compose preflight` | Strict selection manifest and source state | Nothing | Schema, size, source and output validation |
| `compose run` | Same state as preflight | Output presentation + manifest | Token, `--allow-write`, output-root and overwrite checks |

Protocol: `deckatlas.agent/1.0`. One process invocation returns one compact JSON response on stdout.
The private `pptlib` CLI is not an Agent contract and may evolve with the desktop application.

## Steering Versus Enforcement

The calling agent decides what to search for and which pages to select. DeckAtlas does not trust the
agent's prompt or narrative as authorization.

Code-enforced controls:

- Search/detail open SQLite query-only and do not initialize or migrate a library.
- Source paths are withheld from normal search/detail responses.
- The strict selection schema has byte and page-count limits.
- Preflight token binds manifest content, output target, source files and existing output state.
- Write requires `--allow-write`; overwrite requires a separate explicit flag.
- Output must resolve below `PPTLIB_OUTPUT_ROOT`.
- No Agent command can import, archive URLs, delete, deduplicate, repair, sync or manage schedules.
- Original presentation sources are never write targets.

## Ownership And Failure

- Agent-owned: search terms, filters, selection order and whether to request a write.
- DeckAtlas-owned: validation, query execution, output generation, atomic publication and error
  schema.
- Failures return a non-zero documented exit code and structured `{code, message, retryable,
  details}`. A preflight with `ready: false` is a successful read and returns exit code 0.
- There is no automatic retry inside DeckAtlas. The caller may retry only when `retryable` is true.
- There is no remote audit service. Callers should supply `--request-id`; local process/log
  retention follows `PPTLIB_LOG_DIR`.
- Kill switch: stop invoking the executable or revoke filesystem access to the local library/output
  root.

## Non-Agent Automation

The Electron scheduler is deterministic application automation, not an AI agent. Its inventory,
limits, idempotency and cancellation contract are documented in [cron.md](cron.md).

Full command schemas and examples: [agent-cli.md](agent-cli.md).
