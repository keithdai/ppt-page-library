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

// Repo root is the parent of desktop/. Everything local is resolved from here.
const REPO_ROOT = path.resolve(__dirname, '..');
const DEFAULT_HOME = path.join(REPO_ROOT, 'var', 'dev');

function pptlibBinary() {
  const venv = path.join(REPO_ROOT, '.venv', 'bin', 'pptlib');
  return fs.existsSync(venv) ? venv : 'pptlib';
}

function childEnv() {
  return {
    ...process.env,
    PPTLIB_HOME: process.env.PPTLIB_HOME || DEFAULT_HOME,
  };
}

/**
 * Run a pptlib subcommand, streaming stdout/stderr lines to the renderer and
 * resolving with the last JSON object printed on stdout (pptlib prints one).
 */
function runPptlib(args, webContents, { onLine } = {}) {
  return new Promise((resolve, reject) => {
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
  home: childEnv().PPTLIB_HOME,
  pptlib: pptlibBinary(),
}));

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
