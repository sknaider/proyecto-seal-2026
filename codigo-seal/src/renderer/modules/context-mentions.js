/**
 * Código SEAL — @file/@codebase Context Mentions
 * Parse @mentions in chat to include file content as AI context.
 * @file:path → includes file content
 * @folder:path → includes directory tree
 * @codebase → RAG query via SEAL bridge
 * @agent:NAME → pulls agent memory/traces
 *
 * Based on: Cursor @-mention pattern. Clean-room for SEAL.
 */

import { state } from './state.js';
import { escHtml, sealRequest } from './utils.js';

const MENTION_REGEX = /@(file|folder|codebase|agent|soul):?([^\s]*)/g;

/**
 * Parse a message for @mentions and resolve their content.
 * @param {string} text — Raw user message
 * @returns {Promise<{ cleanText: string, context: Object[] }>}
 */
export async function resolveMentions(text) {
  const mentions = [];
  let match;
  const regex = new RegExp(MENTION_REGEX.source, 'g');

  while ((match = regex.exec(text)) !== null) {
    mentions.push({ type: match[1], value: match[2] || '', index: match.index, full: match[0] });
  }

  if (mentions.length === 0) return { cleanText: text, context: [] };

  const context = [];
  for (const m of mentions) {
    try {
      const resolved = await resolveMention(m.type, m.value);
      if (resolved) context.push(resolved);
    } catch (e) {
      console.warn(`[Mentions] Failed to resolve ${m.full}:`, e.message);
    }
  }

  // Clean text: remove @mentions but keep the intent
  let cleanText = text;
  for (const m of mentions.reverse()) {
    cleanText = cleanText.slice(0, m.index) + cleanText.slice(m.index + m.full.length);
  }
  cleanText = cleanText.replace(/\s+/g, ' ').trim();

  return { cleanText, context };
}

async function resolveMention(type, value) {
  switch (type) {
    case 'file': {
      if (!value || !window.seal) return null;
      const path = value.startsWith('/') ? value : `${state.currentFolder}/${value}`;
      const result = await window.seal.fs.readFile(path);
      if (result.error) return null;
      return {
        type: 'file',
        path: path,
        name: path.split('/').pop(),
        content: result.content.slice(0, 10000), // Cap at 10K chars
        tokens: Math.ceil(result.content.length / 4),
      };
    }

    case 'folder': {
      if (!value || !window.seal) return null;
      const path = value.startsWith('/') ? value : `${state.currentFolder}/${value}`;
      const result = await window.seal.fs.readDir(path);
      if (result.error) return null;
      const tree = result.entries.map(e =>
        `${e.isDirectory ? '📁' : '📄'} ${e.name}`
      ).join('\n');
      return {
        type: 'folder',
        path: path,
        content: tree,
        fileCount: result.entries.length,
      };
    }

    case 'codebase': {
      // RAG query via SEAL bridge
      try {
        const result = await sealRequest('/api/memory/search', 'POST', {
          query: value || 'project overview',
          agent: 'TEAM',
          limit: 5,
        });
        if (result && result.results) {
          return {
            type: 'codebase',
            query: value,
            results: result.results.map(r => r.content).join('\n---\n'),
          };
        }
      } catch {}
      return null;
    }

    case 'agent': {
      // Pull agent's recent memory/traces
      const agent = (value || 'ADA').toUpperCase();
      try {
        const result = await sealRequest(`/api/soul/snapshot?agent=${agent}`);
        if (result) {
          return {
            type: 'agent',
            agent: agent,
            ocean: result.ocean,
            recentThoughts: result.recent_thoughts?.slice(0, 3),
            drift: result.drift,
          };
        }
      } catch {}
      return null;
    }

    case 'soul': {
      // Pull SOUL state
      try {
        const result = await sealRequest(`/api/soul/snapshot?agent=${value || 'ADA'}`);
        return result ? { type: 'soul', data: result } : null;
      } catch {}
      return null;
    }

    default:
      return null;
  }
}

/**
 * Build context string for AI from resolved mentions.
 * @param {Object[]} context — Resolved mentions
 * @returns {string}
 */
export function buildContextString(context) {
  if (context.length === 0) return '';

  const parts = context.map(c => {
    switch (c.type) {
      case 'file':
        return `<file path="${c.path}">\n${c.content}\n</file>`;
      case 'folder':
        return `<folder path="${c.path}">\n${c.content}\n</folder>`;
      case 'codebase':
        return `<codebase query="${c.query}">\n${c.results}\n</codebase>`;
      case 'agent':
        return `<agent name="${c.agent}" ocean="${JSON.stringify(c.ocean)}" drift="${c.drift}">\nRecent thoughts: ${JSON.stringify(c.recentThoughts)}\n</agent>`;
      case 'soul':
        return `<soul>\n${JSON.stringify(c.data, null, 2)}\n</soul>`;
      default:
        return '';
    }
  });

  return '\n\n--- Context from @mentions ---\n' + parts.join('\n') + '\n--- End context ---\n';
}

/**
 * Initialize @mention autocomplete on chat inputs.
 */
export function initMentionAutocomplete() {
  const inputs = ['chat-input-main', 'chat-input'];

  inputs.forEach(inputId => {
    const input = document.getElementById(inputId);
    if (!input) return;

    input.addEventListener('input', (e) => {
      const text = input.value;
      const cursor = input.selectionStart;
      const beforeCursor = text.slice(0, cursor);

      // Check if user just typed @
      const atMatch = beforeCursor.match(/@(\w*)$/);
      if (atMatch) {
        showMentionSuggestions(input, atMatch[1]);
      } else {
        hideMentionSuggestions();
      }
    });
  });
}

let suggestionBox = null;

function showMentionSuggestions(input, partial) {
  const suggestions = [
    { label: '@file:', hint: 'Incluir archivo como contexto', icon: '📄' },
    { label: '@folder:', hint: 'Incluir directorio', icon: '📁' },
    { label: '@codebase', hint: 'Buscar en proyecto (RAG)', icon: '🔍' },
    { label: '@agent:ADA', hint: 'Contexto de ADA', icon: '🔭' },
    { label: '@agent:JARVIS', hint: 'Contexto de JARVIS', icon: '🧠' },
    { label: '@soul:ADA', hint: 'SOUL snapshot', icon: '💜' },
  ].filter(s => !partial || s.label.toLowerCase().includes(partial.toLowerCase()));

  if (suggestions.length === 0) { hideMentionSuggestions(); return; }

  if (!suggestionBox) {
    suggestionBox = document.createElement('div');
    suggestionBox.className = 'mention-suggestions';
    document.body.appendChild(suggestionBox);
  }

  const rect = input.getBoundingClientRect();
  suggestionBox.style.left = `${rect.left}px`;
  suggestionBox.style.bottom = `${window.innerHeight - rect.top + 4}px`;
  suggestionBox.classList.remove('hidden');

  suggestionBox.innerHTML = suggestions.map(s =>
    `<div class="mention-item" data-label="${escHtml(s.label)}">
      <span>${s.icon}</span><span class="mention-label">${escHtml(s.label)}</span><span class="mention-hint">${escHtml(s.hint)}</span>
    </div>`
  ).join('');

  suggestionBox.querySelectorAll('.mention-item').forEach(item => {
    item.addEventListener('click', () => {
      const label = item.dataset.label;
      const text = input.value;
      const cursor = input.selectionStart;
      const atStart = text.lastIndexOf('@', cursor - 1);
      input.value = text.slice(0, atStart) + label + text.slice(cursor);
      input.focus();
      input.selectionStart = input.selectionEnd = atStart + label.length;
      hideMentionSuggestions();
    });
  });
}

function hideMentionSuggestions() {
  if (suggestionBox) suggestionBox.classList.add('hidden');
}
