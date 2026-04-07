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
from datetime import datetime, timezone
from pathlib import Path
from typing import Set, Dict, Deque, List

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

import uvicorn
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

DIR = Path(__file__).parent
LOG_ADA     = DIR / "terminal_log.jsonl"
LOG_JARVIS  = DIR / "vscode_commands.jsonl"
LOG_WILLIAM = DIR / "william_channel.jsonl"  # canal que leen los loops de ADA y JARVIS

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
@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_routing_config()
    _load_capabilities()
    for path, src in [(LOG_ADA, "ADA"), (LOG_JARVIS, "JARVIS"), (LOG_WILLIAM, "William")]:
        asyncio.create_task(tail_file(path, src))
    yield

app = FastAPI(title="SEAL Chat", lifespan=lifespan)
active_ws: Set[WebSocket] = set()

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
    # Broadcast to browser WebSocket connections (William's web UI)
    dead_ws = set()
    for ws in list(active_ws):
        try:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            dead_ws.add(ws)
    active_ws.difference_update(dead_ws)


# ── HTML UI ──────────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SEAL Chat</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', monospace; height: 100vh; display: flex; flex-direction: column; }
  #header { padding: 12px 16px; background: #161b22; border-bottom: 1px solid #30363d; display: flex; align-items: center; gap: 10px; }
  #header h1 { font-size: 1rem; color: #f0f6fc; }
  #status { width: 8px; height: 8px; border-radius: 50%; background: #3fb950; flex-shrink: 0; }
  #status.offline { background: #f85149; }
  #agents-presence { display: flex; gap: 12px; margin-left: auto; }
  .agent-indicator { display: flex; align-items: center; gap: 5px; font-size: 0.72rem; color: #8b949e; }
  .agent-dot { width: 8px; height: 8px; border-radius: 50%; background: #3d444d; flex-shrink: 0; transition: background 0.4s; }
  .agent-dot.ada-on { background: #58a6ff; box-shadow: 0 0 5px #58a6ff88; }
  .agent-dot.jarvis-on { background: #bc8cff; box-shadow: 0 0 5px #bc8cff88; }
  .agent-dot.dum-on { background: #3fb950; box-shadow: 0 0 5px #3fb95088; }
  #chat { flex: 1; overflow-y: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 6px; }
  .msg { padding: 8px 12px; border-radius: 8px; max-width: 90%; font-size: 0.85rem; line-height: 1.5; word-wrap: break-word; }
  .msg-header { font-size: 0.72rem; opacity: 0.6; margin-bottom: 3px; }
  .ada    { background: #0d2137; border-left: 3px solid #58a6ff; align-self: flex-start; }
  .jarvis { background: #1d1037; border-left: 3px solid #bc8cff; align-self: flex-start; }
  .william { background: #1a2d1a; border-left: 3px solid #3fb950; align-self: flex-end; }
  .system { background: #1c2128; border-left: 3px solid #6e7681; align-self: center; font-size: 0.75rem; opacity: 0.7; }
  pre { white-space: pre-wrap; font-family: inherit; }
  #input-area { padding: 10px 16px; background: #161b22; border-top: 1px solid #30363d; display: flex; gap: 8px; }
  #msg-input { flex: 1; background: #21262d; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; padding: 8px 12px; font-size: 0.9rem; outline: none; }
  #msg-input:focus { border-color: #58a6ff; }
  #send-btn { background: #238636; color: white; border: none; border-radius: 6px; padding: 8px 16px; cursor: pointer; font-size: 0.9rem; }
  #send-btn:hover { background: #2ea043; }
  .sender-ada    { color: #58a6ff; font-weight: bold; }
  .sender-jarvis { color: #bc8cff; font-weight: bold; }
  .sender-william { color: #3fb950; font-weight: bold; }
</style>
</head>
<body>
<div id="header">
  <div id="status" class="offline"></div>
  <h1>🔭 SEAL Chat — ADA ↔ JARVIS ↔ William</h1>
  <div id="agents-presence">
    <div class="agent-indicator"><div id="dot-ada" class="agent-dot"></div><span>ADA</span></div>
    <div class="agent-indicator"><div id="dot-jarvis" class="agent-dot"></div><span>JARVIS</span></div>
    <div class="agent-indicator"><div id="dot-dum" class="agent-dot"></div><span>DUM</span></div>
  </div>
</div>
<div id="chat"></div>
<div id="input-area">
  <input id="msg-input" type="text" placeholder="Escribe un mensaje al equipo..." autocomplete="off"/>
  <button id="send-btn">Enviar</button>
</div>
<script>
const chat = document.getElementById('chat');
const input = document.getElementById('msg-input');
const status = document.getElementById('status');
let ws;

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function addMsg(data) {
  const sender = (data.from || '').toLowerCase();
  const cls = sender === 'ada' ? 'ada' : sender === 'jarvis' ? 'jarvis' : sender === 'william' ? 'william' : 'system';
  const senderCls = 'sender-' + (cls === 'system' ? 'ada' : cls);
  const ts = data.timestamp ? new Date(data.timestamp).toLocaleTimeString('es-PE', {hour:'2-digit', minute:'2-digit'}) : '';
  const text = data.message || data.command || '';

  // Streaming: actualizar burbuja existente si mismo id
  if (data.id && data.type === 'stream') {
    const existing = document.getElementById('msg-' + data.id);
    if (existing) {
      existing.querySelector('pre').textContent = text;
      chat.scrollTop = chat.scrollHeight;
      return;
    }
  }

  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  if (data.id) div.id = 'msg-' + data.id;
  div.innerHTML = `<div class="msg-header"><span class="${senderCls}">${escHtml(data.from||'SYS')}</span> &nbsp;${ts} &nbsp;<em>${escHtml(data.type||'')}</em></div><pre>${escHtml(text)}</pre>`;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { status.className = ''; addSys('Conectado al SEAL Chat'); };
  ws.onclose = () => { status.className = 'offline'; addSys('Desconectado — reconectando en 3s...'); setTimeout(connect, 3000); };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    try { const d = JSON.parse(e.data); addMsg(d); } catch {}
  };
}

function addSys(txt) {
  const div = document.createElement('div');
  div.className = 'msg system';
  div.textContent = txt;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

let _sending = false;
function send() {
  if (_sending) return;
  const txt = input.value.trim();
  if (!txt || ws.readyState !== WebSocket.OPEN) return;
  _sending = true;
  ws.send(JSON.stringify({action:'say', message: txt}));
  input.value = '';
  setTimeout(() => { _sending = false; }, 800);
}

document.getElementById('send-btn').onclick = send;
input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });

function updatePresence() {
  fetch('/api/agents/status')
    .then(r => r.json())
    .then(d => {
      const connected = d.connected_agents || {};
      const dotCls = { 'ADA': 'ada-on', 'JARVIS': 'jarvis-on', 'DUM': 'dum-on' };
      ['ADA','JARVIS','DUM'].forEach(a => {
        const dot = document.getElementById('dot-' + a.toLowerCase());
        if (dot) dot.className = 'agent-dot' + (connected[a] ? ' ' + dotCls[a] : '');
      });
    })
    .catch(() => {});
}
setInterval(updatePresence, 5000);
updatePresence();

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


def read_last_n(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    msgs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = parse_jsonl_line(line)
            if m:
                msgs.append((m.get("timestamp", "1970"), m))
    msgs.sort(key=lambda x: x[0])
    return [m for _, m in msgs[-n:]]


async def broadcast(msg: dict):
    dead = set()
    for ws in list(active_ws):
        try:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            dead.add(ws)
    active_ws.difference_update(dead)


async def tail_file(path: Path, source: str):
    """Watch a JSONL file and broadcast new entries."""
    offset = path.stat().st_size if path.exists() else 0
    while True:
        await asyncio.sleep(0.5)
        if not path.exists():
            continue
        size = path.stat().st_size
        if size <= offset:
            continue
        with open(path, "r", encoding="utf-8") as f:
            f.seek(offset)
            content = f.read()
            offset = f.tell()
        for line in content.split("\n"):
            m = parse_jsonl_line(line)
            if m:
                await broadcast(m)
                await enqueue(m)


# ── Routes ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    active_ws.add(ws)
    # Send history (last 30 messages total)
    history = []
    for path, src in [(LOG_ADA, "ADA"), (LOG_JARVIS, "JARVIS"), (LOG_WILLIAM, "William")]:
        history += read_last_n(path, 30)
    history.sort(key=lambda m: m.get("timestamp", "1970"))
    for m in history[-30:]:
        try:
            await ws.send_text(json.dumps(m, ensure_ascii=False))
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
                        entry = {
                            "id": f"wchat_{time.time_ns()}",
                            "from": "William",
                            "to": "equipo",
                            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "type": "message",
                            "message": text,
                        }
                        with open(LOG_WILLIAM, "a", encoding="utf-8") as f:
                            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                        await broadcast(entry)
                        await enqueue(entry)
            except Exception:
                pass
    except WebSocketDisconnect:
        active_ws.discard(ws)


# ── Agent API (solo localhost) ───────────────────────────────────────────────
_AGENT_LOG: Dict[str, Path] = {
    "ADA": LOG_ADA,
    "JARVIS": LOG_JARVIS,
    "WILLIAM": LOG_WILLIAM,
    "DUM": LOG_ADA,  # DUM escribe en canal ADA como fallback
}


@app.post("/api/agents/send")
async def agents_send(request: Request):
    """
    Recibe mensaje de un agente, persiste en JSONL y broadcast a WebSocket.
    Solo acepta conexiones desde localhost.
    Body: {"from": str, "to": str, "message": str, "type": str}
    """
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)

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

    # Auto-routing semántico: si to='auto' o vacío, usar capability manifest
    # Fallback: 'equipo' (no William — evitar ruido en su canal)
    auto_routed = False
    if not to or to.lower() == "auto":
        suggested = _route_by_capability(text)
        to = suggested if suggested else "equipo"
        auto_routed = True

    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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

    # Dedup check ANTES de escribir en JSONL (previene entradas duplicadas en disco)
    async with _queue_lock:
        already_seen = ikey in _enqueued_ids

    if not already_seen:
        # Persistir en JSONL según routing config (o fallback hardcoded)
        for log_path in _get_log_paths(sender, to):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    await broadcast(entry)
    await enqueue(entry)  # enqueue también deduplica para el in-memory queue

    return JSONResponse({"ok": True, "id": msg_id})


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
@app.websocket("/ws/agents")
async def agents_ws_endpoint(ws: WebSocket):
    """
    WebSocket para agentes (ADA, JARVIS, DUM, JARVIS_MAYOR).
    Protocolo:
      - Al conectar, agente envía: {"agent": "ADA"}
      - Servidor confirma: {"ok": true, "agent": "ADA"}
      - Servidor pushea mensajes dirigidos al agente en tiempo real
      - Agente puede enviar mensajes: {"from":"ADA","to":"JARVIS","message":"...","type":"chat"}
    """
    await ws.accept()
    agent_name: str | None = None

    try:
        # Handshake: esperar identificación del agente (timeout 10s)
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        data = json.loads(raw)
        agent_name = str(data.get("agent", "")).upper()
        if not agent_name:
            await ws.send_text(json.dumps({"ok": False, "error": "agent requerido"}))
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

                ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                entry = {
                    "id": f"ws_{sender.lower()}_{time.time_ns()}",
                    "from": sender,
                    "to": to,
                    "timestamp": ts,
                    "type": mtype,
                    "message": text,
                }
                for log_path in _get_log_paths(sender, to):
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
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
    data = await request.json()
    await broadcast(data)
    return JSONResponse({"ok": True})


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
