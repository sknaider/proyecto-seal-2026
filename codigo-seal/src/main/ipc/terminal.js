/**
 * Código SEAL — Terminal IPC Handlers
 * node-pty terminal management via IPC.
 */

const { ipcMain } = require('electron');
const os = require('os');

let ptyProcesses = {};
let ptyIdCounter = 0;

function register(mainWindow) {
  ipcMain.handle('terminal:create', async (event, { cols, rows, cwd }) => {
    try {
      const pty = require('node-pty');
      const id = `pty_${++ptyIdCounter}`;
      const shell = os.platform() === 'win32' ? 'powershell.exe' : process.env.SHELL || '/bin/bash';
      const ptyProcess = pty.spawn(shell, [], {
        name: 'xterm-256color',
        cols: cols || 80, rows: rows || 24,
        cwd: cwd || os.homedir(),
        env: { ...process.env, TERM: 'xterm-256color' },
      });
      ptyProcesses[id] = ptyProcess;
      ptyProcess.onData((data) => {
        if (mainWindow && mainWindow.webContents) mainWindow.webContents.send('terminal:data', { id, data });
      });
      ptyProcess.onExit(({ exitCode }) => {
        delete ptyProcesses[id];
        if (mainWindow && mainWindow.webContents) mainWindow.webContents.send('terminal:exit', { id, exitCode });
      });
      return { id, pid: ptyProcess.pid };
    } catch (err) {
      return { error: err.message };
    }
  });

  ipcMain.on('terminal:write', (event, { id, data }) => {
    if (ptyProcesses[id]) ptyProcesses[id].write(data);
  });

  ipcMain.on('terminal:resize', (event, { id, cols, rows }) => {
    if (ptyProcesses[id]) ptyProcesses[id].resize(cols, rows);
  });

  ipcMain.on('terminal:kill', (event, { id }) => {
    if (ptyProcesses[id]) { ptyProcesses[id].kill(); delete ptyProcesses[id]; }
  });
}

function killAll() {
  Object.values(ptyProcesses).forEach((p) => p.kill());
  ptyProcesses = {};
}

module.exports = { register, killAll };
