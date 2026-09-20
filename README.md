# PPT 页库控制台（本地轻量版）

本地 macOS PPT 页级素材库：把多份 PPTX 拆到「页」级别做索引、分类、按页渲染缩略图/预览，
再挑选组合导出成新的 PPTX。**源 PPTX 只留在本地**，缩略图和页面描述同步到妙搭，选片在
妙搭 Web 完成，选完回本地组合导出。

不需要 Docker，也不需要 Node/Redis/外部数据库/CDN。整套跑在本地 Python + 一个可选的
Electron 控制台里，内存占用接近裸 Python。

## 组成

| 层 | 作用 |
| --- | --- |
| `pptlib`（Python/FastAPI CLI） | 导入、分类、渲染、catalog 导出、妙搭同步、本地组合导出 |
| officecli | 按页渲染缩略图/高清预览（默认引擎，比整包转 PDF 快且不产生大中间文件） |
| 妙搭应用（full_stack） | 只存缩略图 + 元数据，负责在线浏览/搜索/选片 |
| Electron 控制台（可选） | 图形化驱动上面的本地任务：导入、渲染进度、同步、组合导出 |

## 环境要求

- macOS 13+，Python 3.11。
- [officecli](https://d.officecli.ai/install.sh) —— 按页渲染引擎，且需要一个 headless
  浏览器（本机 Chrome / Playwright Chromium 即可）。
- LibreOffice（可选）—— 作为无浏览器环境下的渲染回退。
- lark-cli（已登录到目标妙搭租户）—— 同步 catalog 到妙搭时需要。
- Node 18+（仅当要用 Electron 控制台时）。

## 快速开始（CLI）

    make install
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib init
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib doctor
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib import ./sources

### 渲染引擎选择

导入时按页渲染缩略图（`thumbnail_long_edge`，默认 640）和高清预览
（`preview_long_edge`，默认 1440）。引擎由 `PPTLIB_RENDERER` 控制：

- `auto`（默认）：优先 officecli，缺浏览器时自动回退 LibreOffice。
- `officecli`：只用 officecli 按页渲染（常驻 + 单页截图，适合大文件）。
- `libreoffice`：只用 PPTX→PDF→pdftoppm 整包管线（无浏览器环境）。

<!-- prettier-ignore -->
    PPTLIB_RENDERER=officecli PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib import ./sources

## 同步到妙搭

先导出本地 catalog（`catalog.json`，记录每页元数据 + 缩略图相对路径），再用本机
lark-cli 把缩略图上传到妙搭应用文件存储、把元数据 upsert 进应用数据库表
`slides_catalog`：

    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib catalog ./var/dev/catalog
    # 先干跑看命令，确认无误后去掉 --dry-run
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib sync --app-id app_xxxxxxxx --dry-run
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib sync --app-id app_xxxxxxxx --environment online

妙搭里只有 `slide_id` + 元数据 + 一张缩略图；源 PPTX 不上传。

## 从妙搭选片 → 本地组合导出

在妙搭 Web 里挑页，导出一个只含**有序 `slide_id` 列表**的 `manifest.json`
（支持三种结构：裸数组、`{"slide_ids": [...]}`、`{"items": [{"slide_id","order"}...]}`）。
把它下载到本地，然后：

    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib compose ./manifest.json ./out/composed.pptx

`compose` 把每个 `slide_id` 映射回本地源 PPTX 的对应页，用 OOXML 原生页面组合器
（`src/pptlib/export/ooxml.py`）拼出新 PPTX，并写出来源 manifest。默认校验源文件
SHA-256（源已变更会拒绝），需要跳过时加 `--no-verify-hash`。

## Electron 控制台（可选，图形化）

    cd desktop
    npm install
    npm start

控制台把上面四步做成按钮：选择 PPTX → 导入并渲染（实时日志）→ 同步到妙搭 → 选择下载的
manifest.json → 组合导出。它只是本地任务的图形外壳，所有重活和源文件都在本地。

## 本地 Web（可选）

仍保留一个只绑定 `127.0.0.1` 的本地 Web，用于本地浏览/选片：

    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib serve

## Verify

    make check

## 固定页面分类

每页得到一个业务主题（8×4 矩阵）、一个子主题、一个页面用途。分类是确定性本地规则：
标题证据权重最高，其次章节上下文、文件名、正文、备注；短语规则和负向关键词消歧，无需
模型或网络。页面用途是正交过滤维度：`封面与目录`、`观点与结论`、`对比与矩阵`、
`流程与步骤`、`时间线与路线图`、`组织架构`、`数据图表`、`表格与清单`、`案例与证言`、
`总结与行动`。

API 与卡片还暴露 `confidence`（high/medium/low）、`classification_source`（auto/manual）、
`classifier_version`。低置信页仍可检索，带「待确认」标记；`manual` 行在重分类时保留。
