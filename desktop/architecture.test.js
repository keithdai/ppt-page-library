'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  assetUrl,
  createAssetProtocolHandler,
  normalizeAssetPath,
  resolveAsset,
} = require('./security');
const {
  planIsDue,
  planWasMissed,
  scheduledOccurrence,
  timeInWindow,
} = require('./scheduler');
const { createTaskCoordinator } = require('./task-coordinator');

test('asset protocol accepts only images below the managed assets directory', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pinpage-assets-'));
  const assets = path.join(root, 'assets');
  const thumbnails = path.join(assets, 'thumbnails');
  fs.mkdirSync(thumbnails, { recursive: true });
  const image = path.join(thumbnails, 'slide_1.jpg');
  fs.writeFileSync(image, 'jpg');
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  assert.equal(normalizeAssetPath('/assets/thumbnails/slide_1.jpg'), 'thumbnails/slide_1.jpg');
  assert.equal(resolveAsset(assets, '/thumbnails/slide_1.jpg'), fs.realpathSync(image));
  assert.match(assetUrl(assets, '/assets/thumbnails/slide_1.jpg'), /^pptlib-asset:\/\/local\//);

  for (const candidate of [
    '../secret.jpg',
    '/assets/../secret.jpg',
    '/assets/thumbnails/not-image.txt',
    '/assets/private/slide_1.jpg',
    '/tmp/slide_1.jpg',
    '/assets/thumbnails/%2e%2e%2fsecret.jpg',
  ]) {
    assert.equal(resolveAsset(assets, candidate), null, candidate);
  }

  const outside = path.join(root, 'outside.jpg');
  const linked = path.join(thumbnails, 'linked.jpg');
  fs.writeFileSync(outside, 'private');
  fs.symlinkSync(outside, linked);
  assert.equal(resolveAsset(assets, '/assets/thumbnails/linked.jpg'), null);

  const handler = createAssetProtocolHandler(() => assets);
  const allowed = await handler({ url: 'pptlib-asset://local/thumbnails/slide_1.jpg' });
  assert.equal(allowed.status, 200);
  assert.equal(allowed.headers.get('content-type'), 'image/jpeg');
  const denied = await handler({ url: 'pptlib-asset://other/thumbnails/slide_1.jpg' });
  assert.equal(denied.status, 404);
});

test('task coordinator persists lifecycle and serializes heavy work', async () => {
  const states = [];
  const persisted = [];
  const child = { exitCode: null, killed: false, kill() { this.killed = true; } };
  const coordinator = createTaskCoordinator({
    async runCommand(_args, _webContents, hooks) {
      hooks.onSpawn(child);
      hooks.onProgress({ current: 1, total: 2 });
      return { parsed: { ok: true } };
    },
    onState(task) {
      states.push(task);
    },
    async persistTask(task) {
      persisted.push(task);
    },
  });

  const result = await coordinator.run('导入并渲染', ['import'], null);
  assert.deepEqual(result.parsed, { ok: true });
  assert.equal(coordinator.hasActive(), false);
  assert.equal(states[0].state, 'running');
  assert.equal(states.at(-1), null);
  assert.equal(persisted[0].state, 'running');
  assert.equal(persisted.at(-1).state, 'finished');
});

test('task coordinator does not start work when durable start state cannot be written', async () => {
  let commands = 0;
  const coordinator = createTaskCoordinator({
    async runCommand() {
      commands += 1;
      return { parsed: { ok: true } };
    },
    onState() {},
    async persistTask() {
      throw new Error('database unavailable');
    },
  });

  await assert.rejects(
    coordinator.run('导入并渲染', ['import'], null),
    /database unavailable/,
  );
  assert.equal(commands, 0);
  assert.equal(coordinator.hasActive(), false);
});

test('scheduler handles normal and overnight execution windows', () => {
  assert.equal(timeInWindow(9 * 60, '08:00', '10:00'), true);
  assert.equal(timeInWindow(23 * 60, '22:00', '02:00'), true);
  assert.equal(timeInWindow(12 * 60, '22:00', '02:00'), false);

  const plan = {
    id: 'plan_1',
    enabled: true,
    scheduleKind: 'daily',
    scheduleTime: '09:00',
    windowStart: '08:00',
    windowEnd: '10:00',
  };
  const now = new Date(2026, 9, 6, 9, 30);
  assert.equal(scheduledOccurrence(plan, now).getHours(), 9);
  assert.equal(planIsDue(plan, [], now), true);
  assert.equal(planIsDue(plan, [{
    planId: plan.id,
    triggerType: 'scheduled',
    scheduledFor: new Date(2026, 9, 6, 9, 0).toISOString(),
  }], now), false);
  assert.equal(planWasMissed(plan, [], new Date(2026, 9, 6, 11, 0)), true);
});
