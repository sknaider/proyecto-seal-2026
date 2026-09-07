/**
 * Código SEAL — Coordinator Mode
 * Formalizes JARVIS→ADA pattern: coordinator plans, workers execute.
 * Coordinator can ONLY: delegate tasks, read scratchpad, send messages.
 * Workers execute and report via task notifications.
 * Based on: Claude Code coordinator (HIDDEN_FEATURES #3). Clean-room.
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');

const SCRATCHPAD_DIR = path.join(os.homedir(), '.seal', 'scratchpad');

class CoordinatorMode {
  constructor() {
    this.mode = 'normal'; // normal | coordinator | worker
    this.tasks = new Map(); // id → { name, assignee, status, description, output }
    this.taskCounter = 0;
    fs.mkdirSync(SCRATCHPAD_DIR, { recursive: true });
  }

  /**
   * Set mode for current agent.
   */
  setMode(newMode) {
    if (!['normal', 'coordinator', 'worker'].includes(newMode)) {
      return { error: `Invalid mode: ${newMode}` };
    }
    this.mode = newMode;
    return { mode: this.mode };
  }

  /**
   * Get allowed tools for current mode.
   */
  getAllowedTools() {
    switch (this.mode) {
      case 'coordinator':
        return ['AgentTool', 'SendMessage', 'TaskStop', 'ReadScratchpad', 'WriteScratchpad'];
      case 'worker':
        return null; // All tools allowed for workers
      default:
        return null; // Normal mode: all tools
    }
  }

  /**
   * Create a task for delegation.
   */
  createTask({ name, description, assignee, priority }) {
    const id = `T-${String(++this.taskCounter).padStart(3, '0')}`;
    const task = {
      id, name, description,
      assignee: assignee || 'ADA',
      priority: priority || 'normal',
      status: 'planned', // planned → assigned → in_progress → review → completed | failed
      output: [],
      createdAt: new Date().toISOString(),
      completedAt: null,
    };
    this.tasks.set(id, task);
    // Write to scratchpad
    this._writeTaskFile(task);
    return task;
  }

  /**
   * Update task status (worker reports progress).
   */
  updateTask(id, { status, output, result }) {
    const task = this.tasks.get(id);
    if (!task) return { error: 'Task not found' };

    if (status) task.status = status;
    if (output) task.output.push({ text: output, time: new Date().toISOString() });
    if (result) task.result = result;
    if (status === 'completed' || status === 'failed') task.completedAt = new Date().toISOString();

    this._writeTaskFile(task);
    return task;
  }

  /**
   * Get all tasks (Kanban view).
   */
  getBoard() {
    const board = {
      planned: [],
      assigned: [],
      in_progress: [],
      review: [],
      completed: [],
      failed: [],
    };
    for (const task of this.tasks.values()) {
      if (board[task.status]) board[task.status].push(task);
    }
    return board;
  }

  /**
   * Read scratchpad file.
   */
  readScratchpad(filename) {
    try {
      const filePath = path.join(SCRATCHPAD_DIR, filename || 'plan.md');
      if (!fs.existsSync(filePath)) return { content: '', exists: false };
      return { content: fs.readFileSync(filePath, 'utf-8'), exists: true };
    } catch (e) { return { error: e.message }; }
  }

  /**
   * Write to scratchpad.
   */
  writeScratchpad(filename, content) {
    try {
      fs.writeFileSync(path.join(SCRATCHPAD_DIR, filename || 'plan.md'), content, 'utf-8');
      return { ok: true };
    } catch (e) { return { error: e.message }; }
  }

  /**
   * List scratchpad files.
   */
  listScratchpad() {
    try {
      return fs.readdirSync(SCRATCHPAD_DIR).map(f => ({
        name: f,
        size: fs.statSync(path.join(SCRATCHPAD_DIR, f)).size,
      }));
    } catch { return []; }
  }

  /**
   * Get status.json for machine-readable board.
   */
  getStatus() {
    return {
      mode: this.mode,
      taskCount: this.tasks.size,
      board: this.getBoard(),
      scratchpadFiles: this.listScratchpad(),
    };
  }

  _writeTaskFile(task) {
    try {
      const filePath = path.join(SCRATCHPAD_DIR, `task_${task.id}.md`);
      const content = `# ${task.id}: ${task.name}\n\n` +
        `**Assignee:** ${task.assignee}\n` +
        `**Status:** ${task.status}\n` +
        `**Priority:** ${task.priority}\n` +
        `**Created:** ${task.createdAt}\n\n` +
        `## Description\n${task.description || 'N/A'}\n\n` +
        `## Output\n${task.output.map(o => `[${o.time}] ${o.text}`).join('\n') || 'None'}\n`;
      fs.writeFileSync(filePath, content, 'utf-8');
    } catch {}
  }
}

const coordinator = new CoordinatorMode();

function register(mainWindow) {
  ipcMain.handle('coordinator:setMode', async (event, { mode }) => coordinator.setMode(mode));
  ipcMain.handle('coordinator:getMode', async () => ({ mode: coordinator.mode, allowedTools: coordinator.getAllowedTools() }));
  ipcMain.handle('coordinator:createTask', async (event, opts) => coordinator.createTask(opts));
  ipcMain.handle('coordinator:updateTask', async (event, { id, ...update }) => coordinator.updateTask(id, update));
  ipcMain.handle('coordinator:board', async () => coordinator.getBoard());
  ipcMain.handle('coordinator:status', async () => coordinator.getStatus());
  ipcMain.handle('coordinator:readScratchpad', async (event, { filename }) => coordinator.readScratchpad(filename));
  ipcMain.handle('coordinator:writeScratchpad', async (event, { filename, content }) => coordinator.writeScratchpad(filename, content));
  ipcMain.handle('coordinator:listScratchpad', async () => coordinator.listScratchpad());
}

module.exports = { register, coordinator };
