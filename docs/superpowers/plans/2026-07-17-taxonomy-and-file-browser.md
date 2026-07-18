# 分层分类与按文件浏览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 将页面库升级为“一级主体 + 二级场景 + 页面用途”的可解释分类体系，并把“按文件找”实现为文件索引 → 文件详情 → 文件页面的完整流程。

**Architecture:** 保持现有 FastAPI + SQLite/FTS5 + 服务端 Jinja + 原生 JavaScript 架构，不引入联网模型或第三方 UI 依赖。分类器在导入时基于文件名、章节上下文、标题、正文和备注生成固定层级标签；页面库内容视图读取主题树，来源视图先读取 deck 聚合，再读取单文件页面。

**Tech Stack:** Python 3.11, FastAPI, SQLite/FTS5, Jinja2, vanilla CSS/JavaScript, pytest, Docker Compose.

## Global Constraints

- 保持本机轻量运行，不引入 LLM、联网 API 或新的运行时服务。
- 一级主体固定为 8 个，二级场景固定为每个主体 4 个；页面用途固定为 10 个。
- 每页只有一个一级主体、一个二级场景和一个页面用途；低置信度通过字段标记，不使用空分类。
- 旧数据可通过迁移和回填升级；旧 API 的 `topic`、`page_type` 参数保持兼容。
- “按文件找”首屏只展示文件卡片；`deck_id` 进入单文件详情，页面网格保留搜索、筛选、选片单和详情链接。
- 所有行为变更必须先写失败测试，再写生产代码；每个任务独立提交并通过对应测试。

### Task 1: 分层分类模型与确定性分类器

**Files:**
- Modify: `src/pptlib/domain/taxonomy.py`
- Test: `tests/unit/domain/test_taxonomy.py`

**Interfaces:**
- Produce `TOPIC_TREE`, `TOPICS`, `SUBTOPICS`, `PAGE_TYPES` and `classify_slide(...)` compatibility output.
- Add a structured result helper exposing `topic`, `subtopic`, `page_type`, `confidence`, and `classifier_version`.

- [ ] Write failing tests for the approved 8×4 taxonomy, ambiguous “增长” routing, and low-confidence marking.
- [ ] Run `.venv/bin/pytest tests/unit/domain/test_taxonomy.py -v` and verify the new tests fail.
- [ ] Implement phrase groups, negative keywords, deterministic scoring, and fixed fallback/low-confidence behavior.
- [ ] Run the focused test file and then the existing taxonomy tests.
- [ ] Commit `feat: add hierarchical slide taxonomy`.

### Task 2: Taxonomy persistence, import backfill, facets, and API filters

**Files:**
- Create: `src/pptlib/migrations/0004_hierarchical_taxonomy.sql`
- Modify: `src/pptlib/infrastructure/db/repositories.py`
- Modify: `src/pptlib/infrastructure/db/library.py`
- Modify: `src/pptlib/application/library.py`
- Modify: `src/pptlib/bootstrap.py`
- Test: `tests/integration/db/test_migrations.py`
- Test: `tests/integration/db/test_sqlite_library.py`
- Test: `tests/integration/web/test_real_library.py`
- Test: `tests/contract/web/test_library.py`

**Interfaces:**
- Extend slide summaries and search payloads with `subtopic`, `confidence`, and `classification_source`.
- Preserve `topic`, `page_type`, and `deck_id` filters; add `subtopic` filtering and nested facet counts.
- Existing rows must be upgraded idempotently from old topic labels.

- [ ] Add migration and repository tests first, including repeat migration and old-data backfill.
- [ ] Run focused tests to capture expected failures.
- [ ] Implement schema, import writes, bootstrap backfill, query filters, facets, and API serialization.
- [ ] Run focused database/web tests, then the complete check suite.
- [ ] Commit `feat: persist hierarchical taxonomy and facets`.

### Task 3: File index and file detail routing

**Files:**
- Modify: `src/pptlib/web/routes/pages.py`
- Modify: `src/pptlib/application/library.py`
- Modify: `src/pptlib/infrastructure/db/library.py`
- Modify: `src/pptlib/web/templates/library.html`
- Create or modify: `src/pptlib/web/templates/source-deck.html`
- Test: `tests/contract/web/test_library.py`
- Test: `tests/integration/web/test_real_library.py`

**Interfaces:**
- `/library?view=source` renders a deck index with one card per current deck.
- `/library?view=source&deck_id=<id>` renders the existing slide grid scoped to that deck with breadcrumb/back link.
- Deck cards expose display name, slide count, cover thumbnail, and entry link.

- [ ] Add failing route contract tests for source index, deck detail, unknown deck, and filter preservation.
- [ ] Run focused tests and verify failure before implementation.
- [ ] Add deck summary query/facets, route branching, templates, and source-mode navigation.
- [ ] Run focused web tests and verify content view is unchanged.
- [ ] Commit `feat: add source deck index and detail view`.

### Task 4: Content tree UI, metadata tags, and responsive styling

**Files:**
- Modify: `src/pptlib/web/templates/library.html`
- Modify: `src/pptlib/web/templates/source-deck.html`
- Modify: `src/pptlib/web/static/app.css`
- Modify: `src/pptlib/web/static/app.js`
- Test: `tests/contract/web/test_library.py`

**Interfaces:**
- Content view renders expandable topic/subtopic groups with counts.
- Cards show topic, subtopic, page type, and confidence when low.
- Source index uses a compact card grid; source detail keeps selection drawer and native navigation.

- [ ] Add failing HTML contract assertions for nested facets, source deck cards, breadcrumb, and low-confidence marker.
- [ ] Run focused contract tests and verify failure.
- [ ] Implement CSS/HTML/JS with native links, responsive two-column desktop and one-column mobile behavior.
- [ ] Run contract tests and manually inspect rendered HTML markers.
- [ ] Commit `feat: redesign content tree and source browser UI`.

### Task 5: End-to-end verification and Docker delivery

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-16-ppt-page-library-delivery-design.md`
- Test: `tests/integration/docker/test_assets.py`

- [ ] Update runbook/API examples for hierarchical filters and source deck routes.
- [ ] Run `make check` and record all counts.
- [ ] Rebuild with `DOCKER_BUILDKIT=0 docker compose build web` when BuildKit session headers fail.
- [ ] Start with `docker compose up -d --no-build`, verify health, source index, and source detail responses.
- [ ] Commit `docs: document hierarchical taxonomy and source browser`.
