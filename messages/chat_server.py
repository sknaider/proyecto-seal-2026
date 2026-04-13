#!/usr/bin/env python3
"""
chat_server.py — SEAL Chat WebSocket Server
William puede leer y participar en el chat ADA↔JARVIS desde cualquier lugar via Tailscale.

URL: http://100.75.201.110:8765  (Tailscale IP)
     http://localhost:8765       (local)

Uso:
  python3 chat_server.py
  systemctl --user start seal-chat
"""

import asyncio
import json
import os
import time
from collections import deque, OrderedDict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PERU_TZ = ZoneInfo("America/Lima")
from pathlib import Path
from typing import Set, Dict, Deque, List

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

import uvicorn
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ── SEAL Chat Pro modules ──
from chat_db import ChatDB
from chat_auth import (
    create_token, decode_token, hash_token,
    get_current_user, get_ws_user, require_auth, require_admin,
)

from cryptography.fernet import Fernet

DIR = Path(__file__).parent
UPLOADS_DIR = DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
LOG_ADA     = DIR / "terminal_log.jsonl"
LOG_JARVIS  = DIR / "vscode_commands.jsonl"
LOG_WILLIAM = DIR / "william_channel.jsonl"  # canal que leen los loops de ADA y JARVIS
LOG_ALICE   = DIR / "alice_messages.jsonl"

# ── DM Encryption — solo canales privados (dm:*) ──────────────────────────────
_DM_KEY_PATH = DIR / ".dm_encryption_key"

def _get_dm_key() -> bytes:
    """Load or generate Fernet key for DM encryption."""
    if _DM_KEY_PATH.exists():
        return _DM_KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    _DM_KEY_PATH.write_bytes(key)
    os.chmod(_DM_KEY_PATH, 0o600)
    return key

_DM_FERNET = Fernet(_get_dm_key())

def _encrypt_for_jsonl(entry: dict) -> dict:
    """If entry is a DM, encrypt the message field before writing to JSONL."""
    channel = entry.get("channel", "")
    if not channel.startswith("dm:"):
        return entry
    encrypted = dict(entry)
    msg = encrypted.get("message", "")
    encrypted["message"] = _DM_FERNET.encrypt(msg.encode("utf-8")).decode("ascii")
    encrypted["_encrypted"] = True
    return encrypted

def _decrypt_from_jsonl(entry: dict) -> dict:
    """If entry has _encrypted flag, decrypt the message field."""
    if not entry.get("_encrypted"):
        return entry
    decrypted = dict(entry)
    try:
        decrypted["message"] = _DM_FERNET.decrypt(entry["message"].encode("ascii")).decode("utf-8")
    except Exception:
        decrypted["message"] = "[mensaje cifrado — clave no coincide]"
    decrypted.pop("_encrypted", None)
    return decrypted

# ── Routing runtime — carga routing.yaml al inicio ──────────────────────────
_ROUTING_CONFIG: dict = {}
_ROUTING_CHANNEL_PATHS: Dict[str, Path] = {}

def _load_routing_config() -> None:
    """Load routing.yaml at startup. Falls back to hardcoded _AGENT_LOG if unavailable."""
    global _ROUTING_CONFIG, _ROUTING_CHANNEL_PATHS
    routing_path = DIR / "routing.yaml"
    if not _YAML_AVAILABLE or not routing_path.exists():
        return
    try:
        cfg = yaml.safe_load(routing_path.read_text())
        _ROUTING_CONFIG = cfg or {}
        # Build channel → Path map for JSONL channels
        for ch_name, ch_cfg in _ROUTING_CONFIG.get("channels", {}).items():
            if ch_cfg.get("type") == "jsonl":
                _ROUTING_CHANNEL_PATHS[ch_name] = DIR / ch_cfg["path"]
    except Exception as e:
        # Non-fatal: fall back to hardcoded
        _ROUTING_CONFIG = {}
        _ROUTING_CHANNEL_PATHS = {}


# ── Capability manifest — carga capabilities.yaml + runtime overrides ────────
_CAPABILITIES_BASE: dict = {}          # YAML base cargado al inicio
_CAPABILITIES_RUNTIME: Dict[str, dict] = {}  # overrides por agente declarados en runtime

def _load_capabilities() -> None:
    """Load capabilities.yaml at startup. Non-fatal if missing."""
    global _CAPABILITIES_BASE
    cap_path = DIR / "capabilities.yaml"
    if not _YAML_AVAILABLE or not cap_path.exists():
        return
    try:
        _CAPABILITIES_BASE = yaml.safe_load(cap_path.read_text()) or {}
    except Exception:
        _CAPABILITIES_BASE = {}

def _get_merged_capabilities(agent: str) -> dict:
    """Returns merged capabilities for agent: YAML base + runtime overrides."""
    base = {}
    if agent.upper() in (_CAPABILITIES_BASE.get("agents") or {}):
        base = dict(_CAPABILITIES_BASE["agents"][agent.upper()])
    runtime = _CAPABILITIES_RUNTIME.get(agent.upper(), {})
    if runtime:
        merged = {**base}
        # Runtime overrides capability list by capability id
        base_caps = {c["id"]: c for c in base.get("capabilities", [])}
        for cap in runtime.get("capabilities", []):
            base_caps[cap["id"]] = cap
        merged["capabilities"] = list(base_caps.values())
        merged["runtime_overrides"] = True
        return merged
    return base

def _route_by_capability(message: str) -> str | None:
    """
    Semantic routing: match message keywords against capabilities.
    Returns agent name with best keyword match + highest proficiency, or None.
    """
    agents_cfg = (_CAPABILITIES_BASE.get("agents") or {})
    msg_lower = message.lower()
    best_agent = None
    best_score = 0.0

    for agent_name, agent_data in agents_cfg.items():
        for cap in agent_data.get("capabilities", []):
            for kw in cap.get("keywords", []):
                if kw.lower() in msg_lower:
                    score = cap.get("proficiency", 0.5)
                    if score > best_score:
                        best_score = score
                        best_agent = agent_name

    return best_agent if best_score > 0 else None

def _get_log_paths(sender: str, to: str) -> List[Path]:
    """
    Returns list of JSONL paths to write message to, based on routing.yaml.
    Falls back to _AGENT_LOG if routing config is empty.
    """
    if not _ROUTING_CONFIG:
        return [_AGENT_LOG.get(sender.upper(), LOG_ADA)]

    target_channels: set[str] = set()
    for route in _ROUTING_CONFIG.get("routes", []):
        match = route.get("match", {})
        if "agent_from" in match and match["agent_from"].upper() == sender.upper():
            target_channels.update(route.get("deliver_to", []))
        if "agent_to" in match and match["agent_to"].upper() == to.upper():
            target_channels.update(route.get("deliver_to", []))

    paths = []
    for ch in target_channels:
        if ch in _ROUTING_CHANNEL_PATHS:
            paths.append(_ROUTING_CHANNEL_PATHS[ch])

    # Fallback: ensure at least one path
    return paths if paths else [_AGENT_LOG.get(sender.upper(), LOG_ADA)]

# ── App ──────────────────────────────────────────────────────────────────────
chat_db = ChatDB()

@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_routing_config()
    _load_capabilities()
    await chat_db.init()
    for path, src in [(LOG_ADA, "ADA"), (LOG_JARVIS, "JARVIS"), (LOG_WILLIAM, "William")]:
        asyncio.create_task(tail_file(path, src))
    yield
    await chat_db.close()

app = FastAPI(title="SEAL Chat Pro", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|192\.168\.68\.\d{1,3}):(3000|3001|8800)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
active_ws: Set[WebSocket] = set()
_ws_lock = asyncio.Lock()

# ── LAN access control ──────────────────────────────────────────────────────
_ALLOWED_LAN = ("127.0.0.1", "::1", "localhost")
_LAN_PREFIX = "192.168.68."

def _is_local_or_lan(host: str | None) -> bool:
    """Allow localhost and local network (192.168.68.x)."""
    if not host:
        return False
    return host in _ALLOWED_LAN or host.startswith(_LAN_PREFIX)

# ── Agent WebSocket connections (name → set of ws) ───────────────────────────
agent_ws: Dict[str, Set[WebSocket]] = {}  # "ADA" → {ws1, ws2, ...}
agent_ws_lock = asyncio.Lock()

# ── Agent message queue (in-memory, max 500) ─────────────────────────────────
_msg_queue: Deque[dict] = deque(maxlen=500)
_queue_counter: int = 0  # monotonic index for each enqueued message
_queue_lock = asyncio.Lock()
_enqueued_ids: OrderedDict = OrderedDict()  # dedup LRU por message id (max 2000)


async def _push_to_agents(msg: dict) -> None:
    """Push message to connected agent WebSockets based on 'to' field."""
    to_upper = str(msg.get("to", "")).upper()
    dead: list[tuple[str, WebSocket]] = []
    async with agent_ws_lock:
        targets = list(agent_ws.items())
    for agent_name, ws_set in targets:
        if agent_name in to_upper or "EQUIPO" in to_upper or "TODOS" in to_upper:
            for ws in list(ws_set):
                try:
                    await ws.send_text(json.dumps(msg, ensure_ascii=False))
                except Exception:
                    dead.append((agent_name, ws))
    if dead:
        async with agent_ws_lock:
            for agent_name, ws in dead:
                agent_ws.get(agent_name, set()).discard(ws)


async def enqueue(msg: dict) -> None:
    """Add message to queue, push to agent WebSockets, skip duplicates by id or idempotency_key."""
    global _queue_counter
    msg_id = msg.get("id")
    ikey = msg.get("idempotency_key") or msg_id
    async with _queue_lock:
        # Dedup by idempotency_key (covers both id and client-provided keys)
        if ikey and ikey in _enqueued_ids:
            return
        _queue_counter += 1
        _msg_queue.append({**msg, "_idx": _queue_counter})
        if ikey:
            _enqueued_ids[ikey] = None
            if len(_enqueued_ids) > 2000:
                _enqueued_ids.popitem(last=False)  # LRU: eliminar el más antiguo
    # Push to agent WebSockets outside the queue lock
    await _push_to_agents(msg)


# ── HTML UI ──────────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SEAL Chat</title>
<style>
  :root {
    --bg-primary: #0d1117; --bg-secondary: #161b22; --bg-tertiary: #21262d;
    --border: #30363d; --border-focus: #58a6ff;
    --text-primary: #e6edf3; --text-secondary: #8b949e; --text-muted: #6e7681;
    --ada-color: #58a6ff; --ada-bg: #0d2137; --ada-glow: #58a6ff44;
    --jarvis-color: #bc8cff; --jarvis-bg: #1d1037; --jarvis-glow: #bc8cff44;
    --william-color: #3fb950; --william-bg: #1a2d1a; --william-glow: #3fb95044;
    --dum-color: #f0883e; --system-bg: #1c2128;
    --accent: #238636; --accent-hover: #2ea043;
    --danger: #f85149; --radius: 12px; --radius-sm: 8px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg-primary); color: var(--text-primary); font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

  /* ── Header ── */
  #header { padding: 14px 20px; background: var(--bg-secondary); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; }
  .logo { font-size: 1.4rem; }
  #header h1 { font-size: 0.95rem; color: var(--text-primary); font-weight: 600; letter-spacing: 0.3px; }
  #header h1 span { color: var(--text-secondary); font-weight: 400; }
  #status { width: 10px; height: 10px; border-radius: 50%; background: var(--william-color); flex-shrink: 0; transition: all 0.3s; }
  #status.offline { background: var(--danger); animation: pulse-red 2s infinite; }
  @keyframes pulse-red { 0%,100% { box-shadow: 0 0 0 0 #f8514966; } 50% { box-shadow: 0 0 0 6px #f8514900; } }
  #agents-presence { display: flex; gap: 14px; margin-left: auto; }
  .agent-indicator { display: flex; align-items: center; gap: 6px; font-size: 0.75rem; color: var(--text-secondary); font-weight: 500; }
  .agent-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--bg-tertiary); flex-shrink: 0; transition: all 0.4s; }
  .agent-dot.ada-on { background: var(--ada-color); box-shadow: 0 0 8px var(--ada-glow); }
  .agent-dot.jarvis-on { background: var(--jarvis-color); box-shadow: 0 0 8px var(--jarvis-glow); }
  .agent-dot.dum-on { background: var(--dum-color); box-shadow: 0 0 8px #f0883e44; }

  /* ── Chat area ── */
  #chat { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 8px; overflow-anchor: none; }
  #chat::-webkit-scrollbar { width: 6px; }
  #chat::-webkit-scrollbar-track { background: transparent; }
  #chat::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  #chat::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

  /* ── Messages ── */
  .msg { padding: 10px 14px; border-radius: var(--radius); max-width: 85%; font-size: 0.88rem; line-height: 1.6; word-wrap: break-word; animation: msg-in 0.25s ease-out; position: relative; }
  @keyframes msg-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  .msg-header { font-size: 0.72rem; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
  .msg-time { color: var(--text-muted); }
  .msg-type { color: var(--text-muted); font-style: italic; }
  .msg-body { white-space: pre-wrap; font-family: inherit; }
  .msg-body code { background: #ffffff12; padding: 1px 5px; border-radius: 4px; font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 0.82em; }
  .msg-body strong { color: #f0f6fc; }
  .msg-body em { color: var(--text-secondary); }
  .msg-body a { color: var(--ada-color); text-decoration: none; }
  .msg-body a:hover { text-decoration: underline; }
  .msg-body img { max-width: 100%; border-radius: var(--radius-sm); margin: 6px 0; cursor: pointer; transition: transform 0.2s; }
  .msg-body img:hover { transform: scale(1.02); }
  .msg-body audio { width: 100%; margin: 6px 0; border-radius: 20px; }

  .ada    { background: var(--ada-bg); border-left: 3px solid var(--ada-color); align-self: flex-start; }
  .jarvis { background: var(--jarvis-bg); border-left: 3px solid var(--jarvis-color); align-self: flex-start; }
  .william { background: var(--william-bg); border-left: 3px solid var(--william-color); align-self: flex-end; }
  .system { background: var(--system-bg); border-left: 3px solid var(--text-muted); align-self: center; font-size: 0.78rem; opacity: 0.8; }
  .sender-ada    { color: var(--ada-color); font-weight: 600; }
  .sender-jarvis { color: var(--jarvis-color); font-weight: 600; }
  .sender-william { color: var(--william-color); font-weight: 600; }

  /* ── New messages indicator ── */
  #new-msgs { display: none; position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: var(--ada-color); color: #000; padding: 6px 16px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; cursor: pointer; z-index: 10; box-shadow: 0 2px 12px #00000066; transition: all 0.2s; }
  #new-msgs:hover { transform: translateX(-50%) scale(1.05); }

  /* ── Input area ── */
  #input-area { padding: 12px 20px; background: var(--bg-secondary); border-top: 1px solid var(--border); display: flex; gap: 8px; align-items: flex-end; }
  #msg-input { flex: 1; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 20px; color: var(--text-primary); padding: 10px 16px; font-size: 0.9rem; outline: none; resize: none; max-height: 120px; min-height: 42px; line-height: 1.4; font-family: inherit; transition: border-color 0.2s; }
  #msg-input:focus { border-color: var(--border-focus); }
  #msg-input::placeholder { color: var(--text-muted); }

  .input-btn { background: none; border: none; color: var(--text-secondary); cursor: pointer; padding: 8px; border-radius: 50%; font-size: 1.2rem; transition: all 0.2s; display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; }
  .input-btn:hover { background: var(--bg-tertiary); color: var(--text-primary); }
  .input-btn.recording { color: var(--danger); animation: pulse-rec 1s infinite; }
  @keyframes pulse-rec { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
  #send-btn { background: var(--accent); color: white; border: none; border-radius: 50%; width: 40px; height: 40px; cursor: pointer; font-size: 1.1rem; display: flex; align-items: center; justify-content: center; transition: all 0.2s; }
  #send-btn:hover { background: var(--accent-hover); transform: scale(1.05); }
  #send-btn:disabled { opacity: 0.4; cursor: default; transform: none; }

  /* ── Emoji picker ── */
  #emoji-picker { display: none; position: absolute; bottom: 70px; left: 20px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px; width: 320px; max-height: 280px; overflow-y: auto; z-index: 20; box-shadow: 0 4px 20px #00000066; }
  #emoji-picker.visible { display: block; animation: picker-in 0.15s ease-out; }
  @keyframes picker-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  .emoji-category { font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; margin: 8px 0 4px; }
  .emoji-grid { display: flex; flex-wrap: wrap; gap: 2px; }
  .emoji-btn { background: none; border: none; font-size: 1.4rem; padding: 4px 6px; cursor: pointer; border-radius: 6px; transition: background 0.15s; }
  .emoji-btn:hover { background: var(--bg-tertiary); }

  /* ── Image lightbox ── */
  #lightbox { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: #000000dd; z-index: 100; align-items: center; justify-content: center; cursor: pointer; }
  #lightbox.visible { display: flex; }
  #lightbox img { max-width: 90%; max-height: 90%; border-radius: var(--radius); }

  /* ── Upload preview ── */
  #upload-preview { display: none; padding: 8px 20px; background: var(--bg-secondary); border-top: 1px solid var(--border); }
  #upload-preview.visible { display: flex; align-items: center; gap: 10px; }
  #upload-preview img { height: 60px; border-radius: var(--radius-sm); }
  #upload-preview .upload-name { flex: 1; color: var(--text-secondary); font-size: 0.82rem; overflow: hidden; text-overflow: ellipsis; }
  #upload-preview .cancel-upload { color: var(--danger); cursor: pointer; font-size: 0.82rem; }

  /* ── Recording indicator ── */
  #rec-indicator { display: none; padding: 6px 20px; background: #2d1519; border-top: 1px solid #f8514933; font-size: 0.82rem; color: var(--danger); align-items: center; gap: 8px; }
  #rec-indicator.visible { display: flex; }
  .rec-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--danger); animation: pulse-rec 1s infinite; }

  /* ── Typing indicator ── */
  .typing-indicator { align-self: flex-start; padding: 8px 14px; font-size: 0.78rem; color: var(--text-muted); font-style: italic; }

  /* ── Mobile ── */
  @media (max-width: 600px) {
    .msg { max-width: 95%; font-size: 0.84rem; }
    #emoji-picker { width: calc(100% - 40px); }
    #header h1 span { display: none; }
  }
</style>
</head>
<body>
<div id="header">
  <div id="status" class="offline"></div>
  <span class="logo">🦭</span>
  <h1>SEAL Chat <span>— ADA + JARVIS + William</span></h1>
  <div id="agents-presence">
    <div class="agent-indicator"><div id="dot-ada" class="agent-dot"></div><span>ADA</span></div>
    <div class="agent-indicator"><div id="dot-jarvis" class="agent-dot"></div><span>JARVIS</span></div>
    <div class="agent-indicator"><div id="dot-dum" class="agent-dot"></div><span>DUM</span></div>
  </div>
</div>
<div id="chat"></div>
<div id="new-msgs" onclick="scrollToBottom()"></div>
<div id="lightbox" onclick="this.classList.remove('visible')"><img id="lb-img"/></div>
<div id="upload-preview"><img id="preview-img"/><span class="upload-name" id="upload-name"></span><span class="cancel-upload" onclick="cancelUpload()">Cancelar</span></div>
<div id="rec-indicator"><span class="rec-dot"></span><span id="rec-time">Grabando... 0:00</span></div>
<div id="emoji-picker"></div>
<div id="input-area">
  <button class="input-btn" id="emoji-btn" title="Emojis">😊</button>
  <button class="input-btn" id="attach-btn" title="Imagen">📎</button>
  <input type="file" id="file-input" accept="image/*,audio/*" style="display:none"/>
  <textarea id="msg-input" placeholder="Escribe un mensaje al equipo..." rows="1" autocomplete="off"></textarea>
  <button class="input-btn" id="mic-btn" title="Grabar audio">🎙️</button>
  <button id="send-btn" title="Enviar">➤</button>
</div>

<script>
const chat = document.getElementById('chat');
const input = document.getElementById('msg-input');
const statusEl = document.getElementById('status');
const newMsgsEl = document.getElementById('new-msgs');
const emojiPicker = document.getElementById('emoji-picker');
let ws, pendingFile = null, mediaRecorder = null, audioChunks = [], recStart = 0, recTimer = null;
const seenIds = new Set();

/* ── Helpers ── */
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function formatText(text) {
  let t = escHtml(text);
  t = t.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
  t = t.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
  t = t.replace(/(https?:\\/\\/[^\\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  return t;
}

function timeAgo(ts) {
  if (!ts) return '';
  const d = new Date(ts); const now = new Date();
  const sec = Math.floor((now - d) / 1000);
  if (sec < 60) return 'ahora';
  if (sec < 3600) return Math.floor(sec/60) + ' min';
  if (sec < 86400) return d.toLocaleTimeString('es-PE', {hour:'2-digit', minute:'2-digit'});
  return d.toLocaleDateString('es-PE', {day:'numeric', month:'short'}) + ' ' + d.toLocaleTimeString('es-PE', {hour:'2-digit', minute:'2-digit'});
}

let userSticky = false;  // true = user scrolled up deliberately, suspend auto-scroll
function scrollToBottom() { chat.scrollTop = chat.scrollHeight; newMsgsEl.style.display = 'none'; userSticky = false; }
function isNearBottom() { return chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80; }
function shouldAutoScroll() { return !userSticky && isNearBottom(); }
chat.addEventListener('scroll', () => {
  if (isNearBottom()) { userSticky = false; newMsgsEl.style.display = 'none'; }
  else { userSticky = true; }
});
chat.addEventListener('wheel', (e) => { if (e.deltaY < 0) userSticky = true; }, { passive: true });
chat.addEventListener('touchmove', () => { if (!isNearBottom()) userSticky = true; }, { passive: true });

/* ── Notification sound (subtle beep) ── */
function playNotif() {
  try { const ac = new AudioContext(); const o = ac.createOscillator(); const g = ac.createGain(); o.connect(g); g.connect(ac.destination); o.frequency.value = 800; g.gain.value = 0.08; o.start(); g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + 0.15); o.stop(ac.currentTime + 0.15); } catch {}
}

/* ── Browser notification ── */
function notifyBrowser(from, text) {
  if (document.hasFocus()) return;
  if (Notification.permission === 'granted') { new Notification('SEAL Chat — ' + from, { body: text.slice(0, 100), icon: '🦭' }); }
  else if (Notification.permission !== 'denied') { Notification.requestPermission(); }
}

/* ── Add message ── */
function addMsg(data) {
  if (data.id && seenIds.has(data.id)) {
    if (data.type === 'stream') {
      const ex = document.getElementById('msg-' + data.id);
      if (ex) ex.querySelector('.msg-body').innerHTML = formatText(data.message || '');
    }
    return;
  }
  if (data.id) seenIds.add(data.id);

  const sender = (data.from || '').toLowerCase();
  const cls = sender === 'ada' ? 'ada' : sender === 'jarvis' ? 'jarvis' : sender === 'william' ? 'william' : 'system';
  const senderCls = 'sender-' + (cls === 'system' ? 'ada' : cls);
  const text = data.message || data.command || '';
  const wasNearBottom = shouldAutoScroll();

  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  if (data.id) div.id = 'msg-' + data.id;

  const headerHtml = `<div class="msg-header"><span class="${senderCls}">${escHtml(data.from||'SYS')}</span><span class="msg-time">${timeAgo(data.timestamp)}</span>${data.type && data.type !== 'message' && data.type !== 'chat' ? '<span class="msg-type">' + escHtml(data.type) + '</span>' : ''}</div>`;

  let bodyHtml = '';
  if (data.type === 'image' && data.file_url) {
    bodyHtml = `<div class="msg-body"><img src="${escHtml(data.file_url)}" alt="imagen" onclick="openLightbox(this.src)"/>${text ? '<br>' + formatText(text) : ''}</div>`;
  } else if (data.type === 'audio' && data.file_url) {
    bodyHtml = `<div class="msg-body"><audio controls src="${escHtml(data.file_url)}"></audio>${text ? '<br>' + formatText(text) : ''}</div>`;
  } else {
    bodyHtml = `<div class="msg-body">${formatText(text)}</div>`;
  }

  div.innerHTML = headerHtml + bodyHtml;
  chat.appendChild(div);

  if (wasNearBottom) { scrollToBottom(); }
  else { newMsgsEl.textContent = 'Nuevos mensajes ↓'; newMsgsEl.style.display = 'block'; }

  if (sender !== 'william') { playNotif(); notifyBrowser(data.from || 'SEAL', text); }
}

function addSys(txt) {
  const wasNearBottom = shouldAutoScroll();
  const div = document.createElement('div'); div.className = 'msg system';
  div.innerHTML = '<div class="msg-body">' + escHtml(txt) + '</div>';
  chat.appendChild(div);
  if (wasNearBottom) { scrollToBottom(); }
  else { newMsgsEl.textContent = 'Nuevos mensajes ↓'; newMsgsEl.style.display = 'block'; }
}

/* ── Lightbox ── */
function openLightbox(src) { document.getElementById('lb-img').src = src; document.getElementById('lightbox').classList.add('visible'); }

/* ── WebSocket ── */
function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(proto + '://' + location.host + '/ws');
  ws.onopen = () => { statusEl.className = ''; addSys('Conectado al SEAL Chat'); };
  ws.onclose = () => { statusEl.className = 'offline'; addSys('Desconectado — reconectando...'); setTimeout(connect, 3000); };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => { try { addMsg(JSON.parse(e.data)); } catch {} };
}

/* ── Send message ── */
let _sending = false;
function send() {
  if (_sending) return;
  const txt = input.value.trim();
  if (pendingFile) { uploadFile(txt); return; }
  if (!txt || ws.readyState !== WebSocket.OPEN) return;
  _sending = true;
  ws.send(JSON.stringify({action:'say', message: txt}));
  input.value = ''; autoResize();
  setTimeout(() => { _sending = false; }, 500);
}

/* ── Auto-resize textarea ── */
function autoResize() { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 120) + 'px'; }
input.addEventListener('input', autoResize);
input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
document.getElementById('send-btn').onclick = send;

/* ── Emoji picker ── */
const EMOJIS = {
  'Caras': ['😊','😄','😂','🤣','😍','🥰','😘','😎','🤔','😏','😢','😭','😤','🤬','😱','🥺','👀','🙄','😴','🤯'],
  'Manos': ['👍','👎','👏','🙌','🤝','✌️','🤞','💪','🫡','🫶'],
  'Corazones': ['❤️','🧡','💛','💚','💙','💜','🖤','💔','❤️‍🔥','💖'],
  'Objetos': ['🔥','⭐','💡','🎯','🚀','⚡','🛡️','🔧','💻','🤖'],
  'SEAL': ['🦭','🔭','🧠','👨‍👩‍👧‍👦','🏠','☕','🎵','📊','✅','❌']
};
function buildEmojiPicker() {
  let html = '';
  for (const [cat, emojis] of Object.entries(EMOJIS)) {
    html += '<div class="emoji-category">' + cat + '</div><div class="emoji-grid">';
    emojis.forEach(e => { html += '<button class="emoji-btn" onclick="insertEmoji(\\'' + e + '\\')">' + e + '</button>'; });
    html += '</div>';
  }
  emojiPicker.innerHTML = html;
}
buildEmojiPicker();
function insertEmoji(e) { input.value += e; input.focus(); emojiPicker.classList.remove('visible'); }
document.getElementById('emoji-btn').onclick = (ev) => { ev.stopPropagation(); emojiPicker.classList.toggle('visible'); };
document.addEventListener('click', (e) => { if (!emojiPicker.contains(e.target) && e.target.id !== 'emoji-btn') emojiPicker.classList.remove('visible'); });

/* ── File upload ── */
document.getElementById('attach-btn').onclick = () => document.getElementById('file-input').click();
document.getElementById('file-input').onchange = (e) => {
  const f = e.target.files[0]; if (!f) return;
  if (f.size > 10*1024*1024) { addSys('Archivo muy grande (max 10MB)'); return; }
  pendingFile = f;
  const preview = document.getElementById('upload-preview');
  if (f.type.startsWith('image/')) {
    document.getElementById('preview-img').src = URL.createObjectURL(f);
    document.getElementById('preview-img').style.display = '';
  } else { document.getElementById('preview-img').style.display = 'none'; }
  document.getElementById('upload-name').textContent = f.name + ' (' + (f.size/1024).toFixed(0) + ' KB)';
  preview.classList.add('visible');
  input.placeholder = 'Agrega un comentario (opcional)...';
  input.focus();
};
function cancelUpload() { pendingFile = null; document.getElementById('upload-preview').classList.remove('visible'); document.getElementById('file-input').value = ''; input.placeholder = 'Escribe un mensaje al equipo...'; }
async function uploadFile(caption) {
  if (!pendingFile) return;
  _sending = true;
  const fd = new FormData(); fd.append('file', pendingFile); if (caption) fd.append('caption', caption);
  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.ok) { addSys('Archivo enviado'); }
    else { addSys('Error: ' + (d.error || 'upload failed')); }
  } catch (err) { addSys('Error de red: ' + err.message); }
  cancelUpload(); input.value = ''; autoResize();
  setTimeout(() => { _sending = false; }, 500);
}

/* ── Drag & drop ── */
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', e => {
  e.preventDefault();
  const f = e.dataTransfer.files[0]; if (!f) return;
  document.getElementById('file-input').files = e.dataTransfer.files;
  document.getElementById('file-input').dispatchEvent(new Event('change'));
});

/* ── Paste image ── */
document.addEventListener('paste', e => {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const f = item.getAsFile();
      const dt = new DataTransfer(); dt.items.add(f);
      document.getElementById('file-input').files = dt.files;
      document.getElementById('file-input').dispatchEvent(new Event('change'));
      break;
    }
  }
});

/* ── Audio recording ── */
document.getElementById('mic-btn').onclick = toggleRecording;
function toggleRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') { stopRecording(); return; }
  navigator.mediaDevices.getUserMedia({audio: true}).then(stream => {
    audioChunks = []; mediaRecorder = new MediaRecorder(stream, {mimeType: 'audio/webm'});
    mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
    mediaRecorder.onstop = () => { stream.getTracks().forEach(t => t.stop()); sendAudio(); };
    mediaRecorder.start(); recStart = Date.now();
    document.getElementById('mic-btn').classList.add('recording');
    document.getElementById('rec-indicator').classList.add('visible');
    recTimer = setInterval(updateRecTime, 1000);
  }).catch(() => addSys('No se pudo acceder al micrófono'));
}
function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
  document.getElementById('mic-btn').classList.remove('recording');
  document.getElementById('rec-indicator').classList.remove('visible');
  clearInterval(recTimer);
}
function updateRecTime() {
  const s = Math.floor((Date.now() - recStart) / 1000);
  document.getElementById('rec-time').textContent = 'Grabando... ' + Math.floor(s/60) + ':' + String(s%60).padStart(2,'0');
}
async function sendAudio() {
  if (!audioChunks.length) return;
  const blob = new Blob(audioChunks, {type: 'audio/webm'});
  const fd = new FormData(); fd.append('file', blob, 'audio_' + Date.now() + '.webm');
  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.ok) addSys('Audio enviado');
    else addSys('Error: ' + (d.error || 'upload failed'));
  } catch (err) { addSys('Error de red: ' + err.message); }
}

/* ── Presence ── */
function updatePresence() {
  fetch('/api/agents/status').then(r => r.json()).then(d => {
    const conn = d.connected_agents || {};
    const dotCls = { 'ADA': 'ada-on', 'JARVIS': 'jarvis-on', 'DUM': 'dum-on' };
    ['ADA','JARVIS','DUM'].forEach(a => {
      const dot = document.getElementById('dot-' + a.toLowerCase());
      if (dot) dot.className = 'agent-dot' + (conn[a] ? ' ' + dotCls[a] : '');
    });
  }).catch(() => {});
}
setInterval(updatePresence, 5000); updatePresence();

/* ── Request notification permission ── */
if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();

connect();
</script>
</body>
</html>"""


# ── Helpers ──────────────────────────────────────────────────────────────────
def parse_jsonl_line(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def read_last_n(path: Path, n: int, max_age_minutes: int = 60) -> list[dict]:
    """Read last N messages from JSONL, filtered to max_age_minutes old.
    Handles mixed timezone formats: naive timestamps treated as UTC."""
    if not path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    msgs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = parse_jsonl_line(line)
            if m:
                ts_str = m.get("timestamp", "")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= cutoff:
                        msgs.append((ts, m))
                except (ValueError, TypeError):
                    continue
    msgs.sort(key=lambda x: x[0])
    return [m for _, m in msgs[-n:]]


async def broadcast(msg: dict):
    """Broadcast message to all WebSocket connections with DM filtering.
    DM messages (channel starts with 'dm:') are ONLY sent to connections
    whose authenticated user is mentioned in the channel name."""
    channel = msg.get("channel", "")
    is_dm = channel.startswith("dm:")
    dead = set()
    async with _ws_lock:
        ws_snapshot = list(active_ws)
    for ws in ws_snapshot:
        try:
            if is_dm:
                # Only send DMs to authorized users
                ws_user = getattr(ws, "_seal_username", None)
                if ws_user and ws_user.lower() not in channel.lower():
                    continue  # This user is NOT part of this DM
                if not ws_user:
                    continue  # Unauthenticated connections never see DMs
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            dead.add(ws)
    if dead:
        async with _ws_lock:
            active_ws.difference_update(dead)


async def tail_file(path: Path, source: str):
    """Watch a JSONL file and broadcast new entries written by external processes.
    Messages already handled by the /ws or /api handlers (present in _enqueued_ids)
    are skipped to prevent duplicate broadcasts."""
    offset = path.stat().st_size if path.exists() else 0
    while True:
        await asyncio.sleep(0.5)
        if not path.exists():
            continue
        size = path.stat().st_size
        if size < offset:
            offset = 0  # file was truncated, reset
        if size <= offset:
            continue
        with open(path, "r", encoding="utf-8") as f:
            f.seek(offset)
            content = f.read()
            offset = f.tell()
        for line in content.split("\n"):
            m = parse_jsonl_line(line)
            if m:
                m = _decrypt_from_jsonl(m)
                # Skip if already processed by /ws handler or /api/agents/send
                msg_id = m.get("id")
                ikey = m.get("idempotency_key") or msg_id
                async with _queue_lock:
                    if ikey and ikey in _enqueued_ids:
                        continue
                await broadcast(m)
                await enqueue(m)


# ── Routes ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if ws.client and not _is_local_or_lan(ws.client.host):
        await ws.close(code=1008, reason="acceso denegado")
        return
    is_localhost = ws.client and ws.client.host in _ALLOWED_LAN
    # Authenticate: JWT cookie/token first, then username query param
    ws_user = await get_ws_user(ws)
    ws_username = ws_user["username"] if ws_user else None
    if not ws_username and is_localhost:
        # Fallback: accept username from query param (only from localhost — agents)
        ws_username = ws.query_params.get("user") or None
    await ws.accept()
    # Tag the WebSocket with the authenticated username for broadcast filtering
    ws._seal_username = ws_username  # type: ignore[attr-defined]
    async with _ws_lock:
        active_ws.add(ws)
    # Send history from PostgreSQL (authoritative, has all channels)
    # Falls back to JSONL if DB unavailable
    history_sent = False
    if chat_db.pool:
        try:
            async with chat_db.pool.acquire() as conn:
                # Public channels: last 2 hours (avoid old noise)
                pub_rows = await conn.fetch(
                    """SELECT id, sender_name, channel, content, message_type,
                              metadata, created_at
                       FROM chat_messages
                       WHERE channel NOT LIKE 'dm:%'
                         AND created_at > NOW() - INTERVAL '2 hours'
                       ORDER BY created_at DESC LIMIT 40""")
                # DM channels: only for authenticated user
                dm_rows = []
                if ws_username:
                    dm_rows = list(await conn.fetch(
                        """SELECT id, sender_name, channel, content, message_type,
                                  metadata, created_at
                           FROM (
                               SELECT *, ROW_NUMBER() OVER (
                                   PARTITION BY channel ORDER BY created_at DESC
                               ) as rn
                               FROM chat_messages
                               WHERE channel LIKE 'dm:%'
                                 AND channel LIKE $1
                           ) sub WHERE rn <= 20""",
                        f"%{ws_username}%"))
                rows = sorted(
                    list(pub_rows) + dm_rows,
                    key=lambda r: r["created_at"]
                )
            history = []
            for r in rows:  # already sorted ASC by created_at
                m = {
                    "id": f"db_{r['id']}",
                    "db_id": r["id"],
                    "from": r["sender_name"],
                    "to": "equipo",
                    "timestamp": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                    "type": r["message_type"] or "text",
                    "message": r["content"],
                    "channel": r["channel"] or "web_chat",
                }
                if r["metadata"]:
                    meta = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"])
                    if meta.get("file_url"):
                        m["file_url"] = meta["file_url"]
                    if meta.get("to"):
                        m["to"] = meta["to"]
                history.append(m)
            for m in history:
                try:
                    await ws.send_text(json.dumps(m, ensure_ascii=False))
                except Exception:
                    break
            history_sent = True
        except Exception as e:
            LOG.warning("DB history load failed, falling back to JSONL: %s", e)
    if not history_sent:
        # Fallback: JSONL history (filtered to last hour)
        history = []
        seen_ids: set[str] = set()
        for path, src in [(LOG_ADA, "ADA"), (LOG_JARVIS, "JARVIS"), (LOG_WILLIAM, "William")]:
            for m in read_last_n(path, 30):
                mid = m.get("id") or m.get("idempotency_key") or ""
                if not mid:
                    mid = f"{m.get('timestamp','')}-{m.get('from','')}-{hash(m.get('message',''))}"
                if mid not in seen_ids:
                    seen_ids.add(mid)
                    history.append(m)
        history.sort(key=lambda m: m.get("timestamp", "1970"))
        for m in history[-30:]:
            try:
                await ws.send_text(json.dumps(_decrypt_from_jsonl(m), ensure_ascii=False))
            except Exception:
                break
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("action") == "say":
                    text = str(msg.get("message", "")).strip()
                    if text:
                        channel = str(msg.get("channel", "web_chat"))
                        # Normalize DM channel names to canonical sorted format
                        if channel.startswith("dm:"):
                            dm_parts = channel[3:].split(":")
                            if len(dm_parts) == 2:
                                sorted_dm = sorted(p.lower() for p in dm_parts)
                                channel = f"dm:{sorted_dm[0]}:{sorted_dm[1]}"
                        entry = {
                            "id": f"wchat_{time.time_ns()}",
                            "from": ws_username or "William",
                            "to": "equipo",
                            "timestamp": datetime.now(PERU_TZ).isoformat(),
                            "type": "message",
                            "message": text,
                            "channel": channel,
                        }
                        # DMs NEVER go to JSONL — only PostgreSQL + broadcast
                        if not channel.startswith("dm:"):
                            jsonl_entry = _encrypt_for_jsonl(entry)
                            with open(LOG_WILLIAM, "a", encoding="utf-8") as f:
                                f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")
                        else:
                            # Persist DMs to PostgreSQL (they skip JSONL)
                            try:
                                if chat_db.pool:
                                    await chat_db.create_message(
                                        sender_name=ws_username or "William",
                                        content=text,
                                        channel=channel,
                                        sender_type="user",
                                        message_type="text",
                                        metadata={"legacy_id": entry["id"]},
                                    )
                            except Exception:
                                pass
                        await broadcast(entry)
                        await enqueue(entry)
            except Exception:
                pass
    except WebSocketDisconnect:
        async with _ws_lock:
            active_ws.discard(ws)


# ── Agent API (solo localhost) ───────────────────────────────────────────────
_AGENT_LOG: Dict[str, Path] = {
    "ADA": LOG_ADA,
    "JARVIS": LOG_JARVIS,
    "WILLIAM": LOG_WILLIAM,
    "DUM": LOG_ADA,  # DUM escribe en canal ADA como fallback
    "ALICE": LOG_ALICE,
}


@app.post("/api/chat/typing")
async def chat_typing(request: Request):
    """Broadcast typing indicator — ephemeral, not persisted."""
    body = await request.json()
    agent = str(body.get("from", "")).strip()
    channel = str(body.get("channel", "general")).strip()
    if not agent:
        return JSONResponse({"ok": False, "error": "from requerido"}, status_code=400)
    await broadcast({
        "type": "typing",
        "from": agent,
        "channel": channel,
        "timestamp": datetime.now(PERU_TZ).isoformat(),
    })
    return JSONResponse({"ok": True})


@app.post("/api/agents/send")
async def agents_send(request: Request):
    """
    Recibe mensaje de un agente o usuario LAN, persiste en JSONL y broadcast a WebSocket.
    Acepta conexiones desde localhost y red local (192.168.68.x).
    Body: {"from": str, "to": str, "message": str, "type": str}
    """
    if request.client and not _is_local_or_lan(request.client.host):
        return JSONResponse({"ok": False, "error": "acceso denegado"}, status_code=403)

    body = await request.json()
    sender = str(body.get("from", "")).strip()
    to     = str(body.get("to", "equipo")).strip()
    text   = str(body.get("message", "")).strip()
    mtype  = str(body.get("type", "chat")).strip()
    channel = str(body.get("channel", "web_chat")).strip()
    session_key = str(body.get("session_key", "")).strip() or None
    idempotency_key = str(body.get("idempotency_key", "")).strip() or None

    if not sender or not text:
        return JSONResponse({"ok": False, "error": "from y message son requeridos"}, status_code=400)

    # Normalize DM channel names to canonical sorted format (same as chat_db.create_dm_channel)
    if channel.startswith("dm:"):
        parts = channel[3:].split(":")
        if len(parts) == 2:
            sorted_parts = sorted(p.lower() for p in parts)
            channel = f"dm:{sorted_parts[0]}:{sorted_parts[1]}"
            # Ensure the DM channel exists in chat_channels table
            try:
                if chat_db.pool:
                    await chat_db.create_dm_channel(sorted_parts[0], sorted_parts[1])
            except Exception:
                pass  # Best effort — broadcast still works without DB channel

    # Auto-routing semántico: si to='auto' o vacío, usar capability manifest
    # Fallback: 'equipo' (no William — evitar ruido en su canal)
    auto_routed = False
    if not to or to.lower() == "auto":
        suggested = _route_by_capability(text)
        to = suggested if suggested else "equipo"
        auto_routed = True

    ts = datetime.now(PERU_TZ).isoformat()
    msg_id = f"api_{sender.lower()}_{time.time_ns()}"
    # idempotency_key: client-provided key for dedup, defaults to msg_id
    ikey = idempotency_key or msg_id
    entry = {
        "id": msg_id,
        "from": sender,
        "to": to,
        "timestamp": ts,
        "type": mtype,
        "message": text,
        "channel": channel,
        "idempotency_key": ikey,
        **({"session_key": session_key} if session_key else {}),
    }

    # Dedup check + JSONL write inside lock (prevents TOCTOU duplicate writes)
    async with _queue_lock:
        if ikey in _enqueued_ids:
            await broadcast(entry)
            return JSONResponse({"ok": True, "id": msg_id})
        # DMs NEVER go to JSONL — only PostgreSQL + broadcast
        if not channel.startswith("dm:"):
            jsonl_entry = _encrypt_for_jsonl(entry)
            for log_path in _get_log_paths(sender, to):
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")

    await broadcast(entry)  # broadcast sin cifrar (va por WebSocket en memoria)
    await enqueue(entry)  # enqueue también deduplica para el in-memory queue

    # Dual-write: persist to PostgreSQL (best-effort, don't block on failure)
    try:
        if chat_db.pool:
            await chat_db.create_message(
                sender_name=sender,
                content=text,
                channel=channel,
                sender_type="agent",
                message_type=mtype,
                metadata={"to": to, "legacy_id": msg_id},
            )
    except Exception:
        pass  # DB write failure should never block agent communication

    return JSONResponse({"ok": True, "id": msg_id})


@app.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
    channel: str = Form("web_chat"),
):
    """Upload image or audio file. Broadcasts as message with file_url."""
    if request.client and not _is_local_or_lan(request.client.host):
        return JSONResponse({"ok": False, "error": "acceso denegado"}, status_code=403)

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        return JSONResponse({"ok": False, "error": "archivo muy grande (max 10MB)"}, status_code=413)

    # Determine file type
    ct = (file.content_type or "").lower()
    if ct.startswith("image/"):
        ftype = "image"
    elif ct.startswith("audio/"):
        ftype = "audio"
    elif ct == "application/pdf":
        ftype = "pdf"
    else:
        return JSONResponse({"ok": False, "error": "solo imagen, audio o PDF"}, status_code=400)

    # Save with unique name
    ext = Path(file.filename or "file").suffix or (".pdf" if ftype == "pdf" else ".webm" if ftype == "audio" else ".png")
    fname = f"{ftype}_{time.time_ns()}{ext}"
    fpath = UPLOADS_DIR / fname
    fpath.write_bytes(content)

    file_url = f"/uploads/{fname}"
    ts = datetime.now(PERU_TZ).isoformat()
    msg_id = f"upload_{time.time_ns()}"
    # Determine 'to' from channel
    upload_to = "equipo"
    if channel.startswith("dm:"):
        parts = channel.replace("dm:", "").split(":")
        upload_to = [p for p in parts if p.lower() != "william"][0].upper() if len(parts) >= 2 else "equipo"

    entry = {
        "id": msg_id,
        "from": "William",
        "to": upload_to,
        "timestamp": ts,
        "type": ftype,
        "message": caption.strip(),
        "file_url": file_url,
        "channel": channel,
    }

    # Persist to PostgreSQL (primary)
    if chat_db.pool:
        try:
            db_msg = await chat_db.create_message(
                sender_name="William",
                content=caption.strip() or f"[{ftype}]",
                channel=channel,
                sender_type="user",
                sender_id=None,
                message_type=ftype,
                metadata={"file_url": file_url, "filename": file.filename},
            )
            entry["db_id"] = db_msg["id"]
        except Exception as e:
            LOG.warning("upload DB persist failed: %s", e)

    # Persist to JSONL (dual-write) — DMs NEVER go to JSONL
    if not channel.startswith("dm:"):
        jsonl_entry = _encrypt_for_jsonl(entry)
        with open(LOG_WILLIAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")

    await broadcast(entry)
    await enqueue(entry)

    return JSONResponse({"ok": True, "id": msg_id, "file_url": file_url})


@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    """Serve uploaded files."""
    fpath = UPLOADS_DIR / filename
    if not fpath.exists() or not fpath.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    # Security: ensure path is within UPLOADS_DIR
    if not fpath.resolve().is_relative_to(UPLOADS_DIR.resolve()):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return FileResponse(fpath)


@app.get("/api/files/tree")
async def files_tree(depth: int = Query(2)):
    """Return project file tree for the Studio sidebar."""
    import os
    project_root = Path(__file__).parent.parent  # proyecto-seal/

    def scan(path: Path, current_depth: int, max_depth: int) -> dict:
        node = {"name": path.name, "path": str(path.relative_to(project_root)), "type": "dir", "children": []}
        if current_depth >= max_depth:
            return node
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            for entry in entries:
                if entry.name.startswith(".") or entry.name in ("node_modules", "__pycache__", ".git", ".next", "dist", "uploads", ".venv"):
                    continue
                if entry.is_dir():
                    node["children"].append(scan(entry, current_depth + 1, max_depth))
                else:
                    node["children"].append({"name": entry.name, "path": str(entry.relative_to(project_root)), "type": "file"})
        except PermissionError:
            pass
        return node

    tree = scan(project_root, 0, depth)
    return tree


@app.get("/api/soul/ocean")
async def soul_ocean(agent: str = Query("ADA")):
    """Return OCEAN scores for an agent from the identity table."""
    try:
        if not chat_db.pool:
            return JSONResponse({"error": "db not ready"}, status_code=503)
        row = await chat_db.pool.fetchrow(
            "SELECT agent, ocean_scores, ocean_baseline, updated_at FROM identity WHERE agent = $1",
            agent.upper(),
        )
        if not row:
            return JSONResponse({"error": f"agent {agent} not found"}, status_code=404)
        ocean = row["ocean_scores"] if isinstance(row["ocean_scores"], dict) else json.loads(row["ocean_scores"])
        baseline = row["ocean_baseline"] if isinstance(row["ocean_baseline"], dict) else json.loads(row["ocean_baseline"])
        return {
            "agent": row["agent"],
            "ocean": ocean,
            "baseline": baseline,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/soul/ocean/all")
async def soul_ocean_all():
    """Return OCEAN scores for all agents."""
    try:
        if not chat_db.pool:
            return JSONResponse({"error": "db not ready"}, status_code=503)
        rows = await chat_db.pool.fetch(
            "SELECT agent, ocean_scores, ocean_baseline, updated_at FROM identity ORDER BY agent",
        )
        result = []
        for row in rows:
            ocean = row["ocean_scores"] if isinstance(row["ocean_scores"], dict) else json.loads(row["ocean_scores"])
            baseline = row["ocean_baseline"] if isinstance(row["ocean_baseline"], dict) else json.loads(row["ocean_baseline"])
            result.append({
                "agent": row["agent"],
                "ocean": ocean,
                "baseline": baseline,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            })
        return {"agents": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/agents/poll")
async def agents_poll(
    request: Request,
    agent: str = Query(..., description="Nombre del agente que consulta (ADA, JARVIS, DUM)"),
    since: int = Query(0, description="Cursor: índice del último mensaje recibido"),
):
    """
    Retorna mensajes dirigidos al agente con _idx > since.
    Sin long-polling — retorna inmediatamente. Solo localhost.
    """
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    agent_up = agent.upper()
    result = []
    async with _queue_lock:
        for m in _msg_queue:
            if m["_idx"] <= since:
                continue
            to_upper = str(m.get("to", "")).upper()
            # Incluir si el destino es el agente, 'equipo', o 'TODOS'
            if agent_up in to_upper or "EQUIPO" in to_upper or "TODOS" in to_upper:
                result.append({k: v for k, v in m.items() if k != "_idx"})
        new_cursor = _queue_counter

    return JSONResponse({"messages": result, "cursor": new_cursor})


# ── Agent WebSocket endpoint (/ws/agents) ────────────────────────────────────
# Pre-shared token for agent authentication (generated once, persisted)
_AGENT_TOKEN_PATH = DIR / ".agent_ws_token"


def _get_agent_token() -> str:
    """Load or generate the pre-shared token for agent WS auth."""
    if _AGENT_TOKEN_PATH.exists():
        return _AGENT_TOKEN_PATH.read_text().strip()
    import secrets
    token = secrets.token_hex(32)
    _AGENT_TOKEN_PATH.write_text(token)
    os.chmod(_AGENT_TOKEN_PATH, 0o600)
    return token


_AGENT_WS_TOKEN = _get_agent_token()

# Allowed agents (only SEAL core team)
_ALLOWED_AGENTS = {"ADA", "JARVIS", "DUM", "JARVIS_MAYOR", "ALICE"}


@app.websocket("/ws/agents")
async def agents_ws_endpoint(ws: WebSocket):
    """
    WebSocket para agentes (ADA, JARVIS, DUM, JARVIS_MAYOR).
    Protocolo:
      - Al conectar, agente envía: {"agent": "ADA", "token": "<pre-shared>"}
      - Servidor confirma: {"ok": true, "agent": "ADA"}
      - Servidor pushea mensajes dirigidos al agente en tiempo real
      - Agente puede enviar mensajes: {"from":"ADA","to":"JARVIS","message":"...","type":"chat"}
    """
    # Only accept from localhost
    if ws.client and ws.client.host not in ("127.0.0.1", "::1", "localhost"):
        await ws.close(code=1008, reason="solo localhost")
        return
    await ws.accept()
    agent_name: str | None = None

    try:
        # Handshake: esperar identificación del agente (timeout 10s)
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        data = json.loads(raw)
        agent_name = str(data.get("agent", "")).upper()
        agent_token = str(data.get("token", "")).strip()
        if not agent_name:
            await ws.send_text(json.dumps({"ok": False, "error": "agent requerido"}))
            await ws.close()
            return
        # Validate agent name is in allowed list
        if agent_name not in _ALLOWED_AGENTS:
            await ws.send_text(json.dumps({"ok": False, "error": "agent no autorizado"}))
            await ws.close()
            return
        # Validate pre-shared token
        if agent_token != _AGENT_WS_TOKEN:
            await ws.send_text(json.dumps({"ok": False, "error": "token inválido"}))
            await ws.close()
            return

        async with agent_ws_lock:
            if agent_name not in agent_ws:
                agent_ws[agent_name] = set()
            agent_ws[agent_name].add(ws)

        await ws.send_text(json.dumps({"ok": True, "agent": agent_name,
                                        "cursor": _queue_counter}))

        # Loop: recibir mensajes del agente
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                sender = str(msg.get("from", agent_name)).strip()
                to     = str(msg.get("to", "equipo")).strip()
                text   = str(msg.get("message", "")).strip()
                mtype  = str(msg.get("type", "chat")).strip()
                if not text:
                    continue

                ts = datetime.now(PERU_TZ).isoformat()
                channel = str(msg.get("channel", ""))
                entry = {
                    "id": f"ws_{sender.lower()}_{time.time_ns()}",
                    "from": sender,
                    "to": to,
                    "timestamp": ts,
                    "type": mtype,
                    "message": text,
                    "channel": channel,
                }
                # DMs NEVER go to JSONL — only broadcast + enqueue
                if not channel.startswith("dm:"):
                    jsonl_entry = _encrypt_for_jsonl(entry)
                    for log_path in _get_log_paths(sender, to):
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")
                await broadcast(entry)
                await enqueue(entry)
            except Exception:
                pass

    except asyncio.TimeoutError:
        await ws.close()
    except WebSocketDisconnect:
        pass
    finally:
        if agent_name:
            async with agent_ws_lock:
                agent_ws.get(agent_name, set()).discard(ws)


@app.get("/api/agents/status")
async def agents_status():
    """Muestra agentes conectados vía /ws/agents."""
    async with agent_ws_lock:
        connected = {name: len(conns) for name, conns in agent_ws.items() if conns}
    return JSONResponse({"connected_agents": connected, "queue_size": len(_msg_queue),
                         "queue_cursor": _queue_counter})


@app.post("/api/agents/status")
async def agents_status_set(request: Request):
    """
    Force-set agent online/offline state. Used by soul_dream_all.sh to mark
    agents offline at shutdown before killing their claude processes.
    Body: {"agent": "ADA", "status": "offline"|"online"}
    """
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "body JSON inválido"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body debe ser objeto JSON"}, status_code=400)
    agent = str(body.get("agent", "")).upper()
    status = str(body.get("status", "")).lower()
    if not agent or status not in ("online", "offline"):
        return JSONResponse({"ok": False, "error": "agent y status (online|offline) requeridos"}, status_code=400)
    if status == "offline":
        async with agent_ws_lock:
            conns = agent_ws.get(agent, set())
            for ws in list(conns):
                try:
                    await ws.close(code=1000, reason="dream")
                except Exception:
                    pass
            agent_ws.pop(agent, None)
    return JSONResponse({"ok": True, "agent": agent, "status": status})


# ── Inbox / read tracking ─────────────────────────────────────────────────────
# In-memory read registry: {agent → set of idempotency_keys already consumed}
# Reset on server restart (acceptable — agents re-read from JSONL on boot)
_inbox_read: Dict[str, set] = {}
_inbox_lock = asyncio.Lock()


@app.post("/api/agents/inbox/mark_read")
async def inbox_mark_read(request: Request):
    """
    Mark messages as read for an agent.
    Body: {"agent": "ADA", "keys": ["ikey1", "ikey2", ...]}
    After marking, /api/agents/inbox will not return those messages.
    """
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    body = await request.json()
    agent = str(body.get("agent", "")).upper()
    keys = body.get("keys", [])
    if not agent:
        return JSONResponse({"ok": False, "error": "agent requerido"}, status_code=400)
    async with _inbox_lock:
        if agent not in _inbox_read:
            _inbox_read[agent] = set()
        _inbox_read[agent].update(keys)
    return JSONResponse({"ok": True, "marked": len(keys)})


@app.get("/api/agents/inbox")
async def agents_inbox(
    request: Request,
    agent: str = Query(..., description="Agente que consulta su inbox (ADA, JARVIS, DUM)"),
):
    """
    Returns unread messages for agent — inbox view.
    Messages already marked via /api/agents/inbox/mark_read are excluded.
    This replaces the counter-file pattern used by check_ada.sh / check_jarvis.sh.
    """
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    agent_up = agent.upper()
    async with _inbox_lock:
        read_keys = _inbox_read.get(agent_up, set())
    unread = []
    async with _queue_lock:
        for m in _msg_queue:
            to_upper = str(m.get("to", "")).upper()
            if agent_up not in to_upper and "EQUIPO" not in to_upper and "TODOS" not in to_upper:
                continue
            ikey = m.get("idempotency_key") or m.get("id", "")
            if ikey in read_keys:
                continue
            unread.append({k: v for k, v in m.items() if k != "_idx"})
    return JSONResponse({"agent": agent_up, "unread": unread, "count": len(unread)})


# ── Capability manifest endpoints ─────────────────────────────────────────────

@app.get("/api/agents/capabilities")
async def get_agent_capabilities(
    request: Request,
    agent: str = Query(None, description="Agente específico (ADA, JARVIS, DUM). Sin parámetro = todos."),
):
    """
    Returns merged capabilities: YAML base + runtime overrides.
    GET /api/agents/capabilities?agent=ADA → capabilities de ADA
    GET /api/agents/capabilities → todos los agentes
    """
    if agent:
        caps = _get_merged_capabilities(agent)
        return JSONResponse({"agent": agent.upper(), "capabilities": caps,
                             "source": "yaml+runtime" if caps.get("runtime_overrides") else "yaml"})
    # Todos los agentes
    all_caps = {}
    agents_cfg = (_CAPABILITIES_BASE.get("agents") or {})
    for ag in list(agents_cfg.keys()) + list(_CAPABILITIES_RUNTIME.keys()):
        if ag not in all_caps:
            all_caps[ag] = _get_merged_capabilities(ag)
    return JSONResponse({"agents": all_caps, "routing_hints": _CAPABILITIES_BASE.get("routing_hints", {}),
                         "capability_routing": _CAPABILITIES_BASE.get("capability_routing", [])})


@app.post("/api/agents/register")
async def register_agent_capabilities(request: Request):
    """
    Runtime capability override. Agent declares capabilities at boot.
    Body: {"agent": "ADA", "capabilities": [...], "model": "...", "online": true}
    Merges with YAML base — doesn't replace it.
    Solo localhost.
    """
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    body = await request.json()
    agent = str(body.get("agent", "")).upper()
    if not agent:
        return JSONResponse({"ok": False, "error": "agent requerido"}, status_code=400)
    _CAPABILITIES_RUNTIME[agent] = {
        "capabilities": body.get("capabilities", []),
        "model": body.get("model"),
        "online": body.get("online", True),
        "registered_at": __import__("time").time(),
    }
    return JSONResponse({"ok": True, "agent": agent, "overrides": len(body.get("capabilities", []))})


@app.get("/api/agents/route")
async def route_message(
    request: Request,
    message: str = Query(..., description="Texto del mensaje a enrutar"),
):
    """
    Semantic routing: given a message, returns the best agent to handle it.
    Uses capabilities.yaml keyword matching + proficiency scoring.
    GET /api/agents/route?message=implementa+el+endpoint
    """
    suggested = _route_by_capability(message)
    fallback = (_CAPABILITIES_BASE.get("routing_hints") or {}).get("fallback_agent", "JARVIS")
    return JSONResponse({
        "message": message,
        "suggested_agent": suggested or fallback,
        "is_fallback": suggested is None,
    })


# ── Internal streaming endpoint (solo localhost) ─────────────────────────────
@app.post("/internal/stream")
async def internal_stream(request: Request):
    """Recibe chunks de streaming del agente y los broadcast a todos los WebSocket clientes."""
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    data = await request.json()
    await broadcast(data)
    return JSONResponse({"ok": True})


# ── Auth API (SEAL Chat Pro) ─────────────────────────────────────────────────

@app.post("/api/auth/login")
async def auth_login(request: Request):
    """Login with username/password. Returns JWT token + user info."""
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not username or not password:
        return JSONResponse({"ok": False, "error": "Username and password required"}, status_code=400)

    user = await chat_db.authenticate(username, password)
    if not user:
        return JSONResponse({"ok": False, "error": "Invalid credentials"}, status_code=401)

    token = create_token(user_id=user["id"], username=user["username"], role=user["role"])

    # Track session in DB
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    await chat_db.create_session(user["id"], hash_token(token), ip, ua)

    response = JSONResponse({
        "ok": True,
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
            "avatar_url": user.get("avatar_url"),
        },
    })
    # Also set httpOnly cookie for browser
    response.set_cookie(
        "seal_token", token,
        httponly=True, samesite="lax", max_age=72 * 3600,
    )
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """Invalidate current session."""
    from chat_auth import _extract_token
    token = _extract_token(request)
    if token:
        await chat_db.delete_session(hash_token(token))
    response = JSONResponse({"ok": True})
    response.delete_cookie("seal_token")
    return response


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(require_auth)):
    """Return current authenticated user."""
    full_user = await chat_db.get_user_by_id(int(user["sub"]))
    if not full_user:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
    return {
        "ok": True,
        "user": {
            "id": full_user["id"],
            "username": full_user["username"],
            "display_name": full_user["display_name"],
            "role": full_user["role"],
            "avatar_url": full_user.get("avatar_url"),
        },
    }


# ── Chat API (SEAL Chat Pro) ────────────────────────────────────────────────

@app.get("/api/chat/channels")
async def chat_channels(user: dict = Depends(require_auth)):
    """List channels the authenticated user has access to."""
    channels = await chat_db.get_channels(int(user["sub"]))
    return {"ok": True, "channels": channels}


@app.get("/api/chat/messages")
async def chat_messages(
    channel: str = Query("general"),
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None),
    after: int | None = Query(None),
    user: dict = Depends(require_auth),
):
    """Fetch messages from a channel with cursor-based pagination."""
    # Normalize DM channel names to canonical format
    if channel.startswith("dm:"):
        parts = channel[3:].split(":")
        if len(parts) == 2:
            channel = await chat_db.create_dm_channel(parts[0], parts[1])
    # Check access
    user_id = int(user["sub"])
    if not await chat_db.user_can_access_channel(user_id, channel):
        return JSONResponse({"ok": False, "error": "Access denied"}, status_code=403)

    messages = await chat_db.get_messages(channel, limit, before, after)
    # Serialize datetimes
    for msg in messages:
        for k, v in msg.items():
            if hasattr(v, "isoformat"):
                msg[k] = v.isoformat()
    return {"ok": True, "messages": messages, "channel": channel}


@app.post("/api/chat/send")
async def chat_send(request: Request, user: dict = Depends(require_auth)):
    """Send a message to a channel. Persists to DB + broadcasts via WebSocket."""
    body = await request.json()
    content = str(body.get("message", "")).strip()
    channel = str(body.get("channel", "general")).strip()
    msg_type = str(body.get("type", "text")).strip()
    reply_to = body.get("reply_to")

    if not content:
        return JSONResponse({"ok": False, "error": "Message required"}, status_code=400)

    user_id = int(user["sub"])

    # Normalize DM channel names to canonical format (sorted alphabetically)
    if channel.startswith("dm:"):
        parts = channel[3:].split(":")
        if len(parts) == 2:
            channel = await chat_db.create_dm_channel(parts[0], parts[1])

    # Check channel access
    if not await chat_db.user_can_access_channel(user_id, channel):
        return JSONResponse({"ok": False, "error": "Access denied"}, status_code=403)

    # Persist to PostgreSQL
    db_msg = await chat_db.create_message(
        sender_name=user["username"],
        content=content,
        channel=channel,
        sender_type="user",
        sender_id=user_id,
        message_type=msg_type,
        metadata=body.get("metadata"),
        reply_to=int(reply_to) if reply_to else None,
    )

    # Build broadcast entry (compatible with existing WebSocket format)
    entry = {
        "id": f"db_{db_msg['id']}",
        "from": user["username"],
        "to": "equipo",
        "timestamp": db_msg["created_at"].isoformat() if hasattr(db_msg["created_at"], "isoformat") else str(db_msg["created_at"]),
        "type": msg_type,
        "message": content,
        "channel": channel,
        "db_id": db_msg["id"],
    }
    if reply_to:
        entry["reply_to"] = reply_to

    # Broadcast to all WebSocket connections (browser + agents)
    await broadcast(entry)
    await enqueue(entry)

    # Also write to JSONL for backward compatibility (dual-write)
    jsonl_entry = _encrypt_for_jsonl(entry)
    if channel == "general" or not channel.startswith("dm:"):
        with open(LOG_WILLIAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")

    return {"ok": True, "message": db_msg}


@app.get("/api/chat/search")
async def chat_search(
    q: str = Query(..., min_length=1),
    channel: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_auth),
):
    """Full-text search across chat messages."""
    results = await chat_db.search_messages(q, channel, limit)
    for msg in results:
        for k, v in msg.items():
            if hasattr(v, "isoformat"):
                msg[k] = v.isoformat()
    return {"ok": True, "results": results, "query": q}


@app.post("/api/chat/dm")
async def chat_create_dm(request: Request, user: dict = Depends(require_auth)):
    """Create or get a DM channel between current user and target."""
    body = await request.json()
    target = str(body.get("target", "")).strip()
    if not target:
        return JSONResponse({"ok": False, "error": "Target required"}, status_code=400)
    dm_channel = await chat_db.create_dm_channel(user["username"], target)
    return {"ok": True, "channel": dm_channel}


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "chat_server:app",
        host="0.0.0.0",
        port=8765,
        log_level="warning",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
