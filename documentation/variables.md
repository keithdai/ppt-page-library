# Configuration And Variables

PinPage currently has no required cloud secret. Configuration is read by the Python process and
inherited by supervised Electron child processes.

| Name | Used by | Source | Scope | Risk / rule |
| --- | --- | --- | --- | --- |
| `PPTLIB_HOME` | Python, Electron | Environment/default | Local | Contains DB and generated assets; must be writable |
| `PPTLIB_OUTPUT_ROOT` | Python | Environment/default | Local | Default export location; never overwrite without explicit output |
| `PPTLIB_LOG_DIR` | Python | Environment/default | Local | May contain paths and errors; redact before sharing |
| `PPTLIB_TEMP_DIR` | Renderers | Environment/default | Local | Must be cleaned after success/failure |
| `PPTLIB_RENDERER` | PPTX renderer | Environment/default | Local | `auto`, `libreoffice`, or `officecli` |
| `PPTLIB_ENABLE_HTML` | Import/desktop | Environment | Local | Enables HTML/ZIP ingestion and preview |
| `PPTLIB_MAX_FILE_BYTES` | Scanner | Environment/default | Local | System safety ceiling |
| `PPTLIB_MAX_UNCOMPRESSED_PACKAGE_BYTES` | Parser/exporter | Environment/default | Local | ZIP bomb protection |
| `PPTLIB_MAX_PARTS_PER_PACKAGE` | Parser/exporter | Environment/default | Local | Package complexity protection |
| `PPTLIB_REPO_ROOT` | Electron | Environment/persisted choice | Development | Development checkout override; packaged app uses its bundled runtime |
| `PPTLIB_ELECTRON_USER_DATA` | Electron | Environment | Test only | Isolates Electron profile and single-instance lock for smoke tests |
| `PPTLIB_SMOKE` | Electron | Environment | Test only | Starts then exits for smoke verification |

## Secrets

- No API key, password or access token is required for the local workflow.
- No secret should be exposed through preload or bundled renderer JavaScript.
- Optional Miaoda/Lark synchronization uses the external CLI login context and must not copy tokens
  into project files, logs or renderer state.

## Pre-release Checklist

- Keep the PyInstaller runtime and Electron package versioned together.
- Store user-facing settings in a versioned configuration service, not ad hoc environment variables.
- Validate every configured path and show its effective value in Settings.
- Add “reset to default” and export/import settings.
- Redact sensitive paths from support bundles.
