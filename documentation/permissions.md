# Permissions

## Current Model

PinPage is a single-user local desktop application. It has no application account roles and no
remote multi-tenant data plane.

| Resource | Read | Write | Delete |
| --- | --- | --- | --- |
| User-selected source folders | Desktop user | Never | Never |
| SQLite library | Local application | Local application | Local application |
| Generated previews/cache | Local application | Local application | Local application |
| Export destination | Local application after user selection | Local application | Not automatic |
| Electron IPC | Main renderer only for guarded methods | Named methods only | Named methods only |

## Enforcement

- Electron uses `contextIsolation: true` and `nodeIntegration: false`.
- Renderer capabilities are allowlisted in `desktop/preload.js`.
- New automatic-update IPC handlers verify the sender is the main window and main frame.
- CLI subprocesses are spawned with `shell: false`.
- Scanner ignores symlinks and temporary/incomplete files.
- Import and cleanup code never deletes source PPTX/HTML files.

## Gaps Before External Distribution

- Apply `isMainSender` to every existing IPC handler, not only new sensitive handlers.
- Track paths returned by file dialogs and reject renderer-supplied paths outside those grants.
- Restrict `pptlib-asset://` to `${PPTLIB_HOME}/assets`; it currently accepts an encoded absolute path.
- Define a signed update trust policy before enabling automatic application updates.
- If a shared or cloud edition is introduced, this document must be replaced with an authenticated
  tenant/resource permission matrix. The current local model is not sufficient for multi-user use.

## Data Classification

- Source presentations may contain confidential business information.
- Page text, notes, absolute paths and thumbnails inherit the source document's sensitivity.
- Logs and diagnostic bundles must redact user names, absolute path prefixes and URL query values
  before they leave the device.
