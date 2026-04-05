/**
 * Código SEAL — Magic Docs (Phase 4.4)
 * Self-updating documentation files.
 * Files starting with "# MAGIC DOC:" auto-update based on conversation.
 * Based on: Claude Code MagicDocs (HIDDEN_FEATURES #6). Clean-room.
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

const MAGIC_DOC_HEADER = '# MAGIC DOC:';

class MagicDocsManager {
  constructor() {
    this.registeredDocs = new Map(); // path → { title, lastUpdated }
  }

  /**
   * Detect if a file is a Magic Doc.
   */
  isMagicDoc(content) {
    return content.startsWith(MAGIC_DOC_HEADER);
  }

  /**
   * Register a Magic Doc for auto-updates.
   */
  register(filePath) {
    try {
      const content = fs.readFileSync(filePath, 'utf-8');
      if (!this.isMagicDoc(content)) return { error: 'Not a Magic Doc' };

      const titleMatch = content.match(/^# MAGIC DOC:\s*(.+)/);
      const title = titleMatch ? titleMatch[1].trim() : 'Untitled';

      this.registeredDocs.set(filePath, {
        title,
        path: filePath,
        lastUpdated: new Date().toISOString(),
        content,
      });

      return { registered: true, title, path: filePath };
    } catch (e) {
      return { error: e.message };
    }
  }

  /**
   * Update a Magic Doc with new information.
   */
  update(filePath, newContent) {
    const doc = this.registeredDocs.get(filePath);
    if (!doc) return { error: 'Doc not registered' };

    try {
      // Preserve the MAGIC DOC header
      const header = newContent.startsWith(MAGIC_DOC_HEADER)
        ? ''
        : `${MAGIC_DOC_HEADER} ${doc.title}\n\n`;

      fs.writeFileSync(filePath, header + newContent, 'utf-8');
      doc.lastUpdated = new Date().toISOString();
      doc.content = header + newContent;
      return { updated: true, path: filePath };
    } catch (e) {
      return { error: e.message };
    }
  }

  /**
   * List registered Magic Docs.
   */
  list() {
    return [...this.registeredDocs.values()].map(d => ({
      title: d.title,
      path: d.path,
      lastUpdated: d.lastUpdated,
    }));
  }

  /**
   * Scan directory for Magic Docs.
   */
  scanDirectory(dirPath) {
    const found = [];
    try {
      const files = fs.readdirSync(dirPath, { withFileTypes: true });
      for (const file of files) {
        if (file.isFile() && file.name.endsWith('.md')) {
          const fullPath = path.join(dirPath, file.name);
          const content = fs.readFileSync(fullPath, 'utf-8');
          if (this.isMagicDoc(content)) {
            this.register(fullPath);
            found.push(fullPath);
          }
        }
      }
    } catch {}
    return found;
  }
}

const magicDocs = new MagicDocsManager();

function register() {
  ipcMain.handle('magicdocs:register', async (event, { filePath }) => magicDocs.register(filePath));
  ipcMain.handle('magicdocs:update', async (event, { filePath, content }) => magicDocs.update(filePath, content));
  ipcMain.handle('magicdocs:list', async () => magicDocs.list());
  ipcMain.handle('magicdocs:scan', async (event, { dirPath }) => magicDocs.scanDirectory(dirPath));
}

module.exports = { register, magicDocs };
