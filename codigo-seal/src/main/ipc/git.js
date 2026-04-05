/**
 * Código SEAL — Git IPC Handlers
 * Git status, diff, log via execFileSync (no command injection).
 */

const { ipcMain } = require('electron');
const { execFileSync } = require('child_process');

function register() {
  ipcMain.handle('git:status', async (event, { cwd }) => {
    try {
      const branch = execFileSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'], { cwd, timeout: 5000 }).toString().trim();
      const status = execFileSync('git', ['status', '--porcelain'], { cwd, timeout: 5000 }).toString().trim();
      const files = status ? status.split('\n').map(line => ({
        index: line.charAt(0), working: line.charAt(1),
        status: line.substring(0, 2).replace(/ /g, '_'),
        path: line.substring(3),
      })) : [];
      return { branch, files, clean: files.length === 0 };
    } catch (err) { return { error: err.message }; }
  });

  ipcMain.handle('git:diff', async (event, { cwd, filePath }) => {
    try {
      if (!filePath || typeof filePath !== 'string') return { error: 'Invalid file path' };
      const diff = execFileSync('git', ['diff', '--', filePath], { cwd, timeout: 5000 }).toString();
      return { diff: diff || '(sin cambios staged)' };
    } catch (err) { return { error: err.message }; }
  });

  ipcMain.handle('git:log', async (event, { cwd, count }) => {
    try {
      const n = Math.min(Math.max(parseInt(count) || 10, 1), 50);
      const log = execFileSync('git', ['log', '--oneline', `-${n}`], { cwd, timeout: 5000 }).toString().trim();
      if (!log) return { entries: [] };
      return { entries: log.split('\n').map(l => ({ hash: l.substring(0, 7), message: l.substring(8) })) };
    } catch (err) { return { error: err.message }; }
  });
}

module.exports = { register };
