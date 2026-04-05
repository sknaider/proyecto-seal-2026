/**
 * Código SEAL — Git Panel Module
 * Branch, status, log, file click to open.
 */

import { state } from './state.js';
import { escHtml, sealRequest } from './utils.js';
import { openFile } from './explorer.js';

export function initGit() {
  fetchGitStatus();
  state.intervals.push(setInterval(fetchGitStatus, 15000));
}

export async function fetchGitStatus() {
  if (!window.seal || !window.seal.git || !state.currentFolder) return;
  try {
    const status = await window.seal.git.status(state.currentFolder);
    if (status.error) return;

    const branchEl = document.getElementById('git-branch');
    if (branchEl) branchEl.textContent = `⎇ ${status.branch}`;

    const infoEl = document.getElementById('git-status-info');
    if (infoEl) infoEl.textContent = status.clean ? '✓ Árbol de trabajo limpio' : `${status.files.length} archivo(s) modificado(s)`;

    const filesEl = document.getElementById('git-files');
    if (filesEl) {
      filesEl.innerHTML = status.files.map(f => `
        <div class="git-file" data-path="${escHtml(f.path)}" title="${escHtml(f.path)}">
          <span class="git-file-status ${f.status}">${f.status}</span>
          <span class="git-file-name">${escHtml(f.path.split('/').pop())}</span>
        </div>
      `).join('');
      filesEl.querySelectorAll('.git-file').forEach(el => {
        el.addEventListener('click', () => {
          const path = el.dataset.path;
          openFile(`${state.currentFolder}/${path}`, path.split('/').pop());
        });
      });
    }

    const log = await window.seal.git.log(state.currentFolder, 8);
    const logEl = document.getElementById('git-log');
    if (logEl && log && log.entries) {
      logEl.innerHTML = log.entries.map(e =>
        `<div class="git-log-entry"><span class="git-log-hash">${escHtml(e.hash)}</span><span class="git-log-msg">${escHtml(e.message)}</span></div>`
      ).join('');
    }
  } catch (e) {
    console.warn('[Git] Status failed:', e.message);
  }
}
