# PPT 页库：本地 PPT 页级索引与组合工具

## 详细技术设计文档

- 版本：v1.0
- 日期：2026-07-16
- 首发平台：macOS
- 文档状态：开发指导稿

---

## 1. 摘要

本项目建设一个运行在 macOS 本机的 PPT 页面资产库，用于解决以下问题：

1. 从大量本地 PPT 中按页面检索内容。
2. 通过缩略图、文本和视觉摘要快速判断页面是否可复用。
3. 按用户指定顺序从多个源 PPT 中抽取页面并合并为新的 .pptx。
4. 为未来智能体提供安全、稳定、结构化的页面搜索和导出能力。

本设计的核心原则是：

- 页面是检索和组合的基本单元。
- 文件版本是入库、导出和一致性校验的边界。
- 原始 PPT 永远只读，所有派生文件都可重建。
- 入库先保证文本可检索，再异步生成视觉和 AI 派生信息。
- 智能体负责选择和编排页面，专用文件处理器负责 PPT 操作。
- 首版明确静态页面保真范围，不对动画、视频和嵌入对象做无条件承诺。

推荐实现：Python 应用层 + SQLite/FTS5 + LibreOffice headless 渲染 + PyMuPDF 缩略图生成 + OOXML 包级页面复制与合并。未来可增加 PowerPoint for Mac 自动化作为高保真导出后端，但不作为首版硬依赖。

---

## 2. 目标与非目标

### 2.1 目标

首版必须形成可用闭环：

~~~text
指定本地文件夹
    -> 发现并识别 PPT
    -> 逐页提取文本和元数据
    -> 生成缩略图
    -> 建立中文关键词索引
    -> 搜索并预览页面
    -> 形成选片单
    -> 按顺序导出新 PPTX
    -> 校验并返回来源清单
~~~

### 2.2 非目标

以下能力不属于 M0 的强制范围：

- 云端多人协作和权限管理。
- 对源 PPT 的原地修改。
- 复杂的统一母版重排。
- 自动生成完整演示逻辑和逐页讲稿。
- 无条件保留所有动画、切换、宏、视频、音频和嵌入对象。
- 直接支持旧式 .ppt 的全部特性。
- 让智能体执行任意 shell 命令或访问任意本机路径。

---

## 3. 产品范围和交付阶段

### 3.1 M0：可用核心管线

范围：

- macOS 本地运行。
- 支持 .pptx 文件夹批量导入。
- 文件级哈希去重和页面级索引。
- 提取标题、正文、表格、备注和基本元数据。
- LibreOffice 转 PDF，再按页生成缩略图。
- SQLite FTS5 中文关键词搜索。
- 结果分页、缩略图预览、源文件和页码展示。
- 创建选片单、调整顺序、导出合并 PPTX。
- 输出文件结构校验、页数校验和来源 manifest。
- 命令行和最小 Web UI 任选其一，推荐先实现服务层和 CLI。

M0 不要求每页都调用 AI。

### 3.2 M1：检索增强

- 图片文字 OCR。
- 视觉摘要和页面类型识别。
- 结构化关键词、实体和视觉元素。
- 向量 embedding 和混合检索。
- 相似页面聚类和重复页面提示。
- 异步任务队列、失败重试和断点恢复。
- 文件/页面标签。

### 3.3 M2：智能体和日常使用能力

- 稳定的内部 API 和 tool schema。
- 预览-确认-导出的安全流程。
- 选片单持久化、复用和版本校验。
- 导出历史和来源追溯。
- 轻量 Web UI：搜索网格、选片托盘、预览、导出进度。
- 用户点击、加入选片单和移除结果等反馈信号。

### 3.4 M3：高保真兼容后端

- PowerPoint for Mac 自动化后端。
- 复杂母版、动画、视频和嵌入对象兼容性增强。
- 高保真后端与 OOXML 后端的自动选择和降级策略。

---

## 4. 平台和技术决策

### 4.1 macOS 运行约束

首发版本默认运行在用户的 macOS 本机：

- Python 3.11 或更高版本。
- LibreOffice headless 用于 PPTX/PPT 到 PDF 的转换。
- PyMuPDF 用于 PDF 分页渲染。
- SQLite 用于元数据和全文索引。
- 文件处理在后台 worker 进程中运行，避免阻塞 UI。
- 默认不要求安装 Microsoft PowerPoint。

如果调用 PowerPoint for Mac 自动化能力，需要额外的应用控制和辅助功能权限，必须作为显式可选后端，不得在首次启动时隐式请求。

### 4.2 页面复制方案

首版的页面复制采用 OOXML 包级处理，而不是只调用 python-pptx 的公开高层 API。

原因：PPTX 是 ZIP 包，页面依赖 slide layout、slide master、theme、图片、图表、媒体和 relationship。只复制 slide XML 或只复制 shape，不能保证目标文件可打开或页面完整。

页面复制器必须具备以下能力：

1. 读取源 PPTX 的 ZIP 包结构。
2. 定位目标 slide part 和对应关系文件。
3. 复制 slide、layout、master、theme、media、chart、embedding 等依赖。
4. 为目标包创建新的 part 名称和 relationship ID。
5. 更新 slide XML 内的 r:embed、r:link 等引用。
6. 处理目标演示文稿已有的内容类型注册。
7. 清理未使用的临时资源。
8. 输出后使用 ZIP/OOXML 结构校验器和 LibreOffice 重新打开验证。

### 4.3 三种后端的职责

| 后端 | 首版状态 | 主要职责 | 主要风险 |
|---|---|---|---|
| OOXML package backend | 必须实现 | 页面提取、页面合并、静态内容保真 | 关系迁移复杂 |
| LibreOffice backend | 必须安装 | PPTX/PPT 转 PDF、缩略图渲染、基础验证 | 与 PowerPoint 视觉差异 |
| PowerPoint Mac backend | 后续可选 | 高保真页面复制和验证 | 依赖 Office、权限和前台状态 |

OOXML 后端失败时不能静默生成空白或降级错误的页面。必须返回结构化错误，并指出是否可以选择其他后端重试。

---

## 5. 总体架构

~~~mermaid
flowchart TB
    User[用户或智能体]
    API[应用服务层 / Tool API]
    Search[检索服务]
    Selection[选片单服务]
    Import[入库编排器]
    Enrich[异步增强任务]
    Export[导出编排器]
    Parser[PPTX 解析器]
    Renderer[LibreOffice + PDF 渲染器]
    Copier[OOXML 页面复制器]
    Validator[文件验证器]
    DB[(SQLite 元数据 + FTS5)]
    Assets[(缩略图与派生资产)]
    Sources[(本地源 PPT，只读)]

    User --> API
    API --> Search
    API --> Selection
    API --> Import
    API --> Export
    Import --> Sources
    Import --> Parser
    Parser --> DB
    Import --> Renderer
    Renderer --> Assets
    Enrich --> DB
    Enrich --> Assets
    Search --> DB
    Search --> Assets
    Selection --> DB
    Export --> Sources
    Export --> Copier
    Copier --> Validator
    Validator --> Export
    Export --> DB
~~~

### 5.1 模块边界

| 模块 | 责任 | 不负责 |
|---|---|---|
| discovery | 文件发现、路径规范化、哈希、去重 | 解析页面内容 |
| parser | 提取 XML 文本、标题、表格、备注和页面元数据 | 生成最终摘要 |
| renderer | 转 PDF、生成缩略图、渲染验证图 | 建立检索索引 |
| enrichment | OCR、视觉摘要、关键词、页面类型、embedding | 修改源 PPT |
| indexer | 规范化字段、写入 SQLite、更新 FTS | 文件渲染 |
| search | 查询解析、召回、过滤、融合、重排 | 页面复制 |
| selection | 管理选片单和顺序 | 直接读写 PPT 包 |
| exporter | 读取选片单、复制页面、生成输出 manifest | 生成搜索摘要 |
| validator | 校验目标 PPTX 可打开、页数和来源 | 自动修复复杂页面 |
| agent_api | 向智能体暴露受限结构化操作 | 接受任意 shell 或任意路径 |

每个模块通过领域对象或 JSON schema 交互，避免让 UI、智能体直接依赖 ZIP/XML 细节。

---

## 6. 核心领域对象

### 6.1 SourceFile

表示用户配置的一个源文件位置。路径可以移动或失效。

~~~json
{
  "source_file_id": "sf_01J...",
  "canonical_path": "/Users/name/PPT/architecture.pptx",
  "display_name": "architecture.pptx",
  "kind": "pptx",
  "status": "active",
  "current_version_id": "fv_01J...",
  "last_seen_at": "2026-07-16T10:00:00+08:00"
}
~~~

### 6.2 FileVersion

表示某一时刻的不可变文件版本。

~~~json
{
  "file_version_id": "fv_01J...",
  "source_file_id": "sf_01J...",
  "sha256": "...",
  "size_bytes": 104857600,
  "mtime_ns": 123456789,
  "page_count": 56,
  "parser_version": "parser-0.1",
  "status": "ready"
}
~~~

### 6.3 Slide

表示一个特定文件版本中的一页。slide_id 是智能体和选片单使用的稳定引用。

~~~json
{
  "slide_id": "sl_01J...",
  "file_version_id": "fv_01J...",
  "page_number": 3,
  "title": "数据中台总体架构",
  "page_type": "architecture",
  "content_hash": "...",
  "status": "ready"
}
~~~

slide_id 不只由文件路径和页码组成，因为路径可能变化、页面也可能插入。导出时仍必须同时携带 file_version_id、源文件 hash 和页码进行二次校验。

### 6.4 Selection

表示一组有顺序的页面选择。

~~~json
{
  "selection_id": "sel_01J...",
  "name": "客户 A 数据平台方案",
  "items": [
    {"slide_id": "sl_01J...", "order": 1},
    {"slide_id": "sl_01K...", "order": 2}
  ],
  "status": "ready",
  "source_snapshot": "validated"
}
~~~

---

## 7. 入库设计

### 7.1 入库总规则

1. 默认扫描用户明确指定的文件夹，不扫描整个磁盘。
2. 默认只接收 .pptx；.ppt 进入转换分支并标记兼容性风险。
3. 忽略临时文件、隐藏文件、Office 锁文件，例如 ~$*.pptx。
4. 目录遍历必须限制在用户授权的根目录内，禁止通过符号链接逃逸。
5. 文件先计算 size、mtime 和 SHA-256，再决定是否重复处理。
6. 相同 SHA-256 的文件不重复执行解析和渲染；每个来源路径仍保留自己的版本记录，派生资产通过 hash 共享。
7. 文件版本必须先写入 processing 状态，全部基础解析完成后才能变为 ready。
8. 单页解析失败不阻断同一文件其他页面，但文件必须保留失败计数和错误明细。
9. 原文件只读，入库过程不得改写、重命名或移动原始文件。
10. 所有派生资产都必须使用相对路径或内容寻址路径，不能把绝对路径写入可迁移的索引记录。

### 7.2 入库流水线

~~~text
discover
  -> identify
  -> create file_version(processing)
  -> parse slides
  -> persist base metadata
  -> render PDF and thumbnails
  -> update FTS
  -> enqueue enrichment
  -> validate counts
  -> file_version ready
~~~

#### 阶段 A：发现和身份识别

输出：SourceFile、候选 FileVersion。

规则：

- 路径使用 realpath 规范化。
- 文件名采用 Unicode NFC 规范化。
- 先比较文件大小和修改时间，只有无法排除变化时才计算完整 SHA-256。
- 大文件哈希必须以分块方式计算，禁止一次性读入内存。
- 同一 hash 的多个路径可以复用解析、渲染和 embedding 结果，但每个 source_file 仍保留自己的 file_version 记录，保证来源和导出校验明确。
- 文件被删除或不可访问时，不立刻删除索引，标记 source_missing，保留搜索结果并禁止导出。

#### 阶段 B：基础解析

每页至少提取：

- 页码，使用 1-based 编号。
- 页面内部关系标识和 layout 名称。
- 标题候选。
- 所有可提取文本。
- 表格单元格文本。
- 演讲者备注。
- 超链接文本和目标摘要（不保存敏感目标内容）。
- 图片、图表、视频、音频、嵌入对象数量。
- 页面宽高比、布局和文本框数量。

文本清洗规则：

- Unicode NFC 规范化。
- 去除不可见控制字符。
- 保留原始字段，另生成搜索规范化字段。
- 合并连续空白，但保留段落边界。
- 页眉、页脚和重复版权信息作为 boilerplate_text 单独记录并降低权重。
- 表格按行列序列化，单元格之间使用明确分隔符。
- 不把备注混入正文，避免普通检索被备注噪声污染。

标题候选优先级：

1. 标题占位符中的文本。
2. 顶部区域中字号最大的文本框。
3. 页面第一个非页眉文本块。
4. AI 生成候选标题。

#### 阶段 C：渲染和缩略图

首选流程：

~~~text
PPTX -> LibreOffice PDF -> PyMuPDF 按页渲染 -> 缩略图
~~~

必须一次将整份 PPT 转为 PDF，再按页处理，禁止每一页重新启动 LibreOffice。

资产规则：

- 搜索网格默认生成 640px 长边的 JPEG/WebP，质量约 82。
- 预览图按需生成 1440px 长边版本。
- 使用 file_version_id/page_number-content_hash.jpg 的内容路径。
- PDF 仅作为临时产物，任务结束后删除或按配置保留。
- 缩略图生成失败不影响文本检索，但页面状态应为 thumbnail_failed。

#### 阶段 D：增强处理

增强任务必须异步，且不阻塞基础入库。

默认触发视觉/AI 处理的条件：

- 可提取文本字符数小于 80。
- 页面图片区域占比明显较高。
- 页面含图表、流程图、架构图或截图。
- 用户主动要求重新生成摘要。

文本丰富且没有明显视觉信息的页面，可以只生成文本摘要，不强制调用多模态模型。

AI 供应商必须抽象为 provider 接口，支持：

- disabled：只用本地文本和规则。
- internal：调用内部模型服务。
- local：未来接入本地模型。

默认配置为 disabled，除非用户显式启用内部模型。任何远程调用都记录 provider、模型版本、耗时和失败原因，不记录密钥。

#### 阶段 E：提交和状态变更

基础解析、缩略图和索引任务完成后，进行一致性检查：

- 数据库页面数等于解析页数或明确记录缺失页。
- FTS 记录数与可检索页面数一致。
- 缩略图路径存在或有明确失败状态。
- 文件版本 hash 与导入开始时相同。

通过后将文件版本设置为 ready。如果源文件在处理期间发生变化，当前任务设置为 stale，不将结果标记为当前版本。

### 7.3 入库状态机

~~~text
discovered
  -> hashing
  -> processing
  -> parsed
  -> rendered
  -> indexed
  -> ready

processing/parsed/rendered/indexed
  -> partial_ready  (存在单页失败)
  -> failed         (文件级失败)
  -> stale          (源文件发生变化)
~~~

重试规则：

- 文件级失败最多自动重试 2 次。
- 单页失败最多重试 2 次。
- AI/OCR 失败不回滚基础索引。
- 手动重新索引可从指定阶段开始，不必重复计算 hash。

---

## 8. 数据库设计

推荐使用 SQLite，开启 WAL 模式。所有写入通过应用层事务完成；后台并发任务使用单写入队列或短事务，避免多个 worker 长时间占用写锁。

### 8.1 核心表

~~~sql
CREATE TABLE source_files (
    id              TEXT PRIMARY KEY,
    canonical_path  TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('pptx', 'ppt')),
    status          TEXT NOT NULL DEFAULT 'active',
    current_version_id TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_source_files_path
ON source_files(canonical_path);

CREATE TABLE file_versions (
    id              TEXT PRIMARY KEY,
    source_file_id  TEXT NOT NULL REFERENCES source_files(id),
    sha256          TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    mtime_ns        INTEGER,
    page_count      INTEGER,
    parser_version  TEXT NOT NULL,
    status          TEXT NOT NULL,
    error_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    ready_at        TEXT
);

CREATE UNIQUE INDEX idx_file_versions_source_hash
ON file_versions(source_file_id, sha256);

CREATE INDEX idx_file_versions_content_hash
ON file_versions(sha256);

CREATE TABLE slides (
    id              TEXT PRIMARY KEY,
    file_version_id TEXT NOT NULL REFERENCES file_versions(id),
    page_number     INTEGER NOT NULL,
    internal_name   TEXT,
    title           TEXT,
    body_text       TEXT,
    table_text      TEXT,
    speaker_notes   TEXT,
    boilerplate_text TEXT,
    ocr_text        TEXT,
    visual_summary  TEXT,
    page_type       TEXT,
    keywords_json   TEXT,
    entities_json   TEXT,
    visual_elements_json TEXT,
    content_hash    TEXT NOT NULL,
    thumbnail_path  TEXT,
    preview_path    TEXT,
    status          TEXT NOT NULL DEFAULT 'processing',
    created_at      TEXT NOT NULL,
    UNIQUE(file_version_id, page_number)
);

CREATE INDEX idx_slides_file_page
ON slides(file_version_id, page_number);

CREATE TABLE tags (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    normalized_name TEXT NOT NULL UNIQUE
);

CREATE TABLE slide_tags (
    slide_id        TEXT NOT NULL REFERENCES slides(id),
    tag_id          TEXT NOT NULL REFERENCES tags(id),
    source          TEXT NOT NULL DEFAULT 'user',
    PRIMARY KEY(slide_id, tag_id)
);

CREATE TABLE slide_embeddings (
    slide_id        TEXT NOT NULL REFERENCES slides(id),
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    vector_path     TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY(slide_id, provider, model)
);

CREATE TABLE selections (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ready',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE selection_items (
    selection_id    TEXT NOT NULL REFERENCES selections(id),
    slide_id        TEXT NOT NULL REFERENCES slides(id),
    sort_order      INTEGER NOT NULL,
    added_reason    TEXT,
    PRIMARY KEY(selection_id, slide_id),
    UNIQUE(selection_id, sort_order)
);

CREATE TABLE jobs (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    target_id       TEXT,
    status          TEXT NOT NULL,
    progress        REAL NOT NULL DEFAULT 0,
    attempt         INTEGER NOT NULL DEFAULT 0,
    error_code      TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);

CREATE TABLE exports (
    id              TEXT PRIMARY KEY,
    selection_id    TEXT NOT NULL REFERENCES selections(id),
    output_path     TEXT NOT NULL,
    output_sha256   TEXT,
    backend         TEXT NOT NULL,
    status          TEXT NOT NULL,
    page_count      INTEGER,
    manifest_path   TEXT,
    warnings_json   TEXT,
    created_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE TABLE export_items (
    export_id       TEXT NOT NULL REFERENCES exports(id),
    output_order    INTEGER NOT NULL,
    slide_id        TEXT,
    source_file_version_id TEXT,
    source_page_number INTEGER,
    status          TEXT NOT NULL,
    warning_codes_json TEXT,
    PRIMARY KEY(export_id, output_order)
);

CREATE TABLE processing_errors (
    id              TEXT PRIMARY KEY,
    job_id          TEXT REFERENCES jobs(id),
    file_version_id TEXT REFERENCES file_versions(id),
    slide_id        TEXT REFERENCES slides(id),
    stage           TEXT NOT NULL,
    error_code      TEXT NOT NULL,
    message         TEXT NOT NULL,
    details_json    TEXT,
    created_at      TEXT NOT NULL
);
~~~

### 8.2 FTS5 设计

中文搜索不能直接依赖默认英文分词。应用层生成规范化和分词字段，再写入 FTS5：

~~~sql
CREATE VIRTUAL TABLE slides_fts USING fts5(
    slide_id UNINDEXED,
    title_tokens,
    filename_tokens,
    body_tokens,
    table_tokens,
    summary_tokens,
    ocr_tokens,
    visual_tokens,
    notes_tokens,
    tags_tokens,
    tokenize = 'unicode61'
);
~~~

应用层分词规则：

- 中文使用分词结果和 2-gram 结果组合。
- 英文、数字、版本号、产品名保留完整 token。
- 大小写、全角半角、常见标点统一。
- 领域词典支持“数据中台”“数据平台”等词组。
- 文件名中的连接符、下划线和驼峰拆分。
- 同义词扩展必须来自受控词典，不允许模型无限扩展查询。

---

## 9. 检索设计

### 9.1 检索目标

检索结果的最小单位是页面，不是文本段落。每条结果必须能够被预览、加入选片单和导出。

用户输入的自然语言需要被拆成：

- 主查询词。
- 过滤条件。
- 页面类型约束。
- 来源文件约束。
- 标签约束。
- 结果数量和排序偏好。

例如：

~~~text
找金融行业的数据中台架构图，优先最新方案，不要目录页
~~~

可解析为：

~~~json
{
  "query": "数据中台",
  "filters": {"tags": ["金融"]},
  "page_types": ["architecture"],
  "exclude_page_types": ["toc"],
  "sort_preference": "recent"
}
~~~

### 9.2 召回阶段

M0 使用关键词召回：

- FTS5 标题、标签、文件名、摘要、正文、表格、OCR、视觉描述和备注。
- 取每个字段的 Top-K，再合并为候选集。
- 标题精确命中和标签命中优先。

M1 增加向量召回：

- 查询向量与页面文本/视觉画像向量分别召回。
- 关键词和语义结果使用 Reciprocal Rank Fusion 合并。
- 不直接比较 FTS 分数和向量余弦分数，因为两者量纲不同。

推荐初始融合公式：

~~~text
rrf = 1 / (60 + lexical_rank)
    + 0.8 / (60 + semantic_rank)
    + 0.2 / (60 + visual_rank)
~~~

不存在某路召回结果时，该项为 0。实际权重必须使用固定评测集调参。

### 9.3 重排阶段

对候选结果执行以下加权：

| 信号 | 初始权重方向 |
|---|---|
| 标题精确包含查询词 | 强加分 |
| 标签完全命中 | 强加分 |
| 文件名命中 | 中等加分 |
| 摘要命中 | 中等加分 |
| 正文/表格命中 | 基础加分 |
| OCR 命中 | 低于正文 |
| 视觉描述命中 | M1 后中等加分 |
| 页眉页脚命中 | 降权 |
| 目录页、封面页 | 按查询意图处理 |
| 页面过度重复 | 降权或聚合 |

结果不显示未经校准的“百分比匹配度”，而显示“高度相关”“相关”“可能相关”，同时返回命中证据。

### 9.4 去重和多样性

结果处理规则：

- 相同 content_hash 的页面合并为一个结果，并显示多个来源。
- 视觉相似度很高但文本不同的页面归入“相似页面”。
- 默认前 10 条结果中同一文件最多出现 4 条。
- 用户可切换“同一来源优先”或“跨来源优先”。
- 组装演示文稿时，检索只是提供候选；页面顺序和叙事完整性由选片单流程处理。

### 9.5 搜索结果结构

~~~json
{
  "slide_id": "sl_01J...",
  "title": "数据中台总体架构",
  "file_name": "architecture.pptx",
  "page_number": 3,
  "thumbnail_url": "/assets/thumb/...",
  "page_type": "architecture",
  "summary": "展示数据采集、治理、服务和应用层的关系",
  "score_label": "高度相关",
  "matched_fields": ["title", "visual_summary"],
  "match_reason": "标题命中“数据中台”，视觉描述识别为分层架构图",
  "duplicate_group_id": null,
  "warnings": []
}
~~~

---

## 10. 页面复制和导出设计

### 10.1 导出输入

导出器只接受选片单和显式导出选项：

~~~json
{
  "selection_id": "sel_01J...",
  "output_name": "客户A-数据平台方案.pptx",
  "backend": "ooxml",
  "overwrite": false,
  "validate": true,
  "allow_warnings": true
}
~~~

导出前必须检查：

- 选片单不为空。
- 页面顺序连续且无重复冲突。
- 每个页面仍然存在。
- 源文件可读取。
- 源文件 SHA-256 与选片单记录一致。
- 输出路径在允许的工作目录内。
- overwrite=false 时目标文件不存在。

### 10.2 推荐内部流程

~~~text
读取 selection
  -> 锁定源文件版本
  -> 重新校验每个源文件 hash
  -> 创建临时目标 PPTX
  -> 按 selection_items 顺序复制页面
  -> 记录 export_items
  -> 结构验证
  -> LibreOffice 打开/转换验证
  -> 生成 manifest
  -> 原子重命名为最终输出
~~~

不要长期保存每一页的中间 PPTX。页面引用使用 source_file_version_id + page_number，最终导出时一次性合并，减少磁盘占用和重复解析。

### 10.3 保真度等级

导出结果必须标明保真度等级：

| 等级 | 含义 |
|---|---|
| A | 文本、形状、图片、表格、图表和静态布局通过验证 |
| B | 页面可打开，但部分关系资源或字体可能发生变化 |
| C | 已生成，但存在未支持对象，必须人工检查 |
| Failed | 文件无法验证或页面复制失败 |

M0 的目标是常见静态页面达到 A 或 B。遇到动画、视频、音频、宏、ActiveX、OLE 或复杂嵌入对象时，至少记录以下 warning：

~~~text
UNSUPPORTED_ANIMATION
UNSUPPORTED_VIDEO
UNSUPPORTED_OLE
FONT_MAY_SUBSTITUTE
MASTER_MAPPING_CHANGED
~~~

不能把有 warning 的结果标记为完全保真。

### 10.4 输出 manifest

每个 PPTX 旁边生成同名 .manifest.json：

~~~json
{
  "export_id": "exp_01J...",
  "output_file": "客户A-数据平台方案.pptx",
  "created_at": "2026-07-16T12:00:00+08:00",
  "backend": "ooxml",
  "fidelity_level": "A",
  "slides": [
    {
      "output_order": 1,
      "slide_id": "sl_01J...",
      "source_file": "architecture.pptx",
      "source_sha256": "...",
      "source_page_number": 3,
      "status": "copied",
      "warnings": []
    }
  ],
  "validation": {
    "zip_valid": true,
    "slide_count_match": true,
    "libreoffice_opened": true
  }
}
~~~

---

## 11. 智能体调用设计

### 11.1 工具边界

智能体不能直接执行任意文件操作。首版向智能体暴露以下工具：

~~~text
search_slides(query, filters)
get_slide(slide_id)
get_slide_preview(slide_id, size)
create_selection(name, slide_ids)
update_selection(selection_id, operations)
validate_selection(selection_id)
export_deck(selection_id, output_name, options)
get_export_status(export_id)
~~~

### 11.2 工具设计原则

- 参数使用 JSON schema，拒绝未知字段。
- 所有 ID 必须是系统生成的 ID，禁止智能体伪造本地路径替代 ID。
- 搜索返回 match_reason，方便智能体解释选择依据。
- 预览和最终导出分成两个步骤。
- 导出默认不覆盖文件、不删除文件。
- 同一个 selection_id + export options 重复请求应当幂等。
- 导出前重新校验源文件版本。
- 返回结构化 warning，不把 warning 藏在自然语言日志中。
- 对输出文件使用允许目录和文件名净化规则。

### 11.3 智能体推荐流程

~~~text
理解用户目标
  -> search_slides
  -> get_slide_preview
  -> create_selection
  -> validate_selection
  -> 向用户展示候选页面和顺序
  -> 用户确认
  -> export_deck
  -> get_export_status
  -> 返回 PPTX 和来源摘要
~~~

智能体不应仅依据 AI 摘要自动导出。至少对最终页面执行预览或读取基础元数据，并向用户展示页面来源和兼容性告警。

### 11.4 智能体安全约束

- 允许的源目录由用户显式配置。
- 目录路径必须经过规范化并检查是否在 allowlist 内。
- 禁止访问系统目录、密钥目录和应用数据目录。
- 禁止通过 ../、符号链接或 shell 插值绕过路径检查。
- 导出目标只能是配置的 output 目录。
- 默认不覆盖同名文件。
- 不提供删除源文件的工具。
- AI 服务只接收必要的页面图像或派生文本，不默认上传完整 PPT。
- 日志中不得打印 access token、密钥或完整敏感文本。

---

## 12. API 和命令行接口

M0 可先实现 CLI，内部接口保持 JSON 形态，未来再挂 Web API 或智能体工具。

### 12.1 导入

~~~bash
pptlib import \
  --root "/Users/name/PPT" \
  --recursive \
  --render thumbnails \
  --enrichment disabled
~~~

返回：

~~~json
{
  "job_id": "job_01J...",
  "status": "accepted",
  "discovered": 100,
  "skipped_duplicate": 12
}
~~~

### 12.2 搜索

~~~bash
pptlib search "数据中台架构" \
  --page-type architecture \
  --limit 20 \
  --format json
~~~

### 12.3 导出

~~~bash
pptlib export \
  --selection sel_01J... \
  --output "/Users/name/PPT/output/customer-a.pptx" \
  --backend ooxml \
  --validate
~~~

### 12.4 错误返回格式

~~~json
{
  "ok": false,
  "error": {
    "code": "SOURCE_CHANGED",
    "message": "源文件在选片后发生变化",
    "retryable": false,
    "details": {
      "source_file": "architecture.pptx",
      "expected_sha256": "...",
      "actual_sha256": "..."
    }
  }
}
~~~

---

## 13. 错误码和恢复策略

| 错误码 | 含义 | 是否可重试 |
|---|---|---|
| UNSUPPORTED_FORMAT | 文件格式不支持 | 否，转格式后重试 |
| SOURCE_MISSING | 源文件不存在 | 否，恢复文件后重试 |
| SOURCE_CHANGED | 源文件 hash 已变化 | 否，重新入库后重建选片 |
| PARSER_FAILED | PPTX 结构解析失败 | 可手动重试 |
| RENDER_FAILED | LibreOffice/PDF 渲染失败 | 可重试 |
| THUMBNAIL_FAILED | 单页缩略图失败 | 可单页重试 |
| OCR_FAILED | OCR 失败 | 可重试，不影响基础检索 |
| AI_PROVIDER_FAILED | AI provider 调用失败 | 可重试，不影响基础检索 |
| RELATIONSHIP_COPY_FAILED | 页面依赖关系复制失败 | 可切换后端或人工检查 |
| OUTPUT_INVALID | 输出 PPTX 验证失败 | 不应自动发布 |
| OUTPUT_EXISTS | 目标文件已存在 | 用户显式覆盖后重试 |
| PATH_NOT_ALLOWED | 路径不在 allowlist 内 | 否，调整配置 |
| DISK_SPACE_LOW | 临时磁盘空间不足 | 清理后重试 |

恢复原则：先保留已经完成的基础结果，再针对失败阶段重试；不得因为 AI 摘要失败而删除已有文本索引。

---

## 14. 性能和资源策略

原设计中的“无大小限制”改为资源边界明确、失败可恢复：

- 大文件按块计算 hash。
- PPT 解析在独立进程中运行，结束后释放内存。
- PDF 和缩略图使用临时目录，任务完成后清理。
- 任务记录已处理页码，支持断点恢复。
- AI/OCR 有并发上限和速率限制。
- SQLite 写入采用短事务。
- 缩略图和 embedding 使用 content hash 缓存。
- 页面检索优先读取数据库字段，不实时解析 PPT。

建议首轮压测指标：

| 场景 | 目标 |
|---|---|
| 100 页普通 PPT 基础入库 | 能稳定完成并记录耗时 |
| 500 页批量导入 | 过程中不阻塞 UI，失败可恢复 |
| 5000 页索引搜索 | 常用搜索 P95 小于 1 秒 |
| 20 页跨文件导出 | 输出页数、顺序和来源全部正确 |
| 100MB 文件 | 不出现一次性内存暴涨 |
| 大文件失败 | 不影响其他文件索引 |

1GB 文件不作为首版硬性性能承诺，先通过真实样本测量 CPU、内存、磁盘和 LibreOffice 转换时间，再确定生产上限。

---

## 15. 文件和目录结构

~~~text
ppt-page-lib/
├── pyproject.toml
├── README.md
├── src/pptlib/
│   ├── cli.py
│   ├── config.py
│   ├── domain/
│   │   ├── models.py
│   │   ├── states.py
│   │   └── errors.py
│   ├── discovery/
│   │   ├── scanner.py
│   │   ├── hashing.py
│   │   └── identity.py
│   ├── parser/
│   │   ├── pptx_parser.py
│   │   ├── ppt_converter.py
│   │   └── text_normalizer.py
│   ├── renderer/
│   │   ├── libreoffice.py
│   │   ├── pdf_renderer.py
│   │   └── thumbnails.py
│   ├── enrichment/
│   │   ├── ocr.py
│   │   ├── page_classifier.py
│   │   ├── ai_provider.py
│   │   └── embeddings.py
│   ├── index/
│   │   ├── schema.py
│   │   ├── repository.py
│   │   ├── fts.py
│   │   └── vector_store.py
│   ├── search/
│   │   ├── query_parser.py
│   │   ├── lexical.py
│   │   ├── semantic.py
│   │   ├── fusion.py
│   │   └── reranker.py
│   ├── selection/
│   │   └── service.py
│   ├── export/
│   │   ├── exporter.py
│   │   ├── ooxml_backend.py
│   │   ├── powerpoint_backend.py
│   │   ├── manifest.py
│   │   └── validator.py
│   ├── jobs/
│   │   ├── queue.py
│   │   ├── worker.py
│   │   └── retry.py
│   └── agent_api/
│       ├── schemas.py
│       └── tools.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── golden/
│   ├── fixtures/
│   └── performance/
└── data/
    ├── pages.db
    ├── assets/
    ├── previews/
    ├── vectors/
    ├── jobs/
    └── exports/
~~~

原始 PPT 不建议复制到仓库的 data 目录。M0 默认保存源文件引用和 hash；未来的“冻结版本”能力可以使用 macOS APFS clone 或受控复制保存源文件快照。

---

## 16. 测试设计

### 16.1 样本集

建立一个不可随意变更的本地测试集，至少包含：

- 普通文本页。
- 多栏排版页。
- 表格页。
- 图表页。
- 图片和截图页。
- 架构图、流程图和复杂连线页。
- 含中文、英文、数字和中英文混排页。
- 含备注、超链接和特殊字体页。
- 含动画、视频、OLE 或嵌入对象的兼容性样本。
- 一个较大的 PPT，用于内存和耗时测试。

### 16.2 单元测试

- 路径规范化和 allowlist 校验。
- hash、重复文件和文件版本识别。
- 标题提取优先级。
- 中文文本规范化和分词。
- 表格序列化。
- FTS 查询构造和字段权重。
- query parser 的过滤条件解析。
- RRF 融合和结果去重。
- selection 排序、重复和删除。
- manifest 生成。
- 错误码映射。

### 16.3 集成测试

- PPTX 解析后页数和文本数量正确。
- PPTX 转 PDF 后页数一致。
- 缩略图路径可读取。
- FTS 能召回中文、英文和数字组合查询。
- 多个源文件按指定顺序导出。
- 导出后能被 LibreOffice 重新打开。
- 源文件变化后导出被拒绝。
- 单页失败不影响其他页面完成。
- 重试不会产生重复的页面或索引。

### 16.4 导出金样测试

对每个代表性页面进行：

1. 导出前记录源页面截图。
2. 导出后用 LibreOffice 转 PDF。
3. 对输出页渲染截图。
4. 使用图像差异和人工抽检确认视觉变化。
5. 记录页面元素是否完整。

重点检查：

- 文字位置和字体。
- 图片、图表和表格。
- 母版背景和主题色。
- 超链接。
- 备注。
- 动画、视频和嵌入对象告警。

### 16.5 检索评测

建立至少 20 条真实查询，每条标注可接受结果。跟踪：

- Recall@10。
- MRR。
- 首次找到可用页面的时间。
- 同一文件结果占比。
- 重复页面比例。
- 用户加入选片单的结果比例。

---

## 17. 安全、隐私和可靠性

### 17.1 文件安全

- 只读源文件。
- 所有路径先 realpath，再做 allowlist 校验。
- 输出目录与源目录分离。
- 默认不覆盖、不删除。
- 使用临时文件写出，验证通过后原子重命名。
- 临时目录权限设置为当前用户可读写。
- 不在日志中记录完整文件内容。

### 17.2 AI 数据边界

- AI provider 默认关闭。
- 启用远程内部模型前显示数据处理说明。
- 默认只发送缩略图、必要文本和派生字段。
- 不发送完整源 PPT，除非用户显式配置并授权。
- 缓存中记录模型版本和页面 hash，不保存不必要的原始响应。

### 17.3 一致性

- 所有文件版本和 selection 使用不可变 ID。
- 导出前重新读取并校验 source hash。
- 任务状态和输出状态分离。
- 输出失败时不发布半成品。
- 数据库事务只覆盖元数据，不把长时间文件操作包在 SQLite 事务中。

---

## 18. 实施顺序

### 第 1 步：环境和样本验证

- 检查 macOS、Python、LibreOffice、PyMuPDF。
- 准备真实 PPT 样本集。
- 测试 PPTX 转 PDF 的页数、速度和字体表现。
- 选择一个包含图片、表格、图表和母版的样本进行页面复制实验。

### 第 2 步：基础入库

- 实现 discovery、hash 和 file version。
- 实现 PPTX 基础解析。
- 建立 SQLite schema。
- 写入每页基础字段和 FTS。
- 先用 CLI 验证可重复导入。

### 第 3 步：渲染和搜索

- 实现整份 PPT 转 PDF。
- 实现缩略图生成和缓存。
- 实现中文分词和 FTS 搜索。
- 输出命中字段和匹配原因。

### 第 4 步：页面复制和导出

- 先实现单源文件单页抽取。
- 再实现同一文件多页合并。
- 再实现多个源文件跨文件合并。
- 加入关系 ID 重写、资源复制和 manifest。
- 加入 LibreOffice 打开验证。

### 第 5 步：选片单和最小 UI

- 实现 selection 和排序。
- 提供缩略图网格和预览。
- 允许用户查看来源、页码和告警。
- 导出前显示最终页面顺序。

### 第 6 步：增强和智能体接口

- 接入 OCR、页面类型和结构化视觉摘要。
- 加入向量索引和混合检索。
- 固化工具 JSON schema。
- 加入预览确认、幂等导出和源版本检查。

---

## 19. M0 验收标准

### 入库

- 给定一个目录，能发现所有 .pptx 文件并跳过 Office 临时文件。
- 相同 hash 的文件不会重复解析。
- 每页都有页码、标题候选、正文、缩略图状态和源文件版本。
- 单页解析失败不会阻塞其他页面。
- 任务中断后可以从已完成页继续。

### 检索

- 中文关键词可以命中标题、正文、表格和文件名。
- 结果展示缩略图、文件名、页码、标题、摘要和命中原因。
- 可以按源文件过滤；标签过滤在 M1 增强阶段提供。
- 同一内容的重复页面可以聚合或明确标注。

### 组合

- 可以从单个或多个 PPT 中选择页面。
- 可以调整页面顺序。
- 导出的 PPTX 页数和顺序与选片单一致。
- 输出文件能够被 LibreOffice 打开。
- 目标文件旁有来源 manifest。
- 源文件发生变化时，导出会阻止并给出 SOURCE_CHANGED。
- 不支持的对象会产生 warning，不会被伪装成完全保真。

### 智能体

- 智能体可通过结构化工具搜索页面、读取预览、创建选片单和发起导出。
- 智能体无法任意访问路径、覆盖源文件或删除文件。
- 导出结果能返回页面来源、状态、告警和校验信息。

---

## 20. 关键风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| OOXML 页面关系复制不完整 | 导出文件打不开或资源丢失 | 先做小样本金样测试；分阶段支持元素；失败禁止发布 |
| LibreOffice 与 PowerPoint 渲染差异 | 缩略图与用户实际打开效果不同 | 显示渲染后端；保留 PowerPoint 后端扩展点 |
| 中文 FTS 召回差 | 搜索无法满足主要场景 | 应用层分词 + 2-gram + 真实查询集评测 |
| 大 PPT 内存或磁盘压力 | 入库超时或崩溃 | 独立进程、分块 hash、临时空间检查、断点恢复 |
| AI 调用成本或隐私问题 | 入库变慢或数据外传 | 异步、按需、缓存、provider 开关默认关闭 |
| 源文件被移动或替换 | 选片单无法导出 | hash 和版本校验；支持重新定位和重新入库 |
| 智能体误选页面 | 输出内容不符合意图 | 返回命中证据；强制预览/确认；来源可追溯 |
| 多个 worker 同时写 SQLite | 数据库锁和状态错乱 | 单写入队列、短事务、任务幂等 |

---

## 21. 开发约束总结

实现过程中应遵循以下硬规则：

1. 不修改原始 PPT。
2. 不用文件路径和页码单独作为智能体的长期引用。
3. 不把每页中间 PPTX 作为长期索引资产。
4. 不让 AI 摘要阻塞基础入库。
5. 不把 FTS5 的原始 rank 当作百分比匹配度。
6. 不在没有关系迁移和输出验证的情况下声称页面复制成功。
7. 不让智能体直接运行任意命令。
8. 不因为单页增强失败而丢弃基础索引。
9. 不在源文件 hash 变化后继续静默导出。
10. 不把不支持的动画、视频、OLE 或字体问题隐藏起来。

这份设计的首要验证点是：使用真实 PPT 样本完成“入库 → 中文检索 → 选片 → 跨文件导出 → 验证”的闭环。闭环稳定后，再逐步加入视觉理解、语义检索和智能体自动编排。
