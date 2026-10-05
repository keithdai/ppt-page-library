'use strict';

(() => {
  const api = window.pptlib;
  const els = {};
  const autoState = {
    plans: [],
    history: [],
    currentTask: null,
    roots: [],
    expandedPlans: new Set(),
    preflight: null,
    preflightIntent: null,
    elapsedTimer: null,
    previewRequest: 0,
    plansLoadFailed: false,
  };

  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? '' : value).replace(
    /[&<>"']/g,
    (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]),
  );
  const setVisible = (element, visible) => {
    if (element) element.hidden = !visible;
  };
  const writeLog = (message, kind) => {
    if (typeof log === 'function') log(message, kind);
  };
  const setNavState = (kind) => {
    if (typeof navState === 'function') navState(5, kind);
  };

  function collectElements() {
    [
      'auto-status-indicator', 'auto-status-title', 'auto-status-copy', 'auto-next-run',
      'auto-new-plan', 'auto-empty-create', 'auto-preview-all', 'auto-run-all',
      'auto-plans-loading', 'auto-plans-empty', 'auto-plan-table-wrap', 'auto-plan-rows',
      'auto-error', 'auto-live', 'auto-running-dot', 'auto-running-empty',
      'auto-running-content', 'auto-run-state', 'auto-run-title', 'auto-run-context',
      'auto-stop-run', 'auto-run-progress', 'auto-run-bar', 'auto-run-percent',
      'auto-run-file', 'auto-run-files', 'auto-run-pages', 'auto-run-elapsed',
      'auto-history-count', 'auto-refresh-history', 'auto-history-empty',
      'auto-history-list', 'auto-editor', 'auto-editor-form', 'auto-editor-title',
      'auto-editor-close', 'auto-editor-cancel', 'auto-plan-id', 'auto-plan-name',
      'auto-editor-preview',
      'auto-name-error', 'auto-root-list', 'auto-add-root', 'auto-root-error',
      'auto-format-pptx', 'auto-format-html', 'auto-format-zip', 'auto-min-size',
      'auto-max-size', 'auto-rule-summary', 'auto-frequency', 'auto-schedule-time',
      'auto-window-start', 'auto-window-end', 'auto-repair-previews', 'auto-enabled',
      'auto-editor-error', 'auto-preflight', 'auto-preflight-copy',
      'auto-preflight-close', 'auto-preflight-cancel', 'auto-preflight-summary',
      'auto-preflight-roots', 'auto-preflight-error', 'auto-preflight-confirm',
    ].forEach((id) => { els[id] = byId(id); });
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(value % 1024 ** 3 ? 1 : 0)} GB`;
    if (value >= 1024 ** 2) return `${Math.round(value / 1024 ** 2)} MB`;
    if (value >= 1024) return `${Math.round(value / 1024)} KB`;
    return `${value} B`;
  }

  function formatDate(value, withTime = true) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
      month: 'numeric',
      day: 'numeric',
      hour: withTime ? '2-digit' : undefined,
      minute: withTime ? '2-digit' : undefined,
      hour12: false,
    }).format(date);
  }

  function formatDuration(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    const minutes = Math.floor(seconds / 60);
    return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  }

  function formatLabels(formats) {
    const labels = { pptx: 'PPTX', html: 'HTML / HTM', html_zip: 'HTML Deck ZIP' };
    return (formats || []).map((format) => labels[format] || format).join('、');
  }

  function summarizeStats(stats = {}) {
    const parts = [];
    const mapping = [
      ['created', '新增'], ['updated', '更新'], ['moved', '移动'],
      ['repair_preview', '修复'], ['unchanged', '未变化'], ['missing', '源不可用'],
      ['below_minimum', '小于下限'], ['above_maximum', '超限'],
      ['unsupported', '格式不支持'], ['unstable', '仍在写入'],
      ['cancelled', '取消'], ['failed', '失败'],
    ];
    mapping.forEach(([key, label]) => {
      if (stats[key]) parts.push(`${label} ${stats[key]}`);
    });
    return parts.join('、') || '无变化';
  }

  function resultLabel(key) {
    const labels = {
      created: '新增',
      updated: '更新',
      moved: '路径移动',
      repair_preview: '修复预览',
      unchanged: '未变化',
      missing: '源文件不可用',
      below_minimum: '小于下限',
      above_maximum: '超过上限',
      unsupported: '格式不支持',
      unstable: '仍在写入',
      temporary: '临时文件',
      failed: '失败',
      cancelled: '已取消',
    };
    return labels[key] || key;
  }

  function announce(message) {
    els['auto-live'].textContent = '';
    requestAnimationFrame(() => { els['auto-live'].textContent = message; });
  }

  function showError(message, target = els['auto-error']) {
    target.textContent = message;
    setVisible(target, true);
    announce(message);
  }

  function clearError(target = els['auto-error']) {
    target.textContent = '';
    setVisible(target, false);
  }

  function switchAutoView(view) {
    document.querySelectorAll('[data-auto-view]').forEach((tab) => {
      const active = tab.dataset.autoView === view;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll('.auto-panel').forEach((panel) => {
      const active = panel.id === `auto-panel-${view}`;
      panel.classList.toggle('active', active);
      panel.hidden = !active;
    });
    if (view === 'history') loadHistory();
  }

  function nextRun(plans) {
    const scheduled = plans.filter((plan) => plan.enabled && plan.scheduleKind === 'daily');
    if (!scheduled.length) return '暂无';
    const now = new Date();
    return scheduled
      .map((plan) => {
        const [hour, minute] = plan.scheduleTime.split(':').map(Number);
        const target = new Date(now);
        target.setHours(hour, minute, 0, 0);
        if (target <= now) target.setDate(target.getDate() + 1);
        return { target, label: `${target.toDateString() === now.toDateString() ? '今天' : '明天'} ${plan.scheduleTime}` };
      })
      .sort((a, b) => a.target - b.target)[0].label;
  }

  function updateStatusBand() {
    if (autoState.plansLoadFailed) {
      els['auto-status-indicator'].className = 'status-indicator warning';
      els['auto-status-title'].textContent = '扫描计划读取失败';
      els['auto-status-copy'].textContent = '当前数据可能已过期，请重试后再执行更新。';
      els['auto-next-run'].textContent = '未知';
      els['auto-run-all'].disabled = true;
      els['auto-preview-all'].disabled = true;
      setNavState('gray');
      return;
    }
    const enabled = autoState.plans.filter((plan) => plan.enabled).length;
    const missing = autoState.plans.some(
      (plan) => plan.roots.some((root) => root.authorizationStatus !== 'ok'),
    );
    els['auto-status-indicator'].className =
      `status-indicator ${missing ? 'warning' : enabled ? 'on' : 'off'}`;
    els['auto-status-title'].textContent = missing
      ? '部分文件夹需要重新授权'
      : enabled
        ? `自动更新已开启 · ${enabled} 个计划`
        : '自动更新已暂停';
    els['auto-status-copy'].textContent = enabled
      ? '拼页运行且电脑保持唤醒时，计划会在执行窗口内开始。'
      : '开启计划后才会自动执行；立即更新仍可手动使用。';
    els['auto-next-run'].textContent = nextRun(autoState.plans);
    els['auto-run-all'].disabled = enabled === 0 || Boolean(autoState.currentTask);
    els['auto-preview-all'].disabled = enabled === 0 || Boolean(autoState.currentTask);
    setNavState(missing ? 'blue' : enabled ? 'green' : 'gray');
  }

  function planLastResult(plan) {
    const latest = [...plan.roots]
      .filter((root) => root.lastScannedAt)
      .sort((a, b) => String(b.lastScannedAt).localeCompare(String(a.lastScannedAt)))[0];
    return latest ? summarizeStats(latest.lastResult) : '尚未运行';
  }

  function planRow(plan) {
    const expanded = autoState.expandedPlans.has(plan.id);
    const missing = plan.roots.some((root) => root.authorizationStatus !== 'ok');
    const formatRange =
      `${formatLabels(plan.formats)} · ${formatBytes(plan.minFileBytes)}–${formatBytes(plan.maxFileBytes)}`;
    const schedule = plan.scheduleKind === 'manual' ? '仅手动' : `每天 ${plan.scheduleTime}`;
    const status = missing ? '需授权' : plan.enabled ? '开启' : '暂停';
    const statusClass = missing ? 'warning' : plan.enabled ? 'success' : 'muted';
    const rootRows = plan.roots.map((root) => (
      `<div class="auto-root-detail${root.authorizationStatus !== 'ok' ? ' has-error' : ''}">` +
        `<div class="auto-root-path"><strong>${escapeHtml(root.name)}</strong>` +
        `<span title="${escapeHtml(root.path)}">${escapeHtml(root.path)}</span></div>` +
        `<div>${root.lastScannedAt ? formatDate(root.lastScannedAt) : '尚未扫描'}</div>` +
        `<div>${root.authorizationStatus === 'ok' ? summarizeStats(root.lastResult) : '需重新授权'}</div>` +
        `<button class="btn ghost sm" type="button" data-root-run="${escapeHtml(root.id)}" ` +
        `data-plan="${escapeHtml(plan.id)}" ` +
        `${autoState.currentTask || root.authorizationStatus !== 'ok' ? 'disabled' : ''} ` +
        `title="${root.authorizationStatus === 'ok' ? '立即更新此文件夹' : '请先编辑计划并重新选择文件夹'}">` +
        `${root.authorizationStatus === 'ok' ? '立即更新' : '需重新授权'}</button>` +
      '</div>'
    )).join('');
    return (
      `<tr class="auto-plan-row${expanded ? ' expanded' : ''}">` +
        `<td><button class="plan-expand" type="button" data-expand="${escapeHtml(plan.id)}" ` +
        `aria-expanded="${expanded}" aria-controls="roots-${escapeHtml(plan.id)}">` +
        '<svg class="ico sm" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>' +
        `<span><strong>${escapeHtml(plan.name)}</strong><small>${escapeHtml(plan.roots[0]?.path || '')}</small></span>` +
        '</button></td>' +
        `<td>${plan.roots.length} 个目录</td>` +
        `<td title="${escapeHtml(formatRange)}">${escapeHtml(formatRange)}</td>` +
        `<td>${escapeHtml(schedule)}<small>窗口 ${escapeHtml(plan.windowStart)}–${escapeHtml(plan.windowEnd)}</small></td>` +
        `<td>${escapeHtml(planLastResult(plan))}</td>` +
        `<td><span class="status-badge ${statusClass}">${status}</span></td>` +
        '<td><div class="row-actions">' +
          `<button class="btn ghost sm" type="button" data-plan-preview="${escapeHtml(plan.id)}">预检</button>` +
          `<button class="btn sm" type="button" data-plan-run="${escapeHtml(plan.id)}" ` +
          `${autoState.currentTask || missing ? 'disabled' : ''}>立即更新</button>` +
          `<button class="btn ghost sm" type="button" data-plan-toggle="${escapeHtml(plan.id)}" ` +
          `${autoState.currentTask ? 'disabled' : ''}>${plan.enabled ? '暂停' : '开启'}</button>` +
          `<button class="icon-btn" type="button" data-plan-edit="${escapeHtml(plan.id)}" aria-label="编辑 ${escapeHtml(plan.name)}">` +
          '<svg class="ico sm" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></button>' +
          `<button class="icon-btn danger-icon" type="button" data-plan-delete="${escapeHtml(plan.id)}" aria-label="删除 ${escapeHtml(plan.name)}">` +
          '<svg class="ico sm" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2m3 0-1 15H6L5 6"/></svg></button>' +
        '</div></td>' +
      '</tr>' +
      `<tr id="roots-${escapeHtml(plan.id)}" class="auto-root-detail-row"${expanded ? '' : ' hidden'}>` +
        `<td colspan="7"><div class="auto-root-detail-head"><span>文件夹</span><span>最近扫描</span><span>最近结果</span><span>操作</span></div>${rootRows}</td>` +
      '</tr>'
    );
  }

  function bindPlanRows() {
    els['auto-plan-rows'].querySelectorAll('[data-expand]').forEach((button) => {
      button.addEventListener('click', () => {
        const id = button.dataset.expand;
        if (autoState.expandedPlans.has(id)) autoState.expandedPlans.delete(id);
        else autoState.expandedPlans.add(id);
        renderPlans();
      });
    });
    els['auto-plan-rows'].querySelectorAll('[data-plan-edit]').forEach((button) => {
      button.addEventListener('click', () => openEditor(findPlan(button.dataset.planEdit)));
    });
    els['auto-plan-rows'].querySelectorAll('[data-plan-delete]').forEach((button) => {
      button.addEventListener('click', () => removePlan(button.dataset.planDelete));
    });
    els['auto-plan-rows'].querySelectorAll('[data-plan-toggle]').forEach((button) => {
      button.addEventListener('click', async () => {
        const plan = findPlan(button.dataset.planToggle);
        if (!plan) return;
        button.disabled = true;
        try {
          await api.setScanPlanEnabled(plan.id, !plan.enabled);
          await loadPlans();
          announce(plan.enabled ? '扫描计划已暂停' : '扫描计划已开启');
        } catch (error) {
          showError(`${plan.enabled ? '暂停' : '开启'}计划失败：${error.message}`);
        }
      });
    });
    els['auto-plan-rows'].querySelectorAll('[data-plan-preview]').forEach((button) => {
      button.addEventListener('click', () => preflightPlan(findPlan(button.dataset.planPreview), 'preview'));
    });
    els['auto-plan-rows'].querySelectorAll('[data-plan-run]').forEach((button) => {
      button.addEventListener('click', () => preflightPlan(findPlan(button.dataset.planRun), 'run'));
    });
    els['auto-plan-rows'].querySelectorAll('[data-root-run]').forEach((button) => {
      button.addEventListener('click', () => {
        const plan = findPlan(button.dataset.plan);
        const root = plan?.roots.find((item) => item.id === button.dataset.rootRun);
        if (plan && root) preflightPlan({ ...plan, roots: [root] }, 'run-root', root.id);
      });
    });
  }

  function renderPlans() {
    const hasPlans = autoState.plans.length > 0;
    setVisible(els['auto-plans-loading'], false);
    setVisible(els['auto-plans-empty'], !hasPlans);
    setVisible(els['auto-plan-table-wrap'], hasPlans);
    els['auto-plan-rows'].innerHTML = autoState.plans.map(planRow).join('');
    bindPlanRows();
    updateStatusBand();
  }

  function findPlan(id) {
    return autoState.plans.find((plan) => plan.id === id);
  }

  async function loadPlans() {
    clearError();
    try {
      const result = await api.listScanPlans();
      autoState.plansLoadFailed = false;
      autoState.plans = result?.plans || [];
      renderPlans();
    } catch (error) {
      autoState.plansLoadFailed = true;
      autoState.plans = [];
      renderPlans();
      els['auto-error'].innerHTML =
        `<span>读取扫描计划失败：${escapeHtml(error.message)}</span>` +
        '<button id="auto-retry-plans" class="btn sm" type="button">重试</button>';
      setVisible(els['auto-error'], true);
      byId('auto-retry-plans').addEventListener('click', loadPlans);
    }
  }

  function resetEditor(plan = null) {
    els['auto-editor-form'].reset();
    els['auto-plan-id'].value = plan?.id || '';
    els['auto-plan-name'].value = plan?.name || '';
    els['auto-format-pptx'].checked = !plan || plan.formats.includes('pptx');
    els['auto-format-html'].checked = Boolean(plan?.formats.includes('html'));
    els['auto-format-zip'].checked = Boolean(plan?.formats.includes('html_zip'));
    els['auto-min-size'].value = plan ? Math.round(plan.minFileBytes / 1024 ** 2) : 0;
    els['auto-max-size'].value = plan ? Math.round(plan.maxFileBytes / 1024 ** 2) : 3072;
    els['auto-frequency'].value = plan?.scheduleKind || 'daily';
    els['auto-schedule-time'].value = plan?.scheduleTime || '02:30';
    els['auto-window-start'].value = plan?.windowStart || '02:00';
    els['auto-window-end'].value = plan?.windowEnd || '05:00';
    els['auto-repair-previews'].checked = plan?.repairPreviews !== false;
    els['auto-enabled'].checked = plan?.enabled !== false;
    autoState.roots = (plan?.roots || []).map((root) => ({ ...root }));
    clearError(els['auto-editor-error']);
    clearError(els['auto-root-error']);
    clearError(els['auto-name-error']);
    renderEditorRoots();
    updateRuleSummary();
  }

  function openEditor(plan = null) {
    resetEditor(plan);
    els['auto-editor-title'].textContent = plan ? '编辑扫描计划' : '新建扫描计划';
    els['auto-editor'].showModal();
    requestAnimationFrame(() => els['auto-plan-name'].focus());
  }

  function closeEditor() {
    els['auto-editor'].close();
  }

  function renderEditorRoots() {
    els['auto-root-list'].innerHTML = autoState.roots.length
      ? autoState.roots.map((root, index) => (
        `<div class="auto-root-edit">` +
          '<svg class="ico" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>' +
          `<div><strong>${escapeHtml(root.name)}</strong><span title="${escapeHtml(root.path)}">${escapeHtml(root.path)}</span></div>` +
          `<label class="check"><input type="checkbox" data-root-recursive="${index}" ${root.recursive !== false ? 'checked' : ''}/> 子文件夹</label>` +
          `<button class="icon-btn danger-icon" type="button" data-root-remove="${index}" aria-label="移除 ${escapeHtml(root.name)}">` +
          '<svg class="ico sm" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></button>' +
        '</div>'
      )).join('')
      : '<div class="root-placeholder">尚未添加文件夹</div>';
    els['auto-root-list'].querySelectorAll('[data-root-recursive]').forEach((input) => {
      input.addEventListener('change', () => {
        autoState.roots[Number(input.dataset.rootRecursive)].recursive = input.checked;
        updateRuleSummary();
      });
    });
    els['auto-root-list'].querySelectorAll('[data-root-remove]').forEach((button) => {
      button.addEventListener('click', () => {
        autoState.roots.splice(Number(button.dataset.rootRemove), 1);
        renderEditorRoots();
        updateRuleSummary();
      });
    });
  }

  function editorPayload() {
    const formats = [];
    if (els['auto-format-pptx'].checked) formats.push('pptx');
    if (els['auto-format-html'].checked) formats.push('html');
    if (els['auto-format-zip'].checked) formats.push('html_zip');
    return {
      id: els['auto-plan-id'].value,
      name: els['auto-plan-name'].value.trim(),
      enabled: els['auto-enabled'].checked,
      scheduleKind: els['auto-frequency'].value,
      scheduleTime: els['auto-schedule-time'].value,
      windowStart: els['auto-window-start'].value,
      windowEnd: els['auto-window-end'].value,
      formats,
      minFileBytes: Math.round(Number(els['auto-min-size'].value) * 1024 ** 2),
      maxFileBytes: Math.round(Number(els['auto-max-size'].value) * 1024 ** 2),
      stabilitySeconds: 60,
      missingPolicy: 'keep',
      repairPreviews: els['auto-repair-previews'].checked,
      roots: autoState.roots.map((root) => ({ ...root })),
    };
  }

  function validateEditor(payload) {
    clearError(els['auto-name-error']);
    clearError(els['auto-root-error']);
    if (!payload.name) {
      showError('请输入计划名称', els['auto-name-error']);
      els['auto-plan-name'].focus();
      return false;
    }
    if (!payload.roots.length) {
      showError('至少添加一个扫描文件夹', els['auto-root-error']);
      return false;
    }
    if (!payload.formats.length) {
      showError('至少选择一种文件格式', els['auto-editor-error']);
      return false;
    }
    if (payload.minFileBytes < 0 || payload.maxFileBytes <= payload.minFileBytes) {
      showError('最大文件大小必须大于最小值', els['auto-editor-error']);
      return false;
    }
    const toMinutes = (value) => {
      const [hour, minute] = value.split(':').map(Number);
      return hour * 60 + minute;
    };
    const scheduled = toMinutes(payload.scheduleTime);
    const start = toMinutes(payload.windowStart);
    const end = toMinutes(payload.windowEnd);
    const inWindow = start <= end
      ? scheduled >= start && scheduled <= end
      : scheduled >= start || scheduled <= end;
    if (payload.scheduleKind === 'daily' && !inWindow) {
      showError('执行时间必须位于执行窗口内', els['auto-editor-error']);
      return false;
    }
    return true;
  }

  function updateRuleSummary() {
    const payload = editorPayload();
    const recursion = payload.roots.some((root) => root.recursive)
      ? '包含子文件夹'
      : '仅当前文件夹';
    els['auto-rule-summary'].textContent =
      `${formatLabels(payload.formats) || '未选择格式'} · ` +
      `${formatBytes(payload.minFileBytes)}–${formatBytes(payload.maxFileBytes)} · ${recursion}`;
  }

  async function addRoots() {
    const paths = await api.pickAutoUpdateFolders();
    const known = new Set(autoState.roots.map((root) => root.path));
    paths.forEach((rootPath) => {
      if (known.has(rootPath)) return;
      known.add(rootPath);
      autoState.roots.push({
        name: rootPath.split('/').filter(Boolean).pop() || rootPath,
        path: rootPath,
        recursive: true,
      });
    });
    renderEditorRoots();
    updateRuleSummary();
  }

  function countValue(preview, key) {
    return Number(preview?.counts?.[key] || 0);
  }

  function renderPreflight(previews) {
    const combined = {};
    previews.forEach((preview) => {
      Object.entries(preview.counts || {}).forEach(([key, value]) => {
        combined[key] = (combined[key] || 0) + Number(value || 0);
      });
    });
    const tiles = [
      ['created', '新增'], ['updated', '更新'], ['moved', '路径移动'],
      ['repair_preview', '修复预览'], ['unchanged', '未变化'],
      ['above_maximum', '超过上限'], ['unsupported', '格式不支持'], ['failed', '失败'],
    ];
    els['auto-preflight-summary'].innerHTML = tiles.map(([key, label]) => (
      `<div><span>${label}</span><strong>${combined[key] || 0}</strong></div>`
    )).join('');
    els['auto-preflight-roots'].innerHTML = previews.flatMap((preview) =>
      (preview.roots || []).map((root) => (
        '<details class="preflight-root">' +
          `<summary><span><strong>${escapeHtml(root.root.name)}</strong>` +
          `<small>${escapeHtml(root.root.path)}</small></span>` +
          `<span>${escapeHtml(summarizeStats(root.counts))}</span></summary>` +
          `<div class="preflight-reasons">${Object.entries(root.counts || {}).map(
            ([key, value]) => `<span>${escapeHtml(resultLabel(key))} <b>${value}</b></span>`,
          ).join('')}</div>` +
        '</details>'
      )),
    ).join('');
    const blockers = previews.flatMap((preview) => preview.blockers || []);
    if (blockers.length) showError(blockers.join('；'), els['auto-preflight-error']);
    else clearError(els['auto-preflight-error']);
    els['auto-preflight-confirm'].disabled = blockers.length > 0;
  }

  function showPreflight(previews, intent) {
    autoState.preflight = previews;
    autoState.preflightIntent = intent;
    renderPreflight(previews);
    const changeCount = previews.reduce(
      (total, preview) => total +
        countValue(preview, 'created') +
        countValue(preview, 'updated') +
        countValue(preview, 'moved') +
        countValue(preview, 'repair_preview'),
      0,
    );
    if (intent.mode === 'save') {
      els['auto-preflight-copy'].textContent = '确认后保存计划；预检本身不会修改页库。';
      els['auto-preflight-confirm'].textContent = '确认并保存';
    } else if (intent.mode === 'preview') {
      els['auto-preflight-copy'].textContent = '这是只读预检，不会修改页库。';
      els['auto-preflight-confirm'].textContent = '完成';
    } else {
      els['auto-preflight-copy'].textContent = '确认后立即开始，不受定时窗口限制。';
      els['auto-preflight-confirm'].textContent = `更新 ${changeCount} 个文件`;
    }
    els['auto-preflight'].showModal();
  }

  async function previewEditor(event) {
    event.preventDefault();
    const payload = editorPayload();
    if (!validateEditor(payload)) return;
    clearError(els['auto-editor-error']);
    els['auto-editor-preview'].disabled = true;
    els['auto-editor-preview'].textContent = '正在预检…';
    const requestId = ++autoState.previewRequest;
    try {
      const preview = await api.previewScanPlan(payload);
      if (requestId !== autoState.previewRequest) return;
      closeEditor();
      showPreflight([preview], { mode: 'save', payload });
    } catch (error) {
      if (requestId !== autoState.previewRequest) return;
      showError(`预检失败：${error.message}`, els['auto-editor-error']);
    } finally {
      els['auto-editor-preview'].disabled = false;
      els['auto-editor-preview'].textContent = '预检并继续';
    }
  }

  async function preflightPlan(plan, mode, rootId = null) {
    if (!plan) return;
    clearError();
    try {
      const preview = await api.previewScanPlan(plan);
      showPreflight([preview], { mode, planId: plan.id, rootId });
    } catch (error) {
      showError(`预检失败：${error.message}`);
    }
  }

  async function preflightAll(mode) {
    const plans = autoState.plans.filter((plan) => plan.enabled);
    if (!plans.length) return;
    clearError();
    els['auto-run-all'].disabled = true;
    els['auto-preview-all'].disabled = true;
    try {
      const previews = [];
      for (const plan of plans) previews.push(await api.previewScanPlan(plan));
      showPreflight(previews, { mode });
    } catch (error) {
      showError(`预检失败：${error.message}`);
    } finally {
      updateStatusBand();
    }
  }

  async function confirmPreflight() {
    const intent = autoState.preflightIntent;
    if (!intent) return;
    els['auto-preflight-confirm'].disabled = true;
    try {
      if (intent.mode === 'preview') {
        els['auto-preflight'].close();
        return;
      }
      if (intent.mode === 'save') {
        const preview = autoState.preflight[0];
        const payload = { ...intent.payload, previewToken: preview.previewToken };
        if (payload.id) await api.updateScanPlan(payload);
        else await api.createScanPlan(payload);
        els['auto-preflight'].close();
        await loadPlans();
        announce('扫描计划已保存');
        writeLog(`扫描计划“${payload.name}”已保存`, 'ok');
        return;
      }
      els['auto-preflight'].close();
      switchAutoView('running');
      if (intent.mode === 'run-all') {
        await api.runAllScanPlans();
      } else {
        await api.runScanPlan({ planId: intent.planId, rootId: intent.rootId });
      }
      await Promise.all([loadPlans(), loadHistory()]);
      announce('自动更新任务已完成');
    } catch (error) {
      if (!els['auto-preflight'].open) els['auto-preflight'].showModal();
      showError(`操作失败：${error.message}`, els['auto-preflight-error']);
    } finally {
      els['auto-preflight-confirm'].disabled = false;
    }
  }

  async function removePlan(planId) {
    const plan = findPlan(planId);
    if (!plan || !window.confirm(`删除扫描计划“${plan.name}”？\n\n运行记录会保留，页库内容不会删除。`)) return;
    try {
      await api.deleteScanPlan(planId);
      await loadPlans();
      announce('扫描计划已删除');
    } catch (error) {
      showError(`删除计划失败：${error.message}`);
    }
  }

  function progressRatio(progress) {
    if (!progress) return 0;
    const total = Number(progress.total || 0);
    const index = Number(progress.index || 0);
    if (!total) return 0;
    if (progress.stage === 'render') {
      const pages = Number(progress.pages || 0);
      const page = Number(progress.page || 0);
      return Math.min(1, (index - 1 + (pages ? page / pages : 0)) / total);
    }
    return Math.min(1, (progress.stage === 'file_done' ? index : Math.max(0, index - 1)) / total);
  }

  function renderCurrentTask() {
    const task = autoState.currentTask;
    const active = Boolean(task);
    setVisible(els['auto-running-empty'], !active);
    setVisible(els['auto-running-content'], active);
    setVisible(els['auto-running-dot'], active);
    if (!active) {
      clearInterval(autoState.elapsedTimer);
      autoState.elapsedTimer = null;
      updateStatusBand();
      return;
    }
    const progress = task.progress || {};
    const ratio = progressRatio(progress);
    const percent = Math.round(ratio * 100);
    els['auto-run-title'].textContent = task.kind;
    els['auto-run-state'].textContent = task.state === 'stopping' ? '正在停止' : '运行中';
    els['auto-run-state'].className = `status-badge ${task.state === 'stopping' ? 'warning' : 'running'}`;
    els['auto-run-context'].textContent = progress.rootTotal
      ? `文件夹 ${progress.rootIndex}/${progress.rootTotal}`
      : '当前为全局重任务，其他导入、组合和扫描会等待。';
    els['auto-run-file'].textContent = progress.name || '正在准备…';
    els['auto-run-file'].title = progress.name || '';
    els['auto-run-files'].textContent = `${progress.index || 0} / ${progress.total || 0}`;
    els['auto-run-pages'].textContent = progress.pages
      ? `${progress.page || 0} / ${progress.pages}`
      : '—';
    els['auto-run-bar'].style.width = `${percent}%`;
    els['auto-run-percent'].textContent = `${percent}%`;
    els['auto-run-progress'].setAttribute('aria-valuenow', String(percent));
    els['auto-stop-run'].disabled = task.state === 'stopping';
    els['auto-stop-run'].textContent = task.state === 'stopping' ? '正在停止…' : '停止任务';
    if (!autoState.elapsedTimer) {
      autoState.elapsedTimer = setInterval(() => {
        if (!autoState.currentTask) return;
        els['auto-run-elapsed'].textContent = formatDuration(
          Date.now() - new Date(autoState.currentTask.startedAt).getTime(),
        );
      }, 1000);
    }
    updateStatusBand();
  }

  async function refreshCurrentTask() {
    try {
      const result = await api.currentScanRun();
      autoState.currentTask = result?.task || null;
      renderCurrentTask();
    } catch (error) {
      showError(`读取任务状态失败：${error.message}`);
    }
  }

  function runStatusLabel(status) {
    const labels = {
      completed: '已完成', partial: '部分成功', failed: '已失败',
      cancelled: '已取消', interrupted: '已中断', missed: '已错过',
      running: '运行中', stopping: '正在停止', preflighting: '预检中',
    };
    return labels[status] || status;
  }

  function renderHistory() {
    els['auto-history-count'].textContent = `最近 ${autoState.history.length} 次运行`;
    setVisible(els['auto-history-empty'], autoState.history.length === 0);
    setVisible(els['auto-history-list'], autoState.history.length > 0);
    els['auto-history-list'].innerHTML = autoState.history.map((run) => {
      const failed = (run.items || []).filter((item) => item.decision === 'failed');
      const duration = run.finishedAt
        ? new Date(run.finishedAt).getTime() - new Date(run.startedAt).getTime()
        : 0;
      return (
        '<details class="history-run">' +
          '<summary>' +
            `<span class="status-badge ${escapeHtml(run.status)}">${escapeHtml(runStatusLabel(run.status))}</span>` +
            `<span class="history-main"><strong>${escapeHtml(run.planName)}</strong>` +
            `<small>${run.triggerType === 'scheduled' ? '定时执行' : run.triggerType === 'retry' ? '失败重试' : '手动立即更新'} · ${formatDate(run.startedAt)}</small></span>` +
            `<span class="history-stats">${escapeHtml(summarizeStats(run.stats))}</span>` +
            `<span class="history-duration">${formatDuration(duration)}</span>` +
          '</summary>' +
          '<div class="history-detail">' +
            (run.errorMessage
              ? `<p class="history-error">${escapeHtml(run.errorMessage)}</p>`
              : '') +
            ((run.items || []).length
              ? run.items.map((item) => (
                `<div class="history-item ${escapeHtml(item.decision)}">` +
                `<span title="${escapeHtml(item.path)}">${escapeHtml(item.path.split('/').pop())}</span>` +
                `<span>${escapeHtml(resultLabel(item.action || item.decision))}</span>` +
                `<span>${escapeHtml(item.reason || `${item.slideCount || 0} 页`)}</span></div>`
              )).join('')
              : '<p>本次运行没有文件明细。</p>') +
            (failed.length && run.planId
              ? `<button class="btn sm" type="button" data-run-retry="${escapeHtml(run.id)}">重试失败项</button>`
              : '') +
          '</div>' +
        '</details>'
      );
    }).join('');
    els['auto-history-list'].querySelectorAll('[data-run-retry]').forEach((button) => {
      button.addEventListener('click', async () => {
        switchAutoView('running');
        try {
          await api.retryScanRun(button.dataset.runRetry);
          await Promise.all([loadPlans(), loadHistory()]);
        } catch (error) {
          showError(`重试失败：${error.message}`);
        }
      });
    });
  }

  async function loadHistory() {
    try {
      const result = await api.scanRunHistory(50);
      autoState.history = result?.runs || [];
      renderHistory();
    } catch (error) {
      showError(`读取运行记录失败：${error.message}`);
    }
  }

  function bindEvents() {
    document.querySelectorAll('[data-auto-view]').forEach((tab) => {
      tab.addEventListener('click', () => switchAutoView(tab.dataset.autoView));
      tab.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        const tabs = [...document.querySelectorAll('[data-auto-view]')];
        const delta = event.key === 'ArrowRight' ? 1 : -1;
        const next = tabs[(tabs.indexOf(tab) + delta + tabs.length) % tabs.length];
        switchAutoView(next.dataset.autoView);
        next.focus();
      });
    });
    els['auto-new-plan'].addEventListener('click', () => openEditor());
    els['auto-empty-create'].addEventListener('click', () => openEditor());
    els['auto-editor-close'].addEventListener('click', closeEditor);
    els['auto-editor-cancel'].addEventListener('click', closeEditor);
    els['auto-editor'].addEventListener('close', () => {
      autoState.previewRequest += 1;
    });
    els['auto-add-root'].addEventListener('click', addRoots);
    els['auto-editor-form'].addEventListener('submit', previewEditor);
    [
      'auto-format-pptx', 'auto-format-html', 'auto-format-zip',
      'auto-min-size', 'auto-max-size',
    ].forEach((id) => els[id].addEventListener('change', updateRuleSummary));
    els['auto-preflight-close'].addEventListener('click', () => els['auto-preflight'].close());
    els['auto-preflight-cancel'].addEventListener('click', () => {
      els['auto-preflight'].close();
      if (autoState.preflightIntent?.mode === 'save') openEditor(autoState.preflightIntent.payload);
    });
    els['auto-preflight-confirm'].addEventListener('click', confirmPreflight);
    els['auto-preview-all'].addEventListener('click', () => preflightAll('preview'));
    els['auto-run-all'].addEventListener('click', () => preflightAll('run-all'));
    els['auto-refresh-history'].addEventListener('click', loadHistory);
    els['auto-stop-run'].addEventListener('click', async () => {
      els['auto-stop-run'].disabled = true;
      els['auto-stop-run'].textContent = '正在停止…';
      try {
        await api.stopScanRun();
      } catch (error) {
        showError(`停止任务失败：${error.message}`);
      }
    });
    api.onTaskState((task) => {
      const hadTask = Boolean(autoState.currentTask);
      autoState.currentTask = task;
      renderCurrentTask();
      renderPlans();
      if (task && task.kind !== '自动更新预检') {
        switchAutoView('running');
        setNavState('blue');
        const autoStep = document.querySelector('.step[data-step="5"]');
        if (autoStep?.classList.contains('active')) {
          requestAnimationFrame(() => els['auto-stop-run'].focus());
        }
      } else if (hadTask) {
        Promise.all([loadPlans(), loadHistory()]);
      }
    });
    api.onProgress((progress) => {
      if (!autoState.currentTask || progress.taskId !== autoState.currentTask.taskId) return;
      autoState.currentTask.progress = progress;
      renderCurrentTask();
    });
    api.onTaskError(({ kind, message }) => {
      showError(`${kind}失败：${message}`);
    });
  }

  async function init() {
    collectElements();
    if (!els['auto-plan-rows'] || !api.listScanPlans) return;
    bindEvents();
    await Promise.all([loadPlans(), refreshCurrentTask(), loadHistory()]);
  }

  window.PinPageAutoUpdate = {
    formatBytes,
    summarizeStats,
    progressRatio,
    init,
  };
  init();
})();
