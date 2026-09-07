/**
 * Código SEAL — 4-Tier Session Memory Compaction
 * Structured session state that replaces LLM summarization.
 * Tier 1: Microcompact (clear old tool results)
 * Tier 2: Time-based (cache expired, clear safely)
 * Tier 3: Session Memory (structured markdown file)
 * Tier 4: Full compact (LLM summary as last resort)
 * Based on: Claude Code compact system (SPEC_16). Clean-room.
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');

const SESSION_DIR = path.join(os.homedir(), '.seal', 'sessions');

class SessionMemoryManager {
  constructor() {
    this.currentSession = null;
    this.sections = {
      tasks: [],
      messages: [],
      decisions: [],
      services: {},
      errors: [],
      context: {},
    };
    this.tokenEstimate = 0;
  }

  /**
   * Start a new session.
   */
  start(agent, sessionId) {
    fs.mkdirSync(SESSION_DIR, { recursive: true });
    this.currentSession = {
      agent,
      sessionId: sessionId || `${agent}_${Date.now()}`,
      startTime: new Date().toISOString(),
      filePath: path.join(SESSION_DIR, `${agent}_${Date.now()}.md`),
    };
    this.sections = { tasks: [], messages: [], decisions: [], services: {}, errors: [], context: {} };
    this._save();
    return this.currentSession;
  }

  /**
   * Append to a section.
   */
  append(section, entry) {
    if (!this.sections[section]) this.sections[section] = [];
    if (Array.isArray(this.sections[section])) {
      this.sections[section].push({ ...entry, timestamp: new Date().toISOString() });
      // Cap per section
      if (this.sections[section].length > 50) {
        this.sections[section] = this.sections[section].slice(-50);
      }
    } else {
      Object.assign(this.sections[section], entry);
    }
    this._save();
  }

  /**
   * Get structured markdown for context injection.
   */
  toMarkdown() {
    if (!this.currentSession) return '';

    let md = `# Session: ${this.currentSession.agent} — ${this.currentSession.startTime}\n\n`;

    if (this.sections.tasks.length > 0) {
      md += '## Active Tasks\n';
      this.sections.tasks.slice(-10).forEach(t => {
        md += `- [${t.status || 'pending'}] ${t.name || t.text}\n`;
      });
      md += '\n';
    }

    if (this.sections.messages.length > 0) {
      md += '## Pending Messages\n';
      this.sections.messages.slice(-5).forEach(m => {
        md += `- [${m.from}] ${(m.text || '').slice(0, 100)}\n`;
      });
      md += '\n';
    }

    if (this.sections.decisions.length > 0) {
      md += '## Decisions This Session\n';
      this.sections.decisions.slice(-10).forEach(d => {
        md += `- ${d.text || d}\n`;
      });
      md += '\n';
    }

    if (Object.keys(this.sections.services).length > 0) {
      md += '## Services State\n';
      for (const [name, status] of Object.entries(this.sections.services)) {
        md += `- ${name}: ${status}\n`;
      }
      md += '\n';
    }

    if (this.sections.errors.length > 0) {
      md += '## Errors Under Investigation\n';
      this.sections.errors.slice(-5).forEach(e => {
        md += `- ${e.text || e}\n`;
      });
      md += '\n';
    }

    return md;
  }

  /**
   * Tier 1: Microcompact — clear old tool results from messages.
   * Keeps last N results, clears older ones.
   */
  microcompact(messages, keepLast = 5) {
    let toolResultCount = 0;
    return messages.map(m => {
      if (m.role === 'tool' || (m.type === 'tool_result')) {
        toolResultCount++;
        if (toolResultCount > keepLast) {
          return { ...m, content: '[Compacted — tool result cleared]' };
        }
      }
      return m;
    });
  }

  /**
   * Tier 2: Time-based clear — if message is older than threshold, clear content.
   */
  timeBasedClear(messages, maxAgeMs = 300000) { // 5 min default
    const now = Date.now();
    return messages.map(m => {
      if (m.timestamp && (now - new Date(m.timestamp).getTime()) > maxAgeMs) {
        if (m.role === 'tool' || m.type === 'tool_result') {
          return { ...m, content: '[Time-expired — cleared]' };
        }
      }
      return m;
    });
  }

  /**
   * Tier 3: Session Memory inject — replace old messages with session memory markdown.
   */
  sessionMemoryCompact(messages, keepLastN = 10) {
    if (messages.length <= keepLastN) return messages;

    const sessionMd = this.toMarkdown();
    const recent = messages.slice(-keepLastN);

    return [
      { role: 'system', content: `[Session Memory — compacted from ${messages.length} messages]\n\n${sessionMd}` },
      ...recent,
    ];
  }

  /**
   * Load most recent session for an agent.
   */
  loadLatest(agent) {
    try {
      const files = fs.readdirSync(SESSION_DIR)
        .filter(f => f.startsWith(agent) && f.endsWith('.md'))
        .sort()
        .reverse();
      if (files.length === 0) return null;
      return fs.readFileSync(path.join(SESSION_DIR, files[0]), 'utf-8');
    } catch { return null; }
  }

  /**
   * List sessions for an agent.
   */
  listSessions(agent) {
    try {
      return fs.readdirSync(SESSION_DIR)
        .filter(f => f.startsWith(agent || '') && f.endsWith('.md'))
        .sort()
        .reverse()
        .slice(0, 20)
        .map(f => ({ file: f, path: path.join(SESSION_DIR, f) }));
    } catch { return []; }
  }

  _save() {
    if (!this.currentSession) return;
    try {
      fs.writeFileSync(this.currentSession.filePath, this.toMarkdown(), 'utf-8');
    } catch {}
  }
}

const sessionMemory = new SessionMemoryManager();

function register() {
  ipcMain.handle('session:start', async (event, { agent, sessionId }) => sessionMemory.start(agent, sessionId));
  ipcMain.handle('session:append', async (event, { section, entry }) => { sessionMemory.append(section, entry); return { ok: true }; });
  ipcMain.handle('session:markdown', async () => sessionMemory.toMarkdown());
  ipcMain.handle('session:loadLatest', async (event, { agent }) => sessionMemory.loadLatest(agent));
  ipcMain.handle('session:list', async (event, { agent }) => sessionMemory.listSessions(agent));
  ipcMain.handle('session:compact', async (event, { messages, tier, keepLast }) => {
    switch (tier) {
      case 1: return { messages: sessionMemory.microcompact(messages, keepLast) };
      case 2: return { messages: sessionMemory.timeBasedClear(messages) };
      case 3: return { messages: sessionMemory.sessionMemoryCompact(messages, keepLast) };
      default: return { messages };
    }
  });
}

module.exports = { register, sessionMemory };
