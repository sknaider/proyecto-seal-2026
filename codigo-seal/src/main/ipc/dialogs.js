/**
 * Código SEAL — Dialog IPC Handlers
 * Native file/folder dialogs via Electron.
 */

const { ipcMain, dialog } = require('electron');

function register(mainWindow) {
  ipcMain.handle('dialog:openFile', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Abrir Archivo', properties: ['openFile'],
      filters: [
        { name: 'Todos los archivos', extensions: ['*'] },
        { name: 'Código', extensions: ['py','js','ts','jsx','tsx','json','html','css','md','sql','yaml','yml'] },
      ],
    });
    if (result.canceled) return { canceled: true };
    return { canceled: false, filePaths: result.filePaths };
  });

  ipcMain.handle('dialog:openFolder', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Abrir Carpeta', properties: ['openDirectory'],
    });
    if (result.canceled) return { canceled: true };
    return { canceled: false, filePaths: result.filePaths };
  });

  ipcMain.handle('dialog:saveFile', async (event, { defaultPath }) => {
    const result = await dialog.showSaveDialog(mainWindow, {
      title: 'Guardar Como', defaultPath: defaultPath || '',
      filters: [{ name: 'Todos los archivos', extensions: ['*'] }],
    });
    if (result.canceled) return { canceled: true };
    return { canceled: false, filePath: result.filePath };
  });
}

module.exports = { register };
