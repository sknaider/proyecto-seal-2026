/**
 * Código SEAL — Context Menu Module
 * Right-click menus for tabs and file tree.
 */

import { state } from './state.js';
import { escHtml, showToast } from './utils.js';
import { closeTab } from './explorer.js';

export function showContextMenu(x, y, items) {
  const menu = document.getElementById('context-menu');
  const container = document.getElementById('context-menu-items');
  if (!menu || !container) return;
  container.innerHTML = items.map(item =>
    item.separator ? '<div class="ctx-sep"></div>' :
    `<div class="ctx-item">${escHtml(item.label)}</div>`
  ).join('');
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  menu.classList.remove('hidden');
  const actionItems = items.filter(it => !it.separator);
  container.querySelectorAll('.ctx-item').forEach((el, i) => {
    if (actionItems[i] && actionItems[i].action) {
      el.addEventListener('click', () => { actionItems[i].action(); hideContextMenu(); });
    }
  });
  setTimeout(() => {
    const closer = (e) => {
      if (!menu.contains(e.target)) { hideContextMenu(); document.removeEventListener('click', closer); }
    };
    document.addEventListener('click', closer);
  }, 10);
}

export function hideContextMenu() {
  const menu = document.getElementById('context-menu');
  if (menu) menu.classList.add('hidden');
}

export function initContextMenus() {
  document.getElementById('tabs')?.addEventListener('contextmenu', (e) => {
    const tab = e.target.closest('.tab');
    if (!tab) return;
    e.preventDefault();
    const path = tab.dataset.path;
    const file = state.openFiles.find(f => f.path === path);
    if (!file) return;
    showContextMenu(e.clientX, e.clientY, [
      { label: 'Cerrar', action: () => closeTab(file) },
      { label: 'Cerrar otros', action: () => state.openFiles.filter(f => f !== file).forEach(f => closeTab(f)) },
      { label: 'Cerrar todos', action: () => [...state.openFiles].forEach(f => closeTab(f)) },
      { separator: true },
      { label: 'Copiar ruta', action: () => { navigator.clipboard?.writeText(path); showToast('Ruta copiada', 'info', 1500); } },
    ]);
  });
  document.getElementById('file-tree')?.addEventListener('contextmenu', (e) => {
    const item = e.target.closest('.tree-item');
    if (!item) return;
    e.preventDefault();
    const nameEl = item.querySelector('.tree-name');
    const name = nameEl ? nameEl.textContent : '';
    showContextMenu(e.clientX, e.clientY, [
      { label: 'Abrir', action: () => item.click() },
      { label: 'Copiar nombre', action: () => { navigator.clipboard?.writeText(name); showToast('Nombre copiado', 'info', 1500); } },
    ]);
  });
}
