# 拼页 PinPage · 好页拼成好稿（Electron）

本地图形化控制台，**全程本地、无云端依赖**。三步工作流，实时显示 `pptlib` 运行日志：

1. **导入并渲染** — 选择本地 PPTX，抽取文本、分类、按页渲染缩略图/预览。
2. **选片** — 在本地页库里以真实缩略图网格浏览、按主题/类型筛选、搜索、多选，右侧
   已选清单可拖拽排序；选好后一键进入组合。
3. **组合导出** — 按选片顺序用原生 OOXML 复制拼出新 PPTX（保真，非重渲染图片）。

源 PPTX 与所有重活、缩略图都留在本地；缩略图经内置 `pptlib-asset://` 协议喂给渲染层。

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
