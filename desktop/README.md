# PPT 页库控制台（Electron）

本地图形化控制台。它**不做浏览/选片**（选片在妙搭 Web），只把四个本地任务做成按钮，
并实时显示 `pptlib` 的运行日志：

1. **导入并渲染** — 选择本地 PPTX，抽取文本、分类、按页渲染缩略图/预览。
2. **同步到妙搭** — 导出 catalog，用本机已登录的 `lark-cli` 把缩略图 + 元数据推到妙搭应用。
3. **本地组合导出** — 读回从妙搭下载的选片 `manifest.json`，按序拼出 PPTX。

源 PPTX 与所有重活都留在本地；妙搭只承载缩略图和元数据。

## 运行

```bash
cd desktop
npm install            # 安装 electron
npm start              # 启动控制台
```

`main.js` 从仓库根解析 `.venv/bin/pptlib`，默认 `PPTLIB_HOME=../var/dev`
（可用环境变量覆盖）。所有能力经 `preload.js` 的白名单 bridge 暴露给渲染层，渲染层不
直接接触 Node 或 CLI。

## 打包

```bash
npm run dist           # electron-builder 产出 dmg（需已装 electron-builder）
```

## 冒烟自检

```bash
PPTLIB_SMOKE=1 npm start   # 启动后 1.5s 自动退出，用于验证应用能正常拉起
```

## 说明

- 若在受限网络下 `npm install` 未能下载 Electron 二进制（`dist/` 只有 stub），
  用缓存的 zip 解压到 `node_modules/electron/dist/` 后 `codesign --force --deep --sign -`
  即可；缓存通常在 `~/Library/Caches/electron/`。
- `node_modules/` 与 `dist/` 已在根 `.gitignore` 中忽略。
