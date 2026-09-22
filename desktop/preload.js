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
  import: (filePaths) => ipcRenderer.invoke('import', filePaths),
  loadCatalog: () => ipcRenderer.invoke('load-catalog'),
  removeDeck: (deckIds) => ipcRenderer.invoke('remove-deck', deckIds),
  removeSlide: (slideIds) => ipcRenderer.invoke('remove-slide', slideIds),
  compose: (options) => ipcRenderer.invoke('compose', options),
  reveal: (targetPath) => ipcRenderer.invoke('reveal', targetPath),
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
});
