# PPT Page Library

Local macOS PPT page indexing and composition tool.

## Requirements

- macOS 13 or newer.
- Python 3.11.
- LibreOffice is optional for the M0.0 shell and required for rendering/export.

## Start

    make install
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib init
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib doctor
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib import ./sources
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib serve

The server binds only to 127.0.0.1. Use --no-open when running without a desktop session.

Open `/` to check the environment and select one or more PPTX files with the
native file picker. The selected files are persisted under the application data
directory, so they remain available for later export. For a mounted directory,
the advanced scan form accepts an absolute path and recursively imports it.
After importing, `/library` is a dual-entry selection workbench. The default
“按内容找” view filters by a fixed business taxonomy, while “按文件找” is a
two-step source browser: first choose a file card, then open that file's page
grid. Both views support full-text search, combined filters, slide preview,
and adding pages to the persistent local selection list. The selection page
supports drag-and-drop plus keyboard-safe up/down ordering; export returns a
browser download link for the generated PPTX and manifest.

### Fixed page taxonomy

Every indexed slide receives one business topic, one subtopic, and one page
purpose. Classification is deterministic and local: title evidence has the
highest weight, followed by section context, file name, extracted body text,
and notes. Phrase rules and negative keywords disambiguate terms such as
“增长”; no model or network service is required.

The canonical first-release taxonomy is 8×4:

| 主体领域 | 四个固定场景 |
| --- | --- |
| 战略与增长 | 企业战略与愿景、业务规划与增长、OKR与目标管理、路线图与重点项目 |
| 客户与市场 | 客户洞察与需求、市场与竞争、品牌与营销、销售与渠道 |
| 组织与人才 | 组织设计与治理、人才招聘与发展、绩效与激励、文化与协作 |
| 产品与运营 | 产品策略与体验、运营机制与流程、服务与交付、供应链与效率 |
| 数据与经营 | 经营指标与看板、财务与收入、用户与业务增长、效率与成本 |
| 项目与复盘 | 项目计划与进度、风险与问题、复盘与经验、行动项与跟进 |
| 方法论与培训 | 模型与框架、课程与培训、工具与模板、SOP与能力建设 |
| 公司介绍与案例 | 公司概览与能力、解决方案与产品、客户案例、结果与证言 |

Page purpose is a separate, orthogonal filter:
`封面与目录`、`观点与结论`、`对比与矩阵`、`流程与步骤`、`时间线与路线图`、
`组织架构`、`数据图表`、`表格与清单`、`案例与证言`、`总结与行动`。

The API and slide cards also expose `confidence` (`high`, `medium`, or
`low`), `classification_source` (`auto` or `manual`), and
`classifier_version`. Low-confidence pages stay searchable but carry a
“待确认” marker. Rows with `classification_source=manual` are preserved
during bootstrap reclassification; automatic rows may be refreshed when the
classifier version changes.

The JSON API exposes the same facets for integrations and smoke checks:

    curl 'http://127.0.0.1:8765/api/v1/slides?q=组织&topic=组织与人才&subtopic=组织设计与治理&page_type=组织架构'
    curl http://127.0.0.1:8765/api/v1/library/facets

The browser entry points are `/library?view=content` and
`/library?view=source`. In source view, `/library?view=source` renders one
card per current PPTX; `/library?view=source&deck_id=<deck-id>` opens that
file's page grid and keeps the file breadcrumb. `topic`, `subtopic`,
`page_type`, and `deck_id` can be combined with `q`, `page`, and `page_size`.
`/api/v1/library/facets` returns nested topic → subtopic counts as well as
page-purpose and source-file facets.
The current vertical slice supports PPTX text extraction, CJK/Latin search,
selection persistence, cached slide thumbnails, and synchronous static PPTX
export; background import/export jobs remain follow-up work.

## Worker smoke test

    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib worker --once

## Docker (optional)

Docker Desktop 4.x or newer is required. The compose profile runs the web
process and a small polling worker with the same SQLite database. LibreOffice
and CJK fonts are included in the image, so no host Python or LibreOffice
installation is needed.

    mkdir -p sources
    docker compose build
    docker compose up -d
    curl http://127.0.0.1:8765/api/v1/health
    curl -X POST http://127.0.0.1:8765/api/v1/imports \
      -H 'content-type: application/json' \
      -d '{"root":"/sources"}'
    curl -X POST http://127.0.0.1:8765/api/v1/imports/files \
      -F 'files=@/path/to/example.pptx'

Open <http://127.0.0.1:8765>. From the setup page, choose files and click
“选择并导入”; use the advanced directory form only for a mounted `/sources`
folder. Then search, select, reorder, and download the export from the selection
page.
Follow the container output with
`docker compose logs -f web worker`; stop it with `docker compose down`.

The mounts are deliberately explicit:

| Host/container path | Purpose | Mode |
| --- | --- | --- |
| `./sources:/sources` | PPT source directory | read-only |
| `pptlib-data:/data` | SQLite database and indexed assets | read/write |
| `pptlib-exports:/exports` | Generated files | read/write |
| `pptlib-logs:/logs` | JSONL application logs | read/write |

The container listens on `0.0.0.0:8765`; Compose maps it to
`127.0.0.1:8765` on the host, so it is not exposed to the LAN by default. To
remove the persisted database, exports, and logs,
run `docker compose down -v` (this is destructive).

The browser upload limit is 3 GiB per PPTX file. The image currently exposes health/doctor, PPTX text scanning and search,
LibreOffice/PDF slide thumbnail rendering, persistent selection, synchronous
static PPTX export, and the queue worker. The `/sources` mount is read-only;
browser-uploaded files are persisted inside the data volume.

## Verify

    make check

The default macOS runtime does not require Node.js, Docker, Redis, an external
database, or CDN assets. Docker is an optional isolated runtime for development
and deployment.
