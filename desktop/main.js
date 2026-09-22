'use strict';

// Electron control console for the local PPT page library.
//
// A fully local, three-step flow: import + render, browse/select from the local
// page library (real thumbnails), and compose the selection back into a PPTX.
// The desktop app drives the local `pptlib` CLI; all heavy work and the source
// PPTX files stay on disk — nothing is uploaded.

const { app, BrowserWindow, ipcMain, dialog, shell, protocol } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

// Custom scheme to serve local thumbnail/preview images to the renderer. A
// file://-loaded page can't reliably read images from other directories, so
// catalog thumbnails are served through `pptlib-asset://local/<encoded-abs>`.
protocol.registerSchemesAsPrivileged([
  { scheme: 'pptlib-asset', privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

function assetUrl(absPath) {
  return `pptlib-asset://local/${encodeURIComponent(absPath)}`;
}

// Where the pptlib repo lives. In dev the desktop/ folder sits inside the repo,
// so its parent is the root. Once packaged into a .app that assumption breaks
// (__dirname points inside Resources/app.asar), so resolution is layered:
//   1. PPTLIB_REPO_ROOT env override (highest priority)
//   2. a persisted choice from a previous run (userData/repo-root.txt)
//   3. dev fallback: the parent of desktop/
// A root is only accepted if it actually contains src/pptlib.
function repoRootConfigPath() {
  return path.join(app.getPath('userData'), 'repo-root.txt');
}

function isRepoRoot(dir) {
  return !!dir && fs.existsSync(path.join(dir, 'src', 'pptlib'));
}

function resolveRepoRoot() {
  const candidates = [];
  if (process.env.PPTLIB_REPO_ROOT) candidates.push(process.env.PPTLIB_REPO_ROOT);
  try {
    const saved = fs.readFileSync(repoRootConfigPath(), 'utf8').trim();
    if (saved) candidates.push(saved);
  } catch {
    /* no persisted choice yet */
  }
  candidates.push(path.resolve(__dirname, '..'));
  return candidates.find(isRepoRoot) || path.resolve(__dirname, '..');
}

let REPO_ROOT = resolveRepoRoot();

function persistRepoRoot(dir) {
  REPO_ROOT = dir;
  try {
    fs.writeFileSync(repoRootConfigPath(), dir, 'utf8');
  } catch {
    /* best effort */
  }
}

function defaultHome() {
  return path.join(REPO_ROOT, 'var', 'dev');
}

function pptlibBinary() {
  const venv = path.join(REPO_ROOT, '.venv', 'bin', 'pptlib');
  return fs.existsSync(venv) ? venv : 'pptlib';
}

function childEnv() {
  return {
    ...process.env,
    PPTLIB_HOME: process.env.PPTLIB_HOME || defaultHome(),
  };
}

/**
 * Run a pptlib subcommand, streaming stdout/stderr lines to the renderer and
 * resolving with the last JSON object printed on stdout (pptlib prints one).
 */
function runPptlib(args, webContents, { onLine } = {}) {
  return new Promise((resolve, reject) => {
    if (!isRepoRoot(REPO_ROOT)) {
      reject(
        new Error(
          '未找到 pptlib 仓库（缺少 src/pptlib）。请在设置里选择仓库目录，或用 PPTLIB_REPO_ROOT 环境变量指定。',
        ),
      );
      return;
    }
    const child = spawn(pptlibBinary(), args, { cwd: REPO_ROOT, env: childEnv() });
    let stdout = ''; // accumulates only non-progress stdout (for final JSON parse)
    let stderr = '';
    let outBuf = ''; // line buffer so progress markers survive chunk splits
    const PROGRESS_PREFIX = '@@PPTLIB_PROGRESS ';
    const emit = (channel, text) => {
      if (webContents && !webContents.isDestroyed()) {
        webContents.send('log', { channel, text });
      }
      if (onLine) onLine(channel, text);
    };
    const emitProgress = (event) => {
      if (webContents && !webContents.isDestroyed()) {
        webContents.send('progress', event);
      }
    };
    const handleStdoutLine = (line) => {
      if (line.startsWith(PROGRESS_PREFIX)) {
        // Progress markers drive the UI directly and are kept out of both the
        // log stream and the buffer the final-result JSON is parsed from.
        try {
          emitProgress(JSON.parse(line.slice(PROGRESS_PREFIX.length)));
        } catch (_) {
          /* ignore a malformed progress line */
        }
        return;
      }
      stdout += line + '\n';
      if (line) emit('stdout', line);
    };
    child.stdout.on('data', (buf) => {
      outBuf += buf.toString();
      let nl;
      while ((nl = outBuf.indexOf('\n')) >= 0) {
        handleStdoutLine(outBuf.slice(0, nl));
        outBuf = outBuf.slice(nl + 1);
      }
    });
    child.stderr.on('data', (buf) => {
      stderr += buf.toString();
      buf.toString().split(/\r?\n/).forEach((line) => line && emit('stderr', line));
    });
    child.on('error', (error) => reject(error));
    child.on('close', (code) => {
      if (outBuf) handleStdoutLine(outBuf); // flush any trailing partial line
      let parsed = null;
      const match = stdout.match(/\{[\s\S]*\}\s*$/);
      if (match) {
        try {
          parsed = JSON.parse(match[0]);
        } catch (_) {
          parsed = null;
        }
      }
      if (code === 0) {
        resolve({ code, parsed, stdout, stderr });
      } else {
        const message = (parsed && parsed.message) || stderr.trim() || `exit ${code}`;
        reject(new Error(message));
      }
    });
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1120,
    minHeight: 680,
    title: '幻页 · 本地 PPT 页库',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  return win;
}

app.whenReady().then(() => {
  // Serve local catalog thumbnails to the renderer via pptlib-asset://.
  protocol.handle('pptlib-asset', (request) => {
    try {
      const encoded = request.url.replace(/^pptlib-asset:\/\/local\//, '');
      const abs = decodeURIComponent(encoded);
      if (!fs.existsSync(abs)) return new Response('not found', { status: 404 });
      const data = fs.readFileSync(abs);
      const ext = path.extname(abs).toLowerCase();
      const type = ext === '.png' ? 'image/png' : ext === '.webp' ? 'image/webp' : 'image/jpeg';
      return new Response(data, { headers: { 'content-type': type } });
    } catch (error) {
      return new Response(String(error), { status: 500 });
    }
  });
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
  // Smoke check hook: `PPTLIB_SMOKE=1 electron .` boots, then exits cleanly so
  // CI/local verification can confirm the app wires up without a display.
  if (process.env.PPTLIB_SMOKE === '1') {
    setTimeout(() => app.quit(), 1500);
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// --------------------------------------------------------------------------- //
// IPC handlers
// --------------------------------------------------------------------------- //

ipcMain.handle('paths', () => ({
  repoRoot: REPO_ROOT,
  repoRootValid: isRepoRoot(REPO_ROOT),
  home: childEnv().PPTLIB_HOME,
  pptlib: pptlibBinary(),
}));

// Persist an ordered slide_id selection as a manifest.json under PPTLIB_HOME so
// the existing compose flow consumes it unchanged.
ipcMain.handle('write-manifest', async (event, slideIds) => {
  const ids = Array.isArray(slideIds)
    ? slideIds.map((v) => String(v).trim()).filter(Boolean)
    : [];
  if (ids.length === 0) throw new Error('没有可用的 slide_id');
  const dir = path.join(childEnv().PPTLIB_HOME, 'selections');
  fs.mkdirSync(dir, { recursive: true });
  const savePath = path.join(dir, `manifest-${Date.now()}.json`);
  fs.writeFileSync(savePath, JSON.stringify({ slideIds: ids }, null, 2), 'utf8');
  return { path: savePath, count: ids.length };
});

// Let the user point the app at their pptlib checkout (needed after packaging,
// where the .app no longer sits inside the repo).
ipcMain.handle('pick-repo-root', async () => {
  const result = await dialog.showOpenDialog({
    title: '选择 pptlib 仓库目录（包含 src/pptlib）',
    properties: ['openDirectory'],
  });
  if (result.canceled || result.filePaths.length === 0) return { ok: false, repoRoot: REPO_ROOT };
  const chosen = result.filePaths[0];
  if (!isRepoRoot(chosen)) {
    return { ok: false, repoRoot: REPO_ROOT, error: '该目录下没有 src/pptlib，不是有效的 pptlib 仓库' };
  }
  persistRepoRoot(chosen);
  return { ok: true, repoRoot: REPO_ROOT, home: childEnv().PPTLIB_HOME, pptlib: pptlibBinary() };
});

ipcMain.handle('pick-pptx', async () => {
  const result = await dialog.showOpenDialog({
    title: '选择要导入的 PPTX（源文件仅留在本地）',
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'PowerPoint', extensions: ['pptx'] }],
  });
  return result.canceled ? [] : result.filePaths;
});

// Pick one or more folders; the backend scans them recursively for PPTX files.
ipcMain.handle('pick-folder', async () => {
  const result = await dialog.showOpenDialog({
    title: '选择要扫描导入的文件夹（递归查找 PPTX，源文件仅留在本地）',
    properties: ['openDirectory', 'multiSelections'],
  });
  return result.canceled ? [] : result.filePaths;
});

ipcMain.handle('pick-manifest', async () => {
  const result = await dialog.showOpenDialog({
    title: '选择选片清单 manifest.json',
    properties: ['openFile'],
    filters: [{ name: 'Manifest', extensions: ['json'] }],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('pick-output-pptx', async () => {
  const result = await dialog.showSaveDialog({
    title: '组合结果保存为',
    defaultPath: path.join(app.getPath('downloads'), 'composed.pptx'),
    filters: [{ name: 'PowerPoint', extensions: ['pptx'] }],
  });
  return result.canceled ? null : result.filePath;
});

// Import indexes each chosen PPTX *in place* — the original file's path is
// recorded as the canonical source, so nothing is copied and no local space is
// consumed. Compose later reads directly from the original locations.
ipcMain.handle('import', async (event, filePaths) => {
  if (!Array.isArray(filePaths) || filePaths.length === 0) {
    throw new Error('未选择任何文件');
  }
  const results = [];
  for (const source of filePaths) {
    const res = await runPptlib(['import', source], event.sender);
    results.push(res.parsed || {});
  }
  return results;
});

ipcMain.handle('catalog', async (event, outputDir) => {
  const target = outputDir || path.join(childEnv().PPTLIB_HOME, 'catalog');
  const res = await runPptlib(['catalog', target], event.sender);
  return res.parsed;
});

// Load the local catalog for in-app grid selection. This regenerates
// catalog.json from the local index, then attaches a resolvable asset URL for
// each slide's thumbnail (assets live under PPTLIB_HOME/assets). Selection then
// happens entirely locally.
ipcMain.handle('load-catalog', async (event) => {
  const home = childEnv().PPTLIB_HOME;
  const target = path.join(home, 'catalog');
  await runPptlib(['catalog', target], event.sender);
  const catalogPath = path.join(target, 'catalog.json');
  const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'));
  const assetsDir = path.join(home, 'assets');
  const slides = (catalog.slides || []).map((s) => {
    const thumbAbs = path.join(assetsDir, s.thumbnail_file || '');
    const hasThumb = s.thumbnail_file && fs.existsSync(thumbAbs);
    // The renderer also produces a high-resolution preview per page under
    // assets/previews/{slide_id}.jpg — used for zoom/lightbox. Fall back to the
    // thumbnail when a preview is missing (e.g. an older partial render).
    const previewAbs = path.join(assetsDir, 'previews', `${s.slide_id}.jpg`);
    const hasPreview = fs.existsSync(previewAbs);
    return {
      slide_id: s.slide_id,
      deck_id: s.deck_id,
      deck_name: s.deck_name,
      slide_number: s.slide_number,
      title: s.title,
      topic: s.topic,
      subtopic: s.subtopic,
      page_type: s.page_type,
      thumbnail_url: hasThumb ? assetUrl(thumbAbs) : '',
      preview_url: hasPreview ? assetUrl(previewAbs) : hasThumb ? assetUrl(thumbAbs) : '',
    };
  });
  return { slide_count: slides.length, slides };
});

// Remove decks or individual slides from the local index. This only clears the
// local index + cached thumbnails; the original PPTX files are never touched.
ipcMain.handle('remove-deck', async (event, deckIds) => {
  const ids = (Array.isArray(deckIds) ? deckIds : [deckIds]).filter(Boolean);
  if (ids.length === 0) throw new Error('未提供要删除的文件');
  const args = ['remove'];
  ids.forEach((id) => args.push('--deck', String(id)));
  const res = await runPptlib(args, event.sender);
  return res.parsed;
});

ipcMain.handle('remove-slide', async (event, slideIds) => {
  const ids = (Array.isArray(slideIds) ? slideIds : [slideIds]).filter(Boolean);
  if (ids.length === 0) throw new Error('未提供要删除的页面');
  const args = ['remove'];
  ids.forEach((id) => args.push('--slide', String(id)));
  const res = await runPptlib(args, event.sender);
  return res.parsed;
});

ipcMain.handle('compose', async (event, { manifest, output, verifyHash }) => {
  const args = ['compose', manifest, output];
  if (verifyHash === false) args.push('--no-verify-hash');
  const res = await runPptlib(args, event.sender);
  return res.parsed;
});

ipcMain.handle('reveal', (event, targetPath) => {
  if (targetPath && fs.existsSync(targetPath)) shell.showItemInFolder(targetPath);
});
