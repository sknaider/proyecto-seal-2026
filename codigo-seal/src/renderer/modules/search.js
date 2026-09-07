/**
 * Código SEAL — Search Module
 * Search in files via single IPC call to main process.
 */

import { state } from './state.js';
import { escHtml } from './utils.js';
import { openFile } from './explorer.js';

export function initSearch() {
  const input = document.getElementById('search-input');
  if (!input) return;
  let searchTimeout = null;
  input.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    const query = input.value.trim();
    if (query.length < 2) { document.getElementById('search-results').innerHTML = ''; return; }
    searchTimeout = setTimeout(() => searchInFiles(query), 300);
  });
}

async function searchInFiles(query) {
  const resultsEl = document.getElementById('search-results');
  if (!resultsEl || !state.currentFolder) {
    if (resultsEl) resultsEl.innerHTML = '<div class="empty-state">Abre una carpeta primero</div>';
    return;
  }
  resultsEl.innerHTML = '<div class="empty-state">Buscando...</div>';
  try {
    let matches;
    if (window.seal && window.seal.fs && window.seal.fs.searchInFiles) {
      const result = await window.seal.fs.searchInFiles(state.currentFolder, query);
      matches = result.results || [];
    } else {
      matches = [];
    }
    if (matches.length === 0) {
      resultsEl.innerHTML = '<div class="empty-state">Sin resultados</div>';
      return;
    }
    resultsEl.innerHTML = matches.map(m => `
      <div class="search-result" data-path="${escHtml(m.path)}" data-name="${escHtml(m.name)}">
        <div class="search-file">${escHtml(m.name)}:${m.line}</div>
        <div class="search-line">${escHtml(m.text)}</div>
      </div>
    `).join('');
    resultsEl.querySelectorAll('.search-result').forEach(el => {
      el.addEventListener('click', () => openFile(el.dataset.path, el.dataset.name));
    });
  } catch (e) {
    console.warn('[Search] Failed:', e.message);
    resultsEl.innerHTML = '<div class="empty-state">Error en búsqueda</div>';
  }
}
