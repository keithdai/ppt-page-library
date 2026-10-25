# Acceptance Evidence

This directory stores frozen verification reports and release evidence.

Rules:

- Name new evidence by feature or release; include a date when repeated runs need distinction.
- Record source revision, commands, fixtures, environment and observed result.
- Do not edit a historical result to make a later release look green. Add a new report.
- Keep generated binaries, browser profiles, user libraries and confidential source presentations
  outside Git. Store hashes or a sanitized manifest when durable provenance is needed.
- Current test requirements live in
  [`../../documentation/tests.md`](../../documentation/tests.md); this directory records outcomes,
  not policy.

Current evidence:

- [PPTX compose/export verification](compose-export-verification.md)
- [Beta.2 package verification](beta2-package-verification-2026-10-25.md)
