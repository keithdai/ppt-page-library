# Static Website And Public Content

DeckAtlas has no public web application or server-rendered user data. `website/` is a separate
static marketing site deployed to GitHub Pages.

## Route And Data Boundary

| Route/resource | Public | Content source | User/library data allowed |
| --- | --- | --- | --- |
| `/ppt-page-library/` | Yes | `website/index.html` | No |
| CSS/JavaScript | Yes | `website/styles.css`, `script.js` | No |
| Product screenshots/icons | Yes | `website/assets/` | Sanitized public assets only |
| GitHub release/repository links | Yes | Hard-coded public URL | No local path or token |

The desktop application, Agent CLI, SQLite library and HTML preview server are not deployed by this
workflow.

## Metadata

- Canonical URL, Open Graph URL/image, title and description are static in `website/index.html`.
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
