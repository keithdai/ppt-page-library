# 拼页 PinPage · 好页拼成好稿（Electron）

本地图形化控制台，**全程本地、无云端依赖**。主要工作流实时显示 `pptlib` 运行日志：

1. **导入并渲染** — 选择本地 PPTX、标准 render-deck HTML 或 HTML Deck ZIP，抽取文本、
   分类并按页生成缩略图/预览。
2. **选片** — 在本地页库里以真实缩略图网格浏览、按主题/类型/格式筛选、搜索、多选，右侧
   已选清单可拖拽排序；选好后一键进入组合。
3. **组合导出** — 按选片顺序用原生 OOXML 复制拼出新 PPTX（保真，非重渲染图片）。
4. **重复管理** — 本地识别、对比和清理重复页面。
5. **自动更新** — 配置多文件夹、格式、大小和时间规则，支持预检、立即更新、停止及运行历史。

源文件与所有重活、缩略图都留在本地；缩略图经内置 `pptlib-asset://` 协议喂给渲染层。
HTML 页面支持隔离动态预览，但第一阶段暂不支持 HTML 组合输出或转 PPTX。

## 运行

```bash
cd desktop
npm install            # 安装 electron
npm start              # 启动控制台
```

开发态下，`main.js` 从仓库根解析 `.venv/bin/pptlib`，默认
`PPTLIB_HOME=../var/dev`（可用环境变量覆盖）。打包态改用
`Contents/Resources/runtime/pptlib/pptlib` 内置 sidecar，数据写入 Electron
`userData/data`，不再依赖源码仓库或开发 `.venv`。所有能力经 `preload.js` 的白名单
bridge 暴露给渲染层，渲染层不直接接触 Node 或 CLI。

从旧目录版升级时，如果新数据目录尚未建立且已保存的仓库 `var/dev/pages.db` 仍存在，
打包版会继续使用原页库，避免升级后显示为空；后续再通过“移动数据目录”流程完成复制迁移。

HTML 在开发态默认开启，可用 `PPTLIB_ENABLE_HTML=0` 关闭。首个独立安装包默认关闭
HTML，等待 HTML 转换引擎和浏览器一并进入固定运行时。动态预览使用独立、无 preload 的
sandbox `BrowserWindow`，只允许访问一次性 loopback capability URL 下的清理后页面和依赖。

自动更新调度器运行在 Electron 主进程内，不安装 `launchd` 或 `cron`。拼页退出或电脑休眠
时不会执行；导入、组合、重复扫描和自动更新全局互斥。停止任务时会等待当前文件完成清理，
再结束后续文件。

## 打包

```bash
npm run runtime        # 构建 PyInstaller onedir sidecar
npm run dist:dir       # 构建可直接运行的 .app
npm run dist           # 构建 sidecar，并产出可分发 ZIP
npm run dist:dmg       # 发布机构建 DMG
```

打包需要 `uv`。本地无 Developer ID 时，构建钩子会对 `.app` 做 ad-hoc 签名，便于本机
验证；正式分发仍需在 CI/签名机配置 Developer ID、hardened runtime 和 notarization。
PDF 光栅化使用 sidecar 内置的 PDFium；LibreOffice 是当前唯一的系统前置依赖，首次启动
向导会检查并阻止不完整导入。

## 冒烟自检

```bash
PPTLIB_SMOKE=1 npm start   # 启动后 1.5s 自动退出，用于验证应用能正常拉起
```

## 说明

- 若在受限网络下 `npm install` 未能下载 Electron 二进制（`dist/` 只有 stub），
  用缓存的 zip 解压到 `node_modules/electron/dist/` 后 `codesign --force --deep --sign -`
  即可；缓存通常在 `~/Library/Caches/electron/`。
- `node_modules/` 与 `dist/` 已在根 `.gitignore` 中忽略。
