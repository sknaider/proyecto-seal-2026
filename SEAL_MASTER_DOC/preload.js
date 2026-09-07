/**
 * Código SEAL IDE — Preload Script v2
 * ======================================
 * Exposes safe IPC bridge to renderer process.
 * Context isolation enabled — renderer can only use these APIs.
 * v2: adds browser, screenshots, GPU monitoring.
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

  // Browser integrado (como Antigravity)
  browser: {
    open: (url) => ipcRenderer.invoke('browser:open', url),
    close: () => ipcRenderer.invoke('browser:close'),
    screenshot: () => ipcRenderer.invoke('browser:screenshot'),
  },

  // System monitoring
  system: {
    gpu: () => ipcRenderer.invoke('system:gpu'),
  },

  // Menu events from main process
  on: (channel, callback) => {
    const validChannels = [
      'new-file', 'open-file', 'open-folder', 'save-file', 'save-file-as',
      'toggle-terminal', 'toggle-chat', 'toggle-soul', 'toggle-explorer',
      'soul-snapshot', 'emotional-variance', 'run-dream',
      'switch-agent', 'open-scratchpad', 'open-skills', 'open-settings',
      'open-browser',
    ];
    if (validChannels.includes(channel)) {
      ipcRenderer.on(channel, (_, data) => callback(data));
    }
  },
});
