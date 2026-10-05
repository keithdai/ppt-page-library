# PPT 页库控制台（本地轻量版）

本地 macOS 演示页级素材库：把 PPTX 和标准 render-deck HTML 拆到「页」级别做索引、
分类和预览，再统一搜索与选片。**源文件只留在本地且不复制**。当前 PPTX 页面可原生组合
导出；HTML 已完成第一阶段的入库、静态/动态预览和选片，组合输出将在后续阶段开放。

不需要 Docker，也不需要 Node/Redis/外部数据库/CDN。整套跑在本地 Python + 一个可选的
Electron 控制台里，内存占用接近裸 Python。

## 组成

| 层 | 作用 |
| --- | --- |
| `pptlib`（Python/FastAPI CLI） | 统一导入、分类、检索、预览、catalog 和 PPTX 组合导出 |
| PPTX 渲染器 | LibreOffice 高保真分段渲染，officecli 作为失败回退 |
| HTML 适配器 | exact backfill、轻量依赖清单、安全浏览器截图和隔离动态预览 |
| Electron 控制台 | 本地导入、搜索、选片、动态预览和组合导出 |

## 环境要求

- macOS 13+，Python 3.11。
- LibreOffice —— PPTX 默认高保真渲染引擎。
- 本机 Chrome（HTML 静态/动态预览和 officecli 回退渲染需要）。
- [officecli](https://d.officecli.ai/install.sh)（可选）—— LibreOffice 失败时的回退引擎。
- lark-cli（已登录到目标妙搭租户）—— 同步 catalog 到妙搭时需要。
- Node 18+（仅当要用 Electron 控制台时）。

## 快速开始（CLI）

    make install
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib init
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib doctor
    PPTLIB_HOME="$PWD/var/dev" .venv/bin/pptlib import ./sources

桌面端默认启用 HTML。CLI 需要显式开启：

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
「自动更新」模块支持多文件夹、格式/大小规则、预检、立即更新、应用内定时执行、
安全停止和运行历史；不安装系统级后台任务。

产品化与工程化路线图见
[`docs/PinPage-productization-engineering-roadmap.md`](docs/PinPage-productization-engineering-roadmap.md)，
组合导出实测见
[`docs/compose-export-verification.md`](docs/compose-export-verification.md)。

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
