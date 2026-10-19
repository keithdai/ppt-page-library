'use strict';

const logEl = document.getElementById('log');
const logLastEl = document.getElementById('log-last');
const state = {
  importSources: [],
  manifest: null,
  output: null,
  outputFormat: 'pptx',
  htmlEnabled: false,
  composePreflight: null,
  composeResult: null,
  manifestFromSelection: false,
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
  let activeSection = null;
  document.querySelectorAll('.nav-item').forEach((n) => {
    const active = n.getAttribute('data-step') === String(step);
    n.classList.toggle('active', active);
    if (active) n.setAttribute('aria-current', 'step');
    else n.removeAttribute('aria-current');
  });
  document.querySelectorAll('.step').forEach((s) => {
    const active = s.getAttribute('data-step') === String(step);
    s.classList.toggle('active', active);
    if (active) activeSection = s;
  });
  requestAnimationFrame(() => {
    const heading = activeSection?.querySelector('h2');
    if (heading) {
      heading.tabIndex = -1;
      heading.focus();
    }
  });
}

window.pptlib.onLog(({ channel, text }) => log(text, channel));

// --- nav + log drawer ---------------------------------------------------- //

document.querySelectorAll('.nav-item').forEach((item) => {
  item.addEventListener('click', () => {
    const step = item.getAttribute('data-step');
    goStep(step);
    if (step === '4' && !duplicateState.report && !duplicateState.loading) {
      scanDuplicates(false);
    }
  });
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
  applyHtmlFeature(paths.htmlEnabled === true);
  const label = document.getElementById('experimental-label');
  label.textContent = paths.experimentalLabel || '';
  show(label, Boolean(paths.experimentalLabel));
  document.title = paths.experimentalLabel
    ? `DeckAtlas · ${paths.experimentalLabel}` : 'DeckAtlas · 本地演示页库';
  document.getElementById('paths').textContent = paths.runtimeMode === 'bundled'
    ? '运行时: 应用内置'
    : `仓库: ${paths.repoRoot}`;
  const navRepo = document.getElementById('nav-repo');
  const navHome = document.getElementById('nav-home');
  if (navRepo) navRepo.textContent = paths.repoRoot.split('/').pop() || paths.repoRoot;
  if (navHome) navHome.textContent = paths.home.split('/').slice(-2).join('/');
  const warn = document.getElementById('repo-warning');
  if (warn) warn.style.display = paths.repoRootValid ? 'none' : 'flex';
  return paths;
}
refreshPaths();

function applyHtmlFeature(enabled) {
  state.htmlEnabled = enabled;
  document.querySelectorAll('[data-html-only]').forEach((el) => show(el, enabled));
  document.querySelectorAll('[data-html-copy]').forEach((el) => {
    if (!el.dataset.pptxCopy) el.dataset.pptxCopy = el.textContent;
    el.textContent = enabled ? el.dataset.htmlCopy : el.dataset.pptxCopy;
  });
  if (!enabled) {
    document.getElementById('filter-format').value = '';
    if (state.outputFormat !== 'pptx') {
      state.outputFormat = 'pptx';
      state.output = null;
      refreshComposeReady();
    }
  }
  document.getElementById('output-format').value = state.outputFormat;
  updateComposeCopy();
}

const onboardingEl = document.getElementById('onboarding');
const onboardingChecksEl = document.getElementById('onboarding-checks');
const onboardingSourceBtn = document.getElementById('onboarding-source');
const onboardingRecheckBtn = document.getElementById('onboarding-recheck');
let onboardingChecking = false;

function renderOnboardingChecks(report) {
  const checks = [
    {
      label: '运行环境',
      ok: report.sqlite?.ok && report.fts5?.ok,
      detail: report.sqlite?.ok && report.fts5?.ok ? 'SQLite 与搜索可用' : '运行时不可用',
    },
    {
      label: '预览渲染',
      ok: report.libreoffice?.ok && report.pdftoppm?.ok,
      detail: report.libreoffice?.ok && report.pdftoppm?.ok
        ? 'LibreOffice 已就绪'
        : '缺少 LibreOffice 或 PDF 渲染器',
    },
    {
      label: '本地数据目录',
      ok: report.data_directory?.ok && report.output_directory?.ok,
      detail: report.data_directory?.ok && report.output_directory?.ok
        ? '可写入'
        : '目录不可写',
    },
  ];
  onboardingChecksEl.innerHTML = checks
    .map((item) =>
      `<div class="${item.ok ? 'ok' : 'error'}"><span>${item.label}</span>` +
      `<strong>${esc(item.detail)}</strong></div>`)
    .join('');
  const ready = checks.every((item) => item.ok);
  onboardingSourceBtn.disabled = !ready;
  const errorEl = document.getElementById('onboarding-error');
  errorEl.textContent = ready
    ? ''
    : '环境尚未就绪。安装 LibreOffice、检查目录权限后，再重新检查。';
  show(errorEl, !ready);
}

async function checkOnboardingEnvironment() {
  if (onboardingChecking) return;
  onboardingChecking = true;
  busy(onboardingRecheckBtn, true);
  onboardingRecheckBtn.textContent = '正在检查…';
  try {
    const report = await window.pptlib.doctor();
    renderOnboardingChecks(report || {});
  } catch (error) {
    onboardingSourceBtn.disabled = true;
    const errorEl = document.getElementById('onboarding-error');
    errorEl.textContent = `环境检查失败：${error.message}`;
    show(errorEl, true);
  } finally {
    onboardingChecking = false;
    busy(onboardingRecheckBtn, false);
    onboardingRecheckBtn.textContent = '重新检查';
  }
}

function maybeStartOnboarding(slideCount) {
  if (!onboardingEl || typeof onboardingEl.showModal !== 'function') return;
  if (slideCount > 0) {
    localStorage.setItem('pinpage.onboarding.completed', '1');
    return;
  }
  if (
    localStorage.getItem('pinpage.onboarding.completed') === '1' ||
    sessionStorage.getItem('pinpage.onboarding.dismissed') === '1' ||
    onboardingEl.open
  ) return;
  onboardingEl.showModal();
  checkOnboardingEnvironment();
}

onboardingRecheckBtn?.addEventListener('click', checkOnboardingEnvironment);
document.getElementById('onboarding-skip')?.addEventListener('click', () => {
  sessionStorage.setItem('pinpage.onboarding.dismissed', '1');
  onboardingEl.close();
});
onboardingSourceBtn?.addEventListener('click', async () => {
  try {
    const folders = await window.pptlib.pickFolder();
    if (!folders.length) return;
    addImportSources(folders, true);
    onboardingEl.close();
    goStep(1);
    requestAnimationFrame(() => runImportBtn.focus());
  } catch (error) {
    const errorEl = document.getElementById('onboarding-error');
    errorEl.textContent = `选择文件夹失败：${error.message}`;
    show(errorEl, true);
  }
});

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
const pickFolderBtn = document.getElementById('pick-folder');
const runImportBtn = document.getElementById('run-import');
const importList = document.getElementById('import-files');
const importProgEl = document.getElementById('import-prog');
const importBarEl = document.getElementById('import-bar');
const importProgTextEl = document.getElementById('import-prog-text');
const importSummaryEl = document.getElementById('import-summary');
const importSelectionHeadEl = document.getElementById('import-selection-head');
const importSourceCountEl = document.getElementById('import-source-count');
const importSelectionNoteEl = document.getElementById('import-selection-note');
const clearImportSourcesBtn = document.getElementById('clear-import-sources');
const importUrlEl = document.getElementById('import-url');
const addImportUrlBtn = document.getElementById('add-import-url');
const importUrlErrorEl = document.getElementById('import-url-error');
let importRunning = false;
const FILE_ICON =
  '<span class="fico"><svg class="ico" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></span>';
const FOLDER_ICON =
  '<span class="fico"><svg class="ico" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></span>';
const SOURCE_REMOVE_ICON =
  '<svg class="ico sm" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>';
const URL_ICON =
  '<span class="fico"><svg class="ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a18 18 0 0 1 0 18 18 18 0 0 1 0-18"/></svg></span>';

// URL sources deliberately have no path: they must never receive disk semantics.
function importSourceKey(source) {
  return source.url ? `url:${source.url}` : `path:${normalizedPath(source.path)}`;
}

function normalizeImportUrl(value) {
  const text = String(value || '').trim();
  if (!/^https?:\/\//i.test(text) || /[\s\\]/.test(text)) {
    throw new Error('请输入完整的 http:// 或 https:// 链接');
  }
  let url;
  try {
    url = new URL(text);
  } catch {
    throw new Error('链接格式无效，请检查后重试');
  }
  if (!url.hostname || url.username || url.password) {
    throw new Error('在线链接不能包含用户名或密码');
  }
  return url.href;
}

// Local files and directories retain their existing source representation.
function mergeImportSources(current, paths, isDir) {
  const sources = current.slice();
  const known = new Set(sources.map(importSourceKey));
  let added = 0;
  let duplicates = 0;
  for (const rawPath of paths) {
    const path = String(rawPath || '').trim();
    const key = importSourceKey({ path });
    if (!path || known.has(key)) {
      if (path) duplicates += 1;
      continue;
    }
    known.add(key);
    sources.push({ path, isDir });
    added += 1;
  }
  return { sources, added, duplicates };
}

function withoutImportSource(current, path) {
  const key = typeof path === 'object' ? importSourceKey(path) : importSourceKey({ path });
  return current.filter((source) => importSourceKey(source) !== key);
}

function importSourceType(source) {
  if (source.url) return '在线链接';
  if (source.isDir) return '文件夹';
  const extension = source.path.split('.').pop().toLowerCase();
  if (extension === 'htm' || extension === 'html') return 'HTML';
  return extension === 'pptx' || extension === 'zip' ? extension.toUpperCase() : '文件';
}

function resetImportOutcome() {
  show(document.getElementById('import-ok'), false);
  show(document.getElementById('import-err'), false);
  show(importProgEl, false);
  importSummaryEl.hidden = true;
  importBarEl.style.width = '0%';
  importProgTextEl.textContent = '准备中…';
  navState(1, 'gray');
}

function announceImportSelection(text, kind = '') {
  importSelectionNoteEl.textContent = text;
  importSelectionNoteEl.className = `import-selection-note${kind ? ` ${kind}` : ''}`;
  show(importSelectionNoteEl, Boolean(text));
}

function setImportBusy(on) {
  importRunning = on;
  pickImportBtn.disabled = on;
  pickFolderBtn.disabled = on;
  importUrlEl.disabled = on;
  addImportUrlBtn.disabled = on;
  clearImportSourcesBtn.disabled = on;
  renderImportSources();
}

function renderImportSources() {
  importList.innerHTML = '';
  state.importSources.forEach((src) => {
    const li = document.createElement('li');
    const icon = src.url ? URL_ICON : src.isDir ? FOLDER_ICON : FILE_ICON;
    const location = src.url || src.path;
    const label = src.url ? new URL(src.url).host : src.path.split('/').pop() || src.path;
    li.title = location;
    li.innerHTML =
      `${icon}<div class="fcopy"><div class="fname">${esc(label)}</div>` +
      `<div class="fpath">${esc(location)}</div></div>` +
      `<span class="ftag">${importSourceType(src)}</span>` +
      `<button class="source-remove" type="button" title="移除" ` +
      `aria-label="移除 ${esc(label)}"${importRunning ? ' disabled' : ''}>` +
      `${SOURCE_REMOVE_ICON}</button>`;
    li.querySelector('.source-remove').addEventListener('click', () => {
      if (importRunning) return;
      state.importSources = withoutImportSource(state.importSources, src);
      resetImportOutcome();
      renderImportSources();
      announceImportSelection(`已移除“${label}”`);
    });
    importList.appendChild(li);
  });
  const count = state.importSources.length;
  importSourceCountEl.textContent = count;
  show(importSelectionHeadEl, count > 0);
  runImportBtn.disabled = importRunning || count === 0;
  clearImportSourcesBtn.disabled = importRunning;
}

function addImportSources(paths, isDir) {
  const result = mergeImportSources(state.importSources, paths, isDir);
  state.importSources = result.sources;
  if (result.added) resetImportOutcome();
  renderImportSources();
  if (result.added && result.duplicates) {
    announceImportSelection(`已添加 ${result.added} 个来源，忽略 ${result.duplicates} 个重复项`);
  } else if (result.added) {
    announceImportSelection(`已添加 ${result.added} 个来源`, 'ok');
  } else if (result.duplicates) {
    announceImportSelection('所选来源已在列表中，无需重复添加');
  }
}

pickImportBtn.addEventListener('click', async () => {
  const files = await window.pptlib.pickPptx();
  if (files.length) addImportSources(files, false);
});

pickFolderBtn.addEventListener('click', async () => {
  const folders = await window.pptlib.pickFolder();
  if (folders.length) addImportSources(folders, true);
});

document.getElementById('import-url-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if (importRunning || !state.htmlEnabled) return;
  show(importUrlErrorEl, false);
  try {
    const source = { kind: 'url', url: normalizeImportUrl(importUrlEl.value) };
    if (state.importSources.some((item) => importSourceKey(item) === importSourceKey(source))) {
      announceImportSelection('所选链接已在列表中，无需重复添加');
      return;
    }
    state.importSources.push(source);
    resetImportOutcome();
    renderImportSources();
    importUrlEl.value = '';
    announceImportSelection('已添加 1 个在线来源，点击“导入并渲染”开始归档', 'ok');
  } catch (error) {
    importUrlErrorEl.textContent = error.message;
    show(importUrlErrorEl, true);
  }
  importUrlEl.focus();
});

clearImportSourcesBtn.addEventListener('click', () => {
  if (importRunning || state.importSources.length === 0) return;
  const count = state.importSources.length;
  state.importSources = [];
  resetImportOutcome();
  renderImportSources();
  announceImportSelection(`已清空 ${count} 个待导入来源`);
});

// Live import progress, driven by the backend's per-page/per-file events.
function setImportProgress(event) {
  if (!event || !importProgEl) return;
  if (event.taskKind && event.taskKind !== '导入并渲染') return;
  const { stage, index, total, name, page, pages } = event;
  let ratio = 0;
  let text = '准备中…';
  if (stage?.startsWith('download_')) {
    const location = event.url || event.source_url || event.path || name || '在线演示';
    const downloadCopy = {
      download_start: '正在连接在线演示',
      download_resource: '正在下载页面资源',
      download_done: '下载完成，准备归档',
      download_complete: '下载完成，准备归档',
      download_error: '下载失败',
    };
    text = `${downloadCopy[stage] || '正在归档在线演示'}：${location}`;
    if (stage === 'download_resource' && Number.isFinite(event.resource_count)) {
      text = `正在下载页面资源：已保存 ${event.resource_count} 项`;
      if (event.bytes > 0) text += ` · ${formatBytes(event.bytes)}`;
    }
    if (event.error) text += ` · ${event.error}`;
  } else if (stage === 'scan') {
    text = total > 0 ? `发现 ${total} 个文件，开始导入…` : '未发现可导入的文件';
  } else if (stage === 'file') {
    ratio = total ? (index - 1) / total : 0;
    text = `(${index}/${total}) 正在读取：${name}`;
  } else if (stage === 'parse') {
    ratio = total ? (index - 1 + 0.15) / total : 0;
    text = `(${index}/${total}) 已解析文本，开始渲染：${name}`;
  } else if (stage === 'render') {
    // page progress within a file contributes to that file's slice of the bar
    const within = pages ? page / pages : 0;
    ratio = total ? (index - 1 + 0.15 + 0.85 * within) / total : 0;
    text = `(${index}/${total}) ${name}：渲染第 ${page}/${pages} 页`;
  } else if (stage === 'file_done') {
    ratio = total ? index / total : 0;
    let suffix = '';
    if (event.action === 'updated') {
      suffix = '（已更新）';
    } else if (event.action === 'moved') {
      suffix = '（内容未变，路径已同步）';
    } else if (event.action === 'unchanged') {
      suffix = '（未变化，跳过）';
    }
    text = `(${index}/${total}) 完成：${name}${suffix}`;
  } else if (stage === 'error') {
    ratio = total ? index / total : 0;
    text = `(${index}/${total}) 失败：${name}`;
    log(`${name} 导入失败：${event.error || '未知错误'}`, 'stderr');
  }
  importBarEl.style.width = `${Math.round(Math.min(1, Math.max(0, ratio)) * 100)}%`;
  importProgTextEl.textContent = text;
}
window.pptlib.onProgress(setImportProgress);

function summarizeImportResults(results) {
  const imported = results.flatMap((result) => result.imported || []);
  return {
    created: imported.filter((deck) => deck.action === 'created' || (!deck.action && deck.created)),
    updated: imported.filter((deck) => deck.action === 'updated'),
    moved: imported.filter((deck) => deck.action === 'moved'),
    unchanged: imported.filter(
      (deck) => deck.action === 'unchanged' || (!deck.action && !deck.created)
    ),
    removed: results.flatMap((result) => result.removed || []),
    skipped: results.flatMap((result) => Array.isArray(result.skipped) ? result.skipped : []),
    failed: results.flatMap((result) => {
      if (result.failed?.length) return result.failed;
      return result.ok === false ? [{ error: result.message || '导入失败，请查看运行日志' }] : [];
    }),
  };
}

// Render the post-import summary grouped by sync outcome.
function renderImportSummary(results) {
  const { created, updated, moved, unchanged, removed, skipped, failed } = summarizeImportResults(results);
  const rows = [];
  const notices = results.flatMap((result) => result.warnings || []);
  for (const notice of notices) {
    rows.push(`<li class="dup">${esc(String(notice))}</li>`);
  }
  rows.push(`<li class="ok">新增导入 <b>${created.length}</b> 个文件</li>`);
  if (updated.length) {
    const names = updated.map((d) => esc(d.name || (d.path || '').split('/').pop())).join('、');
    rows.push(`<li class="ok">内容变化、已更新 <b>${updated.length}</b> 个：${names}</li>`);
  }
  if (moved.length) {
    const names = moved.map((d) => esc(d.name || (d.path || '').split('/').pop())).join('、');
    rows.push(`<li class="ok">内容未变、路径已同步 <b>${moved.length}</b> 个：${names}</li>`);
  }
  if (unchanged.length) {
    const names = unchanged.map((d) => esc(d.name || (d.path || '').split('/').pop())).join('、');
    rows.push(`<li class="dup">已存在、自动跳过 <b>${unchanged.length}</b> 个：${names}</li>`);
  }
  if (removed.length) {
    const names = removed.map((path) => esc(String(path).split('/').pop())).join('、');
    rows.push(`<li class="dup">源文件已不存在、同步移除 <b>${removed.length}</b> 个：${names}</li>`);
  }
  if (skipped.length) {
    const names = skipped.map((item) => esc(typeof item === 'string'
      ? item : item.url || item.path || item.name || item.reason || '已跳过')).join('、');
    rows.push(`<li class="dup">其他跳过 <b>${skipped.length}</b> 个：${names}</li>`);
  }
  if (failed.length) {
    const details = failed
      .map((item) => {
        const location = item.url || item.source_url || item.path || '';
        const name = esc(item.url || item.source_url || item.name || location.split('/').pop() || '导入来源');
        const reason = esc(item.error || '未知错误');
        return `<div class="import-failure"><strong title="${esc(location)}">${name}</strong><span>${reason}</span></div>`;
      })
      .join('');
    rows.push(`<li class="err">失败 <b>${failed.length}</b> 个${details}</li>`);
  }
  importSummaryEl.innerHTML = rows.join('');
  importSummaryEl.hidden = false;
}

runImportBtn.addEventListener('click', async () => {
  if (importRunning || state.importSources.length === 0) return;
  setImportBusy(true);
  show(importSelectionNoteEl, false);
  show(document.getElementById('import-ok'), false);
  show(document.getElementById('import-err'), false);
  importSummaryEl.hidden = true;
  importBarEl.style.width = '0%';
  importProgTextEl.textContent = '准备中…';
  show(importProgEl, true);
  navState(1, 'blue');
  try {
    const sources = state.importSources.map((s) => s.url ? { kind: 'url', url: s.url } : s.path);
    log(`开始导入 ${sources.length} 个来源…`);
    const results = await window.pptlib.import(sources);
    const summary = summarizeImportResults(results);
    const created = summary.created.length;
    const updated = summary.updated.length;
    const moved = summary.moved.length;
    const unchanged = summary.unchanged.length;
    const removed = summary.removed.length;
    const failed = summary.failed.length;
    log(
      `同步完成：新增 ${created}，更新 ${updated}，路径同步 ${moved}，未变化 ${unchanged}，移除 ${removed}，失败 ${failed}`,
      failed ? 'stderr' : 'ok'
    );
    renderImportSummary(results);
    toast(
      document.getElementById('import-ok'),
      document.getElementById('import-err'),
      failed ? 'err' : 'ok',
      `同步完成：新增 ${created} 个${updated ? `，更新 ${updated} 个` : ''}${moved ? `，路径同步 ${moved} 个` : ''}${unchanged ? `，未变化 ${unchanged} 个` : ''}${removed ? `，移除 ${removed} 个` : ''}${failed ? `，失败 ${failed} 个` : ''}`
    );
    navState(1, failed ? 'gray' : 'green');
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
    show(importProgEl, false);
    setImportBusy(false);
  }
});

// --- Step 2: local catalog grid selection -------------------------------- //
// Browse the local page library (real thumbnails), multi-select, reorder by
// dragging, then compose — entirely local, no upload/round-trip.

const catalog = {
  slides: [],
  byId: {},
  decks: [],
  tree: null,
  commonRoot: '',
  facets: null,
  libraryRevision: null,
  slideCount: 0,
  total: 0,
  page: 1,
  pageSize: 100,
  loading: false,
  requestId: 0,
  matchingDeckIds: null,
};
const selectedIds = [];
let selectionRevision = 0;
let selectionWrite = Promise.resolve();
let selectionGeneration = 0;
const CURRENT_DECK_KEY = 'pinpage.catalog.currentDeckId';
const EXPANDED_FOLDERS_KEY = 'pinpage.catalog.expandedFolders';
const SEARCH_SCOPE_KEY = 'pinpage.catalog.searchScope';
let currentDeckId = localStorage.getItem(CURRENT_DECK_KEY); // which file's pages the grid shows
let searchScope = localStorage.getItem(SEARCH_SCOPE_KEY) === 'all' ? 'all' : 'deck';
let expandedFolderIds = new Set();
let treeInitialized = false;
let searchRenderTimer = null;

function invalidateSelectionManifest() {
  if (!state.manifestFromSelection) return;
  state.manifest = null;
  state.composePreflight = null;
  state.composeResult = null;
  state.manifestFromSelection = false;
  refreshComposeReady();
}

function rememberSlide(slide) {
  const deck = catalog.decks.find((item) => item.deck_id === slide.deck_id);
  const normalized = {
    ...slide,
    source_path: slide.source_path || deck?.source_path || '',
    source_url: slide.source_url || deck?.source_url || '',
    source_format: slide.source_format || deck?.source_format || 'pptx',
  };
  catalog.byId[normalized.slide_id] = normalized;
  return normalized;
}

function applySelection(selection) {
  selectionRevision = Number(selection?.revision) || 0;
  selectedIds.length = 0;
  for (const item of selection?.items || []) {
    const slide = rememberSlide(item);
    selectedIds.push(slide.slide_id);
  }
}

function persistSelection() {
  const slideIds = selectedIds.slice();
  selectionGeneration += 1;
  selectionWrite = selectionWrite
    .catch(() => undefined)
    .then(async () => {
      const response = await window.pptlib.saveSelection({
        slideIds,
        expectedRevision: selectionRevision,
      });
      selectionRevision = response.revision;
      for (const item of response.items || []) rememberSlide(item);
    })
    .catch((error) => {
      log(`保存选片失败：${error.message}`, 'stderr');
    });
  return selectionWrite;
}
try {
  expandedFolderIds = new Set(JSON.parse(localStorage.getItem(EXPANDED_FOLDERS_KEY) || '[]'));
} catch {
  expandedFolderIds = new Set();
}
const gridEl = document.getElementById('grid');
const gridHeadEl = document.getElementById('grid-head');
const gridEmptyEl = document.getElementById('grid-empty');
const deckListEl = document.getElementById('deck-list');
const deckCountEl = document.getElementById('deck-count');
const selListEl = document.getElementById('sel-list');
const selEmptyEl = document.getElementById('sel-empty');
const selCountEl = document.getElementById('sel-count');
const selExportBtn = document.getElementById('sel-export');
const filterTypeEl = document.getElementById('filter-type');
const filterFormatEl = document.getElementById('filter-format');
const gridSearchEl = document.getElementById('grid-search');
const searchScopeButtons = [...document.querySelectorAll('[data-search-scope]')];

const CHECK = '<svg class="ico sm" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>';
const PLUS = '<svg class="ico sm" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>';
const GRIP = '<svg class="ico sm" viewBox="0 0 24 24"><circle cx="9" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="18" r="1"/></svg>';
const RM = '<svg class="ico sm" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>';
const TRASH = '<svg class="ico sm" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M10 11v6M14 11v6"/></svg>';
const DECK_ICON = '<svg class="ico sm" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>';
const TREE_FOLDER_ICON = '<svg class="ico sm" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const CHEVRON = '<svg class="ico sm tree-chevron" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );
}

function isHtmlSlide(slide) {
  return slide?.source_format === 'render_deck_html';
}

function formatBadge(slide) {
  const html = isHtmlSlide(slide);
  return `<span class="format-badge${html ? ' html' : ''}">${html ? 'HTML' : 'PPTX'}</span>`;
}

function slideBadges(slide) {
  return formatBadge(slide) +
    (slide.capabilities?.video === true ? '<span class="video-badge">视频</span>' : '');
}

function matchesFormat(slide) {
  return !filterFormatEl.value || (slide.source_format || 'pptx') === filterFormatEl.value;
}

function selectedHtmlCount() {
  return selectedIds.filter((id) => isHtmlSlide(catalog.byId[id])).length;
}

function updateSelectionExport() {
  const count = selectedHtmlCount();
  selExportBtn.disabled = selectedIds.length === 0 || selExportBtn.dataset.busy === '1';
  const warning = document.getElementById('sel-export-warning');
  warning.textContent = count
    ? 'HTML 页请选 HTML 输出'
    : '';
  show(warning, count > 0);
}

function normalizedPath(value) {
  return String(value || '').replace(/\\/g, '/').replace(/\/+/g, '/');
}

function pathParts(value) {
  return normalizedPath(value).split('/').filter(Boolean);
}

function commonDirectoryParts(decks) {
  const paths = decks
    .map((deck) => pathParts(deck.source_path).slice(0, -1))
    .filter((parts) => parts.length);
  if (!paths.length) return [];
  const common = [];
  const shortest = Math.min(...paths.map((parts) => parts.length));
  for (let index = 0; index < shortest; index += 1) {
    if (!paths.every((parts) => parts[index] === paths[0][index])) break;
    common.push(paths[0][index]);
  }
  // Keep one visible directory when every file comes from exactly one folder.
  if (paths.length === 1 || paths.every((parts) => parts.length === common.length)) {
    common.pop();
  }
  return common;
}

function createFolder(name, fullParts) {
  return {
    name,
    id: `folder:/${fullParts.join('/')}`,
    fullPath: `/${fullParts.join('/')}`,
    folders: new Map(),
    decks: [],
  };
}

function compactFolder(node) {
  let compacted = {
    ...node,
    folders: [...node.folders.values()].map(compactFolder),
  };
  while (compacted.decks.length === 0 && compacted.folders.length === 1) {
    compacted = compacted.folders[0];
  }
  return compacted;
}

function buildDeckTree() {
  const commonParts = commonDirectoryParts(catalog.decks.filter((deck) => !deck.source_url));
  catalog.commonRoot = commonParts.length ? `/${commonParts.join('/')}` : '';
  const root = createFolder('', commonParts);
  for (const deck of catalog.decks) {
    const sourceParts = pathParts(deck.source_path);
    const directoryParts = sourceParts.slice(0, -1);
    let relativeParts = directoryParts.slice(commonParts.length);
    if (!deck.source_path) relativeParts = ['未归档'];
    if (deck.source_url) {
      let host = '在线演示';
      try { host = new URL(deck.source_url).hostname; } catch { /* stored legacy metadata */ }
      relativeParts = [`在线归档 · ${host}`];
    }
    let node = root;
    const fullParts = [...commonParts];
    deck.folderIds = [];
    for (const part of relativeParts) {
      fullParts.push(part);
      if (!node.folders.has(part)) {
        node.folders.set(part, createFolder(part, fullParts));
      }
      node = node.folders.get(part);
      deck.folderIds.push(node.id);
    }
    deck.relativeDirectory = relativeParts.join(' / ');
    node.decks.push(deck);
  }
  return {
    ...root,
    folders: [...root.folders.values()].map(compactFolder),
  };
}

function saveTreeState() {
  localStorage.setItem(CURRENT_DECK_KEY, currentDeckId || '');
  localStorage.setItem(EXPANDED_FOLDERS_KEY, JSON.stringify([...expandedFolderIds]));
}

function revealCurrentDeck() {
  const deck = currentDeck();
  if (!deck) return;
  deck.folderIds.forEach((id) => expandedFolderIds.add(id));
}

function buildDecks() {
  const previousDeckId = currentDeckId;
  if (!catalog.decks.some((d) => d.deck_id === currentDeckId)) {
    currentDeckId = catalog.decks.length ? catalog.decks[0].deck_id : null;
  }
  for (const deck of catalog.decks) {
    deck.pathSearchText = `${deck.deck_name} ${deck.source_path} ${deck.source_url || ''}`.toLowerCase();
  }
  catalog.tree = buildDeckTree();
  if (!treeInitialized) {
    if (expandedFolderIds.size === 0) revealCurrentDeck();
    treeInitialized = true;
  } else if (previousDeckId !== currentDeckId) {
    revealCurrentDeck();
  }
  saveTreeState();
}

function currentDeck() {
  return catalog.decks.find((d) => d.deck_id === currentDeckId) || null;
}

function fillFilters() {
  const types = (catalog.facets?.page_types || [])
    .filter((item) => item.count > 0)
    .map((item) => item.id);
  filterTypeEl.innerHTML =
    '<option value="">全部类型</option>' + types.map((t) => `<option>${esc(t)}</option>`).join('');
}

function searchQuery() {
  return gridSearchEl.value.trim().toLowerCase();
}

function setSearchScope(scope, shouldRender = true) {
  searchScope = scope === 'all' ? 'all' : 'deck';
  localStorage.setItem(SEARCH_SCOPE_KEY, searchScope);
  searchScopeButtons.forEach((button) => {
    const active = button.dataset.searchScope === searchScope;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  gridSearchEl.placeholder =
    searchScope === 'all' ? '搜索全部页库' : '在当前文件中搜索';
  if (shouldRender) {
    renderDeckList();
    loadSlidePage(true);
  }
}

function visibleSlides() {
  return catalog.slides;
}

function pic(s) {
  return s.thumbnail_url
    ? `<img class="pic" src="${esc(s.thumbnail_url)}" loading="lazy" alt="" />`
    : '<div class="pic ph">无缩略图</div>';
}

function deckMatchesQuery(deck, query) {
  const filtering = Boolean(query || filterTypeEl.value || filterFormatEl.value);
  return !filtering || catalog.matchingDeckIds?.has(deck.deck_id);
}

function filteredFolder(folder, query) {
  if (!folder) return null;
  if (!query && !filterFormatEl.value && !filterTypeEl.value) return folder;
  const folderMatches = query && `${folder.name} ${folder.fullPath}`.toLowerCase().includes(query);
  const childQuery = folderMatches ? '' : query;
  const folders = folder.folders
    .map((child) => filteredFolder(child, childQuery))
    .filter(Boolean);
  const decks = folder.decks.filter((deck) =>
    deckMatchesQuery(deck, childQuery) && matchesFormat(deck));
  if (!folders.length && !decks.length) return null;
  return { ...folder, folders, decks };
}

function folderDeckCount(folder) {
  return folder.decks.length +
    folder.folders.reduce((total, child) => total + folderDeckCount(child), 0);
}

function folderSelectedCount(folder) {
  const own = folder.decks.reduce(
    (total, deck) =>
      total + selectedIds.filter((id) => catalog.byId[id]?.deck_id === deck.deck_id).length,
    0,
  );
  return own + folder.folders.reduce(
    (total, child) => total + folderSelectedCount(child),
    0,
  );
}

function renderDeckRow(deck, depth, query) {
  const picked = selectedIds.filter((id) => catalog.byId[id]?.deck_id === deck.deck_id).length;
  const pathHint = query && deck.relativeDirectory
    ? `<span class="dpath">${esc(deck.relativeDirectory)}</span>`
    : '';
  return (
    `<div class="tree-row deck-row${deck.deck_id === currentDeckId ? ' on' : ''}" ` +
    `style="--indent:${depth * 12}px">` +
    `<button class="deck-open" data-deck="${esc(deck.deck_id)}" ` +
    `aria-current="${deck.deck_id === currentDeckId ? 'true' : 'false'}" ` +
    `title="${esc(deck.source_path || deck.deck_name)}">` +
    `<span class="tree-spacer"></span><span class="dico">${DECK_ICON}</span>` +
    `<span class="dinfo"><span class="dname">${esc(deck.deck_name)}</span>` +
    `${pathHint}<span class="dmeta">${formatBadge(deck)} ${deck.slide_count} 页${picked ? ` · 已选 ${picked}` : ''}</span></span></button>` +
    `<button class="ddel" data-deck="${esc(deck.deck_id)}" ` +
    `title="从页库移除该文件（不删源文件）" aria-label="移除文件">${TRASH}</button></div>`
  );
}

function renderFolder(folder, depth, query) {
  const open = Boolean(query) || expandedFolderIds.has(folder.id);
  const selected = folderSelectedCount(folder);
  const children = [
    ...folder.folders.map((child) => renderFolder(child, depth + 1, query)),
    ...folder.decks.map((deck) => renderDeckRow(deck, depth + 1, query)),
  ].join('');
  return (
    '<div class="tree-folder">' +
    `<button class="tree-row folder-row${open ? ' open' : ''}" data-folder="${esc(folder.id)}" ` +
    `style="--indent:${depth * 12}px" title="${esc(folder.fullPath)}" aria-expanded="${open}">` +
    `${CHEVRON}<span class="folder-icon">${TREE_FOLDER_ICON}</span>` +
    `<span class="folder-name">${esc(folder.name)}</span>` +
    (selected ? `<span class="tree-picked">${selected}</span>` : '') +
    `<span class="tree-count">${folderDeckCount(folder)}</span></button>` +
    `<div class="tree-children"${open ? '' : ' hidden'}>${children}</div></div>`
  );
}

function visibleTree(query) {
  return filteredFolder(catalog.tree, query);
}

function renderDeckList() {
  const query = searchScope === 'all' ? searchQuery() : '';
  const tree = visibleTree(query);
  if (query || filterFormatEl.value) {
    const matchingDecks = tree ? folderDeckCount(tree) : 0;
    deckCountEl.textContent = `${matchingDecks} / ${catalog.decks.length} 个`;
  } else {
    deckCountEl.textContent = `${catalog.decks.length} 个`;
  }
  const rows = tree
    ? [
        ...tree.folders.map((folder) => renderFolder(folder, 0, query)),
        ...tree.decks.map((deck) => renderDeckRow(deck, 0, query)),
      ].join('')
    : '';
  deckListEl.innerHTML = rows || '<div class="tree-empty">没有匹配的文件或页面</div>';
  deckListEl.querySelectorAll('.folder-row').forEach((button) => {
    button.addEventListener('click', () => {
      const id = button.dataset.folder;
      if (expandedFolderIds.has(id)) expandedFolderIds.delete(id);
      else expandedFolderIds.add(id);
      saveTreeState();
      renderDeckList();
    });
  });
  deckListEl.querySelectorAll('.deck-open').forEach((button) => {
    button.addEventListener('click', () => {
      currentDeckId = button.dataset.deck;
      setSearchScope('deck', false);
      saveTreeState();
      renderDeckList();
      loadSlidePage(true);
    });
  });
  deckListEl.querySelectorAll('.ddel').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      removeDeck(btn.dataset.deck);
    });
  });
}

function gridMessage(title, hint) {
  return (
    '<div class="grid-message" role="status" aria-live="polite">' +
    `<div class="h">${esc(title)}</div><div class="hint">${esc(hint)}</div></div>`
  );
}

function renderGrid() {
  const deck = currentDeck();
  const query = searchQuery();
  const allLibrary = searchScope === 'all';
  const slides = visibleSlides();
  const hasContext = allLibrary ? catalog.slideCount > 0 : Boolean(deck);

  if (hasContext) {
    const allSelected = slides.length > 0 && slides.every((s) => selectedIds.includes(s.slide_id));
    const title = allLibrary ? '全部页库' : deck.deck_name;
    const fileCount = allLibrary ? new Set(slides.map((slide) => slide.deck_id)).size : 1;
    let count = '';
    if (allLibrary) {
      count = query || filterFormatEl.value
        ? `已加载 ${slides.length} / ${catalog.total} 个结果 · ${fileCount} 个文件`
        : `可搜索 ${catalog.slideCount} 页`;
    } else {
      count = `已加载 ${slides.length} / ${catalog.total || deck.slide_count} 页`;
    }
    let selectLabel = catalog.total > slides.length ? '全选已加载' : '全选本页';
    if (allSelected) selectLabel = '取消全选';
    const selectButton = slides.length
      ? `<button class="btn sm" id="grid-selall">${selectLabel}</button>`
      : '';
    gridHeadEl.hidden = false;
    gridHeadEl.innerHTML =
      `<span class="gt">${esc(title)}</span><span class="gc">${esc(count)}</span>` +
      `<span class="spacer"></span>${selectButton}`;
    const selAll = document.getElementById('grid-selall');
    if (selAll) {
      selAll.addEventListener('click', () => {
        const ids = slides.map((slide) => slide.slide_id);
        if (allSelected) {
          for (const id of ids) {
            const index = selectedIds.indexOf(id);
            if (index >= 0) selectedIds.splice(index, 1);
          }
        } else {
          for (const id of ids) if (!selectedIds.includes(id)) selectedIds.push(id);
        }
        invalidateSelectionManifest();
        persistSelection();
        renderGrid();
        renderSelected();
        renderDeckList();
      });
    }
  } else {
    gridHeadEl.hidden = true;
  }

  if (allLibrary && !query && !filterFormatEl.value && !filterTypeEl.value) {
    gridEl.innerHTML = gridMessage(
      '搜索全部页库',
      '输入标题、正文关键词、页面类型、文件名或目录名。',
    );
  } else if (slides.length === 0) {
    gridEl.innerHTML = gridMessage(
      '没有找到相关页面',
      '换个关键词，或调整页面类型、格式筛选后再试。',
    );
  } else {
    const cards = slides
      .map((s) => {
        const on = selectedIds.includes(s.slide_id);
        const tag = s.page_type || s.topic || '';
        const source = allLibrary ? `${s.deck_name} · p${s.slide_number}` : `p${s.slide_number}`;
        return (
          `<div class="thumb${on ? ' sel' : ''}" data-id="${esc(s.slide_id)}">` +
          `<button class="thumb-preview" data-preview-slide="${esc(s.slide_id)}" ` +
          `aria-label="预览：${esc(s.title || '(无标题)')}" title="放大预览">` +
          (tag ? `<span class="tag">${esc(tag)}</span>` : '') +
          pic(s) +
          '</button>' +
          `<button class="tadd${on ? ' is-added' : ''}" data-id="${esc(s.slide_id)}" ` +
          `title="${on ? '移出选片' : '加入选片'}" aria-label="${on ? '移出选片' : '加入选片'}">` +
          `${on ? CHECK : PLUS}</button>` +
          `<button class="tdel" data-id="${esc(s.slide_id)}" title="从页库移除该页（不删源文件）">${TRASH}</button>` +
          `<div class="meta"><div class="t">${esc(s.title || '(无标题)')}</div>` +
          `<div class="s">${slideBadges(s)} ${esc(source)}</div></div></div>`
        );
      })
      .join('');
    const loadMore = slides.length < catalog.total
      ? `<div class="grid-more"><button class="btn" id="grid-more" ${catalog.loading ? 'disabled' : ''}>` +
        `${catalog.loading ? '正在加载…' : `继续加载（剩余 ${catalog.total - slides.length} 页）`}</button></div>`
      : '';
    gridEl.innerHTML = cards + loadMore;
  }
  document.getElementById('grid-more')?.addEventListener('click', () => loadSlidePage(false));
  gridEl.querySelectorAll('.thumb-preview').forEach((button) => {
    button.addEventListener('click', () => {
      openLightbox(button.dataset.previewSlide);
    });
  });
  gridEl.querySelectorAll('.tdel').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      removeSlide(btn.dataset.id);
    });
  });
  gridEl.querySelectorAll('.tadd').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSelect(btn.dataset.id, true);
    });
  });
}

function renderSelected() {
  selCountEl.textContent = selectedIds.length;
  updateSelectionExport();
  selEmptyEl.hidden = selectedIds.length > 0;
  selListEl.innerHTML = selectedIds
    .map((id, i) => {
      const s = catalog.byId[id];
      if (!s) return '';
      const mini = s.thumbnail_url
        ? `<img class="mini" src="${esc(s.thumbnail_url)}" alt="" />`
        : '<span class="mini"></span>';
      return (
        `<li draggable="true" data-id="${esc(id)}">` +
        `<span class="grip">${GRIP}</span>` +
        `<span class="ord">${i + 1}</span>` +
        mini +
        `<span class="txt"><div class="t">${esc(s.title || '(无标题)')}</div>` +
        `<div class="s">${formatBadge(s)} ${esc(s.deck_name)} · p${s.slide_number}</div></span>` +
        `<button class="rm" data-id="${esc(id)}">${RM}</button></li>`
      );
    })
    .join('');
  selListEl.querySelectorAll('.rm').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSelect(btn.dataset.id);
    });
  });
  bindDrag();
}

function toggleSelect(id, restoreGridFocus = false) {
  const i = selectedIds.indexOf(id);
  if (i >= 0) selectedIds.splice(i, 1);
  else selectedIds.push(id);
  invalidateSelectionManifest();
  persistSelection();
  renderGrid();
  renderSelected();
  renderDeckList();
  if (restoreGridFocus) {
    requestAnimationFrame(() => {
      const button = [...gridEl.querySelectorAll('.tadd')]
        .find((item) => item.dataset.id === id);
      if (button) button.focus();
    });
  }
}

// --- Fullscreen preview (lightbox) --------------------------------------- //
// Uses the high-resolution preview image; navigates within the currently
// visible grid order so ← / → walk the same pages you see.
const lightboxEl = document.getElementById('lightbox');
const lbImg = document.getElementById('lb-img');
const lbTitle = document.getElementById('lb-title');
const lbMeta = document.getElementById('lb-meta');
const lbToggleSelBtn = document.getElementById('lb-toggle-sel');
const lbFullscreenBtn = document.getElementById('lb-fullscreen');
const lbDynamicBtn = document.getElementById('lb-dynamic');
const lbWarningsEl = document.getElementById('lb-warnings');
const lbPreviewErrorEl = document.getElementById('lb-preview-error');
let dynamicOpening = false;
let lbList = []; // slide_ids in the order shown when the lightbox opened
let lbIndex = -1;
let lbReturnFocus = null;

function openLightbox(id) {
  openLightboxList(visibleSlides().map((s) => s.slide_id), id);
}

function openLightboxList(slideIds, id) {
  lbReturnFocus = document.activeElement;
  lbList = slideIds;
  lbIndex = lbList.indexOf(id);
  if (lbIndex < 0) {
    lbList = [id];
    lbIndex = 0;
  }
  showLightboxSlide();
  document.querySelectorAll('.topbar, .nav, .main, .logbar').forEach((element) => {
    element.inert = true;
  });
  lightboxEl.hidden = false;
  requestAnimationFrame(() => document.getElementById('lb-close').focus());
}

function showLightboxSlide() {
  const s = catalog.byId[lbList[lbIndex]];
  if (!s) return;
  lbImg.src = s.preview_url || s.thumbnail_url || '';
  lbImg.alt = s.title || 'PPT 页面预览';
  lbTitle.textContent = s.title || '(无标题)';
  lbMeta.textContent = `${isHtmlSlide(s) ? 'HTML' : 'PPTX'}${s.capabilities?.video ? ' · 视频' : ''} · ${s.deck_name} · 第 ${s.slide_number} 页 · ${lbIndex + 1}/${lbList.length}`;
  show(lbDynamicBtn, state.htmlEnabled && isHtmlSlide(s) && s.capabilities?.dynamic_preview === true);
  lbDynamicBtn.disabled = dynamicOpening;
  lbDynamicBtn.textContent = dynamicOpening ? '正在打开…' : '播放动态版本';
  show(lbPreviewErrorEl, false);
  const warnings = [];
  if (isHtmlSlide(s)) {
    warnings.push('当前为静态预览。动态版本采用安全模式：原始脚本、事件、iframe 和表单已移除，仅运行受信任的播放代码。');
    if (s.capabilities?.requires_network) warnings.push('源页面依赖网络；隔离预览禁止外部请求，部分内容可能不可用。');
    if (state.htmlEnabled) warnings.push('HTML 页请选 HTML 输出。');
  }
  for (const warning of Array.isArray(s.warnings) ? s.warnings : []) {
    const message = typeof warning === 'string' ? warning : warning?.message || warning?.code;
    if (typeof message === 'string') warnings.push(message);
  }
  lbWarningsEl.textContent = warnings.join(' ');
  show(lbWarningsEl, warnings.length > 0);
  const sourceEl = document.getElementById('lb-source');
  sourceEl.textContent = s.source_url ? `在线来源：${s.source_url}` : '';
  show(sourceEl, Boolean(s.source_url));
  lightboxEl.classList.toggle('has-notices', warnings.length > 0 || Boolean(s.source_url));
  const picked = selectedIds.includes(s.slide_id);
  lbToggleSelBtn.textContent = picked ? '移出选片' : '加入选片';
  lbToggleSelBtn.classList.toggle('primary', !picked);
}

function updateFullscreenControl() {
  const active = document.fullscreenElement === lightboxEl;
  lbFullscreenBtn.title = active ? '退出全屏（F）' : '进入全屏（F）';
  lbFullscreenBtn.setAttribute('aria-label', active ? '退出全屏' : '进入全屏');
}

async function toggleLightboxFullscreen() {
  try {
    if (document.fullscreenElement === lightboxEl) {
      await document.exitFullscreen();
    } else {
      await lightboxEl.requestFullscreen();
    }
  } catch (error) {
    log(`无法切换全屏：${error.message}`, 'stderr');
  }
}

async function closeLightbox() {
  if (document.fullscreenElement === lightboxEl) {
    try {
      await document.exitFullscreen();
    } catch (error) {
      log(`退出全屏失败：${error.message}`, 'stderr');
    }
  }
  lightboxEl.hidden = true;
  lbImg.src = '';
  document.querySelectorAll('.topbar, .nav, .main, .logbar').forEach((element) => {
    element.inert = false;
  });
  if (lbReturnFocus && document.contains(lbReturnFocus)) lbReturnFocus.focus();
  lbReturnFocus = null;
}

function lbStep(delta) {
  if (lbList.length === 0) return;
  lbIndex = (lbIndex + delta + lbList.length) % lbList.length;
  showLightboxSlide();
}

document.getElementById('lb-prev').addEventListener('click', () => lbStep(-1));
document.getElementById('lb-next').addEventListener('click', () => lbStep(1));
lbFullscreenBtn.addEventListener('click', toggleLightboxFullscreen);
document.getElementById('lb-close').addEventListener('click', closeLightbox);
document.getElementById('lb-backdrop').addEventListener('click', closeLightbox);
document.addEventListener('fullscreenchange', updateFullscreenControl);
lbDynamicBtn.addEventListener('click', async () => {
  const id = lbList[lbIndex];
  const slide = catalog.byId[id];
  if (dynamicOpening || !state.htmlEnabled || !isHtmlSlide(slide) ||
      slide.capabilities?.dynamic_preview !== true) return;
  dynamicOpening = true;
  showLightboxSlide();
  try {
    await window.pptlib.openHtmlPreview(id);
  } catch (error) {
    if (!lightboxEl.hidden && lbList[lbIndex] === id) {
      lbPreviewErrorEl.textContent = `动态预览失败：${error.message}`;
      show(lbPreviewErrorEl, true);
    }
  } finally {
    dynamicOpening = false;
    lbDynamicBtn.disabled = false;
    lbDynamicBtn.textContent = '播放动态版本';
  }
});
lbToggleSelBtn.addEventListener('click', () => {
  const id = lbList[lbIndex];
  if (id) {
    toggleSelect(id);
    showLightboxSlide(); // refresh the button label + keep viewing
  }
});
document.addEventListener('keydown', (e) => {
  if (lightboxEl.hidden) return;
  if (e.key === 'Escape') {
    if (document.fullscreenElement !== lightboxEl) closeLightbox();
  } else if (e.key.toLowerCase() === 'f') {
    e.preventDefault();
    toggleLightboxFullscreen();
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault();
    lbStep(-1);
  } else if (e.key === 'ArrowRight') {
    e.preventDefault();
    lbStep(1);
  }
});

// Drop a slide from every in-memory view after it's removed from the index.
function forgetSlide(id) {
  delete catalog.byId[id];
  catalog.slides = catalog.slides.filter((s) => s.slide_id !== id);
  const si = selectedIds.indexOf(id);
  if (si >= 0) {
    selectedIds.splice(si, 1);
    invalidateSelectionManifest();
  }
}

// Remove a whole file (deck) from the library. Source PPTX is never touched.
async function removeDeck(deckId) {
  const deck = catalog.decks.find((d) => d.deck_id === deckId);
  if (!deck) return;
  const ok = window.confirm(
    `从页库移除文件「${deck.deck_name}」的全部 ${deck.slide_count} 页？\n\n` +
      `只从本地页库移除索引与缩略图，不会删除你的原始文件。`,
  );
  if (!ok) return;
  try {
    const res = await window.pptlib.removeDeck(deckId);
    catalog.slides
      .filter((slide) => slide.deck_id === deckId)
      .forEach((slide) => forgetSlide(slide.slide_id));
    if (currentDeckId === deckId) currentDeckId = null;
    catalog.libraryRevision = null;
    await loadCatalog();
    log(`已从页库移除「${deck.deck_name}」：${(res && res.slides_removed) || deck.slide_count} 页`, 'ok');
  } catch (error) {
    log(`删除失败：${error.message}`, 'stderr');
    window.alert(`删除失败：${error.message}`);
  }
}

// Remove a single page from the library. Source PPTX is never touched.
async function removeSlide(slideId) {
  const s = catalog.byId[slideId];
  if (!s) return;
  const ok = window.confirm(
    `从页库移除这一页？\n\n${s.deck_name} · p${s.slide_number}${s.title ? '（' + s.title + '）' : ''}\n\n` +
      `只从本地页库移除，不会删除你的原始文件。`,
  );
  if (!ok) return;
  try {
    const res = await window.pptlib.removeSlide(slideId);
    forgetSlide(slideId);
    catalog.libraryRevision = null;
    await loadCatalog();
    log(`已从页库移除 1 页（${s.deck_name} · p${s.slide_number}）`, 'ok');
    return res;
  } catch (error) {
    log(`删除失败：${error.message}`, 'stderr');
    window.alert(`删除失败：${error.message}`);
  }
}

// native drag-to-reorder for the selected list
let dragId = null;
function bindDrag() {
  selListEl.querySelectorAll('li').forEach((li) => {
    li.addEventListener('dragstart', () => {
      dragId = li.dataset.id;
      li.classList.add('dragging');
    });
    li.addEventListener('dragend', () => {
      li.classList.remove('dragging');
      selListEl.querySelectorAll('li').forEach((x) => x.classList.remove('over'));
    });
    li.addEventListener('dragover', (e) => {
      e.preventDefault();
      li.classList.add('over');
    });
    li.addEventListener('dragleave', () => li.classList.remove('over'));
    li.addEventListener('drop', (e) => {
      e.preventDefault();
      const from = selectedIds.indexOf(dragId);
      const to = selectedIds.indexOf(li.dataset.id);
      if (from < 0 || to < 0 || from === to) return;
      selectedIds.splice(to, 0, selectedIds.splice(from, 1)[0]);
      invalidateSelectionManifest();
      persistSelection();
      renderSelected();
    });
  });
}

function pageFilters() {
  return {
    deck_id: searchScope === 'deck' ? currentDeckId || '' : '',
    page_type: filterTypeEl.value,
    source_format: filterFormatEl.value,
  };
}

async function loadSlidePage(reset = true) {
  if (!reset && catalog.loading) return;
  const requestId = ++catalog.requestId;
  if (catalog.slideCount === 0) {
    catalog.slides = [];
    catalog.total = 0;
    catalog.loading = false;
    renderGrid();
    return;
  }
  if (searchScope === 'deck' && !currentDeckId) {
    catalog.slides = [];
    catalog.total = 0;
    catalog.loading = false;
    renderGrid();
    return;
  }
  if (
    searchScope === 'all' &&
    !searchQuery() &&
    !filterFormatEl.value &&
    !filterTypeEl.value
  ) {
    catalog.slides = [];
    catalog.total = catalog.slideCount;
    catalog.loading = false;
    renderGrid();
    return;
  }
  const page = reset ? 1 : catalog.page + 1;
  if (reset) {
    catalog.slides = [];
    catalog.total = 0;
    catalog.matchingDeckIds = new Set();
  }
  catalog.loading = true;
  renderGrid();
  try {
    const response = await window.pptlib.searchLibrary({
      query: searchQuery(),
      page,
      pageSize: catalog.pageSize,
      filters: pageFilters(),
    });
    if (requestId !== catalog.requestId) return;
    const incoming = (response.items || []).map(rememberSlide);
    if (reset) {
      catalog.slides = incoming;
    } else {
      const existing = new Set(catalog.slides.map((slide) => slide.slide_id));
      catalog.slides.push(...incoming.filter((slide) => !existing.has(slide.slide_id)));
    }
    catalog.page = response.page || page;
    catalog.total = Number(response.total) || 0;
    catalog.matchingDeckIds = new Set(response.deckIds || []);
  } catch (error) {
    if (requestId === catalog.requestId) log(`加载页面失败：${error.message}`, 'stderr');
  } finally {
    if (requestId === catalog.requestId) {
      catalog.loading = false;
      renderDeckList();
      renderGrid();
    }
  }
}

function normalizeDeck(deck) {
  return {
    deck_id: deck.id,
    deck_name: deck.name,
    slide_count: Number(deck.slide_count) || 0,
    source_path: deck.source_path || '',
    source_url: deck.source_url || '',
    source_format: deck.source_format || 'pptx',
    cover_thumbnail_url: deck.cover_thumbnail_url || '',
  };
}

async function loadCatalog() {
  const btn = document.getElementById('load-catalog');
  const reloadBtn = document.getElementById('reload-catalog');
  if (btn) busy(btn, true);
  if (reloadBtn) busy(reloadBtn, true);
  navState(2, 'blue');
  try {
    await selectionWrite;
    const selectionGenerationAtRequest = selectionGeneration;
    log(catalog.libraryRevision ? '检查页库更新…' : '加载本地页库…');
    const res = await window.pptlib.loadLibrary(catalog.libraryRevision);
    applyHtmlFeature(res.htmlEnabled === true);
    if (!res.unchanged) {
      catalog.libraryRevision = res.libraryRevision;
      catalog.slideCount = Number(res.slideCount) || 0;
      catalog.decks = (res.decks || []).map(normalizeDeck);
      catalog.facets = res.facets || null;
      catalog.byId = {};
      buildDecks();
      fillFilters();
    }
    if (selectionGeneration === selectionGenerationAtRequest) {
      applySelection(res.selection);
    }
    renderDeckList();
    renderSelected();
    gridEmptyEl.hidden = catalog.slideCount > 0;
    gridEl.hidden = catalog.slideCount === 0;
    await loadSlidePage(true);
    log(
      res.unchanged
        ? `页库无变化，已恢复 ${selectedIds.length} 页选片`
        : `页库已加载：${catalog.slideCount} 页，${catalog.decks.length} 个文件`,
      'ok',
    );
    navState(2, catalog.slideCount ? 'green' : 'gray');
    maybeStartOnboarding(catalog.slideCount);
  } catch (error) {
    log(`加载页库失败：${error.message}`, 'stderr');
    navState(2, 'gray');
  } finally {
    if (btn) busy(btn, false);
    if (reloadBtn) busy(reloadBtn, false);
  }
}

document.getElementById('load-catalog').addEventListener('click', loadCatalog);
document.getElementById('reload-catalog').addEventListener('click', loadCatalog);
loadCatalog().catch(() => maybeStartOnboarding(0));
let libraryTaskWasActive = false;
window.pptlib.onTaskState((task) => {
  if (task) {
    libraryTaskWasActive = true;
    return;
  }
  if (libraryTaskWasActive) {
    libraryTaskWasActive = false;
    loadCatalog();
  }
});
document.getElementById('sel-clear').addEventListener('click', () => {
  selectedIds.length = 0;
  invalidateSelectionManifest();
  persistSelection();
  renderGrid();
  renderSelected();
  renderDeckList();
});
filterTypeEl.addEventListener('change', () => loadSlidePage(true));
filterFormatEl.addEventListener('change', () => {
  if (searchScope === 'deck' && !matchesFormat(currentDeck())) {
    currentDeckId = catalog.decks.find(matchesFormat)?.deck_id || null;
    revealCurrentDeck();
    saveTreeState();
  }
  renderDeckList();
  loadSlidePage(true);
});
gridSearchEl.addEventListener('input', () => {
  window.clearTimeout(searchRenderTimer);
  searchRenderTimer = window.setTimeout(() => {
    renderDeckList();
    loadSlidePage(true);
  }, 250);
});
searchScopeButtons.forEach((button) => {
  button.addEventListener('click', () => setSearchScope(button.dataset.searchScope));
});
setSearchScope(searchScope, false);

// --- Step 4: duplicate management --------------------------------------- //

const duplicateState = {
  report: null,
  loading: false,
  selected: new Set(),
};
const scanDuplicatesBtn = document.getElementById('scan-duplicates');
const deleteDuplicatesBtn = document.getElementById('delete-duplicates');
const dedupeStatsEl = document.getElementById('dedupe-stats');
const dedupeToolbarEl = document.getElementById('dedupe-toolbar');
const dedupeEmptyEl = document.getElementById('dedupe-empty');
const dedupeGroupsEl = document.getElementById('dedupe-groups');

function duplicateKindLabel(kind) {
  if (kind === 'exact_structure') return '结构完全一致';
  if (kind === 'exact_visual') return '视觉完全一致';
  if (kind === 'near') return '轻微改版';
  return '建议保留';
}

function updateDuplicateAction() {
  const count = duplicateState.selected.size;
  deleteDuplicatesBtn.disabled = count === 0;
  deleteDuplicatesBtn.classList.toggle('danger', count > 0);
  deleteDuplicatesBtn.lastChild.textContent = count ? ` 删除已勾选（${count}）` : ' 删除已勾选';
}

function setDuplicateCanonical(groupId, slideId) {
  const group = duplicateState.report.groups.find((item) => item.group_id === groupId);
  if (!group) return;
  group.canonical_slide_id = slideId;
  for (const member of group.members) {
    member.is_canonical = member.slide_id === slideId;
    if (member.is_canonical) duplicateState.selected.delete(member.slide_id);
    else if (group.kind === 'exact') duplicateState.selected.add(member.slide_id);
  }
  renderDuplicateGroups();
}

function renderDuplicateGroups() {
  const report = duplicateState.report;
  const groups = report ? report.groups || [] : [];
  dedupeGroupsEl.innerHTML = groups
    .map((group, groupIndex) => {
      const exact = group.kind === 'exact';
      const confidence = Math.round(Number(group.confidence || 0) * 100);
      const members = group.members
        .map((member) => {
          const canonical = member.slide_id === group.canonical_slide_id;
          const checked = duplicateState.selected.has(member.slide_id);
          const image = member.thumbnail_url
            ? `<img src="${esc(member.thumbnail_url)}" alt="" />`
            : '<span class="dup-no-image">无预览</span>';
          const matchLabel = canonical
            ? '匹配基准 · 建议保留'
            : duplicateKindLabel(member.match_kind);
          return (
            `<div class="dup-member${canonical ? ' canonical' : ''}">` +
            `<button class="dup-preview" data-preview="${esc(member.slide_id)}" ` +
            `data-group="${esc(group.group_id)}" title="放大对比">${image}</button>` +
            '<div class="dup-member-meta">' +
            `<div class="dup-member-title">${esc(member.title || '(无标题)')}</div>` +
            `<div class="dup-member-source">${esc(member.deck_name)} · p${member.slide_number}</div>` +
            `<div class="dup-member-match">${matchLabel}</div></div>` +
            '<div class="dup-member-actions">' +
            (canonical
              ? '<span class="dup-keep-badge">保留</span>'
              : `<label class="dup-check"><input type="checkbox" data-delete="${esc(member.slide_id)}" ` +
                `${checked ? 'checked' : ''}/> 从页库删除</label>` +
                `<button class="btn ghost sm dup-keep" data-keep="${esc(member.slide_id)}" ` +
                `data-group="${esc(group.group_id)}">设为保留</button>`) +
            '</div></div>'
          );
        })
        .join('');
      return (
        `<article class="dup-group" data-kind="${exact ? 'exact' : 'similar'}">` +
        '<header class="dup-group-head">' +
        `<div><div class="dup-group-title">重复组 ${groupIndex + 1}</div>` +
        `<div class="dup-group-sub">${group.members.length} 个来源 · ` +
        `${exact ? '确定重复' : `疑似重复 ${confidence}%`}</div></div>` +
        `<span class="dup-kind">${exact ? '确定重复' : '人工复核'}</span>` +
        `</header><div class="dup-members">${members}</div></article>`
      );
    })
    .join('');

  dedupeGroupsEl.querySelectorAll('[data-delete]').forEach((input) => {
    input.addEventListener('change', () => {
      if (input.checked) duplicateState.selected.add(input.dataset.delete);
      else duplicateState.selected.delete(input.dataset.delete);
      updateDuplicateAction();
    });
  });
  dedupeGroupsEl.querySelectorAll('[data-keep]').forEach((button) => {
    button.addEventListener('click', () => {
      setDuplicateCanonical(button.dataset.group, button.dataset.keep);
    });
  });
  dedupeGroupsEl.querySelectorAll('[data-preview]').forEach((button) => {
    button.addEventListener('click', () => {
      const group = duplicateState.report.groups.find(
        (item) => item.group_id === button.dataset.group,
      );
      if (group) {
        openLightboxList(
          group.members.map((member) => member.slide_id),
          button.dataset.preview,
        );
      }
    });
  });
  updateDuplicateAction();
}

function renderDuplicateReport(report) {
  duplicateState.report = report;
  duplicateState.selected.clear();
  for (const group of report.groups || []) {
    for (const member of group.members) {
      catalog.byId[member.slide_id] = { ...catalog.byId[member.slide_id], ...member };
      if (member.selected_by_default) duplicateState.selected.add(member.slide_id);
    }
  }
  document.getElementById('dedupe-total').textContent = report.analyzed_slides || 0;
  document.getElementById('dedupe-exact').textContent = report.exact_groups || 0;
  document.getElementById('dedupe-similar').textContent = report.similar_groups || 0;
  document.getElementById('dedupe-removable').textContent = report.removable_pages || 0;
  show(dedupeStatsEl, true);
  const hasGroups = (report.groups || []).length > 0;
  show(dedupeToolbarEl, hasGroups);
  show(dedupeGroupsEl, hasGroups);
  show(dedupeEmptyEl, !hasGroups);
  if (!hasGroups) {
    dedupeEmptyEl.querySelector('.h').textContent = '没有发现重复页面';
    dedupeEmptyEl.querySelector('.hint').textContent =
      '当前规则采用保守阈值，不会把同模板、不同内容的页面算作重复。';
  }
  renderDuplicateGroups();
}

async function scanDuplicates(refresh) {
  if (duplicateState.loading) return;
  duplicateState.loading = true;
  busy(scanDuplicatesBtn, true);
  scanDuplicatesBtn.lastChild.textContent = ' 正在分析…';
  navState(4, 'blue');
  try {
    const report = await window.pptlib.findDuplicates(refresh);
    renderDuplicateReport(report);
    const groupCount = (report.groups || []).length;
    log(
      `重复扫描完成：${report.analyzed_slides} 页，发现 ${groupCount} 组，` +
        `默认可清理 ${report.removable_pages} 页`,
      'ok',
    );
    navState(4, 'green');
  } catch (error) {
    log(`重复扫描失败：${error.message}`, 'stderr');
    dedupeEmptyEl.querySelector('.h').textContent = '扫描失败';
    dedupeEmptyEl.querySelector('.hint').textContent = error.message;
    show(dedupeEmptyEl, true);
    navState(4, 'gray');
  } finally {
    duplicateState.loading = false;
    busy(scanDuplicatesBtn, false);
    scanDuplicatesBtn.lastChild.textContent = ' 扫描重复页';
  }
}

scanDuplicatesBtn.addEventListener('click', () => scanDuplicates(false));
deleteDuplicatesBtn.addEventListener('click', async () => {
  const ids = [...duplicateState.selected];
  if (ids.length === 0) return;
  const confirmed = window.confirm(
    `从页库删除已勾选的 ${ids.length} 页？\n\n` +
      '只删除本地索引与预览缓存，不会修改原始 PPTX 文件。',
  );
  if (!confirmed) return;
  busy(deleteDuplicatesBtn, true);
  try {
    await window.pptlib.removeSlide(ids);
    ids.forEach(forgetSlide);
    catalog.libraryRevision = null;
    await loadCatalog();
    log(`已从页库删除 ${ids.length} 个重复页面，原始 PPTX 未改动`, 'ok');
    await scanDuplicates(false);
  } catch (error) {
    log(`删除重复页失败：${error.message}`, 'stderr');
    window.alert(`删除失败：${error.message}`);
  } finally {
    updateDuplicateAction();
  }
});

// "用选片进入组合": persist the ordered ids as a manifest, arm compose, jump.
selExportBtn.addEventListener('click', async () => {
  if (selectedIds.length === 0) {
    updateSelectionExport();
    return;
  }
  busy(selExportBtn, true);
  try {
    await selectionWrite;
    const res = await window.pptlib.writeManifest(selectedIds.slice());
    state.manifest = res.path;
    state.manifestFromSelection = true;
    log(`已按 ${res.count} 页写出选片清单 → ${res.path}`, 'ok');
    toast(
      document.getElementById('manifest-ok'),
      document.getElementById('manifest-err'),
      'ok',
      `已选 ${res.count} 页，进入组合导出`
    );
    navState(2, 'green');
    refreshComposeReady();
    goStep(3);
  } catch (error) {
    log(`写出选片清单失败：${error.message}`, 'stderr');
  } finally {
    busy(selExportBtn, false);
    updateSelectionExport();
  }
});

// --- Step 3: compose ----------------------------------------------------- //

const pickManifestBtn = document.getElementById('pick-manifest');
const pickOutputBtn = document.getElementById('pick-output');
const runComposeBtn = document.getElementById('run-compose');
const composeChosen = document.getElementById('compose-chosen');
const composeSelectionStatus = document.getElementById('compose-selection-status');
const composeSetupEl = document.getElementById('compose-setup');
const composeCheckEl = document.getElementById('compose-check');
const composeResultEl = document.getElementById('compose-result');
const confirmComposeBtn = document.getElementById('confirm-compose');
const outputFormatEl = document.getElementById('output-format');
let composeRunning = false;

function selectedOutputFormat() {
  const formats = {
    pptx: {
      label: 'PPTX', extension: 'pptx', method: 'OOXML 复制',
      hint: '保留 PPTX 原生可编辑内容；HTML 页请选 HTML 输出。',
      description: '选好页后，选一个输出位置即可按序拼出新 PPTX。源页面保真复制（原生 OOXML，非重渲染图片）。',
    },
    html: {
      label: 'HTML 离线单文件', extension: 'html', method: '离线单文件',
      hint: '页面与资源存入一个 .html，可离线打开。PPTX 页使用高清图片，不保留原生编辑能力。',
      description: '按选片顺序生成离线 HTML 演示，页面与资源整合到单个文件。导出前检查来源与兼容性。',
    },
    zip: {
      label: 'HTML 资源包', extension: 'zip', method: 'HTML + 资源包',
      hint: '解压后打开 HTML，分享时保留完整资源目录。PPTX 页使用高清图片。',
      description: '按选片顺序生成 HTML 与资源目录，打包为 ZIP。解压后打开演示，分享时保留整个资源包。',
    },
  };
  return formats[state.outputFormat] || formats.pptx;
}

function updateComposeCopy() {
  const format = selectedOutputFormat();
  document.getElementById('compose-description').textContent = format.description;
  document.getElementById('output-format-hint').textContent = format.hint;
  document.getElementById('compose-method').textContent = format.method;
  confirmComposeBtn.textContent = state.htmlEnabled ? `确认导出 ${format.label}` : '确认导出';
}

function validateSelectedOutput(output) {
  const extension = selectedOutputFormat().extension;
  if (typeof output !== 'string' || !output.toLowerCase().endsWith(`.${extension}`)) {
    throw new Error(`输出文件扩展名必须为 .${extension}，请重新选择保存位置`);
  }
}

function setComposeBusy(on) {
  composeRunning = on;
  for (const control of [pickOutputBtn, pickManifestBtn, outputFormatEl, verifyHashEl]) {
    control.disabled = on;
  }
  runComposeBtn.disabled = on || !(state.manifest && state.output);
  confirmComposeBtn.disabled = on || !state.composePreflight?.ok ||
    !state.composePreflight?.preflight_token || Boolean(state.composePreflight?.blockers?.length);
}

outputFormatEl.addEventListener('change', () => {
  const value = outputFormatEl.value;
  if (composeRunning || !state.htmlEnabled || !['pptx', 'html', 'zip'].includes(value)) {
    outputFormatEl.value = state.outputFormat;
    return;
  }
  if (value === state.outputFormat) return;
  state.outputFormat = value;
  state.output = null;
  state.composeResult = null;
  show(document.getElementById('compose-err'), false);
  refreshComposeReady();
});

function setComposeView(view) {
  show(composeSetupEl, view === 'setup');
  show(composeCheckEl, view === 'check');
  show(composeResultEl, view === 'result');
}

function refreshComposeReady() {
  state.composePreflight = null;
  setComposeView('setup');
  runComposeBtn.disabled = composeRunning || !(state.manifest && state.output);
  updateComposeCopy();
  if (state.manifestFromSelection) {
    composeSelectionStatus.textContent = `已载入当前选片：${selectedIds.length} 页`;
  } else if (state.manifest) {
    composeSelectionStatus.textContent = '已导入已有选片清单';
  } else {
    composeSelectionStatus.textContent = '尚未载入选片，请返回选片后再进入组合';
  }
  composeSelectionStatus.className = `compose-selection-status${state.manifest ? ' is-ready' : ''}`;
  composeChosen.textContent = state.output
    ? `保存到：${state.output}`
    : '尚未选择保存位置';
}

pickManifestBtn.addEventListener('click', async () => {
  try {
    const manifest = await window.pptlib.pickManifest();
    if (manifest) {
      state.manifest = manifest;
      state.manifestFromSelection = false;
      refreshComposeReady();
    }
  } catch (error) {
    toast(
      document.getElementById('compose-ok'),
      document.getElementById('compose-err'),
      'err',
      `选择清单失败：${error.message}`,
    );
  }
});

pickOutputBtn.addEventListener('click', async () => {
  try {
    const format = state.outputFormat;
    const output = await window.pptlib.pickSaveOutput({ format });
    if (state.outputFormat !== format) return;
    if (output) {
      validateSelectedOutput(output);
      state.output = output;
      refreshComposeReady();
    }
  } catch (error) {
    toast(
      document.getElementById('compose-ok'),
      document.getElementById('compose-err'),
      'err',
      `选择输出位置失败：${error.message}`,
    );
  }
});

const verifyHashEl = document.getElementById('verify-hash');
const sideVerifyEl = document.getElementById('side-verify');
if (verifyHashEl && sideVerifyEl) {
  verifyHashEl.addEventListener('change', () => {
    sideVerifyEl.textContent = verifyHashEl.checked ? '开启' : '关闭';
    refreshComposeReady();
  });
}
refreshComposeReady();

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes <= 0) return '—';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${Math.round(bytes / 1024 / 1024)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function composeWarningCopy(code) {
  if (code && typeof code === 'object') {
    return code.message || composeWarningCopy(code.code);
  }
  if (typeof code === 'string' && code.startsWith('PPTX_FLATTENED_TO_IMAGE')) {
    return 'PPTX 页已转为高清静态图片，不保留原生编辑、动画或交互。需要可编辑版本时请选择 PPTX 输出。';
  }
  const htmlWarnings = {
    HTML_UNSAFE_OR_UNSUPPORTED_CONTENT_BLOCKED: 'HTML 中不安全或不支持的内容已移除，请复核导出效果。',
    HTML_EXTERNAL_RESOURCES_BLOCKED: 'HTML 的外部资源已阻止访问，部分内容可能缺失。请先归档所需资源后重试。',
    HTML_CYCLIC_RESOURCE_BLOCKED: 'HTML 中循环引用的资源已阻止加载，请复核相关页面。',
  };
  if (Object.hasOwn(htmlWarnings, code)) return htmlWarnings[code];
  if (code === 'EXTERNAL_LINK_PRESERVED') {
    return '包含外部链接。链接会保留，但离线打开时相关内容可能不可用。';
  }
  if (code === 'UNSUPPORTED_OBJECT_PRESERVED') {
    return '包含视频、音频、OLE 或控件。对象会保留，建议导出后在 PowerPoint 中复核。';
  }
  return `检测到兼容性提示：${code}`;
}

function renderComposePreflight(result) {
  state.composePreflight = result;
  const blockers = Array.isArray(result.blockers) ? result.blockers : [];
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  document.getElementById('compose-check-title').textContent =
    blockers.length ? '暂时无法导出' : warnings.length ? '发现兼容性提示' : '检查通过';
  let checkCopy = '来源、页面尺寸与文件状态均正常。';
  if (blockers.length) {
    checkCopy = '按下面提示修复后再试，当前设置会保留。';
  } else if (warnings.length) {
    checkCopy = '可以继续导出，完成后建议复核提示项。';
  }
  document.getElementById('compose-check-copy').textContent = checkCopy;
  document.getElementById('compose-check-summary').innerHTML = [
    ['页面', `${result.page_count || 0} 页`],
    ['来源', `${result.source_count || 0} 个文件`],
    ['预计体积', `约 ${formatBytes(result.estimated_output_bytes)}`],
    ['预计保真', result.estimated_fidelity || '—'],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${esc(value)}</strong></div>`).join('');
  const issueRows = [
    ...blockers.map((item) => ({
      kind: 'blocker',
      text: item.message || item.code || '存在阻断问题',
    })),
    ...warnings.map((code) => ({ kind: 'warning', text: composeWarningCopy(code) })),
  ];
  if (result.will_replace_output) {
    issueRows.push({
      kind: 'warning',
      text: '输出位置已有同名文件。确认导出后将覆盖该文件。',
    });
  }
  if (!issueRows.length) issueRows.push({ kind: 'ok', text: '未发现阻断项或兼容性风险。' });
  document.getElementById('compose-check-issues').innerHTML = issueRows
    .map((item) => `<div class="compose-issue ${item.kind}">${esc(item.text)}</div>`)
    .join('');
  confirmComposeBtn.disabled = !result.ok || blockers.length > 0 || !result.preflight_token;
  setComposeView('check');
  requestAnimationFrame(() => {
    document.getElementById('compose-check-title').focus();
  });
}

runComposeBtn.addEventListener('click', async () => {
  if (composeRunning || !(state.manifest && state.output)) return;
  const verifyHash = verifyHashEl.checked;
  const options = { manifest: state.manifest, output: state.output, format: state.outputFormat, verifyHash };
  setComposeBusy(true);
  show(document.getElementById('compose-ok'), false);
  show(document.getElementById('compose-err'), false);
  show(document.getElementById('compose-prog'), true);
  document.querySelector('#compose-prog .prog-num').textContent = '正在检查来源与兼容性…';
  navState(3, 'blue');
  try {
    validateSelectedOutput(options.output);
    log('开始导出前检查…');
    const result = await window.pptlib.preflightCompose(options);
    if (state.manifest !== options.manifest || state.output !== options.output) {
      throw new Error('选片或输出位置已变化，请重新检查');
    }
    renderComposePreflight(result);
    log(result.ok ? '导出前检查通过' : '导出前检查发现阻断项', result.ok ? 'ok' : 'stderr');
    if (!result.ok) navState(3, 'gray');
  } catch (error) {
    log(`导出前检查失败：${error.message}`, 'stderr');
    toast(
      document.getElementById('compose-ok'),
      document.getElementById('compose-err'),
      'err',
      `检查失败：${error.message}`
    );
    navState(3, 'gray');
  } finally {
    show(document.getElementById('compose-prog'), false);
    setComposeBusy(false);
  }
});

document.getElementById('compose-back').addEventListener('click', () => {
  setComposeView('setup');
  runComposeBtn.focus();
});

function renderComposeResult(result) {
  state.composeResult = result;
  const format = selectedOutputFormat();
  const fidelity = result.fidelity_level ? `，保真等级 ${result.fidelity_level}` : '';
  document.getElementById('compose-result-copy').textContent =
    `${result.page_count} 页已按选片顺序生成${state.htmlEnabled ? ` ${format.label}` : ''}${fidelity}。` +
    (state.outputFormat === 'zip' ? '解压后打开 HTML 演示。' : '');
  document.getElementById('compose-open').textContent =
    state.outputFormat === 'zip' ? '打开资源包' : '打开文件';
  show(document.getElementById('compose-manifest'), Boolean(result.manifest_path));
  document.getElementById('compose-result-summary').innerHTML =
    `<span>输出文件</span><strong>${esc(result.output_path)}</strong>`;
  const warningEl = document.getElementById('compose-result-warnings');
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  warningEl.innerHTML = warnings
    .map((code) => `<div class="compose-issue warning">${esc(composeWarningCopy(code))}</div>`)
    .join('');
  show(warningEl, warnings.length > 0);
  setComposeView('result');
  requestAnimationFrame(() => document.getElementById('compose-result-title').focus());
}

confirmComposeBtn.addEventListener('click', async () => {
  if (composeRunning || !state.composePreflight?.ok || !state.composePreflight?.preflight_token ||
      state.composePreflight?.blockers?.length) return;
  const verifyHash = verifyHashEl.checked;
  setComposeBusy(true);
  show(document.getElementById('compose-err'), false);
  show(document.getElementById('compose-prog'), true);
  document.querySelector('#compose-prog .prog-num').textContent =
    state.htmlEnabled ? `正在生成 ${selectedOutputFormat().label} 并校验输出…` : '正在组合并校验输出…';
  navState(3, 'blue');
  try {
    validateSelectedOutput(state.output);
    log('开始组合…');
    const result = await window.pptlib.compose({
      manifest: state.manifest,
      output: state.output,
      format: state.outputFormat,
      verifyHash,
      preflightToken: state.composePreflight.preflight_token,
    });
    if (result.ok === false) throw new Error(result.message || '组合失败，请查看运行日志');
    log(
      `组合完成：${result.page_count} 页 ${selectedOutputFormat().label} → ${result.output_path}`,
      'ok',
    );
    renderComposeResult(result);
    localStorage.setItem('pinpage.onboarding.completed', '1');
    navState(3, 'green');
  } catch (error) {
    log(`组合失败：${error.message}`, 'stderr');
    toast(
      document.getElementById('compose-ok'),
      document.getElementById('compose-err'),
      'err',
      `组合失败：${error.message}`,
    );
    navState(3, 'gray');
  } finally {
    show(document.getElementById('compose-prog'), false);
    setComposeBusy(false);
  }
});

async function openComposePath(targetPath, label) {
  if (!targetPath) return;
  try {
    await window.pptlib.openPath(targetPath);
  } catch (error) {
    log(`${label}失败：${error.message}`, 'stderr');
    toast(
      document.getElementById('compose-ok'),
      document.getElementById('compose-err'),
      'err',
      `${label}失败：${error.message}`,
    );
  }
}

document.getElementById('compose-open').addEventListener('click', () => {
  openComposePath(state.composeResult?.output_path, '打开文件');
});
document.getElementById('compose-reveal').addEventListener('click', () => {
  const outputPath = state.composeResult?.output_path;
  if (!outputPath) return;
  window.pptlib.reveal(outputPath).catch((error) => {
    log(`在 Finder 中显示失败：${error.message}`, 'stderr');
    toast(
      document.getElementById('compose-ok'),
      document.getElementById('compose-err'),
      'err',
      `在 Finder 中显示失败：${error.message}`,
    );
  });
});
document.getElementById('compose-manifest').addEventListener('click', () => {
  openComposePath(state.composeResult?.manifest_path, '打开来源清单');
});
document.getElementById('compose-edit').addEventListener('click', () => {
  setComposeView('setup');
  goStep(2);
});
