/**
 * Código SEAL — Keyboard & Menu Module
 * Shortcuts, menu events, activity panel switching.
 */

import { state } from './state.js';
import { sealRequest, showToast } from './utils.js';
import { saveCurrentFile } from './editor.js';
import { activateBottomTab } from './bottom-panel.js';
import { openFolder, newFile, openFileDialog, saveFileAs, cycleTab, closeTab } from './explorer.js';
import { fetchSOUL } from './soul.js';
import { fetchGitStatus } from './git.js';

export function initKeyboard() {
  document.addEventListener('keydown', e => {
    if (e.key === '`' && e.ctrlKey) { e.preventDefault(); activateBottomTab('terminal'); }
    if (e.key === 'C' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activateBottomTab('chat'); }
    if (e.key === 'U' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('soul'); }
    if (e.key === 'E' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('explorer'); }
    if (e.key === 'F' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('search'); document.getElementById('search-input')?.focus(); }
    if (e.key === 'G' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('git'); fetchGitStatus(); }
    if (e.key === 'M' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('manager'); }
    if (e.key === 'n' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); newFile(); }
    if (e.key === 'Tab' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); cycleTab(1); }
    if (e.key === 'Tab' && e.ctrlKey && e.shiftKey) { e.preventDefault(); cycleTab(-1); }
    if (e.key === 'w' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); if (state.activeFile) closeTab(state.activeFile); }
    if (e.key === 'p' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); if (window._openPalette) window._openPalette(); }
  });
}

export function activatePanel(name) {
  document.querySelectorAll('.activity-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.side-panel').forEach(p => p.classList.remove('active'));
  const btn = document.querySelector(`.activity-btn[data-panel="${name}"]`);
  const panel = document.getElementById(`panel-${name}`);
  if (btn) btn.classList.add('active');
  if (panel) panel.classList.add('active');
}

export function initMenuEvents() {
  if (!window.seal || !window.seal.on) return;
  window.seal.on('new-file', newFile);
  window.seal.on('open-file', openFileDialog);
  window.seal.on('open-folder', openFolder);
  window.seal.on('open-browser', async () => {
    if (window.seal && window.seal.browser) await window.seal.browser.open('https://www.google.com');
  });
  window.seal.on('open-scratchpad', () => showToast('Scratchpad: próximamente', 'info'));
  window.seal.on('open-settings', () => showToast('Configuración: próximamente', 'info'));
  window.seal.on('emotional-variance', () => { activatePanel('soul'); fetchSOUL(); });
  window.seal.on('toggle-terminal', () => activateBottomTab('terminal'));
  window.seal.on('toggle-chat', () => activateBottomTab('chat'));
  window.seal.on('toggle-soul', () => activatePanel('soul'));
  window.seal.on('toggle-explorer', () => activatePanel('explorer'));
  window.seal.on('soul-snapshot', () => { activatePanel('soul'); fetchSOUL(); });
  window.seal.on('save-file', () => saveCurrentFile());
  window.seal.on('save-file-as', saveFileAs);
  window.seal.on('switch-agent', (agent) => {
    state.soulAgent = agent;
    fetchSOUL();
    const el = document.getElementById('status-agent');
    if (el) el.textContent = `🔭 ${agent}`;
  });
  window.seal.on('run-dream', async () => {
    try { await sealRequest('/api/dream/check?agent=ADA'); } catch (e) { console.warn('[Dream] failed:', e.message); }
  });
}
