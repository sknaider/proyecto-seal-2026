/**
 * Código SEAL IDE — Main Process v2 (Modular)
 * =============================================
 * Electron main process. Window lifecycle + menu.
 * All IPC handlers extracted to src/main/ipc/ modules.
 */

const { app, BrowserWindow, Menu, shell } = require('electron');
const path = require('path');
const os = require('os');
const ipc = require('./ipc/index.js');

// ── Constants ──────────────────────────────────────────────────────
const IS_DEV = process.env.NODE_ENV === 'development';
const APP_NAME = 'Código SEAL';
const APP_VERSION = '0.2.0';

// ── Window Management ──────────────────────────────────────────────
let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600, height: 1000, minWidth: 1024, minHeight: 700,
    title: APP_NAME, backgroundColor: '#0d1117', show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'preload.js'),
      nodeIntegration: false, contextIsolation: true, sandbox: false,
    },
  });

  if (IS_DEV) {
    mainWindow.loadURL('http://localhost:3001');
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.setTitle(`${APP_NAME} v${APP_VERSION}`);
  });

  mainWindow.on('closed', () => { mainWindow = null; });

  // Register all IPC handlers
  ipc.registerAll(mainWindow);

  // Build menu
  buildMenu();
}

// ── Application Menu ───────────────────────────────────────────────
function buildMenu() {
  const send = (channel, data = null) => {
    if (mainWindow && mainWindow.webContents) mainWindow.webContents.send(channel, data);
  };

  const template = [
    {
      label: APP_NAME,
      submenu: [
        { label: `Acerca de ${APP_NAME}`, role: 'about' },
        { type: 'separator' },
        { label: 'Configuración', accelerator: 'CmdOrCtrl+,', click: () => send('open-settings') },
        { type: 'separator' },
        { label: 'Salir', accelerator: 'CmdOrCtrl+Q', role: 'quit' },
      ],
    },
    {
      label: 'Archivo',
      submenu: [
        { label: 'Nuevo Archivo', accelerator: 'CmdOrCtrl+N', click: () => send('new-file') },
        { label: 'Abrir Archivo', accelerator: 'CmdOrCtrl+O', click: () => send('open-file') },
        { label: 'Abrir Carpeta', accelerator: 'CmdOrCtrl+Shift+O', click: () => send('open-folder') },
        { type: 'separator' },
        { label: 'Guardar', accelerator: 'CmdOrCtrl+S', click: () => send('save-file') },
        { label: 'Guardar Como', accelerator: 'CmdOrCtrl+Shift+S', click: () => send('save-file-as') },
      ],
    },
    {
      label: 'Editar',
      submenu: [
        { role: 'undo', label: 'Deshacer' }, { role: 'redo', label: 'Rehacer' },
        { type: 'separator' },
        { role: 'cut', label: 'Cortar' }, { role: 'copy', label: 'Copiar' },
        { role: 'paste', label: 'Pegar' }, { role: 'selectAll', label: 'Seleccionar Todo' },
      ],
    },
    {
      label: 'Ver',
      submenu: [
        { label: 'Terminal', accelerator: 'CmdOrCtrl+`', click: () => send('toggle-terminal') },
        { label: 'Chat del Equipo', accelerator: 'CmdOrCtrl+Shift+C', click: () => send('toggle-chat') },
        { label: 'SOUL Dashboard', accelerator: 'CmdOrCtrl+Shift+U', click: () => send('toggle-soul') },
        { label: 'Explorador', accelerator: 'CmdOrCtrl+Shift+E', click: () => send('toggle-explorer') },
        { type: 'separator' },
        { role: 'togglefullscreen', label: 'Pantalla Completa' },
        { role: 'toggleDevTools', label: 'DevTools', visible: IS_DEV },
      ],
    },
    {
      label: 'SEAL',
      submenu: [
        { label: 'SOUL Snapshot', click: () => send('soul-snapshot') },
        { label: 'Emotional Variance', click: () => send('emotional-variance') },
        { label: 'Dream Consolidation', click: () => send('run-dream') },
        { type: 'separator' },
        { label: 'Agent: JARVIS', click: () => send('switch-agent', 'JARVIS') },
        { label: 'Agent: ADA', click: () => send('switch-agent', 'ADA') },
        { label: 'Agent: DUM', click: () => send('switch-agent', 'DUM') },
        { type: 'separator' },
        { label: 'Scratchpad', click: () => send('open-scratchpad') },
        { label: 'Skills', click: () => send('open-skills') },
        { type: 'separator' },
        { label: 'Browser Integrado', accelerator: 'CmdOrCtrl+Shift+B', click: () => send('open-browser') },
      ],
    },
    {
      label: 'Ayuda',
      submenu: [
        { label: `${APP_NAME} v${APP_VERSION}` },
        { label: 'Equipo SEAL — William, JARVIS, ADA, DUM' },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ── App Lifecycle ──────────────────────────────────────────────────
app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  ipc.cleanup();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.setName(APP_NAME);
