/**
 * Código SEAL — AI Provider IPC Handlers
 * Bridge between renderer and ProviderRegistry.
 */

const { ipcMain } = require('electron');
const registry = require('../services/provider.js');

function register(mainWindow) {
  ipcMain.handle('ai:providers', async () => registry.getAll());

  ipcMain.handle('ai:setActive', async (event, providerId) => {
    try {
      registry.setActive(providerId);
      return { success: true };
    } catch (e) { return { error: e.message }; }
  });

  ipcMain.handle('ai:health', async () => await registry.checkAllHealth());

  ipcMain.handle('ai:models', async (event, { providerId } = {}) => {
    return await registry.listModels(providerId);
  });

  ipcMain.handle('ai:complete', async (event, request) => {
    // Non-streaming — collect all chunks and return
    const chunks = [];
    let fullText = '';
    for await (const chunk of registry.complete(request)) {
      if (chunk.type === 'text') fullText += chunk.text;
      if (chunk.type === 'error') return { error: chunk.error };
      if (chunk.type === 'done') break;
    }
    return { text: fullText, provider: request.model };
  });

  // Streaming via events
  ipcMain.handle('ai:stream', async (event, request) => {
    const streamId = `stream_${Date.now()}`;
    (async () => {
      for await (const chunk of registry.complete(request)) {
        if (mainWindow && mainWindow.webContents) {
          mainWindow.webContents.send('ai:chunk', { streamId, ...chunk });
        }
        if (chunk.type === 'done' || chunk.type === 'error') break;
      }
    })();
    return { streamId };
  });

  ipcMain.handle('ai:estimateTokens', async (event, { text, providerId }) => {
    return { tokens: registry.estimateTokens(text, providerId) };
  });
}

module.exports = { register };
