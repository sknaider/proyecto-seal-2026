/**
 * Código SEAL — Skills Panel Module
 * Load and display skills with search and categories.
 */

import { state } from './state.js';
import { escHtml } from './utils.js';
import { addChatMessage } from './chat.js';

export let allSkills = [];

export async function loadSkills() {
  const skillsEl = document.getElementById('skills-list');
  const countEl = document.getElementById('skills-count');
  const searchEl = document.getElementById('skills-search');
  if (!skillsEl) return;

  const builtinSkills = [
    { name: 'commit', description: 'Git commit inteligente', dir: 'commit', category: 'SEAL', icon: '📝' },
    { name: 'review', description: 'Code review del equipo', dir: 'review', category: 'SEAL', icon: '🔍' },
    { name: 'dream', description: 'Dream consolidation', dir: 'dream', category: 'SEAL', icon: '🌙' },
    { name: 'snapshot', description: 'SOUL snapshot completo', dir: 'seal-snapshot', category: 'SEAL', icon: '📊' },
    { name: 'audit', description: 'Auditoría del sistema', dir: 'seal-audit', category: 'SEAL', icon: '🔭' },
    { name: 'handoff', description: 'Handoff entre agentes', dir: 'seal-handoff', category: 'SEAL', icon: '🤝' },
    { name: 'train', description: 'Training pipeline', dir: 'seal-train', category: 'SEAL', icon: '🧪' },
    { name: 'eval', description: 'Evaluación de modelos', dir: 'seal-eval', category: 'SEAL', icon: '📋' },
  ];
  allSkills = [...builtinSkills];

  if (window.seal && window.seal.fs) {
    const SKILLS_DIR = '/home/dadito/.claude/skills';
    try {
      const tree = await window.seal.fs.readDir(SKILLS_DIR);
      if (tree && tree.entries) {
        for (const child of tree.entries) {
          if (!child.isDirectory) continue;
          if (allSkills.some(s => s.dir === child.name)) continue;
          try {
            const result = await window.seal.fs.readFile(`${SKILLS_DIR}/${child.name}/SKILL.md`);
            if (!result || result.error || !result.content) continue;
            const match = result.content.match(/^---\n([\s\S]*?)\n---/);
            if (match) {
              const fm = {};
              match[1].split('\n').forEach(line => {
                const [k, ...v] = line.split(':');
                if (k && v.length) fm[k.trim()] = v.join(':').trim().replace(/^["']|["']$/g, '');
              });
              allSkills.push({
                name: fm.name || child.name,
                description: (fm.description || '').slice(0, 80),
                dir: child.name,
                category: child.name.startsWith('seal-') ? 'SEAL' : 'General',
                icon: child.name.startsWith('seal-') ? '🔭' : '⚡',
              });
            }
          } catch {}
        }
      }
    } catch {}
  }

  allSkills.sort((a, b) => {
    if (a.category !== b.category) return a.category === 'SEAL' ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  if (countEl) countEl.textContent = `(${allSkills.length})`;
  renderSkills(allSkills);

  if (searchEl) {
    searchEl.addEventListener('input', () => {
      const q = searchEl.value.toLowerCase().trim();
      if (!q) { renderSkills(allSkills); return; }
      renderSkills(allSkills.filter(s => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q)));
    });
  }
}

function renderSkills(skills) {
  const el = document.getElementById('skills-list');
  if (!el) return;
  if (skills.length === 0) { el.innerHTML = '<div class="empty-state">No se encontraron skills</div>'; return; }

  let html = '';
  let lastCategory = '';
  for (const s of skills) {
    if (s.category !== lastCategory) {
      html += `<div class="skill-category">${escHtml(s.category)}</div>`;
      lastCategory = s.category;
    }
    html += `<div class="skill-item" data-skill="${escHtml(s.dir)}" title="${escHtml(s.description)}">
      <span class="skill-icon">${s.icon}</span>
      <span class="skill-name">/${escHtml(s.name)}</span>
      <span class="skill-desc">${escHtml(s.description)}</span>
    </div>`;
  }
  el.innerHTML = html;

  el.querySelectorAll('.skill-item').forEach(item => {
    item.addEventListener('click', () => {
      const name = item.dataset.skill;
      addChatMessage({ from: 'William', message: `/${name}`, timestamp: new Date().toISOString() });
      if (state.chatWs && state.chatWs.readyState === WebSocket.OPEN) {
        state.chatWs.send(JSON.stringify({ action: 'say', message: `/${name}` }));
      }
    });
  });
}
