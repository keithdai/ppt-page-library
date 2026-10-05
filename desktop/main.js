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
const { randomUUID } = require('node:crypto');
const path = require('node:path');
const fs = require('node:fs');
const { createHtmlPreview, isMainSender, isSlideId } = require('./renderer/html-preview');

app.setName('拼页');
if (process.env.PPTLIB_ELECTRON_USER_DATA) {
  app.setPath('userData', path.resolve(process.env.PPTLIB_ELECTRON_USER_DATA));
}

const APP_ICON_PATH = path.join(__dirname, 'assets', 'icon.png');
const hasElectronInstanceLock = app.requestSingleInstanceLock();
let mainWindow = null;
let approvedSlides = new Map();
const htmlPreviews = new Set();
let activeHeavyTask = null;
let schedulerTimer = null;

function closeHtmlPreviews() {
  return Promise.allSettled([...htmlPreviews].map((preview) => preview.dispose()));
}

// Custom scheme to serve local thumbnail/preview images to the renderer. A
// file://-loaded page can't reliably read images from other directories, so
// catalog thumbnails are served through `pptlib-asset://local/<encoded-abs>`.
protocol.registerSchemesAsPrivileged([
  { scheme: 'pptlib-asset', privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

function assetUrl(absPath) {
  const version = fs.existsSync(absPath) ? fs.statSync(absPath).mtimeMs : 0;
  return `pptlib-asset://local/${encodeURIComponent(absPath)}?v=${version}`;
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
  { onLine, onSpawn, taskId = null, taskKind = null, onProgress } = {},
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
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    if (onSpawn) onSpawn(child);
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

function taskSnapshot(task = activeHeavyTask) {
  if (!task) return null;
  return {
    taskId: task.taskId,
    kind: task.kind,
    state: task.state,
    startedAt: task.startedAt,
    progress: task.progress || null,
  };
}

function broadcastTaskState(task = activeHeavyTask) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('task-state', taskSnapshot(task));
  }
}

function beginHeavyTask(kind) {
  if (activeHeavyTask) {
    throw new Error(`当前正在执行“${activeHeavyTask.kind}”，请等待完成或先停止`);
  }
  let finish;
  const done = new Promise((resolve) => { finish = resolve; });
  activeHeavyTask = {
    taskId: randomUUID(),
    kind,
    state: 'running',
    startedAt: new Date().toISOString(),
    child: null,
    progress: null,
    done,
    finish,
  };
  broadcastTaskState();
  return activeHeavyTask;
}

function finishHeavyTask(task) {
  if (activeHeavyTask !== task) return;
  task.state = 'finished';
  task.child = null;
  task.finish();
  activeHeavyTask = null;
  broadcastTaskState(null);
}

async function runHeavyTask(kind, args, webContents) {
  const task = beginHeavyTask(kind);
  try {
    return await runPptlibForTask(task, args, webContents);
  } finally {
    finishHeavyTask(task);
  }
}

function runPptlibForTask(task, args, webContents) {
  return runPptlib(args, webContents, {
    taskId: task.taskId,
    taskKind: task.kind,
    onSpawn(child) {
      task.child = child;
      if (task.state === 'stopping' && child.exitCode === null) child.kill('SIGTERM');
    },
    onProgress(progress) {
      task.progress = progress;
      broadcastTaskState(task);
    },
  });
}

async function stopHeavyTask({ wait = false, timeoutMs = 15000 } = {}) {
  const task = activeHeavyTask;
  if (!task) return { ok: true, stopped: false };
  if (task.state !== 'stopping') {
    task.state = 'stopping';
    broadcastTaskState(task);
    if (task.child && !task.child.killed) task.child.kill('SIGTERM');
  }
  if (!wait) return { ok: true, stopped: true, task: taskSnapshot(task) };
  let timeout;
  await Promise.race([
    task.done,
    new Promise((resolve) => {
      timeout = setTimeout(() => {
        if (activeHeavyTask === task && task.child && task.child.exitCode === null) {
          task.child.kill('SIGKILL');
        }
        resolve();
      }, timeoutMs);
    }),
  ]);
  if (timeout) clearTimeout(timeout);
  return { ok: true, stopped: true, task: taskSnapshot(task) };
}

function requireMainSender(event) {
  if (!isMainSender(event, mainWindow)) throw new Error('不允许的请求');
}

function localDayKey(value) {
  const date = new Date(value);
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

function minuteOfDay(value) {
  const [hour, minute] = String(value).split(':').map(Number);
  return hour * 60 + minute;
}

function timeInWindow(nowMinutes, start, end) {
  const startMinutes = minuteOfDay(start);
  const endMinutes = minuteOfDay(end);
  if (startMinutes <= endMinutes) {
    return nowMinutes >= startMinutes && nowMinutes <= endMinutes;
  }
  return nowMinutes >= startMinutes || nowMinutes <= endMinutes;
}

function scheduledOccurrence(plan, now) {
  const scheduleMinutes = minuteOfDay(plan.scheduleTime);
  const startMinutes = minuteOfDay(plan.windowStart);
  const endMinutes = minuteOfDay(plan.windowEnd);
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  const target = new Date(now);
  target.setHours(Math.floor(scheduleMinutes / 60), scheduleMinutes % 60, 0, 0);
  if (startMinutes > endMinutes) {
    if (scheduleMinutes >= startMinutes && nowMinutes < startMinutes) {
      target.setDate(target.getDate() - 1);
    } else if (scheduleMinutes <= endMinutes && nowMinutes >= startMinutes) {
      target.setDate(target.getDate() + 1);
    }
  }
  return target;
}

function planRunRecorded(plan, history, target) {
  const day = localDayKey(target);
  return history.some(
    (run) => run.planId === plan.id &&
      run.triggerType === 'scheduled' &&
      localDayKey(run.scheduledFor || run.startedAt) === day,
  );
}

function planIsDue(plan, history, now = new Date()) {
  if (!plan.enabled || plan.scheduleKind !== 'daily') return false;
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  if (!timeInWindow(nowMinutes, plan.windowStart, plan.windowEnd)) return false;
  const target = scheduledOccurrence(plan, now);
  return now >= target && !planRunRecorded(plan, history, target);
}

function planWasMissed(plan, history, now = new Date()) {
  if (!plan.enabled || plan.scheduleKind !== 'daily') return false;
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  if (timeInWindow(nowMinutes, plan.windowStart, plan.windowEnd)) return false;
  const target = scheduledOccurrence(plan, now);
  if (plan.updatedAt && new Date(plan.updatedAt) > target) return false;
  return now > target && !planRunRecorded(plan, history, target);
}
let schedulerChecking = false;
async function checkScheduledPlans() {
  if (schedulerChecking || activeHeavyTask || !runtimeAvailable()) return;
  schedulerChecking = true;
  try {
    const [planResult, historyResult] = await Promise.all([
      runPptlib(['scan-plan', 'list'], null),
      runPptlib(['scan-plan', 'history', '--limit', '200'], null),
    ]);
    const plans = planResult.parsed?.plans || [];
    const history = historyResult.parsed?.runs || [];
    for (const plan of plans.filter((item) => planWasMissed(item, history))) {
      const scheduledFor = scheduledOccurrence(plan, new Date()).toISOString();
      const missed = await runPptlib(
        ['scan-plan', 'missed', plan.id, '--scheduled-for', scheduledFor],
        null,
      );
      if (missed.parsed) {
        history.push({
          planId: plan.id,
          triggerType: 'scheduled',
          startedAt: scheduledFor,
        });
      }
    }
    const due = plans.find((plan) => planIsDue(plan, history));
    if (!due || activeHeavyTask) return;
    const scheduledFor = scheduledOccurrence(due, new Date()).toISOString();
    runHeavyTask(
      '自动更新',
      [
        'scan-plan', 'run', due.id, '--trigger', 'scheduled',
        '--scheduled-for', scheduledFor,
      ],
      mainWindow?.webContents,
    ).catch((error) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('task-error', {
          kind: '自动更新',
          message: error.message,
        });
      }
    });
  } catch (error) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('task-error', {
        kind: '自动更新调度',
        message: error.message,
      });
    }
  } finally {
    schedulerChecking = false;
  }
}

function startScheduler() {
  if (schedulerTimer) clearInterval(schedulerTimer);
  schedulerTimer = setInterval(checkScheduledPlans, 60 * 1000);
  schedulerTimer.unref?.();
  setTimeout(checkScheduledPlans, 1500);
}

async function runAllEnabledPlans(webContents) {
  const task = beginHeavyTask('立即更新全部');
  const results = [];
  try {
    const listResult = await runPptlibForTask(task, ['scan-plan', 'list'], webContents);
    const plans = (listResult.parsed?.plans || []).filter((plan) => plan.enabled);
    for (const plan of plans) {
      if (task.state === 'stopping') break;
      const result = await runPptlibForTask(
        task,
        ['scan-plan', 'run', plan.id, '--trigger', 'manual'],
        webContents,
      );
      results.push(result.parsed || {});
    }
    return { ok: true, results };
  } finally {
    finishHeavyTask(task);
  }
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

    // Serve local catalog thumbnails to the renderer via pptlib-asset://.
    protocol.handle('pptlib-asset', (request) => {
      try {
        const encoded = new URL(request.url).pathname.slice(1);
        const abs = decodeURIComponent(encoded);
        if (!fs.existsSync(abs)) return new Response('not found', { status: 404 });
        const data = fs.readFileSync(abs);
        const ext = path.extname(abs).toLowerCase();
        const type = ext === '.png' ? 'image/png' : ext === '.webp' ? 'image/webp' : 'image/jpeg';
        return new Response(data, {
          headers: { 'content-type': type, 'cache-control': 'no-store' },
        });
      } catch (error) {
        return new Response(String(error), { status: 500 });
      }
    });
    createWindow();
    runPptlib(['scan-plan', 'recover'], null).catch(() => {
      /* Recovery is best effort; the module will surface database errors. */
    });
    startScheduler();
    powerMonitor.on('resume', checkScheduledPlans);
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
  if (schedulerTimer) clearInterval(schedulerTimer);
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
  if (htmlPreviews.size === 0 && !activeHeavyTask) return;
  event.preventDefault();
  quitCleanupPending = true;
  Promise.allSettled([
    closeHtmlPreviews(),
    stopHeavyTask({ wait: true }),
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

ipcMain.handle('paths', () => ({
  repoRoot: REPO_ROOT,
  repoRootValid: runtimeAvailable(),
  runtimeMode: hasBundledRuntime() ? 'bundled' : 'development',
  home: childEnv().PPTLIB_HOME,
  pptlib: pptlibBinary(),
  htmlEnabled: htmlEnabled(),
}));

ipcMain.handle('html-preview', async (event, slideId) => {
  if (!isMainSender(event, mainWindow)) throw new Error('不允许的预览请求');
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
ipcMain.handle('pick-folder', async () => {
  const result = await dialog.showOpenDialog({
    title: htmlEnabled()
      ? '选择文件夹（递归查找 PPTX / HTML / ZIP，源文件仅留在本地）'
      : '选择要扫描导入的文件夹（递归查找 PPTX，源文件仅留在本地）',
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
  closeHtmlPreviews();
  approvedSlides.clear();
  const res = await runHeavyTask('导入并渲染', ['import', ...filePaths], event.sender);
  return [res.parsed || {}];
});

ipcMain.handle('catalog', async (event, outputDir) => {
  if (activeHeavyTask) throw new Error('页库正在更新，请等待当前任务完成');
  const target = outputDir || path.join(childEnv().PPTLIB_HOME, 'catalog');
  const res = await runPptlib(['catalog', target], event.sender);
  return res.parsed;
});

// Load the local catalog for in-app grid selection. This regenerates
// catalog.json from the local index, then attaches a resolvable asset URL for
// each slide's thumbnail (assets live under PPTLIB_HOME/assets). Selection then
// happens entirely locally.
ipcMain.handle('load-catalog', async (event) => {
  if (!isMainSender(event, mainWindow)) throw new Error('不允许的页库请求');
  if (activeHeavyTask) throw new Error('页库正在更新，请等待当前任务完成');
  const home = childEnv().PPTLIB_HOME;
  const target = path.join(home, 'catalog');
  await runPptlib(['catalog', target, '--include-local-fields'], event.sender);
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
      source_path: s.source_path || '',
      source_format: s.source_format || 'pptx',
      page_key: s.page_key || '',
      page_kind: s.page_kind || '',
      capabilities: s.capabilities && typeof s.capabilities === 'object' ? s.capabilities : {},
      warnings: Array.isArray(s.warnings) ? s.warnings : [],
      slide_number: s.slide_number,
      title: s.title,
      summary: s.summary || '',
      search_text: s.search_text || s.summary || '',
      topic: s.topic,
      subtopic: s.subtopic,
      page_type: s.page_type,
      thumbnail_url: hasThumb ? assetUrl(thumbAbs) : '',
      preview_url: hasPreview ? assetUrl(previewAbs) : hasThumb ? assetUrl(thumbAbs) : '',
    };
  });
  approvedSlides = new Map(slides.map((slide) => [slide.slide_id, slide]));
  return { slide_count: slides.length, slides, htmlEnabled: htmlEnabled() };
});

ipcMain.handle('find-duplicates', async (event, refresh) => {
  const args = ['duplicates'];
  if (refresh === true) args.push('--refresh');
  const res = await runHeavyTask('重复扫描', args, event.sender);
  const report = res.parsed || {};
  const assetsDir = path.join(childEnv().PPTLIB_HOME, 'assets');
  report.groups = (report.groups || []).map((group) => ({
    ...group,
    members: (group.members || []).map((member) => {
      const thumbnail = path.join(assetsDir, 'thumbnails', `${member.slide_id}.jpg`);
      const preview = path.join(assetsDir, 'previews', `${member.slide_id}.jpg`);
      let previewUrl = '';
      if (fs.existsSync(preview)) previewUrl = assetUrl(preview);
      else if (fs.existsSync(thumbnail)) previewUrl = assetUrl(thumbnail);
      return {
        ...member,
        thumbnail_url: fs.existsSync(thumbnail) ? assetUrl(thumbnail) : '',
        preview_url: previewUrl,
      };
    }),
  }));
  return report;
});

// Remove decks or individual slides from the local index. This only clears the
// local index + cached thumbnails; the original PPTX files are never touched.
ipcMain.handle('remove-deck', async (event, deckIds) => {
  const ids = (Array.isArray(deckIds) ? deckIds : [deckIds]).filter(Boolean);
  if (ids.length === 0) throw new Error('未提供要删除的文件');
  closeHtmlPreviews();
  for (const [id, slide] of approvedSlides) {
    if (ids.includes(slide.deck_id)) approvedSlides.delete(id);
  }
  const args = ['remove'];
  ids.forEach((id) => args.push('--deck', String(id)));
  const res = await runHeavyTask('页库清理', args, event.sender);
  return res.parsed;
});

ipcMain.handle('remove-slide', async (event, slideIds) => {
  const ids = (Array.isArray(slideIds) ? slideIds : [slideIds]).filter(Boolean);
  if (ids.length === 0) throw new Error('未提供要删除的页面');
  closeHtmlPreviews();
  for (const id of ids) approvedSlides.delete(id);
  const args = ['remove'];
  ids.forEach((id) => args.push('--slide', String(id)));
  const res = await runHeavyTask('页库清理', args, event.sender);
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
  const res = await runHeavyTask('组合导出', args, event.sender);
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
  const res = await runHeavyTask(
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
  const res = await runHeavyTask('自动更新', args, event.sender);
  return res.parsed;
});

ipcMain.handle('scan-plan:run-all', async (event) => {
  requireMainSender(event);
  return runAllEnabledPlans(event.sender);
});

ipcMain.handle('scan-run:current', async (event) => {
  requireMainSender(event);
  return { ok: true, task: taskSnapshot() };
});

ipcMain.handle('scan-run:stop', async (event) => {
  requireMainSender(event);
  return stopHeavyTask();
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
  const res = await runHeavyTask(
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
