# Beta.2 package verification

## Result and scope

The rebuilt arm64 desktop and Agent runtime execute on the build Mac. The Agent import/search/
compose/re-import loop and mixed HTML export passed with synthetic fixtures. This is limited
release evidence, not a clean-machine, production-security or all-platform certification.

Version: `0.3.0-beta.2`. Source: the release tag `v0.3.0-beta.2`; the attached release manifest
records its exact build commit. Environment: macOS 26.6.2 (Darwin 25.6), Apple Silicon, Python 3.11.15,
Electron 31.7.7, locally installed LibreOffice and Google Chrome.

## Verification

| Check | Observed result |
| --- | --- |
| Local quality gate | 376 Python tests and 42 desktop tests pass, with lint/types/repository checks |
| Fresh Python runtime build | PyInstaller completes; bundled `pptlib doctor` reports rendering ready |
| Fresh Agent runtime build | Capabilities succeeds, reports beta.2, includes `import` |
| Desktop package | arm64 app includes freshly built sidecar, version and third-party notices |
| Desktop startup | Isolated profile/library with `PPTLIB_SMOKE=1`; bounded startup exits 0 |
| Signature integrity | `codesign --verify --deep --strict` succeeds; signature is ad-hoc, no Team ID |
| Native PPTX import | Synthetic two-page deck indexed and actual LibreOffice previews generated |
| HTML import | Synthetic two-page static `.slide` deck indexed and actual Chrome previews generated |
| Search and single-page read | Four expected “Supply Chain” pages found; `slide get` succeeds |
| Native compose/re-import | Real preflight token accepted; two-page output re-imports with previews |
| Mixed compose | Four-page offline HTML produced; `PPTX_FLATTENED_TO_IMAGE` warning retained |
| Source protection | Synthetic input SHA-256 values unchanged after import and compose |
| License packaging | MIT plus Python, Electron/Chromium and library license texts accompany artifacts |

Builder commands:

```bash
PPTLIB_BUILD_ROOT=/tmp/deckatlas-release-beta2 UV_NO_SYNC=1 bash packaging/build_runtime.sh
PPTLIB_BUILD_ROOT=/tmp/deckatlas-release-beta2 UV_NO_SYNC=1 bash packaging/build_agent_cli.sh
```

Electron Builder used the repository's existing build configuration with temporary output paths,
the freshly built runtime, notices and the beta.2 artifact name. ZIP archives and SHA-256 values
are attached to the release. No real user presentations or library files were used as fixtures.
Browser rendering uses the existing isolated safe launch configuration; desktop startup used
both `--password-store=basic` and `--use-mock-keychain`.

## Outstanding checks

- Developer ID, notarization and downloaded/quarantined Gatekeeper acceptance are not complete.
- No fresh-machine or prior user-database upgrade acceptance was performed in this run.
- Renderer filesystem dialog-grant enforcement remains incomplete as documented in permissions.
- Real child cancellation, 10k-page performance and VoiceOver require separate evidence.
- Online URL archival and complex embedded media were not re-tested in this package run.
- Full visual parity of arbitrary PowerPoint documents is not certified.

The release remains an experimental Pre-release with these limits stated in installation notes.
