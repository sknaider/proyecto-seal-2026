/**
 * Código SEAL — AI Diff View
 * Monaco DiffEditor with per-hunk accept/reject.
 * Shows original vs AI-proposed changes side-by-side.
 * Based on: Cursor diff pattern. Clean-room for SEAL.
 */

import { state } from './state.js';
import { escHtml, showToast } from './utils.js';

let diffEditor = null;
let diffContainer = null;
let pendingDiff = null;

export function initDiffView() {
  // Create diff container (hidden by default)
  diffContainer = document.createElement('div');
  diffContainer.id = 'diff-view-container';
  diffContainer.className = 'diff-view-container hidden';
  diffContainer.innerHTML = `
    <div class="diff-view-header">
      <span class="diff-view-title">🔭 SEAL — Revisión de cambios</span>
      <div class="diff-view-info" id="diff-view-info"></div>
      <div class="diff-view-actions">
        <button class="diff-btn accept-all" id="diff-accept-all">✓ Aceptar todo</button>
        <button class="diff-btn reject-all" id="diff-reject-all">✗ Rechazar todo</button>
        <button class="diff-btn close" id="diff-close">Cerrar</button>
      </div>
    </div>
    <div id="diff-editor-mount" class="diff-editor-mount"></div>
  `;

  // Insert before editor-wrapper
  const center = document.getElementById('center');
  const editorWrapper = document.getElementById('editor-wrapper');
  if (center && editorWrapper) {
    center.insertBefore(diffContainer, editorWrapper);
  }

  // Button handlers
  document.getElementById('diff-accept-all')?.addEventListener('click', acceptAll);
  document.getElementById('diff-reject-all')?.addEventListener('click', rejectAll);
  document.getElementById('diff-close')?.addEventListener('click', closeDiffView);
}

/**
 * Show a diff between original and proposed code.
 * @param {Object} options
 * @param {string} options.originalCode - The original file content
 * @param {string} options.proposedCode - AI-proposed modified content
 * @param {string} options.fileName - File name for display
 * @param {string} options.language - Monaco language ID
 * @param {string} options.filePath - Full file path (for applying changes)
 * @param {string} [options.description] - What the AI changed
 */
export function showDiff({ originalCode, proposedCode, fileName, language, filePath, description }) {
  if (!diffContainer) return;

  const monaco = window.monacoInstance;
  if (!monaco) {
    showToast('Monaco no disponible para diff', 'error');
    return;
  }

  pendingDiff = { originalCode, proposedCode, fileName, filePath, language };

  // Show container, hide editor
  diffContainer.classList.remove('hidden');
  document.getElementById('editor-wrapper').style.display = 'none';

  // Info
  const info = document.getElementById('diff-view-info');
  if (info) {
    const added = proposedCode.split('\n').length - originalCode.split('\n').length;
    const sign = added >= 0 ? '+' : '';
    info.textContent = `${fileName} — ${description || 'Cambios propuestos'} (${sign}${added} líneas)`;
  }

  // Create or reuse diff editor
  const mount = document.getElementById('diff-editor-mount');
  if (diffEditor) {
    diffEditor.dispose();
    diffEditor = null;
  }
  mount.innerHTML = '';

  const originalModel = monaco.editor.createModel(originalCode, language);
  const modifiedModel = monaco.editor.createModel(proposedCode, language);

  diffEditor = monaco.editor.createDiffEditor(mount, {
    theme: 'seal-dark',
    readOnly: true,
    renderSideBySide: true,
    fontSize: 13,
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    automaticLayout: true,
    enableSplitViewResizing: true,
    renderOverviewRuler: true,
    diffWordWrap: 'on',
    originalEditable: false,
  });

  diffEditor.setModel({
    original: originalModel,
    modified: modifiedModel,
  });
}

function acceptAll() {
  if (!pendingDiff) return;

  const { proposedCode, filePath } = pendingDiff;

  // Apply changes to the file
  if (state.editor && state.activeFile && state.activeFile.path === filePath) {
    // Replace entire content in Monaco
    const model = state.editor.getModel();
    if (model) {
      const fullRange = model.getFullModelRange();
      state.editor.executeEdits('seal-diff-accept', [{
        range: fullRange,
        text: proposedCode,
        forceMoveMarkers: true,
      }]);
    }
  }

  // Save to filesystem
  if (window.seal && window.seal.fs) {
    window.seal.fs.writeFile(filePath, proposedCode);
  }

  showToast('Cambios aceptados', 'success', 2000);
  closeDiffView();
}

function rejectAll() {
  showToast('Cambios rechazados', 'info', 1500);
  closeDiffView();
}

export function closeDiffView() {
  if (diffContainer) diffContainer.classList.add('hidden');
  document.getElementById('editor-wrapper').style.display = '';

  if (diffEditor) {
    diffEditor.dispose();
    diffEditor = null;
  }

  pendingDiff = null;

  // Re-layout main editor
  if (state.editor) state.editor.layout();
}

/**
 * Quick diff from Inline Chat result.
 * @param {string} originalCode - Selected code
 * @param {string} proposedCode - AI-generated replacement
 * @param {string} instruction - What the user asked
 */
export function showInlineDiff(originalCode, proposedCode, instruction) {
  if (!state.activeFile) return;

  showDiff({
    originalCode,
    proposedCode,
    fileName: state.activeFile.name,
    language: state.activeFile.name.split('.').pop() || 'plaintext',
    filePath: state.activeFile.path,
    description: instruction,
  });
}
