# PPT 页库施工版设计

- 版本：v2.0
- 日期：2026-07-16
- 状态：待开发前确认
- 上游文档：../../../ppt-page-library-design.md
- 首发平台：macOS 13 及以上
- 实施原则：本地优先、依赖最少、风险优先、可恢复、可验证

---

## 1. 文档目的

本文档把 v1 技术设计收敛为可以直接拆任务、写代码、测试和验收的施工方案。v1 中未被本文档修改的产品原则继续有效；两者冲突时以本文档为准。

本文档解决五类落地问题：

1. 明确首版技术栈、进程模型、目录结构和依赖边界。
2. 对齐页面、后端用例、HTTP 契约、前端页面和数据库记录。
3. 把入库恢复、资产缓存、OOXML 复制、导出验证变成可测试的模块。
4. 给出分阶段开发顺序、完成定义和发布门槛。
5. 给出 M0 的测试矩阵、性能预算和验收样例。

---

## 2. 已冻结的技术决策

### 2.1 运行形态

M0 是单机、本地浏览器访问的 Python 单体应用：

    用户浏览器
      -> http://127.0.0.1:<随机可用端口>
      -> FastAPI
          -> Jinja2 页面与 HTMX 局部更新
          -> 应用用例
          -> SQLite / FTS5
          -> 本机文件系统
          -> 单独 worker 进程
              -> LibreOffice
              -> PyMuPDF
              -> OOXML 处理器

M0 不引入：

- Node.js 构建链。
- React、Vue 或其他 SPA 框架。
- Electron。
- Docker。
- Redis、Celery、PostgreSQL 或外部队列。
- 云端服务硬依赖。
- PowerPoint for Mac 硬依赖。

### 2.2 技术栈

运行时依赖：

| 范围 | 选择 | 说明 |
|---|---|---|
| 语言 | Python 3.11 | 兼顾依赖兼容性和性能 |
| Web | FastAPI + Uvicorn | 本地 HTTP、JSON schema、未来智能体工具复用 |
| 页面 | Jinja2 + 本地 vendored HTMX | 无 Node 构建，支持局部刷新 |
| 样式 | 项目内原生 CSS | 不使用 CDN |
| 数据库 | Python sqlite3 + SQLite FTS5 | 不引入 ORM |
| 迁移 | 有序 SQL 文件 + migration runner | 迁移行为完全可见 |
| PPTX 解析 | zipfile + defusedxml/lxml 安全模式 | 包级读取和关系图处理 |
| 渲染 | LibreOffice headless + PyMuPDF | 整份转 PDF，再按页渲染 |
| 图像 | Pillow | 缩略图和视觉差异 |
| 配置 | TOML + 环境变量覆盖 | 密钥不落配置文件 |
| 日志 | Python logging + JSONL 文件 | 便于定位 job 和 export |

开发依赖：

- pytest、pytest-cov、pytest-timeout。
- HTTPX，用于 HTTP 契约测试。
- Python Playwright，仅用于开发和验收，不进入运行时安装。
- Ruff，用于格式和静态检查。
- mypy，用于核心领域和应用层类型检查。

### 2.3 分发策略

M0 先提供 Python 虚拟环境安装和启动命令，避免过早承担签名、公证和 universal2 打包复杂度：

    python3.11 -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/pptlib doctor
    .venv/bin/pptlib init
    .venv/bin/pptlib serve

M1 再增加 pywebview 或 macOS app bundle。Web 页面和应用用例不因增加桌面壳而重写。

---

## 3. M0 产品闭环和范围

### 3.1 M0 必须完成

    配置来源目录
      -> 扫描 PPTX
      -> 创建不可变文件版本
      -> 提取逐页文本
      -> 整份渲染并生成缩略图
      -> 提交 FTS 索引
      -> 搜索和预览
      -> 加入有序选片单
      -> 预检兼容性
      -> 跨文件复制页面
      -> 结构和视觉验证
    -> 原子发布 PPTX 与 manifest

### 3.1.1 页面库双入口与固定分类

页面库不是按单一目录组织，而是为同一批页面提供两个稳定入口：

- **按内容找**（默认）：按固定主题和页面类型筛选，适合“我需要一页组织架构/数据图表”的检索场景。
- **按文件找**：先展示来源文件索引，再进入单文件详情页查看其中的页面，适合回到原始 PPT、上下文和版本。

每个页面保持唯一来源文件归属，同时自动得到且仅得到一个一级主体、一个二级场景和一个页面用途。首版冻结为 8×4 层级分类：

| 一级主体 | 二级场景 |
| --- | --- |
| 战略与增长 | 企业战略与愿景、业务规划与增长、OKR与目标管理、路线图与重点项目 |
| 客户与市场 | 客户洞察与需求、市场与竞争、品牌与营销、销售与渠道 |
| 组织与人才 | 组织设计与治理、人才招聘与发展、绩效与激励、文化与协作 |
| 产品与运营 | 产品策略与体验、运营机制与流程、服务与交付、供应链与效率 |
| 数据与经营 | 经营指标与看板、财务与收入、用户与业务增长、效率与成本 |
| 项目与复盘 | 项目计划与进度、风险与问题、复盘与经验、行动项与跟进 |
| 方法论与培训 | 模型与框架、课程与培训、工具与模板、SOP与能力建设 |
| 公司介绍与案例 | 公司概览与能力、解决方案与产品、客户案例、结果与证言 |

页面用途（独立于主体领域）：封面与目录、观点与结论、对比与矩阵、流程与步骤、时间线与路线图、组织架构、数据图表、表格与清单、案例与证言、总结与行动。

分类器使用文件名、章节上下文、页面标题、正文和备注的确定性短语规则，并通过正负关键词处理“增长”等歧义词；标题权重最高，其次为章节、文件名、正文和备注。每条结果还写入 `confidence`（high/medium/low）、`classification_source`（auto/manual）和 `classifier_version`。未命中时仍落在固定分类，但标记为 low 并在页面库显示“待确认”。启动回填只更新自动分类，保留 `classification_source=manual` 的人工覆盖。

查询支持 `q`、`topic`、`subtopic`、`page_type`、`deck_id` 组合，分页和选片单语义保持不变；`GET /api/v1/library/facets` 返回嵌套的主体/场景计数、页面用途计数和当前文件计数。

来源入口的页面契约固定为：`GET /library?view=source` 返回一个当前 PPTX 一个卡片的文件索引；`GET /library?view=source&deck_id=<id>` 返回该文件的详情、面包屑和页码排序的页面网格。内容入口为 `GET /library?view=content`。

### 3.2 M0 明确不做

- OCR、embedding、视觉大模型和自然语言过滤条件解析。
- 标签管理和相似页面聚类。
- 旧式 .ppt 导入。
- .pptm、宏、ActiveX 和受密码保护文件。
- 混合页面尺寸自动缩放。
- 云同步、多用户和权限系统。
- 自动删除或修改源 PPT。
- PowerPoint 自动化后端。

### 3.3 M0 成功定义

使用冻结样本集完成以下闭环且无人工修库：

- 至少 30 个 PPTX、500 页。
- 至少 20 条中文真实查询。
- 至少 5 次跨文件、每次 10 至 20 页的导出。
- 进程在 parse、render、index、export 任意阶段被终止后可恢复。
- 所有发布的输出都有 manifest、页级验证结果和来源追溯。

---

## 4. 仓库结构

    ppt-page-lib/
    ├── pyproject.toml
    ├── README.md
    ├── Makefile
    ├── scripts/
    │   ├── dev.sh
    │   └── verify_fixture_set.py
    ├── src/pptlib/
    │   ├── __init__.py
    │   ├── default.toml
    │   ├── migrations/
    │   │   ├── 0001_initial.sql
    │   │   └── 0002_fts.sql
    │   ├── cli.py
    │   ├── config.py
    │   ├── logging.py
    │   ├── bootstrap.py
    │   ├── domain/
    │   │   ├── ids.py
    │   │   ├── models.py
    │   │   ├── states.py
    │   │   ├── errors.py
    │   │   └── policies.py
    │   ├── application/
    │   │   ├── import_decks.py
    │   │   ├── search_slides.py
    │   │   ├── manage_selection.py
    │   │   ├── export_selection.py
    │   │   ├── reconcile.py
    │   │   └── dto.py
    │   ├── discovery/
    │   │   ├── scanner.py
    │   │   ├── identity.py
    │   │   └── path_policy.py
    │   ├── ingestion/
    │   │   ├── coordinator.py
    │   │   ├── stage_ledger.py
    │   │   ├── parser.py
    │   │   ├── renderer.py
    │   │   ├── indexer.py
    │   │   └── commit.py
    │   ├── search/
    │   │   ├── analyzer.py
    │   │   ├── query.py
    │   │   ├── lexical.py
    │   │   └── explain.py
    │   ├── selection/
    │   │   └── policy.py
    │   ├── export/
    │   │   ├── coordinator.py
    │   │   ├── preflight.py
    │   │   ├── package_graph.py
    │   │   ├── transplant.py
    │   │   ├── manifest.py
    │   │   └── fidelity.py
    │   ├── infrastructure/
    │   │   ├── db/
    │   │   │   ├── connection.py
    │   │   │   ├── migrations.py
    │   │   │   └── repositories.py
    │   │   ├── assets/
    │   │   │   ├── store.py
    │   │   │   └── derivation.py
    │   │   ├── libreoffice.py
    │   │   ├── pdf.py
    │   │   └── filesystem.py
    │   ├── worker/
    │   │   ├── main.py
    │   │   ├── queue.py
    │   │   ├── lease.py
    │   │   └── handlers.py
    │   └── web/
    │       ├── app.py
    │       ├── dependencies.py
    │       ├── routes/
    │       │   ├── pages.py
    │       │   ├── api.py
    │       │   └── fragments.py
    │       ├── templates/
    │       │   ├── base.html
    │       │   ├── setup.html
    │       │   ├── library.html
    │       │   ├── selection.html
    │       │   ├── jobs.html
    │       │   ├── exports.html
    │       │   └── fragments/
    │       └── static/
    │           ├── app.css
    │           ├── app.js
    │           └── vendor/htmx.min.js
    ├── tests/
    │   ├── unit/
    │   ├── integration/
    │   ├── contract/
    │   ├── golden/
    │   ├── e2e/
    │   ├── performance/
    │   └── fixtures/
    └── var/
        └── .gitkeep

依赖方向固定为：

    web -> application -> domain
    worker -> application -> domain
    application -> infrastructure ports
    infrastructure -> domain

domain 不得导入 FastAPI、sqlite3、LibreOffice、文件系统实现或模板代码。

---

## 5. 本机目录和配置

正式运行默认目录：

| 类型 | macOS 路径 |
|---|---|
| 数据库 | ~/Library/Application Support/PPT Page Library/pages.db |
| 派生资产 | ~/Library/Application Support/PPT Page Library/assets |
| 临时文件 | ~/Library/Caches/PPT Page Library/tmp |
| 日志 | ~/Library/Logs/PPT Page Library |
| 默认导出 | ~/Documents/PPT Page Library Exports |

开发环境通过 PPTLIB_HOME 指向仓库下的 var/dev，避免污染正式数据。

配置项至少包括：

- source_roots：用户显式授权的来源目录。
- output_root：唯一允许写入最终文件的目录。
- libreoffice_path：自动发现失败时的显式路径。
- max_workers：M0 固定默认 1，最大 2。
- max_file_bytes：默认 500 MB。
- max_uncompressed_package_bytes：默认 2 GB。
- max_parts_per_package：默认 20,000。
- job_lease_seconds：默认 120。
- job_max_attempts：基础任务默认 3。
- thumbnail_long_edge：默认 640。
- preview_long_edge：默认 1440。
- analyzer_version：初始为 cjk-bigram-v1。

---

## 6. 领域身份和状态

### 6.1 身份定义

| 名称 | 含义 |
|---|---|
| source_root_id | 用户授权的扫描根 |
| source_file_id | 一个规范化文件位置 |
| file_version_id | 一个不可变源文件内容版本 |
| slide_id | 一个 file version 中的固定页 |
| selection_id | 可编辑选片单 |
| selection_item_id | 选片单中的一次页面引用，允许同一页重复出现 |
| export_id | 一次幂等导出意图 |
| artifact_id | 一个不可变派生产物 |
| job_id | 一个后台工作意图 |

### 6.2 Hash 不混用

- file_sha256：源文件完整字节。
- package_part_hash：某个 OOXML part 的原始字节。
- slide_package_hash：slide 与静态依赖闭包的稳定摘要。
- normalized_text_hash：规范化后的可搜索文本。
- visual_hash：渲染图像感知摘要，仅用于相似性和测试。
- derivation_key：源 hash、工具版本和规范化配置的组合摘要。

页面去重只在同一种 hash 语义内进行。M0 搜索结果默认使用 slide_package_hash 精确聚合，不用 normalized_text_hash 代替视觉重复判断。

### 6.3 正交状态

FileVersion：

    discovered -> hashing -> processing -> ready
                                   |-> partial_ready
                                   |-> failed
                                   |-> stale

Slide 使用四个独立字段：

- parse_status：pending、ready、failed。
- render_status：pending、ready、failed。
- index_status：pending、ready、failed。
- enrichment_status：disabled、pending、ready、failed。

Job：

    queued -> leased -> running -> succeeded
                         |-> retry_wait -> queued
                         |-> failed
                         |-> cancelled
                         |-> abandoned

Export：

    requested -> preflighting -> building -> validating -> published
                    |              |             |-> rejected
                    |              |-> failed
                    |-> blocked

任何状态变更都必须在数据库短事务中完成，文件转换和 PPTX 构建不得占用数据库事务。

---

## 7. 数据库施工设计

### 7.1 必须修订 v1 的地方

1. source_snapshot 不再是 Selection 的模糊字符串；每个 selection item 直接固定 file_version_id 和 source_page_number。
2. selection_items 使用独立 ID，允许同一 slide 在选片单中重复出现。
3. jobs 增加幂等键、租约和阶段账本。
4. artifacts 独立建模，路径不再直接散落在 slides。
5. exports 增加 idempotency_key 和请求快照。
6. validation_evidence 保存验证事实，fidelity level 由事实计算。
7. FTS 使用独立 search document，通过同一事务同步。

### 7.2 核心表

source_roots：

- id、canonical_path、display_name、enabled。
- created_at、updated_at。
- canonical_path 唯一。

source_files：

- id、source_root_id、canonical_path、display_name。
- availability_status：active、missing、denied。
- current_version_id。
- file_system_id：可取时保存设备号与 inode，仅作移动检测信号。
- last_seen_at、created_at、updated_at。
- canonical_path 唯一。

file_versions：

- id、source_file_id、file_sha256、size_bytes、mtime_ns。
- page_count、parser_version、ingest_status、error_count。
- created_at、ready_at。
- source_file_id + file_sha256 唯一。

slides：

- id、file_version_id、page_number、internal_name。
- title、body_text、table_text、speaker_notes、boilerplate_text。
- slide_package_hash、normalized_text_hash、visual_hash。
- layout_name、width_emu、height_emu。
- object_counts_json、warning_codes_json。
- parse_status、render_status、index_status、enrichment_status。
- file_version_id + page_number 唯一。

artifacts：

- id、kind、derivation_key、relative_path。
- sha256、byte_size、tool_name、tool_version、config_json。
- status：writing、ready、corrupt、deleted。
- created_at。
- kind + derivation_key 唯一。

artifact_refs：

- artifact_id、owner_type、owner_id、role。
- 四字段联合唯一。

selections：

- id、name、revision、status。
- created_at、updated_at。

selection_items：

- id、selection_id、slide_id。
- file_version_id、source_page_number、sort_order。
- added_reason、created_at。
- selection_id + sort_order 唯一。

jobs：

- id、kind、target_type、target_id、idempotency_key。
- status、priority、attempt、max_attempts。
- lease_owner、lease_expires_at、cancel_requested。
- progress_current、progress_total。
- error_code、error_message。
- created_at、available_at、started_at、finished_at。
- kind + idempotency_key 唯一。

job_stages：

- job_id、stage_name、stage_order。
- status、attempt、checkpoint_json、output_json。
- started_at、finished_at。
- job_id + stage_name 唯一。

exports：

- id、selection_id、selection_revision、idempotency_key。
- request_json、output_relative_path、output_sha256。
- backend、status、fidelity_level、page_count。
- manifest_relative_path、created_at、finished_at。
- idempotency_key 唯一。

export_items：

- export_id、output_order、selection_item_id、slide_id。
- source_file_version_id、source_page_number。
- status、fidelity_level、warning_codes_json。
- export_id + output_order 唯一。

validation_evidence：

- id、export_id、export_item_order 可空。
- check_name、status、metric_value、threshold_value。
- details_json、created_at。

processing_errors：

- id、job_id、job_stage、file_version_id、slide_id、export_id。
- error_code、message、details_json、created_at。

### 7.3 FTS 原子性

建立 slide_search_documents 普通表，保存 analyzer 处理后的字段和 analyzer_version；slides_fts 以该表为 external content。

索引阶段在一个短事务中完成：

1. 写入或更新 slide_search_documents。
2. 删除该 rowid 的旧 FTS 记录。
3. 插入新 FTS 记录。
4. 更新 slides.index_status。
5. 提交事务。

应用启动和手动 doctor 都检查 slides.index_status、search document 和 FTS 数量；不一致时创建 reconcile job，不直接静默修复。

---

## 8. 后台任务和可恢复提交

### 8.1 进程模型

- Web 进程只处理短请求，不执行 LibreOffice 或长时间 hash。
- worker 是同一 Python 包启动的独立进程。
- M0 只有一个 worker 消费 SQLite jobs。
- worker 使用租约领取任务；每 30 秒续租一次。
- Web 进程崩溃不影响 worker；worker 崩溃后租约到期可恢复。

### 8.2 入库阶段

    discover
      -> fingerprint
      -> hash
      -> parse
      -> render_document
      -> render_slides
      -> index
      -> commit

每个阶段必须满足：

- 输入通过不可变 ID 或 hash 引用。
- 重复执行不会生成重复记录。
- 先写临时产物，再登记 artifact writing。
- 校验通过后原子重命名并标记 artifact ready。
- checkpoint 只记录已完成且可验证的事实。

### 8.3 ready 提交条件

FileVersion 只有在 commit 阶段同时满足以下条件时才变为 ready：

- 源 file_sha256 与任务开始时一致。
- 数据库 slide 数等于声明 page_count。
- 每页 parse_status 为 ready 或有明确失败。
- 可检索页面的 search document 和 FTS 记录一致。
- 缩略图存在，或 render_status 明确为 failed。
- 所有 artifact ready 且实际文件存在、hash 匹配。

单页失败但剩余页面可搜索时为 partial_ready。源文件变化时为 stale，且不得更新 source_files.current_version_id。

### 8.4 Reconcile

应用启动执行只读快速检查：

- 过期租约。
- writing 状态超过 10 分钟的 artifact。
- published 但文件不存在的 export。
- ready artifact 的文件不存在。
- FTS 计数异常。

发现异常后创建 reconcile job。reconcile 只修复可重建派生数据，不修改源 PPT。

---

## 9. 文件发现与安全

### 9.1 扫描规则

- 仅扫描 enabled source root。
- 路径先做 Unicode NFC，再 realpath。
- 每个候选路径必须仍位于 root 内。
- 不跟随逃出 root 的符号链接。
- 忽略隐藏文件、锁文件和临时文件。
- M0 只接受扩展名和 ZIP 内容均符合 PPTX 的文件。

### 9.2 防御规则

- ZIP entry 禁止绝对路径和父目录跳转。
- 解压前累计 declared size，超过预算拒绝。
- part 数超过预算拒绝。
- XML 禁止外部实体和网络访问。
- 外部 relationship 只作为数据记录，处理器不主动请求目标。
- LibreOffice 子进程使用参数数组，不使用 shell。
- 每个 LibreOffice 任务有硬超时和独立临时 profile。
- 输出文件必须在 output root 内，使用 O_EXCL 或等价机制防止非预期覆盖。
- 扫描后打开源文件时重新读取 stat；hash 完成后再次读取 stat，变化则任务 stale。

---

## 10. 搜索施工设计

### 10.1 M0 查询能力

M0 搜索框只处理关键词，不猜测自然语言过滤条件。过滤器由 UI 明确提交：

- source_file_id。
- page_type。
- has_thumbnail。
- availability。
- created/modified date range。

### 10.2 轻量中文 analyzer

不引入大型分词模型：

1. Unicode NFC。
2. 全角半角和常见标点统一。
3. 英文小写化，保留产品名、版本号和数字组合。
4. 连续中文生成 2-gram。
5. 用户词典中的完整领域词额外保留。
6. 文件名拆分连接符、下划线和驼峰。

analyzer_version 写入 search document。规则变化必须触发 reindex job。

### 10.3 排序和解释

M0 使用 FTS5 BM25 字段权重：

- title：10。
- tags：8，为后续预留。
- filename：5。
- body：3。
- table：3。
- summary：2。
- OCR：1.5，为后续预留。
- notes：0.5。
- boilerplate：0.1。

搜索结果必须返回：

- 命中的字段。
- 命中片段。
- 来源文件和页码。
- 页面可用状态。
- 是否存在导出告警。

前 10 条默认同一文件最多 4 条；用户选择单一来源过滤时不应用该限制。

---

## 11. 选片单施工设计

### 11.1 业务规则

- 一个 selection item 固定 slide_id、file_version_id 和 source_page_number。
- 同一 slide 可以被加入多次，每次有不同 selection_item_id。
- 每次修改 selection.revision + 1。
- 排序必须形成从 1 开始的连续整数。
- 导出请求固定 selection_revision；选片单之后变化不影响已开始导出。
- 源文件 missing 或 hash 变化时，选片单仍可查看，但预检阻止导出。

### 11.2 并发

M0 是单用户，但仍使用 revision 做乐观并发：

- 前端修改携带 expected_revision。
- 不一致返回 SELECTION_REVISION_CONFLICT。
- 前端重新加载当前选片单，不自动覆盖。

---

## 12. OOXML 关系图移植

### 12.1 风险原型先行

完整开发前先完成一个仅面向冻结样本集的 spike。退出条件：

- 解析 slide 的 relationship 依赖闭包。
- 从空白目标 package 复制单页。
- 同一源复制多页并复用 master/layout/theme。
- 跨两个源复制页面并处理 part name 与 rId 冲突。
- 能复制图片、表格、图表和图表内嵌 workbook。
- 能识别视频、OLE、宏和未知 relationship。
- 输出通过结构、LibreOffice 和视觉验证。

spike 不直接进入生产模块；验证后的规则再按正式模块重写并由测试固定。

### 12.2 Package graph

每个 part 记录：

- part name。
- content type。
- 原始字节 hash。
- relationship 集合。
- internal 或 external target mode。
- 是否属于支持矩阵。

从目标 slide part 开始遍历 internal relationship，形成依赖闭包。遍历必须有 visited 集，避免循环。

### 12.3 M0 复制策略

- 目标 presentation 从项目内冻结的最小空白模板创建。
- 第一张选中页面决定目标 slide size。
- 不同 slide size 默认阻止导出并返回 INCOMPATIBLE_SLIDE_SIZE。
- 同一 source file version 的 master/layout/theme 在一次 export 内复用。
- 不同来源即使 XML hash 相同，M0 也不做跨来源语义去重。
- 所有 part name 在写入前统一规划。
- 所有目标 relationship ID 由目标 part 内重新分配。
- external hyperlink 可以保留，但生成 EXTERNAL_LINK_PRESERVED warning。
- 视频、音频、OLE、ActiveX、宏和未知对象不声称 A 级保真。
- 任一依赖缺失时该页失败；不得输出空白占位页冒充成功。

### 12.4 写包事务

    preflight
      -> build transplant plan
      -> write to temporary directory
      -> assemble temporary pptx
      -> validate package
      -> render output
      -> compare pages
      -> write manifest
      -> atomic publish

任一步失败时保留 export 和 processing error，删除可安全删除的临时文件，不发布目标 PPTX。

---

## 13. 保真验证

### 13.1 验证证据

每次导出至少生成：

| 检查 | 范围 | 失败影响 |
|---|---|---|
| zip_integrity | deck | Failed |
| required_parts | deck | Failed |
| relationship_targets | deck/page | Failed |
| content_types | deck | Failed |
| slide_count | deck | Failed |
| libreoffice_convert | deck | Failed |
| output_page_count | deck | Failed |
| object_support | page | warning、C 或 Failed |
| render_difference | page | A、B 或 C |
| source_hash_match | page | blocked |

### 13.2 图像差异

源页基准图和输出页验证图都由同一 LibreOffice 版本、相同分辨率渲染，降低环境噪声。

每页记录：

- mean absolute pixel error。
- changed pixel ratio。
- image width 和 height。
- 是否产生空白页。

初始阈值：

- A：MAE <= 0.015，changed ratio <= 0.03，且无重要 warning。
- B：MAE <= 0.05，changed ratio <= 0.10，或只有字体/外链 warning。
- C：结构可打开，但超过 B 阈值或有不支持对象。
- Failed：结构、关系、打开、页数或空白页检查失败。

阈值必须由冻结样本集校准；阈值调整要更新 validator_version 并保留旧证据。

### 13.3 发布规则

- A、B 默认允许发布。
- C 只有请求 allow_warnings=true 且用户已在预检页确认时允许发布。
- Failed 永不发布。
- deck fidelity 取所有页面中的最低等级。

---

## 14. HTTP 和页面契约

### 14.1 JSON 接口

| 方法与路径 | 用途 | 关键返回 |
|---|---|---|
| GET /api/v1/health | 存活检查 | status、version |
| GET /api/v1/doctor | 环境检查 | SQLite、FTS5、LibreOffice、目录权限 |
| GET /api/v1/source-roots | 来源目录列表 | roots |
| POST /api/v1/source-roots | 授权来源目录 | source_root |
| POST /api/v1/imports | 发起扫描入库 | job_id |
| GET /api/v1/jobs/{id} | 查询进度 | status、stage、progress、errors |
| GET /api/v1/slides | 搜索和过滤 | items、next_cursor、query_explanation |
| GET /api/v1/library/facets | 页面库固定主体/场景、页面用途和来源文件聚合 | nested topics、page_types、decks |
| GET /api/v1/slides/{id} | 页面详情 | metadata、preview、warnings |
| POST /api/v1/selections | 创建选片单 | selection |
| PATCH /api/v1/selections/{id} | 增删和排序 | selection、revision |
| POST /api/v1/selections/{id}/preflight | 导出预检 | blockers、warnings、estimated_fidelity |
| POST /api/v1/exports | 发起导出 | export_id、job_id |
| GET /api/v1/exports/{id} | 导出状态 | status、fidelity、manifest、output |

页面库浏览路由：

| 路径 | 用途 |
| --- | --- |
| GET `/library?view=content` | 按主体 → 场景 → 页面用途筛选页面 |
| GET `/library?view=source` | 文件索引，每个当前 PPTX 一个文件卡片 |
| GET `/library?view=source&deck_id=<id>` | 单文件详情和该文件的页面网格 |

页面库查询参数 `topic`、`subtopic`、`page_type`、`deck_id` 可组合使用；未知固定分类返回 `REQUEST_INVALID`。

统一成功 envelope：

    {
      "ok": true,
      "data": {},
      "request_id": "req_..."
    }

统一错误 envelope：

    {
      "ok": false,
      "error": {
        "code": "SOURCE_CHANGED",
        "message": "源文件在选片后发生变化",
        "retryable": false,
        "details": {}
      },
      "request_id": "req_..."
    }

### 14.2 幂等规则

- POST /imports：root、scan options 和 scan generation 生成 idempotency key。
- POST /exports：selection_id、selection_revision、规范化 options 生成 idempotency key。
- 相同 key 已成功时返回原结果。
- 相同 key 处理中时返回原 job。
- overwrite 不参与隐式行为；M0 始终不覆盖现有文件。

### 14.3 HTML 页面

| 页面 | 主要功能 |
|---|---|
| 初始设置 | 环境 doctor、选择来源目录和输出目录 |
| 页面库 | “按内容找/按文件找”双入口、固定分类筛选、来源文件筛选、搜索框、缩略图网格、分页、选片托盘 |
| 页面详情 | 大图、文本、来源、对象统计、兼容告警 |
| 选片单 | 排序、重复页面、删除、预检、导出 |
| 任务 | 入库/导出进度、阶段、重试、错误详情 |
| 导出历史 | 文件、保真等级、manifest、来源摘要 |

HTMX 只负责局部 HTML：

- 搜索结果。
- 选片托盘。
- job 状态，每 1 秒轮询；完成后停止。
- export 预检。

必须保留无拖拽操作：选片单每项提供上移、下移和删除按钮。拖拽只是增强。

---

## 15. 前后端功能对齐矩阵

| 用户功能 | 页面 | 应用用例 | 数据记录 | 核心测试 |
|---|---|---|---|---|
| 配置来源目录 | 初始设置 | add_source_root | source_roots | 路径 allowlist |
| 扫描入库 | 设置/任务 | import_decks | jobs、job_stages、file_versions | 中断恢复 |
| 搜索页面 | 页面库 | search_slides | search_documents、FTS | 中文召回 |
| 查看预览 | 页面详情 | get_slide | slides、artifacts | 资产缺失 |
| 加入选片单 | 页面库 | manage_selection | selections、items | revision 冲突 |
| 调整顺序 | 选片单 | reorder_selection | selection_items | 连续顺序 |
| 导出预检 | 选片单 | preflight_export | slides、source files | hash、尺寸、对象 |
| 发起导出 | 选片单 | export_selection | exports、jobs | 幂等 |
| 查看进度 | 任务 | get_job | jobs、stages | 租约恢复 |
| 下载/打开结果 | 导出历史 | get_export | exports、artifacts | output root |
| 查看来源 | 导出历史 | get_manifest | export_items | 顺序和 hash |

任何页面功能进入开发前必须能在本矩阵中找到应用用例、持久化事实和测试位置。

---

## 16. 错误处理

错误按处置方式分类：

### 16.1 用户可修复

- SOURCE_ROOT_NOT_ALLOWED。
- OUTPUT_EXISTS。
- SOURCE_MISSING。
- SOURCE_CHANGED。
- INCOMPATIBLE_SLIDE_SIZE。
- DISK_SPACE_LOW。
- LIBREOFFICE_NOT_FOUND。

前端显示具体修复动作，不显示堆栈。

### 16.2 可自动重试

- LIBREOFFICE_TIMEOUT。
- DATABASE_BUSY。
- TEMPORARY_IO_ERROR。
- WORKER_LOST。

采用指数退避，达到 max_attempts 后转 failed。

### 16.3 不可自动发布

- PPTX_PACKAGE_INVALID。
- RELATIONSHIP_TARGET_MISSING。
- OUTPUT_PAGE_COUNT_MISMATCH。
- OUTPUT_RENDER_FAILED。
- OUTPUT_BLANK_PAGE。

这些错误永远不能因 allow_warnings=true 被绕过。

错误详情写 processing_errors；日志只记录必要元数据，不记录完整页面文本或密钥。

---

## 17. 测试设计

### 17.1 测试层次

| 层次 | 目标 | 是否依赖 LibreOffice |
|---|---|---|
| unit | 纯规则、状态、hash、路径、排序 | 否 |
| contract | HTTP schema、错误 envelope、HTMX fragment | 否 |
| integration | SQLite、FTS、artifact store、job lease | 否 |
| package golden | OOXML 关系图与移植 | 部分 |
| render golden | 源/输出图像对比 | 是 |
| e2e | 浏览器完整闭环 | 是 |
| performance | 资源预算 | 是 |
| failure injection | 进程中断与恢复 | 部分 |

### 17.2 冻结样本集

至少包含：

1. 普通中文文本。
2. 中英文混排和版本号。
3. 多栏和复杂形状。
4. 表格。
5. 图片和透明图。
6. 原生图表及内嵌 workbook。
7. 多 master、多 layout。
8. 主题色和自定义字体。
9. 备注和超链接。
10. 动画。
11. 视频或音频。
12. OLE。
13. 外部 relationship。
14. 损坏 relationship 的负样本。
15. 两种 slide size 的组合样本。
16. 100 MB 以上性能样本。

样本不得包含真实敏感内容。每个样本有 fixture manifest，说明页数、对象类型、预期 warning 和允许等级。

### 17.3 必须覆盖的失败点

- hash 完成前终止。
- parse 到第 N 页终止。
- PDF 已生成但 artifact 未提交。
- 缩略图生成一半终止。
- FTS 写入前终止。
- FTS 提交后、job 更新前终止。
- export 写包中终止。
- output 已验证但原子发布前终止。
- worker 租约过期。
- 源文件在入库和导出中途变化。

每种失败都必须证明：重试不产生重复页面、重复 FTS、重复 export 或错误发布。

### 17.4 测试命令

    make lint
    make typecheck
    make test-unit
    make test-integration
    make test-contract
    make test-golden
    make test-e2e
    make test-performance
    make check

make check 是提交前门槛，至少运行 lint、typecheck、unit、integration、contract。golden、e2e 和 performance 在发布候选时执行。

---

## 18. M0 验收标准

### 18.1 环境

- macOS 13+ 的 Intel 与 Apple Silicon Python 环境均可安装。
- doctor 能识别 SQLite FTS5、LibreOffice 路径、目录权限和剩余磁盘空间。
- 缺少 LibreOffice 时文本入库和搜索仍可用，渲染与导出明确阻止。

### 18.2 入库

- 30 个 PPTX、500 页完整入库。
- 相同文件 hash 不重复解析和渲染。
- 同一内容的多个路径保留各自来源记录。
- 单页失败不阻塞其余可搜索页面。
- 任意阶段终止后恢复，无重复记录。
- ready、partial_ready、failed、stale 与实际资产一致。

### 18.3 搜索

- 20 条冻结查询 Recall@10 >= 0.85。
- MRR >= 0.65。
- 5000 页索引常用搜索 P95 < 1 秒。
- 结果显示标题、缩略图状态、来源、页码和命中证据。
- 相同 slide_package_hash 聚合后仍可展开全部来源。

### 18.4 选片

- 支持加入、重复加入、删除、上移、下移和拖拽。
- 刷新页面后顺序不丢失。
- revision 冲突不会静默覆盖。
- 源文件缺失或变化时选片单仍可查看，但预检给出 blocker。

### 18.5 导出

- 单源、多源导出的页数和顺序与选片单一致。
- 相同幂等请求只产生一个 export。
- 不同 slide size 默认阻止导出。
- 所有发布文件可由 LibreOffice 转 PDF。
- 结构失败、页数不符和空白页永不发布。
- 每个输出都有 manifest 和 validation evidence。
- A/B/C 等级可由证据重复计算。

### 18.6 安全

- 无法通过符号链接、父目录或 ZIP entry 逃出授权目录。
- 不调用外部 relationship。
- 不覆盖既有输出文件。
- 不修改、移动或删除源 PPT。
- 日志不包含完整页面文本、token 或密钥。

### 18.7 UI

- Safari 当前系统版本和最新版 Chrome 至少各完成一次 E2E。
- 首次设置、搜索、选片、预检、导出和查看结果无阻断。
- 键盘可完成搜索、加入选片、调整顺序和发起导出。
- 长任务有阶段、进度和错误反馈。

---

## 19. 开发里程碑

### M0.0 项目骨架

交付：

- Python 包、CLI、配置、日志和迁移 runner。
- FastAPI health/doctor。
- Jinja base 页面和本地静态资源。
- SQLite 临时测试数据库。
- worker 启动、租约和空 job handler。
- lint、typecheck、unit、contract 基线。

退出条件：

- pptlib doctor、pptlib serve、pptlib worker 可启动。
- make check 通过。
- 不依赖 Node、Docker 或网络 CDN。

### M0.1 OOXML 风险原型

交付：

- package graph 检查工具。
- 单源/多源复制实验。
- 支持矩阵和 fixture manifest。
- 结构与视觉验证原型。

退出条件：

- 代表性静态样本达到 A/B。
- 不支持样本产生预期 blocker 或 warning。
- 未发现会推翻包级复制路线的风险。

### M0.2 入库与搜索纵切

交付：

- source root、扫描、hash、parse、render、artifact、FTS。
- 页面库搜索网格和页面详情。
- job 状态与恢复。

退出条件：

- 500 页闭环。
- 中文检索指标达标。
- 中断恢复测试通过。

### M0.3 选片与导出纵切

交付：

- selection revision、排序和重复页面。
- preflight、transplant、validation、manifest。
- 导出历史页面。

退出条件：

- 五组跨文件金样通过。
- 发布规则和幂等测试通过。

### M0.4 可靠性和发布候选

交付：

- reconcile、磁盘预算、安全限制和错误 UX。
- 全量 E2E、性能和失败注入报告。
- 安装、升级、备份和故障恢复说明。

退出条件：

- 全部 M0 验收条目有证据。
- 无 P0/P1 未解决缺陷。

---

## 20. 开发任务完成定义

每个任务只有同时满足以下条件才算完成：

1. 行为在应用用例层有明确输入和输出。
2. 失败模式使用稳定错误码。
3. 数据库迁移可从空库执行。
4. 迁移不依赖手工修库。
5. 至少有一个先失败后通过的自动测试。
6. Web 行为同时覆盖 JSON 或 HTML 契约。
7. 长任务有幂等、进度和恢复语义。
8. 涉及文件写入时使用临时文件和原子发布。
9. 涉及 OOXML 时更新支持矩阵或金样。
10. make check 通过。

---

## 21. 开发启动顺序

正式编码按以下顺序展开：

1. 建立 M0.0 骨架和测试命令。
2. 写领域 ID、状态和错误码测试。
3. 写 SQLite migration runner 和 repository contract。
4. 写 job lease 与 stage ledger 的失败测试。
5. 完成 OOXML M0.1 风险原型。
6. 风险原型通过后实现入库与搜索纵切。
7. 实现选片和导出纵切。
8. 完成 UI、E2E 和发布候选验收。

不在 OOXML 风险原型通过前实现 OCR、embedding、智能体工具或桌面壳。

---

## 22. 设计自检结论

- 无未决技术栈选择。
- M0 与后续阶段已分开。
- 前端功能均能映射到应用用例、数据库记录和测试。
- Selection、Job、Artifact、Export 的 v1 模型缺口已闭合。
- OOXML 复制先风险验证、后生产实现。
- 运行时不需要 Node、Docker、Redis 或外部数据库。
- 验收标准包含功能、恢复、性能、安全和 UI。
- 当前目录不是 Git 仓库，因此本文档无法提交 commit；进入开发前可按需初始化 Git。
