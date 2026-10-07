# Test Coverage Map

## Existing Coverage

| Use case | Rule | Expected behavior | Evidence | Status |
| --- | --- | --- | --- | --- |
| Scan filtering | Format/size/temporary files are filtered before hashing | Accepted and rejected sets include reasons | `tests/unit/discovery/test_scanner.py` | Existing |
| Plan validation | Roots cannot overlap; schedule must be inside window | Invalid plans are rejected before save | `tests/unit/application/test_scan_plans.py` | Existing |
| Safe missing source | Default automatic update does not remove library content | Current deck remains visible | `tests/unit/application/test_import_progress.py` | Existing |
| Cross-root move | A moved file keeps deck identity | One deck remains at the new path | `tests/unit/application/test_scan_plans.py` | Existing |
| Retry | Retry scope contains only failed/cancelled paths | Successful files are not rerun | `tests/unit/application/test_scan_plans.py` | Existing |
| Rendering | LibreOffice is headless and uses safe environment | No UI flags; renderer behavior verified | `tests/unit/rendering/test_thumbnails.py` | Existing |
| Compose order | Output follows selected order | Manifest and presentation page order match | `tests/unit/export/test_ooxml.py` | Existing |
| Compose size | Unselected slides are not retained | Output contains only selected slide parts | `tests/unit/export/test_ooxml.py` | Existing |
| Source integrity | Changed source is rejected | No output replaces a valid prior result | `tests/unit/export/test_ooxml.py` | Existing |
| Export preflight binding | Manifest/output changes invalidate confirmation | Existing output is never silently replaced | `tests/unit/export/test_ooxml.py`, real compose smoke | Existing |
| Database migration recovery | Failed migration restores verified backup | Last valid database remains readable | `tests/unit/test_bootstrap.py` | Existing |
| Preview cache refresh | Failed refresh preserves complete cache | Existing thumbnails and previews remain usable | `tests/unit/rendering/test_thumbnails.py` | Existing |
| Bundled PDF rasterizer | PDF pages map to expected slide positions | No `pdftoppm` executable is required | `tests/unit/rendering/test_thumbnails.py` | Existing |
| First-run and export UI | Onboarding and preflight states are actionable | Blockers disable export; selection changes invalidate checks | `desktop/html-preview.test.js` | Existing |
| IPC surface | Automatic-update IPC handlers exist | Main process registers required channels | `desktop/html-preview.test.js` | Existing |
| Scheduler | Normal and cross-midnight windows do not duplicate | Due checks use scheduled occurrence | `desktop/html-preview.test.js` | Existing |
| Package content | Built app contains UI and Python sidecar | `app.asar`, CLI, config and migrations are present | Build verification | Existing/manual |

## Proposed Tests

| Type | Use case | Expected behavior | Priority |
| --- | --- | --- | --- |
| Automated integration | Stop during LibreOffice rendering | Current file cleans up; next file never starts | P0 |
| Automated E2E | Create plan → preflight → save → run → history | Complete desktop workflow succeeds | P0 |
| Automated E2E | Compose from UI with native dialogs | Selected order, output path and completion state match | P1 |
| Automated visual | 1120/1280/1600 widths | No clipped controls or overlapping text | P1 |
| Automated performance | 10k-page catalog | Startup/search meet performance budgets | P1 |
| Guarded live | Signed/notarized installer update | Upgrade preserves data and settings | P1 |
| Manual review | VoiceOver and keyboard-only workflow | All actions and dialogs are operable | P1 |

## Gaps

| Risk | Missing verification | Exposure |
| --- | --- | --- |
| High | End-to-end cancellation with real LibreOffice child processes | Orphan process or partial cache |
| High | Signed update chain | Untrusted or broken desktop updates |
| Medium | Asset protocol path-boundary test | Local file disclosure if renderer is compromised |
| Medium | Large catalog memory and latency benchmark | Slow or unstable daily use |
| Medium | Diagnostic bundle redaction | Sensitive path/content leakage |

## Merge Gate Recommendation

Required on every change:

- Ruff.
- Full Python suite.
- Node desktop tests.
- `git diff --check`.
- Electron directory build.

Required for release candidates:

- Real import, compose and re-import smoke dataset.
- LibreOffice + bundled PDFium render verification.
- Keyboard/accessibility pass.
- Signed/notarized package verification.
- Upgrade from the previous production database schema.
