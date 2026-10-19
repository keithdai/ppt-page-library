'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { setImmediate: nextTurn } = require('node:timers/promises');

// Run the actual IPC handlers and coordinator without loading or launching Electron.
function mainFixture({ enabled = true, packaged = false, runCommand } = {}) {
  const handlers = new Map();
  const commands = [];
  const persisted = [];
  const events = [];
  const dialogs = [];
  const app = new EventEmitter();
  Object.assign(app, {
    isPackaged: packaged,
    setName() {},
    getPath: () => __dirname,
    requestSingleInstanceLock: () => false,
    quit() {},
  });
  let saveResult = { canceled: false, filePath: '/tmp/out.pptx' };
  const parent = {
    isDestroyed: () => false,
    webContents: {
      mainFrame: {},
      isDestroyed: () => false,
      send: (channel, payload) => events.push({ channel, payload }),
    },
  };
  function spawn(_binary, args, options) {
    assert.equal(options.shell, false);
    const child = new EventEmitter();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.exitCode = null;
    child.killed = false;
    let closed = false;
    function finish(code) {
      if (closed) return;
      closed = true;
      child.exitCode = code;
      child.emit('close', code);
    }
    child.kill = () => {
      child.killed = true;
      child.stderr.write('Task stopped');
      finish(1);
    };
    setImmediate(async () => {
      if (closed) return;
      if (args[0] === 'desktop' && args[1] === 'task-set') {
        persisted.push(JSON.parse(args[3]));
        child.stdout.write('{"ok":true}\n');
        finish(0);
        return;
      }
      commands.push([...args]);
      try {
        const response = await (runCommand?.(args, child) || { parsed: { ok: true } });
        if (closed) return;
        for (const progress of response.progress || []) {
          child.stdout.write(`@@PPTLIB_PROGRESS ${JSON.stringify(progress)}\n`);
        }
        if (response.stdout !== undefined) child.stdout.write(response.stdout);
        else child.stdout.write(JSON.stringify(response.parsed ?? { ok: true }) + '\n');
        if (response.stderr) child.stderr.write(response.stderr);
        finish(response.code || 0);
      } catch (error) {
        child.stderr.write(error.message);
        finish(1);
      }
    });
    return child;
  }
  const context = vm.createContext({
    __dirname, URL, parent, setTimeout, clearTimeout,
    process: {
      platform: process.platform,
      env: { PPTLIB_ENABLE_HTML: enabled ? '1' : '0' },
    },
    require(id) {
      if (id === 'electron') return {
        app,
        ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
        protocol: { registerSchemesAsPrivileged() {} },
        dialog: {
          async showSaveDialog(options) { dialogs.push(options); return saveResult; },
          async showOpenDialog(options) { dialogs.push(options); return { canceled: true }; },
        },
      };
      if (id === 'node:child_process') return { spawn };
      return require(id);
    },
  });
  vm.runInContext(readFileSync(path.join(__dirname, 'main.js'), 'utf8'), context);
  vm.runInContext('mainWindow = parent', context);
  const event = { sender: parent.webContents, senderFrame: parent.webContents.mainFrame };
  return {
    commands, persisted, events, dialogs, handlers, event,
    run: (code) => vm.runInContext(code, context),
    invoke: (channel, value) => handlers.get(channel)(event, value),
    setSaveResult: (result) => { saveResult = result; },
  };
}

test('save picker validates the chosen format, extension, local path and cancellation', async () => {
  const f = mainFixture();
  for (const format of ['pptx', 'html', 'zip']) {
    f.setSaveResult({ filePath: `/tmp/out.${format}` });
    assert.equal(await f.invoke('pick-save-output', { format }), `/tmp/out.${format}`);
    assert.equal(f.dialogs.at(-1).filters[0].extensions[0], format);
    assert.equal(path.extname(f.dialogs.at(-1).defaultPath), `.${format}`);
  }
  f.setSaveResult({ filePath: '/tmp/out.HTML' });
  assert.equal(await f.invoke('pick-save-output', { format: 'html' }), '/tmp/out.HTML');
  f.setSaveResult({ filePath: '/tmp/wrong.pptx' });
  await assert.rejects(f.invoke('pick-save-output', { format: 'html' }), /\.html/);
  f.setSaveResult({ filePath: 'https://example.com/out.html' });
  await assert.rejects(f.invoke('pick-save-output', { format: 'html' }), /本地/);
  f.setSaveResult({ canceled: true });
  assert.equal(await f.invoke('pick-save-output', { format: 'zip' }), null);
  await assert.rejects(f.invoke('pick-save-output', { format: 'exe' }), /不支持/);
  f.setSaveResult({ filePath: '/tmp/legacy.pptx' });
  assert.equal(await f.invoke('pick-output-pptx'), '/tmp/legacy.pptx');
});

test('HTML disabled retains PPTX pick/import/export and rejects online or HTML output IPC', async () => {
  const f = mainFixture({ enabled: false });
  await f.invoke('pick-pptx');
  assert.equal(JSON.stringify(f.dialogs.at(-1).filters[0].extensions), '["pptx"]');
  await f.invoke('pick-save-output', { format: 'pptx' });
  await assert.rejects(f.invoke('pick-save-output', { format: 'html' }), /关闭/);
  await assert.rejects(f.invoke('pick-save-output', { format: 'zip' }), /关闭/);
  for (const source of ['https://example.com/deck', { url: 'https://example.com/deck' }, '/tmp/local.html', '/tmp/bundle.zip']) {
    await assert.rejects(f.invoke('import', [source]), /关闭/);
  }
  await f.invoke('import', ['/tmp/a.pptx', '/tmp/b.pptx']);
  assert.deepEqual(f.commands[0], ['import', '/tmp/a.pptx', '/tmp/b.pptx']);
  await f.invoke('compose-preflight', { manifest: '/tmp/m.json', output: '/tmp/out.pptx' });
  await f.invoke('compose', { manifest: '/tmp/m.json', output: '/tmp/out.pptx' });
  await assert.rejects(f.invoke('compose-preflight', { manifest: '/tmp/m.json', output: '/tmp/out.html' }), /关闭/);
  await assert.rejects(f.invoke('compose', { manifest: '/tmp/m.json', output: '/tmp/out.zip' }), /关闭/);
});

test('mixed sources run sequentially in one durable task and preserve structured failures', async () => {
  let running = 0;
  const f = mainFixture({
    async runCommand(args) {
      running += 1;
      assert.equal(running, 1);
      await nextTurn();
      running -= 1;
      if (args[0] === 'import-url') return {
        code: 1,
        parsed: {
          ok: false, imported: [{ name: 'partial.html', created: true }], skipped: 0,
          failed: [{ path: '/cache/page', error: 'Resource missing' }],
        },
        progress: [{ stage: 'download_resource', url: args[1] }],
      };
      return { parsed: { imported: [{ name: args[1], created: true }], failed: [] } };
    },
  });
  const url = 'https://example.com/a//deck.html?x=1&y=2#slide-3';
  const results = await f.invoke('import', [
    '/tmp/a.pptx', { path: '/tmp/local.html' }, { kind: 'url', url }, '/tmp/bundle.zip',
  ]);
  assert.deepEqual(f.commands, [
    ['import', '/tmp/a.pptx', '/tmp/local.html'],
    ['import-url', url],
    ['import', '/tmp/bundle.zip'],
  ]);
  assert.equal(results.length, 3);
  assert.equal(results[1].imported[0].name, 'partial.html');
  assert.equal(results[1].failed[0].url, url);
  assert.equal(results[1].failed[0].error, 'Resource missing');
  assert.equal(results[2].imported[0].created, true);
  assert.equal(f.persisted[0].state, 'running');
  assert.equal(f.persisted.at(-1).state, 'failed');
  assert.equal(new Set(f.persisted.map((task) => task.taskId)).size, 1);
  assert.equal(f.run('taskCoordinator.hasActive()'), false);
  const progress = f.events.find((event) => event.channel === 'progress').payload;
  assert.equal(progress.stage, 'download_resource');
  assert.equal(progress.taskKind, '导入并渲染');
  assert.equal(progress.taskId, f.persisted[0].taskId);
});

test('URL strings route only to import-url, while invalid sources never reach the CLI', async () => {
  const f = mainFixture();
  await f.invoke('import', ['https://example.com/deck']);
  assert.deepEqual(f.commands, [['import-url', 'https://example.com/deck']]);
  const initial = f.commands.length;
  for (const source of [
    'javascript:alert(1)', 'file:///tmp/deck.html', 'ftp://example.com/a',
    'https://user:pass@example.com/a', 'https://', 'https://example.com\\a',
    { url: '/tmp/not-a-url.html' }, { kind: 'url', path: '/tmp/a' },
    {}, null, '--flag', 'relative.pptx',
  ]) {
    await assert.rejects(f.invoke('import', [source]), undefined, JSON.stringify(source));
  }
  assert.equal(f.commands.length, initial);
});

test('malformed output and command errors stay visible while later sources still run', async () => {
  const f = mainFixture({
    runCommand(args) {
      if (args[0] === 'import-url') return { stdout: 'not JSON\n' };
      throw new Error('Cannot read source');
    },
  });
  const results = await f.invoke('import', [
    'https://example.com/a', '/tmp/b.pptx', 'https://example.com/c',
  ]);
  assert.equal(results.length, 3);
  assert.equal(results.every((result) => result.ok === false && result.failed.length === 1), true);
  assert.match(results[0].failed[0].error, /未返回有效结果/);
  assert.equal(results[1].failed[0].path, '/tmp/b.pptx');
  assert.equal(results[1].failed[0].error, 'Cannot read source');
  assert.equal(results[2].failed[0].url, 'https://example.com/c');
});

test('stop cancels the active command, does not start later sources, and keeps batch results', async () => {
  let release;
  let started;
  const startedPromise = new Promise((resolve) => { started = resolve; });
  const f = mainFixture({
    runCommand() {
      started();
      return new Promise((resolve) => { release = resolve; });
    },
  });
  const pending = f.invoke('import', ['https://example.com/first', '/tmp/later.pptx']);
  await startedPromise;
  await assert.rejects(f.invoke('import', ['/tmp/concurrent.pptx']), /当前正在执行/);
  await f.invoke('scan-run:stop');
  const results = await pending;
  release({ parsed: { ok: true } });
  assert.equal(f.commands.length, 1);
  assert.equal(results.length, 2);
  assert.equal(results[0].ok, false);
  assert.match(results[1].failed[0].error, /任务已停止/);
  assert.equal(f.persisted.at(-1).state, 'interrupted');
});

test('all formats route through existing compose commands and reject mismatched extensions', async () => {
  const f = mainFixture();
  for (const format of ['pptx', 'html', 'zip']) {
    const options = { manifest: '/tmp/mixed.json', output: `/tmp/out.${format}`, format, verifyHash: false };
    await f.invoke('compose-preflight', options);
    await f.invoke('compose', { ...options, preflightToken: 'token' });
    assert.deepEqual(f.commands.at(-2), [
      'compose-preflight', '/tmp/mixed.json', `/tmp/out.${format}`, '--no-verify-hash',
    ]);
    assert.deepEqual(f.commands.at(-1), [
      'compose', '/tmp/mixed.json', `/tmp/out.${format}`, '--no-verify-hash', '--preflight-token', 'token',
    ]);
  }
  const initial = f.commands.length;
  for (const channel of ['compose-preflight', 'compose']) {
    await assert.rejects(f.invoke(channel, { manifest: '/tmp/m.json', output: '/tmp/out.zip', format: 'pptx' }), /\.pptx/);
    await assert.rejects(f.invoke(channel, { manifest: '/tmp/m.json', output: '/tmp/out.htm' }), /不支持/);
  }
  assert.equal(f.commands.length, initial);
});

test('new IPC remains restricted to the main sender and preview labels stay developer-only', async () => {
  const f = mainFixture();
  for (const channel of ['import', 'pick-save-output', 'compose-preflight', 'compose']) {
    await assert.rejects(
      f.handlers.get(channel)({ ...f.event, sender: {} }, {}),
      /不允许/,
    );
  }
  f.run("process.env.PPTLIB_EXPERIMENTAL_LABEL = 'HTML Archive Preview'");
  assert.equal(f.invoke('paths').experimentalLabel, 'HTML Archive Preview');
  f.run('app.isPackaged = true');
  assert.equal(f.invoke('paths').experimentalLabel, '');
});

test('preload forwards selected format and typed sources through named IPC only', async () => {
  let bridge;
  const calls = [];
  vm.runInNewContext(readFileSync(path.join(__dirname, 'preload.js'), 'utf8'), {
    require: () => ({
      contextBridge: { exposeInMainWorld(_name, value) { bridge = value; } },
      ipcRenderer: { invoke: (...args) => { calls.push(args); return Promise.resolve(); } },
    }),
  });
  const source = { kind: 'url', url: 'https://example.com/deck' };
  await bridge.pickSaveOutput({ format: 'html' });
  await bridge.import(['/tmp/a.pptx', source]);
  assert.deepEqual(calls, [
    ['pick-save-output', { format: 'html' }],
    ['import', ['/tmp/a.pptx', source]],
  ]);
  assert.equal(bridge.invoke, undefined);
});
