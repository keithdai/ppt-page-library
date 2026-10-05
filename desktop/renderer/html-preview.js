'use strict';

// Main-process only. Kept here because renderer/**/* is already packaged.
const { randomUUID } = require('node:crypto');
const { StringDecoder } = require('node:string_decoder');

const MAX_OUTPUT_BYTES = 64 * 1024;

function isSlideId(value) {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value);
}

function isMainSender(event, parent) {
  return Boolean(parent && !parent.isDestroyed() &&
    event.sender === parent.webContents &&
    event.senderFrame === parent.webContents.mainFrame);
}

function previewBoundary(value) {
  if (typeof value !== 'string') throw new Error('Invalid preview URL');
  const url = new URL(value);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' ||
      !url.port || Number(url.port) < 1 || url.username || url.password ||
      url.href !== `${url.origin}${url.pathname}` || url.href !== value ||
      !/^\/[A-Za-z0-9_-]{16,128}\/$/.test(url.pathname)) {
    throw new Error('Invalid preview URL');
  }
  return { url: url.href, origin: url.origin, prefix: url.pathname };
}

function allowedPreviewRequest(boundary, details) {
  if (details.resourceType === 'subFrame' || details.resourceType === 'object') return false;
  if (details.method && !['GET', 'HEAD'].includes(details.method)) return false;
  let url;
  try {
    url = new URL(details.url);
  } catch {
    return false;
  }
  const media = details.resourceType === 'image' || details.resourceType === 'media';
  if (url.protocol === 'data:') {
    return media && /^data:(image|audio|video)\//i.test(details.url);
  }
  if (url.protocol === 'blob:') return media && url.origin === boundary.origin;
  if (url.origin !== boundary.origin || url.username || url.password ||
      !url.pathname.startsWith(boundary.prefix)) return false;
  // Resource keys encode directory separators. Decode once, then reject
  // traversal and double encoding without rejecting ordinary nested resources.
  try {
    const segments = decodeURIComponent(url.pathname).split('/');
    if (segments.some((part) => part === '.' || part === '..' || /[\\%\u0000]/.test(part))) {
      return false;
    }
  } catch {
    return false;
  }
  if (details.resourceType === 'mainFrame') return url.href === boundary.url;
  return ['image', 'media', 'stylesheet', 'script', 'font', 'xhr', 'other']
    .includes(details.resourceType);
}

function validateReadiness(value, slide) {
  if (!value || value.ok !== true || value.slide_id !== slide.slide_id ||
      value.page_key !== slide.page_key || value.slide_number !== slide.slide_number ||
      value.preview_profile !== 'sanitized-v1' || !Array.isArray(value.warnings)) {
    throw new Error('Invalid HTML preview readiness');
  }
  return previewBoundary(value.url);
}

function createHtmlPreview({
  slide, parent, spawnServer, BrowserWindow, session,
  readinessMs = 30000, killMs = 2000, onDispose = () => {},
}) {
  let child;
  let win;
  let previewSession;
  let disposed = false;
  let exited = false;
  let settled = false;
  let receivedReady = false;
  let boundary;
  let stdout = '';
  let stdoutBytes = 0;
  let stderr = '';
  let stderrBytes = 0;
  let killTimer;
  let cleanup = Promise.resolve();
  let resolveReady;
  let rejectReady;
  let resolveStopped;
  const stopped = new Promise((resolve) => { resolveStopped = resolve; });
  const ready = new Promise((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });
  const decoder = new StringDecoder('utf8');
  const errorDecoder = new StringDecoder('utf8');
  const timer = setTimeout(() => dispose(new Error('HTML preview timed out')), readinessMs);
  timer.unref();

  function dispose(error = new Error('HTML preview closed')) {
    if (disposed) return cleanup;
    disposed = true;
    clearTimeout(timer);
    parent.removeListener('closed', onParentClose);
    parent.removeListener('close', onParentClose);
    if (!settled) {
      settled = true;
      rejectReady(error);
    }
    if (child && !exited) {
      try { child.kill('SIGTERM'); } catch { /* process already gone */ }
      if (!exited) {
        killTimer = setTimeout(() => {
          if (!exited) {
            try { child.kill('SIGKILL'); } catch { /* process already gone */ }
          }
          resolveStopped();
        }, killMs);
        killTimer.unref();
      }
    } else {
      resolveStopped();
    }
    if (win && !win.isDestroyed()) win.destroy();
    const tasks = [stopped];
    if (previewSession) {
      // Electron has no Session.destroy(). Keep its request guard closed while
      // clearing the ephemeral partition and all connections.
      previewSession.webRequest.onBeforeRequest((_details, callback) => callback({ cancel: true }));
      tasks.push(
        previewSession.closeAllConnections(),
        previewSession.clearStorageData(),
        previewSession.clearCache(),
      );
    }
    cleanup = Promise.allSettled(tasks).then(() => {
      win = null;
      previewSession = null;
      stdout = '';
      stderr = '';
      onDispose();
    });
    return cleanup;
  }

  function onParentClose() {
    dispose();
  }

  function createPreviewWindow() {
    previewSession = session.fromPartition(`html-preview-${randomUUID()}`, { cache: false });
    previewSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    previewSession.setPermissionCheckHandler(() => false);
    previewSession.setDevicePermissionHandler(() => false);
    previewSession.on('will-download', (event) => event.preventDefault());
    let requestedDocument = false;
    previewSession.webRequest.onBeforeRequest((details, callback) => {
      let allowed = !disposed && allowedPreviewRequest(boundary, details);
      if (details.resourceType === 'mainFrame') {
        allowed = allowed && !requestedDocument;
        if (allowed) requestedDocument = true;
      }
      callback({ cancel: !allowed });
    });
    previewSession.webRequest.onHeadersReceived((details, callback) => {
      const redirect = details.statusCode >= 300 && details.statusCode < 400 &&
        details.statusCode !== 304;
      callback({ cancel: disposed || redirect });
    });
    win = new BrowserWindow({
      parent,
      width: 1120,
      height: 760,
      show: false,
      title: '拼页 · HTML 动态预览（安全模式）',
      autoHideMenuBar: true,
      webPreferences: {
        session: previewSession,
        sandbox: true,
        nodeIntegration: false,
        nodeIntegrationInWorker: false,
        nodeIntegrationInSubFrames: false,
        contextIsolation: true,
        webviewTag: false,
        webSecurity: true,
        allowRunningInsecureContent: false,
        devTools: false,
        navigateOnDragDrop: false,
      },
    });
    const contents = win.webContents;
    contents.setWindowOpenHandler(() => ({ action: 'deny' }));
    for (const name of ['will-navigate', 'will-frame-navigate', 'will-redirect', 'will-attach-webview']) {
      contents.on(name, (event) => event.preventDefault());
    }
    contents.on('will-redirect', () => dispose(new Error('HTML preview redirect blocked')));
    contents.on('did-fail-load', () => dispose(new Error('HTML preview failed to load')));
    contents.on('render-process-gone', () => dispose(new Error('HTML preview renderer stopped')));
    contents.on('destroyed', () => dispose());
    win.on('closed', () => dispose());
    // Do not propagate loadURL's error: it can contain the secret token URL.
    Promise.resolve(win.loadURL(boundary.url)).then(() => {
      if (disposed) return;
      clearTimeout(timer);
      win.show();
      settled = true;
      resolveReady({ ok: true });
    }).catch(() => dispose(new Error('HTML preview failed to load')));
  }

  parent.on('closed', onParentClose);
  parent.on('close', onParentClose);
  try {
    if (parent.isDestroyed() || !isSlideId(slide.slide_id) ||
        slide.source_format !== 'render_deck_html' || slide.capabilities?.dynamic_preview !== true) {
      throw new Error('HTML preview is not available for this slide');
    }
    child = spawnServer(slide.slide_id);
    const onServerStopped = (code) => {
      exited = true;
      clearTimeout(killTimer);
      resolveStopped();
      // Errors from the CLI may contain the capability URL. Never forward it.
      const diagnostic = stderr.trim().replace(/https?:\/\/[^\s"'<>]+/gi, '[preview URL hidden]');
      dispose(new Error(`HTML preview server closed (${code ?? 'unknown'})${diagnostic ? `: ${diagnostic}` : ''}`));
    };
    child.on('error', () => {
      if (!child.pid) {
        exited = true;
        resolveStopped();
      }
      dispose(new Error('Could not start HTML preview server'));
    });
    child.on('exit', onServerStopped);
    child.on('close', onServerStopped);
    child.stdout.on('error', () => dispose(new Error('HTML preview output stream failed')));
    child.stderr.on('error', () => dispose(new Error('HTML preview error stream failed')));
    child.stderr.on('data', (buffer) => {
      if (disposed) return;
      stderrBytes += buffer.length;
      if (stderrBytes > MAX_OUTPUT_BYTES) {
        dispose(new Error('HTML preview server produced too much error output'));
        return;
      }
      stderr += errorDecoder.write(buffer);
    });
    child.stdout.on('data', (buffer) => {
      if (disposed) return;
      stdoutBytes += buffer.length;
      if (stdoutBytes > MAX_OUTPUT_BYTES) {
        dispose(new Error('HTML preview server produced too much output'));
        return;
      }
      stdout += decoder.write(buffer);
      if (receivedReady) {
        if (stdout.trim()) dispose(new Error('Unexpected HTML preview server output'));
        stdout = '';
        return;
      }
      const newline = stdout.indexOf('\n');
      if (newline < 0) return;
      try {
        const response = JSON.parse(stdout.slice(0, newline));
        if (response?.ok === false) {
          const messages = {
            SOURCE_CHANGED: 'HTML 来源或依赖已变化，请重新导入',
            NOT_FOUND: 'HTML 页面不存在或版本已过期，请刷新页库',
            REQUEST_INVALID: 'HTML 预览未启用或请求无效',
          };
          dispose(new Error(Object.hasOwn(messages, response.error)
            ? messages[response.error] : 'HTML preview server rejected this source'));
          return;
        }
        boundary = validateReadiness(response, slide);
        stdout = stdout.slice(newline + 1);
        if (stdout.trim()) throw new Error('Unexpected output');
        stdout = '';
        receivedReady = true;
        createPreviewWindow();
      } catch {
        dispose(new Error('Invalid HTML preview response or window setup failed'));
      }
    });
  } catch {
    dispose(new Error('Could not start HTML preview'));
  }
  return { ready, dispose, get closed() { return disposed; } };
}

module.exports = {
  isSlideId, isMainSender, previewBoundary, allowedPreviewRequest,
  validateReadiness, createHtmlPreview,
};
