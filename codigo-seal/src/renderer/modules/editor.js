/**
 * Código SEAL — Monaco Editor Module
 * Handles: Monaco init, theme, file opening, save, language detection.
 */

import { state } from './state.js';
import { escHtml, findTabByPath, getLanguage, showToast } from './utils.js';

export async function saveCurrentFile() {
  if (!state.editor || !state.activeFile || !window.seal) return;
  const content = state.editor.getValue();
  const result = await window.seal.fs.writeFile(state.activeFile.path, content);
  if (result && result.error) { showToast(`Error: ${result.error}`, 'error'); return; }
  const tab = findTabByPath(state.activeFile.path);
  if (tab) tab.classList.remove('modified');
  document.getElementById('current-file').textContent = state.activeFile.name;
  showToast(`Guardado: ${state.activeFile.name}`, 'success', 2000);
}

export async function initMonaco() {
  try {
    const monaco = await window.monacoReady;
    monaco.editor.defineTheme('seal-dark', {
      base: 'vs-dark',
      inherit: true,
      rules: [
        { token: 'comment', foreground: '64748b', fontStyle: 'italic' },
        { token: 'keyword', foreground: 'c084fc' },
        { token: 'string', foreground: '86efac' },
        { token: 'number', foreground: 'fbbf24' },
        { token: 'type', foreground: '7dd3fc' },
      ],
      colors: {
        'editor.background': '#0a0a0f',
        'editor.foreground': '#e2e8f0',
        'editor.lineHighlightBackground': '#12121a',
        'editor.selectionBackground': '#3b82f640',
        'editorCursor.foreground': '#3b82f6',
        'editorLineNumber.foreground': '#334155',
        'editorLineNumber.activeForeground': '#64748b',
        'editor.inactiveSelectionBackground': '#1e3a5f40',
        'editorIndentGuide.background': '#1e1e2e',
        'editorIndentGuide.activeBackground': '#334155',
      }
    });
    monaco.editor.setTheme('seal-dark');
    window.monacoInstance = monaco;
  } catch (e) {
    console.error('[Editor] Monaco init failed:', e);
  }
}

export function openFileInEditor(file) {
  const container = document.getElementById('monaco-container');
  const welcome = document.getElementById('welcome');
  welcome.classList.remove('visible');
  container.style.display = 'block';

  const monaco = window.monacoInstance;
  if (!monaco) {
    container.innerHTML = `<pre style="padding:12px;overflow:auto;height:100%;margin:0;font-size:12px;color:var(--seal-text);background:var(--seal-bg)">${escHtml(file.content)}</pre>`;
    return;
  }

  if (!state.editorModels[file.path]) {
    const lang = getLanguage(file.name);
    state.editorModels[file.path] = monaco.editor.createModel(file.content, lang);
  }

  if (!state.editor) {
    state.editor = monaco.editor.create(container, {
      model: state.editorModels[file.path],
      theme: 'seal-dark',
      fontSize: 13,
      fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      minimap: { enabled: true, maxColumn: 80 },
      scrollBeyondLastLine: false,
      renderLineHighlight: 'all',
      cursorBlinking: 'smooth',
      cursorSmoothCaretAnimation: 'on',
      smoothScrolling: true,
      tabSize: 2,
      wordWrap: 'on',
      bracketPairColorization: { enabled: true },
      guides: { bracketPairs: true, indentation: true },
      padding: { top: 8 },
      automaticLayout: true,
    });

    state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveCurrentFile());

    state.editor.onDidChangeModelContent(() => {
      if (state.activeFile) {
        const tab = findTabByPath(state.activeFile.path);
        if (tab) tab.classList.add('modified');
      }
    });

    state.editor.onDidChangeCursorPosition((e) => {
      const pos = document.getElementById('status-pos');
      if (pos) pos.textContent = `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
    });
  } else {
    state.editor.setModel(state.editorModels[file.path]);
  }

  document.getElementById('current-file').textContent = file.name;
  const langEl = document.getElementById('status-lang');
  if (langEl) langEl.textContent = getLanguage(file.name);
}
