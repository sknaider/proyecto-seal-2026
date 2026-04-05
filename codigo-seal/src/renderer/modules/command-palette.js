/**
 * Código SEAL — Command Palette Module
 * Ctrl+P palette with file, skill, and action search.
 */

import { state } from './state.js';
import { escHtml, getFileIcon } from './utils.js';
import { openFolder, newFile, setActiveFile } from './explorer.js';
import { activateBottomTab } from './bottom-panel.js';
import { activatePanel } from './keyboard.js';
import { addChatMessage } from './chat.js';

export function initCommandPalette() {
  const palette = document.getElementById('command-palette');
  const input = document.getElementById('palette-input');
  const results = document.getElementById('palette-results');
  const overlay = palette.querySelector('.palette-overlay');
  let selectedIdx = -1;

  function open() {
    palette.classList.remove('hidden');
    input.value = '';
    input.focus();
    selectedIdx = -1;
    renderResults('');
  }

  function close() {
    palette.classList.add('hidden');
    input.value = '';
  }

  overlay.addEventListener('click', close);
  input.addEventListener('input', () => { selectedIdx = -1; renderResults(input.value.trim().toLowerCase()); });
  input.addEventListener('keydown', (e) => {
    const items = results.querySelectorAll('.palette-item');
    if (e.key === 'Escape') { close(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); selectedIdx = Math.min(selectedIdx + 1, items.length - 1); updateSel(items); }
    if (e.key === 'ArrowUp') { e.preventDefault(); selectedIdx = Math.max(selectedIdx - 1, 0); updateSel(items); }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (selectedIdx >= 0 && items[selectedIdx]) items[selectedIdx].click();
      else if (items.length > 0) items[0].click();
    }
  });

  function updateSel(items) {
    items.forEach((it, i) => it.classList.toggle('selected', i === selectedIdx));
    if (items[selectedIdx]) items[selectedIdx].scrollIntoView({ block: 'nearest' });
  }

  function renderResults(query) {
    const entries = [];
    state.openFiles.forEach(f => {
      entries.push({ icon: getFileIcon(f.name), label: f.name, hint: f.path, category: 'Archivos abiertos', action: () => { setActiveFile(f); close(); } });
    });
    const actions = [
      { icon: '📁', label: 'Abrir carpeta', hint: 'Ctrl+Shift+O', action: () => { openFolder(); close(); } },
      { icon: '⚡', label: 'Terminal', hint: 'Ctrl+`', action: () => { activateBottomTab('terminal'); close(); } },
      { icon: '💬', label: 'Chat del Equipo', hint: 'Ctrl+Shift+C', action: () => { activateBottomTab('chat'); close(); } },
      { icon: '🧠', label: 'SOUL Dashboard', hint: 'Ctrl+Shift+U', action: () => { activatePanel('soul'); close(); } },
      { icon: '🎯', label: 'Manager View', hint: 'Ctrl+Shift+M', action: () => { activatePanel('manager'); close(); } },
      { icon: '🔍', label: 'Buscar en archivos', hint: 'Ctrl+Shift+F', action: () => { activatePanel('search'); close(); } },
    ];
    actions.forEach(a => entries.push({ ...a, category: 'Acciones' }));

    const filtered = query ? entries.filter(e => e.label.toLowerCase().includes(query) || (e.hint || '').toLowerCase().includes(query)) : entries;

    let html = '';
    let lastCat = '';
    for (const e of filtered.slice(0, 20)) {
      if (e.category !== lastCat) { html += `<div class="palette-category">${escHtml(e.category)}</div>`; lastCat = e.category; }
      html += `<div class="palette-item"><span class="palette-icon">${e.icon}</span><span class="palette-label">${escHtml(e.label)}</span><span class="palette-hint">${escHtml(e.hint || '')}</span></div>`;
    }
    results.innerHTML = html || '<div class="palette-item"><span class="palette-label" style="color:var(--seal-text-dim)">Sin resultados</span></div>';

    results.querySelectorAll('.palette-item').forEach((el, i) => {
      const entry = filtered[i];
      if (entry) el.addEventListener('click', entry.action);
    });
  }

  window._openPalette = open;
}
