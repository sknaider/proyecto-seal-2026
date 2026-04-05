/**
 * Código SEAL — Boot Module
 * Orchestrates initialization of all modules.
 */

import { state } from './state.js';
import { sleep, sealRequest } from './utils.js';
import { initMonaco, saveCurrentFile } from './editor.js';
import { initTerminalButtons } from './terminal.js';
import { initBottomPanel, activateBottomTab } from './bottom-panel.js';
import { initChat } from './chat.js';
import { initFileTree, openFolder, newFile } from './explorer.js';
import { initSearch } from './search.js';
import { initGit } from './git.js';
import { initSOUL, fetchSOUL, updateOceanDisplay } from './soul.js';
import { initManagerView, startHealthMonitor } from './manager.js';
import { initKeyboard, initMenuEvents } from './keyboard.js';
import { initCommandPalette } from './command-palette.js';
import { initContextMenus } from './context-menu.js';
import { initResizeHandles } from './resize.js';
import { loadSkills } from './skills.js';
import { initGhostText } from './ghost-text.js';
import { initInlineChat } from './inline-chat.js';
import { initDiffView } from './diff-view.js';
import { initMentionAutocomplete } from './context-mentions.js';
import { initErrorDetection } from './error-detection.js';
import { initComposer } from './composer.js';
import { initLayouts } from './layouts.js';

export async function boot() {
  const status = document.getElementById('boot-status');
  const msgs = document.getElementById('boot-messages');

  const addMsg = (from, text, cls) => {
    const d = document.createElement('div');
    d.className = `boot-msg ${cls}`;
    d.textContent = `${from}: ${text}`;
    d.style.animationDelay = `${msgs.children.length * 0.2}s`;
    msgs.appendChild(d);
  };

  // Phase 1: Connect to SOUL
  status.textContent = 'Conectando SOUL...';
  let soul = null;
  try {
    soul = await sealRequest('/api/soul/snapshot?agent=ADA');
    status.textContent = 'SOUL conectado ✓';
  } catch { status.textContent = 'SOUL offline — modo degradado'; }
  await sleep(200);

  // Phase 2: Check GPU
  status.textContent = 'Verificando GPU...';
  let gpuInfo = null;
  if (window.seal && window.seal.system) {
    try { gpuInfo = await window.seal.system.gpu(); } catch {}
  }
  await sleep(150);

  // Phase 3: Team greetings
  status.textContent = 'Equipo presente...';
  await sleep(200);

  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Buenos días' : hour < 19 ? 'Buenas tardes' : 'Buenas noches';

  if (soul && soul.ocean) {
    const o = soul.ocean;
    addMsg('ADA', `${greeting} William. OCEAN: O=${(o.O||0).toFixed(2)} C=${(o.C||0).toFixed(2)} E=${(o.E||0).toFixed(2)}. La casa te espera.`, 'ada');
    await sleep(400);
    addMsg('JARVIS', `Arquitectura lista. Sin drift. Todo en orden.`, 'jarvis');
    await sleep(400);
    const gpuText = gpuInfo && !gpuInfo.error ? `GPU: ${gpuInfo.temp}°C, ${gpuInfo.util}% util.` : 'GPU: verificando...';
    addMsg('DUM', `${gpuText} Vigilando.`, 'dum');
  } else {
    addMsg('ADA', `${greeting} William. Modo offline — SOUL reconectará.`, 'ada');
    await sleep(300);
    addMsg('JARVIS', `Equipo presente.`, 'jarvis');
    await sleep(200);
    addMsg('DUM', `Vigilando.`, 'dum');
  }
  await sleep(500);

  status.textContent = '¡Listo!';
  await sleep(250);
  document.getElementById('boot-screen').classList.add('fade-out');
  await sleep(500);
  document.getElementById('boot-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');

  // Init all modules
  initActivityBar();
  await initMonaco();
  initBottomPanel();
  initTerminalButtons();
  initChat();
  initFileTree();
  initSearch();
  initGit();
  initSOUL();
  initManagerView();
  initKeyboard();
  initMenuEvents();
  initCommandPalette();
  initResizeHandles();
  initContextMenus();
  loadSkills();
  startHealthMonitor();
  initGhostText();
  initInlineChat();
  initDiffView();
  initMentionAutocomplete();
  initErrorDetection();
  initComposer();
  initLayouts();
  initWelcomeActions();

  if (soul && soul.ocean) updateOceanDisplay(soul.ocean);

  // Cleanup on unload
  window.addEventListener('beforeunload', () => {
    state.intervals.forEach(clearInterval);
    if (state.managerInterval) clearInterval(state.managerInterval);
  });
}

function initActivityBar() {
  document.querySelectorAll('.activity-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.activity-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.side-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const panel = document.getElementById(`panel-${btn.dataset.panel}`);
      if (panel) panel.classList.add('active');
    });
  });
}

function initWelcomeActions() {
  document.querySelectorAll('.welcome-action[data-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.action;
      if (action === 'open-folder') openFolder();
      else if (action === 'new-file') newFile();
      else if (action === 'terminal') activateBottomTab('terminal');
      else if (action === 'chat') activateBottomTab('chat');
      else if (action === 'palette' && window._openPalette) window._openPalette();
    });
  });
}
