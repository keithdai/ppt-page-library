# Configuration And Variables

DeckAtlas requires no cloud secret for local use. Python reads environment variables and Electron
passes a controlled environment to supervised child processes.

## Runtime Variables

| Name | Used by | Default/source | Rule or risk |
| --- | --- | --- | --- |
| `PPTLIB_HOME` | Python, Electron, Agent CLI | App data directory | Owns DB, archives and generated assets; must be writable |
| `PPTLIB_OUTPUT_ROOT` | Python, Agent CLI | User Documents export folder | Hard boundary for Agent writes |
| `PPTLIB_LOG_DIR` | Python, Electron | User log directory | May contain sensitive paths/errors |
| `PPTLIB_TEMP_DIR` | Render/archive/export | User cache directory | Clean on success, failure and cancellation |
| `PPTLIB_RENDERER` | PPTX renderer | `libreoffice` | `libreoffice`, `auto` or `officecli` only |
| `PPTLIB_ENABLE_HTML` | Python, Electron | CLI off; desktop dev on; package off | Enables local/online HTML archive and HTML output |
| `PPTLIB_HTML_BROWSER` | HTML renderer | Bundled Playwright Chromium | Optional explicit browser executable for development |
| `PPTLIB_MAX_WORKERS` | Worker runtime | `1` | Valid range 1-2; heavy desktop work remains serialized |
| `PPTLIB_JOB_LEASE_SECONDS` | Worker queue | `120` | Positive lease duration |
| `PPTLIB_THUMBNAIL_LONG_EDGE` | Renderers | `640` | Positive pixel size |
| `PPTLIB_PREVIEW_LONG_EDGE` | Renderers | `1920` | Positive pixel size; mixed output relies on this preview |
| `PPTLIB_MAX_FILE_BYTES` | Scanner | 3 GiB | Rejects oversized local source |
| `PPTLIB_MAX_UNCOMPRESSED_PACKAGE_BYTES` | Parser/exporter | 2 GiB | ZIP expansion protection |
| `PPTLIB_MAX_PARTS_PER_PACKAGE` | Parser/exporter | 20,000 | Package complexity protection |
| `PPTLIB_REPO_ROOT` | Electron | Auto-detected/persisted | Development checkout override; package uses bundled runtime |
| `PPTLIB_ELECTRON_USER_DATA` | Electron | Electron user data | Test-only isolated profile and instance lock |
| `PPTLIB_SMOKE` | Electron | unset | Test-only bounded startup/exit hook |
| `PPTLIB_EXPERIMENTAL_LABEL` | Electron | unset | Development-only UI label; never shown in packaged app |
| `PPTLIB_BUILD_ROOT` | Runtime build scripts | Repository `build/` | Optional absolute build/smoke directory; release builds use a temporary location |

Defaults owned by Python live in `src/pptlib/default.toml`. Electron-only defaults live in
`desktop/main.js`. Adding or changing a variable requires updating this document and tests.

## Persistent User Settings

The packaged desktop app stores user-selected repository/data compatibility settings in Electron
user data. These are local configuration, not secrets. Schema and migration behavior must remain
backward compatible before adding more persisted settings.

## Secrets

- No API key, password or access token is required for import, search, preview or compose.
- No secret may be exposed through preload or bundled renderer JavaScript.
- Optional Miaoda/Lark synchronization uses the external `lark-cli` login context. DeckAtlas must
  not read, copy or log its tokens.
- Git tracks no `.env` file. Only a documented `.env.example` may be added if a future feature
  genuinely needs one.

## Pre-release Checklist

- Confirm runtime and Electron versions are built from the same revision.
- Confirm effective paths in Settings and migration from legacy internal directories.
- Run support-bundle/path redaction tests.
- Verify all browser launches use isolated profiles, `--password-store=basic` and
  `--use-mock-keychain`; terminate residual test browsers.
- Verify signing/notarization secrets exist only in the release environment and are never bundled.
- Exercise reset/default and failed-migration recovery against a copy of a prior user database.
