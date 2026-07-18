# 固定分类双入口页面库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将页面库升级为“内容分类 + 来源文件”双入口检索工作台，让用户能够快速筛选、预览、批量加入选片单并保留来源追溯。

**Architecture:** 在现有 SQLite/FTS5 页面库之上增加固定 taxonomy 元数据表，导入时按标题、正文、备注和文件名生成主题与页面类型；页面查询支持 query、主题、页面类型、来源文件四个筛选维度。前端保留现有服务端渲染和原生 JavaScript，不引入第三方 UI 依赖，页面库用筛选栏、视图切换和固定选片抽屉承载双入口。

**Tech Stack:** Python 3.11、FastAPI、SQLite/FTS5、Jinja2、原生 CSS/JavaScript、pytest、Docker Compose。

## Global Constraints

- 固定主题分类：战略与规划、客户与市场、组织与人才、产品与运营、数据与业绩、项目与复盘、方法论与培训、公司介绍与案例。
- 固定页面类型：封面与目录、观点与结论、对比与矩阵、流程与步骤、时间线与路线图、组织架构、数据图表、表格与清单、案例与证言、总结与行动。
- 一个页面必须有且仅有一个主题和一个页面类型；来源文件保持唯一归属。
- 主题和页面类型由规则自动推断，不要求用户逐页手工录入；未命中规则时分别回退到“方法论与培训”和“观点与结论”。
- 查询接口必须兼容现有 `q/page/page_size` 参数，并新增 `topic/page_type/deck_id` 可选参数。
- 页面库默认按内容主题浏览，来源文件作为可切换入口和筛选面板；筛选条件可组合。
- 不增加前端第三方依赖，不改变导出接口和选片单持久化语义。
- 所有行为变更先写失败测试，再实现最小代码；完成后运行 `make check`。

---

### Task 1: 固定分类规则与 SQLite taxonomy 索引

**Files:**
- Create: `src/pptlib/domain/taxonomy.py`
- Create: `src/pptlib/migrations/0003_taxonomy.sql`
- Modify: `src/pptlib/infrastructure/db/repositories.py`
- Modify: `src/pptlib/bootstrap.py`
- Modify: `tests/integration/db/test_migrations.py`
- Create: `tests/unit/domain/test_taxonomy.py`
- Modify: `tests/integration/ingestion/test_import_search.py`

**Interfaces:**
- `Taxonomy` exposes `TOPICS`, `PAGE_TYPES`, `classify_slide(deck_name: str, title: str, text: str) -> tuple[str, str]`.
- Migration creates `slide_taxonomy(slide_id TEXT PRIMARY KEY REFERENCES slides(id) ON DELETE CASCADE, topic TEXT NOT NULL, page_type TEXT NOT NULL, classified_at TEXT NOT NULL)` and indexes on `topic` and `page_type`.
- `DeckRepository.import_parsed` inserts one taxonomy row for every imported slide in the same transaction.
- `bootstrap.initialize` backfills missing taxonomy rows for current parsed slides before returning.

- [ ] **Step 1: Write failing taxonomy tests**

Add unit cases asserting:
```python
assert classify_slide("组织人才管理.pptx", "组织架构设计", "") == ("组织与人才", "组织架构")
assert classify_slide("客户案例.pptx", "项目推进流程", "") == ("客户与市场", "流程与步骤")
assert classify_slide("年度经营.pptx", "收入增长", "同比增长 32%") == ("数据与业绩", "数据图表")
assert classify_slide("未命名.pptx", "普通内容", "") == ("方法论与培训", "观点与结论")
```
Add migration assertions for `slide_taxonomy` and its indexes. Extend the real import test to assert the imported slide has a taxonomy row.

- [ ] **Step 2: Run focused tests and verify failure**

Run:
```
pytest tests/unit/domain/test_taxonomy.py tests/integration/db/test_migrations.py tests/integration/ingestion/test_import_search.py -v
```
Expected: FAIL because the classifier and migration table do not exist.

- [ ] **Step 3: Implement fixed taxonomy and import/backfill**

Create explicit ordered keyword rules in `taxonomy.py`; normalize text with casefold and whitespace removal, score deck name/title/body, choose the highest score with deterministic order, then apply the stated fallbacks. Add migration SQL and repository inserts. In `bootstrap.initialize`, query current parsed slides missing taxonomy, classify them from joined deck and slide text, and insert rows idempotently.

- [ ] **Step 4: Run focused tests and verify pass**

Run the same focused pytest command. Expected: all taxonomy, migration, and import assertions pass.

- [ ] **Step 5: Commit**

```bash
git add src/pptlib/domain/taxonomy.py src/pptlib/migrations/0003_taxonomy.sql src/pptlib/infrastructure/db/repositories.py src/pptlib/bootstrap.py tests/unit/domain/test_taxonomy.py tests/integration/db/test_migrations.py tests/integration/ingestion/test_import_search.py
git commit -m "feat: classify imported slides with fixed taxonomy"
```

---

### Task 2: 后端筛选接口与来源/分类聚合

**Files:**
- Modify: `src/pptlib/application/library.py`
- Modify: `src/pptlib/infrastructure/db/repositories.py`
- Modify: `src/pptlib/infrastructure/db/library.py`
- Modify: `src/pptlib/web/routes/api.py`
- Modify: `src/pptlib/web/routes/pages.py`
- Modify: `tests/contract/web/test_library.py`
- Modify: `tests/integration/db/test_sqlite_library.py`
- Modify: `tests/integration/web/test_real_library.py`

**Interfaces:**
- Add `LibraryFilters(topic: str = "", page_type: str = "", deck_id: str = "")`.
- Extend `SlideCatalog.search(query, page, page_size, filters=LibraryFilters())`.
- Add `FacetOption(id: str, label: str, count: int)` and `LibraryFacets(topics, page_types, decks)`.
- Add `SlideCatalog.facets() -> LibraryFacets`.
- `GET /api/v1/slides` accepts `topic`, `page_type`, `deck_id` and returns filters plus matching items.
- Add `GET /api/v1/library/facets` returning fixed topic/page-type options and current-source deck options with counts.
- `GET /library` accepts the same query parameters and passes `facets`, `filters`, and option labels to Jinja.

- [ ] **Step 1: Write failing query/filter tests**

Add tests that import two classified decks and assert:
```
GET /api/v1/slides?topic=组织与人才 -> only organization slides
GET /api/v1/slides?deck_id=<id> -> only that deck's slides
GET /api/v1/library/facets -> contains all fixed topics and source file counts
GET /library?topic=数据与业绩 -> renders the active filter chip and filtered result count
```
Add in-memory catalog coverage so contract tests remain deterministic.

- [ ] **Step 2: Run focused tests and verify failure**

Run:
```
pytest tests/contract/web/test_library.py tests/integration/db/test_sqlite_library.py tests/integration/web/test_real_library.py -v
```
Expected: FAIL because filter arguments and facet endpoints are not implemented.

- [ ] **Step 3: Implement filters and facets**

Update repository SQL to join `slide_taxonomy`, apply optional exact filters, preserve FTS ranking and current-version constraints, and calculate counts from current parsed slides. Keep empty-query browsing and pagination behavior unchanged. Add safe validation against fixed taxonomy values and return `REQUEST_INVALID` for unknown topic/page type. Extend both SQLite and in-memory adapters.

- [ ] **Step 4: Run focused tests and verify pass**

Run the same focused pytest command. Expected: all filter, facet, pagination, and contract tests pass.

- [ ] **Step 5: Commit**

```
git add src/pptlib/application/library.py src/pptlib/infrastructure/db/repositories.py src/pptlib/infrastructure/db/library.py src/pptlib/web/routes/api.py src/pptlib/web/routes/pages.py tests/contract/web/test_library.py tests/integration/db/test_sqlite_library.py tests/integration/web/test_real_library.py
git commit -m "feat: add taxonomy and source facets to library search"
```

---

### Task 3: 页面库双入口工作台 UI

**Files:**
- Modify: `src/pptlib/web/templates/library.html`
- Modify: `src/pptlib/web/static/app.css`
- Modify: `src/pptlib/web/static/app.js`
- Modify: `tests/contract/web/test_library.py`
- Modify: `tests/contract/web/test_pages.py`

**Interfaces:**
- Library page renders a primary `content-nav` for fixed topics and page types, a `source-nav` for deck options, a combined filter form, and active filter chips.
- Add `view=content|source` to switch between “按内容找” and “按文件找”; both views reuse the same result grid.
- Card actions support `data-slide-id` selection, selected styling, and a compact “查看大图” action without changing the selection API.
- Add a sticky `selection-drawer` on desktop and a bottom sheet on mobile showing selected count and a link to the existing selection/export flow.
- Add keyboard-friendly filter controls and preserve query parameters across pagination.

- [ ] **Step 1: Write failing template contract tests**

Assert the library HTML contains:
```
content-nav
source-nav
view-switch
filter-chip
selection-drawer
data-filter-topic
data-filter-deck
```
Assert a selected slide has `is-added` and the active topic/source query is preserved in pagination links.

- [ ] **Step 2: Run focused contract tests and verify failure**

Run:
```
pytest tests/contract/web/test_library.py tests/contract/web/test_pages.py -v
```
Expected: FAIL because the new workbench elements are absent.

- [ ] **Step 3: Implement the workbench layout**

Build the content-first default layout with a compact topic rail, page-type secondary filters, source-file disclosure section, result toolbar, view switch, and sticky selection drawer. Use CSS grid and the existing emerald/ink design tokens; keep cards image-first and make the whole preview clickable. Use native JS only for selection count, view switch URL updates, and mobile drawer toggling. Do not duplicate export logic.

- [ ] **Step 4: Run focused contract tests and verify pass**

Run the same contract command and inspect one rendered `/library` response for desktop and mobile class presence.

- [ ] **Step 5: Commit**

```
git add src/pptlib/web/templates/library.html src/pptlib/web/static/app.css src/pptlib/web/static/app.js tests/contract/web/test_library.py tests/contract/web/test_pages.py
git commit -m "feat: redesign library as dual-entry selection workbench"
```

---

### Task 4: 全量验证、Docker 冒烟与交付文档

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-16-ppt-page-library-delivery-design.md` only if the user-facing behavior list needs the new taxonomy flow.
- Test: `make check`
- Test: `docker compose build web worker`
- Test: `docker compose up -d --force-recreate`

**Interfaces:**
- README documents fixed taxonomy, content/source dual entry, facet query parameters, and the Docker URL.
- The final runtime exposes `/api/v1/health`, `/api/v1/library/facets`, `/library?view=content`, and `/library?view=source`.

- [ ] **Step 1: Run all tests**

Run `make check`. Expected: ruff clean, mypy clean, all unit/integration/contract tests pass.

- [ ] **Step 2: Build and restart Docker**

Run:
```
docker build -t ppt-page-library:local .
docker compose up -d --force-recreate
```
Expected: web container healthy and worker running.

- [ ] **Step 3: Smoke test both entry points**

Run:
```
curl -fsS http://127.0.0.1:8765/api/v1/health
curl -fsS http://127.0.0.1:8765/api/v1/library/facets
curl -fsS 'http://127.0.0.1:8765/library?view=content'
curl -fsS 'http://127.0.0.1:8765/library?view=source'
```
Expected: success envelope, fixed categories, source facets, and both HTML views.

- [ ] **Step 4: Commit docs and final verification**

```
git add README.md docs/superpowers/specs/2026-07-16-ppt-page-library-delivery-design.md
git commit -m "docs: describe dual-entry page library workflow"
git diff --check
```
