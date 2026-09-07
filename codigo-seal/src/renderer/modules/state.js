/**
 * Código SEAL — Shared State
 * Singleton state object shared across all modules.
 */

export const state = {
  currentFolder: null,
  openFiles: [],
  activeFile: null,
  editor: null,
  editorModels: {},
  terminals: [],
  activeTermIdx: -1,
  get terminal() { return this.terminals[this.activeTermIdx]?.term || null; },
  get terminalId() { return this.terminals[this.activeTermIdx]?.ptyId || null; },
  get terminalFit() { return this.terminals[this.activeTermIdx]?.fit || null; },
  chatWs: null,
  chatConnected: false,
  soulAgent: 'ADA',
  managerInterval: null,
  intervals: [],
};

export const BRIDGE_URL = 'http://localhost:8766';
export const CHAT_WS = 'ws://localhost:8765/ws';
