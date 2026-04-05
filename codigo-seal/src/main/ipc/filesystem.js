/**
 * Código SEAL — Filesystem IPC Handlers
 * File read/write/readDir/stat + search via IPC.
 */

const { ipcMain } = require('electron');
const fs = require('fs').promises;
const path = require('path');

function register() {
  ipcMain.handle('fs:readFile', async (event, filePath) => {
    try {
      const content = await fs.readFile(filePath, 'utf-8');
      return { content };
    } catch (err) { return { error: err.message }; }
  });

  ipcMain.handle('fs:writeFile', async (event, { filePath, content }) => {
    try {
      await fs.writeFile(filePath, content, 'utf-8');
      return { success: true };
    } catch (err) { return { error: err.message }; }
  });

  ipcMain.handle('fs:readDir', async (event, dirPath) => {
    try {
      const entries = await fs.readdir(dirPath, { withFileTypes: true });
      return {
        entries: entries.map((e) => ({
          name: e.name, isDirectory: e.isDirectory(), isFile: e.isFile(),
          path: path.join(dirPath, e.name),
        })),
      };
    } catch (err) { return { error: err.message }; }
  });

  ipcMain.handle('fs:stat', async (event, filePath) => {
    try {
      const stat = await fs.stat(filePath);
      return { size: stat.size, isDirectory: stat.isDirectory(), isFile: stat.isFile(), modified: stat.mtime.toISOString() };
    } catch (err) { return { error: err.message }; }
  });

  // Search in files (single IPC call, no recursive round-trips)
  ipcMain.handle('fs:searchInFiles', async (event, { rootDir, query, maxResults, maxDepth }) => {
    const results = [];
    const queryLower = query.toLowerCase();
    const skip = new Set(['node_modules', '__pycache__', '.git', 'dist', 'build', '.venv', 'venv']);
    const searchExts = new Set(['py','js','ts','jsx','tsx','json','md','html','css','sh','bash','sql','yaml','yml','xml','toml','txt','cfg','ini']);
    async function search(dir, depth) {
      if (depth > (maxDepth || 5) || results.length >= (maxResults || 50)) return;
      try {
        const entries = await fs.readdir(dir, { withFileTypes: true });
        for (const entry of entries) {
          if (entry.name.startsWith('.') || skip.has(entry.name)) continue;
          const fullPath = path.join(dir, entry.name);
          if (entry.isDirectory()) { await search(fullPath, depth + 1); }
          else {
            const ext = entry.name.split('.').pop().toLowerCase();
            if (!searchExts.has(ext)) continue;
            try {
              const content = await fs.readFile(fullPath, 'utf-8');
              const lines = content.split('\n');
              for (let i = 0; i < lines.length && results.length < maxResults; i++) {
                if (lines[i].toLowerCase().includes(queryLower)) {
                  results.push({ path: fullPath, name: entry.name, line: i + 1, text: lines[i].trim().slice(0, 120) });
                }
              }
            } catch {}
          }
        }
      } catch {}
    }
    await search(rootDir, 0);
    return { results };
  });
}

module.exports = { register };
