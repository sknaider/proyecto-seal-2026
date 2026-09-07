/**
 * Código SEAL — Swarm Permission Sync (Phase 4.3)
 * File-based mailbox for inter-agent permission requests.
 * Workers request permission → Leader approves/denies → Worker proceeds.
 * Based on: Claude Code swarm permissionSync. Clean-room.
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PERMISSIONS_DIR = path.join(os.homedir(), '.seal', 'permissions');
const PENDING_DIR = path.join(PERMISSIONS_DIR, 'pending');
const RESOLVED_DIR = path.join(PERMISSIONS_DIR, 'resolved');

class SwarmPermissionManager {
  constructor() {
    fs.mkdirSync(PENDING_DIR, { recursive: true });
    fs.mkdirSync(RESOLVED_DIR, { recursive: true });
  }

  /**
   * Worker requests permission.
   */
  request({ agent, tool, input, reason }) {
    const id = `perm_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const request = {
      id, agent, tool, input: typeof input === 'string' ? input.slice(0, 500) : JSON.stringify(input).slice(0, 500),
      reason, status: 'pending', requestedAt: new Date().toISOString(),
    };
    fs.writeFileSync(path.join(PENDING_DIR, `${id}.json`), JSON.stringify(request, null, 2));
    return { id, status: 'pending' };
  }

  /**
   * Leader resolves a permission request.
   */
  resolve(id, { approved, reason }) {
    const pendingPath = path.join(PENDING_DIR, `${id}.json`);
    if (!fs.existsSync(pendingPath)) return { error: 'Request not found' };

    const request = JSON.parse(fs.readFileSync(pendingPath, 'utf-8'));
    request.status = approved ? 'approved' : 'denied';
    request.resolvedReason = reason;
    request.resolvedAt = new Date().toISOString();

    // Move to resolved
    fs.writeFileSync(path.join(RESOLVED_DIR, `${id}.json`), JSON.stringify(request, null, 2));
    fs.unlinkSync(pendingPath);

    return request;
  }

  /**
   * Worker polls for resolution.
   */
  poll(id) {
    const resolvedPath = path.join(RESOLVED_DIR, `${id}.json`);
    if (!fs.existsSync(resolvedPath)) return { status: 'pending' };
    return JSON.parse(fs.readFileSync(resolvedPath, 'utf-8'));
  }

  /**
   * List pending requests (for leader UI).
   */
  listPending() {
    try {
      return fs.readdirSync(PENDING_DIR)
        .filter(f => f.endsWith('.json'))
        .map(f => JSON.parse(fs.readFileSync(path.join(PENDING_DIR, f), 'utf-8')));
    } catch { return []; }
  }

  /**
   * Cleanup old resolved (>1 hour).
   */
  cleanup() {
    const cutoff = Date.now() - 3600000;
    try {
      for (const f of fs.readdirSync(RESOLVED_DIR)) {
        const p = path.join(RESOLVED_DIR, f);
        if (fs.statSync(p).mtimeMs < cutoff) fs.unlinkSync(p);
      }
    } catch {}
  }
}

const permManager = new SwarmPermissionManager();

function register() {
  ipcMain.handle('swarm:requestPermission', async (event, opts) => permManager.request(opts));
  ipcMain.handle('swarm:resolvePermission', async (event, { id, approved, reason }) => permManager.resolve(id, { approved, reason }));
  ipcMain.handle('swarm:pollPermission', async (event, { id }) => permManager.poll(id));
  ipcMain.handle('swarm:listPending', async () => permManager.listPending());
  ipcMain.handle('swarm:cleanup', async () => { permManager.cleanup(); return { ok: true }; });
}

module.exports = { register, permManager };
