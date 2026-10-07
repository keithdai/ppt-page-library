'use strict';

// Electron control console for the local PPT page library.
//
// A fully local, three-step flow: import + render, browse/select from the local
// page library (real thumbnails), and compose the selection back into a PPTX.
// The desktop app drives the local `pptlib` CLI; all heavy work and the source
// PPTX files stay on disk — nothing is uploaded.

const {
  app,
  BrowserWindow,
  ipcMain,
  dialog,
  shell,
  protocol,
  nativeImage,
  session,
  powerMonitor,
} = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const { createHtmlPreview, isSlideId } = require('./renderer/html-preview');
const { createScheduler, planIsDue, planWasMissed } = require('./scheduler');
const {
  assetUrl,
  createAssetProtocolHandler,
  requireMainSender: assertMainSender,
} = require('./security');
const { createTaskCoordinator } = require('./task-coordinator');

app.setName('拼页');
if (process.env.PPTLIB_ELECTRON_USER_DATA) {
  app.setPath('userData', path.resolve(process.env.PPTLIB_ELECTRON_USER_DATA));
}

const APP_ICON_PATH = path.join(__dirname, 'assets', 'icon.png');
const hasElectronInstanceLock = app.requestSingleInstanceLock();
let mainWindow = null;
let approvedSlides = new Map();
const htmlPreviews = new Set();

function closeHtmlPreviews() {
  return Promise.allSettled([...htmlPreviews].map((preview) => preview.dispose()));
}

// Custom scheme serves only thumbnail/preview images below PPTLIB_HOME/assets.
protocol.registerSchemesAsPrivileged([
  { scheme: 'pptlib-asset', privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

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

function bundledRuntimeBinary() {
  return path.join(process.resourcesPath || '', 'runtime', 'pptlib', 'pptlib');
}

function hasBundledRuntime() {
  return app.isPackaged === true && fs.existsSync(bundledRuntimeBinary());
}

function runtimeAvailable() {
  if (app.isPackaged === true) return hasBundledRuntime();
  return isRepoRoot(REPO_ROOT);
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
let repositoryInstanceLockPath = null;

function isLockOwnerRunning(lockPath) {
  try {
    const pid = Number.parseInt(fs.readFileSync(lockPath, 'utf8').trim(), 10);
    if (!Number.isInteger(pid) || pid <= 0) return false;
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error && error.code === 'EPERM';
  }
}

function acquireRepositoryInstanceLock() {
  const lockPath = path.join(process.env.PPTLIB_HOME || defaultHome(), '.desktop-instance.lock');
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const descriptor = fs.openSync(lockPath, 'wx');
      try {
        fs.writeFileSync(descriptor, String(process.pid), 'utf8');
      } finally {
        fs.closeSync(descriptor);
      }
      repositoryInstanceLockPath = lockPath;
      return true;
    } catch (error) {
      if (!error || error.code !== 'EEXIST') throw error;
      if (isLockOwnerRunning(lockPath)) return false;
      fs.rmSync(lockPath, { force: true });
    }
  }
  return false;
}

function releaseRepositoryInstanceLock() {
  if (!repositoryInstanceLockPath) return;
  try {
    const owner = fs.readFileSync(repositoryInstanceLockPath, 'utf8').trim();
    if (owner === String(process.pid)) {
      fs.rmSync(repositoryInstanceLockPath, { force: true });
    }
  } catch {
    /* lock already removed */
  }
  repositoryInstanceLockPath = null;
}

const hasRepositoryInstanceLock =
  hasElectronInstanceLock && acquireRepositoryInstanceLock();
const hasSingleInstanceLock =
  hasElectronInstanceLock && hasRepositoryInstanceLock;

function persistRepoRoot(dir) {
  closeHtmlPreviews();
  approvedSlides.clear();
  REPO_ROOT = dir;
  try {
    fs.writeFileSync(repoRootConfigPath(), dir, 'utf8');
  } catch {
    /* best effort */
  }
}

function defaultHome() {
  if (app.isPackaged === true) {
    const packagedHome = path.join(app.getPath('userData'), 'data');
    if (fs.existsSync(path.join(packagedHome, 'pages.db'))) return packagedHome;
    const legacyHome = path.join(REPO_ROOT, 'var', 'dev');
    if (isRepoRoot(REPO_ROOT) && fs.existsSync(path.join(legacyHome, 'pages.db'))) {
      return legacyHome;
    }
    return packagedHome;
  }
  return path.join(REPO_ROOT, 'var', 'dev');
}

function pptlibBinary() {
  if (hasBundledRuntime()) return bundledRuntimeBinary();
  if (app.isPackaged === true) return bundledRuntimeBinary();
  const venv = path.join(REPO_ROOT, '.venv', 'bin', 'pptlib');
  return fs.existsSync(venv) ? venv : 'pptlib';
}

function runtimeWorkingDirectory() {
  return hasBundledRuntime() ? path.dirname(bundledRuntimeBinary()) : REPO_ROOT;
}

function childEnv() {
  const env = {
    ...process.env,
    PPTLIB_HOME: process.env.PPTLIB_HOME || defaultHome(),
    PPTLIB_ENABLE_HTML:
      process.env.PPTLIB_ENABLE_HTML ?? (app.isPackaged === true ? '0' : '1'),
  };
  if (app.isPackaged === true) {
    env.PPTLIB_LOG_DIR = process.env.PPTLIB_LOG_DIR || app.getPath('logs');
    env.PPTLIB_TEMP_DIR =
      process.env.PPTLIB_TEMP_DIR || path.join(app.getPath('temp'), 'com.pptlib.desktop');
    env.PPTLIB_RENDERER = process.env.PPTLIB_RENDERER || 'libreoffice';
  }
  return env;
}

function htmlEnabled() {
  return childEnv().PPTLIB_ENABLE_HTML === '1';
}

/**
 * Run a pptlib subcommand, streaming stdout/stderr lines to the renderer and
 * resolving with the last JSON object printed on stdout (pptlib prints one).
 */
function runPptlib(
  args,
  webContents,
  { input = null, onLine, onSpawn, taskId = null, taskKind = null, onProgress } = {},
) {
  return new Promise((resolve, reject) => {
    if (!runtimeAvailable()) {
      reject(
        new Error(
          '未找到拼页运行时。请重新安装应用，或在开发模式下选择 pptlib 仓库。',
        ),
      );
      return;
    }
    const child = spawn(pptlibBinary(), args, {
      cwd: runtimeWorkingDirectory(),
      env: childEnv(),
      shell: false,
      stdio: [input === null ? 'ignore' : 'pipe', 'pipe', 'pipe'],
    });
    if (onSpawn) onSpawn(child);
    if (input !== null) child.stdin.end(input);
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
      const payload = taskId ? { ...event, taskId, taskKind } : event;
      if (webContents && !webContents.isDestroyed()) {
        webContents.send('progress', payload);
      }
      if (onProgress) onProgress(payload);
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

function requireMainSender(event) {
  assertMainSender(event, mainWindow);
}

function taskError(kind, error) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('task-error', {
      kind,
      message: error.message,
    });
  }
}

const taskCoordinator = createTaskCoordinator({
  runCommand: runPptlib,
  onState(task) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('task-state', task);
    }
  },
  async persistTask(task) {
    await runPptlib(
      ['desktop', 'task-set', '--payload', JSON.stringify(task)],
      null,
    );
  },
});

const scheduler = createScheduler({
  runCommand: runPptlib,
  taskCoordinator,
  runtimeAvailable,
  getWebContents: () => mainWindow?.webContents || null,
  onError: taskError,
});

function assetizeSlide(slide) {
  const assetsDir = path.join(childEnv().PPTLIB_HOME, 'assets');
  return {
    ...slide,
    thumbnail_url: assetUrl(assetsDir, slide.thumbnail_url),
    preview_url: assetUrl(assetsDir, slide.preview_url),
  };
}

function rememberSlides(slides) {
  for (const slide of slides) approvedSlides.set(slide.slide_id, slide);
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1120,
    minHeight: 680,
    title: '拼页 PinPage',
    icon: APP_ICON_PATH,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow = win;
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null;
  });
  return win;
}

if (!hasSingleInstanceLock) {
  if (hasElectronInstanceLock) app.releaseSingleInstanceLock();
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  app.whenReady().then(() => {
    const appIcon = nativeImage.createFromPath(APP_ICON_PATH);
    if (!appIcon.isEmpty() && app.dock) app.dock.setIcon(appIcon);
    app.setAppUserModelId('com.pptlib.desktop');

    protocol.handle(
      'pptlib-asset',
      createAssetProtocolHandler(() => path.join(childEnv().PPTLIB_HOME, 'assets')),
    );
    createWindow();
    Promise.allSettled([
      runPptlib(['scan-plan', 'recover'], null),
      runPptlib(['desktop', 'task-recover'], null),
    ]).finally(() => scheduler.start());
    powerMonitor.on('resume', scheduler.check);
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
    // Smoke check hook: `PPTLIB_SMOKE=1 electron .` boots, then exits cleanly so
    // CI/local verification can confirm the app wires up without a display.
    if (process.env.PPTLIB_SMOKE === '1') {
      setTimeout(() => app.quit(), 1500);
    }
  });
}

app.on('will-quit', () => {
  scheduler.stop();
  releaseRepositoryInstanceLock();
});
let quitCleanupPending = false;
let quitCleanupDone = false;
app.on('before-quit', (event) => {
  if (quitCleanupDone) return;
  if (quitCleanupPending) {
    event.preventDefault();
    return;
  }
  if (htmlPreviews.size === 0 && !taskCoordinator.hasActive()) return;
  event.preventDefault();
  quitCleanupPending = true;
  Promise.allSettled([
    closeHtmlPreviews(),
    taskCoordinator.stop({ wait: true }),
  ]).finally(() => {
    quitCleanupPending = false;
    quitCleanupDone = true;
    app.quit();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// --------------------------------------------------------------------------- //
// IPC handlers
// --------------------------------------------------------------------------- //

ipcMain.handle('paths', (event) => {
  requireMainSender(event);
  return {
    repoRoot: REPO_ROOT,
    repoRootValid: runtimeAvailable(),
    runtimeMode: hasBundledRuntime() ? 'bundled' : 'development',
    home: childEnv().PPTLIB_HOME,
    pptlib: pptlibBinary(),
    htmlEnabled: htmlEnabled(),
  };
});

ipcMain.handle('html-preview', async (event, slideId) => {
  requireMainSender(event);
  if (!htmlEnabled()) throw new Error('HTML 功能已关闭');
  if (!isSlideId(slideId)) throw new Error('无效的页面 ID');
  const slide = approvedSlides.get(slideId);
  if (!slide || slide.source_format !== 'render_deck_html' ||
      slide.capabilities.dynamic_preview !== true) {
    throw new Error('该页面不支持动态预览，请刷新页库');
  }
  if (!runtimeAvailable()) throw new Error('未找到拼页运行时');
  let preview;
  preview = createHtmlPreview({
    slide,
    parent: mainWindow,
    BrowserWindow,
    session,
    onDispose: () => htmlPreviews.delete(preview),
    // Unlike runPptlib, this process stays alive and its URL must never be logged.
    spawnServer: (id) => spawn(pptlibBinary(), ['html-preview', id], {
      cwd: runtimeWorkingDirectory(),
      env: childEnv(),
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
    }),
  });
  if (!preview.closed) htmlPreviews.add(preview);
  return preview.ready;
});

// Persist an ordered slide_id selection as a manifest.json under PPTLIB_HOME so
// the existing compose flow consumes it unchanged.
ipcMain.handle('write-manifest', async (event, slideIds) => {
  requireMainSender(event);
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
ipcMain.handle('pick-repo-root', async (event) => {
  requireMainSender(event);
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

ipcMain.handle('pick-pptx', async (event) => {
  requireMainSender(event);
  const result = await dialog.showOpenDialog({
    title: htmlEnabled()
      ? '选择 PPTX、HTML 或 HTML Deck ZIP（源文件仅留在本地）'
      : '选择要导入的 PPTX（源文件仅留在本地）',
    properties: ['openFile', 'multiSelections'],
    filters: htmlEnabled()
      ? [{ name: 'PPTX / HTML Deck', extensions: ['pptx', 'html', 'htm', 'zip'] }]
      : [{ name: 'PowerPoint', extensions: ['pptx'] }],
  });
  return result.canceled ? [] : result.filePaths;
});

// Pick one or more folders; the backend scans them recursively for PPTX files.
ipcMain.handle('pick-folder', async (event) => {
  requireMainSender(event);
  const result = await dialog.showOpenDialog({
    title: htmlEnabled()
      ? '选择文件夹（递归查找 PPTX / HTML / ZIP，源文件仅留在本地）'
      : '选择要扫描导入的文件夹（递归查找 PPTX，源文件仅留在本地）',
    properties: ['openDirectory', 'multiSelections'],
  });
  return result.canceled ? [] : result.filePaths;
});

ipcMain.handle('pick-manifest', async (event) => {
  requireMainSender(event);
  const result = await dialog.showOpenDialog({
    title: '选择选片清单 manifest.json',
    properties: ['openFile'],
    filters: [{ name: 'Manifest', extensions: ['json'] }],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('pick-output-pptx', async (event) => {
  requireMainSender(event);
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
  requireMainSender(event);
  if (!Array.isArray(filePaths) || filePaths.length === 0) {
    throw new Error('未选择任何文件');
  }
  closeHtmlPreviews();
  approvedSlides.clear();
  const res = await taskCoordinator.run(
    '导入并渲染',
    ['import', ...filePaths],
    event.sender,
  );
  return [res.parsed || {}];
});

ipcMain.handle('library:bootstrap', async (event, sinceRevision) => {
  requireMainSender(event);
  const args = ['desktop', 'bootstrap'];
  if (sinceRevision) args.push('--since-revision', String(sinceRevision));
  const result = await runPptlib(args, event.sender);
  const payload = result.parsed || {};
  payload.htmlEnabled = htmlEnabled();
  if (!payload.unchanged) approvedSlides.clear();
  payload.decks = (payload.decks || []).map((deck) => ({
    ...deck,
    cover_thumbnail_url: assetUrl(
      path.join(childEnv().PPTLIB_HOME, 'assets'),
      deck.cover_thumbnail_url,
    ),
  }));
  const selectionItems = (payload.selection?.items || []).map(assetizeSlide);
  if (payload.selection) payload.selection.items = selectionItems;
  rememberSlides(selectionItems);
  return payload;
});

ipcMain.handle('library:search', async (event, options = {}) => {
  requireMainSender(event);
  const page = Math.max(1, Number.parseInt(options.page, 10) || 1);
  const pageSize = Math.min(200, Math.max(1, Number.parseInt(options.pageSize, 10) || 100));
  const result = await runPptlib(
    [
      'desktop',
      'search',
      '--query',
      String(options.query || '').slice(0, 500),
      '--page',
      String(page),
      '--page-size',
      String(pageSize),
      '--filters',
      JSON.stringify(options.filters || {}),
    ],
    event.sender,
  );
  const payload = result.parsed || {};
  payload.items = (payload.items || []).map(assetizeSlide);
  rememberSlides(payload.items);
  return payload;
});

ipcMain.handle('selection:save', async (event, payload = {}) => {
  requireMainSender(event);
  const result = await runPptlib(
    ['desktop', 'selection-save', '--stdin'],
    event.sender,
    { input: JSON.stringify(payload) },
  );
  const response = result.parsed || {};
  response.items = (response.items || []).map(assetizeSlide);
  rememberSlides(response.items);
  return response;
});

ipcMain.handle('find-duplicates', async (event, refresh) => {
  requireMainSender(event);
  const args = ['duplicates'];
  if (refresh === true) args.push('--refresh');
  const res = await taskCoordinator.run('重复扫描', args, event.sender);
  const report = res.parsed || {};
  const assetsDir = path.join(childEnv().PPTLIB_HOME, 'assets');
  report.groups = (report.groups || []).map((group) => ({
    ...group,
    members: (group.members || []).map((member) => {
      const thumbnail = `/assets/thumbnails/${member.slide_id}.jpg`;
      const preview = `/assets/previews/${member.slide_id}.jpg`;
      const thumbnailUrl = assetUrl(assetsDir, thumbnail);
      const previewUrl = assetUrl(assetsDir, preview) || thumbnailUrl;
      return {
        ...member,
        thumbnail_url: thumbnailUrl,
        preview_url: previewUrl,
      };
    }),
  }));
  return report;
});

// Remove decks or individual slides from the local index. This only clears the
// local index + cached thumbnails; the original PPTX files are never touched.
ipcMain.handle('remove-deck', async (event, deckIds) => {
  requireMainSender(event);
  const ids = (Array.isArray(deckIds) ? deckIds : [deckIds]).filter(Boolean);
  if (ids.length === 0) throw new Error('未提供要删除的文件');
  closeHtmlPreviews();
  for (const [id, slide] of approvedSlides) {
    if (ids.includes(slide.deck_id)) approvedSlides.delete(id);
  }
  const args = ['remove'];
  ids.forEach((id) => args.push('--deck', String(id)));
  const res = await taskCoordinator.run('页库清理', args, event.sender);
  return res.parsed;
});

ipcMain.handle('remove-slide', async (event, slideIds) => {
  requireMainSender(event);
  const ids = (Array.isArray(slideIds) ? slideIds : [slideIds]).filter(Boolean);
  if (ids.length === 0) throw new Error('未提供要删除的页面');
  closeHtmlPreviews();
  for (const id of ids) approvedSlides.delete(id);
  const args = ['remove'];
  ids.forEach((id) => args.push('--slide', String(id)));
  const res = await taskCoordinator.run('页库清理', args, event.sender);
  return res.parsed;
});

ipcMain.handle('doctor', async (event) => {
  requireMainSender(event);
  const res = await runPptlib(['doctor'], event.sender);
  return res.parsed;
});

ipcMain.handle('compose-preflight', async (event, { manifest, output, verifyHash }) => {
  requireMainSender(event);
  const args = ['compose-preflight', manifest, output];
  if (verifyHash === false) args.push('--no-verify-hash');
  const res = await runPptlib(args, event.sender);
  return res.parsed;
});

ipcMain.handle('compose', async (event, {
  manifest, output, verifyHash, preflightToken,
}) => {
  requireMainSender(event);
  const args = ['compose', manifest, output];
  if (verifyHash === false) args.push('--no-verify-hash');
  if (preflightToken) args.push('--preflight-token', String(preflightToken));
  const res = await taskCoordinator.run('组合导出', args, event.sender);
  return res.parsed;
});

ipcMain.handle('scan-plan:list', async (event) => {
  requireMainSender(event);
  const res = await runPptlib(['scan-plan', 'list'], event.sender);
  return res.parsed;
});

ipcMain.handle('scan-plan:create', async (event, payload) => {
  requireMainSender(event);
  const res = await runPptlib(
    ['scan-plan', 'save', '--payload', JSON.stringify(payload || {})],
    event.sender,
  );
  return res.parsed;
});

ipcMain.handle('scan-plan:update', async (event, payload) => {
  requireMainSender(event);
  const res = await runPptlib(
    ['scan-plan', 'save', '--payload', JSON.stringify(payload || {})],
    event.sender,
  );
  return res.parsed;
});

ipcMain.handle('scan-plan:delete', async (event, planId) => {
  requireMainSender(event);
  const res = await runPptlib(['scan-plan', 'delete', String(planId)], event.sender);
  return res.parsed;
});

ipcMain.handle('scan-plan:set-enabled', async (event, { planId, enabled } = {}) => {
  requireMainSender(event);
  const res = await runPptlib(
    ['scan-plan', 'set-enabled', String(planId), enabled ? '1' : '0'],
    event.sender,
  );
  return res.parsed;
});

ipcMain.handle('scan-plan:preview', async (event, payload) => {
  requireMainSender(event);
  const res = await taskCoordinator.run(
    '自动更新预检',
    ['scan-plan', 'preview', '--payload', JSON.stringify(payload || {})],
    event.sender,
  );
  return res.parsed;
});

ipcMain.handle('scan-plan:run', async (event, { planId, rootId } = {}) => {
  requireMainSender(event);
  const args = ['scan-plan', 'run', String(planId), '--trigger', 'manual'];
  if (rootId) args.push('--root-id', String(rootId));
  const res = await taskCoordinator.run('自动更新', args, event.sender);
  return res.parsed;
});

ipcMain.handle('scan-plan:run-all', async (event) => {
  requireMainSender(event);
  return scheduler.runAll(event.sender);
});

ipcMain.handle('scan-run:current', async (event) => {
  requireMainSender(event);
  return { ok: true, task: taskCoordinator.snapshot() };
});

ipcMain.handle('scan-run:stop', async (event) => {
  requireMainSender(event);
  return taskCoordinator.stop();
});

ipcMain.handle('scan-run:history', async (event, limit = 50) => {
  requireMainSender(event);
  const safeLimit = Math.min(200, Math.max(1, Number(limit) || 50));
  const res = await runPptlib(
    ['scan-plan', 'history', '--limit', String(safeLimit)],
    event.sender,
  );
  return res.parsed;
});

ipcMain.handle('scan-run:retry', async (event, runId) => {
  requireMainSender(event);
  const res = await taskCoordinator.run(
    '重试失败项',
    ['scan-plan', 'retry', String(runId)],
    event.sender,
  );
  return res.parsed;
});

ipcMain.handle('reveal', (event, targetPath) => {
  requireMainSender(event);
  if (!targetPath || !fs.existsSync(targetPath)) throw new Error('文件不存在');
  shell.showItemInFolder(targetPath);
  return { ok: true };
});

ipcMain.handle('open-path', async (event, targetPath) => {
  requireMainSender(event);
  if (!targetPath || !fs.existsSync(targetPath)) throw new Error('文件不存在');
  const error = await shell.openPath(targetPath);
  if (error) throw new Error(error);
  return { ok: true };
});
