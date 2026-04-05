/**
 * Código SEAL — SEAL Studio Layouts (Phase 4.7)
 * Predefined workspace layouts for different workflows.
 * Code, Debug, Review, Medical, Manager modes.
 */

import { state } from './state.js';
import { activatePanel } from './keyboard.js';
import { activateBottomTab } from './bottom-panel.js';
import { toggleComposer } from './composer.js';
import { showToast } from './utils.js';

const LAYOUTS = {
  code: {
    name: 'Code',
    icon: '💻',
    description: 'Editor + Terminal + Chat',
    sidebar: 'explorer',
    bottomTab: 'terminal',
    composerOpen: false,
  },
  debug: {
    name: 'Debug',
    icon: '🔍',
    description: 'Editor + Terminal + Errors',
    sidebar: 'explorer',
    bottomTab: 'terminal',
    composerOpen: false,
  },
  review: {
    name: 'Review',
    icon: '📋',
    description: 'Git + Diff + Chat',
    sidebar: 'git',
    bottomTab: 'chat',
    composerOpen: false,
  },
  medical: {
    name: 'Medical AI',
    icon: '🏥',
    description: 'SOUL + Chat + Data Sovereign',
    sidebar: 'soul',
    bottomTab: 'chat',
    composerOpen: false,
  },
  manager: {
    name: 'Manager',
    icon: '🎯',
    description: 'Agents + Tasks + Health',
    sidebar: 'manager',
    bottomTab: 'chat',
    composerOpen: false,
  },
  composer: {
    name: 'Composer',
    icon: '✨',
    description: 'Multi-file AI editing',
    sidebar: 'explorer',
    bottomTab: 'chat',
    composerOpen: true,
  },
};

let currentLayout = 'code';

export function initLayouts() {
  // Add layout switcher to status bar
  const statusLeft = document.querySelector('.status-left');
  if (statusLeft) {
    const layoutBtn = document.createElement('span');
    layoutBtn.className = 'status-item status-layout';
    layoutBtn.id = 'status-layout';
    layoutBtn.textContent = `${LAYOUTS.code.icon} ${LAYOUTS.code.name}`;
    layoutBtn.style.cursor = 'pointer';
    layoutBtn.addEventListener('click', showLayoutPicker);
    statusLeft.appendChild(layoutBtn);
  }
}

function showLayoutPicker() {
  const existing = document.getElementById('layout-picker');
  if (existing) { existing.remove(); return; }

  const picker = document.createElement('div');
  picker.id = 'layout-picker';
  picker.style.cssText = 'position:fixed;bottom:24px;left:10px;background:var(--seal-surface);border:1px solid var(--seal-border);border-radius:8px;padding:6px;z-index:500;box-shadow:0 4px 16px rgba(0,0,0,0.5);';

  picker.innerHTML = Object.entries(LAYOUTS).map(([key, layout]) => `
    <div class="layout-option ${key === currentLayout ? 'active' : ''}" data-layout="${key}" style="padding:6px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:12px;border-radius:4px;${key === currentLayout ? 'background:var(--seal-accent-dim);' : ''}">
      <span>${layout.icon}</span>
      <span style="color:var(--seal-text)">${layout.name}</span>
      <span style="color:var(--seal-text-dim);font-size:10px;margin-left:auto">${layout.description}</span>
    </div>
  `).join('');

  picker.querySelectorAll('.layout-option').forEach(opt => {
    opt.addEventListener('mouseenter', () => opt.style.background = 'var(--seal-surface2)');
    opt.addEventListener('mouseleave', () => {
      opt.style.background = opt.dataset.layout === currentLayout ? 'var(--seal-accent-dim)' : '';
    });
    opt.addEventListener('click', () => {
      applyLayout(opt.dataset.layout);
      picker.remove();
    });
  });

  document.body.appendChild(picker);
  setTimeout(() => {
    const closer = (e) => { if (!picker.contains(e.target)) { picker.remove(); document.removeEventListener('click', closer); } };
    document.addEventListener('click', closer);
  }, 10);
}

export function applyLayout(layoutKey) {
  const layout = LAYOUTS[layoutKey];
  if (!layout) return;

  currentLayout = layoutKey;

  // Apply sidebar
  if (layout.sidebar) activatePanel(layout.sidebar);

  // Apply bottom tab
  if (layout.bottomTab) activateBottomTab(layout.bottomTab);

  // Composer
  if (layout.composerOpen && typeof toggleComposer === 'function') toggleComposer();

  // Update status bar
  const el = document.getElementById('status-layout');
  if (el) el.textContent = `${layout.icon} ${layout.name}`;

  showToast(`Layout: ${layout.name}`, 'info', 1500);
}

export function getCurrentLayout() { return currentLayout; }
export function getLayouts() { return LAYOUTS; }
