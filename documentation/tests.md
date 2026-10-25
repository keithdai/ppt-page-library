# Test And Release Verification Map

`make quality` is the required local and CI gate. It checks repository boundaries, current
documentation links, Python formatting/lint/type safety, all Python tests, all desktop tests and
whitespace. Platform-specific release evidence remains a separate macOS release gate.

## Existing Automated Coverage

| Use case | Rule / negative behavior pinned | Evidence |
| --- | --- | --- |
| Repository governance | Generated/user/delivery artifacts are not tracked; current doc links resolve | `scripts/check_repository.py` |
| Scan filtering | Unsupported, temporary, oversized and symlink sources are rejected before hashing | `tests/unit/discovery/test_scanner.py` |
| Import identity | Unchanged files skip work; moved content keeps identity; source drift fails closed | Import and scan-plan unit/integration tests |
| Database safety | Ordered migrations, rollback and verified backup recovery | `tests/integration/db/`, `tests/unit/test_bootstrap.py` |
| Taxonomy/search | Deterministic classification, filters, CJK search and pagination | Domain, application and ingestion tests |
| PPTX parser safety | Unsafe external media/package limits are rejected | `tests/unit/ingestion/test_parser.py` |
| PPTX rendering | Headless safe environment, segmentation, cache integrity and PDFium mapping | `tests/unit/rendering/test_thumbnails.py` |
| Local HTML ingestion | Recognized pages, dependencies, notes, sanitization and ZIP constraints | `tests/unit/ingestion/test_html*.py` |
| Online HTML archive | Public-network checks, redirects, limits, immutable snapshots and failures | `tests/unit/ingestion/test_remote_html.py` |
| HTML round trip | Archive, import, compose and re-import keep page order and content | `tests/integration/test_html_roundtrip.py` |
| HTML/mixed compose | Format compatibility, media, namespace isolation, rollback and source protection | `tests/integration/test_html_compose.py` |
| PPTX compose | Order, reachable OOXML dependencies, system-color freeze, source hash and atomic output | `tests/unit/export/test_ooxml.py` |
| Agent CLI | Strict protocol/schema, query-only reads, revision checks and bounded writes | `tests/unit/application/test_agent.py`, `tests/integration/test_agent_cli.py` |
| Delete behavior | Cache/index removal only; saved selections block unsafe deletion | `tests/unit/application/test_delete.py` |
| Automatic update | Plans, occurrence dedupe, moves, retries, stop and history | `tests/unit/application/test_scan_plans.py`, desktop tests |
| Electron boundary | Sender/frame checks, asset confinement, sandbox preview and named preload methods | `desktop/architecture.test.js`, `html-preview.test.js` |
| Desktop HTML workflow | URL typing, mixed import, format picker, stale preflight and feature gate | `desktop/html-archive.test.js` |
| Task coordination | One heavy task, durable start/finish, cancellation and bounded quit | `desktop/architecture.test.js`, desktop tests |
| Package content | Sidecar/config/migrations included and executable | `tests/integration/db/test_packaging.py`, build scripts |

The current HTML branch contains 362 Python tests and 42 Node tests. Counts are diagnostic, not a
quality target; rules and negative cases above are the maintained contract.

## CI Merge Gate

`.github/workflows/quality.yml` runs on pull requests and pushes to `main` using Python 3.11 and
Node 20:

1. Repository and documentation checks.
2. Ruff lint for the Python codebase and formatter enforcement for governance scripts. Existing
   application code predates a single Ruff-format baseline; that mechanical migration stays
   separate from behavior changes.
3. Strict mypy.
4. Full Python unit/integration suite.
5. Desktop Node tests.
6. `git diff --check`.

GitHub Pages deployment remains isolated in `pages.yml` and runs only for website changes.

## Release-Candidate Gate

Required on a clean macOS release machine:

| Check | Expected evidence |
| --- | --- |
| Real PPTX import/render/search | Sanitized fixture manifest, command output and visual inspection |
| Native PPTX compose/re-import | Page order, dimensions, OOXML package and visual comparison |
| Local/online HTML archive | Public test URL, dependency manifest, offline preview and re-import |
| Mixed HTML output | Full-resolution PPTX fallback and HTML media/interaction verification |
| Large-library budget | 10k-page startup, search, memory and scrolling measurements |
| Cancellation | Stop during real LibreOffice/Chromium work; no orphan process or partial publish |
| Packaging | Version-matched Electron and Python sidecars, clean install and upgrade |
| Security | Path grants, protocol traversal, private-network redirects and diagnostic redaction |
| Distribution | Developer ID signature, hardened runtime, notarization and Gatekeeper |
| Upgrade/recovery | Previous production DB, migration failure restore and settings preservation |
| Accessibility | Keyboard-only, focus order, VoiceOver and dynamic text/layout review |

Create a new immutable report under `docs/acceptance/`; do not edit prior evidence.

## Proposed Tests

| Type | Use case | Expected behavior | Priority |
| --- | --- | --- | --- |
| Automated Electron integration | Dialog path-grant ledger | Import/open/reveal reject non-granted and stale paths | P0 |
| Automated integration | Stop real LibreOffice/Chromium child | Temp cleanup completes; next source never starts | P0 |
| Automated performance | 10k-page catalog | Published startup/search/memory budgets remain green | P1 |
| Automated visual | 1120/1280/1600 widths | No clipping, overlap or unusable controls | P1 |
| Guarded macOS | Signed/notarized upgrade | Data/settings preserved; update provenance verified | P1 |
| Automated release | Desktop/runtime/Agent compatibility manifest | Release components declare compatible versions | P1 |
| Manual accessibility | VoiceOver and keyboard-only | All actions/dialogs are operable and announced | P1 |

## Unverified Risks

| Severity | Gap | Exposure |
| --- | --- | --- |
| High | Renderer-to-filesystem path grants are not persisted | Compromised renderer could request an existing local path |
| High | Signed update chain is not implemented | External distribution cannot guarantee update provenance |
| High | Real child-process cancellation is not end-to-end automated | Orphan process or partial cache after forced exit |
| Medium | 10k-page performance budget is not continuously measured | Latency or memory regression in daily use |
| Medium | Diagnostic bundle redaction has no automated proof | Sensitive paths/content may leak in support material |
| Medium | Product and Python component versions are not governed together | Ambiguous package compatibility and support reports |
| Medium | Accessibility is manual only | Keyboard or screen-reader regression |

## Static Website Verification

The public site contract is described in [seo.md](seo.md). Website changes require manual browser
inspection in addition to `make quality`:

- Inspect the pain → workflow → quick start → CLI → download narrative and retained screenshot.
- Inspect the four illustrative pain scenes, captions and workflow links. Scroll each lazy image
  into view and confirm it loads; no scene may be an empty placeholder. Check two-column desktop
  and one-column mobile layouts without clipping, including dark colors and blocked images.
- Check anchors, image loading and page overflow at 320, 390, 768, 1120 and 1440 pixel widths.
  Wide code blocks and the format table may scroll inside their own containers.
- Exercise copy success and denied-clipboard selection fallback, FAQ expansion, keyboard skip
  link, dark colors and reduced-motion behavior.
- Parse shell/JSON examples without executing imports or exports; compare commands to the
  versioned Agent CLI contract and label unreleased features explicitly.
- Verify public download availability before enabling installer or CLI package links.

These browser and example checks are manual, not CI-enforced regression tests. Clipboard success
and rejection were exercised with controlled API responses; OS clipboard integration, a complete
fresh-machine installation and VoiceOver still require separate release verification.
