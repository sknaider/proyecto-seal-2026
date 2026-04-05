/**
 * Código SEAL — Terminal Error Detection + Auto-Fix
 * Parses terminal output for errors, links to files, suggests AI fixes.
 * Based on: Cursor terminal error pattern. Clean-room for SEAL.
 */

import { state } from './state.js';
import { showToast, escHtml } from './utils.js';
import { openFile } from './explorer.js';

// Error patterns for common languages/tools
const ERROR_PATTERNS = [
  // Python traceback
  { name: 'Python Traceback', regex: /File "([^"]+)", line (\d+)(?:, in (\w+))?/g, lang: 'python' },
  // Node.js / JavaScript
  { name: 'Node.js Error', regex: /at .+\(([^:]+):(\d+):(\d+)\)/g, lang: 'javascript' },
  { name: 'JS SyntaxError', regex: /([^\s:]+):(\d+)\n.*SyntaxError/g, lang: 'javascript' },
  // TypeScript
  { name: 'TypeScript Error', regex: /([^\s(]+)\((\d+),(\d+)\): error TS/g, lang: 'typescript' },
  // Rust
  { name: 'Rust Error', regex: /error\[E\d+\]: .+\n\s*--> ([^:]+):(\d+):(\d+)/g, lang: 'rust' },
  // Go
  { name: 'Go Error', regex: /([^\s:]+\.go):(\d+):(\d+):/g, lang: 'go' },
  // C/C++ GCC
  { name: 'GCC Error', regex: /([^\s:]+\.[ch](?:pp)?):(\d+):(\d+): (?:error|warning)/g, lang: 'cpp' },
  // Java
  { name: 'Java Exception', regex: /at [\w.]+\(([^:]+):(\d+)\)/g, lang: 'java' },
  // Generic "file:line" pattern
  { name: 'Generic', regex: /([^\s:]+\.\w{1,4}):(\d+)(?::(\d+))?\s*[-—:]/g, lang: null },
  // pytest
  { name: 'pytest', regex: /FAILED\s+([^\s:]+)::(\w+)/g, lang: 'python' },
  // npm/node errors
  { name: 'npm Error', regex: /ERR!\s+(.+)/g, lang: null },
  // Permission denied
  { name: 'Permission Denied', regex: /Permission denied[:\s]*(.+)/gi, lang: null },
];

/**
 * Scan terminal output for errors.
 * @param {string} output — Terminal text to scan
 * @returns {Object[]} — Array of detected errors
 */
export function detectErrors(output) {
  const errors = [];
  const seen = new Set();

  for (const pattern of ERROR_PATTERNS) {
    const regex = new RegExp(pattern.regex.source, pattern.regex.flags);
    let match;
    while ((match = regex.exec(output)) !== null) {
      const file = match[1];
      const line = match[2] ? parseInt(match[2]) : null;
      const col = match[3] ? parseInt(match[3]) : null;
      const key = `${file}:${line}`;

      if (seen.has(key)) continue;
      seen.add(key);

      errors.push({
        pattern: pattern.name,
        file,
        line,
        col,
        lang: pattern.lang,
        context: output.slice(Math.max(0, match.index - 50), match.index + match[0].length + 100).trim(),
        fullMatch: match[0],
      });
    }
  }

  return errors;
}

/**
 * Create clickable error annotations in terminal output.
 * @param {string} output — Raw terminal output
 * @returns {string} — HTML with clickable file links
 */
export function annotateErrors(output) {
  const errors = detectErrors(output);
  if (errors.length === 0) return null;

  return errors.map(e => `
    <div class="terminal-error" data-file="${escHtml(e.file)}" data-line="${e.line || 0}">
      <span class="error-icon">⚠</span>
      <span class="error-location">${escHtml(e.file)}${e.line ? `:${e.line}` : ''}${e.col ? `:${e.col}` : ''}</span>
      <span class="error-pattern">(${e.pattern})</span>
      <button class="error-fix-btn" data-context="${escHtml(e.context)}">Fix with AI</button>
    </div>
  `).join('');
}

/**
 * Initialize error detection on terminal output.
 * Hooks into terminal data stream to detect errors in real-time.
 */
export function initErrorDetection() {
  let outputBuffer = '';
  let errorPanel = null;

  // Create error panel
  errorPanel = document.createElement('div');
  errorPanel.id = 'terminal-errors';
  errorPanel.className = 'terminal-errors hidden';
  const termPanel = document.getElementById('terminal-bottom-panel');
  if (termPanel) termPanel.appendChild(errorPanel);

  // Hook into terminal data
  if (window.seal && window.seal.terminal) {
    const originalOnData = window.seal.terminal.onData;
    window.seal.terminal.onData = (callback) => {
      originalOnData(({ id, data }) => {
        callback({ id, data });

        // Buffer output for error detection
        outputBuffer += data;
        if (outputBuffer.length > 5000) outputBuffer = outputBuffer.slice(-5000);

        // Check for errors after newline (debounced)
        if (data.includes('\n')) {
          const errors = detectErrors(outputBuffer);
          if (errors.length > 0) {
            showErrorPanel(errorPanel, errors);
          }
        }
      });
    };
  }
}

function showErrorPanel(panel, errors) {
  if (!panel) return;

  panel.classList.remove('hidden');
  panel.innerHTML = `
    <div class="error-panel-header">
      <span>⚠ ${errors.length} error(es) detectado(s)</span>
      <button class="error-dismiss" onclick="this.parentElement.parentElement.classList.add('hidden')">×</button>
    </div>
    ${errors.slice(0, 5).map(e => `
      <div class="error-entry" data-file="${escHtml(e.file)}" data-line="${e.line || 0}">
        <span class="error-loc">${escHtml(e.file)}:${e.line || '?'}</span>
        <button class="error-open-btn">Abrir</button>
        <button class="error-fix-btn" data-context="${escHtml(e.context)}">Fix AI</button>
      </div>
    `).join('')}
  `;

  // Bind click handlers
  panel.querySelectorAll('.error-open-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const entry = btn.closest('.error-entry');
      const file = entry.dataset.file;
      const name = file.split('/').pop();
      openFile(file, name);
    });
  });

  panel.querySelectorAll('.error-fix-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const context = btn.dataset.context;
      if (!window.seal || !window.seal.ai) {
        showToast('AI Provider no disponible', 'error');
        return;
      }
      btn.textContent = '⟳...';
      btn.disabled = true;
      try {
        const result = await window.seal.ai.complete({
          messages: [
            { role: 'system', content: 'You are a debugging assistant. Given an error context, suggest a concise fix. Be specific — mention the file, line, and exact change needed.' },
            { role: 'user', content: `Error detected:\n${context}\n\nSuggest a fix:` },
          ],
          maxTokens: 512,
          temperature: 0.3,
        });
        if (result.text) {
          showToast(result.text.slice(0, 200), 'info', 8000);
        }
      } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
      }
      btn.textContent = 'Fix AI';
      btn.disabled = false;
    });
  });
}
