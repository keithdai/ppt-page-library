'use strict';

const logEl = document.getElementById('log');
const state = {
  importFiles: [],
  manifest: null,
  output: null,
};

function log(text, kind) {
  const line = document.createElement('div');
  if (kind === 'stderr') line.className = 'line-err';
  if (kind === 'ok') line.className = 'line-ok';
  line.textContent = text;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function busy(button, on) {
  button.disabled = on;
  button.dataset.label = button.dataset.label || button.textContent;
  button.textContent = on ? '运行中…' : button.dataset.label;
}

window.pptlib.onLog(({ channel, text }) => log(text, channel));

window.pptlib.paths().then((paths) => {
  document.getElementById('paths').textContent = `HOME: ${paths.home}  ·  CLI: ${paths.pptlib}`;
});

// Step 1: import + render
const pickImportBtn = document.getElementById('pick-import');
const runImportBtn = document.getElementById('run-import');
const importList = document.getElementById('import-files');

pickImportBtn.addEventListener('click', async () => {
  const files = await window.pptlib.pickPptx();
  if (files.length) {
    state.importFiles = files;
    importList.innerHTML = '';
    files.forEach((file) => {
      const li = document.createElement('li');
      li.textContent = file;
      importList.appendChild(li);
    });
    runImportBtn.disabled = false;
  }
});

runImportBtn.addEventListener('click', async () => {
  busy(runImportBtn, true);
  try {
    log(`开始导入 ${state.importFiles.length} 个文件…`);
    const results = await window.pptlib.import(state.importFiles);
    const imported = results.reduce((sum, r) => sum + ((r.imported || []).length), 0);
    log(`导入完成：新增/更新 ${imported} 个 deck`, 'ok');
  } catch (error) {
    log(`导入失败：${error.message}`, 'stderr');
  } finally {
    busy(runImportBtn, false);
  }
});

// Step 2: catalog + sync
const runCatalogBtn = document.getElementById('run-catalog');
const runSyncBtn = document.getElementById('run-sync');

runCatalogBtn.addEventListener('click', async () => {
  busy(runCatalogBtn, true);
  try {
    const res = await window.pptlib.catalog(null);
    log(`已导出 catalog：${res.slide_count} 页 → ${res.catalog_path}`, 'ok');
  } catch (error) {
    log(`导出 catalog 失败：${error.message}`, 'stderr');
  } finally {
    busy(runCatalogBtn, false);
  }
});

runSyncBtn.addEventListener('click', async () => {
  const appId = document.getElementById('app-id').value.trim();
  if (!appId) {
    log('请先填写妙搭 App ID', 'stderr');
    return;
  }
  const environment = document.getElementById('environment').value;
  const dryRun = document.getElementById('dry-run').checked;
  busy(runSyncBtn, true);
  try {
    log(`${dryRun ? '[干跑] ' : ''}同步到 ${appId} (${environment})…`);
    const res = await window.pptlib.sync({ appId, environment, dryRun });
    log(
      `同步完成：${res.slide_count} 页，缩略图 ${res.thumbnails_uploaded}，元数据 ${res.rows_upserted}`,
      'ok'
    );
  } catch (error) {
    log(`同步失败：${error.message}`, 'stderr');
  } finally {
    busy(runSyncBtn, false);
  }
});

// Step 3: compose
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

runComposeBtn.addEventListener('click', async () => {
  const verifyHash = document.getElementById('verify-hash').checked;
  busy(runComposeBtn, true);
  try {
    log('开始组合…');
    const res = await window.pptlib.compose({
      manifest: state.manifest,
      output: state.output,
      verifyHash,
    });
    log(`组合完成：${res.page_count} 页 (保真 ${res.fidelity_level}) → ${res.output_path}`, 'ok');
    window.pptlib.reveal(res.output_path);
  } catch (error) {
    log(`组合失败：${error.message}`, 'stderr');
  } finally {
    busy(runComposeBtn, false);
  }
});

document.getElementById('clear-log').addEventListener('click', () => {
  logEl.innerHTML = '';
});
