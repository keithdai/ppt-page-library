'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// Minimal, explicit bridge. The renderer never touches Node or the CLI
// directly; every capability is a named, awaitable call.
contextBridge.exposeInMainWorld('pptlib', {
  paths: () => ipcRenderer.invoke('paths'),
  writeManifest: (slideIds) => ipcRenderer.invoke('write-manifest', slideIds),
  pickRepoRoot: () => ipcRenderer.invoke('pick-repo-root'),
  pickPptx: () => ipcRenderer.invoke('pick-pptx'),
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  pickManifest: () => ipcRenderer.invoke('pick-manifest'),
  pickOutputPptx: () => ipcRenderer.invoke('pick-output-pptx'),
  pickAutoUpdateFolders: () => ipcRenderer.invoke('pick-folder'),
  import: (filePaths) => ipcRenderer.invoke('import', filePaths),
  loadLibrary: (sinceRevision) => ipcRenderer.invoke('library:bootstrap', sinceRevision),
  searchLibrary: (options) => ipcRenderer.invoke('library:search', options),
  saveSelection: (payload) => ipcRenderer.invoke('selection:save', payload),
  openHtmlPreview: (slideId) => ipcRenderer.invoke('html-preview', slideId),
  findDuplicates: (refresh) => ipcRenderer.invoke('find-duplicates', refresh),
  removeDeck: (deckIds) => ipcRenderer.invoke('remove-deck', deckIds),
  removeSlide: (slideIds) => ipcRenderer.invoke('remove-slide', slideIds),
  doctor: () => ipcRenderer.invoke('doctor'),
  preflightCompose: (options) => ipcRenderer.invoke('compose-preflight', options),
  compose: (options) => ipcRenderer.invoke('compose', options),
  listScanPlans: () => ipcRenderer.invoke('scan-plan:list'),
  createScanPlan: (plan) => ipcRenderer.invoke('scan-plan:create', plan),
  updateScanPlan: (plan) => ipcRenderer.invoke('scan-plan:update', plan),
  deleteScanPlan: (planId) => ipcRenderer.invoke('scan-plan:delete', planId),
  setScanPlanEnabled: (planId, enabled) =>
    ipcRenderer.invoke('scan-plan:set-enabled', { planId, enabled }),
  previewScanPlan: (plan) => ipcRenderer.invoke('scan-plan:preview', plan),
  runScanPlan: (options) => ipcRenderer.invoke('scan-plan:run', options),
  runAllScanPlans: () => ipcRenderer.invoke('scan-plan:run-all'),
  currentScanRun: () => ipcRenderer.invoke('scan-run:current'),
  stopScanRun: () => ipcRenderer.invoke('scan-run:stop'),
  scanRunHistory: (limit) => ipcRenderer.invoke('scan-run:history', limit),
  retryScanRun: (runId) => ipcRenderer.invoke('scan-run:retry', runId),
  reveal: (targetPath) => ipcRenderer.invoke('reveal', targetPath),
  openPath: (targetPath) => ipcRenderer.invoke('open-path', targetPath),
  onLog: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('log', listener);
    return () => ipcRenderer.removeListener('log', listener);
  },
  onProgress: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('progress', listener);
    return () => ipcRenderer.removeListener('progress', listener);
  },
  onTaskState: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('task-state', listener);
    return () => ipcRenderer.removeListener('task-state', listener);
  },
  onTaskError: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('task-error', listener);
    return () => ipcRenderer.removeListener('task-error', listener);
  },
});
