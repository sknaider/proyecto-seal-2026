/**
 * Código SEAL IDE — Preload Script
 * ==================================
 * Exposes safe IPC bridge to renderer process.
 * Context isolation enabled — renderer can only use these APIs.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('seal', {
  // App info
  getAppInfo: () => ipcRenderer.invoke('app:info'),

  // SEAL Runtime bridge
  request: (endpoint, method, body) =>
    ipcRenderer.invoke('seal:request', { endpoint, method, body }),

  // Terminal
  terminal: {
    create: (opts) => ipcRenderer.invoke('terminal:create', opts || {}),
    write: (id, data) => ipcRenderer.send('terminal:write', { id, data }),
    resize: (id, cols, rows) => ipcRenderer.send('terminal:resize', { id, cols, rows }),
    kill: (id) => ipcRenderer.send('terminal:kill', { id }),
    onData: (callback) => ipcRenderer.on('terminal:data', (_, payload) => callback(payload)),
    onExit: (callback) => ipcRenderer.on('terminal:exit', (_, payload) => callback(payload)),
  },

  // File system
  fs: {
    readFile: (filePath) => ipcRenderer.invoke('fs:readFile', filePath),
    writeFile: (filePath, content) => ipcRenderer.invoke('fs:writeFile', { filePath, content }),
    readDir: (dirPath) => ipcRenderer.invoke('fs:readDir', dirPath),
    stat: (filePath) => ipcRenderer.invoke('fs:stat', filePath),
  },

  // Menu events from main process
  on: (channel, callback) => {
    const validChannels = [
      'new-file', 'open-file', 'open-folder', 'save-file', 'save-file-as',
      'toggle-terminal', 'toggle-chat', 'toggle-soul', 'toggle-explorer',
      'soul-snapshot', 'emotional-variance', 'run-dream',
      'switch-agent', 'open-scratchpad', 'open-skills', 'open-settings',
    ];
    if (validChannels.includes(channel)) {
      ipcRenderer.on(channel, (_, data) => callback(data));
    }
  },
});
