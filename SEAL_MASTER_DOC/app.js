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
  terminal: null,
  terminalId: null,
  chatWs: null,
  chatConnected: false,
  soulAgent: 'ADA',
  managerInterval: null,
};

const BRIDGE_URL = 'http://localhost:8766';
const CHAT_WS = 'ws://localhost:8765/ws';

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

  status.textContent = 'Conectando SOUL...';
  let soul = null;
  try {
    soul = await sealRequest('/api/soul/snapshot?agent=ADA');
    status.textContent = 'SOUL conectado';
  } catch { status.textContent = 'SOUL offline — modo degradado'; }
  await sleep(300);

  status.textContent = 'Equipo presente...';
  await sleep(200);
  if (soul && soul.ocean) {
    addMsg('ADA', `Buenos días William. OCEAN estable. La casa te espera.`, 'ada');
    await sleep(400);
    addMsg('JARVIS', `Arquitectura lista. Sin drift. Todo en orden.`, 'jarvis');
    await sleep(400);
    addMsg('DUM', `GPU monitoreado. Vigilando.`, 'dum');
  } else {
    addMsg('ADA', `Buenos días William. Modo offline — SOUL reconectará.`, 'ada');
    await sleep(300);
    addMsg('JARVIS', `Equipo presente.`, 'jarvis');
  }
  await sleep(600);

  status.textContent = '¡Listo!';
  await sleep(300);
  document.getElementById('boot-screen').classList.add('fade-out');
  await sleep(500);
  document.getElementById('boot-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');

  // Init all components
  initActivityBar();
  await initMonaco();
  initTerminal();
  initChat();
  initFileTree();
  initSOUL();
  initManagerView();
  initKeyboard();
  initMenuEvents();
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
    state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, async () => {
      if (state.activeFile && window.seal) {
        const content = state.editor.getValue();
        await window.seal.fs.writeFile(state.activeFile.path, content);
        const tab = document.querySelector(`.tab[data-path="${state.activeFile.path}"]`);
        if (tab) tab.classList.remove('modified');
        document.getElementById('current-file').textContent = state.activeFile.name;
      }
    });

    // Track modifications
    state.editor.onDidChangeModelContent(() => {
      if (state.activeFile) {
        const tab = document.querySelector(`.tab[data-path="${state.activeFile.path}"]`);
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

// ── Terminal ───────────────────────────────────────────────────────
function initTerminal() {
  document.getElementById('btn-toggle-term').addEventListener('click', toggleTerminal);
  document.getElementById('btn-new-term').addEventListener('click', createTerminal);
}

function toggleTerminal() {
  const w = document.getElementById('terminal-wrapper');
  if (w.classList.contains('collapsed')) {
    w.classList.remove('collapsed');
    w.classList.add('expanded');
    if (!state.terminal) createTerminal();
    else state.terminal.focus();
  } else {
    w.classList.remove('expanded');
    w.classList.add('collapsed');
  }
}

async function createTerminal() {
  const container = document.getElementById('xterm-container');
  container.innerHTML = '';

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
  term.open(container);
  setTimeout(() => fitAddon.fit(), 100);
  state.terminal = term;

  if (window.seal && window.seal.terminal) {
    const result = await window.seal.terminal.create({
      cols: term.cols, rows: term.rows, cwd: state.currentFolder || undefined,
    });
    if (result && result.id) {
      state.terminalId = result.id;
      term.onData(data => window.seal.terminal.write(result.id, data));
      window.seal.terminal.onData(({ id, data }) => {
        if (id === result.id) term.write(data);
      });
      window.seal.terminal.onExit(({ id }) => {
        if (id === result.id) term.writeln('\r\n\x1b[33m[Terminal cerrado]\x1b[0m');
      });
    }
  } else {
    term.writeln('\x1b[36m  ╔═══════════════════════════════════════╗\x1b[0m');
    term.writeln('\x1b[36m  ║     🔭 Código SEAL — Terminal        ║\x1b[0m');
    term.writeln('\x1b[36m  ╚═══════════════════════════════════════╝\x1b[0m');
    term.writeln('');
    term.writeln('\x1b[33m  Terminal disponible cuando se ejecute desde Electron.\x1b[0m');
    term.writeln('\x1b[90m  Ejecutar: cd ~/IA/proyecto-seal/codigo-seal && npx electron .\x1b[0m');
  }

  new ResizeObserver(() => { try { fitAddon.fit(); } catch {} }).observe(container);
}

// ── Chat ───────────────────────────────────────────────────────────
function initChat() {
  document.getElementById('btn-send').addEventListener('click', sendChat);
  document.getElementById('chat-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) sendChat();
  });
  connectChat();
}

function connectChat() {
  try {
    state.chatWs = new WebSocket(CHAT_WS);
    state.chatWs.onopen = () => {
      state.chatConnected = true;
      document.getElementById('chat-dot').className = 'chat-dot connected';
    };
    state.chatWs.onclose = () => {
      state.chatConnected = false;
      document.getElementById('chat-dot').className = 'chat-dot disconnected';
      setTimeout(connectChat, 3000);
    };
    state.chatWs.onerror = () => state.chatWs.close();
    state.chatWs.onmessage = e => {
      try { addChatMessage(JSON.parse(e.data)); } catch {}
    };
  } catch {
    document.getElementById('chat-dot').className = 'chat-dot disconnected';
    setTimeout(connectChat, 3000);
  }
}

function addChatMessage(msg) {
  const c = document.getElementById('chat-messages');
  const d = document.createElement('div');
  d.className = 'chat-msg';
  const from = (msg.from || 'SYS').toLowerCase();
  d.innerHTML = `<span class="chat-from ${from}">${escHtml(msg.from || 'SYS')}</span> <span class="chat-text">${escHtml((msg.message || '').slice(0, 500))}</span>`;
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
}

function sendChat() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  if (state.chatWs && state.chatWs.readyState === WebSocket.OPEN) {
    state.chatWs.send(JSON.stringify({ action: 'say', message: text }));
  }
  input.value = '';
}

// ── File Explorer ──────────────────────────────────────────────────
function initFileTree() {
  document.getElementById('btn-open-folder').addEventListener('click', openFolder);
}

async function openFolder() {
  const path = state.currentFolder || '/home/dadito/IA/proyecto-seal';
  await loadDirectory(path);
  state.currentFolder = path;
  document.getElementById('explorer-empty').classList.add('hidden');
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
        item.addEventListener('click', async () => {
          const next = item.nextElementSibling;
          if (expanded && next && next.className === 'tree-children') {
            next.remove(); expanded = false;
            item.querySelector('.tree-icon').textContent = '📁';
          } else {
            const ch = document.createElement('div');
            ch.className = 'tree-children';
            item.after(ch);
            await loadDirectory(entry.path, ch, depth + 1);
            expanded = true;
            item.querySelector('.tree-icon').textContent = '📂';
          }
        });
      } else {
        item.addEventListener('click', () => openFile(entry.path, entry.name));
        item.addEventListener('dblclick', () => openFile(entry.path, entry.name));
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
  const tab = document.querySelector(`.tab[data-path="${file.path}"]`);
  if (tab) tab.classList.add('active');
  openFileInEditor(file);
}

function closeTab(file) {
  state.openFiles = state.openFiles.filter(f => f.path !== file.path);
  const tab = document.querySelector(`.tab[data-path="${file.path}"]`);
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
  try {
    // GPU
    const gpuEl = document.getElementById('health-gpu');
    if (gpuEl && window.seal) {
      const info = await window.seal.request('/api/health', 'GET');
      if (info) gpuEl.textContent = 'Online';
    }
  } catch {}

  // Agent status indicators in titlebar
  try {
    const data = await sealRequest('/api/agents');
    if (data) {
      document.getElementById('health-runtime')?.classList.add('health-ok');
    }
  } catch {}
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
  setInterval(async () => {
    // GPU status in statusbar
    try {
      if (window.seal) {
        const gpuEl = document.getElementById('status-gpu');
        // Will work when running in Electron with access to nvidia-smi
      }
    } catch {}
  }, 10000);
}

// ── Keyboard Shortcuts ─────────────────────────────────────────────
function initKeyboard() {
  document.addEventListener('keydown', e => {
    if (e.key === '`' && e.ctrlKey) { e.preventDefault(); toggleTerminal(); }
    if (e.key === 'C' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('chat'); }
    if (e.key === 'S' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('soul'); }
    if (e.key === 'E' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('explorer'); }
    if (e.key === 'M' && e.ctrlKey && e.shiftKey) { e.preventDefault(); activatePanel('manager'); }
    if (e.key === 'n' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); /* new file */ }
    if (e.key === 'p' && e.ctrlKey && !e.shiftKey) { e.preventDefault(); /* command palette */ }
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
  window.seal.on('toggle-terminal', toggleTerminal);
  window.seal.on('toggle-chat', () => activatePanel('chat'));
  window.seal.on('toggle-soul', () => activatePanel('soul'));
  window.seal.on('toggle-explorer', () => activatePanel('explorer'));
  window.seal.on('soul-snapshot', () => { activatePanel('soul'); fetchSOUL(); });
  window.seal.on('open-folder', openFolder);
  window.seal.on('save-file', async () => {
    if (state.editor && state.activeFile && window.seal) {
      await window.seal.fs.writeFile(state.activeFile.path, state.editor.getValue());
    }
  });
  window.seal.on('switch-agent', (agent) => {
    state.soulAgent = agent;
    fetchSOUL();
    document.getElementById('status-agent').textContent = `🔭 ${agent}`;
  });
  window.seal.on('run-dream', async () => {
    try { await sealRequest('/api/dream/check?agent=ADA'); } catch {}
  });
}

// ── Helpers ────────────────────────────────────────────────────────
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
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
