/**
 * Código SEAL — AutoDream Memory Consolidation (Phase 4.5)
 * Automatically consolidates memories after threshold (24h + 5 sessions).
 * Orient → Gather → Consolidate → Prune.
 * Based on: Claude Code autoDream (HIDDEN_FEATURES #2). Clean-room.
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');

const DREAM_STATE_FILE = path.join(os.homedir(), '.seal', 'dream_state.json');

class AutoDreamManager {
  constructor() {
    this.state = this._loadState();
  }

  /**
   * Check if dream should fire.
   */
  shouldDream() {
    const hoursSinceLastDream = (Date.now() - (this.state.lastDreamTime || 0)) / 3600000;
    const sessionsSinceDream = this.state.sessionCount - (this.state.lastDreamSession || 0);
    return hoursSinceLastDream >= 24 && sessionsSinceDream >= 5;
  }

  /**
   * Record a new session.
   */
  recordSession() {
    this.state.sessionCount = (this.state.sessionCount || 0) + 1;
    this._saveState();
  }

  /**
   * Execute dream consolidation (called via SEAL MCP bridge).
   */
  async executeDream(agent) {
    if (!this.shouldDream()) return { skipped: true, reason: 'Threshold not met' };

    try {
      // Call SEAL bridge to trigger dream
      const res = await fetch(`http://localhost:8766/api/dream/run?agent=${agent || 'ADA'}`, {
        method: 'POST', timeout: 60000,
      });
      const result = await res.json();

      // Update state
      this.state.lastDreamTime = Date.now();
      this.state.lastDreamSession = this.state.sessionCount;
      this.state.dreamCount = (this.state.dreamCount || 0) + 1;
      this._saveState();

      return { success: true, dreamCount: this.state.dreamCount, result };
    } catch (e) {
      return { error: e.message };
    }
  }

  /**
   * Get dream status.
   */
  getStatus() {
    const hoursSinceLastDream = (Date.now() - (this.state.lastDreamTime || 0)) / 3600000;
    const sessionsSinceDream = this.state.sessionCount - (this.state.lastDreamSession || 0);
    return {
      shouldDream: this.shouldDream(),
      hoursSinceLastDream: Math.round(hoursSinceLastDream),
      sessionsSinceDream,
      totalDreams: this.state.dreamCount || 0,
      totalSessions: this.state.sessionCount || 0,
    };
  }

  _loadState() {
    try {
      if (fs.existsSync(DREAM_STATE_FILE)) {
        return JSON.parse(fs.readFileSync(DREAM_STATE_FILE, 'utf-8'));
      }
    } catch {}
    return { sessionCount: 0, lastDreamTime: 0, lastDreamSession: 0, dreamCount: 0 };
  }

  _saveState() {
    try {
      fs.mkdirSync(path.dirname(DREAM_STATE_FILE), { recursive: true });
      fs.writeFileSync(DREAM_STATE_FILE, JSON.stringify(this.state, null, 2));
    } catch {}
  }
}

const autoDream = new AutoDreamManager();

function register() {
  ipcMain.handle('dream:status', async () => autoDream.getStatus());
  ipcMain.handle('dream:execute', async (event, { agent }) => autoDream.executeDream(agent));
  ipcMain.handle('dream:recordSession', async () => { autoDream.recordSession(); return { ok: true }; });
}

module.exports = { register, autoDream };
