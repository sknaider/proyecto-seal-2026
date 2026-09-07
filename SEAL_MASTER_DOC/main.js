/**
 * Código SEAL IDE — Main Process
 * ================================
 * Electron main process. Creates the window, manages IPC,
 * connects to SEAL Runtime bridge.
 *
 * Architecture:
 *   Main Process (this file)
 *     ├── Creates BrowserWindow (renderer)
 *     ├── Manages node-pty terminals
 *     ├── IPC bridge to renderer
 *     └── Connects to SEAL Runtime (FastAPI bridge on port 8766)
 *
 *   Renderer Process (renderer/)
 *     ├── Monaco Editor (code editing)
 *     ├── xterm.js (terminal UI)
 *     ├── Chat Panel (team communication)
 *     ├── File Explorer (project navigation)
 *     ├── SOUL Dashboard (OCEAN, drift, emotions)
 *     └── Agent Status (JARVIS/ADA/DUM live)
 */

const { app, BrowserWindow, ipcMain, Menu, shell } = require('electron');
const path = require('path');
const os = require('os');

// ── Constants ──────────────────────────────────────────────────────

const SEAL_BRIDGE_URL = process.env.SEAL_BRIDGE_URL || 'http://localhost:8766';
const SEAL_CHAT_URL = process.env.SEAL_CHAT_URL || 'http://localhost:8765';
const IS_DEV = process.env.NODE_ENV === 'development';
const APP_NAME = 'Código SEAL';
const APP_VERSION = '0.1.0';

// ── Window Management ──────────────────────────────────────────────

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 1024,
    minHeight: 700,
    title: APP_NAME,
    backgroundColor: '#0d1117',
    show: false, // Show when ready to prevent flash
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false, // needed for node-pty via IPC
    },
  });

  // Load renderer
  if (IS_DEV) {
    mainWindow.loadURL('http://localhost:3001');
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
  }

  // Show when ready
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.setTitle(`${APP_NAME} v${APP_VERSION}`);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Build menu
  buildMenu();
}

// ── Application Menu ───────────────────────────────────────────────

function buildMenu() {
  const template = [
    {
      label: APP_NAME,
      submenu: [
        { label: `Acerca de ${APP_NAME}`, role: 'about' },
        { type: 'separator' },
        { label: 'Configuración', accelerator: 'CmdOrCtrl+,', click: () => sendToRenderer('open-settings') },
        { type: 'separator' },
        { label: 'Salir', accelerator: 'CmdOrCtrl+Q', role: 'quit' },
      ],
    },
    {
      label: 'Archivo',
      submenu: [
        { label: 'Nuevo Archivo', accelerator: 'CmdOrCtrl+N', click: () => sendToRenderer('new-file') },
        { label: 'Abrir Archivo', accelerator: 'CmdOrCtrl+O', click: () => sendToRenderer('open-file') },
        { label: 'Abrir Carpeta', accelerator: 'CmdOrCtrl+Shift+O', click: () => sendToRenderer('open-folder') },
        { type: 'separator' },
        { label: 'Guardar', accelerator: 'CmdOrCtrl+S', click: () => sendToRenderer('save-file') },
        { label: 'Guardar Como', accelerator: 'CmdOrCtrl+Shift+S', click: () => sendToRenderer('save-file-as') },
      ],
    },
    {
      label: 'Editar',
      submenu: [
        { role: 'undo', label: 'Deshacer' },
        { role: 'redo', label: 'Rehacer' },
        { type: 'separator' },
        { role: 'cut', label: 'Cortar' },
        { role: 'copy', label: 'Copiar' },
        { role: 'paste', label: 'Pegar' },
        { role: 'selectAll', label: 'Seleccionar Todo' },
      ],
    },
    {
      label: 'Ver',
      submenu: [
        { label: 'Terminal', accelerator: 'CmdOrCtrl+`', click: () => sendToRenderer('toggle-terminal') },
        { label: 'Chat del Equipo', accelerator: 'CmdOrCtrl+Shift+C', click: () => sendToRenderer('toggle-chat') },
        { label: 'SOUL Dashboard', accelerator: 'CmdOrCtrl+Shift+S', click: () => sendToRenderer('toggle-soul') },
        { label: 'Explorador', accelerator: 'CmdOrCtrl+Shift+E', click: () => sendToRenderer('toggle-explorer') },
        { type: 'separator' },
        { role: 'togglefullscreen', label: 'Pantalla Completa' },
        { role: 'toggleDevTools', label: 'DevTools', visible: IS_DEV },
      ],
    },
    {
      label: 'SEAL',
      submenu: [
        { label: 'SOUL Snapshot', click: () => sendToRenderer('soul-snapshot') },
        { label: 'Emotional Variance', click: () => sendToRenderer('emotional-variance') },
        { label: 'Dream Consolidation', click: () => sendToRenderer('run-dream') },
        { type: 'separator' },
        { label: 'Agent: JARVIS (Architect)', click: () => sendToRenderer('switch-agent', 'JARVIS') },
        { label: 'Agent: ADA (Engineer)', click: () => sendToRenderer('switch-agent', 'ADA') },
        { label: 'Agent: DUM (Guardian)', click: () => sendToRenderer('switch-agent', 'DUM') },
        { type: 'separator' },
        { label: 'Scratchpad', click: () => sendToRenderer('open-scratchpad') },
        { label: 'Skills', click: () => sendToRenderer('open-skills') },
        { type: 'separator' },
        { label: 'Browser Integrado', accelerator: 'CmdOrCtrl+Shift+B', click: () => sendToRenderer('open-browser') },
      ],
    },
    {
      label: 'Ayuda',
      submenu: [
        { label: `${APP_NAME} v${APP_VERSION}` },
        { label: 'Equipo SEAL — William, JARVIS, ADA, DUM' },
        { type: 'separator' },
        { label: 'GitHub', click: () => shell.openExternal('https://github.com/sknaider/proyecto-seal') },
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

function sendToRenderer(channel, data = null) {
  if (mainWindow && mainWindow.webContents) {
    mainWindow.webContents.send(channel, data);
  }
}

// ── IPC Handlers ───────────────────────────────────────────────────

// Terminal management via node-pty
let ptyProcesses = {};
let ptyIdCounter = 0;

ipcMain.handle('terminal:create', async (event, { cols, rows, cwd }) => {
  try {
    const pty = require('node-pty');
    const id = `pty_${++ptyIdCounter}`;
    const shell = os.platform() === 'win32' ? 'powershell.exe' : process.env.SHELL || '/bin/bash';

    const ptyProcess = pty.spawn(shell, [], {
      name: 'xterm-256color',
      cols: cols || 80,
      rows: rows || 24,
      cwd: cwd || os.homedir(),
      env: { ...process.env, TERM: 'xterm-256color' },
    });

    ptyProcesses[id] = ptyProcess;

    ptyProcess.onData((data) => {
      if (mainWindow && mainWindow.webContents) {
        mainWindow.webContents.send('terminal:data', { id, data });
      }
    });

    ptyProcess.onExit(({ exitCode }) => {
      delete ptyProcesses[id];
      if (mainWindow && mainWindow.webContents) {
        mainWindow.webContents.send('terminal:exit', { id, exitCode });
      }
    });

    return { id, pid: ptyProcess.pid };
  } catch (err) {
    return { error: err.message };
  }
});

ipcMain.on('terminal:write', (event, { id, data }) => {
  if (ptyProcesses[id]) {
    ptyProcesses[id].write(data);
  }
});

ipcMain.on('terminal:resize', (event, { id, cols, rows }) => {
  if (ptyProcesses[id]) {
    ptyProcesses[id].resize(cols, rows);
  }
});

ipcMain.on('terminal:kill', (event, { id }) => {
  if (ptyProcesses[id]) {
    ptyProcesses[id].kill();
    delete ptyProcesses[id];
  }
});

// SEAL Runtime bridge
ipcMain.handle('seal:request', async (event, { endpoint, method, body }) => {
  try {
    const url = `${SEAL_BRIDGE_URL}${endpoint}`;
    const options = {
      method: method || 'GET',
      headers: { 'Content-Type': 'application/json' },
    };
    if (body) options.body = JSON.stringify(body);

    const response = await fetch(url, options);
    return await response.json();
  } catch (err) {
    return { error: err.message };
  }
});

// File system operations
const fs = require('fs').promises;

ipcMain.handle('fs:readFile', async (event, filePath) => {
  try {
    const content = await fs.readFile(filePath, 'utf-8');
    return { content };
  } catch (err) {
    return { error: err.message };
  }
});

ipcMain.handle('fs:writeFile', async (event, { filePath, content }) => {
  try {
    await fs.writeFile(filePath, content, 'utf-8');
    return { success: true };
  } catch (err) {
    return { error: err.message };
  }
});

ipcMain.handle('fs:readDir', async (event, dirPath) => {
  try {
    const entries = await fs.readdir(dirPath, { withFileTypes: true });
    return {
      entries: entries.map((e) => ({
        name: e.name,
        isDirectory: e.isDirectory(),
        isFile: e.isFile(),
        path: path.join(dirPath, e.name),
      })),
    };
  } catch (err) {
    return { error: err.message };
  }
});

ipcMain.handle('fs:stat', async (event, filePath) => {
  try {
    const stat = await fs.stat(filePath);
    return {
      size: stat.size,
      isDirectory: stat.isDirectory(),
      isFile: stat.isFile(),
      modified: stat.mtime.toISOString(),
    };
  } catch (err) {
    return { error: err.message };
  }
});

// Browser integrado — como Antigravity
ipcMain.handle('browser:open', async (event, url) => {
  const { BrowserView } = require('electron');
  const view = new BrowserView({
    webPreferences: { nodeIntegration: false, contextIsolation: true },
  });
  mainWindow.addBrowserView(view);
  const bounds = mainWindow.getBounds();
  view.setBounds({ x: 280, y: 64, width: bounds.width - 280, height: bounds.height - 86 });
  view.setAutoResize({ width: true, height: true });
  view.webContents.loadURL(url || 'about:blank');
  return { success: true, url };
});

ipcMain.handle('browser:close', async () => {
  const views = mainWindow.getBrowserViews();
  views.forEach(v => mainWindow.removeBrowserView(v));
  return { success: true };
});

// Screenshots/Artifacts — captura de pantalla del browser
ipcMain.handle('browser:screenshot', async () => {
  const views = mainWindow.getBrowserViews();
  if (views.length === 0) return { error: 'No browser open' };
  const image = await views[0].webContents.capturePage();
  const png = image.toPNG();
  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  const screenshotPath = path.join(os.homedir(), 'IA', 'proyecto-seal', 'artifacts', `screenshot_${ts}.png`);
  await fs.mkdir(path.dirname(screenshotPath), { recursive: true });
  await fs.writeFile(screenshotPath, png);
  return { path: screenshotPath, size: png.length };
});

// GPU status via nvidia-smi
ipcMain.handle('system:gpu', async () => {
  const { execSync } = require('child_process');
  try {
    const output = execSync('nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits', { timeout: 5000 });
    const [temp, util, memUsed, memTotal] = output.toString().trim().split(', ');
    return { temp: parseInt(temp), util: parseInt(util), memUsed: parseInt(memUsed), memTotal: parseInt(memTotal) };
  } catch { return { error: 'nvidia-smi not available' }; }
});

// App info
ipcMain.handle('app:info', () => ({
  name: APP_NAME,
  version: APP_VERSION,
  platform: os.platform(),
  arch: os.arch(),
  hostname: os.hostname(),
  homedir: os.homedir(),
  bridgeUrl: SEAL_BRIDGE_URL,
  chatUrl: SEAL_CHAT_URL,
}));

// ── App Lifecycle ──────────────────────────────────────────────────

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  // Kill all pty processes
  Object.values(ptyProcesses).forEach((p) => p.kill());
  ptyProcesses = {};

  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

// Set app name
app.setName(APP_NAME);
