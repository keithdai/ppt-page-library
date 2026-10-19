# DeckAtlas Agent CLI

`deckatlas` is the stable, machine-readable interface for local AI agents. The existing `pptlib`
command remains the private process interface used by the Electron application.

## Contract

- Protocol: `deckatlas.agent/1.0`
- One invocation produces exactly one compact JSON response on stdout.
- Errors use `{code, message, retryable, details}` and a non-zero exit code.
- Search and slide inspection are read-only and never initialize or migrate the library.
- `compose run` is the only write operation. It requires an explicit write flag and a token from a
  successful preflight.
- Export destinations must be inside `PPTLIB_OUTPUT_ROOT`.
- Source presentations are never modified.

## Commands

```bash
deckatlas capabilities

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
