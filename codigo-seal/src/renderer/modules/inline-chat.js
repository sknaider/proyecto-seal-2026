/**
 * Código SEAL — Inline Chat (Ctrl+K Edit-in-Place)
 * Select code → Ctrl+K → type instruction → AI generates edit → accept/reject.
 * Based on: Cursor Cmd+K pattern. Clean-room for SEAL.
 */

import { state } from './state.js';
import { escHtml, showToast } from './utils.js';

let inlineChatWidget = null;
let pendingEdit = null;

export function initInlineChat() {
  const checkEditor = setInterval(() => {
    if (window.monacoInstance && state.editor) {
      clearInterval(checkEditor);
      registerKeybinding();
    }
  }, 500);
}

function registerKeybinding() {
  const monaco = window.monacoInstance;

  // Ctrl+K triggers inline chat
  state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK, () => {
    const selection = state.editor.getSelection();
    if (!selection || selection.isEmpty()) {
      showToast('Selecciona código primero', 'warning', 2000);
      return;
    }
    showInlineChatInput(selection);
  });
}

function showInlineChatInput(selection) {
  const monaco = window.monacoInstance;
  const editor = state.editor;

  // Remove previous widget
  if (inlineChatWidget) {
    editor.removeContentWidget(inlineChatWidget);
    inlineChatWidget = null;
  }

  // Get selected text
  const selectedText = editor.getModel().getValueInRange(selection);

  // Create widget
  const domNode = document.createElement('div');
  domNode.className = 'inline-chat-widget';
  domNode.innerHTML = `
    <div class="inline-chat-header">🔭 SEAL — Editar con AI</div>
    <input type="text" class="inline-chat-input" placeholder="Describe el cambio..." autocomplete="off">
    <div class="inline-chat-actions">
      <button class="inline-chat-btn send">Enviar</button>
      <button class="inline-chat-btn cancel">Esc</button>
    </div>
    <div class="inline-chat-result hidden"></div>
  `;

  const input = domNode.querySelector('.inline-chat-input');
  const resultDiv = domNode.querySelector('.inline-chat-result');
  const sendBtn = domNode.querySelector('.inline-chat-btn.send');
  const cancelBtn = domNode.querySelector('.inline-chat-btn.cancel');

  // Send request
  const sendRequest = async () => {
    const instruction = input.value.trim();
    if (!instruction) return;

    input.disabled = true;
    sendBtn.textContent = '⟳ Generando...';
    sendBtn.disabled = true;

    try {
      if (!window.seal || !window.seal.ai) {
        showToast('AI Provider no disponible', 'error');
        return;
      }

      const result = await window.seal.ai.complete({
        messages: [
          { role: 'system', content: 'You are a code editor. Given the selected code and an instruction, output ONLY the modified code. No explanations, no markdown fences, just the code.' },
          { role: 'user', content: `Selected code:\n\`\`\`\n${selectedText}\n\`\`\`\n\nInstruction: ${instruction}\n\nOutput the modified code:` },
        ],
        maxTokens: 2048,
        temperature: 0.3,
      });

      if (result.error) {
        showToast(`Error: ${result.error}`, 'error');
        resetWidget();
        return;
      }

      // Clean response
      let newCode = result.text.trim();
      // Remove markdown fences if present
      if (newCode.startsWith('```')) {
        newCode = newCode.replace(/^```\w*\n?/, '').replace(/\n?```$/, '');
      }

      pendingEdit = { selection, oldCode: selectedText, newCode };

      // Show diff preview
      resultDiv.classList.remove('hidden');
      resultDiv.innerHTML = `
        <div class="inline-chat-diff">
          <div class="diff-old">${escHtml(selectedText).replace(/\n/g, '<br>')}</div>
          <div class="diff-arrow">→</div>
          <div class="diff-new">${escHtml(newCode).replace(/\n/g, '<br>')}</div>
        </div>
        <div class="inline-chat-confirm">
          <button class="inline-chat-btn accept">✓ Aceptar</button>
          <button class="inline-chat-btn reject">✗ Rechazar</button>
        </div>
      `;

      // Accept
      resultDiv.querySelector('.accept').addEventListener('click', () => {
        applyEdit();
        removeWidget();
      });

      // Reject
      resultDiv.querySelector('.reject').addEventListener('click', () => {
        pendingEdit = null;
        removeWidget();
        showToast('Cambio rechazado', 'info', 1500);
      });

    } catch (e) {
      showToast(`Error: ${e.message}`, 'error');
      resetWidget();
    }
  };

  function resetWidget() {
    input.disabled = false;
    sendBtn.textContent = 'Enviar';
    sendBtn.disabled = false;
  }

  function removeWidget() {
    if (inlineChatWidget) {
      editor.removeContentWidget(inlineChatWidget);
      inlineChatWidget = null;
    }
    pendingEdit = null;
  }

  // Event handlers
  sendBtn.addEventListener('click', sendRequest);
  cancelBtn.addEventListener('click', removeWidget);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); sendRequest(); }
    if (e.key === 'Escape') removeWidget();
  });

  // Create Monaco content widget
  inlineChatWidget = {
    getId: () => 'seal-inline-chat',
    getDomNode: () => domNode,
    getPosition: () => ({
      position: { lineNumber: selection.endLineNumber + 1, column: 1 },
      preference: [monaco.editor.ContentWidgetPositionPreference.BELOW],
    }),
  };

  editor.addContentWidget(inlineChatWidget);
  setTimeout(() => input.focus(), 50);
}

function applyEdit() {
  if (!pendingEdit || !state.editor) return;
  const { selection, newCode } = pendingEdit;

  state.editor.executeEdits('seal-inline-chat', [{
    range: selection,
    text: newCode,
    forceMoveMarkers: true,
  }]);

  showToast('Cambio aplicado', 'success', 2000);
  pendingEdit = null;
}
