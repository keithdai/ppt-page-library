'use strict';

// Electron control console for the local PPT page library.
//
// This is intentionally a *console*, not a browse/select UI: selection happens
// in the Miaoda web app. The desktop app drives the local `pptlib` CLI to do
// the four local jobs — import + render, export a catalog, sync to Miaoda, and
// compose a downloaded selection manifest back into a PPTX. All heavy work and
// the source PPTX files stay local.

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

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
    let stdout = '';
    let stderr = '';
    const emit = (channel, text) => {
      if (webContents && !webContents.isDestroyed()) {
        webContents.send('log', { channel, text });
      }
      if (onLine) onLine(channel, text);
    };
    child.stdout.on('data', (buf) => {
      stdout += buf.toString();
      buf.toString().split(/\r?\n/).forEach((line) => line && emit('stdout', line));
    });
    child.stderr.on('data', (buf) => {
      stderr += buf.toString();
      buf.toString().split(/\r?\n/).forEach((line) => line && emit('stderr', line));
    });
    child.on('error', (error) => reject(error));
    child.on('close', (code) => {
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
    width: 1080,
    height: 760,
    title: 'PPT 页库控制台',
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

ipcMain.handle('pick-manifest', async () => {
  const result = await dialog.showOpenDialog({
    title: '选择从妙搭下载的选片 manifest.json',
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

// Import copies each chosen PPTX into a per-file staging dir, then imports that
// directory so the existing scanner + renderer run unchanged.
ipcMain.handle('import', async (event, filePaths) => {
  if (!Array.isArray(filePaths) || filePaths.length === 0) {
    throw new Error('未选择任何文件');
  }
  const stagingRoot = path.join(childEnv().PPTLIB_HOME, 'uploaded_sources');
  const results = [];
  for (const source of filePaths) {
    const staging = path.join(stagingRoot, `desktop-${Date.now()}-${path.basename(source)}`);
    fs.mkdirSync(staging, { recursive: true });
    fs.copyFileSync(source, path.join(staging, path.basename(source)));
    const res = await runPptlib(['import', staging], event.sender);
    results.push(res.parsed || {});
  }
  return results;
});

ipcMain.handle('catalog', async (event, outputDir) => {
  const target = outputDir || path.join(childEnv().PPTLIB_HOME, 'catalog');
  const res = await runPptlib(['catalog', target], event.sender);
  return res.parsed;
});

ipcMain.handle('sync', async (event, { appId, environment, dryRun }) => {
  const args = ['sync', '--app-id', appId, '--environment', environment || 'online'];
  if (dryRun) args.push('--dry-run');
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
