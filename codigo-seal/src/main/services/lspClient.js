/**
 * Código SEAL — LSP Client Manager
 * Connects to Language Server Protocol servers for diagnostics.
 * Receives errors/warnings passively and forwards to renderer.
 * Based on: Claude Code LSP passive feedback (SPEC_16). Clean-room.
 */

const { ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

// LSP server configs per language
const LSP_SERVERS = {
  python: {
    command: 'pyright-langserver',
    args: ['--stdio'],
    languages: ['python'],
    fileExts: ['.py'],
  },
  typescript: {
    command: 'typescript-language-server',
    args: ['--stdio'],
    languages: ['typescript', 'javascript'],
    fileExts: ['.ts', '.tsx', '.js', '.jsx'],
  },
};

class LSPClientManager {
  constructor() {
    this.clients = new Map(); // lang → { process, pending, initialized }
    this.diagnostics = new Map(); // uri → [{ severity, message, range }]
    this.mainWindow = null;
    this.maxDiagnosticsPerFile = 10;
    this.maxTotalDiagnostics = 30;
  }

  setMainWindow(win) {
    this.mainWindow = win;
  }

  /**
   * Start LSP server for a language.
   */
  async start(lang) {
    const config = LSP_SERVERS[lang];
    if (!config) return { error: `No LSP server for ${lang}` };

    if (this.clients.has(lang)) return { status: 'already_running' };

    try {
      const proc = spawn(config.command, config.args, {
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env },
      });

      const client = {
        process: proc,
        initialized: false,
        requestId: 0,
        pending: new Map(),
      };

      this.clients.set(lang, client);

      // Handle LSP messages from stdout
      let buffer = '';
      proc.stdout.on('data', (data) => {
        buffer += data.toString();
        while (true) {
          const headerEnd = buffer.indexOf('\r\n\r\n');
          if (headerEnd === -1) break;
          const header = buffer.slice(0, headerEnd);
          const lengthMatch = header.match(/Content-Length:\s*(\d+)/i);
          if (!lengthMatch) { buffer = buffer.slice(headerEnd + 4); continue; }
          const length = parseInt(lengthMatch[1]);
          const bodyStart = headerEnd + 4;
          if (buffer.length < bodyStart + length) break;
          const body = buffer.slice(bodyStart, bodyStart + length);
          buffer = buffer.slice(bodyStart + length);
          try {
            this.handleMessage(lang, JSON.parse(body));
          } catch {}
        }
      });

      proc.on('error', (err) => {
        console.warn(`[LSP:${lang}] Process error: ${err.message}`);
        this.clients.delete(lang);
      });

      proc.on('exit', () => {
        this.clients.delete(lang);
      });

      // Send initialize
      await this.sendRequest(lang, 'initialize', {
        processId: process.pid,
        capabilities: {
          textDocument: {
            publishDiagnostics: { relatedInformation: true },
          },
        },
        rootUri: null,
      });

      client.initialized = true;
      return { status: 'started', lang };
    } catch (err) {
      return { error: err.message };
    }
  }

  /**
   * Send LSP request.
   */
  sendRequest(lang, method, params) {
    const client = this.clients.get(lang);
    if (!client) return Promise.reject(new Error(`No client for ${lang}`));

    const id = ++client.requestId;
    const message = JSON.stringify({ jsonrpc: '2.0', id, method, params });
    const header = `Content-Length: ${Buffer.byteLength(message)}\r\n\r\n`;

    return new Promise((resolve, reject) => {
      client.pending.set(id, { resolve, reject });
      client.process.stdin.write(header + message);
      setTimeout(() => {
        if (client.pending.has(id)) {
          client.pending.delete(id);
          reject(new Error('LSP timeout'));
        }
      }, 10000);
    });
  }

  /**
   * Send LSP notification (no response expected).
   */
  sendNotification(lang, method, params) {
    const client = this.clients.get(lang);
    if (!client) return;
    const message = JSON.stringify({ jsonrpc: '2.0', method, params });
    const header = `Content-Length: ${Buffer.byteLength(message)}\r\n\r\n`;
    client.process.stdin.write(header + message);
  }

  /**
   * Handle incoming LSP message.
   */
  handleMessage(lang, msg) {
    // Response to our request
    if (msg.id && this.clients.get(lang)?.pending.has(msg.id)) {
      const { resolve, reject } = this.clients.get(lang).pending.get(msg.id);
      this.clients.get(lang).pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg.result);
      return;
    }

    // Server notification
    if (msg.method === 'textDocument/publishDiagnostics') {
      this.handleDiagnostics(msg.params);
    }
  }

  /**
   * Handle diagnostics notification.
   */
  handleDiagnostics(params) {
    const { uri, diagnostics } = params;

    // Store diagnostics (capped per file)
    const limited = diagnostics
      .sort((a, b) => (a.severity || 4) - (b.severity || 4))
      .slice(0, this.maxDiagnosticsPerFile)
      .map(d => ({
        severity: d.severity, // 1=Error, 2=Warning, 3=Info, 4=Hint
        message: d.message,
        range: d.range,
        source: d.source,
      }));

    if (limited.length > 0) {
      this.diagnostics.set(uri, limited);
    } else {
      this.diagnostics.delete(uri);
    }

    // Forward to renderer
    if (this.mainWindow && this.mainWindow.webContents) {
      this.mainWindow.webContents.send('lsp:diagnostics', { uri, diagnostics: limited });
    }
  }

  /**
   * Notify LSP that a file was opened.
   */
  notifyFileOpened(filePath, content, languageId) {
    for (const [lang, config] of Object.entries(LSP_SERVERS)) {
      if (config.languages.includes(languageId) && this.clients.has(lang)) {
        this.sendNotification(lang, 'textDocument/didOpen', {
          textDocument: {
            uri: `file://${filePath}`,
            languageId,
            version: 1,
            text: content,
          },
        });
      }
    }
  }

  /**
   * Get all current diagnostics.
   */
  getAllDiagnostics() {
    const all = [];
    for (const [uri, diags] of this.diagnostics) {
      for (const d of diags) {
        all.push({ uri, ...d });
      }
    }
    return all
      .sort((a, b) => (a.severity || 4) - (b.severity || 4))
      .slice(0, this.maxTotalDiagnostics);
  }

  /**
   * Stop all LSP servers.
   */
  stopAll() {
    for (const [lang, client] of this.clients) {
      try { client.process.kill(); } catch {}
    }
    this.clients.clear();
    this.diagnostics.clear();
  }
}

// Singleton
const lspManager = new LSPClientManager();

// IPC handlers
function register(mainWindow) {
  lspManager.setMainWindow(mainWindow);

  ipcMain.handle('lsp:start', async (event, { lang }) => lspManager.start(lang));
  ipcMain.handle('lsp:diagnostics', async () => lspManager.getAllDiagnostics());
  ipcMain.handle('lsp:fileOpened', async (event, { filePath, content, languageId }) => {
    lspManager.notifyFileOpened(filePath, content, languageId);
    return { ok: true };
  });
}

function cleanup() {
  lspManager.stopAll();
}

module.exports = { register, cleanup, lspManager };
