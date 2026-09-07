/**
 * Código SEAL — Renderer App v2
 * ================================
 * Full renderer: Monaco Editor, xterm.js terminal, Chat WebSocket,
 * SOUL Dashboard live, Manager View, File Explorer, Boot Sequence.
 *
 * Monaco loaded via CDN AMD loader. Terminal via node-pty IPC.
 * Chat via WebSocket to localhost:8765. SOUL via bridge API.
 */

// ── State ──────────────────────────────────────────────────────────
const state = {
  currentFolder: null,
  openFiles: [],
  activeFile: null,
  editor: null,         // Monaco editor instance
  editorModels: {},     // path → Monaco model
  terminals: [],        // [{ id, term, fit, ptyId }]
  activeTermIdx: -1,
  // Legacy aliases
  get terminal() { return this.terminals[this.activeTermIdx]?.term || null; },
  get terminalId() { return this.terminals[this.activeTermIdx]?.ptyId || null; },
  get terminalFit() { return this.terminals[this.activeTermIdx]?.fit || null; },
  chatWs: null,
  chatConnected: false,
  soulAgent: 'ADA',
  managerInterval: null,
};

const BRIDGE_URL = 'http://localhost:8766';
const CHAT_WS = 'ws://localhost:8765/ws';

// ── Shared: Save current file (single source of truth) ──────────
async function saveCurrentFile() {
  if (!state.editor || !state.activeFile || !window.seal) return;
  const content = state.editor.getValue();
  const result = await window.seal.fs.writeFile(state.activeFile.path, content);
  if (result && result.error) { showToast(`Error: ${result.error}`, 'error'); return; }
  const tab = findTabByPath(state.activeFile.path);
  if (tab) tab.classList.remove('modified');
  document.getElementById('current-file').textContent = state.activeFile.name;
  showToast(`Guardado: ${state.activeFile.name}`, 'success', 2000);
}

// ── Shared: Expand/Collapse bottom panel (single source of truth) ─
function expandBottomPanel() {
  const w = document.getElementById('chat-wrapper');
  const b = document.getElementById('btn-toggle-bottom');
  w.classList.remove('collapsed'); w.classList.add('expanded');
  b.textContent = '\u25B2';
}
function collapseBottomPanel() {
  const w = document.getElementById('chat-wrapper');
  const b = document.getElementById('btn-toggle-bottom');
  w.classList.remove('expanded'); w.classList.add('collapsed');
  b.textContent = '\u25BC';
}

// ── Shared: Ensure terminal exists ────────────────────────────────
function ensureTerminal() {
  if (state.terminals.length === 0) createTerminal();
}

// ── Boot Sequence ──────────────────────────────────────────────────
async function boot() {
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

  // Init all components
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

  if (soul && soul.ocean) updateOceanDisplay(soul.ocean);
}

// ── Monaco Editor ──────────────────────────────────────────────────
async function initMonaco() {
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
    console.error('Monaco init failed:', e);
  }
}

function openFileInEditor(file) {
  const container = document.getElementById('monaco-container');
  const welcome = document.getElementById('welcome');
  welcome.classList.remove('visible');
  container.style.display = 'block';

  const monaco = window.monacoInstance;
  if (!monaco) {
    container.innerHTML = `<pre style="padding:12px;overflow:auto;height:100%;margin:0;font-size:12px;color:var(--seal-text);background:var(--seal-bg)">${escHtml(file.content)}</pre>`;
    return;
  }

  // Get or create model
  if (!state.editorModels[file.path]) {
    const lang = getLanguage(file.name);
    state.editorModels[file.path] = monaco.editor.createModel(file.content, lang);
  }

  // Create editor if not exists
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

    // Save on Ctrl+S
    state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveCurrentFile());

    // Track modifications
    state.editor.onDidChangeModelContent(() => {
      if (state.activeFile) {
        const tab = findTabByPath(state.activeFile.path);
        if (tab) tab.classList.add('modified');
      }
    });

    // Update status bar cursor position
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

function getLanguage(filename) {
  const ext = filename.split('.').pop().toLowerCase();
  const map = {
    py: 'python', js: 'javascript', ts: 'typescript', jsx: 'javascript',
    tsx: 'typescript', json: 'json', md: 'markdown', html: 'html',
    css: 'css', sh: 'shell', bash: 'shell', sql: 'sql', yaml: 'yaml',
    yml: 'yaml', xml: 'xml', rs: 'rust', go: 'go', java: 'java',
    cpp: 'cpp', c: 'c', h: 'c', hpp: 'cpp', rb: 'ruby', php: 'php',
    txt: 'plaintext', log: 'plaintext', csv: 'plaintext',
  };
  return map[ext] || 'plaintext';
}

// ── Activity Bar ───────────────────────────────────────────────────
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

// ── Bottom Panel (Chat + Terminal) ────────────────────────────────
function initBottomPanel() {
  const wrapper = document.getElementById('chat-wrapper');
  const toggleBtn = document.getElementById('btn-toggle-bottom');

  // Toggle expand/collapse
  toggleBtn.addEventListener('click', () => {
    if (wrapper.classList.contains('collapsed')) {
      expandBottomPanel();
      if (document.getElementById('terminal-bottom-panel').classList.contains('active')) ensureTerminal();
      if (state.terminalFit) setTimeout(() => state.terminalFit.fit(), 50);
    } else {
      collapseBottomPanel();
    }
  });

  // Bottom tab switching
  document.querySelectorAll('.bottom-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.bottom-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.bottom-content').forEach(c => c.classList.remove('active'));
      tab.classList.add('active');
      const panel = document.getElementById(`${tab.dataset.bottom}-bottom-panel`);
      if (panel) panel.classList.add('active');

      // Expand if collapsed
      if (wrapper.classList.contains('collapsed')) expandBottomPanel();

      // Create terminal on first switch
      if (tab.dataset.bottom === 'terminal') ensureTerminal();
      if (tab.dataset.bottom === 'terminal' && state.terminalFit) {
        setTimeout(() => state.terminalFit.fit(), 50);
      }
    });
  });
}

// ── Terminal ───────────────────────────────────────────────────────
async function createTerminal() {
  const container = document.getElementById('xterm-bottom');
  if (!container) return;

  const idx = state.terminals.length;
  const termName = `Terminal ${idx + 1}`;

  const term = new Terminal({
    theme: {
      background: '#0a0a0f', foreground: '#e2e8f0', cursor: '#3b82f6',
      selectionBackground: '#3b82f640',
      black: '#0a0a0f', red: '#ef4444', green: '#22c55e', yellow: '#f59e0b',
      blue: '#3b82f6', magenta: '#a78bfa', cyan: '#06b6d4', white: '#e2e8f0',
    },
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    fontSize: 12, cursorBlink: true, cursorStyle: 'bar',
  });

  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);

  // Create a wrapper div for this terminal
  const termDiv = document.createElement('div');
  termDiv.id = `xterm-instance-${idx}`;
  termDiv.style.cssText = 'width:100%;height:100%;display:none;';
  container.appendChild(termDiv);

  term.open(termDiv);

  const termEntry = { id: idx, term, fit: fitAddon, ptyId: null, name: termName, el: termDiv };
  state.terminals.push(termEntry);

  if (window.seal && window.seal.terminal) {
    const result = await window.seal.terminal.create({
      cols: term.cols, rows: term.rows, cwd: state.currentFolder || undefined,
    });
    if (result && result.id) {
      termEntry.ptyId = result.id;
      term.onData(data => window.seal.terminal.write(result.id, data));
      // Listeners handled globally in initTerminalButtons (no per-terminal leak)
    }
  } else {
    term.writeln('\x1b[36m  ╔═══════════════════════════════════════╗\x1b[0m');
    term.writeln('\x1b[36m  ║     🔭 Código SEAL — Terminal        ║\x1b[0m');
    term.writeln('\x1b[36m  ╚═══════════════════════════════════════╝\x1b[0m');
    term.writeln('');
    term.writeln(`\x1b[33m  ${termName} — Terminal disponible en Electron.\x1b[0m`);
  }

  const ro = new ResizeObserver(() => { try { fitAddon.fit(); } catch {} });
  ro.observe(termDiv);
  termEntry.resizeObserver = ro;

  // Activate this terminal
  switchTerminal(idx);
  renderTerminalTabs();
  setTimeout(() => fitAddon.fit(), 100);
}

function switchTerminal(idx) {
  state.terminals.forEach((t, i) => {
    t.el.style.display = i === idx ? 'block' : 'none';
  });
  state.activeTermIdx = idx;
  const active = state.terminals[idx];
  if (active) {
    setTimeout(() => { active.fit.fit(); active.term.focus(); }, 50);
  }
  renderTerminalTabs();
}

function renderTerminalTabs() {
  const tabsEl = document.getElementById('terminal-tabs');
  if (!tabsEl) return;
  tabsEl.innerHTML = state.terminals.map((t, i) =>
    `<button class="term-tab ${i === state.activeTermIdx ? 'active' : ''}" data-idx="${i}">
      ⚡ ${escHtml(t.name)}<span class="term-tab-close" data-close="${i}">×</span>
    </button>`
  ).join('');

  tabsEl.querySelectorAll('.term-tab').forEach(btn => {
    btn.addEventListener('click', (e) => {
      if (e.target.classList.contains('term-tab-close')) return;
      switchTerminal(parseInt(btn.dataset.idx));
    });
  });
  tabsEl.querySelectorAll('.term-tab-close').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      removeTerminalTab(parseInt(btn.dataset.close));
    });
  });
}

function removeTerminalTab(idx) {
  if (idx < 0 || idx >= state.terminals.length) return;
  const t = state.terminals[idx];
  if (!t) return;
  if (t.ptyId && window.seal && window.seal.terminal) window.seal.terminal.kill(t.ptyId);
  if (t.resizeObserver) t.resizeObserver.disconnect();
  t.term.dispose();
  t.el.remove();
  state.terminals.splice(idx, 1);
  // Re-index and fix activeTermIdx
  state.terminals.forEach((t, i) => { t.id = i; });
  if (idx < state.activeTermIdx) state.activeTermIdx--;
  if (state.activeTermIdx >= state.terminals.length) state.activeTermIdx = state.terminals.length - 1;
  if (state.terminals.length > 0) switchTerminal(state.activeTermIdx);
  else { state.activeTermIdx = -1; renderTerminalTabs(); }
}

// Init + button for new terminal + global IPC listeners (registered once)
let termListenersRegistered = false;
function initTerminalButtons() {
  document.getElementById('btn-add-terminal')?.addEventListener('click', createTerminal);
  if (!termListenersRegistered && window.seal && window.seal.terminal) {
    window.seal.terminal.onData(({ id, data }) => {
      const t = state.terminals.find(t => t.ptyId === id);
      if (t) t.term.write(data);
    });
    window.seal.terminal.onExit(({ id }) => {
      const t = state.terminals.find(t => t.ptyId === id);
      if (t) {
        t.term.writeln('\r\n\x1b[33m[Terminal cerrado]\x1b[0m');
        removeTerminalTab(t.id);
      }
    });
    termListenersRegistered = true;
  }
}

// ── Chat ───────────────────────────────────────────────────────────
function initChat() {
  // Sidebar chat (compact)
  document.getElementById('btn-send').addEventListener('click', () => sendChatFrom('chat-input'));
  document.getElementById('chat-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) sendChatFrom('chat-input');
  });

  // Bottom panel chat (main, larger)
  document.getElementById('btn-send-main').addEventListener('click', () => sendChatFrom('chat-input-main'));
  document.getElementById('chat-input-main').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatFrom('chat-input-main'); }
  });

  connectChat();
}

let chatReconnectDelay = 1000;
const CHAT_MAX_RECONNECT = 60000;

function connectChat() {
  try {
    state.chatWs = new WebSocket(CHAT_WS);
    state.chatWs.onopen = () => {
      state.chatConnected = true;
      chatReconnectDelay = 1000; // reset backoff
      updateChatDots('connected');
      showToast('Chat del equipo conectado', 'success', 2000);
    };
    state.chatWs.onclose = () => {
      state.chatConnected = false;
      updateChatDots('disconnected');
      setTimeout(connectChat, chatReconnectDelay);
      chatReconnectDelay = Math.min(chatReconnectDelay * 2, CHAT_MAX_RECONNECT);
    };
    state.chatWs.onerror = () => state.chatWs.close();
    state.chatWs.onmessage = e => {
      try {
        const msg = JSON.parse(e.data);
        // Skip echo of own messages (prevent duplicates)
        if (msg.from === 'William' && msg._echo) return;
        addChatMessage(msg);
      } catch {}
    };
  } catch {
    updateChatDots('disconnected');
    setTimeout(connectChat, chatReconnectDelay);
    chatReconnectDelay = Math.min(chatReconnectDelay * 2, CHAT_MAX_RECONNECT);
  }
}

function updateChatDots(cls) {
  ['chat-dot', 'chat-dot-bottom'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.className = `chat-dot ${cls}`;
  });
}

function addChatMessage(msg) {
  const from = (msg.from || 'SYS').toLowerCase();
  const text = escHtml((msg.message || '').slice(0, 2000));
  const time = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }) : '';

  // Sidebar (compact)
  const sidebar = document.getElementById('chat-messages');
  if (sidebar) {
    const d = document.createElement('div');
    d.className = 'chat-msg';
    d.innerHTML = `<span class="chat-from ${from}">${escHtml(msg.from || 'SYS')}</span> <span class="chat-text">${text.slice(0, 500)}</span>`;
    sidebar.appendChild(d);
    sidebar.scrollTop = sidebar.scrollHeight;
  }

  // Bottom panel (rich, styled bubbles) — cap at 500 to prevent DOM leak
  const main = document.getElementById('chat-messages-main');
  if (main) {
    const d = document.createElement('div');
    d.className = `chat-msg-main ${from}`;
    d.innerHTML = `<div class="chat-msg-header"><span class="chat-msg-from ${from}">${escHtml(msg.from || 'SYS')}</span><span class="chat-msg-time">${time}</span></div><div class="chat-msg-text">${text}</div>`;
    main.appendChild(d);
    main.scrollTop = main.scrollHeight;
    while (main.children.length > 500) main.removeChild(main.firstChild);
  }
}

function sendChatFrom(inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  if (state.chatWs && state.chatWs.readyState === WebSocket.OPEN) {
    state.chatWs.send(JSON.stringify({ action: 'say', message: text }));
  }
  // Also show locally as "William" immediately
  addChatMessage({ from: 'William', message: text, timestamp: new Date().toISOString() });
  input.value = '';
  input.focus();
}

// ── File Explorer ──────────────────────────────────────────────────
function initFileTree() {
  document.getElementById('btn-open-folder').addEventListener('click', openFolder);
  document.getElementById('btn-new-tab')?.addEventListener('click', newFile);
}

async function openFolder() {
  let folderPath = state.currentFolder || '/home/dadito/IA/proyecto-seal';

  // Use Electron dialog if available
  if (window.seal && window.seal.dialog) {
    try {
      const result = await window.seal.dialog.openFolder();
      if (result.canceled) return;
      folderPath = result.filePaths[0];
    } catch {}
  }

  await loadDirectory(folderPath);
  state.currentFolder = folderPath;
  document.getElementById('explorer-empty').classList.add('hidden');
  // Update panel title with folder name
  const panelTitle = document.querySelector('#panel-explorer .panel-title');
  if (panelTitle) {
    const folderName = folderPath.split('/').pop() || folderPath;
    panelTitle.textContent = `EXPLORADOR — ${folderName.toUpperCase()}`;
  }
  showToast(`Carpeta abierta: ${folderPath.split('/').pop()}`, 'info', 2000);
}

async function loadDirectory(dirPath, parentEl, depth = 0) {
  if (!window.seal) return;
  const container = parentEl || document.getElementById('file-tree');
  if (!parentEl) container.innerHTML = '';

  try {
    const result = await window.seal.fs.readDir(dirPath);
    if (result.error) return;
    const entries = result.entries
      .filter(e => !e.name.startsWith('.') && !['node_modules','__pycache__','.git','dist','build'].includes(e.name))
      .sort((a, b) => (a.isDirectory === b.isDirectory) ? a.name.localeCompare(b.name) : a.isDirectory ? -1 : 1);

    for (const entry of entries) {
      const item = document.createElement('div');
      item.className = 'tree-item';
      item.style.paddingLeft = `${8 + depth * 16}px`;
      const icon = entry.isDirectory ? '📁' : getFileIcon(entry.name);
      item.innerHTML = `<span class="tree-icon">${icon}</span><span class="tree-name">${entry.name}</span>`;

      if (entry.isDirectory) {
        let expanded = false;
        let loading = false;
        item.addEventListener('click', async () => {
          if (loading) return;
          const next = item.nextElementSibling;
          if (expanded && next && next.className === 'tree-children') {
            next.remove(); expanded = false;
            item.querySelector('.tree-icon').textContent = '📁';
          } else {
            loading = true;
            const ch = document.createElement('div');
            ch.className = 'tree-children';
            item.after(ch);
            await loadDirectory(entry.path, ch, depth + 1);
            expanded = true;
            loading = false;
            item.querySelector('.tree-icon').textContent = '📂';
          }
        });
      } else {
        item.addEventListener('click', () => openFile(entry.path, entry.name));
      }
      container.appendChild(item);
    }
  } catch {}
}

function getFileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  return { py:'🐍', js:'📜', ts:'📘', json:'📋', md:'📝', html:'🌐', css:'🎨',
    sh:'⚙️', yaml:'📄', yml:'📄', sql:'🗃️', txt:'📄', log:'📊',
    jsx:'⚛️', tsx:'⚛️', rs:'🦀', go:'🔵', java:'☕', toml:'📄' }[ext] || '📄';
}

async function openFile(filePath, fileName) {
  if (!window.seal) return;
  const existing = state.openFiles.find(f => f.path === filePath);
  if (existing) { setActiveFile(existing); return; }
  const result = await window.seal.fs.readFile(filePath);
  if (result.error) return;
  const file = { path: filePath, name: fileName, content: result.content };
  state.openFiles.push(file);
  addTab(file);
  setActiveFile(file);
}

function addTab(file) {
  const tabs = document.getElementById('tabs');
  const tab = document.createElement('div');
  tab.className = 'tab';
  tab.dataset.path = file.path;
  tab.innerHTML = `<span class="tab-icon">${getFileIcon(file.name)}</span>${file.name}<span class="tab-close">×</span>`;
  tab.addEventListener('click', () => setActiveFile(file));
  tab.querySelector('.tab-close').addEventListener('click', e => { e.stopPropagation(); closeTab(file); });
  tabs.appendChild(tab);
}

function setActiveFile(file) {
  state.activeFile = file;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const tab = findTabByPath(file.path);
  if (tab) tab.classList.add('active');
  openFileInEditor(file);
  updateBreadcrumb(file.path);
}

function updateBreadcrumb(filePath) {
  const el = document.getElementById('breadcrumb');
  if (!el) return;
  const parts = filePath.split('/').filter(Boolean);
  // Show last 4 segments max
  const shown = parts.slice(-4);
  el.innerHTML = (parts.length > 4 ? '<span class="breadcrumb-segment">...</span><span class="breadcrumb-sep">›</span>' : '') +
    shown.map((p, i) =>
      `<span class="breadcrumb-segment">${escHtml(p)}</span>${i < shown.length - 1 ? '<span class="breadcrumb-sep">›</span>' : ''}`
    ).join('');
}

function closeTab(file) {
  state.openFiles = state.openFiles.filter(f => f.path !== file.path);
  const tab = findTabByPath(file.path);
  if (tab) tab.remove();
  if (state.editorModels[file.path]) {
    state.editorModels[file.path].dispose();
    delete state.editorModels[file.path];
  }
  if (state.activeFile === file) {
    if (state.openFiles.length > 0) setActiveFile(state.openFiles[state.openFiles.length - 1]);
    else {
      state.activeFile = null;
      document.getElementById('monaco-container').style.display = 'none';
      document.getElementById('welcome').classList.add('visible');
      document.getElementById('current-file').textContent = '';
      if (state.editor) { state.editor.dispose(); state.editor = null; }
    }
  }
}

// ── Tab Navigation ────────────────────────────────────────────────
function cycleTab(direction) {
  if (state.openFiles.length < 2) return;
  const idx = state.openFiles.indexOf(state.activeFile);
  let next = (idx + direction + state.openFiles.length) % state.openFiles.length;
  setActiveFile(state.openFiles[next]);
}

let untitledCounter = 0;
function newFile() {
  untitledCounter++;
  const name = `sin-titulo-${untitledCounter}`;
  const path = `/tmp/${name}`;
  const file = { path, name, content: '' };
  state.openFiles.push(file);
  addTab(file);
  setActiveFile(file);
}

// ── File Dialogs ──────────────────────────────────────────────────
async function openFileDialog() {
  if (!window.seal || !window.seal.dialog) return;
  try {
    const result = await window.seal.dialog.openFile();
    if (result.canceled || !result.filePaths || !result.filePaths[0]) return;
    const filePath = result.filePaths[0];
    const name = filePath.split('/').pop();
    await openFile(filePath, name);
  } catch {}
}

async function saveFileAs() {
  if (!state.editor || !window.seal || !window.seal.dialog) return;
  try {
    const defaultPath = state.activeFile ? state.activeFile.path : '';
    const result = await window.seal.dialog.saveFile(defaultPath);
    if (result.canceled || !result.filePath) return;
    const content = state.editor.getValue();
    await window.seal.fs.writeFile(result.filePath, content);
    // Update active file reference
    const name = result.filePath.split('/').pop();
    if (state.activeFile) {
      const oldPath = state.activeFile.path;
      state.activeFile.path = result.filePath;
      state.activeFile.name = name;
      // Update Monaco model key
      if (state.editorModels[oldPath]) {
        state.editorModels[result.filePath] = state.editorModels[oldPath];
        delete state.editorModels[oldPath];
      }
      const tab = findTabByPath(oldPath);
      if (tab) { tab.dataset.path = result.filePath; tab.classList.remove('modified'); }
    }
    document.getElementById('current-file').textContent = name;
  } catch {}
}

// ── Search in Files ───────────────────────────────────────────────
function initSearch() {
  const input = document.getElementById('search-input');
  const results = document.getElementById('search-results');
  if (!input || !results) return;

  let searchTimeout = null;
  input.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    const query = input.value.trim();
    if (query.length < 2) { results.innerHTML = ''; return; }
    searchTimeout = setTimeout(() => searchInFiles(query), 300);
  });
}

async function searchInFiles(query) {
  const resultsEl = document.getElementById('search-results');
  if (!resultsEl || !state.currentFolder) {
    if (resultsEl) resultsEl.innerHTML = '<div class="empty-state">Abre una carpeta primero</div>';
    return;
  }

  resultsEl.innerHTML = '<div class="empty-state">Buscando...</div>';

  try {
    // Single IPC call — search runs in main process (fast, no round-trips)
    let matches;
    if (window.seal && window.seal.fs && window.seal.fs.searchInFiles) {
      const result = await window.seal.fs.searchInFiles(state.currentFolder, query);
      matches = result.results || [];
    } else {
      // Fallback for browser testing (no Electron)
      matches = [];
    }

    if (matches.length === 0) {
      resultsEl.innerHTML = '<div class="empty-state">Sin resultados</div>';
      return;
    }

    resultsEl.innerHTML = matches.map(m => `
      <div class="search-result" data-path="${escHtml(m.path)}" data-name="${escHtml(m.name)}">
        <div class="search-file">${escHtml(m.name)}:${m.line}</div>
        <div class="search-line">${escHtml(m.text)}</div>
      </div>
    `).join('');

    resultsEl.querySelectorAll('.search-result').forEach(el => {
      el.addEventListener('click', () => openFile(el.dataset.path, el.dataset.name));
    });
  } catch (e) {
    console.warn('[Search] Failed:', e.message);
    resultsEl.innerHTML = '<div class="empty-state">Error en búsqueda</div>';
  }
}

// ── Git Panel ─────────────────────────────────────────────────────
function initGit() {
  fetchGitStatus();
  // Refresh every 15s
  setInterval(fetchGitStatus, 15000);
}

async function fetchGitStatus() {
  if (!window.seal || !window.seal.git || !state.currentFolder) return;
  try {
    const status = await window.seal.git.status(state.currentFolder);
    if (status.error) return;

    // Branch
    const branchEl = document.getElementById('git-branch');
    if (branchEl) branchEl.textContent = `⎇ ${status.branch}`;

    // Status info
    const infoEl = document.getElementById('git-status-info');
    if (infoEl) infoEl.textContent = status.clean ? '✓ Árbol de trabajo limpio' : `${status.files.length} archivo(s) modificado(s)`;

    // Files
    const filesEl = document.getElementById('git-files');
    if (filesEl) {
      filesEl.innerHTML = status.files.map(f => `
        <div class="git-file" data-path="${escHtml(f.path)}" title="${escHtml(f.path)}">
          <span class="git-file-status ${f.status}">${f.status}</span>
          <span class="git-file-name">${escHtml(f.path.split('/').pop())}</span>
        </div>
      `).join('');

      filesEl.querySelectorAll('.git-file').forEach(el => {
        el.addEventListener('click', () => {
          const path = el.dataset.path;
          const fullPath = `${state.currentFolder}/${path}`;
          openFile(fullPath, path.split('/').pop());
        });
      });
    }

    // Log
    const log = await window.seal.git.log(state.currentFolder, 8);
    const logEl = document.getElementById('git-log');
    if (logEl && log && log.entries) {
      logEl.innerHTML = log.entries.map(e =>
        `<div class="git-log-entry"><span class="git-log-hash">${escHtml(e.hash)}</span><span class="git-log-msg">${escHtml(e.message)}</span></div>`
      ).join('');
    }
  } catch {}
}

// ── SOUL Dashboard ─────────────────────────────────────────────────
function initSOUL() {
  document.querySelectorAll('.agent-sel-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.agent-sel-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.soulAgent = btn.dataset.agent;
      fetchSOUL();
    });
  });
  fetchSOUL();
  setInterval(fetchSOUL, 30000);
}

async function fetchSOUL() {
  try {
    const data = await sealRequest(`/api/soul/snapshot?agent=${state.soulAgent}`);
    if (data && data.ocean) updateOceanDisplay(data.ocean);
    if (data && data.drift !== undefined) {
      const dEl = document.getElementById('drift-value');
      if (dEl) dEl.textContent = typeof data.drift === 'number' ? data.drift.toFixed(3) : (data.drift || '--');
    }
    if (data && data.recent_thoughts && data.recent_thoughts[0]) {
      const el = document.getElementById('emotion-value');
      if (el) el.textContent = data.recent_thoughts[0].state || '--';
    }
  } catch {}
}

function updateOceanDisplay(ocean) {
  ['O','C','E','A','N'].forEach(d => {
    const v = ocean[d] || 0;
    const bar = document.getElementById(`bar-${d}`);
    const val = document.getElementById(`val-${d}`);
    if (bar) bar.style.width = `${v * 100}%`;
    if (val) val.textContent = v.toFixed(2);
  });
}

// ── Manager View ───────────────────────────────────────────────────
function initManagerView() {
  document.getElementById('btn-spawn-agent')?.addEventListener('click', spawnAgent);
  updateManagerHealth();
  state.managerInterval = setInterval(updateManagerHealth, 15000);
}

async function updateManagerHealth() {
  // GPU via system IPC (nvidia-smi)
  try {
    if (window.seal && window.seal.system) {
      const gpu = await window.seal.system.gpu();
      const gpuEl = document.getElementById('health-gpu');
      const statusGpu = document.getElementById('status-gpu');
      if (gpu && !gpu.error) {
        if (gpuEl) gpuEl.textContent = `${gpu.temp}°C / ${gpu.util}%`;
        if (statusGpu) statusGpu.textContent = `GPU: ${gpu.temp}°C`;
        // Alert on titlebar if hot
        if (gpu.temp > 80) gpuEl?.classList.add('health-err');
        else gpuEl?.classList.remove('health-err');
      }
    }
  } catch {}

  // SEAL Runtime bridge
  try {
    const health = await sealRequest('/api/health');
    const rtEl = document.getElementById('health-runtime');
    if (rtEl) {
      if (health && !health.error) { rtEl.textContent = '●'; rtEl.className = 'health-ok'; }
      else { rtEl.textContent = '●'; rtEl.className = 'health-err'; }
    }
  } catch {
    const rtEl = document.getElementById('health-runtime');
    if (rtEl) { rtEl.textContent = '○'; rtEl.className = 'health-err'; }
  }

  // Chat server
  try {
    const chatEl = document.getElementById('health-chat');
    if (chatEl) {
      chatEl.textContent = state.chatConnected ? '●' : '○';
      chatEl.className = state.chatConnected ? 'health-ok' : 'health-err';
    }
  } catch {}

  // DB (PostgreSQL via bridge)
  try {
    const db = await sealRequest('/api/soul/snapshot?agent=ADA');
    const dbEl = document.getElementById('health-db');
    if (dbEl) {
      if (db && !db.error) { dbEl.textContent = '●'; dbEl.className = 'health-ok'; }
      else { dbEl.textContent = '○'; dbEl.className = 'health-err'; }
    }
  } catch {
    const dbEl = document.getElementById('health-db');
    if (dbEl) { dbEl.textContent = '○'; dbEl.className = 'health-err'; }
  }

  // Agent indicators in titlebar
  updateAgentIndicators();
}

function updateAgentIndicators() {
  // ADA is always active (this IS ADA's IDE)
  document.querySelector('.agent-dot.ada')?.classList.add('active');
  // JARVIS and DUM based on chat connection (they send messages)
  if (state.chatConnected) {
    document.querySelector('.agent-dot.jarvis')?.classList.add('active');
    document.querySelector('.agent-dot.dum')?.classList.add('active');
  }
}

async function spawnAgent() {
  const prompt = document.getElementById('spawn-prompt')?.value;
  const type = document.getElementById('spawn-type')?.value;
  if (!prompt) return;
  try {
    const result = await sealRequest('/api/agents/spawn', 'POST', {
      name: `agent_${Date.now()}`, prompt, agent_type: type, description: prompt.slice(0, 50),
    });
    if (result) {
      document.getElementById('spawn-prompt').value = '';
      addManagerTask(prompt, 'in-progress');
    }
  } catch {}
}

function addManagerTask(name, status) {
  const list = document.getElementById('manager-tasks');
  if (!list) return;
  const d = document.createElement('div');
  d.className = `manager-task ${status}`;
  const icons = { 'completed': '✓', 'in-progress': '⟳', 'pending': '○' };
  d.innerHTML = `<span class="task-status">${icons[status] || '○'}</span><span class="task-name">${escHtml(name)}</span>`;
  list.appendChild(d);
}

// ── Health Monitor ─────────────────────────────────────────────────
function startHealthMonitor() {
  // Update chat indicator
  const updateChatIndicator = () => {
    const el = document.getElementById('status-chat-indicator');
    if (el) el.textContent = state.chatConnected ? '💬 Online' : '💬 Offline';
  };
  setInterval(updateChatIndicator, 5000);
  updateChatIndicator();

  // GPU status bar updated by updateManagerHealth() every 15s — no duplicate polling
}

// ── Keyboard Shortcuts ─────────────────────────────────────────────
function initKeyboard() {
  document.addEventListener('keydown', e => {
    if (e.key === '`' && e.ctrlKey) { e.preventDefault(); activateBottomTab('terminal'); }
    if (e.key === 'C' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activateBottomTab('chat'); }
    if (e.key === 'U' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('soul'); }
    if (e.key === 'E' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('explorer'); }
    if (e.key === 'F' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('search'); document.getElementById('search-input')?.focus(); }
    if (e.key === 'G' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('git'); fetchGitStatus(); }
    if (e.key === 'M' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('manager'); }
    if (e.key === 'n' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); newFile(); }
    if (e.key === 'Tab' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); cycleTab(1); }
    if (e.key === 'Tab' && e.ctrlKey && e.shiftKey) { e.preventDefault(); cycleTab(-1); }
    if (e.key === 'w' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); if (state.activeFile) closeTab(state.activeFile); }
    if (e.key === 'p' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); if (window._openPalette) window._openPalette(); }
  });
}

function activatePanel(name) {
  document.querySelectorAll('.activity-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.side-panel').forEach(p => p.classList.remove('active'));
  const btn = document.querySelector(`.activity-btn[data-panel="${name}"]`);
  const panel = document.getElementById(`panel-${name}`);
  if (btn) btn.classList.add('active');
  if (panel) panel.classList.add('active');
}

// ── Menu Events ────────────────────────────────────────────────────
function initMenuEvents() {
  if (!window.seal || !window.seal.on) return;
  window.seal.on('toggle-terminal', () => activateBottomTab('terminal'));
  window.seal.on('toggle-chat', () => activateBottomTab('chat'));
  window.seal.on('toggle-soul', () => activatePanel('soul'));
  window.seal.on('toggle-explorer', () => activatePanel('explorer'));
  window.seal.on('soul-snapshot', () => { activatePanel('soul'); fetchSOUL(); });
  window.seal.on('new-file', newFile);
  window.seal.on('open-file', openFileDialog);
  window.seal.on('open-folder', openFolder);
  window.seal.on('open-browser', async () => {
    if (window.seal && window.seal.browser) await window.seal.browser.open('https://www.google.com');
  });
  window.seal.on('open-scratchpad', () => showToast('Scratchpad: próximamente', 'info'));
  window.seal.on('open-settings', () => showToast('Configuración: próximamente', 'info'));
  window.seal.on('emotional-variance', () => { activatePanel('soul'); fetchSOUL(); });
  window.seal.on('save-file', () => saveCurrentFile());
  window.seal.on('save-file-as', saveFileAs);
  window.seal.on('switch-agent', (agent) => {
    state.soulAgent = agent;
    fetchSOUL();
    document.getElementById('status-agent').textContent = `🔭 ${agent}`;
  });
  window.seal.on('run-dream', async () => {
    try { await sealRequest('/api/dream/check?agent=ADA'); } catch {}
  });
}

// ── Skills Panel ──────────────────────────────────────────────────
let allSkills = [];

async function loadSkills() {
  const skillsEl = document.getElementById('skills-list');
  const countEl = document.getElementById('skills-count');
  const searchEl = document.getElementById('skills-search');
  if (!skillsEl) return;

  // Built-in SEAL skills (always available, no filesystem needed)
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

  // Try to load from filesystem (Electron only)
  if (window.seal && window.seal.fs) {
    const SKILLS_DIR = '/home/dadito/.claude/skills';
    try {
      const tree = await window.seal.fs.readDir(SKILLS_DIR);
      if (tree && tree.entries) {
        for (const child of tree.entries) {
          if (!child.isDirectory) continue;
          if (allSkills.some(s => s.dir === child.name)) continue; // skip if already builtin
          const skillPath = `${SKILLS_DIR}/${child.name}/SKILL.md`;
          try {
            const result = await window.seal.fs.readFile(skillPath);
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

  // Sort: SEAL first, then alphabetical
  allSkills.sort((a, b) => {
    if (a.category !== b.category) return a.category === 'SEAL' ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  if (countEl) countEl.textContent = `(${allSkills.length})`;
  renderSkills(allSkills);

  // Search filter
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

  if (skills.length === 0) {
    el.innerHTML = '<div class="empty-state">No se encontraron skills</div>';
    return;
  }

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

  // Click to invoke — send as chat command
  el.querySelectorAll('.skill-item').forEach(item => {
    item.addEventListener('click', () => {
      const name = item.dataset.skill;
      addChatMessage({ from: 'William', message: `/${name}`, timestamp: new Date().toISOString() });
      // Also send via WebSocket if connected
      if (state.chatWs && state.chatWs.readyState === WebSocket.OPEN) {
        state.chatWs.send(JSON.stringify({ action: 'say', message: `/${name}` }));
      }
    });
  });
}

// ── Resize Handles ────────────────────────────────────────────────
function initResizeHandles() {
  // Sidebar resize
  const sidebarHandle = document.getElementById('resize-sidebar');
  const sidebar = document.getElementById('sidebar');
  if (sidebarHandle && sidebar) {
    let startX, startWidth;
    sidebarHandle.addEventListener('mousedown', (e) => {
      startX = e.clientX;
      startWidth = sidebar.offsetWidth;
      sidebarHandle.classList.add('active');
      const onMove = (ev) => {
        const newWidth = Math.max(160, Math.min(500, startWidth + ev.clientX - startX));
        sidebar.style.width = `${newWidth}px`;
      };
      const onUp = () => {
        sidebarHandle.classList.remove('active');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (state.editor) state.editor.layout();
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  // Bottom panel resize
  const bottomHandle = document.getElementById('resize-bottom');
  const chatWrapper = document.getElementById('chat-wrapper');
  if (bottomHandle && chatWrapper) {
    let startY, startHeight;
    bottomHandle.addEventListener('mousedown', (e) => {
      if (chatWrapper.classList.contains('collapsed')) return;
      startY = e.clientY;
      startHeight = chatWrapper.offsetHeight;
      bottomHandle.classList.add('active');
      const onMove = (ev) => {
        const newHeight = Math.max(100, Math.min(600, startHeight - (ev.clientY - startY)));
        chatWrapper.style.height = `${newHeight}px`;
      };
      const onUp = () => {
        bottomHandle.classList.remove('active');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (state.editor) state.editor.layout();
        if (state.terminalFit) setTimeout(() => state.terminalFit.fit(), 50);
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }
}

// ── Command Palette ───────────────────────────────────────────────
function initCommandPalette() {
  const palette = document.getElementById('command-palette');
  const input = document.getElementById('palette-input');
  const results = document.getElementById('palette-results');
  const overlay = palette.querySelector('.palette-overlay');
  let selectedIdx = -1;

  function open() {
    palette.classList.remove('hidden');
    input.value = '';
    input.focus();
    selectedIdx = -1;
    renderPaletteResults('');
  }

  function close() {
    palette.classList.add('hidden');
    input.value = '';
  }

  overlay.addEventListener('click', close);

  input.addEventListener('input', () => {
    selectedIdx = -1;
    renderPaletteResults(input.value.trim().toLowerCase());
  });

  input.addEventListener('keydown', (e) => {
    const items = results.querySelectorAll('.palette-item');
    if (e.key === 'Escape') { close(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); selectedIdx = Math.min(selectedIdx + 1, items.length - 1); updateSelection(items); }
    if (e.key === 'ArrowUp') { e.preventDefault(); selectedIdx = Math.max(selectedIdx - 1, 0); updateSelection(items); }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (selectedIdx >= 0 && items[selectedIdx]) items[selectedIdx].click();
      else if (items.length > 0) items[0].click();
    }
  });

  function updateSelection(items) {
    items.forEach((it, i) => it.classList.toggle('selected', i === selectedIdx));
    if (items[selectedIdx]) items[selectedIdx].scrollIntoView({ block: 'nearest' });
  }

  function renderPaletteResults(query) {
    const entries = [];

    // Open files
    state.openFiles.forEach(f => {
      entries.push({ icon: getFileIcon(f.name), label: f.name, hint: f.path, category: 'Archivos abiertos', action: () => { setActiveFile(f); close(); } });
    });

    // Skills
    (typeof allSkills !== 'undefined' ? allSkills : []).forEach(s => {
      entries.push({ icon: s.icon, label: `/${s.name}`, hint: s.description, category: 'Skills', action: () => {
        addChatMessage({ from: 'William', message: `/${s.name}`, timestamp: new Date().toISOString() });
        close();
      }});
    });

    // Actions
    const actions = [
      { icon: '📁', label: 'Abrir carpeta', hint: 'Ctrl+Shift+O', action: () => { openFolder(); close(); } },
      { icon: '⚡', label: 'Terminal', hint: 'Ctrl+`', action: () => { activateBottomTab('terminal'); close(); } },
      { icon: '💬', label: 'Chat del Equipo', hint: 'Ctrl+Shift+C', action: () => { activateBottomTab('chat'); close(); } },
      { icon: '🧠', label: 'SOUL Dashboard', hint: 'Ctrl+Shift+S', action: () => { activatePanel('soul'); close(); } },
      { icon: '🎯', label: 'Manager View', hint: 'Ctrl+Shift+M', action: () => { activatePanel('manager'); close(); } },
      { icon: '🔍', label: 'Buscar en archivos', hint: 'Ctrl+Shift+F', action: () => { activatePanel('search'); close(); } },
    ];
    actions.forEach(a => entries.push({ ...a, category: 'Acciones' }));

    // Filter
    const filtered = query ? entries.filter(e => e.label.toLowerCase().includes(query) || (e.hint || '').toLowerCase().includes(query)) : entries;

    // Render
    let html = '';
    let lastCat = '';
    for (const e of filtered.slice(0, 20)) {
      if (e.category !== lastCat) { html += `<div class="palette-category">${escHtml(e.category)}</div>`; lastCat = e.category; }
      html += `<div class="palette-item"><span class="palette-icon">${e.icon}</span><span class="palette-label">${escHtml(e.label)}</span><span class="palette-hint">${escHtml(e.hint || '')}</span></div>`;
    }
    results.innerHTML = html || '<div class="palette-item"><span class="palette-label" style="color:var(--seal-text-dim)">Sin resultados</span></div>';

    // Bind clicks
    results.querySelectorAll('.palette-item').forEach((el, i) => {
      const entry = filtered[i];
      if (entry) el.addEventListener('click', entry.action);
    });
  }

  window._openPalette = open;
}

// ── Bottom Tab Helpers ────────────────────────────────────────────
function activateBottomTab(name) {
  const wrapper = document.getElementById('chat-wrapper');
  const toggleBtn = document.getElementById('btn-toggle-bottom');
  // Find the tab
  const tab = document.querySelector(`.bottom-tab[data-bottom="${name}"]`);
  if (tab) {
    document.querySelectorAll('.bottom-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.bottom-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    const panel = document.getElementById(`${name}-bottom-panel`);
    if (panel) panel.classList.add('active');
  }
  // Expand
  if (document.getElementById('chat-wrapper').classList.contains('collapsed')) expandBottomPanel();
  // Create terminal if needed
  if (name === 'terminal') ensureTerminal();
  if (name === 'terminal' && state.terminalFit) setTimeout(() => state.terminalFit.fit(), 50);
  if (name === 'chat') {
    const input = document.getElementById('chat-input-main');
    if (input) setTimeout(() => input.focus(), 50);
  }
}

// ── Context Menu ──────────────────────────────────────────────────
function showContextMenu(x, y, items) {
  const menu = document.getElementById('context-menu');
  const container = document.getElementById('context-menu-items');
  if (!menu || !container) return;

  container.innerHTML = items.map(item =>
    item.separator ? '<div class="ctx-sep"></div>' :
    `<div class="ctx-item" data-action="${escHtml(item.id || '')}">${escHtml(item.label)}</div>`
  ).join('');

  // Position
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  menu.classList.remove('hidden');

  // Bind actions
  container.querySelectorAll('.ctx-item').forEach((el, i) => {
    const item = items.filter(it => !it.separator)[i];
    if (item && item.action) el.addEventListener('click', () => { item.action(); hideContextMenu(); });
  });

  // Close on click outside
  setTimeout(() => {
    const closer = (e) => {
      if (!menu.contains(e.target)) { hideContextMenu(); document.removeEventListener('click', closer); }
    };
    document.addEventListener('click', closer);
  }, 10);
}

function hideContextMenu() {
  const menu = document.getElementById('context-menu');
  if (menu) menu.classList.add('hidden');
}

function initContextMenus() {
  // Tab right-click
  document.getElementById('tabs')?.addEventListener('contextmenu', (e) => {
    const tab = e.target.closest('.tab');
    if (!tab) return;
    e.preventDefault();
    const path = tab.dataset.path;
    const file = state.openFiles.find(f => f.path === path);
    if (!file) return;
    showContextMenu(e.clientX, e.clientY, [
      { label: 'Cerrar', action: () => closeTab(file) },
      { label: 'Cerrar otros', action: () => {
        state.openFiles.filter(f => f !== file).forEach(f => closeTab(f));
      }},
      { label: 'Cerrar todos', action: () => {
        [...state.openFiles].forEach(f => closeTab(f));
      }},
      { separator: true },
      { label: 'Copiar ruta', action: () => {
        navigator.clipboard?.writeText(path);
        showToast('Ruta copiada', 'info', 1500);
      }},
    ]);
  });

  // File tree right-click
  document.getElementById('file-tree')?.addEventListener('contextmenu', (e) => {
    const item = e.target.closest('.tree-item');
    if (!item) return;
    e.preventDefault();
    // Get the path from the tree item data
    const nameEl = item.querySelector('.tree-name');
    const name = nameEl ? nameEl.textContent : '';
    showContextMenu(e.clientX, e.clientY, [
      { label: 'Abrir', action: () => item.click() },
      { label: 'Copiar nombre', action: () => {
        navigator.clipboard?.writeText(name);
        showToast('Nombre copiado', 'info', 1500);
      }},
    ]);
  });
}

// ── Toast Notifications ───────────────────────────────────────────
function showToast(message, type = 'info', duration = 3000) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const icons = { success: '✓', error: '✗', info: 'ℹ', warning: '⚠' };
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span class="toast-icon">${icons[type] || ''}</span><span>${escHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('fade-out');
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ── Helpers ────────────────────────────────────────────────────────
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#x27;'); }
function findTabByPath(path) { return Array.from(document.querySelectorAll('.tab')).find(t => t.dataset.path === path); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function sealRequest(endpoint, method, body) {
  if (window.seal && window.seal.request) {
    return await window.seal.request(endpoint, method || 'GET', body);
  }
  // Fallback: direct fetch to bridge
  const opts = { method: method || 'GET', headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BRIDGE_URL}${endpoint}`, opts);
  return await res.json();
}

// ── Start ──────────────────────────────────────────────────────────
boot();
