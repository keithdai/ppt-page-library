'use strict';

const logEl = document.getElementById('log');
const logLastEl = document.getElementById('log-last');
const state = {
  importFiles: [],
  manifest: null,
  output: null,
};

// --- helpers ------------------------------------------------------------- //

function log(text, kind) {
  const line = document.createElement('div');
  if (kind === 'stderr') line.className = 'line-err';
  if (kind === 'ok') line.className = 'line-ok';
  line.textContent = text;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
  if (logLastEl) logLastEl.textContent = text;
}

function busy(button, on) {
  button.disabled = on;
  button.dataset.busy = on ? '1' : '';
}

function show(el, on) {
  if (el) el.hidden = !on;
}

// Show a success/error toast in a step, hiding its sibling.
function toast(okEl, errEl, kind, text) {
  if (kind === 'ok') {
    show(errEl, false);
    if (okEl) { okEl.textContent = text; show(okEl, true); }
  } else {
    show(okEl, false);
    if (errEl) { errEl.textContent = text; show(errEl, true); }
  }
}

// Update a nav step's state dot: 'gray' | 'blue' | 'green'.
function navState(step, kind) {
  const item = document.querySelector(`.nav-item[data-step="${step}"]`);
  if (!item) return;
  const dot = item.querySelector('[data-dot]');
  if (dot) dot.className = `sd ${kind}`;
  item.classList.toggle('done', kind === 'green');
}

function goStep(step) {
  document.querySelectorAll('.nav-item').forEach((n) => {
    n.classList.toggle('active', n.getAttribute('data-step') === String(step));
  });
  document.querySelectorAll('.step').forEach((s) => {
    s.classList.toggle('active', s.getAttribute('data-step') === String(step));
  });
}

window.pptlib.onLog(({ channel, text }) => log(text, channel));

// --- nav + log drawer ---------------------------------------------------- //

document.querySelectorAll('.nav-item').forEach((item) => {
  item.addEventListener('click', () => goStep(item.getAttribute('data-step')));
});

const logbar = document.getElementById('logbar');
document.getElementById('log-toggle').addEventListener('click', (e) => {
  // Let the "clear" button work without toggling the drawer.
  if (e.target.closest('#clear-log')) return;
  logbar.classList.toggle('open');
});
document.getElementById('clear-log').addEventListener('click', (e) => {
  e.stopPropagation();
  logEl.innerHTML = '';
  if (logLastEl) logLastEl.textContent = '';
});

// --- paths / repo root --------------------------------------------------- //

async function refreshPaths() {
  const paths = await window.pptlib.paths();
  document.getElementById('paths').textContent = `仓库: ${paths.repoRoot}`;
  const navRepo = document.getElementById('nav-repo');
  const navHome = document.getElementById('nav-home');
  if (navRepo) navRepo.textContent = paths.repoRoot.split('/').pop() || paths.repoRoot;
  if (navHome) navHome.textContent = paths.home.split('/').slice(-2).join('/');
  const warn = document.getElementById('repo-warning');
  if (warn) warn.style.display = paths.repoRootValid ? 'none' : 'flex';
  return paths;
}
refreshPaths();

const pickRepoBtn = document.getElementById('pick-repo');
if (pickRepoBtn) {
  pickRepoBtn.addEventListener('click', async () => {
    const res = await window.pptlib.pickRepoRoot();
    if (res.ok) {
      log(`已设置仓库目录：${res.repoRoot}`, 'ok');
      await refreshPaths();
    } else if (res.error) {
      log(res.error, 'stderr');
    }
  });
}

// --- Step 1: import + render --------------------------------------------- //

const pickImportBtn = document.getElementById('pick-import');
const runImportBtn = document.getElementById('run-import');
const importList = document.getElementById('import-files');
const FILE_ICON =
  '<span class="fico"><svg class="ico" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></span>';

pickImportBtn.addEventListener('click', async () => {
  const files = await window.pptlib.pickPptx();
  if (files.length) {
    state.importFiles = files;
    importList.innerHTML = '';
    files.forEach((file) => {
      const li = document.createElement('li');
      li.innerHTML = `${FILE_ICON}<div class="fname">${file.split('/').pop()}</div>`;
      importList.appendChild(li);
    });
    runImportBtn.disabled = false;
  }
});

runImportBtn.addEventListener('click', async () => {
  busy(runImportBtn, true);
  show(document.getElementById('import-ok'), false);
  show(document.getElementById('import-err'), false);
  show(document.getElementById('import-prog'), true);
  navState(1, 'blue');
  try {
    log(`开始导入 ${state.importFiles.length} 个文件…`);
    const results = await window.pptlib.import(state.importFiles);
    const imported = results.reduce((sum, r) => sum + ((r.imported || []).length), 0);
    log(`导入完成：新增/更新 ${imported} 个 deck`, 'ok');
    toast(
      document.getElementById('import-ok'),
      document.getElementById('import-err'),
      'ok',
      `导入完成：新增 / 更新 ${imported} 个 deck，缩略图与预览已渲染`
    );
    navState(1, 'green');
  } catch (error) {
    log(`导入失败：${error.message}`, 'stderr');
    toast(
      document.getElementById('import-ok'),
      document.getElementById('import-err'),
      'err',
      `导入失败：${error.message}`
    );
    navState(1, 'gray');
  } finally {
    show(document.getElementById('import-prog'), false);
    busy(runImportBtn, false);
  }
});

// --- Step 2: catalog + sync ---------------------------------------------- //

const runCatalogBtn = document.getElementById('run-catalog');
const runSyncBtn = document.getElementById('run-sync');

runCatalogBtn.addEventListener('click', async () => {
  busy(runCatalogBtn, true);
  try {
    const res = await window.pptlib.catalog(null);
    log(`已导出 catalog：${res.slide_count} 页 → ${res.catalog_path}`, 'ok');
    toast(
      document.getElementById('sync-ok'),
      document.getElementById('sync-err'),
      'ok',
      `已导出目录：${res.slide_count} 页 → ${res.catalog_path}`
    );
  } catch (error) {
    log(`导出 catalog 失败：${error.message}`, 'stderr');
    toast(
      document.getElementById('sync-ok'),
      document.getElementById('sync-err'),
      'err',
      `导出目录失败：${error.message}`
    );
  } finally {
    busy(runCatalogBtn, false);
  }
});

runSyncBtn.addEventListener('click', async () => {
  const appId = document.getElementById('app-id').value.trim();
  if (!appId) {
    toast(
      document.getElementById('sync-ok'),
      document.getElementById('sync-err'),
      'err',
      '请先填写妙搭 App ID'
    );
    log('请先填写妙搭 App ID', 'stderr');
    return;
  }
  const environment = document.getElementById('environment').value;
  const dryRun = document.getElementById('dry-run').checked;
  busy(runSyncBtn, true);
  show(document.getElementById('sync-ok'), false);
  show(document.getElementById('sync-err'), false);
  show(document.getElementById('sync-prog'), true);
  navState(2, 'blue');
  try {
    log(`${dryRun ? '[预演] ' : ''}同步到 ${appId} (${environment})…`);
    const res = await window.pptlib.sync({ appId, environment, dryRun });
    log(
      `同步完成：${res.slide_count} 页，缩略图 ${res.thumbnails_uploaded}，元数据 ${res.rows_upserted}`,
      'ok'
    );
    toast(
      document.getElementById('sync-ok'),
      document.getElementById('sync-err'),
      'ok',
      `${dryRun ? '[预演] ' : ''}同步完成：${res.slide_count} 页，缩略图 ${res.thumbnails_uploaded}，元数据 ${res.rows_upserted}`
    );
    navState(2, 'green');
  } catch (error) {
    log(`同步失败：${error.message}`, 'stderr');
    toast(
      document.getElementById('sync-ok'),
      document.getElementById('sync-err'),
      'err',
      `同步失败：${error.message}`
    );
    navState(2, 'gray');
  } finally {
    show(document.getElementById('sync-prog'), false);
    busy(runSyncBtn, false);
  }
});

const openEditBtn = document.getElementById('open-miaoda-edit');
if (openEditBtn) {
  openEditBtn.addEventListener('click', () => {
    const appId = document.getElementById('app-id').value.trim();
    if (appId) window.pptlib.openExternal(`https://miaoda.feishu.cn/app/${appId}`);
  });
}

// --- Step 3: in-app selection via embedded Miaoda webview ---------------- //

const miaodaWrap = document.getElementById('miaoda-wrap');
let miaodaView = null;

async function setupMiaoda() {
  const cfg = await window.pptlib.miaodaConfig();
  // Keep step 2's sync target aligned with the embedded app by default.
  const appIdInput = document.getElementById('app-id');
  if (appIdInput && !appIdInput.value) appIdInput.value = cfg.appId;

  const view = document.createElement('webview');
  view.setAttribute('src', cfg.url);
  view.setAttribute('partition', cfg.partition);
  view.setAttribute('allowpopups', '');
  miaodaWrap.appendChild(view);
  miaodaView = view;

  const urlEl = document.getElementById('miaoda-url');
  urlEl.textContent = cfg.url;
  view.addEventListener('did-navigate', (e) => (urlEl.textContent = e.url));
  view.addEventListener('did-navigate-in-page', (e) => (urlEl.textContent = e.url));

  document.getElementById('miaoda-back').addEventListener('click', () => {
    if (miaodaView && miaodaView.canGoBack()) miaodaView.goBack();
  });
  document.getElementById('miaoda-reload').addEventListener('click', () => {
    if (miaodaView) miaodaView.reload();
  });
  document.getElementById('miaoda-open-browser').addEventListener('click', () => {
    if (miaodaView) window.pptlib.openExternal(miaodaView.getURL());
  });
}
setupMiaoda();

// A manifest downloaded inside the Miaoda view is captured in main and pushed
// here — arm compose without making the user re-pick a file.
window.pptlib.onMiaodaManifest((payload) => {
  if (payload.ok) {
    state.manifest = payload.path;
    const label = payload.filename || payload.path;
    log(`已接住妙搭选片清单：${label}`, 'ok');
    toast(
      document.getElementById('manifest-ok'),
      document.getElementById('manifest-err'),
      'ok',
      `已接住妙搭选片清单：${label}`
    );
    navState(3, 'green');
    refreshComposeReady();
    goStep(4);
  } else {
    log(payload.error || '接收妙搭清单失败', 'stderr');
    toast(
      document.getElementById('manifest-ok'),
      document.getElementById('manifest-err'),
      'err',
      payload.error || '接收妙搭清单失败'
    );
  }
});

// Fallback: paste an ordered slide_id list → write a manifest locally.
document.getElementById('use-slide-ids').addEventListener('click', async () => {
  const text = document.getElementById('slide-ids').value;
  const ids = text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (ids.length === 0) {
    log('请先粘贴至少一个 slide_id', 'stderr');
    return;
  }
  try {
    const res = await window.pptlib.writeManifest(ids);
    state.manifest = res.path;
    log(`已按 ${res.count} 个 slide_id 写出清单 → ${res.path}`, 'ok');
    toast(
      document.getElementById('manifest-ok'),
      document.getElementById('manifest-err'),
      'ok',
      `已按 ${res.count} 个 slide_id 写出清单，进入组合导出`
    );
    navState(3, 'green');
    refreshComposeReady();
    goStep(4);
  } catch (error) {
    log(`写出清单失败：${error.message}`, 'stderr');
  }
});

// --- Step 4: compose ----------------------------------------------------- //

const pickManifestBtn = document.getElementById('pick-manifest');
const pickOutputBtn = document.getElementById('pick-output');
const runComposeBtn = document.getElementById('run-compose');
const composeChosen = document.getElementById('compose-chosen');

function refreshComposeReady() {
  runComposeBtn.disabled = !(state.manifest && state.output);
  composeChosen.textContent = [
    state.manifest ? `manifest: ${state.manifest}` : '',
    state.output ? `输出: ${state.output}` : '',
  ]
    .filter(Boolean)
    .join('\n');
}

pickManifestBtn.addEventListener('click', async () => {
  const manifest = await window.pptlib.pickManifest();
  if (manifest) {
    state.manifest = manifest;
    refreshComposeReady();
  }
});

pickOutputBtn.addEventListener('click', async () => {
  const output = await window.pptlib.pickOutputPptx();
  if (output) {
    state.output = output;
    refreshComposeReady();
  }
});

const verifyHashEl = document.getElementById('verify-hash');
const sideVerifyEl = document.getElementById('side-verify');
if (verifyHashEl && sideVerifyEl) {
  verifyHashEl.addEventListener('change', () => {
    sideVerifyEl.textContent = verifyHashEl.checked ? '开启' : '关闭';
  });
}

runComposeBtn.addEventListener('click', async () => {
  const verifyHash = verifyHashEl.checked;
  busy(runComposeBtn, true);
  show(document.getElementById('compose-ok'), false);
  show(document.getElementById('compose-err'), false);
  show(document.getElementById('compose-prog'), true);
  navState(4, 'blue');
  try {
    log('开始组合…');
    const res = await window.pptlib.compose({
      manifest: state.manifest,
      output: state.output,
      verifyHash,
    });
    log(`组合完成：${res.page_count} 页 (保真 ${res.fidelity_level}) → ${res.output_path}`, 'ok');
    toast(
      document.getElementById('compose-ok'),
      document.getElementById('compose-err'),
      'ok',
      `组合完成：${res.page_count} 页（保真 ${res.fidelity_level}） → ${res.output_path}`
    );
    navState(4, 'green');
    window.pptlib.reveal(res.output_path);
  } catch (error) {
    log(`组合失败：${error.message}`, 'stderr');
    toast(
      document.getElementById('compose-ok'),
      document.getElementById('compose-err'),
      'err',
      `组合失败：${error.message}`
    );
    navState(4, 'gray');
  } finally {
    show(document.getElementById('compose-prog'), false);
    busy(runComposeBtn, false);
  }
});
