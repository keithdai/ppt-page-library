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
        querySelector: (selector) => element(`${id} ${selector}`),
        addEventListener(name, callback) { this.listeners[name] = callback; },
        appendChild() {}, focus() {},
      });
    }
    return elements.get(id);
  }
  const document = {
    getElementById: element,
    querySelector: (selector) => selector === '#compose-prog .prog-num'
      ? element('compose-progress-copy') : null,
    querySelectorAll: (selector) => selector === '[data-html-only]'
      ? [element('filter-format'), element('import-url-form'), element('compose-format')] : [],
    addEventListener() {},
    createElement: () => element('created'),
  };
  const context = vm.createContext({
    document,
    URL,
    requestAnimationFrame: (callback) => callback(),
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {} },
    window: {
      pptlib: {
        onLog() {}, onProgress() {}, onTaskState() {},
        paths: () => new Promise(() => {}),
        loadLibrary: () => new Promise(() => {}),
        searchLibrary: () => new Promise(() => {}),
        saveSelection: () => new Promise(() => {}),
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

test('renderer sends format filters and lets mixed selections proceed to output preflight', () => {
  const f = rendererFixture();
  f.run("applyHtmlFeature(true); searchScope = 'all'; filterFormatEl.value = 'render_deck_html'");
  assert.equal(f.run('pageFilters().source_format'), 'render_deck_html');
  f.run("selectedIds.push('p1'); renderSelected()");
  assert.equal(f.element('sel-export').disabled, false);
  f.run("selectedIds.push('h1'); renderSelected()");
  assert.equal(f.element('sel-count').textContent, 2);
  assert.equal(f.element('sel-export').disabled, false);
  assert.equal(f.element('sel-export-warning').hidden, false);
  assert.equal(f.element('sel-export-warning').textContent, 'HTML 页请选 HTML 输出');
  f.run('selectedIds.pop(); renderSelected()');
  assert.equal(f.element('sel-export').disabled, false);
  assert.equal(f.element('sel-export-warning').hidden, true);
  f.run('applyHtmlFeature(false)');
  assert.equal(f.element('filter-format').hidden, true);
  assert.equal(f.element('filter-format').value, '');
  assert.equal(f.element('import-url-form').hidden, true);
  assert.equal(f.element('compose-format').hidden, true);
});

test('URL input validates, de-duplicates, preserves URL syntax and never adds a disk path', () => {
  const f = rendererFixture();
  f.run('applyHtmlFeature(true)');
  f.element('import-url').value = ' https://example.com/a//deck.html?x=1&y=2#page-3 ';
  const submit = () => f.element('import-url-form').listeners.submit({ preventDefault() {} });
  submit();
  assert.deepEqual(JSON.parse(f.run('JSON.stringify(state.importSources)')), [
    { kind: 'url', url: 'https://example.com/a//deck.html?x=1&y=2#page-3' },
  ]);
  f.element('import-url').value = 'https://example.com/a//deck.html?x=1&y=2#page-3';
  submit();
  assert.equal(f.run('state.importSources.length'), 1);
  assert.match(f.element('import-selection-note').textContent, /无需重复/);
  assert.equal(f.run('importSourceType(state.importSources[0])'), '在线链接');
  assert.equal(f.run('withoutImportSource(state.importSources, state.importSources[0]).length'), 0);
  for (const value of ['not a URL', 'file:///private/file.html', 'https://user:secret@example.com', 'https://', 'https://example.com\\a']) {
    f.element('import-url').value = value;
    submit();
    assert.equal(f.element('import-url-error').hidden, false, value);
    assert.equal(f.element('import-url').value, value);
    assert.equal(f.run('state.importSources.length'), 1);
  }
  f.run('setImportBusy(true)');
  assert.equal(f.element('import-url').disabled, true);
  assert.equal(f.element('add-import-url').disabled, true);
  f.run('setImportBusy(false); applyHtmlFeature(false)');
  f.element('import-url').value = 'https://example.com/other';
  submit();
  assert.equal(f.run('state.importSources.length'), 1);
});

test('renderer imports mixed sources without flattening URLs and keeps failure details visible', async () => {
  const f = rendererFixture();
  f.run(`
    applyHtmlFeature(true);
    state.importSources = [{path:'/tmp/local.pptx'}, {kind:'url', url:'https://example.com/deck'}];
    window.pptlib.import = async sources => {
      window.sentSources = sources;
      return [
        {imported:[{created:true, name:'local.pptx'}], failed:[]},
        {ok:false, imported:[], failed:[{url:'https://example.com/deck', error:'<download failed>'}]}
      ];
    };
  `);
  await f.element('run-import').listeners.click();
  assert.equal(f.run('JSON.stringify(window.sentSources)'),
    '["/tmp/local.pptx",{"kind":"url","url":"https://example.com/deck"}]');
  assert.equal(f.element('import-summary').hidden, false);
  assert.match(f.element('import-summary').innerHTML, /https:\/\/example.com\/deck/);
  assert.match(f.element('import-summary').innerHTML, /&lt;download failed&gt;/);
  assert.equal(f.element('import-err').hidden, false);
  assert.equal(f.element('import-ok').hidden, true);
  assert.equal(f.element('run-import').disabled, false);
});

test('download progress is readable and unrelated task progress is ignored', () => {
  const f = rendererFixture();
  f.run(`setImportProgress({stage:'download_start', path:'https://example.com/deck', taskKind:'导入并渲染'})`);
  assert.match(f.element('import-prog-text').textContent, /正在连接在线演示.*https:\/\/example.com\/deck/);
  f.run(`setImportProgress({stage:'download_resource', name:'image.png', taskKind:'导入并渲染'})`);
  assert.match(f.element('import-prog-text').textContent, /正在下载页面资源.*image.png/);
  f.run(`setImportProgress({stage:'download_resource', resource_count:3, bytes:1048576, taskKind:'导入并渲染'})`);
  assert.match(f.element('import-prog-text').textContent, /已保存 3 项.*1 MB/);
  const text = f.element('import-prog-text').textContent;
  f.run(`setImportProgress({stage:'scan', total:4, taskKind:'自动更新'})`);
  assert.equal(f.element('import-prog-text').textContent, text);
});

test('HTML controls are gated in markup and compatibility copy explains flattened PPTX pages', () => {
  const markup = readFileSync(path.join(__dirname, 'renderer/index.html'), 'utf8');
  assert.match(markup, /<form id="import-url-form"[^>]*data-html-only hidden>/);
  assert.match(markup, /class="field compose-format" data-html-only hidden/);
  assert.match(markup, /<label for="import-url">/);
  assert.match(markup, /<option value="html">HTML 离线单文件（\.html）/);
  assert.match(markup, /<option value="zip">HTML 资源包（\.zip）/);
  assert.doesNotMatch(markup, /第一阶段仅支持|暂不支持导出/);
  const f = rendererFixture();
  f.run(`renderComposePreflight({
    ok:true, blockers:[], preflight_token:'token',
    warnings:['PPTX_FLATTENED_TO_IMAGE: details', 'HTML_EXTERNAL_RESOURCES_BLOCKED']
  })`);
  assert.match(f.element('compose-check-issues').innerHTML, /不保留原生编辑/);
  assert.match(f.element('compose-check-issues').innerHTML, /部分内容可能缺失/);
});

test('source URL provenance survives normalization and is displayed as plain text', () => {
  const f = rendererFixture();
  f.run(`
    const deck = normalizeDeck({id:'d2', name:'HTML', source_url:'https://example.com/<source>'});
    catalog.decks = [deck];
    rememberSlide(catalog.byId.h1);
    lbList = ['h1']; lbIndex = 0; showLightboxSlide();
  `);
  assert.equal(f.element('lb-source').hidden, false);
  assert.equal(f.element('lb-source').textContent, '在线来源：https://example.com/<source>');
  assert.equal(f.element('lb-source').innerHTML, '');
});

test('mixed selection writes its ordered manifest before choosing a compatible output', async () => {
  const f = rendererFixture();
  f.run(`
    applyHtmlFeature(true);
    selectedIds.push('h1', 'p1');
    window.pptlib.writeManifest = async ids => {
      window.manifestIds = ids;
      return {path:'/tmp/mixed.json', count:ids.length};
    };
  `);
  await f.element('sel-export').listeners.click();
  assert.equal(f.run('JSON.stringify(window.manifestIds)'), '["h1","p1"]');
  assert.equal(f.run('state.manifest'), '/tmp/mixed.json');
  assert.equal(f.run('state.manifestFromSelection'), true);
  assert.equal(f.element('sel-export').disabled, false);
});

test('format changes invalidate output and preflight, and the save picker receives the format', async () => {
  const f = rendererFixture();
  f.run(`
    applyHtmlFeature(true);
    state.manifest = '/tmp/manifest.json';
    state.output = '/tmp/out.pptx';
    state.composePreflight = {ok:true, preflight_token:'old'};
    window.pptlib.pickSaveOutput = async options => {
      window.saveOptions = options;
      return '/tmp/out.' + options.format;
    };
  `);
  for (const format of ['html', 'zip', 'pptx']) {
    f.element('output-format').value = format;
    f.element('output-format').listeners.change();
    assert.equal(f.run('state.output'), null);
    assert.equal(f.run('state.composePreflight'), null);
    assert.equal(f.element('run-compose').disabled, true);
    await f.element('pick-output').listeners.click();
    assert.equal(f.run('window.saveOptions.format'), format);
    assert.equal(f.run('state.output'), `/tmp/out.${format}`);
    assert.equal(f.element('run-compose').disabled, false);
  }
  f.run("window.pptlib.pickSaveOutput = async () => '/tmp/wrong.zip'");
  await f.element('pick-output').listeners.click();
  assert.match(f.element('compose-err').textContent, /\.pptx/);
  assert.equal(f.run('state.output'), '/tmp/out.pptx');
  f.run('applyHtmlFeature(false)');
  assert.equal(f.element('compose-format').hidden, true);
  assert.equal(f.run('state.outputFormat'), 'pptx');
  assert.equal(f.element('confirm-compose').textContent, '确认导出');
});

test('all formats use the existing preflight/compose contract and HTML is blocked only for PPTX', async () => {
  const f = rendererFixture();
  f.run(`
    applyHtmlFeature(true);
    selectedIds.push('h1', 'p1');
    state.manifest = '/tmp/mixed.json';
    window.pptlib.preflightCompose = async options => {
      window.checkedOptions = options;
      return options.format === 'pptx'
        ? {ok:false, blockers:[{message:'HTML 页请选 HTML 输出'}]}
        : {ok:true, blockers:[], preflight_token:'html-token'};
    };
    window.pptlib.compose = async options => {
      window.composedOptions = options;
      return {ok:true, output_path:options.output, page_count:2};
    };
  `);
  for (const format of ['pptx', 'html', 'zip']) {
    f.run(`state.outputFormat = '${format}'; state.output = '/tmp/out.${format}'; refreshComposeReady()`);
    await f.element('run-compose').listeners.click();
    assert.equal(f.run('window.checkedOptions.format'), format);
    if (format === 'pptx') {
      assert.equal(f.element('confirm-compose').disabled, true);
      assert.match(f.element('compose-check-issues').innerHTML, /HTML 页请选 HTML 输出/);
      continue;
    }
    assert.equal(f.element('confirm-compose').disabled, false);
    await f.element('confirm-compose').listeners.click();
    assert.equal(f.run('window.composedOptions.output'), `/tmp/out.${format}`);
    assert.equal(f.run('window.composedOptions.preflightToken'), 'html-token');
    assert.doesNotMatch(f.element('compose-result-copy').textContent, /undefined/);
    assert.match(f.element('compose-result-copy').textContent, /HTML/);
    assert.equal(f.element('compose-manifest').hidden, true);
  }
});

test('PPTX export works with HTML disabled and resets any stale HTML output', async () => {
  const f = rendererFixture();
  f.run(`
    state.outputFormat = 'html'; state.output = '/tmp/old.html';
    state.composePreflight = {ok:true, preflight_token:'old'};
    applyHtmlFeature(false);
    state.manifest = '/tmp/pptx.json';
    window.pptlib.pickSaveOutput = async ({format}) => '/tmp/out.' + format;
    window.pptlib.preflightCompose = async options => ({ok:true, preflight_token:'pptx-token'});
    window.pptlib.compose = async options => {
      window.composedOptions = options;
      return {ok:true, output_path:options.output, page_count:1, fidelity_level:'A'};
    };
  `);
  assert.equal(f.run('state.output'), null);
  assert.equal(f.run('state.composePreflight'), null);
  await f.element('pick-output').listeners.click();
  await f.element('run-compose').listeners.click();
  await f.element('confirm-compose').listeners.click();
  assert.equal(f.run('window.composedOptions.output'), '/tmp/out.pptx');
  assert.equal(f.element('compose-result-copy').textContent, '1 页已按选片顺序生成，保真等级 A。');
  assert.match(f.element('compose-description').textContent, /原生 OOXML/);
});

test('a selection change during preflight cannot resurrect an old token', async () => {
  const f = rendererFixture();
  f.run(`
    state.manifest = '/tmp/old.json'; state.output = '/tmp/out.pptx';
    state.manifestFromSelection = true;
    window.pptlib.preflightCompose = () => new Promise(resolve => { window.finishCheck = resolve; });
  `);
  const pending = f.element('run-compose').listeners.click();
  assert.equal(f.element('output-format').disabled, true);
  f.run(`invalidateSelectionManifest(); window.finishCheck({ok:true, preflight_token:'old'})`);
  await pending;
  assert.equal(f.run('state.composePreflight'), null);
  assert.equal(f.element('run-compose').disabled, true);
  assert.match(f.element('compose-err').textContent, /重新检查/);
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

test('compose setup keeps manifest import inside advanced options', () => {
  const markup = readFileSync(path.join(__dirname, 'renderer/index.html'), 'utf8');
  const advancedStart = markup.indexOf('<details class="compose-advanced">');
  const advancedEnd = markup.indexOf('</details>', advancedStart);
  const outputPicker = markup.indexOf('id="pick-output"');
  const manifestPicker = markup.indexOf('id="pick-manifest"');

  assert.ok(outputPicker > 0 && outputPicker < advancedStart);
  assert.ok(manifestPicker > advancedStart && manifestPicker < advancedEnd);
  assert.doesNotMatch(markup, /手动选择 manifest/);
});

test('compose setup shows selection and output without exposing manifest path', () => {
  const f = rendererFixture();
  assert.equal(
    f.element('compose-selection-status').textContent,
    '尚未载入选片，请返回选片后再进入组合',
  );
  assert.equal(f.element('compose-selection-status').className, 'compose-selection-status');
  f.run(`
    selectedIds.push('p1', 'p2');
    state.manifest = '/tmp/generated-manifest.json';
    state.manifestFromSelection = true;
    refreshComposeReady();
  `);

  assert.equal(f.element('compose-selection-status').textContent, '已载入当前选片：2 页');
  assert.equal(
    f.element('compose-selection-status').className,
    'compose-selection-status is-ready',
  );
  assert.equal(f.element('compose-chosen').textContent, '尚未选择保存位置');
  assert.equal(f.element('run-compose').disabled, true);

  f.run(`
    state.output = '/tmp/composed.pptx';
    refreshComposeReady();
  `);
  assert.equal(f.element('compose-chosen').textContent, '保存到：/tmp/composed.pptx');
  assert.doesNotMatch(f.element('compose-chosen').textContent, /manifest/);
  assert.equal(f.element('run-compose').disabled, false);

  f.run(`
    state.manifestFromSelection = false;
    refreshComposeReady();
  `);
  assert.equal(f.element('compose-selection-status').textContent, '已导入已有选片清单');
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

test('fullscreen presentation mode hides selection chrome and fills the viewport', () => {
  const styles = readFileSync(path.join(__dirname, 'renderer/styles.css'), 'utf8');
  assert.match(
    styles,
    /\.lightbox:fullscreen \.lb-bar,[\s\S]*?\.lightbox:fullscreen \.lb-preview-error\s*\{\s*display:\s*none;/,
  );
  assert.match(
    styles,
    /\.lightbox:fullscreen \.lb-img\s*\{[\s\S]*?max-width:\s*100vw;[\s\S]*?max-height:\s*100vh;/,
  );
});

test('main process defaults HTML on, respects off, rejects foreign IPC and waits before quit', async () => {
  const app = new EventEmitter();
  let quitCalls = 0;
  Object.assign(app, {
    setName(name) { assert.equal(name, 'DeckAtlas'); },
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
  assert.equal(
    run('JSON.stringify(legacyUserDataDirs().map(dir => path.basename(dir)))'),
    '["拼页","PPT Page Library"]',
  );
  run('mainWindow = parent');
  const event = { sender: parent.webContents, senderFrame: parent.webContents.mainFrame };
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
  assert.equal(handlers.get('paths')(event).htmlEnabled, true);
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
  await assert.rejects(open({ ...event, sender: {} }, 's1'), /不允许/);
  await assert.rejects(open({ ...event, senderFrame: {} }, 's1'), /不允许/);
  await assert.rejects(open(event, url), /无效/);
  await assert.rejects(open(event, 's1'), /不支持/);
  run("process.env.PPTLIB_ENABLE_HTML = '0'");
  assert.equal(run('childEnv().PPTLIB_ENABLE_HTML'), '0');
  assert.equal(handlers.get('paths')(event).htmlEnabled, false);
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
