'use strict';

const logEl = document.getElementById('log');
const logLastEl = document.getElementById('log-last');
const state = {
  importSources: [],
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
const pickFolderBtn = document.getElementById('pick-folder');
const runImportBtn = document.getElementById('run-import');
const importList = document.getElementById('import-files');
const importProgEl = document.getElementById('import-prog');
const importBarEl = document.getElementById('import-bar');
const importProgTextEl = document.getElementById('import-prog-text');
const importSummaryEl = document.getElementById('import-summary');
const FILE_ICON =
  '<span class="fico"><svg class="ico" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></span>';
const FOLDER_ICON =
  '<span class="fico"><svg class="ico" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></span>';

// Picked import sources: each is { path, isDir }. The backend accepts both
// files and directories (directories are scanned recursively for PPTX).
function renderImportSources() {
  importList.innerHTML = '';
  state.importSources.forEach((src) => {
    const li = document.createElement('li');
    const icon = src.isDir ? FOLDER_ICON : FILE_ICON;
    const label = src.path.split('/').pop() || src.path;
    li.innerHTML = `${icon}<div class="fname">${esc(label)}</div>` +
      (src.isDir ? '<span class="ftag">文件夹</span>' : '');
    importList.appendChild(li);
  });
  runImportBtn.disabled = state.importSources.length === 0;
}

pickImportBtn.addEventListener('click', async () => {
  const files = await window.pptlib.pickPptx();
  if (files.length) {
    state.importSources = files.map((path) => ({ path, isDir: false }));
    renderImportSources();
  }
});

pickFolderBtn.addEventListener('click', async () => {
  const folders = await window.pptlib.pickFolder();
  if (folders.length) {
    state.importSources = folders.map((path) => ({ path, isDir: true }));
    renderImportSources();
  }
});

// Live import progress, driven by the backend's per-page/per-file events.
function setImportProgress(event) {
  if (!event || !importProgEl) return;
  const { stage, index, total, name, page, pages } = event;
  let ratio = 0;
  let text = '准备中…';
  if (stage === 'scan') {
    text = total > 0 ? `发现 ${total} 个文件，开始导入…` : '未发现可导入的 PPTX';
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
    text = `(${index}/${total}) 完成：${name}${event.created ? '' : '（已存在，跳过）'}`;
  } else if (stage === 'error') {
    ratio = total ? index / total : 0;
    text = `(${index}/${total}) 失败：${name}`;
  }
  importBarEl.style.width = `${Math.round(Math.min(1, Math.max(0, ratio)) * 100)}%`;
  importProgTextEl.textContent = text;
}
window.pptlib.onProgress(setImportProgress);

// Render the post-import summary: new / duplicate(skipped) / failed.
function renderImportSummary(results) {
  const imported = results.flatMap((r) => r.imported || []);
  const failed = results.flatMap((r) => r.failed || []);
  const created = imported.filter((d) => d.created);
  const existing = imported.filter((d) => !d.created);
  const rows = [];
  rows.push(`<li class="ok">新增导入 <b>${created.length}</b> 个文件</li>`);
  if (existing.length) {
    const names = existing.map((d) => esc(d.name || (d.path || '').split('/').pop())).join('、');
    rows.push(`<li class="dup">已存在、自动跳过 <b>${existing.length}</b> 个：${names}</li>`);
  }
  if (failed.length) {
    const names = failed.map((f) => esc((f.path || '').split('/').pop())).join('、');
    rows.push(`<li class="err">失败 <b>${failed.length}</b> 个：${names}</li>`);
  }
  importSummaryEl.innerHTML = rows.join('');
  importSummaryEl.hidden = false;
}

runImportBtn.addEventListener('click', async () => {
  busy(runImportBtn, true);
  show(document.getElementById('import-ok'), false);
  show(document.getElementById('import-err'), false);
  importSummaryEl.hidden = true;
  importBarEl.style.width = '0%';
  importProgTextEl.textContent = '准备中…';
  show(importProgEl, true);
  navState(1, 'blue');
  try {
    const paths = state.importSources.map((s) => s.path);
    log(`开始导入 ${paths.length} 个来源…`);
    const results = await window.pptlib.import(paths);
    const imported = results.flatMap((r) => r.imported || []);
    const created = imported.filter((d) => d.created).length;
    const existing = imported.length - created;
    const failed = results.flatMap((r) => r.failed || []).length;
    log(`导入完成：新增 ${created}，已存在跳过 ${existing}，失败 ${failed}`, 'ok');
    renderImportSummary(results);
    toast(
      document.getElementById('import-ok'),
      document.getElementById('import-err'),
      'ok',
      `导入完成：新增 ${created} 个${existing ? `，已存在跳过 ${existing} 个` : ''}${failed ? `，失败 ${failed} 个` : ''}`
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
    show(importProgEl, false);
    busy(runImportBtn, false);
  }
});

// --- Step 2: local catalog grid selection -------------------------------- //
// Browse the local page library (real thumbnails), multi-select, reorder by
// dragging, then compose — entirely local, no upload/round-trip.

const catalog = { slides: [], byId: {}, decks: [] };
const selectedIds = [];
let currentDeckId = null; // which file's pages the grid shows
const gridEl = document.getElementById('grid');
const gridHeadEl = document.getElementById('grid-head');
const gridEmptyEl = document.getElementById('grid-empty');
const deckListEl = document.getElementById('deck-list');
const selListEl = document.getElementById('sel-list');
const selEmptyEl = document.getElementById('sel-empty');
const selCountEl = document.getElementById('sel-count');
const selExportBtn = document.getElementById('sel-export');
const filterTypeEl = document.getElementById('filter-type');
const gridSearchEl = document.getElementById('grid-search');

const TICK = '<span class="tick"><svg class="ico sm" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg></span>';
const GRIP = '<svg class="ico sm" viewBox="0 0 24 24"><circle cx="9" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="18" r="1"/></svg>';
const RM = '<svg class="ico sm" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>';
const TRASH = '<svg class="ico sm" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M10 11v6M14 11v6"/></svg>';
const DECK_ICON = '<svg class="ico sm" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>';
const ZOOM = '<svg class="ico sm" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3M11 8v6M8 11h6"/></svg>';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );
}

// Group slides into decks (files), preserving first-seen order.
function buildDecks() {
  const map = new Map();
  for (const s of catalog.slides) {
    if (!map.has(s.deck_id)) {
      map.set(s.deck_id, { deck_id: s.deck_id, deck_name: s.deck_name, slides: [] });
    }
    map.get(s.deck_id).slides.push(s);
  }
  catalog.decks = [...map.values()];
  if (!catalog.decks.some((d) => d.deck_id === currentDeckId)) {
    currentDeckId = catalog.decks.length ? catalog.decks[0].deck_id : null;
  }
}

function currentDeck() {
  return catalog.decks.find((d) => d.deck_id === currentDeckId) || null;
}

function fillFilters() {
  const types = [...new Set(catalog.slides.map((s) => s.page_type).filter(Boolean))].sort();
  filterTypeEl.innerHTML =
    '<option value="">全部类型</option>' + types.map((t) => `<option>${esc(t)}</option>`).join('');
}

// Pages shown in the grid = current deck, filtered by type + search.
function visibleSlides() {
  const deck = currentDeck();
  if (!deck) return [];
  const type = filterTypeEl.value;
  const q = gridSearchEl.value.trim().toLowerCase();
  return deck.slides.filter((s) => {
    if (type && s.page_type !== type) return false;
    if (q) {
      const hay = `${s.title} ${s.topic} ${s.subtopic}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function pic(s) {
  return s.thumbnail_url
    ? `<img class="pic" src="${esc(s.thumbnail_url)}" loading="lazy" alt="" />`
    : '<div class="pic ph">无缩略图</div>';
}

function renderDeckList() {
  deckListEl.innerHTML = catalog.decks
    .map((d) => {
      const picked = d.slides.filter((s) => selectedIds.includes(s.slide_id)).length;
      return (
        `<li data-deck="${esc(d.deck_id)}" class="${d.deck_id === currentDeckId ? 'on' : ''}">` +
        `<span class="dico">${DECK_ICON}</span>` +
        `<span class="dinfo"><div class="dname">${esc(d.deck_name)}</div>` +
        `<div class="dmeta">${d.slides.length} 页</div></span>` +
        `<span class="dpick"${picked ? '' : ' hidden'}>已选 ${picked}</span>` +
        `<button class="ddel" data-deck="${esc(d.deck_id)}" title="从页库移除该文件（不删源文件）">${TRASH}</button></li>`
      );
    })
    .join('');
  deckListEl.querySelectorAll('li').forEach((li) => {
    li.addEventListener('click', (e) => {
      if (e.target.closest('.ddel')) return; // delete button handles itself
      currentDeckId = li.dataset.deck;
      renderDeckList();
      renderGrid();
    });
  });
  deckListEl.querySelectorAll('.ddel').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      removeDeck(btn.dataset.deck);
    });
  });
}

function renderGrid() {
  const deck = currentDeck();
  const slides = visibleSlides();
  // header: current file name + count + select-all toggle
  if (deck) {
    const allSelected = slides.length > 0 && slides.every((s) => selectedIds.includes(s.slide_id));
    gridHeadEl.hidden = false;
    gridHeadEl.innerHTML =
      `<span class="gt">${esc(deck.deck_name)}</span>` +
      `<span class="gc">显示 ${slides.length} / ${deck.slides.length} 页</span>` +
      '<span class="spacer"></span>' +
      `<button class="btn sm" id="grid-selall">${allSelected ? '取消全选' : '全选本页'}</button>`;
    const selAll = document.getElementById('grid-selall');
    if (selAll) {
      selAll.addEventListener('click', () => {
        const ids = slides.map((s) => s.slide_id);
        if (allSelected) {
          for (const id of ids) {
            const i = selectedIds.indexOf(id);
            if (i >= 0) selectedIds.splice(i, 1);
          }
        } else {
          for (const id of ids) if (!selectedIds.includes(id)) selectedIds.push(id);
        }
        renderGrid();
        renderSelected();
        renderDeckList();
      });
    }
  } else {
    gridHeadEl.hidden = true;
  }
  gridEl.innerHTML = slides
    .map((s) => {
      const on = selectedIds.includes(s.slide_id);
      const tag = s.page_type || s.topic || '';
      return (
        `<div class="thumb${on ? ' sel' : ''}" data-id="${esc(s.slide_id)}">` +
        (tag ? `<span class="tag">${esc(tag)}</span>` : '') +
        `<button class="tzoom" data-id="${esc(s.slide_id)}" title="放大预览">${ZOOM}</button>` +
        `<button class="tdel" data-id="${esc(s.slide_id)}" title="从页库移除该页（不删源文件）">${TRASH}</button>` +
        TICK +
        pic(s) +
        `<div class="meta"><div class="t">${esc(s.title || '(无标题)')}</div>` +
        `<div class="s">p${s.slide_number}</div></div></div>`
      );
    })
    .join('');
  gridEl.querySelectorAll('.thumb').forEach((el) => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('.tdel') || e.target.closest('.tzoom')) return; // buttons handle themselves
      toggleSelect(el.dataset.id);
    });
  });
  gridEl.querySelectorAll('.tdel').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      removeSlide(btn.dataset.id);
    });
  });
  gridEl.querySelectorAll('.tzoom').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openLightbox(btn.dataset.id);
    });
  });
}

function renderSelected() {
  selCountEl.textContent = selectedIds.length;
  selExportBtn.disabled = selectedIds.length === 0;
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
        `<div class="s">${esc(s.deck_name)} · p${s.slide_number}</div></span>` +
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

function toggleSelect(id) {
  const i = selectedIds.indexOf(id);
  if (i >= 0) selectedIds.splice(i, 1);
  else selectedIds.push(id);
  renderGrid();
  renderSelected();
  renderDeckList();
}

// --- Fullscreen preview (lightbox) --------------------------------------- //
// Uses the high-resolution preview image; navigates within the currently
// visible grid order so ← / → walk the same pages you see.
const lightboxEl = document.getElementById('lightbox');
const lbImg = document.getElementById('lb-img');
const lbTitle = document.getElementById('lb-title');
const lbMeta = document.getElementById('lb-meta');
const lbToggleSelBtn = document.getElementById('lb-toggle-sel');
let lbList = []; // slide_ids in the order shown when the lightbox opened
let lbIndex = -1;

function openLightbox(id) {
  lbList = visibleSlides().map((s) => s.slide_id);
  lbIndex = lbList.indexOf(id);
  if (lbIndex < 0) {
    lbList = [id];
    lbIndex = 0;
  }
  showLightboxSlide();
  lightboxEl.hidden = false;
}

function showLightboxSlide() {
  const s = catalog.byId[lbList[lbIndex]];
  if (!s) return;
  lbImg.src = s.preview_url || s.thumbnail_url || '';
  lbTitle.textContent = s.title || '(无标题)';
  lbMeta.textContent = `${s.deck_name} · 第 ${s.slide_number} 页 · ${lbIndex + 1}/${lbList.length}`;
  const picked = selectedIds.includes(s.slide_id);
  lbToggleSelBtn.textContent = picked ? '移出选片' : '加入选片';
  lbToggleSelBtn.classList.toggle('primary', !picked);
}

function closeLightbox() {
  lightboxEl.hidden = true;
  lbImg.src = '';
}

function lbStep(delta) {
  if (lbList.length === 0) return;
  lbIndex = (lbIndex + delta + lbList.length) % lbList.length;
  showLightboxSlide();
}

document.getElementById('lb-prev').addEventListener('click', () => lbStep(-1));
document.getElementById('lb-next').addEventListener('click', () => lbStep(1));
document.getElementById('lb-close').addEventListener('click', closeLightbox);
document.getElementById('lb-backdrop').addEventListener('click', closeLightbox);
lbToggleSelBtn.addEventListener('click', () => {
  const id = lbList[lbIndex];
  if (id) {
    toggleSelect(id);
    showLightboxSlide(); // refresh the button label + keep viewing
  }
});
document.addEventListener('keydown', (e) => {
  if (lightboxEl.hidden) return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowLeft') lbStep(-1);
  else if (e.key === 'ArrowRight') lbStep(1);
});

// Drop a slide from every in-memory view after it's removed from the index.
function forgetSlide(id) {
  delete catalog.byId[id];
  catalog.slides = catalog.slides.filter((s) => s.slide_id !== id);
  const si = selectedIds.indexOf(id);
  if (si >= 0) selectedIds.splice(si, 1);
}

function repaintCatalog() {
  buildDecks();
  fillFilters();
  renderDeckList();
  renderGrid();
  renderSelected();
  if (catalog.slides.length === 0) {
    gridEl.hidden = true;
    gridEmptyEl.hidden = false;
  }
}

// Remove a whole file (deck) from the library. Source PPTX is never touched.
async function removeDeck(deckId) {
  const deck = catalog.decks.find((d) => d.deck_id === deckId);
  if (!deck) return;
  const ok = window.confirm(
    `从页库移除文件「${deck.deck_name}」的全部 ${deck.slides.length} 页？\n\n` +
      `只从本地页库移除索引与缩略图，不会删除你的原始 PPTX 文件。`,
  );
  if (!ok) return;
  try {
    const res = await window.pptlib.removeDeck(deckId);
    for (const s of deck.slides) forgetSlide(s.slide_id);
    if (currentDeckId === deckId) currentDeckId = null;
    repaintCatalog();
    log(`已从页库移除「${deck.deck_name}」：${(res && res.slides_removed) || deck.slides.length} 页`, 'ok');
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
      `只从本地页库移除，不会删除你的原始 PPTX 文件。`,
  );
  if (!ok) return;
  try {
    const res = await window.pptlib.removeSlide(slideId);
    forgetSlide(slideId);
    repaintCatalog();
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
      renderSelected();
    });
  });
}

async function loadCatalog() {
  const btn = document.getElementById('load-catalog');
  const reloadBtn = document.getElementById('reload-catalog');
  if (btn) busy(btn, true);
  if (reloadBtn) busy(reloadBtn, true);
  navState(2, 'blue');
  try {
    log('加载本地页库…');
    const res = await window.pptlib.loadCatalog();
    catalog.slides = res.slides || [];
    catalog.byId = {};
    catalog.slides.forEach((s) => (catalog.byId[s.slide_id] = s));
    // drop any previously-selected ids no longer present
    for (let i = selectedIds.length - 1; i >= 0; i--) {
      if (!catalog.byId[selectedIds[i]]) selectedIds.splice(i, 1);
    }
    buildDecks();
    fillFilters();
    renderDeckList();
    renderGrid();
    renderSelected();
    gridEmptyEl.hidden = catalog.slides.length > 0;
    gridEl.hidden = catalog.slides.length === 0;
    log(`页库已加载：${catalog.slides.length} 页，${catalog.decks.length} 个文件`, 'ok');
    navState(2, catalog.slides.length ? 'green' : 'gray');
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
// Auto-load the local library on startup so the grid is ready without a click.
// Silent: failures just leave the empty-state + "加载本地页库" button in place.
window.pptlib
  .loadCatalog()
  .then((res) => {
    if (!res || !res.slides || res.slides.length === 0) return;
    catalog.slides = res.slides;
    catalog.byId = {};
    catalog.slides.forEach((s) => (catalog.byId[s.slide_id] = s));
    buildDecks();
    fillFilters();
    renderDeckList();
    renderGrid();
    renderSelected();
    gridEmptyEl.hidden = true;
    gridEl.hidden = false;
    navState(2, 'green');
    log(`页库已就绪：${catalog.slides.length} 页，${catalog.decks.length} 个文件`, 'ok');
  })
  .catch(() => {
    /* no library yet — leave the empty state's load button for the user */
  });
document.getElementById('sel-clear').addEventListener('click', () => {
  selectedIds.length = 0;
  renderGrid();
  renderSelected();
  renderDeckList();
});
filterTypeEl.addEventListener('change', renderGrid);
gridSearchEl.addEventListener('input', renderGrid);

// "用选片进入组合": persist the ordered ids as a manifest, arm compose, jump.
selExportBtn.addEventListener('click', async () => {
  if (selectedIds.length === 0) return;
  busy(selExportBtn, true);
  try {
    const res = await window.pptlib.writeManifest(selectedIds.slice());
    state.manifest = res.path;
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
  }
});

// --- Step 3: compose ----------------------------------------------------- //

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
  navState(3, 'blue');
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
    navState(3, 'green');
    window.pptlib.reveal(res.output_path);
  } catch (error) {
    log(`组合失败：${error.message}`, 'stderr');
    toast(
      document.getElementById('compose-ok'),
      document.getElementById('compose-err'),
      'err',
      `组合失败：${error.message}`
    );
    navState(3, 'gray');
  } finally {
    show(document.getElementById('compose-prog'), false);
    busy(runComposeBtn, false);
  }
});
