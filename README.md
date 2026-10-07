# DeckAtlas

**Find the right slide. Build the next deck.**

DeckAtlas 是一款本地优先的 macOS 演示页库。它把散落在不同文件夹中的历史 PPTX
拆成可搜索、可预览、可重新组稿的页面，帮助高频做方案的人更快找到并复用已有内容。
源文件不上传、不复制，索引、预览和选片记录都留在本机。

当前 Beta 支持 PPTX 导入、页级搜索、高清预览、跨文件选片、拖拽排序、可编辑 PPTX
组合导出、重复管理和目录自动更新。HTML 适配仍是开发能力，发布包默认关闭。

[产品网站](website/) · [试用手册](docs/DeckAtlas-v0.3.0-beta.1-trial-manual.md) ·
[产品化路线图](docs/DeckAtlas-productization-engineering-roadmap.md)

## 组成

| 层 | 作用 |
| --- | --- |
| `pptlib`（Python CLI） | 统一导入、分类、检索、预览、catalog 和 PPTX 组合导出 |
| PPTX 渲染器 | LibreOffice 高保真分段渲染，officecli 作为失败回退 |
| HTML 适配器 | exact backfill、轻量依赖清单、安全浏览器截图和隔离动态预览 |
| Electron 控制台 | 本地导入、搜索、选片、动态预览和组合导出 |

## 当前兼容性

- macOS 13+，当前预览构建仅提供 Apple Silicon 版本。
- 需要安装 LibreOffice，作为 PPTX 预览渲染引擎。
- 当前 Beta 尚未完成 Apple Developer ID 签名与公证。
- 从源码运行需要 Python 3.11；开发桌面端还需要 Node 18+。
- Chrome 和 [officecli](https://d.officecli.ai/install.sh) 仅用于开发态 HTML 与回退渲染。

## 快速开始（CLI）

    make install
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib init
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib doctor
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib import ./sources

桌面开发态默认启用 HTML；`v0.3.0-beta.1` Beta 打包版默认关闭。CLI 需要显式开启：

    PPTLIB_ENABLE_HTML=1 PPTLIB_HOME="$PWD/var/dev" \
      .venv/bin/pptlib import --html ./sources

HTML 第一阶段只接受带 `fs-deck-generator=render-deck`、`.slide-frame` 和稳定
`data-slide-key` 的本地 `.html` / `.htm`，以及包含唯一 `index.html` 的安全 ZIP bundle。
来源脚本、事件、iframe、表单和外部网络不会进入隔离预览。

### 渲染引擎选择

导入时按页渲染缩略图（`thumbnail_long_edge`，默认 640）和高清预览
（`preview_long_edge`，默认 1920；16:9 页面为 1920×1080）。引擎由
`PPTLIB_RENDERER` 控制：

- `libreoffice`（默认）：使用分段 PPTX→PDF→内置 PDFium 管线（每段最多 16 页）。
- `auto`：优先 LibreOffice，失败时自动回退 officecli。
- `officecli`：只用 officecli 按页渲染（常驻 + 单页截图）。

<!-- prettier-ignore -->
    PPTLIB_RENDERER=officecli PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib import ./sources

已解析文件缺少缩略图或高清预览时，可原地补图，不会重新解析 PPTX：

    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib render-missing

为避免渲染器读取内网或本地文件，PPTX 中的外链图片、音视频和外部对象会被拒绝；
普通网页超链接，以及内嵌视频生成的 `video → NULL` 安全占位关系不受影响。请先把
真实外链媒体嵌入 PPTX，再重新渲染。

## 同步到妙搭（兼容能力）

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

控制台提供本地闭环：选择 PPTX / HTML → 导入并渲染 → 搜索与选片 → PPTX 组合导出。
HTML 页面会显示格式、视频和兼容性状态，并可按需打开独立 sandbox 动态预览窗口。
页库直接从 SQLite 分页读取，不再为每次刷新生成全量 `catalog.json`；选片顺序与任务状态
会持久化，窗口刷新或应用重启后可恢复。
「自动更新」模块支持多文件夹、格式/大小规则、预检、立即更新、应用内定时执行、
安全停止和运行历史；不安装系统级后台任务。

产品化与工程化路线图见
[`docs/DeckAtlas-productization-engineering-roadmap.md`](docs/DeckAtlas-productization-engineering-roadmap.md)，
本周试用方法与注意事项见
[`docs/DeckAtlas-v0.3.0-beta.1-trial-manual.md`](docs/DeckAtlas-v0.3.0-beta.1-trial-manual.md)，
组合导出实测见
[`docs/compose-export-verification.md`](docs/compose-export-verification.md)。

## Verify

    make check

## 固定页面分类

每页得到一个业务主题（8×4 矩阵）、一个子主题、一个页面用途。分类是确定性本地规则：
标题证据权重最高，其次章节上下文、文件名、正文、备注；短语规则和负向关键词消歧，无需
模型或网络。页面用途是正交过滤维度：`封面与目录`、`观点与结论`、`对比与矩阵`、
`流程与步骤`、`时间线与路线图`、`组织架构`、`数据图表`、`表格与清单`、`案例与证言`、
`总结与行动`。

索引数据与卡片还暴露 `confidence`（high/medium/low）、`classification_source`（auto/manual）、
`classifier_version`。低置信页仍可检索，带「待确认」标记；`manual` 行在重分类时保留。
