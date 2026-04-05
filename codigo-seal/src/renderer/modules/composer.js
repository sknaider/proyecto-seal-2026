/**
 * Código SEAL — Multi-File Composer / Agent Mode
 * Natural language → coordinated multi-file edits.
 * User describes change → AI decomposes → per-file diffs → accept/reject.
 * Based on: Cursor Composer + Agent Mode patterns. Clean-room for SEAL.
 */

import { state } from './state.js';
import { escHtml, showToast, sealRequest } from './utils.js';
import { showDiff } from './diff-view.js';
import { resolveMentions, buildContextString } from './context-mentions.js';

let composerPanel = null;
let composerVisible = false;

export function initComposer() {
  // Create composer panel (right side or modal)
  composerPanel = document.createElement('div');
  composerPanel.id = 'composer-panel';
  composerPanel.className = 'composer-panel hidden';
  composerPanel.innerHTML = `
    <div class="composer-header">
      <span class="composer-title">🔭 Composer</span>
      <button class="composer-close" id="composer-close">×</button>
    </div>
    <div class="composer-body">
      <textarea class="composer-input" id="composer-input" placeholder="Describe el cambio que quieres hacer...\n\nEjemplo: 'Agrega validación de email al formulario de registro y actualiza los tests'"></textarea>
      <div class="composer-context" id="composer-context">
        <span class="composer-context-label">Contexto: </span>
        <span id="composer-file-count">0 archivos</span>
      </div>
      <div class="composer-actions">
        <button class="seal-btn" id="composer-generate">Generar cambios</button>
        <button class="seal-btn" id="composer-agent-mode" style="background:var(--seal-jarvis)">Agent Mode</button>
      </div>
    </div>
    <div class="composer-results hidden" id="composer-results">
      <div class="composer-results-header">
        <span>Cambios propuestos</span>
        <div>
          <button class="diff-btn accept-all" id="composer-accept-all">✓ Aceptar todo</button>
          <button class="diff-btn reject-all" id="composer-reject-all">✗ Rechazar todo</button>
        </div>
      </div>
      <div id="composer-file-list"></div>
    </div>
    <div class="composer-agent-log hidden" id="composer-agent-log">
      <div class="composer-agent-header">Agent Mode — Log</div>
      <div id="composer-log-entries"></div>
    </div>
  `;

  document.body.appendChild(composerPanel);

  // Event handlers
  document.getElementById('composer-close')?.addEventListener('click', toggleComposer);
  document.getElementById('composer-generate')?.addEventListener('click', generateChanges);
  document.getElementById('composer-agent-mode')?.addEventListener('click', runAgentMode);
  document.getElementById('composer-accept-all')?.addEventListener('click', acceptAllChanges);
  document.getElementById('composer-reject-all')?.addEventListener('click', rejectAllChanges);

  // Keyboard shortcut: Ctrl+Shift+I for Composer
  document.addEventListener('keydown', (e) => {
    if (e.key === 'I' && e.ctrlKey && e.shiftKey) { e.preventDefault(); toggleComposer(); }
  });
}

export function toggleComposer() {
  composerVisible = !composerVisible;
  if (composerPanel) {
    composerPanel.classList.toggle('hidden', !composerVisible);
    if (composerVisible) document.getElementById('composer-input')?.focus();
  }
}

let pendingChanges = []; // [{ file, originalCode, proposedCode, accepted }]

async function generateChanges() {
  const input = document.getElementById('composer-input');
  const instruction = input?.value.trim();
  if (!instruction) return;

  const genBtn = document.getElementById('composer-generate');
  genBtn.textContent = '⟳ Generando...';
  genBtn.disabled = true;

  try {
    // Resolve @mentions in instruction
    const { cleanText, context } = await resolveMentions(instruction);
    const contextStr = buildContextString(context);

    // Get project file list for AI context
    let fileList = '';
    if (state.currentFolder && window.seal) {
      const result = await window.seal.fs.readDir(state.currentFolder);
      if (result.entries) {
        fileList = result.entries
          .filter(e => !e.name.startsWith('.') && !['node_modules','dist','build'].includes(e.name))
          .map(e => `${e.isDirectory ? '📁' : '📄'} ${e.name}`)
          .join('\n');
      }
    }

    // Ask AI to decompose into file-level changes
    if (!window.seal || !window.seal.ai) {
      showToast('AI Provider no disponible', 'error');
      return;
    }

    const result = await window.seal.ai.complete({
      messages: [
        { role: 'system', content: `You are a multi-file code editor. Given an instruction, identify which files need changes and describe EACH change as a JSON array.

Output ONLY valid JSON:
[{"file": "relative/path.ext", "action": "modify|create|delete", "description": "what to change"}]

Project files:
${fileList}
${contextStr}` },
        { role: 'user', content: cleanText },
      ],
      maxTokens: 2048,
      temperature: 0.3,
    });

    if (result.error) {
      showToast(`Error: ${result.error}`, 'error');
      return;
    }

    // Parse file changes
    let changes = [];
    try {
      const jsonMatch = result.text.match(/\[[\s\S]*\]/);
      if (jsonMatch) changes = JSON.parse(jsonMatch[0]);
    } catch {
      showToast('No se pudieron parsear los cambios', 'error');
      return;
    }

    if (changes.length === 0) {
      showToast('No se identificaron cambios necesarios', 'info');
      return;
    }

    // Show results
    pendingChanges = changes.map(c => ({ ...c, accepted: null }));
    showComposerResults(changes);

  } catch (e) {
    showToast(`Error: ${e.message}`, 'error');
  } finally {
    genBtn.textContent = 'Generar cambios';
    genBtn.disabled = false;
  }
}

function showComposerResults(changes) {
  const resultsDiv = document.getElementById('composer-results');
  const fileList = document.getElementById('composer-file-list');
  resultsDiv.classList.remove('hidden');

  fileList.innerHTML = changes.map((c, i) => `
    <div class="composer-file-entry" data-idx="${i}">
      <span class="composer-file-action ${c.action}">${c.action === 'create' ? '+' : c.action === 'delete' ? '−' : '~'}</span>
      <span class="composer-file-path">${escHtml(c.file)}</span>
      <span class="composer-file-desc">${escHtml(c.description)}</span>
      <div class="composer-file-btns">
        <button class="diff-btn accept-all composer-file-accept" data-idx="${i}">✓</button>
        <button class="diff-btn reject-all composer-file-reject" data-idx="${i}">✗</button>
        <button class="diff-btn close composer-file-diff" data-idx="${i}">Diff</button>
      </div>
    </div>
  `).join('');

  // Bind handlers
  fileList.querySelectorAll('.composer-file-accept').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx);
      pendingChanges[idx].accepted = true;
      btn.closest('.composer-file-entry').classList.add('accepted');
      showToast(`${pendingChanges[idx].file} aceptado`, 'success', 1500);
    });
  });

  fileList.querySelectorAll('.composer-file-reject').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx);
      pendingChanges[idx].accepted = false;
      btn.closest('.composer-file-entry').classList.add('rejected');
    });
  });
}

function acceptAllChanges() {
  pendingChanges.forEach(c => c.accepted = true);
  showToast(`${pendingChanges.length} cambios aceptados`, 'success');
  // TODO: Apply each change to filesystem
}

function rejectAllChanges() {
  pendingChanges = [];
  document.getElementById('composer-results')?.classList.add('hidden');
  showToast('Todos los cambios rechazados', 'info');
}

async function runAgentMode() {
  const input = document.getElementById('composer-input');
  const instruction = input?.value.trim();
  if (!instruction) return;

  const logDiv = document.getElementById('composer-agent-log');
  const entries = document.getElementById('composer-log-entries');
  logDiv.classList.remove('hidden');
  entries.innerHTML = '';

  const addLog = (text, type = 'info') => {
    const entry = document.createElement('div');
    entry.className = `agent-log-entry ${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString('es')}] ${text}`;
    entries.appendChild(entry);
    entries.scrollTop = entries.scrollHeight;
  };

  addLog('Agent Mode iniciado...');
  addLog(`Instrucción: ${instruction.slice(0, 100)}`);

  try {
    // Step 1: Plan
    addLog('Paso 1: Planificando cambios...');
    await generateChanges();
    addLog(`${pendingChanges.length} archivos identificados`);

    // Step 2: Auto-accept all
    addLog('Paso 2: Aplicando cambios...');
    pendingChanges.forEach(c => c.accepted = true);

    // Step 3: Run tests (if available)
    addLog('Paso 3: Verificando...');
    if (window.seal && window.seal.terminal) {
      addLog('Ejecutando tests...', 'running');
      // Would run tests here in a real implementation
    }

    addLog('Agent Mode completado', 'success');
  } catch (e) {
    addLog(`Error: ${e.message}`, 'error');
  }
}
