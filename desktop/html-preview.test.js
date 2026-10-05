'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { setTimeout: delay } = require('node:timers/promises');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {
  isSlideId, isMainSender, previewBoundary, allowedPreviewRequest,
  validateReadiness, createHtmlPreview,
} = require('./renderer/html-preview');

const url = 'http://127.0.0.1:54321/abcdefghijklmnop1234567890/';
const slide = {
  slide_id: 's_0123456789', page_key: 'page-1', slide_number: 1,
  source_format: 'render_deck_html', capabilities: { dynamic_preview: true },
};
const readiness = {
  ok: true, url, slide_id: slide.slide_id, page_key: slide.page_key,
  slide_number: 1, preview_profile: 'sanitized-v1', warnings: [],
};

function fixture(t, options = {}) {
  const parent = new EventEmitter();
  parent.isDestroyed = () => false;
  parent.webContents = { mainFrame: {} };
  const child = new EventEmitter();
  child.pid = 123;
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  const signals = [];
  child.kill = (signal) => {
    signals.push(signal);
    if (!options.ignoreTerm || signal === 'SIGKILL') {
      child.emit('exit', 0);
      child.emit('close', 0);
    }
  };
  const partitions = [];
  const sessions = [];
  const windows = [];
  class FakeWindow extends EventEmitter {
    constructor(config) {
      super();
      this.config = config;
      this.destroyed = false;
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = (handler) => { this.openHandler = handler; };
      windows.push(this);
    }
    isDestroyed() { return this.destroyed; }
    destroy() { this.destroyed = true; this.emit('closed'); }
    show() { this.shown = true; }
    loadURL(target) {
      this.url = target;
      this.config.webPreferences.session.request(
        { url: target, resourceType: 'mainFrame', method: 'GET' },
        ({ cancel }) => assert.equal(cancel, false),
      );
      if (options.loadFailure) return Promise.reject(new Error(`Failed to load ${target}`));
      if (options.hangLoad) return new Promise(() => {});
      return Promise.resolve();
    }
  }
  const session = {
    fromPartition(name, config) {
      partitions.push({ name, config });
      const ses = new EventEmitter();
      ses.webRequest = {
        onBeforeRequest: (handler) => { ses.request = handler; },
        onHeadersReceived: (handler) => { ses.headers = handler; },
      };
      ses.setPermissionRequestHandler = (handler) => { ses.permissionRequest = handler; };
      ses.setPermissionCheckHandler = (handler) => { ses.permissionCheck = handler; };
      ses.setDevicePermissionHandler = (handler) => { ses.devicePermission = handler; };
      ses.cleaned = [];
      for (const method of ['closeAllConnections', 'clearStorageData', 'clearCache']) {
        ses[method] = async () => { ses.cleaned.push(method); };
      }
      sessions.push(ses);
      return ses;
    },
  };
  let disposed = 0;
  let spawnedId;
  const preview = createHtmlPreview({
    slide: options.slide || slide, parent, BrowserWindow: FakeWindow, session,
    spawnServer(id) { spawnedId = id; return child; },
    readinessMs: options.readinessMs || 1000,
    killMs: 10,
    onDispose() { disposed += 1; },
  });
  preview.ready.catch(() => {});
  t.after(() => preview.dispose());
  return {
    preview, parent, child, signals, partitions, sessions, windows,
    get spawnedId() { return spawnedId; },
    get disposed() { return disposed; },
    send(value = readiness) { child.stdout.write(`${JSON.stringify(value)}\n`); },
  };
}

test('slide IDs and IPC require the main window and its main frame', () => {
  for (const value of ['s-123', '123', 'a_b']) assert.equal(isSlideId(value), true);
  for (const value of [null, {}, ['s1'], '', '-flag', 'a/b', '../s1', url, 'a'.repeat(129)]) {
    assert.equal(isSlideId(value), false);
  }
  const contents = { mainFrame: {} };
  const parent = { isDestroyed: () => false, webContents: contents };
  assert.equal(isMainSender({ sender: contents, senderFrame: contents.mainFrame }, parent), true);
  assert.equal(isMainSender({ sender: {}, senderFrame: contents.mainFrame }, parent), false);
  assert.equal(isMainSender({ sender: contents, senderFrame: {} }, parent), false);
  assert.equal(isMainSender({ sender: contents, senderFrame: contents.mainFrame }, null), false);
});

test('readiness accepts only canonical loopback HTTP with a token and matching metadata', () => {
  assert.equal(validateReadiness(readiness, slide).origin, 'http://127.0.0.1:54321');
  for (const candidate of [
    url.replace('http:', 'https:'), url.replace('127.0.0.1', 'localhost'),
    url.replace('127.0.0.1', '127.1'), url.replace('127.0.0.1', '127.0.0.1.evil.test'),
    url.replace('127.0.0.1', 'user@127.0.0.1'), url.replace(':54321', ''),
    url.replace(':54321', ':0'), url + 'other.html',
    url.replace('abcdefghijklmnop1234567890', 'short'), `${url}?x=1`, `${url}#x`,
    `${url}?`, `${url}#`,
    ` ${url}`, url + 'a/../',
    'file:///tmp/index.html', 'javascript:alert(1)',
  ]) assert.throws(() => previewBoundary(candidate));
  for (const [key, value] of Object.entries({
    ok: false, slide_id: 'other', page_key: 'other', slide_number: 2,
    preview_profile: 'original', warnings: null,
  })) assert.throws(() => validateReadiness({ ...readiness, [key]: value }, slide));
});

test('requests stay within the exact origin and token, with media-only data/blob', () => {
  const boundary = previewBoundary(url);
  const asset = url + 'assets/video.mp4';
  const allows = (target, resourceType = 'image', method = 'GET') =>
    allowedPreviewRequest(boundary, { url: target, resourceType, method });
  assert.equal(allows(asset, 'media'), true);
  assert.equal(allows(url + 'resource/assets%2Fvideo.mp4', 'media'), true);
  assert.equal(allows(url, 'mainFrame'), true);
  assert.equal(allows(asset, 'mainFrame'), false);
  for (const target of [
    asset.replace(':54321', ':54322'), asset.replace('http:', 'https:'),
    asset.replace('127.0.0.1', 'localhost'), asset.replace('/assets/', 'x/assets/'),
    asset.replace('/assets/', '/../'), asset.replace('video.mp4', '%2e%2e%2fprivate'),
    asset.replace('video.mp4', '%255cetc'), asset.replace('video.mp4', '%00'),
    asset.replace('video.mp4', '%ZZ'), 'file:///etc/passwd',
    'pptlib-asset://local/private', 'https://example.com/image.png',
  ]) assert.equal(allows(target), false, target);
  for (const type of ['subFrame', 'object', 'webSocket']) assert.equal(allows(asset, type), false);
  assert.equal(allows(asset, 'xhr', 'POST'), false);
  assert.equal(allows('data:image/png;base64,AAAA'), true);
  assert.equal(allows('data:text/html,hello'), false);
  assert.equal(allows('data:video/mp4;base64,AAAA', 'script'), false);
  assert.equal(allows('blob:http://127.0.0.1:54321/id', 'media'), true);
  assert.equal(allows('blob:https://example.com/id', 'media'), false);
  assert.equal(allows('blob:http://127.0.0.1:54321/id', 'script'), false);
});

test('sandbox uses a unique nonpersistent session and returns no token URL', async (t) => {
  const first = fixture(t);
  const second = fixture(t);
  const line = `${JSON.stringify(readiness)}\n`;
  first.child.stdout.write(line.slice(0, 27));
  assert.equal(first.windows.length, 0);
  first.child.stdout.write(line.slice(27));
  second.send();
  assert.deepEqual(await first.preview.ready, { ok: true });
  assert.deepEqual(await second.preview.ready, { ok: true });
  assert.equal(first.spawnedId, slide.slide_id);
  assert.notEqual(first.partitions[0].name, second.partitions[0].name);
  assert.equal(first.partitions[0].name.startsWith('persist:'), false);
  const prefs = first.windows[0].config.webPreferences;
  assert.equal(prefs.sandbox, true);
  assert.equal(prefs.nodeIntegration, false);
  assert.equal(prefs.nodeIntegrationInWorker, false);
  assert.equal(prefs.nodeIntegrationInSubFrames, false);
  assert.equal(prefs.contextIsolation, true);
  assert.equal(prefs.webviewTag, false);
  assert.equal(Object.hasOwn(prefs, 'preload'), false);
  assert.deepEqual(first.windows[0].openHandler(), { action: 'deny' });
  const ses = first.sessions[0];
  ses.permissionRequest(null, 'media', (allowed) => assert.equal(allowed, false));
  assert.equal(ses.permissionCheck(), false);
  assert.equal(ses.devicePermission(), false);
  let prevented = 0;
  ses.emit('will-download', { preventDefault() { prevented += 1; } });
  for (const name of ['will-navigate', 'will-frame-navigate', 'will-attach-webview']) {
    first.windows[0].webContents.emit(name, { preventDefault() { prevented += 1; } });
  }
  assert.equal(prevented, 4);
  ses.request({ url, resourceType: 'mainFrame' }, ({ cancel }) => assert.equal(cancel, true));
  ses.headers({ statusCode: 302 }, ({ cancel }) => assert.equal(cancel, true));
  ses.headers({ statusCode: 200 }, ({ cancel }) => assert.equal(cancel, false));
  await first.preview.dispose();
  assert.equal(first.windows[0].destroyed, true);
  assert.equal(first.disposed, 1);
  assert.equal(ses.cleaned.length, 3);
  ses.request({ url, resourceType: 'image' }, ({ cancel }) => assert.equal(cancel, true));
  await first.preview.dispose();
  assert.equal(first.disposed, 1);
  assert.deepEqual(first.signals, ['SIGTERM']);
});

test('unapproved sources, malformed readiness and output overflow fail closed', async (t) => {
  const denied = fixture(t, { slide: { ...slide, source_format: 'pptx' } });
  await assert.rejects(denied.preview.ready);
  assert.equal(denied.spawnedId, undefined);
  for (const data of [
    'not JSON\n', `${JSON.stringify({ ...readiness, page_key: 'stale' })}\n`,
    'x'.repeat(65537), `${JSON.stringify(readiness)}\n{}\n`,
  ]) {
    const f = fixture(t);
    f.child.stdout.write(data);
    await assert.rejects(f.preview.ready);
    assert.deepEqual(f.signals, ['SIGTERM']);
    assert.equal(f.windows.length, 0);
  }
  const stderr = fixture(t);
  stderr.child.stderr.write('x'.repeat(65537));
  await assert.rejects(stderr.preview.ready, /too much error output/);
});

test('parent close during startup cancels readiness and stops server', async (t) => {
  const f = fixture(t);
  f.parent.emit('close');
  await assert.rejects(f.preview.ready, /closed/);
  await f.preview.dispose();
  f.send();
  assert.equal(f.windows.length, 0);
  assert.equal(f.parent.listenerCount('closed'), 0);
  assert.deepEqual(f.signals, ['SIGTERM']);
});

test('window/parent close, child crash, renderer crash, redirect and load failure clean up', async (t) => {
  for (const trigger of ['window', 'parent', 'child', 'renderer', 'redirect', 'load']) {
    const f = fixture(t);
    f.send();
    await f.preview.ready;
    if (trigger === 'window') f.windows[0].destroy();
    if (trigger === 'parent') f.parent.emit('closed');
    if (trigger === 'child') f.child.emit('exit', 1);
    if (trigger === 'renderer') f.windows[0].webContents.emit('render-process-gone');
    if (trigger === 'redirect') f.windows[0].webContents.emit('will-redirect', { preventDefault() {} });
    if (trigger === 'load') f.windows[0].webContents.emit('did-fail-load');
    await f.preview.dispose();
    assert.equal(f.windows[0].destroyed, true, trigger);
    assert.equal(f.sessions[0].cleaned.length, 3, trigger);
    assert.equal(f.disposed, 1, trigger);
    assert.deepEqual(f.signals, trigger === 'child' ? [] : ['SIGTERM'], trigger);
  }
});

test('load errors and CLI stderr never expose the token URL', async (t) => {
  const f = fixture(t, { loadFailure: true });
  f.send();
  await assert.rejects(f.preview.ready, (error) => {
    assert.equal(error.message.includes(url), false);
    return /failed to load/.test(error.message);
  });
  const cli = fixture(t);
  cli.child.stderr.write(`Source changed; failed ${url}\n`);
  cli.child.emit('exit', 1);
  await assert.rejects(cli.preview.ready, (error) => {
    assert.equal(error.message.includes(url), false);
    assert.equal(error.message.includes('abcdefghijklmnop'), false);
    return /Source changed/.test(error.message);
  });
});

test('backend source-version rejection is actionable without echoing arbitrary output', async (t) => {
  const f = fixture(t);
  f.send({ ok: false, error: 'SOURCE_CHANGED', message: `Changed at ${url}` });
  await assert.rejects(f.preview.ready, (error) =>
    !error.message.includes(url) && error.message.includes('重新导入'));
  assert.equal(f.windows.length, 0);
});

test('readiness/load timeouts and uncooperative children have bounded teardown', async (t) => {
  for (const hangLoad of [false, true]) {
    const f = fixture(t, { hangLoad, readinessMs: 15, ignoreTerm: true });
    if (hangLoad) f.send();
    await Promise.all([assert.rejects(f.preview.ready, /timed out/), delay(40)]);
    await f.preview.dispose();
    assert.deepEqual(f.signals, ['SIGTERM', 'SIGKILL']);
    assert.equal(f.disposed, 1);
  }
});

function rendererFixture() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        value: '', dataset: {}, hidden: false, textContent: '', innerHTML: '',
        disabled: false, style: {}, listeners: {},
        classList: { toggle() {}, add() {}, remove() {} },
        setAttribute() {}, removeAttribute() {}, querySelectorAll: () => [],
        addEventListener(name, callback) { this.listeners[name] = callback; },
        appendChild() {}, focus() {},
      });
    }
    return elements.get(id);
  }
  const document = {
    getElementById: element,
    querySelector: () => null,
    querySelectorAll: (selector) => selector === '[data-html-only]'
      ? [element('filter-format')] : [],
    addEventListener() {},
    createElement: () => element('created'),
  };
  const context = vm.createContext({
    document,
    requestAnimationFrame: (callback) => callback(),
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {} },
    window: {
      pptlib: {
        onLog() {}, onProgress() {},
        paths: () => new Promise(() => {}),
        loadCatalog: () => new Promise(() => {}),
      },
    },
  });
  vm.runInContext(readFileSync(path.join(__dirname, 'renderer/app.js'), 'utf8'), context);
  vm.runInContext(`
    catalog.slides = [
      {slide_id:'p1', deck_id:'d1', deck_name:'PPT', page_type:'body', source_format:'pptx', searchIndex:''},
      {slide_id:'h1', deck_id:'d2', deck_name:'HTML', slide_number:1, source_format:'render_deck_html',
       capabilities:{dynamic_preview:true, video:true, requires_network:true},
       warnings:[{message:'<script>not markup</script>'}], searchIndex:''},
    ];
    catalog.byId = Object.fromEntries(catalog.slides.map(s => [s.slide_id, s]));
    buildDecks();
  `, context);
  return { element, run: (code) => vm.runInContext(code, context) };
}

test('renderer filters formats, permits HTML selection and blocks mixed export', () => {
  const f = rendererFixture();
  f.run("applyHtmlFeature(true); searchScope = 'all'; filterFormatEl.value = 'render_deck_html'");
  assert.equal(f.run('visibleSlides().map(s => s.slide_id).join()'), 'h1');
  assert.equal(f.run("folderDeckCount(visibleTree(''))"), 1);
  f.run("selectedIds.push('p1'); renderSelected()");
  assert.equal(f.element('sel-export').disabled, false);
  f.run("selectedIds.push('h1'); renderSelected()");
  assert.equal(f.element('sel-count').textContent, 2);
  assert.equal(f.element('sel-export').disabled, true);
  assert.equal(f.element('sel-export-warning').hidden, false);
  f.run('selectedIds.pop(); renderSelected()');
  assert.equal(f.element('sel-export').disabled, false);
  assert.equal(f.element('sel-export-warning').hidden, true);
  f.run('applyHtmlFeature(false)');
  assert.equal(f.element('filter-format').hidden, true);
  assert.equal(f.element('filter-format').value, '');
});

test('import source selection accumulates, de-duplicates and removes one source', () => {
  const f = rendererFixture();
  const merged = JSON.parse(f.run(`
    JSON.stringify(mergeImportSources(
      [{path:'/deck/a.pptx', isDir:false}],
      ['/deck/a.pptx', '/deck/b.html', '/deck/assets'],
      false
    ))
  `));
  assert.deepEqual(merged, {
    sources: [
      { path: '/deck/a.pptx', isDir: false },
      { path: '/deck/b.html', isDir: false },
      { path: '/deck/assets', isDir: false },
    ],
    added: 2,
    duplicates: 1,
  });
  const remaining = JSON.parse(f.run(`
    JSON.stringify(withoutImportSource(
      ${JSON.stringify(merged.sources)},
      '/deck/b.html'
    ))
  `));
  assert.deepEqual(remaining, [
    { path: '/deck/a.pptx', isDir: false },
    { path: '/deck/assets', isDir: false },
  ]);
});

test('compose preflight distinguishes blockers, warnings and ready state', () => {
  const f = rendererFixture();
  f.run(`renderComposePreflight({
    ok:false, page_count:2, source_count:1, estimated_output_bytes:4096,
    estimated_fidelity:'不可导出', warnings:[],
    blockers:[{code:'SOURCE_CHANGED', message:'源文件已变化'}]
  })`);
  assert.equal(f.element('compose-check').hidden, false);
  assert.equal(f.element('compose-setup').hidden, true);
  assert.equal(f.element('confirm-compose').disabled, true);
  assert.match(f.element('compose-check-issues').innerHTML, /源文件已变化/);

  f.run(`renderComposePreflight({
    ok:true, page_count:2, source_count:2, estimated_output_bytes:10485760,
    estimated_fidelity:'B', warnings:['EXTERNAL_LINK_PRESERVED'], blockers:[],
    preflight_token:'token'
  })`);
  assert.equal(f.element('confirm-compose').disabled, false);
  assert.match(f.element('compose-check-summary').innerHTML, /10 MB/);
  assert.match(f.element('compose-check-issues').innerHTML, /外部链接/);
});

test('selection changes invalidate a generated manifest and preflight', () => {
  const f = rendererFixture();
  f.run(`
    state.manifest = '/tmp/old-manifest.json';
    state.output = '/tmp/out.pptx';
    state.manifestFromSelection = true;
    state.composePreflight = {ok:true, preflight_token:'old'};
    invalidateSelectionManifest();
  `);

  assert.equal(f.run('state.manifest'), null);
  assert.equal(f.run('state.composePreflight'), null);
  assert.equal(f.run('state.manifestFromSelection'), false);
  assert.equal(f.element('run-compose').disabled, true);
});

test('dynamic control requires HTML capability and feature gate; warnings remain plain text', () => {
  const f = rendererFixture();
  f.run("applyHtmlFeature(true); lbList = ['h1','p1']; lbIndex = 0; showLightboxSlide()");
  assert.equal(f.element('lb-dynamic').hidden, false);
  assert.match(f.element('lb-warnings').textContent, /原始脚本/);
  assert.match(f.element('lb-warnings').textContent, /禁止外部请求/);
  assert.match(f.element('lb-warnings').textContent, /<script>not markup<\/script>/);
  assert.equal(f.element('lb-warnings').innerHTML, '');
  f.run('applyHtmlFeature(false); showLightboxSlide()');
  assert.equal(f.element('lb-dynamic').hidden, true);
  f.run("applyHtmlFeature(true); catalog.byId.h1.capabilities.dynamic_preview = false; showLightboxSlide()");
  assert.equal(f.element('lb-dynamic').hidden, true);
  f.run('lbIndex = 1; showLightboxSlide()');
  assert.equal(f.element('lb-dynamic').hidden, true);
  assert.equal(f.element('lb-warnings').hidden, true);
});

test('main process defaults HTML on, respects off, rejects foreign IPC and waits before quit', async () => {
  const app = new EventEmitter();
  let quitCalls = 0;
  Object.assign(app, {
    setName(name) { assert.equal(name, '拼页'); },
    getPath: () => __dirname,
    requestSingleInstanceLock: () => false,
    quit() { quitCalls += 1; },
  });
  const handlers = new Map();
  const parent = { isDestroyed: () => false, webContents: { mainFrame: {} } };
  const context = vm.createContext({
    __dirname,
    process: { env: {}, platform: process.platform },
    parent,
    require(id) {
      if (id === 'electron') return {
        app, ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
        protocol: { registerSchemesAsPrivileged() {} },
      };
      if (id === './renderer/html-preview') return require('./renderer/html-preview');
      return require(id);
    },
  });
  vm.runInContext(readFileSync(path.join(__dirname, 'main.js'), 'utf8'), context);
  const run = (code) => vm.runInContext(code, context);
  run('mainWindow = parent');
  for (const channel of [
    'scan-plan:list',
    'scan-plan:create',
    'scan-plan:update',
    'scan-plan:delete',
    'scan-plan:preview',
    'scan-plan:run',
    'scan-plan:run-all',
    'scan-run:current',
    'scan-run:stop',
    'scan-run:history',
    'scan-run:retry',
  ]) {
    assert.equal(typeof handlers.get(channel), 'function', channel);
  }
  assert.equal(run('childEnv().PPTLIB_ENABLE_HTML'), '1');
  assert.equal(handlers.get('paths')().htmlEnabled, true);
  assert.equal(run('app.isPackaged = true; runtimeAvailable()'), false);
  assert.equal(run('app.isPackaged = false; runtimeAvailable()'), true);
  assert.equal(run(`planIsDue(
    {id:'p1', enabled:true, scheduleKind:'daily', scheduleTime:'02:30',
     windowStart:'02:00', windowEnd:'05:00'},
    [],
    new Date(2026, 9, 5, 2, 31)
  )`), true);
  assert.equal(run(`planIsDue(
    {id:'p1', enabled:true, scheduleKind:'daily', scheduleTime:'02:30',
     windowStart:'02:00', windowEnd:'05:00'},
    [{planId:'p1', triggerType:'scheduled', startedAt:new Date(2026, 9, 5, 2, 30).toISOString()}],
    new Date(2026, 9, 5, 2, 31)
  )`), false);
  assert.equal(run(`planIsDue(
    {id:'p1', enabled:true, scheduleKind:'daily', scheduleTime:'02:30',
     windowStart:'02:00', windowEnd:'05:00'},
    [],
    new Date(2026, 9, 5, 10, 0)
  )`), false);
  assert.equal(run(`planIsDue(
    {id:'p2', enabled:true, scheduleKind:'daily', scheduleTime:'00:30',
     windowStart:'23:00', windowEnd:'02:00'},
    [],
    new Date(2026, 9, 5, 23, 30)
  )`), false);
  assert.equal(run(`planIsDue(
    {id:'p2', enabled:true, scheduleKind:'daily', scheduleTime:'00:30',
     windowStart:'23:00', windowEnd:'02:00'},
    [],
    new Date(2026, 9, 6, 0, 31)
  )`), true);
  assert.equal(run(`planIsDue(
    {id:'p3', enabled:true, scheduleKind:'daily', scheduleTime:'23:30',
     windowStart:'23:00', windowEnd:'02:00'},
    [],
    new Date(2026, 9, 6, 1, 0)
  )`), true);
  assert.equal(run(`planIsDue(
    {id:'p3', enabled:true, scheduleKind:'daily', scheduleTime:'23:30',
     windowStart:'23:00', windowEnd:'02:00'},
    [{planId:'p3', triggerType:'scheduled',
      startedAt:new Date(2026, 9, 6, 0, 30).toISOString(),
      scheduledFor:new Date(2026, 9, 5, 23, 30).toISOString()}],
    new Date(2026, 9, 6, 1, 0)
  )`), false);
  assert.equal(run(`planWasMissed(
    {id:'p1', enabled:true, scheduleKind:'daily', scheduleTime:'02:30',
     windowStart:'02:00', windowEnd:'05:00', updatedAt:'2026-10-04T12:00:00'},
    [],
    new Date(2026, 9, 5, 10, 0)
  )`), true);
  const open = handlers.get('html-preview');
  const event = { sender: parent.webContents, senderFrame: parent.webContents.mainFrame };
  await assert.rejects(open({ ...event, sender: {} }, 's1'), /不允许/);
  await assert.rejects(open({ ...event, senderFrame: {} }, 's1'), /不允许/);
  await assert.rejects(open(event, url), /无效/);
  await assert.rejects(open(event, 's1'), /不支持/);
  run("process.env.PPTLIB_ENABLE_HTML = '0'");
  assert.equal(run('childEnv().PPTLIB_ENABLE_HTML'), '0');
  assert.equal(handlers.get('paths')().htmlEnabled, false);
  await assert.rejects(open(event, 's1'), /关闭/);
  run(`
    let finishCleanup;
    const cleanupPending = new Promise(resolve => { finishCleanup = resolve; });
    const pendingPreview = {dispose: () => cleanupPending};
    htmlPreviews.add(pendingPreview);
  `);
  quitCalls = 0;
  let prevented = 0;
  app.emit('before-quit', { preventDefault() { prevented += 1; } });
  app.emit('before-quit', { preventDefault() { prevented += 1; } });
  assert.equal(prevented, 2);
  assert.equal(quitCalls, 0);
  run('htmlPreviews.delete(pendingPreview); finishCleanup()');
  await delay(0);
  assert.equal(quitCalls, 1);
});
