/**
 * Código SEAL — Terminal Module
 * Multi-terminal with tabs, PTY via IPC, resize observer.
 */

import { state } from './state.js';
import { escHtml } from './utils.js';

let termListenersRegistered = false;

export function ensureTerminal() {
  if (state.terminals.length === 0) createTerminal();
}

export function initTerminalButtons() {
  document.getElementById('btn-add-terminal')?.addEventListener('click', createTerminal);
  if (!termListenersRegistered && window.seal && window.seal.terminal) {
    window.seal.terminal.onData(({ id, data }) => {
      const t = state.terminals.find(t => t.ptyId === id);
      if (t) t.term.write(data);
    });
    window.seal.terminal.onExit(({ id }) => {
      const idx = state.terminals.findIndex(t => t.ptyId === id);
      if (idx === -1) return;
      state.terminals[idx].term.writeln('\r\n\x1b[33m[Terminal cerrado]\x1b[0m');
      removeTerminalTab(idx);
    });
    termListenersRegistered = true;
  }
}

export async function createTerminal() {
  const container = document.getElementById('xterm-bottom');
  if (!container) return;

  const idx = state.terminals.length;
  const termName = `Terminal ${idx + 1}`;

  const term = new Terminal({
    theme: {
      background: '#0a0a0f', foreground: '#e2e8f0', cursor: '#3b82f6',
      selectionBackground: '#3b82f640',
      black: '#0a0a0f', red: '#ef4444', green: '#22c55e', yellow: '#f59e0b',
      blue: '#3b82f6', magenta: '#a78bfa', cyan: '#06b6d4', white: '#e2e8f0',
    },
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    fontSize: 12, cursorBlink: true, cursorStyle: 'bar',
  });

  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);

  const termDiv = document.createElement('div');
  termDiv.id = `xterm-instance-${idx}`;
  termDiv.style.cssText = 'width:100%;height:100%;display:none;';
  container.appendChild(termDiv);

  term.open(termDiv);

  const termEntry = { id: idx, term, fit: fitAddon, ptyId: null, name: termName, el: termDiv, resizeObserver: null };
  state.terminals.push(termEntry);

  if (window.seal && window.seal.terminal) {
    const result = await window.seal.terminal.create({
      cols: term.cols, rows: term.rows, cwd: state.currentFolder || undefined,
    });
    if (result && result.id) {
      termEntry.ptyId = result.id;
      term.onData(data => window.seal.terminal.write(result.id, data));
    }
  } else {
    term.writeln('\x1b[36m  ╔═══════════════════════════════════════╗\x1b[0m');
    term.writeln('\x1b[36m  ║     🔭 Código SEAL — Terminal        ║\x1b[0m');
    term.writeln('\x1b[36m  ╚═══════════════════════════════════════╝\x1b[0m');
    term.writeln('');
    term.writeln(`\x1b[33m  ${termName} — Terminal disponible en Electron.\x1b[0m`);
  }

  const ro = new ResizeObserver(() => { try { fitAddon.fit(); } catch {} });
  ro.observe(termDiv);
  termEntry.resizeObserver = ro;

  switchTerminal(idx);
  renderTerminalTabs();
  setTimeout(() => fitAddon.fit(), 100);
}

export function switchTerminal(idx) {
  state.terminals.forEach((t, i) => {
    t.el.style.display = i === idx ? 'block' : 'none';
  });
  state.activeTermIdx = idx;
  const active = state.terminals[idx];
  if (active) setTimeout(() => { active.fit.fit(); active.term.focus(); }, 50);
  renderTerminalTabs();
}

export function renderTerminalTabs() {
  const tabsEl = document.getElementById('terminal-tabs');
  if (!tabsEl) return;
  tabsEl.innerHTML = state.terminals.map((t, i) =>
    `<button class="term-tab ${i === state.activeTermIdx ? 'active' : ''}" data-idx="${i}">
      ⚡ ${escHtml(t.name)}<span class="term-tab-close" data-close="${i}">×</span>
    </button>`
  ).join('');

  tabsEl.querySelectorAll('.term-tab').forEach(btn => {
    btn.addEventListener('click', (e) => {
      if (e.target.classList.contains('term-tab-close')) return;
      switchTerminal(parseInt(btn.dataset.idx));
    });
  });
  tabsEl.querySelectorAll('.term-tab-close').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      removeTerminalTab(parseInt(btn.dataset.close));
    });
  });
}

export function removeTerminalTab(idx) {
  if (idx < 0 || idx >= state.terminals.length) return;
  const t = state.terminals[idx];
  if (!t) return;
  if (t.ptyId && window.seal && window.seal.terminal) window.seal.terminal.kill(t.ptyId);
  if (t.resizeObserver) t.resizeObserver.disconnect();
  t.term.dispose();
  t.el.remove();
  state.terminals.splice(idx, 1);
  state.terminals.forEach((t, i) => { t.id = i; });
  if (idx < state.activeTermIdx) state.activeTermIdx--;
  if (state.activeTermIdx >= state.terminals.length) state.activeTermIdx = state.terminals.length - 1;
  if (state.terminals.length > 0) switchTerminal(state.activeTermIdx);
  else { state.activeTermIdx = -1; renderTerminalTabs(); }
}
