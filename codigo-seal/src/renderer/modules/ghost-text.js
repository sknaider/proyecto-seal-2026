/**
 * Código SEAL — Ghost Text (Inline AI Completion)
 * Monaco InlineCompletionProvider powered by Ollama FIM.
 * THE feature that defines an AI IDE.
 *
 * Flow: User types → debounce 300ms → extract prefix+suffix →
 *       IPC to main → Ollama FIM → ghost text rendered in editor.
 *
 * Based on: Cursor Tab completion pattern + OpenClaude FIM analysis.
 * Clean-room implementation for SEAL.
 */

import { state } from './state.js';

let completionController = null;
let debounceTimer = null;
const DEBOUNCE_MS = 300;
const MAX_PREFIX_CHARS = 2000;
const MAX_SUFFIX_CHARS = 500;

export function initGhostText() {
  // Wait for Monaco to be ready, then register provider
  const checkMonaco = setInterval(() => {
    if (window.monacoInstance && state.editor) {
      clearInterval(checkMonaco);
      registerProvider();
    }
  }, 500);
}

function registerProvider() {
  const monaco = window.monacoInstance;

  monaco.languages.registerInlineCompletionsProvider('*', {
    provideInlineCompletions: async (model, position, context, token) => {
      // Cancel previous request
      if (completionController) completionController.abort();
      completionController = new AbortController();

      // Debounce
      await new Promise((resolve, reject) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(resolve, DEBOUNCE_MS);
        token.onCancellationRequested(() => reject(new Error('cancelled')));
      }).catch(() => null);

      if (token.isCancellationRequested) return { items: [] };

      // Extract prefix (text before cursor) and suffix (text after cursor)
      const textModel = model;
      const offset = textModel.getOffsetAt(position);
      const fullText = textModel.getValue();

      const prefix = fullText.slice(Math.max(0, offset - MAX_PREFIX_CHARS), offset);
      const suffix = fullText.slice(offset, offset + MAX_SUFFIX_CHARS);

      // Skip if prefix is too short or just whitespace
      if (prefix.trim().length < 3) return { items: [] };

      try {
        // Call Ollama FIM via IPC (provider system handles routing)
        if (!window.seal || !window.seal.ai) return { items: [] };

        const result = await window.seal.ai.complete({
          model: '', // Provider system picks best FIM model
          mode: 'fim',
          prefix: prefix,
          suffix: suffix,
          maxTokens: 128,
          temperature: 0.2,
          stop: ['\n\n', '\r\n\r\n'], // Stop at blank lines
        });

        if (token.isCancellationRequested) return { items: [] };
        if (!result || result.error || !result.text) return { items: [] };

        // Clean up the completion text
        let completionText = result.text;
        // Remove leading/trailing whitespace only if it doesn't match context
        if (completionText.startsWith('\n') && !prefix.endsWith('\n')) {
          completionText = completionText.trimStart();
        }

        if (!completionText || completionText.length < 2) return { items: [] };

        return {
          items: [{
            insertText: completionText,
            range: {
              startLineNumber: position.lineNumber,
              startColumn: position.column,
              endLineNumber: position.lineNumber,
              endColumn: position.column,
            },
          }],
        };
      } catch (e) {
        if (e.name !== 'AbortError') console.warn('[GhostText] FIM failed:', e.message);
        return { items: [] };
      }
    },

    freeInlineCompletions: () => {},
  });

  // Status bar indicator
  const statusEl = document.getElementById('status-mode');
  if (statusEl) statusEl.textContent = 'Ghost Text ✓';

  console.log('[GhostText] Registered inline completion provider');
}

/**
 * Toggle ghost text on/off.
 */
export function toggleGhostText(enabled) {
  if (state.editor) {
    state.editor.updateOptions({
      inlineSuggest: { enabled: enabled !== false },
    });
  }
}
