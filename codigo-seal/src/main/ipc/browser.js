/**
 * Código SEAL — Browser IPC Handlers
 * Integrated browser, screenshots, GPU monitoring.
 */

const { ipcMain } = require('electron');
const path = require('path');
const os = require('os');
const fs = require('fs').promises;

function register(mainWindow) {
  // Browser integrado (URL validated)
  ipcMain.handle('browser:open', async (event, url) => {
    if (url && !url.startsWith('http://') && !url.startsWith('https://') && url !== 'about:blank') {
      return { error: 'Only http:// and https:// URLs allowed' };
    }
    if (!mainWindow) return { error: 'No window available' };
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
    if (!mainWindow) return { error: 'No window' };
    const views = mainWindow.getBrowserViews();
    views.forEach(v => { mainWindow.removeBrowserView(v); v.webContents.close(); });
    return { success: true };
  });

  ipcMain.handle('browser:screenshot', async () => {
    if (!mainWindow) return { error: 'No window' };
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

  // GPU status
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
    name: 'Código SEAL', version: '0.2.0',
    platform: os.platform(), arch: os.arch(),
    hostname: os.hostname(), homedir: os.homedir(),
  }));
}

module.exports = { register };
