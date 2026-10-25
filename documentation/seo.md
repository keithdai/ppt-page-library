# Static Website And Public Content

DeckAtlas has no public web application or server-rendered user data. `website/` is a separate
static marketing site deployed to GitHub Pages.

## Route And Data Boundary

| Route/resource | Public | Content source | User/library data allowed |
| --- | --- | --- | --- |
| `/ppt-page-library/` | Yes | `website/index.html` | No |
| CSS/JavaScript | Yes | `website/styles.css`, `script.js` | No |
| `/ppt-page-library/agent-setup.md` | Yes | `website/agent-setup.md` | No |
| Product screenshots/icons | Yes | `website/assets/` | Sanitized public assets only |
| GitHub release/repository links | Yes | Hard-coded public URL | No local path or token |

The desktop application, Agent CLI, SQLite library and HTML preview server are not deployed by this
workflow.

## Product Narrative And Usage Examples

The homepage follows pain points, desktop workflow, quick start, Agent CLI usage, then version and
download status. Main anchors are `#pain`, `#workflow`, `#quickstart`, `#cli` and `#download`;
`#local` and `#formats` remain within the workflow for compatibility with older links.

- Examples use fictional filenames and shell variables, never actual user-library paths or data.
- The existing sanitized product screenshot is reused; the file-search scenario is explanatory
  HTML, not a screenshot or a claim about measured search results.
- Four generated office-comic illustrations under `website/assets/pain-*.webp` explain large
  files, cross-deck page search, Agent access and manual reuse. They are illustrative scenarios,
  labelled as such, with HTML captions, alt text and links to the corresponding workflow.
  Generation uses the text-to-image backend
  `https://copilot-cn.bytedance.net/api/ide/v1/tool_text_to_image`; the published site serves the
  optimized static assets without contacting that backend or including generation credentials.
- The four illustrations total about 261 KiB, reserve 4:3 space and lazy-load. Desktop uses two
  columns and mobile one. Inspect all scenes before publishing, including dark colors.
- Large-file copy describes index/preview reuse, not compression or guaranteed loading time.
  Page-search copy distinguishes within-document text search from cross-file page indexing;
  Agent access requires an explicit configured interface, not automatic access to every local file.
- The hero leads with the value of reusing presentation material across PPTX and HTML, rather
  than defining the product as PPT-only. HTML's experimental status remains visible; per-format
  output and mixed-output fidelity are described in the workflow. Its Agent scenario is labelled
  illustrative and does not imply a live website chat or successful retrieval.
- Agent onboarding is a copyable natural-language request linking to `agent-setup.md`. Daily
  tasks are natural-language examples; installation, real slide IDs, selection manifests and
  preflight tokens are handled by the host Agent. Developer commands are collapsed.
- The public Agent guide is a static task instruction, not a Skill auto-registration or an
  installer. It requires host tools and user access, checks existing executables and library paths,
  and does not promise integration with all chat products. Independent CLI packages remain pending.
- The guide preserves source ownership, configured output scope and explicit write/overwrite
  authorization. It separates host persistence from installation and warns against replacing an
  existing library with an empty one. Full command semantics remain in `documentation/agent-cli.md`.
- Local Agent import is marked as a next-Beta preview until it is present in the published build.
  Callers must inspect `capabilities.commands` before using it.
- HTML is marked experimental; packaged Beta defaults, mixed-output image fallback and static
  URL archival restrictions are stated.
- Desktop and standalone CLI download controls remain disabled until verified release assets
  exist. The Releases index may be linked before an individual release exists.
- Copy controls operate on static code and prompt text via the Clipboard API, with a selection fallback;
  no commands execute in the website and no analytics or third-party scripts are added.

## Metadata

- Canonical URL, Open Graph URL/image, title and description are static in `website/index.html`.
- Title and descriptions describe page reuse, quick start and CLI access; they do not claim
  an available installer or a published Agent import feature.
- No dynamic user input enters metadata or page HTML.
- The screenshot must use sanitized demo content and contain no customer, internal or personal
  material.
- Release links must point to an existing public GitHub release. The website does not proxy or
  generate downloads.

## Deployment

`.github/workflows/pages.yml` runs only for `website/**` or workflow changes on `main`, or by manual
dispatch. It uses read-only repository contents plus GitHub Pages deployment permissions. It does
not receive application signing secrets or access local user data.

Before publishing:

1. Verify canonical/OG URLs and the release link.
2. Inspect every image at full resolution for sensitive content.
3. Check keyboard navigation, alt text, reduced motion and mobile layout.
4. Confirm no source map, local path, API token or development artifact is present.
