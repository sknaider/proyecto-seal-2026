/**
 * Código SEAL — Shared Utilities
 * Helper functions used across all modules.
 */

import { BRIDGE_URL } from './state.js';

export function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#x27;');
}

export function findTabByPath(path) {
  return Array.from(document.querySelectorAll('.tab')).find(t => t.dataset.path === path);
}

export function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

export function showToast(message, type = 'info', duration = 3000) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const icons = { success: '✓', error: '✗', info: 'ℹ', warning: '⚠' };
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span class="toast-icon">${icons[type] || ''}</span><span>${escHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('fade-out');
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

export async function sealRequest(endpoint, method, body) {
  if (window.seal && window.seal.request) {
    return await window.seal.request(endpoint, method || 'GET', body);
  }
  const opts = { method: method || 'GET', headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BRIDGE_URL}${endpoint}`, opts);
  return await res.json();
}

export function getFileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  return { py:'🐍', js:'📜', ts:'📘', json:'📋', md:'📝', html:'🌐', css:'🎨',
    sh:'⚙️', yaml:'📄', yml:'📄', sql:'🗃️', txt:'📄', log:'📊',
    jsx:'⚛️', tsx:'⚛️', rs:'🦀', go:'🔵', java:'☕', toml:'📄' }[ext] || '📄';
}

export function getLanguage(filename) {
  const ext = filename.split('.').pop().toLowerCase();
  const map = {
    py: 'python', js: 'javascript', ts: 'typescript', jsx: 'javascript',
    tsx: 'typescript', json: 'json', md: 'markdown', html: 'html',
    css: 'css', sh: 'shell', bash: 'shell', sql: 'sql', yaml: 'yaml',
    yml: 'yaml', xml: 'xml', rs: 'rust', go: 'go', java: 'java',
    cpp: 'cpp', c: 'c', h: 'c', hpp: 'cpp', rb: 'ruby', php: 'php',
    txt: 'plaintext', log: 'plaintext', csv: 'plaintext',
  };
  return map[ext] || 'plaintext';
}
