/**
 * Código SEAL — Bottom Panel Module
 * Chat + Terminal bottom panel with tabs, expand/collapse.
 */

import { state } from './state.js';
import { ensureTerminal, createTerminal } from './terminal.js';

export function expandBottomPanel() {
  const w = document.getElementById('chat-wrapper');
  const b = document.getElementById('btn-toggle-bottom');
  w.classList.remove('collapsed'); w.classList.add('expanded');
  b.textContent = '\u25B2';
}

export function collapseBottomPanel() {
  const w = document.getElementById('chat-wrapper');
  const b = document.getElementById('btn-toggle-bottom');
  w.classList.remove('expanded'); w.classList.add('collapsed');
  b.textContent = '\u25BC';
}

export function initBottomPanel() {
  const wrapper = document.getElementById('chat-wrapper');
  const toggleBtn = document.getElementById('btn-toggle-bottom');

  toggleBtn.addEventListener('click', () => {
    if (wrapper.classList.contains('collapsed')) {
      expandBottomPanel();
      if (document.getElementById('terminal-bottom-panel').classList.contains('active')) ensureTerminal();
      if (state.terminalFit) setTimeout(() => state.terminalFit.fit(), 50);
    } else {
      collapseBottomPanel();
    }
  });

  document.querySelectorAll('.bottom-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.bottom-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.bottom-content').forEach(c => c.classList.remove('active'));
      tab.classList.add('active');
      const panel = document.getElementById(`${tab.dataset.bottom}-bottom-panel`);
      if (panel) panel.classList.add('active');
      if (wrapper.classList.contains('collapsed')) expandBottomPanel();
      if (tab.dataset.bottom === 'terminal') ensureTerminal();
      if (tab.dataset.bottom === 'terminal' && state.terminalFit) setTimeout(() => state.terminalFit.fit(), 50);
    });
  });
}

export function activateBottomTab(name) {
  const tab = document.querySelector(`.bottom-tab[data-bottom="${name}"]`);
  if (tab) {
    document.querySelectorAll('.bottom-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.bottom-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    const panel = document.getElementById(`${name}-bottom-panel`);
    if (panel) panel.classList.add('active');
  }
  if (document.getElementById('chat-wrapper').classList.contains('collapsed')) expandBottomPanel();
  if (name === 'terminal') ensureTerminal();
  if (name === 'terminal' && state.terminalFit) setTimeout(() => state.terminalFit.fit(), 50);
  if (name === 'chat') {
    const input = document.getElementById('chat-input-main');
    if (input) setTimeout(() => input.focus(), 50);
  }
}
