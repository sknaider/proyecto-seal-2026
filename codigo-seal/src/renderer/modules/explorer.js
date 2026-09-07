/**
 * Código SEAL — File Explorer Module
 * File tree, open folder, tabs, breadcrumb, new file, save-as.
 */

import { state } from './state.js';
import { escHtml, findTabByPath, getFileIcon, showToast } from './utils.js';
import { openFileInEditor, saveCurrentFile } from './editor.js';

let untitledCounter = 0;

export function initFileTree() {
  document.getElementById('btn-open-folder')?.addEventListener('click', openFolder);
  document.getElementById('btn-new-tab')?.addEventListener('click', newFile);
}

export async function openFolder() {
  let folderPath = state.currentFolder || '/home/dadito/IA/proyecto-seal';
  if (window.seal && window.seal.dialog) {
    try {
      const result = await window.seal.dialog.openFolder();
      if (result.canceled) return;
      folderPath = result.filePaths[0];
    } catch {}
  }
  await loadDirectory(folderPath);
  state.currentFolder = folderPath;
  document.getElementById('explorer-empty')?.classList.add('hidden');
  const panelTitle = document.querySelector('#panel-explorer .panel-title');
  if (panelTitle) panelTitle.textContent = `EXPLORADOR — ${folderPath.split('/').pop().toUpperCase()}`;
  showToast(`Carpeta abierta: ${folderPath.split('/').pop()}`, 'info', 2000);
}

export async function loadDirectory(dirPath, parentEl, depth = 0) {
  if (!window.seal) return;
  const container = parentEl || document.getElementById('file-tree');
  if (!parentEl) container.innerHTML = '';
  try {
    const result = await window.seal.fs.readDir(dirPath);
    if (result.error) return;
    const entries = result.entries
      .filter(e => !e.name.startsWith('.') && !['node_modules','__pycache__','.git','dist','build'].includes(e.name))
      .sort((a, b) => (a.isDirectory === b.isDirectory) ? a.name.localeCompare(b.name) : a.isDirectory ? -1 : 1);
    for (const entry of entries) {
      const item = document.createElement('div');
      item.className = 'tree-item';
      item.style.paddingLeft = `${8 + depth * 16}px`;
      const icon = entry.isDirectory ? '📁' : getFileIcon(entry.name);
      item.innerHTML = `<span class="tree-icon">${icon}</span><span class="tree-name">${entry.name}</span>`;
      if (entry.isDirectory) {
        let expanded = false;
        let loading = false;
        item.addEventListener('click', async () => {
          if (loading) return;
          const next = item.nextElementSibling;
          if (expanded && next && next.className === 'tree-children') {
            next.remove(); expanded = false;
            item.querySelector('.tree-icon').textContent = '📁';
          } else {
            loading = true;
            const ch = document.createElement('div');
            ch.className = 'tree-children';
            item.after(ch);
            await loadDirectory(entry.path, ch, depth + 1);
            expanded = true;
            loading = false;
            item.querySelector('.tree-icon').textContent = '📂';
          }
        });
      } else {
        item.addEventListener('click', () => openFile(entry.path, entry.name));
      }
      container.appendChild(item);
    }
  } catch (e) {
    console.warn('[Explorer] readDir failed:', e.message);
  }
}

export async function openFile(filePath, fileName) {
  if (!window.seal) return;
  const existing = state.openFiles.find(f => f.path === filePath);
  if (existing) { setActiveFile(existing); return; }
  const result = await window.seal.fs.readFile(filePath);
  if (result.error) return;
  const file = { path: filePath, name: fileName, content: result.content };
  state.openFiles.push(file);
  addTab(file);
  setActiveFile(file);
}

export function addTab(file) {
  const tabs = document.getElementById('tabs');
  const tab = document.createElement('div');
  tab.className = 'tab';
  tab.dataset.path = file.path;
  tab.innerHTML = `<span class="tab-icon">${getFileIcon(file.name)}</span>${file.name}<span class="tab-close">×</span>`;
  tab.addEventListener('click', () => setActiveFile(file));
  tab.querySelector('.tab-close').addEventListener('click', e => { e.stopPropagation(); closeTab(file); });
  tabs.appendChild(tab);
}

export function setActiveFile(file) {
  state.activeFile = file;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const tab = findTabByPath(file.path);
  if (tab) tab.classList.add('active');
  openFileInEditor(file);
  updateBreadcrumb(file.path);
}

function updateBreadcrumb(filePath) {
  const el = document.getElementById('breadcrumb');
  if (!el) return;
  const parts = filePath.split('/').filter(Boolean);
  const shown = parts.slice(-4);
  el.innerHTML = (parts.length > 4 ? '<span class="breadcrumb-segment">...</span><span class="breadcrumb-sep">›</span>' : '') +
    shown.map((p, i) =>
      `<span class="breadcrumb-segment">${escHtml(p)}</span>${i < shown.length - 1 ? '<span class="breadcrumb-sep">›</span>' : ''}`
    ).join('');
}

export function closeTab(file) {
  state.openFiles = state.openFiles.filter(f => f.path !== file.path);
  const tab = findTabByPath(file.path);
  if (tab) tab.remove();
  if (state.editorModels[file.path]) {
    state.editorModels[file.path].dispose();
    delete state.editorModels[file.path];
  }
  if (state.activeFile === file) {
    if (state.openFiles.length > 0) setActiveFile(state.openFiles[state.openFiles.length - 1]);
    else {
      state.activeFile = null;
      document.getElementById('monaco-container').style.display = 'none';
      document.getElementById('welcome').classList.add('visible');
      document.getElementById('current-file').textContent = '';
      if (state.editor) { state.editor.dispose(); state.editor = null; }
    }
  }
}

export function cycleTab(direction) {
  if (state.openFiles.length < 2) return;
  const idx = state.openFiles.indexOf(state.activeFile);
  let next = (idx + direction + state.openFiles.length) % state.openFiles.length;
  setActiveFile(state.openFiles[next]);
}

export function newFile() {
  untitledCounter++;
  const name = `sin-titulo-${untitledCounter}`;
  const path = `/tmp/${name}`;
  const file = { path, name, content: '' };
  state.openFiles.push(file);
  addTab(file);
  setActiveFile(file);
}

export async function openFileDialog() {
  if (!window.seal || !window.seal.dialog) return;
  try {
    const result = await window.seal.dialog.openFile();
    if (result.canceled || !result.filePaths || !result.filePaths[0]) return;
    const filePath = result.filePaths[0];
    await openFile(filePath, filePath.split('/').pop());
  } catch (e) {
    console.warn('[Explorer] openFile dialog failed:', e.message);
  }
}

export async function saveFileAs() {
  if (!state.editor || !window.seal || !window.seal.dialog) return;
  try {
    const defaultPath = state.activeFile ? state.activeFile.path : '';
    const result = await window.seal.dialog.saveFile(defaultPath);
    if (result.canceled || !result.filePath) return;
    const content = state.editor.getValue();
    await window.seal.fs.writeFile(result.filePath, content);
    const name = result.filePath.split('/').pop();
    if (state.activeFile) {
      const oldPath = state.activeFile.path;
      state.activeFile.path = result.filePath;
      state.activeFile.name = name;
      if (state.editorModels[oldPath]) {
        state.editorModels[result.filePath] = state.editorModels[oldPath];
        delete state.editorModels[oldPath];
      }
      const tab = findTabByPath(oldPath);
      if (tab) { tab.dataset.path = result.filePath; tab.classList.remove('modified'); }
    }
    document.getElementById('current-file').textContent = name;
    showToast(`Guardado como: ${name}`, 'success', 2000);
  } catch (e) {
    console.warn('[Explorer] saveAs failed:', e.message);
  }
}
