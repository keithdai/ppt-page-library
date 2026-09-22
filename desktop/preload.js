'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// Minimal, explicit bridge. The renderer never touches Node or the CLI
// directly; every capability is a named, awaitable call.
contextBridge.exposeInMainWorld('pptlib', {
  paths: () => ipcRenderer.invoke('paths'),
  miaodaConfig: () => ipcRenderer.invoke('miaoda-config'),
  writeManifest: (slideIds) => ipcRenderer.invoke('write-manifest', slideIds),
  pickRepoRoot: () => ipcRenderer.invoke('pick-repo-root'),
  pickPptx: () => ipcRenderer.invoke('pick-pptx'),
  pickManifest: () => ipcRenderer.invoke('pick-manifest'),
  pickOutputPptx: () => ipcRenderer.invoke('pick-output-pptx'),
  import: (filePaths) => ipcRenderer.invoke('import', filePaths),
  catalog: (outputDir) => ipcRenderer.invoke('catalog', outputDir),
  sync: (options) => ipcRenderer.invoke('sync', options),
  compose: (options) => ipcRenderer.invoke('compose', options),
  reveal: (targetPath) => ipcRenderer.invoke('reveal', targetPath),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
  onLog: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('log', listener);
    return () => ipcRenderer.removeListener('log', listener);
  },
  // Fired when the embedded Miaoda view downloads a selection manifest.
  onMiaodaManifest: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('miaoda-manifest', listener);
    return () => ipcRenderer.removeListener('miaoda-manifest', listener);
  },
});
