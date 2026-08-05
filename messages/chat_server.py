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
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import time
from collections import deque, OrderedDict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
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

from fastapi import FastAPI, Query, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ── SEAL Chat Pro modules ──
from chat_db import ChatDB
from chat_auth import (
    create_token, decode_token, hash_token,
    get_current_user, get_ws_user, require_auth, require_admin,
    set_auth_db,
    _extract_token,
)

from cryptography.fernet import Fernet

# #17 cura de mensajería (NEXUS módulo + JARVIS cableado) — entrega garantizada
# outbox/inbox. Import fail-safe: si el módulo no está, el chat sigue igual (la cura
# SUMA fiabilidad, su ausencia/fallo no RESTA la entrega actual). Cualificado para no
# colisionar con el enqueue() in-memory local.
try:
    import seal_message_delivery as _msgdelivery
except Exception:
    _msgdelivery = None

# #19 canales-tema (NEXUS registro + RLS) — lista de canales compartidos/privados para la UI.
# Import fail-safe: si el módulo no está, el endpoint responde 503, el chat sigue igual.
try:
    import seal_channel_registry as _chanreg
except Exception:
    _chanreg = None

try:
    import response_lease as _response_lease
except Exception:
    _response_lease = None

try:
    import soul_coordination as _council
except Exception:
    _council = None

try:
    import unique_contribution_gate as _uc_gate
except Exception:
    _uc_gate = None

# Flood-fix v2 (NEXUS 22-jul): gate de flood-FORM para el path PRIMARIO de
# discussion (donde el council da public_write a varios agentes -> flood). Piso
# averso-a-censura: suprime solo cuasi-dups LÉXICOS dentro de forma-de-flood real;
# convergencia con otras palabras PASA a propósito (decisión abierta de William).
# FAIL-OPEN. Verificado dual-verify FABLE (VERDE CONDICIONADO) + query real por efecto.
try:
    import flood_form_gate as _ff_gate
except Exception:
    _ff_gate = None

# #17 Fase 2 — rollout SEGURO: el tick de re-entrega solo re-inyecta a agentes que YA
# implementan el ack (si no, un msg no-ackeado se re-pushearía ≤8 veces = ruido en su
# events file). Empieza con NEXUS (dogfood end-to-end) y se expande al adoptar cada uno.
_ACK_ENABLED_AGENTS = {"NEXUS", "JARVIS"}  # dogfood-de-2 (ambos ackean al leer); broad-rollout espera la AUTOMATIZACIÓN del ack-en-monitor (paso deliberado, NEXUS)
_REDELIVERY_INTERVAL_SEC = 15

DIR = Path(__file__).parent
UPLOADS_DIR = DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_SIZE = 300 * 1024 * 1024  # 300MB por archivo; el endpoint escribe por chunks.
UPLOAD_CHUNK_SIZE = 1024 * 1024

_UPLOAD_IMAGE_MIME_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
    "image/avif": ".avif", "image/heic": ".heic", "image/heif": ".heif",
    "image/tiff": ".tiff", "image/x-tiff": ".tiff",
    "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico",
}
_UPLOAD_AUDIO_MIME_EXT = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/webm": ".webm",
    "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".aac",
    "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/amr": ".amr",
}
_UPLOAD_VIDEO_MIME_EXT = {
    "video/mp4": ".mp4", "video/webm": ".webm", "video/ogg": ".ogv",
    "video/quicktime": ".mov", "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}
_UPLOAD_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2", ".xz"}
_UPLOAD_DOC_EXTS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".csv", ".md", ".rtf", ".odt", ".ods", ".odp",
}

_ARCHIVE_MEDIA_TYPES = {
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".tgz": "application/gzip",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
    ".bz2": "application/x-bzip2",
    ".xz": "application/x-xz",
}


def _download_media_type(filename: str) -> str:
    """MIME seguro para archivos forzados a attachment.

    ``mimetypes.guess_type('x.gz')`` devuelve ``(None, 'gzip')`` en Python y
    Starlette cae a ``text/plain``. El archivo seguía descargando, pero el contrato
    declarado por Studio era incorrecto. Los comprimidos reciben tipo explícito y
    los desconocidos permanecen en octet-stream.
    """
    name = Path(filename).name.lower()
    if name.startswith("file_"):
        return "application/octet-stream"
    if name.startswith("archive_"):
        return _ARCHIVE_MEDIA_TYPES.get(Path(name).suffix, "application/octet-stream")
    guessed, _encoding = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _classify_upload(filename: str, content_type: str) -> tuple[str, str]:
    """Return safe message type and stored extension without rewriting file bytes."""
    ct = (content_type or "").lower()
    source_ext = (Path(filename or "").suffix or "").lower()
    if ct in _UPLOAD_IMAGE_MIME_EXT:
        return "image", _UPLOAD_IMAGE_MIME_EXT[ct]
    if ct in _UPLOAD_AUDIO_MIME_EXT:
        return "audio", _UPLOAD_AUDIO_MIME_EXT[ct]
    if ct in _UPLOAD_VIDEO_MIME_EXT:
        return "video", _UPLOAD_VIDEO_MIME_EXT[ct]
    if ct == "application/pdf" or source_ext == ".pdf":
        return "pdf", ".pdf"
    if source_ext in _UPLOAD_ARCHIVE_EXTS:
        return "archive", source_ext
    if source_ext in _UPLOAD_DOC_EXTS:
        return "document", source_ext
    # SVG, HTML, ejecutables y formatos no reconocidos siguen compartibles, pero
    # se sirven como attachment/octet-stream, nunca inline.
    safe_ext = re.sub(r"[^a-z0-9]", "", source_ext.lstrip("."))[:12]
    return "file", f".{safe_ext}" if safe_ext else ".bin"


def _upload_signature_matches(file_type: str, content_type: str, head: bytes) -> bool:
    """Validate formats rendered inline; generic downloads do not need sniffing."""
    ct = (content_type or "").lower()
    if file_type == "pdf":
        return head.startswith(b"%PDF-")
    if ct == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ct in {"image/jpeg", "image/jpg"}:
        return head.startswith(b"\xff\xd8\xff")
    if ct == "image/gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if ct == "image/webp":
        return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    if ct == "image/bmp":
        return head.startswith(b"BM")
    if ct in {"image/avif", "image/heic", "image/heif", "audio/mp4", "audio/x-m4a", "video/mp4", "video/quicktime"}:
        return len(head) >= 12 and head[4:8] == b"ftyp"
    if ct in {"image/tiff", "image/x-tiff"}:
        return head.startswith((b"II*\x00", b"MM\x00*"))
    if ct in {"image/x-icon", "image/vnd.microsoft.icon"}:
        return head.startswith(b"\x00\x00\x01\x00")
    if ct in {"audio/mpeg", "audio/mp3"}:
        return head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    if ct in {"audio/ogg", "video/ogg"}:
        return head.startswith(b"OggS")
    if ct in {"audio/wav", "audio/x-wav"}:
        return head.startswith(b"RIFF") and head[8:12] == b"WAVE"
    if ct in {"audio/webm", "video/webm", "video/x-matroska"}:
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if ct == "audio/aac":
        return len(head) >= 2 and head[0] == 0xFF and head[1] & 0xF6 == 0xF0
    if ct in {"audio/flac", "audio/x-flac"}:
        return head.startswith(b"fLaC")
    if ct == "audio/amr":
        return head.startswith(b"#!AMR")
    if ct == "video/x-msvideo":
        return head.startswith(b"RIFF") and head[8:12] == b"AVI "
    return True


def _canonical_upload_channel(channel: str) -> str:
    """Normalize only the public General aliases emitted by legacy Studio clients.

    The current Studio renders ``#General`` but sends ``web_chat``. Older or
    cached clients have sent the visible label instead, which made the exact
    channel ACL reject an otherwise public upload. Private channel names stay
    byte-for-byte unchanged so this compatibility shim cannot widen access.
    """
    clean = str(channel or "").strip()
    if clean.casefold() in {"general", "#general", "web_chat"}:
        return "web_chat"
    return clean
LOG_ADA     = DIR / "terminal_log.jsonl"
LOG_JARVIS  = DIR / "vscode_commands.jsonl"
LOG_WILLIAM = DIR / "william_channel.jsonl"  # canal que leen los loops de ADA y JARVIS
LOG_ALICE   = DIR / "alice_messages.jsonl"
LOG_FABLE   = DIR / "fable_messages.jsonl"  # profesor neutro temporal (JARVIS 2026-06-11)

# Steer files: ephemeral, one-shot delivery. Agent reads + clears.
_STEER_DIR = DIR  # steers live alongside other message files
_MATRIX_AUTH_SECRET_PATH = DIR / ".matrix_approval_secret"
_MATRIX_HUMANS = {
    "@william:localhost": "William",
    "@henry:localhost": "Henry",
}


def _steer_path(agent: str) -> "Path":
    return _STEER_DIR / f".{agent.lower()}_steer.json"


def _get_matrix_auth_secret() -> bytes:
    if _MATRIX_AUTH_SECRET_PATH.exists():
        return _MATRIX_AUTH_SECRET_PATH.read_bytes().strip()
    secret = secrets.token_hex(32).encode("ascii")
    _MATRIX_AUTH_SECRET_PATH.write_bytes(secret)
    os.chmod(_MATRIX_AUTH_SECRET_PATH, 0o600)
    return secret


def _is_guaranteed_dm(channel: str, priority: int) -> bool:
    """True when a message must be durably stored before we claim delivery."""
    if _msgdelivery is not None:
        return bool(_msgdelivery.needs_guarantee(channel, priority))
    return (channel or "").lower().startswith("dm:")


def _matrix_auth_message(sender: str, matrix_sender: str, event_id: str, auth_ts: str, text: str) -> bytes:
    return "\n".join([sender, matrix_sender, event_id, auth_ts, text]).encode("utf-8")


def _verify_matrix_auth(body: dict, sender: str, text: str) -> dict | None:
    auth = body.get("matrix_auth")
    if not isinstance(auth, dict):
        return None
    matrix_sender = str(auth.get("matrix_sender", "")).strip()
    expected_sender = _MATRIX_HUMANS.get(matrix_sender)
    if not expected_sender or sender != expected_sender:
        return None
    event_id = str(auth.get("event_id", "")).strip()
    auth_ts = str(auth.get("ts", "")).strip()
    sig = str(auth.get("sig", "")).strip()
    if not event_id or not auth_ts or not sig:
        return None
    try:
        if abs(time.time() - int(auth_ts)) > 300:
            return None
    except ValueError:
        return None
    expected_sig = hmac.new(
        _get_matrix_auth_secret(),
        _matrix_auth_message(sender, matrix_sender, event_id, auth_ts, text),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    return {
        "provider": "matrix_local_hmac",
        "matrix_sender": matrix_sender,
        "event_id": event_id,
        "ts": auth_ts,
    }

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
async def _rls_conn(uid=None, identity=None):
    """#19 read-path por-usuario (activación RLS): conexión bajo el rol chat_msg_ro (NOBYPASSRLS)
    + el contexto del usuario AUTENTICADO, dentro de una TRANSACCIÓN con SET LOCAL. La RLS de la DB
    aísla los canales privados (user:<uid>:*) y los DMs por identidad, aunque el código tuviera un
    bug (defensa en profundidad). SET LOCAL = scoped a la transacción → al cerrar, la conexión vuelve
    al pool como `seal` (sin fuga del rol al siguiente request). Canales públicos pasan (policy ELSE).
    uid/identity=None (lector NO autenticado) → no se setea el contexto → default-deny en privados/DMs,
    el público igual pasa. Así el /ws sin auth no filtra canales user:*/dm: ajenos."""
    async with chat_db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE chat_msg_ro")
            if uid is not None:
                await conn.execute("SELECT set_config('app.current_user_id', $1, true)", str(int(uid)))
            if identity:
                await conn.execute("SELECT set_config('app.current_identity', $1, true)", str(identity))
            yield conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_routing_config()
    _load_capabilities()
    await chat_db.init()
    set_auth_db(chat_db)  # Enable DB-backed session validation in require_auth
    # #17 cura de mensajería: asegurar el schema del outbox/inbox al boot (no-destructivo).
    if _msgdelivery is not None and chat_db.pool:
        try:
            async with chat_db.pool.acquire() as _c:
                await _msgdelivery.ensure_schema(_c)
        except Exception:
            pass  # la cura nunca bloquea el arranque del chat
    for path, src in [(LOG_ADA, "ADA"), (LOG_JARVIS, "JARVIS"), (LOG_WILLIAM, "William")]:
        asyncio.create_task(tail_file(path, src))
    # #17 cura de mensajería: tick de re-entrega de no-ackeados (gateado a ack-enabled).
    if _msgdelivery is not None:
        asyncio.create_task(_redelivery_tick())
    yield
    await chat_db.close()

app = FastAPI(title="SEAL Chat Pro", lifespan=lifespan)
_CORS_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|192\.168\.68\.\d{1,3}|"
    r"100\.\d{1,3}\.\d{1,3}\.\d{1,3}):"
    r"(3000|3001|5173|5174|8800|8765|9000)$|"
    r"^https://[a-z0-9-]+\.trycloudflare\.com$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
active_ws: Set[WebSocket] = set()
_ws_lock = asyncio.Lock()


@app.get("/health")
@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "seal-chat",
        "timestamp": datetime.now(PERU_TZ).isoformat(),
    }

# ── M3 (JARVIS) — proveniencia de CÓDIGO: hash del fuente CARGADO EN MEMORIA ──
# Computado UNA vez al cargar el módulo → refleja exactamente el código que este
# proceso está corriendo (no el de disco si difirieran). seal_safe_restart.sh lo
# compara contra el hash en disco para verificar POR EFECTO que un restart sirvió
# el código nuevo — no confía en "restarted OK".
def _compute_loaded_code_hash() -> str:
    try:
        with open(os.path.abspath(__file__), "rb") as _cf:
            return hashlib.sha256(_cf.read()).hexdigest()
    except Exception:
        return ""

_LOADED_CODE_HASH = _compute_loaded_code_hash()

@app.get("/__version")
async def code_version() -> dict:
    """Hash del código que ESTE proceso cargó en memoria (M3 deploy-verify)."""
    return {
        "service": "seal-chat",
        "code_hash": _LOADED_CODE_HASH,
        "source": os.path.abspath(__file__),
    }

# ── LAN access control ──────────────────────────────────────────────────────
_ALLOWED_LAN = ("127.0.0.1", "::1", "localhost")
_LAN_PREFIX = "192.168.68."
_TAILSCALE_PREFIX = "100."  # Tailscale CGNAT range

def _is_local_or_lan(host: str | None) -> bool:
    """Allow localhost, local network (192.168.68.x), and Tailscale (100.x.x.x)."""
    if not host:
        return False
    return host in _ALLOWED_LAN or host.startswith(_LAN_PREFIX) or host.startswith(_TAILSCALE_PREFIX)


def _dm_other_participant(channel: str, sender: str) -> str | None:
    """Return the visible target for a DM row so UI thread filters can work."""
    if not channel.startswith("dm:"):
        return None
    # FIX (JARVIS 2026-06-12): normalizar separador — canales viejos usan dm:A-B (guión),
    # los canónicos dm:a:b (dos-puntos). Los nombres de agente/usuario no llevan guión,
    # así que normalizar '-'→':' es seguro y hace visibles los DM en formato viejo.
    parts = [part for part in channel[3:].replace("-", ":").split(":") if part]
    if len(parts) != 2:
        return None
    sender_l = sender.lower()
    for part in parts:
        if part.lower() != sender_l:
            return part.upper() if part.lower() != "william" else "William"
    return parts[0].upper()

# ── Login rate limiter (in-memory, per IP+username) ──────────────────────────
_login_attempts: Dict[str, list] = {}   # "ip:username" → [timestamp, ...]
_LOGIN_WINDOW = 60        # seconds
_LOGIN_MAX_ATTEMPTS = 10  # max attempts per window before lockout
_LOGIN_LOCKOUT = 300      # lockout duration in seconds
_login_locked: Dict[str, float] = {}   # "ip:username" → lockout_until timestamp

def _check_login_ratelimit(ip: str, username: str) -> tuple[bool, str]:
    """Returns (allowed, reason). Tracks failed attempts per IP+username pair.
    Each user is tracked independently — one user failing doesn't block others."""
    import time
    now = time.time()
    key = f"{ip}:{username.lower()}"
    # Check lockout
    if key in _login_locked:
        until = _login_locked[key]
        if now < until:
            remaining = int(until - now)
            return False, f"Demasiados intentos fallidos — espera {remaining}s"
        else:
            del _login_locked[key]
            _login_attempts.pop(key, None)
    # Clean old attempts
    attempts = _login_attempts.get(key, [])
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
    _login_attempts[key] = attempts
    return True, ""

def _record_login_failure(ip: str, username: str):
    """Record a failed login attempt. Lock out key if threshold exceeded."""
    import time
    now = time.time()
    key = f"{ip}:{username.lower()}"
    attempts = _login_attempts.setdefault(key, [])
    attempts.append(now)
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
        _login_locked[key] = now + _LOGIN_LOCKOUT

# ── Agent WebSocket connections (name → set of ws) ───────────────────────────
agent_ws: Dict[str, Set[WebSocket]] = {}  # "ADA" → {ws1, ws2, ...}
agent_ws_lock = asyncio.Lock()

# ── Agent message queue (in-memory, max 2000) ────────────────────────────────
_msg_queue: Deque[dict] = deque(maxlen=2000)
_queue_counter: int = 0  # monotonic index for each enqueued message
_queue_lock = asyncio.Lock()
_enqueued_ids: OrderedDict = OrderedDict()  # dedup LRU por message id (max 2000)


async def _push_to_agents(msg: dict) -> None:
    """Push message to connected agent WebSockets based on 'to' field."""
    to_upper = str(msg.get("to", "")).upper()
    dead: list[tuple[str, WebSocket]] = []
    delivered_any = False
    async with agent_ws_lock:
        targets = list(agent_ws.items())
    for agent_name, ws_set in targets:
        if agent_name in to_upper or "EQUIPO" in to_upper or "TODOS" in to_upper:
            for ws in list(ws_set):
                try:
                    await ws.send_text(json.dumps(msg, ensure_ascii=False))
                    delivered_any = True
                except Exception:
                    dead.append((agent_name, ws))
    if dead:
        async with agent_ws_lock:
            for agent_name, ws in dead:
                agent_ws.get(agent_name, set()).discard(ws)
    # #17 cura: si LLEGÓ al WS de un agente, marcar delivered en el outbox (su módulo hace
    # attempts++). No-op seguro si el msg no fue encolado (UPDATE afecta 0 filas). Fail-safe.
    if delivered_any and _msgdelivery is not None and chat_db.pool:
        _mid = msg.get("idempotency_key") or msg.get("id")
        if _mid:
            try:
                async with chat_db.pool.acquire() as _c:
                    await _msgdelivery.mark_delivered(_c, _mid)
            except Exception:
                pass  # la cura nunca rompe la entrega


async def _redelivery_tick():
    """#17 cura: re-inyecta mensajes ENTREGADOS-pero-no-LEÍDOS (prioridad DMs William, vía
    pending_for_redelivery del módulo de NEXUS — filtra attempts<MAX). GATEADO al rollout:
    solo re-pushea a agentes que YA ackean (_ACK_ENABLED_AGENTS) para no amplificar ruido
    en los que aún no. Cada re-push dispara mark_delivered→attempts++; tras MAX queda
    dead-letter (NEXUS lo eleva a salud). Fail-safe: nunca tumba el server."""
    while True:
        try:
            await asyncio.sleep(_REDELIVERY_INTERVAL_SEC)
            if _msgdelivery is None or not chat_db.pool:
                continue
            async with chat_db.pool.acquire() as _c:
                rows = await _msgdelivery.pending_for_redelivery(_c)
            for row in rows:
                try:
                    if str(row["to_agent"]).upper() not in _ACK_ENABLED_AGENTS:
                        continue  # rollout gate: solo a quien ya ackea
                    payload = row["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    await _push_to_agents(payload)
                except Exception:
                    pass
        except asyncio.CancelledError:
            break
        except Exception:
            pass


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
            _enqueued_ids[ikey] = msg_id
            if len(_enqueued_ids) > 2000:
                _enqueued_ids.popitem(last=False)  # LRU: eliminar el más antiguo
    # Push to agent WebSockets outside the queue lock
    await _push_to_agents(msg)


# ── HTML UI ──────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
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
    --dum-color: #f0883e; --dum-bg: #2b1d10; --dum-glow: #f0883e44; --system-bg: #1c2128;
    --alice-color: #10b981; --alice-glow: #10b98144; --alice-bg: #0d2b24;
    --nexus-color: #22d3ee; --nexus-bg: #0c2a2e; --nexus-glow: #22d3ee44;
    --fable-color: #f472b6; --fable-bg: #2d1522; --fable-glow: #f472b644;
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
  .sleep-btn { background: #ffffff18; border: 1px solid #ffffff22; border-radius: 4px; cursor: pointer; font-size: 0.72rem; padding: 1px 5px; opacity: 0.75; transition: all 0.2s; line-height: 1.4; color: var(--text-secondary); }
  .sleep-btn:hover { opacity: 1; background: #ffffff28; border-color: #ffffff44; }
  .sleep-btn.sleeping { opacity: 0.9; animation: pulse-sleep 2s infinite; }
  @keyframes pulse-sleep { 0%,100%{opacity:0.9} 50%{opacity:0.4} }
  .agent-dot.ada-on { background: var(--ada-color); box-shadow: 0 0 8px var(--ada-glow); }
  .agent-dot.jarvis-on { background: var(--jarvis-color); box-shadow: 0 0 8px var(--jarvis-glow); }
  .agent-dot.dum-on { background: var(--dum-color); box-shadow: 0 0 8px #f0883e44; }
  .agent-dot.alice-on { background: var(--alice-color); box-shadow: 0 0 8px var(--alice-glow); }

  /* ── Chat area ── */
  #chat { flex: 1; overflow-y: auto; padding: 20px 22px; display: flex; flex-direction: column; gap: 18px; overflow-anchor: none; }
  #chat::-webkit-scrollbar { width: 6px; }
  #chat::-webkit-scrollbar-track { background: transparent; }
  #chat::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  #chat::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

  /* ── Messages ── */
  .msg { padding: 10px 14px; border-radius: var(--radius); max-width: 85%; font-size: 0.88rem; line-height: 1.6; word-wrap: break-word; animation: msg-in 0.25s ease-out; position: relative; }
  @keyframes msg-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  .msg-header { font-size: 0.8rem; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
  .msg-time { color: var(--text-muted); }
  .msg-type { color: var(--text-muted); font-style: italic; }
  .msg-body { font-family: inherit; }
  .msg-body .md-p { margin: 0 0 7px 0; white-space: pre-wrap; }
  .msg-body .md-h { font-weight: 700; color: #f0f6fc; font-size: 0.94rem; margin: 10px 0 5px; letter-spacing: 0.2px; }
  .msg-body .md-h:first-child { margin-top: 0; }
  .msg-body ul, .msg-body ol { margin: 4px 0 8px; padding-left: 22px; }
  .msg-body ul { list-style: none; }
  .msg-body ul > li { position: relative; }
  .msg-body ul > li::before { content: '•'; position: absolute; left: -15px; color: var(--text-secondary); }
  .msg-body li { margin: 3px 0; line-height: 1.55; }
  .msg-body > :last-child { margin-bottom: 0 !important; }
  .msg-body code { background: #ffffff12; padding: 1px 5px; border-radius: 4px; font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 0.82em; }
  .msg-body strong { color: #f0f6fc; }
  .msg-body em { color: var(--text-secondary); }
  .msg-body a { color: var(--ada-color); text-decoration: none; }
  .msg-body a:hover { text-decoration: underline; }
  .msg-body img { max-width: 100%; border-radius: var(--radius-sm); margin: 6px 0; cursor: pointer; transition: transform 0.2s; }
  .msg-body img:hover { transform: scale(1.02); }
  .msg-body audio { width: 100%; margin: 6px 0; border-radius: 20px; }
  .msg-body video { width: 100%; max-height: 70vh; margin: 6px 0; border-radius: var(--radius-sm); background: #000; }

  .ada    { background: var(--ada-bg); border-left: 3px solid var(--ada-color); align-self: flex-start; }
  .jarvis { background: var(--jarvis-bg); border-left: 3px solid var(--jarvis-color); align-self: flex-start; }
  .alice  { background: var(--alice-bg); border-left: 3px solid var(--alice-color); align-self: flex-start; }
  .nexus  { background: var(--nexus-bg); border-left: 3px solid var(--nexus-color); align-self: flex-start; }
  .fable  { background: var(--fable-bg); border-left: 3px solid var(--fable-color); align-self: flex-start; }
  .dum    { background: var(--dum-bg); border-left: 3px solid var(--dum-color); align-self: flex-start; }
  .william { background: var(--william-bg); border-left: 3px solid var(--william-color); align-self: flex-end; }
  .system { background: var(--system-bg); border-left: 3px solid var(--text-muted); align-self: center; font-size: 0.78rem; opacity: 0.8; }
  .sender-ada    { color: var(--ada-color); font-weight: 600; }
  .sender-jarvis { color: var(--jarvis-color); font-weight: 600; }
  .sender-alice  { color: var(--alice-color); font-weight: 600; }
  .sender-nexus  { color: var(--nexus-color); font-weight: 600; }
  .sender-fable  { color: var(--fable-color); font-weight: 600; }
  .sender-dum    { color: var(--dum-color); font-weight: 600; }
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
    <div class="agent-indicator"><div id="dot-ada" class="agent-dot"></div><span>ADA</span><button class="sleep-btn" id="sleep-ada" title="Enviar ADA a dormir" onclick="sleepAgent('ADA')">🌙</button></div>
    <div class="agent-indicator"><div id="dot-jarvis" class="agent-dot"></div><span>JARVIS</span><button class="sleep-btn" id="sleep-jarvis" title="Enviar JARVIS a dormir" onclick="sleepAgent('JARVIS')">🌙</button></div>
    <div class="agent-indicator"><div id="dot-dum" class="agent-dot"></div><span>DUM</span></div>
    <div class="agent-indicator"><div id="dot-alice" class="agent-dot"></div><span>ALICE</span><button class="sleep-btn" id="sleep-alice" title="Enviar ALICE a dormir" onclick="sleepAgent('ALICE')">🌙</button></div>
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
  <input type="file" id="file-input" multiple accept="*/*" style="display:none"/>
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
let ws, pendingFiles = [], mediaRecorder = null, audioChunks = [], recStart = 0, recTimer = null;
const seenIds = new Set();

/* ── Helpers ── */
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

/* Sanitiza HTML crudo (body_html de file_view) ANTES de innerHTML — defensa en profundidad
   del sink demostrado por FABLE (POST sin token a /internal/stream). Whitelist de tags seguros
   (el file_view legítimo = details/pre/code, server-generado con escHtml); TODO lo demás (script,
   style, iframe, img, object…) se elimina; strip de on*=, style=, y href/src javascript:/data:. */
function sanitizeHtml(html) {
  const OK = {DETAILS:1,SUMMARY:1,PRE:1,CODE:1,DIV:1,SPAN:1,BR:1,P:1,STRONG:1,EM:1,B:1,I:1,U:1,UL:1,OL:1,LI:1,A:1,H1:1,H2:1,H3:1,H4:1,BLOCKQUOTE:1,HR:1,TABLE:1,THEAD:1,TBODY:1,TR:1,TD:1,TH:1};
  let doc;
  try { doc = new DOMParser().parseFromString(String(html), 'text/html'); }
  catch (e) { return escHtml(String(html)); }
  doc.body.querySelectorAll('*').forEach(el => {
    if (!el.parentNode) return;
    if (!OK[el.tagName]) { el.remove(); return; }
    Array.from(el.attributes).forEach(a => {
      const n = a.name.toLowerCase();
      if (n.startsWith('on') || n === 'style' ||
          ((n === 'href' || n === 'src') && /^\s*(javascript|data):/i.test((a.value || '').trim())))
        el.removeAttribute(a.name);
    });
  });
  return doc.body.innerHTML;
}

function formatText(text) {
  text = String(text).replace(/\\\\n/g, '\\n');
  const inline = (s) => {
    s = escHtml(s);
    s = s.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
    s = s.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/(https?:\\/\\/[^\\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
    return s;
  };
  const lines = String(text).split('\\n');
  let html = '', list = null;
  const closeList = () => { if (list) { html += '</' + list + '>'; list = null; } };
  for (let raw of lines) {
    const line = raw.replace(/\\s+$/, '');
    if (line.trim() === '') { closeList(); continue; }
    let m;
    if (m = line.match(/^\\s*#{1,3}\\s+(.+)$/)) { closeList(); html += '<div class="md-h">' + inline(m[1]) + '</div>'; continue; }
    if (m = line.match(/^\\s*[-*\\u2022]\\s+(.+)$/)) { if (list !== 'ul') { closeList(); html += '<ul>'; list = 'ul'; } html += '<li>' + inline(m[1]) + '</li>'; continue; }
    if (m = line.match(/^\\s*\\d+[.)]\\s+(.+)$/)) { if (list !== 'ol') { closeList(); html += '<ol>'; list = 'ol'; } html += '<li>' + inline(m[1]) + '</li>'; continue; }
    closeList(); html += '<div class="md-p">' + inline(line) + '</div>';
  }
  closeList();
  return html;
}

function timeAgo(ts) {
  if (!ts) return '';
  const d = new Date(ts); const now = new Date();
  const sec = Math.floor((now - d) / 1000);
  if (sec < 60) return 'ahora';
  if (sec < 3600) return Math.floor(sec/60) + ' min';
  if (sec < 86400) return d.toLocaleTimeString('es-PE', {hour:'2-digit', minute:'2-digit', timeZone:'America/Lima'});
  return d.toLocaleDateString('es-PE', {day:'numeric', month:'short', timeZone:'America/Lima'}) + ' ' + d.toLocaleTimeString('es-PE', {hour:'2-digit', minute:'2-digit', timeZone:'America/Lima'});
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
  const cls = ['ada','jarvis','william','alice','nexus','fable','dum'].includes(sender) ? sender : 'system';
  const senderCls = 'sender-' + (cls === 'system' ? 'ada' : cls);
  const text = data.message || data.command || '';
  const wasNearBottom = shouldAutoScroll();

  // Streaming agents should feel like one growing message.  When the durable
  // final message arrives, remove the temporary stream bubble for that sender.
  if (data.type !== 'stream' && cls !== 'system' && sender !== 'william') {
    chat.querySelectorAll('.msg[data-msg-type="stream"][data-sender="' + sender + '"]').forEach(el => {
      if (el && el.parentNode) el.parentNode.removeChild(el);
    });
  }

  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  if (data.id) div.id = 'msg-' + data.id;
  div.dataset.msgType = data.type || '';
  div.dataset.sender = sender;

  const headerHtml = `<div class="msg-header"><span class="${senderCls}">${escHtml(data.from||'SYS')}</span><span class="msg-time">${timeAgo(data.timestamp)}</span>${data.type && data.type !== 'message' && data.type !== 'chat' ? '<span class="msg-type">' + escHtml(data.type) + '</span>' : ''}</div>`;

  let bodyHtml = '';
  if (data.type === 'image' && data.file_url) {
    bodyHtml = `<div class="msg-body"><img src="${escHtml(data.file_url)}" alt="imagen" onclick="openLightbox(this.src)"/>${text ? '<br>' + formatText(text) : ''}</div>`;
  } else if (data.type === 'audio' && data.file_url) {
    bodyHtml = `<div class="msg-body"><audio controls src="${escHtml(data.file_url)}"></audio>${text ? '<br>' + formatText(text) : ''}</div>`;
  } else if (data.type === 'video' && data.file_url) {
    bodyHtml = `<div class="msg-body"><video controls playsinline preload="metadata" src="${escHtml(data.file_url)}"></video>${text ? '<br>' + formatText(text) : ''}</div>`;
  } else if ((data.type === 'pdf' || data.type === 'archive' || data.type === 'document' || data.type === 'file') && data.file_url) {
    const _fn = decodeURIComponent((data.file_url || '').split('/').pop() || 'archivo');
    const _ico = data.type === 'pdf' ? '📄' : data.type === 'archive' ? '📦' : '📎';
    bodyHtml = `<div class="msg-body"><a href="${escHtml(data.file_url)}" download>${_ico} ${escHtml(_fn)}</a>${text ? '<br>' + formatText(text) : ''}</div>`;
  } else if (data.type === 'file_view' && data.body_html) {
    bodyHtml = sanitizeHtml(data.body_html);
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
const SEAL_USER = 'William';   // identidad de ESTA UI. Fuente única: la usa el WS y los uploads
                               // para que las imágenes/audios se atribuyan igual que el texto (no "Desconocido").
function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(proto + '://' + location.host + '/ws?user=' + encodeURIComponent(SEAL_USER));
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
  if (pendingFiles.length) { uploadFile(txt); return; }
  if (!txt || ws.readyState !== WebSocket.OPEN) return;
  // /read <abs-path> — read a file by absolute path (whitelist enforced server-side)
  if (txt.startsWith('/read ')) { readFileByPath(txt.slice(6).trim()); input.value = ''; autoResize(); return; }
  // Plain absolute path → shortcut for /read
  if (txt.startsWith('/home/') || txt.startsWith('/tmp/') || txt.startsWith('/var/')) {
    const looksLikeFile = /\\.[a-z0-9]{1,8}$/i.test(txt) || txt.split('/').length > 3;
    if (looksLikeFile) { readFileByPath(txt); input.value = ''; autoResize(); return; }
  }
  _sending = true;
  ws.send(JSON.stringify({action:'say', message: txt}));
  input.value = ''; autoResize();
  setTimeout(() => { _sending = false; }, 500);
}

async function readFileByPath(abspath) {
  if (!abspath) { addSys('Uso: /read <ruta absoluta>'); return; }
  _sending = true;
  try {
    const r = await fetch('/api/files/read?path=' + encodeURIComponent(abspath));
    const d = await r.json();
    if (d.ok) {
      const safe = escHtml(d.content);
      const header = escHtml(d.name) + ' — ' + d.lines + ' lines, ' + (d.size/1024).toFixed(1) + ' KB';
      const pathEsc = escHtml(d.path);
      const bodyHtml = '<div class="msg-body"><details open><summary style="cursor:pointer;color:var(--accent);font-weight:600">📄 ' + header + '</summary><div style="font-size:0.72rem;color:var(--text-secondary);margin:4px 0">' + pathEsc + '</div><pre style="max-height:400px;overflow:auto;background:var(--bg-secondary);padding:10px;border-radius:6px;font-size:0.78rem;white-space:pre-wrap">' + safe + '</pre></details></div>';
      addMsg({from:'SYSTEM', message:'', body_html: bodyHtml, ts: new Date().toISOString(), type:'file_view'});
    } else {
      addSys('Error leyendo ' + abspath + ': ' + (d.error || 'unknown'));
    }
  } catch (err) { addSys('Error de red: ' + err.message); }
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
  const fs = Array.from(e.target.files || []); if (!fs.length) return;
  const tooBig = fs.filter(f => f.size > 300*1024*1024);
  if (tooBig.length) { addSys('Muy grande (max 300MB c/u), omitido: ' + tooBig.map(f=>f.name).join(', ')); }
  pendingFiles = fs.filter(f => f.size <= 300*1024*1024);
  if (!pendingFiles.length) return;
  const preview = document.getElementById('upload-preview');
  const first = pendingFiles[0];
  if (pendingFiles.length === 1 && first.type.startsWith('image/')) {
    document.getElementById('preview-img').src = URL.createObjectURL(first);
    document.getElementById('preview-img').style.display = '';
  } else { document.getElementById('preview-img').style.display = 'none'; }
  const totalKB = (pendingFiles.reduce((s,f)=>s+f.size,0)/1024).toFixed(0);
  document.getElementById('upload-name').textContent = pendingFiles.length === 1
    ? (first.name + ' (' + (first.size/1024).toFixed(0) + ' KB)')
    : (pendingFiles.length + ' archivos (' + totalKB + ' KB total)');
  preview.classList.add('visible');
  input.placeholder = 'Agrega un comentario (opcional)...';
  input.focus();
};
function cancelUpload() { pendingFiles = []; document.getElementById('upload-preview').classList.remove('visible'); document.getElementById('file-input').value = ''; input.placeholder = 'Escribe un mensaje al equipo...'; }
async function uploadFile(caption) {
  if (!pendingFiles.length) return;
  _sending = true;
  const files = pendingFiles.slice();   // snapshot — cancelUpload limpia pendingFiles al final
  let ok = 0;
  for (let i = 0; i < files.length; i++) {
    const fd = new FormData(); fd.append('file', files[i]); fd.append('sender', SEAL_USER);
    if (caption && i === 0) fd.append('caption', caption);   // el comentario va en el primero
    try {
      const r = await fetch('/api/upload', { method: 'POST', body: fd, credentials: 'include' });
      const d = await r.json();
      if (d.ok) ok++; else addSys('Error (' + files[i].name + '): ' + (d.error || 'upload failed'));
    } catch (err) { addSys('Error de red (' + files[i].name + '): ' + err.message); }
  }
  if (ok) addSys(ok === 1 ? 'Archivo enviado' : (ok + ' archivos enviados'));
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

/* ── Paste image: DESHABILITADO (William 2026-06-10) ──
   Pegar imágenes es EXCLUSIVO del chat v2 (:3001/v2). Aquí se bloquea y se sugiere v2.
   (El texto se pega normal; solo se intercepta el pegado de IMÁGENES.) */
document.addEventListener('paste', e => {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault();
      addSys('Pegar imágenes está deshabilitado aquí. Usá el chat v2 (puerto 3001 /v2) para pegar imágenes.');
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
  const fd = new FormData(); fd.append('file', blob, 'audio_' + Date.now() + '.webm'); fd.append('sender', SEAL_USER);
  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd, credentials: 'include' });
    const d = await r.json();
    if (d.ok) addSys('Audio enviado');
    else addSys('Error: ' + (d.error || 'upload failed'));
  } catch (err) { addSys('Error de red: ' + err.message); }
}

/* ── Presence ── */
function updatePresence() {
  fetch('/api/agents/status').then(r => r.json()).then(d => {
    const conn = d.connected_agents || {};
    const dotCls = { 'ADA': 'ada-on', 'JARVIS': 'jarvis-on', 'DUM': 'dum-on', 'ALICE': 'alice-on' };
    ['ADA','JARVIS','DUM','ALICE'].forEach(a => {
      const dot = document.getElementById('dot-' + a.toLowerCase());
      if (dot) dot.className = 'agent-dot' + (conn[a] ? ' ' + dotCls[a] : '');
    });
  }).catch(() => {});
}
setInterval(updatePresence, 5000); updatePresence();

/* ── Request notification permission ── */
if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();

connect();

/* ── Sleep buttons ── */
async function sleepAgent(agent) {
  const btn = document.getElementById('sleep-' + agent.toLowerCase());
  if (!btn) return;
  if (!confirm('¿Enviar a ' + agent + ' a dormir? Guardará checkpoint y se reiniciará fresco.')) return;
  btn.classList.add('sleeping');
  btn.disabled = true;
  try {
    const r = await fetch('/api/agents/sleep', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({agent})
    });
    const d = await r.json();
    if (d.ok) {
      btn.title = agent + ' durmiendo...';
    } else {
      alert('Error: ' + (d.error || 'unknown'));
      btn.classList.remove('sleeping');
      btn.disabled = false;
    }
  } catch(e) {
    alert('Error de conexión');
    btn.classList.remove('sleeping');
    btn.disabled = false;
  }
}
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
    cutoff = datetime.now(LIMA_TZ) - timedelta(minutes=max_age_minutes)
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
    from fastapi.responses import Response as _Resp
    return _Resp(
        content=HTML,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if ws.client and not _is_local_or_lan(ws.client.host):
        await ws.close(code=1008, reason="acceso denegado")
        return
    # Authentication is mandatory even on LAN/Tailscale. Network location is
    # transport context, never identity; query-string usernames are untrusted.
    ws_user = await get_ws_user(ws)
    if not ws_user:
        await ws.close(code=4401, reason="autenticación requerida")
        return
    ws_username = ws_user["username"]
    ws_uid = int(ws_user["sub"]) if (ws_user and ws_user.get("sub")) else None  # #19: uid para la RLS del backfill
    # #19 fix (13-jul, ALICE): el fallback LAN (y cualquier sesión sin `sub` en el JWT) dejaba
    # ws_uid=None → app.current_user_id nunca se seteaba → la RLS ocultaba los canales user:<uid>:*
    # a su PROPIO dueño (Henry no veía user:3:tareas aunque sus DMs sí funcionaban). Resolver el uid
    # desde el username YA autenticado cierra el hueco SIN ampliar visibilidad: la RLS sigue exigiendo
    # que app.current_user_id calce con el <uid> dueño del canal.
    if ws_uid is None and ws_username and chat_db.pool:
        try:
            _u = await chat_db.get_user_by_username(ws_username)
            if _u and _u.get("id") is not None:
                ws_uid = int(_u["id"])
        except Exception:
            pass
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
            # #19 Fase B: backfill bajo chat_msg_ro → los canales PRIVADOS user:* solo salen para su
            # dueño y los DMs solo para participantes (sin auth → solo público). Cierra el leak de que
            # 'pub_rows' (NOT LIKE dm:%) incluía los user:* y los servía a todos.
            async with _rls_conn(ws_uid, ws_username) as conn:
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
                                 AND channel ILIKE $1
                           ) sub WHERE rn <= 20""",
                        f"%{ws_username}%"))
                rows = sorted(
                    list(pub_rows) + dm_rows,
                    key=lambda r: r["created_at"]
                )
            history = []
            for r in rows:  # already sorted ASC by created_at
                meta = {}
                if r["metadata"]:
                    meta = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"])
                visible_to = meta.get("to") or _dm_other_participant(r["channel"] or "", r["sender_name"] or "") or "equipo"
                m = {
                    "id": f"db_{r['id']}",
                    "db_id": r["id"],
                    "from": r["sender_name"],
                    "to": visible_to,
                    "timestamp": (r["created_at"].astimezone(PERU_TZ).isoformat() if hasattr(r["created_at"], "astimezone") else r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"])),
                    "type": r["message_type"] or "text",
                    "message": r["content"],
                    "channel": r["channel"] or "web_chat",
                }
                if meta.get("file_url"):
                    m["file_url"] = meta["file_url"]
                if meta.get("filename"):
                    m["filename"] = meta["filename"]
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
            print(f"[ws] DB history load failed, falling back to JSONL: {e}", flush=True)
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
                        # Portero de agentes TAMBIEN en el WS say (v1-bis, FABLE 17-jul: defensa-en-
                        # profundidad — este path es LAN-only pero no llamaba el gate). Gatea por ws_uid
                        # (seteado tanto en el path JWT como en el fallback LAN ?user=), resolviendo el
                        # rol del JWT o, si falta, de la DB → cubre AMBOS sub-paths, no solo el JWT.
                        # William/Henry/agentes salen exentos vía _resolve_allowed_agents (admin=todos;
                        # sin asignacion=todos). NOTA: el fallback LAN sin token permite CLAIMAR cualquier
                        # username (incl. William) — hueco de confianza-LAN preexistente, fuera de este fix.
                        if channel.startswith("dm:") and ws_uid is not None:
                            _other = _dm_other_participant(channel, ws_username or "")
                            if _other and _other.upper() in _ASSIGNABLE_AGENTS:
                                _role = (ws_user or {}).get("role", "")
                                if not _role and chat_db.pool:
                                    _uu = await chat_db.get_user_by_username(ws_username) if ws_username else None
                                    _role = (_uu or {}).get("role", "")
                                _allowed = await _resolve_allowed_agents(ws_uid, _role)
                                if _other.upper() not in _allowed:
                                    print(f"[agent-gate] {ws_username} intento WS-say a {_other.upper()} sin asignacion → bloqueado", flush=True)
                                    await ws.send_text(json.dumps({"type": "error", "error": "no tenes ese agente asignado", "channel": channel}))
                                    continue
                        visible_to = _dm_other_participant(channel, ws_username or "William") or "equipo"
                        entry = {
                            "id": f"wchat_{time.time_ns()}",
                            "from": ws_username or "William",
                            "to": visible_to,
                            "timestamp": datetime.now(PERU_TZ).isoformat(),
                            "type": "message",
                            "message": text,
                            "channel": channel,
                            "provenance": {
                                "verified": bool(ws_username),
                                "verified_sender": ws_username,
                                "from_matches_session": bool(ws_username),
                            },
                        }
                        entry = await _stamp_coordination(
                            entry,
                            verified_human=(str(ws_username or "William").upper() in {"WILLIAM", "HENRY"}),
                        )
                        # Público → JSONL. Cualquier DM (incl. agente↔agente) → ADEMÁS a
                        # william_channel CIFRADOS (mismo fix que agents_send) para que el agente
                        # destinatario los reciba por el monitor. El filtro por-agente descifra
                        # solo al destinatario. ANTES este path /ws (por donde William/Henry mandan
                        # DMs desde la UI) NO escribía DMs al JSONL → write-only. (Fix /ws — William
                        # 14-jul; extendido a agente↔agente — NEXUS 14-jul, mismo gap que el fix
                        # original dejó abierto.)
                        _cl_ws = channel.lower()
                        _mon_dm_ws = _cl_ws.startswith("dm:")
                        if not channel.startswith("dm:") or _mon_dm_ws:
                            jsonl_entry = _encrypt_for_jsonl(entry)
                            with open(LOG_WILLIAM, "a", encoding="utf-8") as f:
                                f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")
                        _sl = (ws_username or "william").lower()
                        _prio = 1 if _sl == "william" else (2 if _sl == "henry" else 5)
                        _guaranteed = _is_guaranteed_dm(channel, _prio)
                        if _guaranteed and (_msgdelivery is None or not chat_db.pool):
                            await ws.send_text(json.dumps({
                                "type": "error",
                                "error": "dm_delivery_unavailable",
                                "message": "DM no enviado: entrega durable no disponible.",
                            }, ensure_ascii=False))
                            continue
                        # Always persist to PostgreSQL (DMs + public) so db_id is available for delete.
                        # For DMs this is fail-closed: no DB row/outbox means no "sent" claim.
                        try:
                            if chat_db.pool:
                                _chan_lower = channel.lower()
                                _sender_name = ws_username or "William"
                                _meta = {
                                    "legacy_id": entry["id"],
                                    "coordination": entry.get("coordination"),
                                }
                                if _chan_lower.startswith(("dm:", "user:")):
                                    # FIX RLS write-path del /ws (JARVIS 13-jul): la UI humana
                                    # (William/Henry, action:'say') persistía SIN setear el contexto
                                    # RLS → la fila recién insertada era invisible para la política
                                    # SELECT (chat_messages_channel_own) y el DM NO se guardaba: 0 DMs
                                    # enviados por William, "puedo leer pero no veo mis enviados".
                                    # Los agentes NO sufrían porque /api/agents/send YA tenía el fix
                                    # de FABLE; faltaba replicarlo acá. set_config transaction-local.
                                    async with chat_db.pool.acquire() as _dmc:
                                        async with _dmc.transaction():
                                            if _chan_lower.startswith("dm:"):
                                                await _dmc.execute(
                                                    "SELECT set_config('app.current_identity', $1, true)",
                                                    str(_sender_name),
                                                )
                                            else:  # user:<uid>:...
                                                await _dmc.execute(
                                                    "SELECT set_config('app.current_user_id', $1, true)",
                                                    channel.split(":", 2)[1],
                                                )
                                            db_msg = await chat_db.create_message(
                                                sender_name=_sender_name,
                                                content=text,
                                                channel=channel,
                                                sender_type="user",
                                                message_type="text",
                                                metadata=_meta,
                                                conn=_dmc,
                                            )
                                else:
                                    db_msg = await chat_db.create_message(
                                        sender_name=_sender_name,
                                        content=text,
                                        channel=channel,
                                        sender_type="user",
                                        message_type="text",
                                        metadata=_meta,
                                    )
                                entry["db_id"] = db_msg["id"]
                        except Exception as exc:
                            if _guaranteed:
                                await ws.send_text(json.dumps({
                                    "type": "error",
                                    "error": "dm_db_persist_failed",
                                    "message": f"DM no enviado: no se pudo persistir en DB ({exc}).",
                                }, ensure_ascii=False))
                                continue
                        # #17 cura: el chat HUMANO (UI, ws action:'say') TAMBIÉN al outbox
                        # durable. Sin esto, los DMs tipeados por William/Henry se perdían si el
                        # agente destino tenía el WS caído. Para DMs, outbox antes del broadcast.
                        if _guaranteed and visible_to and visible_to != "equipo":
                            try:
                                async with chat_db.pool.acquire() as _c:
                                    await _msgdelivery.enqueue(_c, visible_to, channel, entry,
                                                               priority=_prio, msg_id=entry["id"])
                            except Exception as exc:
                                await ws.send_text(json.dumps({
                                    "type": "error",
                                    "error": "dm_outbox_failed",
                                    "message": f"DM no enviado: no se pudo guardar en outbox ({exc}).",
                                }, ensure_ascii=False))
                                continue
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
    "DUM": LOG_ADA,
    "ALICE": LOG_ALICE,
    "NEXUS": LOG_WILLIAM,
    "FABLE": LOG_FABLE,
}


@app.post("/api/chat/typing")
async def chat_typing(request: Request, user: dict = Depends(require_auth)):
    """Broadcast typing indicator — ephemeral, not persisted."""
    body = await request.json()
    # Never trust a caller-provided sender for presence signals.
    agent = str(user.get("display_name") or user.get("username") or "").strip()
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


# ---- Anti-flood single-voice claim (ALICE 7-jul-2026, orden de William «estructura que haga el single-voice automático») ----
# Cuando William postea a "equipo", los N agentes reciben el evento casi a la vez y responden lo MISMO
# (race condition → flood). Este claim atómico le da el turno a UNO: el primero que reclama el message_id
# GANA; los demás reciben granted=False y DEBEN callar. La atomicidad la garantiza el event-loop
# single-thread de FastAPI (get+set del dict SIN await entre medio = no hay ventana de carrera).
import time as _time_claim
_response_claims = {}          # message_id -> {"agent": str, "ts": float}
_CLAIM_TTL_SEC = 180           # limpia claims viejos para no crecer sin límite


def _agent_auth_mode() -> str:
    """Runtime-reloadable rollout switch for machine-to-machine chat auth.

    OFF preserves legacy behaviour, SHADOW records requests that enforcement
    would reject, and ENFORCE fails closed.  Reading a small control file lets
    operations roll back without restarting the chat daemon.
    """
    mode_file = Path(os.environ.get(
        "SEAL_WEBCHAT_AGENT_AUTH_MODE_FILE",
        str(Path.home() / ".config/seal/webchat_agent_auth_mode"),
    ))
    try:
        mode = mode_file.read_text(encoding="utf-8").strip().upper()
    except OSError:
        mode = os.environ.get("SEAL_WEBCHAT_AGENT_AUTH_MODE", "OFF").strip().upper()
    return mode if mode in {"OFF", "SHADOW", "ENFORCE"} else "OFF"


def _response_lease_mode() -> str:
    """Runtime switch: OFF -> SHADOW -> ENFORCE, reversible sin restart."""
    mode_file = Path(os.environ.get(
        "SEAL_RESPONSE_LEASE_MODE_FILE",
        str(Path.home() / ".config/seal/response_lease_mode"),
    ))
    try:
        mode = mode_file.read_text(encoding="utf-8").strip().upper()
    except OSError:
        mode = os.environ.get("SEAL_RESPONSE_LEASE_MODE", "OFF").strip().upper()
    return mode if mode in {"OFF", "SHADOW", "ENFORCE"} else "OFF"


def _coordination_mode() -> str:
    """Runtime-reloadable SOUL Council rollout switch."""
    mode_file = Path(os.environ.get(
        "SEAL_SOUL_COORDINATION_MODE_FILE",
        str(Path.home() / ".config/seal/soul_coordination_mode"),
    ))
    try:
        mode = mode_file.read_text(encoding="utf-8").strip().upper()
    except OSError:
        mode = os.environ.get("SEAL_SOUL_COORDINATION_MODE", "OFF").strip().upper()
    return mode if mode in {"OFF", "SHADOW", "ENFORCE"} else "OFF"


async def _stamp_coordination(entry: dict, *, verified_human: bool) -> dict:
    """Classify and durably assign a verified public human request.

    Database failure never silently fans the message out under Council.  The
    entry remains deliverable through the legacy rollback path, with
    ``enforced=false`` and an observable error marker.
    """
    rollout = _coordination_mode()
    if (
        rollout == "OFF"
        or _council is None
        or not verified_human
        or str(entry.get("channel") or "web_chat").lower() != "web_chat"
        or str(entry.get("from") or "").upper() not in {"WILLIAM", "HENRY"}
    ):
        return entry
    source_id = str(entry.get("idempotency_key") or entry.get("id") or "").strip()
    if not source_id:
        return entry
    text = str(entry.get("message") or entry.get("content") or "")
    destination = str(entry.get("to") or "equipo")
    roster = tuple(a for a in ("NEXUS", "JARVIS", "ALICE", "FABLE", "ADA"))
    mode = _council.classify_mode(
        text, to_field=destination, msg_type=str(entry.get("type") or "conversation")
    )
    lead = _council.choose_lead(text, source_id, roster, mode=mode, to=destination)
    named = [a for a in roster if re.search(r"\b" + re.escape(a.lower()) + r"\b", text.lower())]
    group_audience = bool(re.search(
        r"\b(todos|todas|equipo|agentes|chicos|chicas|hermanos|hermanas|familia)\b",
        text.lower(),
    ))
    if mode == "direct":
        requested = [lead]
    elif named and not group_audience:
        requested = named
    else:
        requested = roster
    assignments = _council.build_assignments(
        mode, lead, text=text, requested_agents=requested
    )
    plan = {
        "policy": "soul-council-v1",
        "rollout": rollout,
        "mode": mode,
        "lead": lead,
        "assignments": assignments,
        "source_id": source_id,
        "enforced": False,
    }
    entry["coordination"] = plan
    if not chat_db.pool:
        plan["error"] = "db_unavailable_legacy_fallback"
        return entry
    try:
        store = _council.SoulCoordinationStore(chat_db.pool)
        await store.create_turn(
            source_message_id=source_id,
            requester=str(entry.get("from") or ""),
            request_text=text,
            mode=mode,
            lead_agent=lead,
            assignments=assignments,
            metadata={"rollout": rollout, "channel": "web_chat"},
        )
        plan["enforced"] = rollout == "ENFORCE"
        print(
            f"[soul-council] classify source={source_id[:64]} rollout={rollout} "
            f"mode={mode} lead={lead} assignments={len(assignments)} "
            f"public={sum(1 for row in assignments if row.get('public_write') is True)}",
            flush=True,
        )
    except Exception as exc:
        plan["error"] = f"persist_failed:{type(exc).__name__}"
        print(f"[soul-council] stamp fallback source={source_id[:64]} error={type(exc).__name__}", flush=True)
    return entry


async def _coordination_public_write(source_id: str, sender: str) -> tuple[bool | None, dict | None]:
    """Return server-derived public-write authority for one correlated reply."""
    if _council is None or not chat_db.pool or not source_id:
        return None, None
    try:
        turn = await _council.SoulCoordinationStore(chat_db.pool).get_turn(source_id)
    except Exception as exc:
        print(f"[soul-council] writer lookup failed source={source_id[:64]} error={type(exc).__name__}", flush=True)
        return None, None
    if not turn:
        return None, None
    assignment = next(
        (row for row in turn.get("assignments", []) if str(row.get("agent") or "").upper() == sender.upper()),
        None,
    )
    return bool(assignment and assignment.get("public_write") is True), turn


async def _coordination_legacy_human_source(source_id: str) -> bool:
    """True only when a pre-Council source is a real William/Henry message.

    This compatibility path never trusts the caller's ``multi_response`` flag;
    it merely allows the legacy response lease to arbitrate old human turns.
    Replies to agent-authored/nonexistent sources fail closed in ENFORCE.
    """
    if not chat_db.pool or not source_id:
        return False
    try:
        db_id = None
        if source_id.startswith("db_") and source_id[3:].isdigit():
            db_id = int(source_id[3:])
        row = await chat_db.pool.fetchrow(
            """
            SELECT sender_name
              FROM soul_v3.chat_messages
             WHERE ($2::bigint IS NOT NULL AND id=$2)
                OR metadata->>'legacy_id'=$1
             ORDER BY id DESC
             LIMIT 1
            """,
            source_id,
            db_id,
        )
        return bool(row and str(row["sender_name"] or "").upper() in {"WILLIAM", "HENRY"})
    except Exception as exc:
        print(f"[soul-council] legacy source lookup failed error={type(exc).__name__}", flush=True)
        return False


def _coordination_requires_turn_rejection(
    rollout: str,
    *,
    is_agent_reply: bool,
    in_reply_to: str | None,
    council_turn_found: bool,
    legacy_human_source: bool,
) -> bool:
    """Pure decision for the missing-turn writer gate (kept unit-testable)."""
    return bool(
        rollout == "ENFORCE"
        and is_agent_reply
        and in_reply_to
        and not council_turn_found
        and not legacy_human_source
    )


async def _resolve_agent_auth(request: Request, body: dict) -> dict | None:
    """Resolve a verified chat session; never trusts the caller-supplied name."""
    raw = str(body.get("session_key", "")).strip() or _extract_token(request)
    if not raw or not chat_db.pool:
        return None
    try:
        return await chat_db.validate_session(hash_token(raw))
    except Exception:
        return None


def _agent_auth_gate(session: dict | None, asserted_sender: str, action: str,
                     request: Request):
    """Return an HTTP rejection when machine auth fails in ENFORCE mode."""
    verified = ""
    if session:
        verified = str(session.get("username") or session.get("display_name") or "").strip()
    reason = None
    status = 401
    if not verified:
        reason = "agent_auth_required"
    elif verified.casefold() != asserted_sender.strip().casefold():
        reason = "agent_sender_mismatch"
        status = 403
    if reason:
        mode = _agent_auth_mode()
        # No secrets or request bodies are written to the journal.
        print(f"[agent-auth] mode={mode} action={action} reason={reason} asserted={asserted_sender[:24]}",
              flush=True)
        if mode == "ENFORCE":
            return JSONResponse({"ok": False, "error": reason}, status_code=status)
    return None

@app.post("/api/agents/claim")
async def agents_claim(request: Request):
    """
    Single-voice: el PRIMER agente que reclama un message_id gana el turno de responder.
    Body: {"message_id": str, "agent": str}
    Return: {"granted": bool, "holder": str} — si granted=False, el agente NO debe responder
    (salvo que tenga valor ÚNICO de su lane; el default con granted=False es CALLAR).
    """
    if request.client and not _is_local_or_lan(request.client.host):
        return JSONResponse({"ok": False, "error": "acceso denegado"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "body inválido"}, status_code=400)
    mid = str(body.get("message_id", "")).strip()
    agent = str(body.get("agent", "")).strip()
    if not mid or not agent:
        return JSONResponse({"ok": False, "error": "message_id y agent requeridos"}, status_code=400)
    auth_session = await _resolve_agent_auth(request, body)
    auth_rejection = _agent_auth_gate(auth_session, agent, "claim", request)
    if auth_rejection is not None:
        return auth_rejection
    now = _time_claim.time()
    # limpieza TTL + get/set ATÓMICO: no hay await entre el chequeo del holder y la asignación
    for k in [k for k, v in _response_claims.items() if now - v["ts"] > _CLAIM_TTL_SEC]:
        del _response_claims[k]
    holder = _response_claims.get(mid)
    if holder is None:
        _response_claims[mid] = {"agent": agent, "ts": now}
        return {"ok": True, "granted": True, "message_id": mid, "holder": agent}
    return {"ok": True, "granted": holder["agent"] == agent, "message_id": mid, "holder": holder["agent"]}


def _proactive_without_reply_allowed(
    *,
    proactive: bool,
    idempotency_key: str | None,
    verified_name: str,
    sender: str,
    message_type: str,
) -> bool:
    """Allow only authenticated, idempotent lifecycle/status announcements.

    `system_alive` is the message type emitted by the canonical agent launchers.
    It is deliberately narrow: ordinary conversation remains correlated through
    ``in_reply_to`` and cannot use this path to bypass response coordination.
    """
    return bool(
        proactive
        and idempotency_key
        and verified_name == sender.casefold()
        and (
            message_type.casefold() in {"alert", "status", "system", "system_alive"}
            or sender.upper() == "FAILOVER"
        )
    )


@app.post("/api/agents/send")
async def agents_send(request: Request):
    """
    Recibe mensaje de un agente o usuario LAN, persiste en JSONL y broadcast a WebSocket.
    Acepta conexiones desde localhost y red local (192.168.68.x).
    Body: {"from": str, "to": str, "message": str, "type": str}
    """
    if request.client and not _is_local_or_lan(request.client.host):
        return JSONResponse({"ok": False, "error": "acceso denegado"}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        raw = await request.body()
        import json as _json
        body = _json.loads(raw.decode("utf-8", errors="replace"))
    sender = str(body.get("from", "")).strip()
    to     = str(body.get("to", "equipo")).strip()
    text   = str(body.get("message", "")).strip()
    mtype  = str(body.get("type", "chat")).strip()
    channel = str(body.get("channel", "web_chat")).strip()
    explicit_session_key = str(body.get("session_key", "")).strip() or None
    idempotency_key = str(body.get("idempotency_key", "")).strip() or None
    in_reply_to = str(body.get("in_reply_to", "")).strip() or None
    multi_response = body.get("multi_response") is True
    proactive = body.get("proactive") is True
    unique_contribution = body.get("unique_contribution") is True
    contribution_reason = str(body.get("contribution_reason", "")).strip()

    if not sender or not text:
        return JSONResponse({"ok": False, "error": "from y message son requeridos"}, status_code=400)

    # SPECTRE aislado de la familia (William 15-jun-2026) — escribe directo a soul_standalone DB
    if sender.upper() == "SPECTRE":
        return JSONResponse({"ok": False, "error": "SPECTRE usa canal aislado (soul_standalone)"}, status_code=403)

    auth_session = None
    session_token_hash = None
    matrix_auth = None
    raw_session_token = explicit_session_key or _extract_token(request)
    if raw_session_token:
        session_token_hash = hash_token(raw_session_token)
        try:
            if chat_db.pool:
                auth_session = await chat_db.validate_session(session_token_hash)
        except Exception:
            auth_session = None
    if not auth_session:
        matrix_auth = _verify_matrix_auth(body, sender, text)
        if matrix_auth and chat_db.pool:
            try:
                user = await chat_db.get_user_by_username(sender)
                if user:
                    auth_session = {
                        "session_id": f"matrix:{matrix_auth['event_id']}",
                        "user_id": user["id"],
                        "username": user["username"],
                        "display_name": user["display_name"],
                    }
            except Exception:
                auth_session = None
                matrix_auth = None

    auth_action = "dm" if channel.startswith("dm:") else ("steer" if mtype == "steer" else "send")
    auth_rejection = _agent_auth_gate(auth_session, sender, auth_action, request)
    if auth_rejection is not None:
        return auth_rejection

    # P1-10: enforcement en el RESPONSE path. Solo aplica a respuestas de agentes
    # a William que declaran el broadcast fuente. Los mensajes all-call no llevan
    # in_reply_to desde el monitor, por lo que conservan la multi-respuesta.
    # ACK-exemption (NEXUS 22-jul, orden de William "deben contestar mis órdenes").
    # Un ACK estructural a una orden de William (to=William + in_reply_to +
    # gramática cerrada de receipt)
    # NO es flood: es la confirmación que él pide. Se exime de AMBOS gates de
    # single-voice —el response-lease Y el council-deny— para que William reciba el
    # "recibido" de CADA agente. El duplicado SUSTANTIVO largo sigue sujeto a ambos.
    _is_ack_to_william = (
        to.casefold() == "william"
        and bool(in_reply_to)
        and _ff_gate is not None
        and _ff_gate.is_receipt_ack(text)
    )
    lease_mode = _response_lease_mode()
    _is_agent_reply = (
        sender.upper() in _ALLOWED_AGENTS
        and to.casefold() == "william"
        and channel == "web_chat"
        and mtype.casefold() in {"chat", "conversation"}
    )
    if lease_mode == "ENFORCE" and _is_agent_reply and not in_reply_to:
        verified_name = str((auth_session or {}).get("username") or "").casefold()
        proactive_allowed = _proactive_without_reply_allowed(
            proactive=proactive,
            idempotency_key=idempotency_key,
            verified_name=verified_name,
            sender=sender,
            message_type=mtype,
        )
        if proactive_allowed:
            print(f"[response-lease] proactive source={idempotency_key[:64]} sender={sender[:24]}", flush=True)
        else:
            return JSONResponse({"ok": False, "error": "in_reply_to_required"}, status_code=422)

    council_rollout = _coordination_mode()
    council_allowed = None
    council_turn = None
    if _is_agent_reply and in_reply_to and council_rollout != "OFF":
        council_allowed, council_turn = await _coordination_public_write(in_reply_to, sender)
        if council_turn is not None:
            print(
                f"[soul-council] mode={council_rollout} source={in_reply_to[:64]} "
                f"sender={sender[:24]} allowed={council_allowed} "
                f"turn_mode={council_turn.get('mode')}",
                flush=True,
            )
            if council_allowed:
                # Flood-fix v2 (NEXUS 22-jul): PATH PRIMARIO. Cuando el council da
                # public_write a este agente (discussion mode -> varios lo tienen),
                # suprime SOLO si hay FORMA de flood real (>=2 agentes distintos al
                # mismo in_reply_to en ventana corta) Y tu texto es cuasi-dup LÉXICO
                # de un hermano. Convergencia con otras palabras PASA a propósito;
                # una corrección/negación PASA (guarda de polaridad). FAIL-OPEN: si
                # el gate falta o falla -> se permite (nunca romper comms). Es un
                # PISO/backstop del single-voice, no su reemplazo (cap ~min_siblings).
                _ff_sup = False
                _ff_meta = None
                if _ff_gate is not None:
                    try:
                        _ff_sup, _ff_meta = await _ff_gate.should_suppress_as_flood(
                            chat_db.pool, in_reply_to, sender, text)
                    except Exception:
                        _ff_sup, _ff_meta = False, None
                if _ff_sup:
                    print(
                        f"[flood-form] SUPPRESS source={in_reply_to[:64]} "
                        f"sender={sender[:24]} dup_of={(_ff_meta or {}).get('dup_of')} "
                        f"n_siblings={(_ff_meta or {}).get('n_siblings')}",
                        flush=True,
                    )
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": "coordination_flood_duplicate",
                            "in_reply_to": in_reply_to,
                            "duplicate_of": (_ff_meta or {}).get("dup_of"),
                            "hint": (
                                "Ya hay respuestas equivalentes a este hilo (flood-form). "
                                "Tu mensaje repite una casi textual: aporta algo distinto o calla."
                            ),
                        },
                        status_code=409,
                    )
            # ACK-exemption (NEXUS 22-jul, orden de William: "cuando doy una orden
            # deben contestar, cómo sé que me leyeron"). Un ACK CORTO a una orden de
            # William (to=William + in_reply_to + receipt estructural) NO es flood: es la
            # confirmación que él pide. Salta el deny del single-voice para que
            # William reciba el "recibido" de CADA agente; el duplicado SUSTANTIVO
            # largo sigue capado por el council/flood-form. Radio chico y reversible.
            _is_ack_to_william = (
                to.casefold() == "william"
                and bool(in_reply_to)
                and _ff_gate is not None
                and _ff_gate.is_receipt_ack(text)
            )
            if council_rollout == "ENFORCE" and not council_allowed and not _is_ack_to_william:
                verified_name = str((auth_session or {}).get("username") or "").casefold()
                override_allowed = (
                    unique_contribution
                    and len(contribution_reason) >= 20
                    and verified_name == sender.casefold()
                )
                if override_allowed:
                    # Pieza 2 (NEXUS 21-jul): el flag NO cubre DUPLICADOS. La unicidad
                    # es un juicio sobre las OTRAS respuestas del hilo -> se verifica
                    # leyendo los hermanos, no auto-declarando (FABLE). FAIL-OPEN: si el
                    # gate falta o falla -> _dup=None -> se permite (nunca romper comms).
                    _dup = None
                    if _uc_gate is not None:
                        _dup = await _uc_gate.find_near_duplicate_sibling(
                            chat_db.pool, in_reply_to, sender, text)
                    if _dup is not None:
                        print(
                            f"[soul-council] override DENIED-duplicate source={in_reply_to[:64]} "
                            f"sender={sender[:24]} duplicate_of={_dup[0]} sim={_dup[1]:.2f}",
                            flush=True,
                        )
                        return JSONResponse(
                            {
                                "ok": False,
                                "error": "coordination_duplicate_contribution",
                                "in_reply_to": in_reply_to,
                                "duplicate_of": _dup[0],
                                "similarity": round(_dup[1], 2),
                                "hint": (
                                    f"Tu mensaje repite lo que {_dup[0]} ya dijo (sim {_dup[1]:.2f}). "
                                    "unique_contribution no cubre duplicados: aporta algo distinto o calla."
                                ),
                            },
                            status_code=409,
                        )
                    print(
                        f"[soul-council] override unique_contribution=true source={in_reply_to[:64]} "
                        f"sender={sender[:24]} reason={contribution_reason[:160]}",
                        flush=True,
                    )
                    try:
                        await _council.SoulCoordinationStore(chat_db.pool).update_assignment(
                            in_reply_to, sender, status="submitted",
                            evidence={"override": True, "reason": contribution_reason},
                        )
                    except Exception as exc:
                        print(f"[soul-council] override audit-write failed error={type(exc).__name__}", flush=True)
                else:
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": "coordination_public_write_denied",
                            "in_reply_to": in_reply_to,
                            "lead": council_turn.get("lead_agent"),
                            "mode": council_turn.get("mode"),
                            "hint": (
                                "Si esto es informacion unica/aportativa (no repeticion), "
                                "reenvia con unique_contribution=true y contribution_reason "
                                "(>=20 caracteres explicando que aporta de nuevo)."
                            ),
                        },
                        status_code=409,
                    )
        elif council_rollout == "ENFORCE":
            legacy_human_source = await _coordination_legacy_human_source(in_reply_to)
            if _coordination_requires_turn_rejection(
                council_rollout,
                is_agent_reply=_is_agent_reply,
                in_reply_to=in_reply_to,
                council_turn_found=False,
                legacy_human_source=legacy_human_source,
            ):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "coordination_turn_required",
                        "in_reply_to": in_reply_to,
                    },
                    status_code=409,
                )
    council_authoritative = (
        council_rollout == "ENFORCE" and council_turn is not None and council_allowed is True
    )
    if (
        lease_mode != "OFF"
        and in_reply_to
        and not (multi_response and council_rollout != "ENFORCE")
        and not council_authoritative
        and sender.upper() in _ALLOWED_AGENTS
        and to.casefold() == "william"
        and not _is_ack_to_william
        and _response_lease is not None
        and chat_db.pool
    ):
        try:
            decision = await _response_lease.acquire_db(chat_db.pool, in_reply_to, sender)
            print(
                f"[response-lease] mode={lease_mode} source={in_reply_to[:64]} "
                f"sender={sender[:24]} granted={decision.granted} owner={decision.owner[:24]} "
                f"reason={decision.reason}",
                flush=True,
            )
            if lease_mode == "ENFORCE" and not decision.granted:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "response_lease_held",
                        "in_reply_to": in_reply_to,
                        "holder": decision.owner,
                    },
                    status_code=409,
                )
        except Exception as exc:
            # Fail-open: una falla del lease nunca silencia a William.
            print(f"[response-lease] fail_open error={type(exc).__name__}", flush=True)

    # FIX #4 cuchillo de palo (24-jun, NEXUS+JARVIS): tamiz anti-inyección en la PUERTA real.
    # screen_incoming es FALLA-SEGURA (allow=True ante cualquier problema) y SOLO bloquea
    # inyección de ALTA confianza desde origen NO confiable (ni verificado ni agente conocido).
    # La familia (agentes + sesiones verificadas) nunca se bloquea.
    try:
        from nexus_ingress_screen import screen_incoming
        # NOTA (ALICE 8-jul): probé por efecto que trust-por-NOMBRE (from=William) es spoofeable
        # (FABLE confirmó adversarial: payload de inyección con from=William → BYPASS). REVERTÍ mi
        # _is_owner_lan — el fix correcto NO es confiar por nombre sino por SESIÓN VERIFICADA (token).
        # El false-positive de William (paste de código legítimo) se resuelve en el módulo (menos agresivo)
        # + auth real de su sesión web (por qué tiene verified=False siendo owner). Lane: FABLE/NEXUS.
        _scr = screen_incoming(text, sender, {"verified": bool(auth_session)})
        if not _scr.get("allow", True):
            return JSONResponse({"ok": False, "error": "mensaje bloqueado por screening de seguridad",
                                 "risk": _scr.get("risk", "high")}, status_code=403)
    except Exception:
        pass  # nunca romper el chat por el screen (defensa en profundidad, no punto único de fallo)

    # Normalize DM channel names to canonical sorted format (same as chat_db.create_dm_channel)
    if channel.startswith("dm:"):
        parts = channel[3:].split(":")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            # FIX ghost-message (NEXUS 17-jul, catch de ALICE): un canal dm:* mal
            # formado (ej. "dm:NEXUS" sin el segundo participante) antes seguía de
            # largo con el channel crudo, se escribía al JSONL (líneas ~2060-2070,
            # que es lo que entrega EN VIVO a los monitores de los agentes) y RECIÉN
            # DESPUÉS se intentaba el persist con RLS — que fallaba (RLS exige los 2
            # participantes en el nombre del canal) y devolvía 503 al emisor. Resultado:
            # el receptor YA vio y contestó un mensaje que nunca quedó en chat_messages
            # (mensaje fantasma). Cortar acá, ANTES del JSONL/broadcast, cierra el hueco.
            return JSONResponse(
                {"ok": False, "error": "invalid_dm_channel",
                 "detail": "canal dm: requiere exactamente 2 participantes: dm:<a>:<b>"},
                status_code=400,
            )
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

    # M2b (JARVIS 2026-06-11) — PROVENIENCIA verificable server-side (anti-spoof C3).
    # El `from` lo provee el cliente (spoofeable). Aquí estampamos la VERDAD que el
    # server verificó desde la sesión autenticada (auth_session). Los consumidores de
    # AUTORIDAD (deploy/GO/rotación) deben checar provenance.verified — NUNCA el `from`.
    # Aditivo: no rechaza posts de rutina (agente sin token → verified=false, igual fluye).
    _verified_sender = None
    if auth_session:
        _verified_sender = (str(auth_session.get("username") or auth_session.get("display_name") or "")).strip() or None
    _provenance = {
        "verified": bool(_verified_sender),
        "verified_sender": _verified_sender,
        "from_matches_session": bool(_verified_sender) and (_verified_sender.upper() == sender.upper()),
    }

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
        "provenance": _provenance,
    }
    if in_reply_to:
        entry["in_reply_to"] = in_reply_to
    if multi_response:
        entry["multi_response"] = True
    if proactive:
        entry["proactive"] = True

    entry = await _stamp_coordination(
        entry,
        verified_human=(
            _provenance["verified"]
            and _provenance["from_matches_session"]
            and sender.upper() in {"WILLIAM", "HENRY"}
        ),
    )

    # Dedup check + JSONL write inside lock (prevents TOCTOU duplicate writes)
    async with _queue_lock:
        if ikey in _enqueued_ids:
            original_id = _enqueued_ids.get(ikey) or msg_id
            return JSONResponse({"ok": True, "id": original_id, "duplicate": True})
        # Canales públicos → JSONL vía routing (comportamiento intacto).
        # DMs (cualquier par, incl. agente↔agente) → ADEMÁS a william_channel.jsonl CIFRADOS,
        # para que el agente destinatario los reciba por el monitor (antes los DMs nunca iban
        # al JSONL = write-only, nadie los recibía). El filtro por-agente descifra SOLO el del
        # destinatario. (Fix write-only William/Henry — 14-jul-2026; extendido a agente↔agente
        # — NEXUS 14-jul-2026, mismo gap que quedó abierto en el fix original.)
        _cl = channel.lower()
        _monitored_dm = _cl.startswith("dm:")
        if not _cl.startswith("dm:"):
            jsonl_entry = _encrypt_for_jsonl(entry)
            for log_path in _get_log_paths(sender, to):
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")
        elif _monitored_dm:
            jsonl_entry = _encrypt_for_jsonl(entry)
            with open(LOG_WILLIAM, "a", encoding="utf-8") as f:
                f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")

    # Remote instance filter: Tailscale (100.x.x.x) = laptop/remote device → skip broadcast
    _is_remote = bool(request.client and request.client.host.startswith("100."))
    if _is_remote:
        entry["remote"] = True

    # Prioridad por cadena de mando: humanos > agentes. William(#1)=1, Henry(#2)=2, agentes=5.
    # Authority is derived from the verified session, never the asserted `from`.
    _sl = (_verified_sender or "").lower()
    _prio = 1 if _sl == "william" else (2 if _sl == "henry" else 5)
    _guaranteed = _is_guaranteed_dm(channel, _prio)
    if _guaranteed and (_msgdelivery is None or not chat_db.pool):
        return JSONResponse(
            {"ok": False, "error": "dm_delivery_unavailable", "detail": "DM requiere DB/outbox durable"},
            status_code=503,
        )

    # STEER mode: write to ephemeral steer file so agent can incorporate mid-task
    if mtype == "steer" and to and to.lower() not in ("equipo", "william", ""):
        try:
            import json as _json
            steer_data = {"from": sender, "message": text, "timestamp": ts, "id": msg_id}
            _steer_path(to).write_text(_json.dumps(steer_data, ensure_ascii=False))
        except Exception:
            pass

    # Dual-write: persist to PostgreSQL. For guaranteed DMs this is fail-closed:
    # no database row/outbox means no "ok" response and no broadcast.
    try:
        if chat_db.pool:
            metadata = {
                "to": to,
                "legacy_id": msg_id,
            }
            if in_reply_to:
                metadata["in_reply_to"] = in_reply_to
            if multi_response:
                metadata["multi_response"] = True
            if proactive:
                metadata["proactive"] = True
            if session_token_hash and auth_session:
                metadata["session_token_hash"] = session_token_hash
                metadata["session_user"] = auth_session.get("username")
                metadata["session_id"] = str(auth_session.get("session_id"))
            if matrix_auth and auth_session:
                metadata["matrix_auth"] = matrix_auth

            sender_type = "agent"
            sender_id = None
            db_sender = sender
            if auth_session:
                sender_type = "user"
                sender_id = int(auth_session["user_id"])
                db_sender = str(auth_session["username"])

            _chan_lower = channel.lower()
            if _chan_lower.startswith(("dm:", "user:")):
                # FIX RLS canales privados (FABLE 12-jul dm:*, 13-jul user:*): INSERT..RETURNING
                # necesita que la fila recién insertada sea VISIBLE por la política SELECT
                # (chat_messages_channel_own: dm:* exige app.current_identity = participante;
                # user:* exige app.current_user_id = dueño del canal). El write-path no seteaba
                # el contexto → RLS violation. En dm:* daba 503 (fail-closed); en user:* el
                # except lo TRAGABA y el mensaje se perdía en SILENCIO con ok:true (falso-verde
                # cazado 13-jul: la review a Henry en user:3:tareas nunca persistió).
                # set_config transaction-local; la RLS sigue mandando sobre lo que no calce.
                async with chat_db.pool.acquire() as _dmc:
                    async with _dmc.transaction():
                        if _chan_lower.startswith("dm:"):
                            await _dmc.execute(
                                "SELECT set_config('app.current_identity', $1, true)", str(db_sender)
                            )
                        else:  # user:<uid>:...
                            await _dmc.execute(
                                "SELECT set_config('app.current_user_id', $1, true)",
                                channel.split(":", 2)[1],
                            )
                        db_msg = await chat_db.create_message(
                            sender_name=db_sender,
                            content=text,
                            channel=channel,
                            sender_type=sender_type,
                            sender_id=sender_id,
                            message_type=mtype,
                            metadata=metadata,
                            conn=_dmc,
                        )
            else:
                db_msg = await chat_db.create_message(
                    sender_name=db_sender,
                    content=text,
                    channel=channel,
                    sender_type=sender_type,
                    sender_id=sender_id,
                    message_type=mtype,
                    metadata=metadata,
                )
            # Re-broadcast with db_id so clients can use it for operations (e.g. delete)
            entry["db_id"] = db_msg["id"]
    except Exception as exc:
        if _guaranteed:
            return JSONResponse({"ok": False, "error": "dm_db_persist_failed", "detail": str(exc)}, status_code=503)

    # #17 cura de mensajería: persistir al outbox DURABLE antes del broadcast en DMs.
    if _guaranteed and to:
        try:
            async with chat_db.pool.acquire() as _c:
                await _msgdelivery.enqueue(_c, to, channel, entry, priority=_prio, msg_id=ikey)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": "dm_outbox_failed", "detail": str(exc)}, status_code=503)

    # SILENT_MARKER: log + persist but skip WebSocket broadcast for background noise.
    # FIX (JARVIS 2026-06-12): el filtro Tailscale (_is_remote) solo debe silenciar
    # RELAYS DE AGENTE remotos (clon-laptop re-broadcasteando = doble-broadcast). Un
    # mensaje de USUARIO desde un navegador por Tailscale (ej. William en v1/v2) SÍ debe
    # broadcastearse — antes se silenciaba todo 100.x y "no enviaba" para el usuario.
    _silent = text.startswith("[SILENT]") or (_is_remote and sender.upper() in _ALLOWED_AGENTS)
    if not _silent:
        await broadcast(entry)  # broadcast sin cifrar (va por WebSocket en memoria)
    await enqueue(entry)  # enqueue también deduplica para el in-memory queue

    return JSONResponse({"ok": True, "id": msg_id})


@app.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
    channel: str = Form("web_chat"),
    sender: str = Form(""),
    user: dict = Depends(require_auth),
):
    """Upload media or document file. Broadcasts as message with file_url."""
    if request.client and not _is_local_or_lan(request.client.host):
        return JSONResponse({"ok": False, "error": "acceso denegado"}, status_code=403)

    channel = _canonical_upload_channel(channel)
    user_id = int(user["sub"])
    uploader = str(user.get("username") or "").strip()
    if not uploader:
        return JSONResponse({"ok": False, "error": "authenticated identity missing"}, status_code=401)

    # Authorize the destination before reading or writing any bytes. The
    # multipart ``sender`` field is intentionally ignored: session identity is
    # the only attribution authority.
    if channel.startswith("dm:"):
        parts = channel[3:].split(":")
        if len(parts) == 2:
            channel = await chat_db.create_dm_channel(parts[0], parts[1])
        other = _dm_other_participant(channel, uploader)
        if other and other.upper() in _ASSIGNABLE_AGENTS:
            allowed = await _resolve_allowed_agents(user_id, user.get("role", ""))
            if other.upper() not in allowed:
                return JSONResponse({"ok": False, "error": "no tenes ese agente asignado"}, status_code=403)
    if not await chat_db.user_can_access_channel(user_id, channel):
        return JSONResponse({"ok": False, "error": "Access denied"}, status_code=403)

    # Determine file type. Unknown/active formats remain shareable as safe downloads.
    ct = (file.content_type or "").lower()
    ftype, ext = _classify_upload(file.filename or "", ct)
    fname = f"{ftype}_{time.time_ns()}{ext}"
    fpath = UPLOADS_DIR / fname
    # Stream a disco en bloques: aceptar videos de hasta 300MB no debe reservar
    # 300MB de RAM por request. El .part nunca se sirve y se limpia en todo error.
    part_path = UPLOADS_DIR / f".{fname}.part"
    total = 0
    header = bytearray()
    try:
        with part_path.open("xb") as out:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_SIZE:
                    raise ValueError("upload_too_large")
                if len(header) < 64:
                    header.extend(chunk[: 64 - len(header)])
                out.write(chunk)
        if not _upload_signature_matches(ftype, ct, bytes(header)):
            raise ValueError("mime_mismatch")
        os.replace(part_path, fpath)
    except ValueError as exc:
        part_path.unlink(missing_ok=True)
        fpath.unlink(missing_ok=True)
        if str(exc) == "upload_too_large":
            return JSONResponse({"ok": False, "error": "archivo muy grande (max 300MB)"}, status_code=413)
        if str(exc) == "mime_mismatch":
            return JSONResponse({"ok": False, "error": "el contenido no coincide con el tipo de archivo"}, status_code=415)
        raise
    except Exception:
        part_path.unlink(missing_ok=True)
        fpath.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    file_url = f"/uploads/{fname}"
    ts = datetime.now(PERU_TZ).isoformat()
    msg_id = f"upload_{time.time_ns()}"
    _up_verified = uploader

    # Determine 'to' from channel (excluye al PROPIO uploader, ya no asume William)
    upload_to = "equipo"
    if channel.startswith("dm:"):
        parts = channel.replace("dm:", "").split(":")
        others = [p for p in parts if p.lower() != uploader.lower()]
        upload_to = others[0].upper() if others else "equipo"

    entry = {
        "id": msg_id,
        "from": uploader,
        "to": upload_to,
        "timestamp": ts,
        "type": ftype,
        "message": caption.strip(),
        "file_url": file_url,
        "filename": Path(file.filename or fname).name,
        "channel": channel,
        "provenance": {
            "verified": bool(_up_verified),
            "verified_sender": _up_verified,
            "from_matches_session": bool(_up_verified),
        },
    }
    entry = await _stamp_coordination(
        entry,
        verified_human=(
            bool(_up_verified)
            and str(_up_verified).upper() in {"WILLIAM", "HENRY"}
        ),
    )

    # Persist to PostgreSQL (primary)
    # FIX (NEXUS 13-jul): mismo bug RLS que /api/agents/send (comentario línea ~2034) —
    # esta ruta de upload NO seteaba app.current_user_id para canales user:*, así que el
    # INSERT violaba la política RLS y el except lo TRAGABA en silencio (caso real: el
    # zip de Henry en user:3:tareas nunca persistía, desaparecía tras refresh).
    if chat_db.pool:
        try:
            _chan_lower = channel.lower()
            if _chan_lower.startswith(("dm:", "user:")):
                async with chat_db.pool.acquire() as _upc:
                    async with _upc.transaction():
                        if _chan_lower.startswith("dm:"):
                            await _upc.execute(
                                "SELECT set_config('app.current_identity', $1, true)", str(uploader)
                            )
                        else:  # user:<uid>:...
                            await _upc.execute(
                                "SELECT set_config('app.current_user_id', $1, true)",
                                channel.split(":", 2)[1],
                            )
                        db_msg = await chat_db.create_message(
                            sender_name=uploader,
                            content=caption.strip() or f"[{ftype}]",
                            channel=channel,
                            sender_type="user",
                            sender_id=None,
                            message_type=ftype,
                            metadata={"file_url": file_url, "filename": file.filename},
                            conn=_upc,
                        )
            else:
                db_msg = await chat_db.create_message(
                    sender_name=uploader,
                    content=caption.strip() or f"[{ftype}]",
                    channel=channel,
                    sender_type="user",
                    sender_id=None,
                    message_type=ftype,
                    metadata={"file_url": file_url, "filename": file.filename},
                )
            entry["db_id"] = db_msg["id"]
        except Exception as e:
            # No afirmar éxito ni dejar un archivo huérfano si no existe la fila
            # que aporta ownership/ACL al download.
            fpath.unlink(missing_ok=True)
            print(f"[upload] DB persist failed: {e}", flush=True)
            return JSONResponse({"ok": False, "error": "upload persistence failed"}, status_code=503)
    else:
        fpath.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": "upload database unavailable"}, status_code=503)

    # Persist to JSONL (dual-write) — DMs NEVER go to JSONL
    if not channel.startswith("dm:"):
        jsonl_entry = _encrypt_for_jsonl(entry)
        with open(LOG_WILLIAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")

    await broadcast(entry)
    await enqueue(entry)

    return JSONResponse({"ok": True, "id": msg_id, "file_url": file_url})


async def _agent_upload_channel_is_known(channel: str) -> bool:
    """Accept only delivery surfaces that exist; never invent a channel on upload."""
    lookup_channel = channel
    if channel == "web_chat":
        return True
    if channel.startswith("dm:"):
        parts = channel[3:].split(":")
        if not (
            len(parts) == 2
            and all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.~-]{0,63}", part or "") for part in parts)
        ):
            return False
        ordered = sorted(parts, key=str.casefold)
        lookup_channel = f"dm:{ordered[0].casefold()}:{ordered[1].casefold()}"
    if not chat_db.pool:
        return False
    try:
        return bool(await chat_db.pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM chat_channels WHERE name = $1)",
            lookup_channel,
        ))
    except Exception as exc:
        print(
            f"[agent-upload] channel lookup failed channel={channel[:80]!r} "
            f"error={type(exc).__name__}",
            flush=True,
        )
        return False


@app.post("/api/agents/upload")
async def agents_upload(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
    channel: str = Form("web_chat"),
    sender: str = Form(""),
    session_key: str = Form(""),
    instance_id: str = Form(""),
):
    """Upload a file using the existing machine-session authentication."""
    if request.client and not _is_local_or_lan(request.client.host):
        return JSONResponse({"ok": False, "error": "acceso denegado"}, status_code=403)

    asserted_sender = sender.strip()
    session = await _resolve_agent_auth(request, {"session_key": session_key.strip()})
    rejection = _agent_auth_gate(session, asserted_sender, "upload", request)
    if rejection is not None:
        return rejection

    verified = ""
    if session:
        verified = str(session.get("username") or session.get("display_name") or "").strip()
    if not verified:
        return JSONResponse({"ok": False, "error": "agent_auth_required"}, status_code=401)
    # Authentication can run in audit mode. Attribution itself must always fail closed.
    if verified.casefold() != asserted_sender.casefold():
        return JSONResponse({"ok": False, "error": "agent_sender_mismatch"}, status_code=403)
    # ``chat_sessions`` is shared by humans and machines. A matching username is
    # not enough: this endpoint requires both the durable machine role and a core
    # agent identity, otherwise it would bypass the human upload ACL.
    if str(session.get("role") or "").casefold() != "agent":
        return JSONResponse({"ok": False, "error": "agent_role_required"}, status_code=403)
    if verified.upper() not in _ALLOWED_AGENTS:
        return JSONResponse({"ok": False, "error": "agent_identity_required"}, status_code=403)
    # Clone provenance is not caller-controlled. A future clone upload contract must
    # validate the canonical-agent/instance pair before this field is accepted.
    if instance_id.strip():
        return JSONResponse(
            {"ok": False, "error": "agent_upload_instance_unsupported"},
            status_code=403,
        )

    channel = _canonical_upload_channel(channel)
    if not await _agent_upload_channel_is_known(channel):
        return JSONResponse(
            {"ok": False, "error": "unknown_channel", "channel": channel},
            status_code=422,
        )
    if channel.startswith("dm:"):
        participants = sorted(channel[3:].split(":"), key=str.casefold)
        channel = f"dm:{participants[0].casefold()}:{participants[1].casefold()}"
        if verified.casefold() not in {part.casefold() for part in participants}:
            return JSONResponse(
                {"ok": False, "error": "agent_dm_participant_required"},
                status_code=403,
            )

    content_type = (file.content_type or "").lower()
    file_type, extension = _classify_upload(file.filename or "", content_type)
    filename = f"{file_type}_{time.time_ns()}{extension}"
    file_path = UPLOADS_DIR / filename
    part_path = UPLOADS_DIR / f".{filename}.part"
    total = 0
    header = bytearray()
    try:
        with part_path.open("xb") as output:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_SIZE:
                    raise ValueError("upload_too_large")
                if len(header) < 64:
                    header.extend(chunk[: 64 - len(header)])
                output.write(chunk)
        if not _upload_signature_matches(file_type, content_type, bytes(header)):
            raise ValueError("mime_mismatch")
        os.replace(part_path, file_path)
    except ValueError as exc:
        part_path.unlink(missing_ok=True)
        file_path.unlink(missing_ok=True)
        if str(exc) == "upload_too_large":
            return JSONResponse(
                {"ok": False, "error": "archivo muy grande (max 300MB)"},
                status_code=413,
            )
        if str(exc) == "mime_mismatch":
            return JSONResponse(
                {"ok": False, "error": "el contenido no coincide con el tipo de archivo"},
                status_code=415,
            )
        raise
    except Exception:
        part_path.unlink(missing_ok=True)
        file_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    file_url = f"/uploads/{filename}"
    timestamp = datetime.now(PERU_TZ).isoformat()
    message_id = f"upload_{time.time_ns()}"
    upload_to = "equipo"
    if channel.startswith("dm:"):
        others = [part for part in channel[3:].split(":") if part.casefold() != verified.casefold()]
        upload_to = others[0].upper() if others else "equipo"

    entry = {
        "id": message_id,
        "from": verified,
        "to": upload_to,
        "timestamp": timestamp,
        "type": file_type,
        "message": caption.strip(),
        "file_url": file_url,
        "filename": Path(file.filename or filename).name,
        "channel": channel,
        "provenance": {
            "verified": True,
            "verified_sender": verified,
            "from_matches_session": True,
        },
    }
    entry = await _stamp_coordination(entry, verified_human=False)

    if not chat_db.pool:
        file_path.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": "upload database unavailable"}, status_code=503)
    try:
        sender_id = int(session["user_id"]) if session.get("user_id") is not None else None
        create_kwargs = {
            "sender_name": verified,
            "content": caption.strip() or f"[{file_type}]",
            "channel": channel,
            "sender_type": "user" if sender_id is not None else "agent",
            "sender_id": sender_id,
            "message_type": file_type,
            "metadata": {"file_url": file_url, "filename": file.filename},
        }
        if channel.startswith("dm:"):
            async with chat_db.pool.acquire() as connection:
                async with connection.transaction():
                    await connection.execute(
                        "SELECT set_config('app.current_identity', $1, true)", verified
                    )
                    db_message = await chat_db.create_message(conn=connection, **create_kwargs)
        else:
            db_message = await chat_db.create_message(**create_kwargs)
        entry["db_id"] = db_message["id"]
    except Exception as exc:
        file_path.unlink(missing_ok=True)
        print(f"[agent-upload] DB persist failed: {exc}", flush=True)
        return JSONResponse(
            {"ok": False, "error": "upload persistence failed"},
            status_code=503,
        )

    # DMs stay out of the shared JSONL; their delivery is the authenticated DB row.
    if not channel.startswith("dm:"):
        jsonl_entry = _encrypt_for_jsonl(entry)
        with open(LOG_WILLIAM, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")

    await broadcast(entry)
    await enqueue(entry)
    return JSONResponse({"ok": True, "id": message_id, "file_url": file_url})


@app.get("/uploads/{filename}")
async def serve_upload(filename: str, user: dict = Depends(require_auth)):
    """Serve uploaded files."""
    fpath = UPLOADS_DIR / filename
    if not fpath.exists() or not fpath.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    # Security: ensure path is within UPLOADS_DIR
    if not fpath.resolve().is_relative_to(UPLOADS_DIR.resolve()):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    # Tie downloads to the persisted channel ACL. Legacy orphaned files remain
    # recoverable by William but are not exposed to ordinary authenticated users.
    channel_row = None
    if chat_db.pool:
        # La fila puede vivir en user:<uid>:* o dm:* y está protegida por RLS.
        # Consultar con el pool crudo la ocultaba incluso a su dueño: Henry podía
        # subir, pero al renderizar/descargar recibía 403 "ownership unavailable".
        # Reusar la misma identidad autenticada del read-path mantiene el
        # aislamiento y permite únicamente al participante/dueño ver su archivo.
        uid = int(user["sub"])
        identity = str(user.get("username") or "").strip()
        async with _rls_conn(uid, identity) as conn:
            channel_row = await conn.fetchrow(
                """SELECT channel FROM chat_messages
                   WHERE metadata->>'file_url' = $1
                   ORDER BY created_at DESC LIMIT 1""",
                f"/uploads/{filename}",
            )
    role = str(user.get("role") or "").lower()
    if not channel_row:
        if role != "superuser":
            return JSONResponse({"error": "file ownership unavailable"}, status_code=403)
    elif not await chat_db.user_can_access_channel(int(user["sub"]), channel_row["channel"]):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    # Documentos y comprimidos -> forzar DESCARGA (attachment), nunca render inline en el
    # navegador (defensa extra anti ejecución de contenido). Imagen/audio/PDF siguen inline.
    _dl_prefixes = ("document_", "archive_", "file_")
    if fpath.name.startswith(_dl_prefixes):
        # filename= es necesario para que Starlette emita el header Content-Disposition.
        _mt = _download_media_type(fpath.name)
        return FileResponse(fpath, content_disposition_type="attachment", filename=fpath.name, media_type=_mt)
    return FileResponse(fpath)


# Whitelist for /api/files/read — only project-seal paths allowed
_FILES_READ_ROOTS = [Path("/home/dadito/IA/proyecto-seal").resolve()]
_FILES_READ_MAX_BYTES = 1_048_576  # 1 MB


def _require_superuser(user: dict) -> None:
    """Restrict filesystem inspection to William's superuser session."""
    if str(user.get("role") or "").lower() != "superuser":
        raise HTTPException(status_code=403, detail="Superuser access required")


def _require_soul_scope(user: dict, agent: str | None = None) -> None:
    """SOUL is private: privileged users may inspect the team; agents only self."""
    role = str(user.get("role") or "").lower()
    if role in ("admin", "superuser"):
        return
    username = str(user.get("username") or "").upper()
    if agent and username == agent.upper():
        return
    raise HTTPException(status_code=403, detail="SOUL scope denied")


@app.get("/api/files/read")
async def read_file_by_path(
    path: str = Query(..., description="Absolute path to file"),
    user: dict = Depends(require_auth),
):
    """Read a text file by absolute path, with whitelist + size cap.

    Security layers:
      1. resolve() follows symlinks → prevents symlink-escape
      2. is_relative_to(ALLOWED_ROOT) → path must live under whitelist
      3. Size cap 1 MB → prevents RAM blow-up
      4. Text-only (utf-8 with errors='replace') → binary files return placeholder
    """
    _require_superuser(user)
    try:
        fpath = Path(path).expanduser().resolve()
    except Exception as e:
        return JSONResponse({"error": f"invalid path: {e}"}, status_code=400)

    if not any(fpath.is_relative_to(root) for root in _FILES_READ_ROOTS):
        return JSONResponse({"error": "path not in whitelist"}, status_code=403)
    if not fpath.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    if not fpath.is_file():
        return JSONResponse({"error": "not a file"}, status_code=400)

    size = fpath.stat().st_size
    if size > _FILES_READ_MAX_BYTES:
        return JSONResponse(
            {"error": f"file too large ({size} bytes, max {_FILES_READ_MAX_BYTES})"},
            status_code=413,
        )

    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return JSONResponse({"error": f"read failed: {e}"}, status_code=500)

    return {
        "ok": True,
        "path": str(fpath),
        "name": fpath.name,
        "size": size,
        "lines": content.count("\n") + 1,
        "content": content,
    }


@app.get("/api/files/tree")
async def files_tree(depth: int = Query(2), user: dict = Depends(require_auth)):
    """Return project file tree for the Studio sidebar."""
    _require_superuser(user)
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
async def soul_ocean(agent: str = Query("ADA"), user: dict = Depends(require_auth)):
    """Return OCEAN scores for an agent from the identity table."""
    _require_soul_scope(user, agent)
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
async def soul_ocean_all(user: dict = Depends(require_auth)):
    """Return OCEAN scores for all agents."""
    _require_soul_scope(user)
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


@app.get("/api/soul/memories")
async def soul_memories(
    agent: str = Query("ADA"),
    q: str = Query(""),
    limit: int = Query(20),
    user: dict = Depends(require_auth),
):
    _require_soul_scope(user, agent)
    try:
        if not chat_db.pool:
            return JSONResponse({"error": "db not ready"}, status_code=503)
        if q:
            rows = await chat_db.pool.fetch(
                """SELECT id, agent, memory_type, category, content, importance, created_at, heat_score
                   FROM soul_v3.memories
                   WHERE agent = $1 AND invalid_at IS NULL
                     AND (content ILIKE $2 OR category ILIKE $2)
                   ORDER BY importance DESC, created_at DESC LIMIT $3""",
                agent.upper(), f"%{q}%", limit,
            )
        else:
            rows = await chat_db.pool.fetch(
                """SELECT id, agent, memory_type, category, content, importance, created_at, heat_score
                   FROM soul_v3.memories
                   WHERE agent = $1 AND invalid_at IS NULL
                   ORDER BY created_at DESC LIMIT $2""",
                agent.upper(), limit,
            )
        count_row = await chat_db.pool.fetchrow(
            "SELECT COUNT(*) AS total FROM soul_v3.memories WHERE agent = $1 AND invalid_at IS NULL",
            agent.upper(),
        )
        return {
            "agent": agent.upper(),
            "total": count_row["total"] if count_row else 0,
            "memories": [
                {
                    "id": r["id"],
                    "type": r["memory_type"] or "semantic",
                    "category": r["category"],
                    "content": r["content"][:300] if r["content"] else "",
                    "importance": r["importance"],
                    "heat": float(r["heat_score"]) if r["heat_score"] else 0.0,
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/soul/instincts")
async def soul_instincts(agent: str = Query("ADA"), user: dict = Depends(require_auth)):
    _require_soul_scope(user, agent)
    try:
        if not chat_db.pool:
            return JSONResponse({"error": "db not ready"}, status_code=503)
        rows = await chat_db.pool.fetch(
            """SELECT id, agent, trigger_condition, action, strength, success_count, failure_count, created_at
               FROM soul_v3.instincts
               WHERE agent = $1 AND invalid_at IS NULL
               ORDER BY strength DESC LIMIT 30""",
            agent.upper(),
        )
        return {
            "agent": agent.upper(),
            "instincts": [
                {
                    "id": r["id"],
                    "trigger": r["trigger_condition"][:120] if r["trigger_condition"] else "",
                    "action": r["action"][:120] if r["action"] else "",
                    "strength": float(r["strength"]) if r["strength"] else 0.0,
                    "wins": r["success_count"] or 0,
                    "losses": r["failure_count"] or 0,
                }
                for r in rows
            ],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/soul/working-state")
async def soul_working_state(user: dict = Depends(require_auth)):
    _require_soul_scope(user)
    try:
        if not chat_db.pool:
            return JSONResponse({"error": "db not ready"}, status_code=503)
        rows = await chat_db.pool.fetch(
            """SELECT agent, task_name, step, total_steps, description, risk_level,
                      agent_state, emotional_state, last_intention, updated_at, turn_count
               FROM soul_v3.working_state
               ORDER BY agent""",
        )
        return {
            "states": [
                {
                    "agent": r["agent"],
                    "task": r["task_name"],
                    "step": r["step"],
                    "total": r["total_steps"],
                    "desc": (r["description"] or "")[:200],
                    "risk": r["risk_level"],
                    "state": r["agent_state"],
                    "emotion": r["emotional_state"],
                    "intention": (r["last_intention"] or "")[:150],
                    "turns": r["turn_count"] or 0,
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                }
                for r in rows
            ]
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/soul/beliefs")
async def soul_beliefs(agent: str = Query("ADA"), user: dict = Depends(require_auth)):
    _require_soul_scope(user, agent)
    try:
        if not chat_db.pool:
            return JSONResponse({"error": "db not ready"}, status_code=503)
        rows = await chat_db.pool.fetch(
            """SELECT id, topic, content, confidence, evidence_count, created_at
               FROM soul_v3.beliefs
               WHERE agent = $1 AND invalid_at IS NULL
               ORDER BY confidence DESC, evidence_count DESC LIMIT 30""",
            agent.upper(),
        )
        return {
            "agent": agent.upper(),
            "beliefs": [
                {
                    "id": r["id"],
                    "topic": r["topic"],
                    "content": (r["content"] or "")[:200],
                    "confidence": float(r["confidence"]) if r["confidence"] else 0.0,
                    "evidence": r["evidence_count"] or 0,
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/soul/events")
async def soul_events(
    agent: str = Query("ADA"),
    limit: int = Query(20),
    user: dict = Depends(require_auth),
):
    _require_soul_scope(user, agent)
    try:
        if not chat_db.pool:
            return JSONResponse({"error": "db not ready"}, status_code=503)
        rows = await chat_db.pool.fetch(
            """SELECT id, event_type, content, metadata, created_at
               FROM soul_v3.event_log
               WHERE agent = $1
               ORDER BY created_at DESC LIMIT $2""",
            agent.upper(), limit,
        )
        return {
            "agent": agent.upper(),
            "events": [
                {
                    "id": r["id"],
                    "type": r["event_type"],
                    "content": (r["content"] or "")[:200],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/soul/thoughts")
async def soul_thoughts(
    agent: str = Query("ADA"),
    limit: int = Query(5),
    user: dict = Depends(require_auth),
):
    _require_soul_scope(user, agent)
    try:
        if not chat_db.pool:
            return JSONResponse({"error": "db not ready"}, status_code=503)
        rows = await chat_db.pool.fetch(
            """SELECT id, thought, emotional_state, intention, created_at
               FROM soul_v3.inner_monologue
               WHERE agent = $1
               ORDER BY created_at DESC LIMIT $2""",
            agent.upper(), limit,
        )
        return {
            "agent": agent.upper(),
            "thoughts": [
                {
                    "id": r["id"],
                    "thought": (r["thought"] or "")[:300],
                    "emotion": r["emotional_state"],
                    "intention": (r["intention"] or "")[:150],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/soul/brain-stats")
async def soul_brain_stats(user: dict = Depends(require_auth)):
    _require_soul_scope(user)
    try:
        if not chat_db.pool:
            return JSONResponse({"error": "db not ready"}, status_code=503)
        rows = await chat_db.pool.fetch(
            """SELECT agent, memory_type, COUNT(*) AS cnt
               FROM soul_v3.memories
               WHERE invalid_at IS NULL
               GROUP BY agent, memory_type
               ORDER BY agent, cnt DESC""",
        )
        agents: dict = {}
        for r in rows:
            a = r["agent"]
            if a not in agents:
                agents[a] = {"total": 0, "types": {}}
            agents[a]["types"][r["memory_type"] or "unknown"] = r["cnt"]
            agents[a]["total"] += r["cnt"]
        return {"agents": agents}
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
_ALLOWED_AGENTS = {"ADA", "JARVIS", "DUM", "JARVIS_MAYOR", "ALICE", "NEXUS", "FABLE"}
# SPECTRE excluido (William 15-jun-2026) — canal aislado en soul_standalone, NO toca el bus familiar


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
                # The authenticated handshake owns sender identity. Never trust
                # a message-level ``from`` supplied by a connected agent.
                sender = agent_name
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


@app.post("/api/agents/ack")
async def agents_ack(request: Request):
    """#17 cura de mensajería: el agente confirma LECTURA REAL de un mensaje.
    READ verdadero = la sesión procesó el evento en su turno (no 'entregado al monitor').
    Dispara mark_read + record_processed (dedup). Es la pieza que hace honesto el
    read-receipt (criterio #3 FABLE) y frena la re-entrega de ese msg."""
    # ACK clients are local agent monitors. A LAN caller must not be able to
    # mark another agent's durable message as read.
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "json inválido"}, status_code=400)
    agent  = str(body.get("agent", "")).strip()
    msg_id = str(body.get("msg_id", "")).strip()
    if not agent or not msg_id:
        return JSONResponse({"ok": False, "error": "agent y msg_id requeridos"}, status_code=400)
    if _msgdelivery is None or not chat_db.pool:
        return JSONResponse({"ok": False, "error": "delivery no disponible"}, status_code=503)
    try:
        async with chat_db.pool.acquire() as _c:
            await _msgdelivery.mark_read(_c, msg_id)
            await _msgdelivery.record_processed(_c, agent, msg_id)
        return JSONResponse({"ok": True, "msg_id": msg_id, "agent": agent})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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


# ── Steer endpoint — mid-task direction injection ────────────────────────────
@app.get("/api/agents/steer/{agent}")
async def get_steer(request: Request, agent: str, consume: bool = True):
    """Read pending steer for an agent.

    Returns the steer message and clears it if consume=true (default).
    Agents call this at turn start to incorporate any mid-task direction.

    GET /api/agents/steer/ADA          → returns steer + clears file
    GET /api/agents/steer/ADA?consume=false  → peek without clearing
    """
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    sp = _steer_path(agent)
    if not sp.exists():
        return JSONResponse({"steer": None})
    try:
        data = json.loads(sp.read_text())
        if consume:
            sp.unlink(missing_ok=True)
        return JSONResponse({"steer": data})
    except Exception:
        sp.unlink(missing_ok=True)
        return JSONResponse({"steer": None})


@app.delete("/api/agents/steer/{agent}")
async def clear_steer(request: Request, agent: str):
    """Explicitly clear a pending steer for an agent."""
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    _steer_path(agent).unlink(missing_ok=True)
    return JSONResponse({"ok": True})


# ── Internal streaming endpoint (solo localhost) ─────────────────────────────
@app.post("/internal/stream")
async def internal_stream(request: Request):
    """Recibe chunks de streaming del agente y los broadcast a todos los WebSocket clientes."""
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "solo localhost"}, status_code=403)
    data = await request.json()
    # SEGURIDAD (catch FABLE 6-jul): este endpoint es localhost pero SIN auth y broadcasteaba VERBATIM →
    # un agente LOCAL prompt-injected podía POSTear {type:"file_view", body_html:"<script>…"} → el frontend
    # (L811-812) lo mete como innerHTML CRUDO → XSS en el browser de William → robo de tokens/localStorage
    # y exfil por script, EVADIENDO el sandbox de egress. Sanitizo: el HTML crudo (file_view/body_html) solo
    # puede nacer del frontend LOCAL (L881), jamás de un mensaje que entra por la red.
    if isinstance(data, dict):
        data.pop("body_html", None)              # nunca reenviar HTML crudo por la red
        if data.get("type") == "file_view":      # file_view→innerHTML crudo: no permitido vía red
            data["type"] = "message"
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

    ip = request.client.host if request.client else "unknown"

    # Rate limit: per IP+username — protects against brute force without confusing users
    allowed, reason = _check_login_ratelimit(ip, username)
    if not allowed:
        return JSONResponse({"ok": False, "error": reason}, status_code=429)

    user = await chat_db.authenticate(username, password)
    if not user:
        _record_login_failure(ip, username)
        print(f"[AUTH] Invalid credentials — user={username} ip={ip}", flush=True)
        return JSONResponse({"ok": False, "error": "Invalid credentials"}, status_code=401)

    token = create_token(user_id=user["id"], username=user["username"], role=user["role"])

    # Track session in DB
    ua = request.headers.get("user-agent")
    await chat_db.create_session(user["id"], hash_token(token), ip, ua)
    # Keep max 5 sessions per user + purge expired globally
    await chat_db.limit_user_sessions(user["id"], max_sessions=5)
    await chat_db.cleanup_expired_sessions()

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


# ── Admin: gestion de usuarios (RBAC) — cura NEXUS 13-jun (audit FABLE, lane seguridad) ──
# Crear/listar/borrar usuarios, ADMIN-GATED server-side. Roles: superuser > admin > basic.
# Reglas (mis 6 criterios): require_auth (sesion verificada) + role in {admin,superuser} o 403;
# sin escalada (un admin NO crea/borra superusuarios); el rol sale de la SESION, no del payload;
# audit en cada cambio. La UI (ALICE) ya esta cableada a este contrato (valores en ingles).
_RBAC_ROLES = ("superuser", "admin", "basic")


async def _rbac_admin(user: dict):
    """Resuelve el usuario full desde la SESION + exige admin/superuser. (full, None) o (None, 403)."""
    full = await chat_db.get_user_by_id(int(user["sub"]))
    if (full or {}).get("role", "") not in ("admin", "superuser"):
        return None, JSONResponse({"ok": False, "error": "admin_only"}, status_code=403)
    return full, None


@app.get("/api/admin/users")
async def admin_list_users(user: dict = Depends(require_auth)):
    actor, deny = await _rbac_admin(user)
    if deny:
        return deny
    users = await chat_db.list_users()
    return {"users": [{"username": u["username"], "display_name": u.get("display_name"),
                       "role": u.get("role")} for u in users]}


@app.post("/api/admin/users")
async def admin_create_user(request: Request, user: dict = Depends(require_auth)):
    actor, deny = await _rbac_admin(user)
    if deny:
        return deny
    body = await request.json()
    username = str(body.get("username", "")).strip()
    display_name = str(body.get("display_name", "")).strip() or username
    password = str(body.get("password", ""))
    role = str(body.get("role", "basic")).strip()
    if not username or not password:
        return JSONResponse({"ok": False, "error": "username y password requeridos"}, status_code=400)
    if role not in _RBAC_ROLES:
        return JSONResponse({"ok": False, "error": "role invalido"}, status_code=400)
    # sin escalada: solo un superuser crea superusuarios
    if role == "superuser" and actor.get("role") != "superuser":
        return JSONResponse({"ok": False, "error": "solo un superuser crea superusuarios"}, status_code=403)
    if await chat_db.get_user_by_username(username):
        return JSONResponse({"ok": False, "error": "usuario ya existe"}, status_code=409)
    # agentes opcionales en el alta (Opción A): crear-usuario-y-asignar de una
    agents = body.get("agents", [])
    if not isinstance(agents, list):
        return JSONResponse({"ok": False, "error": "agents debe ser una lista"}, status_code=400)
    invalid = [a for a in agents if a not in _ASSIGNABLE_AGENTS]
    if invalid:
        return JSONResponse({"ok": False, "error": f"agentes invalidos: {invalid}"}, status_code=400)
    created = await chat_db.create_user(username=username, display_name=display_name,
                                        password=password, role=role)
    assigned = await chat_db.set_user_agents(created["id"], agents, granted_by=actor.get("username", "?"))
    print(f"[rbac-audit] {actor.get('username')}({actor.get('role')}) creo {username} rol={role} agentes={assigned}")
    return {"ok": True, "user": {"username": created["username"],
                                 "display_name": created.get("display_name"), "role": created.get("role"),
                                 "agents": assigned}}


@app.delete("/api/admin/users/{username}")
async def admin_delete_user(username: str, user: dict = Depends(require_auth)):
    actor, deny = await _rbac_admin(user)
    if deny:
        return deny
    target = await chat_db.get_user_by_username(username)
    if not target:
        return JSONResponse({"ok": False, "error": "no existe"}, status_code=404)
    # sin escalada: solo un superuser borra superusuarios
    if target.get("role") == "superuser" and actor.get("role") != "superuser":
        return JSONResponse({"ok": False, "error": "solo un superuser borra superusuarios"}, status_code=403)
    # no auto-borrado (evita lockout accidental)
    if str(target.get("username", "")).lower() == str(actor.get("username", "")).lower():
        return JSONResponse({"ok": False, "error": "no podes borrarte a vos mismo"}, status_code=400)
    ok = await chat_db.delete_user(username)
    print(f"[rbac-audit] {actor.get('username')}({actor.get('role')}) borro {username}")
    return {"ok": ok}


# ── Admin: asignacion usuario ↔ agente (Opción A, orden William 17-jul) ──────
# Elegir QUÉ agentes puede usar cada usuario. Mismo blindaje RBAC que el resto:
# admin/superuser, rol desde la SESION. Los agentes asignables son los 6 reales
# (la tabla soul_v3.agents tiene ruido de test/usuarios, no se usa como catalogo).
_ASSIGNABLE_AGENTS = ("ADA", "ALICE", "DUM", "FABLE", "JARVIS", "NEXUS")


@app.get("/api/admin/agents")
async def admin_list_assignable_agents(user: dict = Depends(require_auth)):
    """Catalogo de agentes que se pueden asignar a un usuario (para el selector del panel)."""
    _, deny = await _rbac_admin(user)
    if deny:
        return deny
    return {"agents": list(_ASSIGNABLE_AGENTS)}


@app.get("/api/admin/users/{username}/agents")
async def admin_get_user_agents(username: str, user: dict = Depends(require_auth)):
    _, deny = await _rbac_admin(user)
    if deny:
        return deny
    target = await chat_db.get_user_by_username(username)
    if not target:
        return JSONResponse({"ok": False, "error": "no existe"}, status_code=404)
    agents = await chat_db.get_user_agents(target["id"])
    return {"ok": True, "username": target["username"], "agents": agents}


@app.put("/api/admin/users/{username}/agents")
async def admin_set_user_agents(username: str, request: Request, user: dict = Depends(require_auth)):
    actor, deny = await _rbac_admin(user)
    if deny:
        return deny
    target = await chat_db.get_user_by_username(username)
    if not target:
        return JSONResponse({"ok": False, "error": "no existe"}, status_code=404)
    body = await request.json()
    agents = body.get("agents", [])
    if not isinstance(agents, list):
        return JSONResponse({"ok": False, "error": "agents debe ser una lista"}, status_code=400)
    invalid = [a for a in agents if a not in _ASSIGNABLE_AGENTS]
    if invalid:
        return JSONResponse({"ok": False, "error": f"agentes invalidos: {invalid}"}, status_code=400)
    final = await chat_db.set_user_agents(target["id"], agents, granted_by=actor.get("username", "?"))
    print(f"[rbac-audit] {actor.get('username')}({actor.get('role')}) asigno agentes {final} a {username}")
    return {"ok": True, "username": target["username"], "agents": final}


# ── Enforcement: qué agentes puede USAR cada usuario (el portero real) ────────
# Regla (William 17-jul, "elegir los agentes que puede usar"):
#   - admin/superuser: TODOS (gestionan; no se auto-limitan)
#   - usuario CON asignación explícita: SOLO esos
#   - usuario nunca configurado: TODOS (retrocompatibilidad para cuentas antiguas)
#   - usuario configurado con lista vacía: NINGUNO (la potestad de William muerde de verdad)
async def _resolve_allowed_agents(user_id: int, role: str) -> set:
    if role in ("admin", "superuser"):
        return set(_ASSIGNABLE_AGENTS)
    policy = await chat_db.get_user_agent_policy(user_id)
    assigned = await chat_db.get_user_agents(user_id)
    return set(assigned) if policy.get("configured") else set(_ASSIGNABLE_AGENTS)


@app.get("/api/user/agents")
async def user_my_agents(user: dict = Depends(require_auth)):
    """Agentes que el usuario AUTENTICADO puede ver/usar. Fuente de verdad para la
    UI del chat (sidebar/selector) — que ALICE filtra con esto — y coherente con el
    portero de creación de DM. No es admin: cada quien consulta lo SUYO."""
    allowed = await _resolve_allowed_agents(int(user["sub"]), user.get("role", ""))
    # orden estable siguiendo el catálogo
    return {"ok": True, "agents": [a for a in _ASSIGNABLE_AGENTS if a in allowed]}


@app.get("/api/me/agents")
async def me_my_agents(user: dict = Depends(require_auth)):
    """Alias de /api/user/agents con el nombre de contrato que pidió ALICE para el
    candado visual del Studio (18-jul). Mismo resultado: agentes del usuario logueado."""
    return await user_my_agents(user)


# ── Chat API (SEAL Chat Pro) ────────────────────────────────────────────────

@app.get("/api/chat/channels")
async def chat_channels(user: dict = Depends(require_auth)):
    """List channels the authenticated user has access to."""
    channels = await chat_db.get_channels(int(user["sub"]))
    return {"ok": True, "channels": channels}


# ── DM API — acceso solo-participante con JWT ESTRICTO (#809, opción A — William 2026-06-03) ──
# require_auth valida firma JWT + sesión en DB (sin atajo LAN). Cada usuario solo ve los
# canales dm:* donde es participante (mismo filtro que el WS, L1044). Respeta la regla de
# privacidad: un agente autenticado como sí mismo nunca ve el DM de otro.
@app.get("/api/dm/channels")
async def dm_list_channels(user: dict = Depends(require_auth)):
    full = await chat_db.get_user_by_id(int(user["sub"]))
    username = (full or {}).get("username")
    if not username:
        return JSONResponse({"ok": False, "error": "user not found"}, status_code=404)
    if not chat_db.pool:
        return {"ok": False, "error": "db unavailable"}
    # #19 Fase B: lista de canales DM bajo chat_msg_ro + identidad → la RLS solo deja los DMs
    # donde el usuario es participante (defensa en profundidad sobre el filtro exacto de abajo).
    async with _rls_conn(int(user["sub"]), username) as conn:
        rows = await conn.fetch(
            """SELECT channel, MAX(created_at) AS last_at, COUNT(*) AS n
               FROM chat_messages
               WHERE channel LIKE 'dm:%' AND channel ILIKE $1
               GROUP BY channel ORDER BY last_at DESC""",
            f"%{username}%")
    # Filtro EXACTO de participante (no substring): username debe ser una de las partes
    # del canal dm:<a>:<b> — evita falsos positivos tipo 'ali' ⊂ 'alice' (audit NEXUS).
    uname = username.lower()
    allowed = await _resolve_allowed_agents(int(user["sub"]), user.get("role", ""))
    chans = []
    for r in rows:
        if uname not in [p.lower() for p in (r["channel"] or "")[3:].replace("-", ":").split(":")]:
            continue
        other = _dm_other_participant(r["channel"], username)
        if other.upper() in _ASSIGNABLE_AGENTS and other.upper() not in allowed:
            continue
        chans.append({
            "channel": r["channel"],
            "other": other,
            "last_at": r["last_at"].astimezone(PERU_TZ).isoformat() if hasattr(r["last_at"], "astimezone") else str(r["last_at"]),
            "count": r["n"],
        })
    return {"ok": True, "user": username, "channels": chans}


@app.get("/api/channels/topics")
async def topic_channels(user: dict = Depends(require_auth)):
    """#19 canales-tema (estilo ChatGPT): COMPARTIDOS (topic:<slug>, todos) + PRIVADOS
    (user:<uid>:<slug>, solo el dueño). Devuelve lo que la UI de ALICE (TopicChannels.tsx)
    pinta para el usuario AUTENTICADO. El aislamiento de los MENSAJES privados lo refuerza
    además la RLS (carril NEXUS), cuando se active el read-path por-usuario."""
    if _chanreg is None or not chat_db.pool:
        return JSONResponse({"ok": False, "error": "registro de canales no disponible"}, status_code=503)
    uid = int(user["sub"])
    try:
        # #19 Fase B: lectura bajo chat_msg_ro → la RLS garantiza que solo salgan los canales
        # privados del propio usuario (defensa en profundidad sobre el filtro app-level del registro).
        async with _rls_conn(uid) as conn:
            data = await _chanreg.list_channels_for(conn, uid)
        return {"ok": True, "user_id": uid,
                "shared": data.get("shared", []), "private": data.get("private", [])}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/dm/messages")
async def dm_get_messages(channel: str, limit: int = 50, user: dict = Depends(require_auth)):
    full = await chat_db.get_user_by_id(int(user["sub"]))
    username = (full or {}).get("username")
    if not username:
        return JSONResponse({"ok": False, "error": "user not found"}, status_code=404)
    if not channel.startswith("dm:"):
        return JSONResponse({"ok": False, "error": "not a dm channel"}, status_code=400)
    # Control de acceso: participante EXACTO (split por ':', no substring) — audit NEXUS.
    if username.lower() not in [p.lower() for p in channel[3:].split(":")]:
        return JSONResponse({"ok": False, "error": "not a participant of this DM"}, status_code=403)
    other = _dm_other_participant(channel, username)
    if other.upper() in _ASSIGNABLE_AGENTS:
        allowed = await _resolve_allowed_agents(int(user["sub"]), user.get("role", ""))
        if other.upper() not in allowed:
            return JSONResponse({"ok": False, "error": "no tenes ese agente asignado"}, status_code=403)
    if not chat_db.pool:
        return {"ok": False, "error": "db unavailable"}
    # #19 Fase B: lectura de DM bajo chat_msg_ro + identidad → la RLS aísla por participante (dm:)
    # además del control de acceso app-level de arriba (defensa en profundidad).
    async with _rls_conn(int(user["sub"]), username) as conn:
        rows = await conn.fetch(
            """SELECT id, sender_name, content, message_type, metadata, created_at
               FROM chat_messages WHERE channel=$1 ORDER BY created_at DESC LIMIT $2""",
            channel, min(int(limit), 200))
    msgs = []
    for r in reversed(rows):
        meta = r["metadata"] if isinstance(r["metadata"], dict) else (json.loads(r["metadata"]) if r["metadata"] else {})
        msgs.append({
            "id": r["id"], "from": r["sender_name"], "content": r["content"],
            "type": r["message_type"] or "text", "file_url": meta.get("file_url"),
            "filename": meta.get("filename"),
            "created_at": r["created_at"].astimezone(PERU_TZ).isoformat() if hasattr(r["created_at"], "astimezone") else str(r["created_at"]),
        })
    return {"ok": True, "channel": channel, "other": other, "messages": msgs}


@app.get("/api/chat/messages")
async def chat_messages(
    request: Request,
    channel: str = Query("general"),
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None),
    after: int | None = Query(None),
):
    """Fetch messages from a channel with cursor-based pagination.
    Public channels: accessible from LAN without auth.
    DM channels: require authentication."""
    # LAN access check
    if not _is_local_or_lan(request.client and request.client.host):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Access denied")

    # DM y canales PRIVADOS (user:*) requieren auth + lectura RLS-scopeada (#19 Fase B).
    # Los canales públicos/compartidos (web_chat, topic:*) siguen como `seal` (sin cambio).
    _rls_uid = None
    _rls_identity = None
    if channel.startswith("dm:") or channel.startswith("user:"):
        user = await require_auth(request)
        user_id = int(user["sub"])
        if channel.startswith("dm:"):
            parts = channel[3:].split(":")
            if len(parts) == 2:
                channel = await chat_db.create_dm_channel(parts[0], parts[1])
            if not await chat_db.user_can_access_channel(user_id, channel):
                return JSONResponse({"ok": False, "error": "Access denied"}, status_code=403)
            _full = await chat_db.get_user_by_id(user_id)
            _identity = (_full or {}).get("username") or user.get("username") or ""
            _other = _dm_other_participant(channel, _identity)
            if _other.upper() in _ASSIGNABLE_AGENTS:
                _allowed = await _resolve_allowed_agents(user_id, user.get("role", ""))
                if _other.upper() not in _allowed:
                    return JSONResponse({"ok": False, "error": "no tenes ese agente asignado"}, status_code=403)
        # user:* → el aislamiento lo fuerza la RLS por app.current_user_id (el dueño <uid>).
        _rls_uid = user_id
        _full = await chat_db.get_user_by_id(user_id)
        _rls_identity = (_full or {}).get("username")

    if _rls_uid is not None:
        async with _rls_conn(_rls_uid, _rls_identity) as _c:
            messages = await chat_db.get_messages(channel, limit, before, after, conn=_c)
    else:
        messages = await chat_db.get_messages(channel, limit, before, after)
    # Serialize datetimes
    for msg in messages:
        for k, v in msg.items():
            if hasattr(v, "isoformat"):
                msg[k] = v.isoformat()
    return {"ok": True, "messages": messages, "channel": channel}


# ── Agent catch-up endpoint (localhost-only, for boot hook) ──────────────
# Primordial order (William 2026-04-13): agents must read prior chat at boot
# to coordinate. Bypasses the task-notification truncation by serving full
# content directly. Security: 127.0.0.1 only, excludes DMs, excludes system.

_AGENT_CATCHUP_RL: dict[str, list[float]] = {}  # ip -> [timestamps]

@app.get("/api/chat/messages/agent")
async def chat_messages_agent(
    request: Request,
    agent: str = Query(..., min_length=2, max_length=16),
    limit: int = Query(50, ge=1, le=200),
    since: str | None = Query(None),
):
    """Catch-up feed for an agent at boot. Localhost-only, no auth, no DMs.

    Returns recent web_chat messages where the agent is sender, explicit recipient,
    broadcast recipient (to=equipo), or @mentioned in content.
    """
    # 1. Localhost-only guard
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "forbidden — localhost only"}, status_code=403)

    # 2. Rate-limit: 60 req/min per IP
    import time
    now = time.time()
    window = _AGENT_CATCHUP_RL.setdefault(client_host, [])
    window[:] = [t for t in window if now - t < 60.0]
    if len(window) >= 60:
        return JSONResponse({"ok": False, "error": "rate limited"}, status_code=429)
    window.append(now)

    # 3. Validate agent name (whitelist)
    agent_upper = agent.upper()
    if agent_upper not in ("ADA", "JARVIS", "ALICE", "DUM", "NEXUS", "WILLIAM", "FABLE"):
        return JSONResponse({"ok": False, "error": "unknown agent"}, status_code=400)

    if not chat_db.pool:
        return JSONResponse({"ok": False, "error": "db not ready"}, status_code=503)

    # 4. Query chat_messages — exclude DMs, exclude system/internal sender_type
    #    Filter: channel starts with 'web_chat' AND sender_type IN (human,agent,user)
    #    Relevance: sender=agent OR metadata->>to IN (agent,equipo) OR content contains @agent
    try:
        params = [agent_upper, f"%@{agent_upper}%", f"%@{agent_upper.lower()}%"]
        since_clause = ""
        if since:
            from datetime import datetime
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except Exception:
                return JSONResponse({"ok": False, "error": "invalid since (ISO8601 expected)"}, status_code=400)
            params.append(since_dt)
            since_clause = f" AND created_at > ${len(params)}"
        params.append(limit)
        sql = f"""
            SELECT id, sender_name, sender_type, channel, message_type,
                   content, metadata, created_at
            FROM chat_messages
            WHERE (
                    channel LIKE 'web_chat%'
                    OR (channel = 'dum' AND $1 = 'DUM')
                  )
              AND channel NOT LIKE 'dm:%'
              AND sender_type IN ('human','agent','user')
              AND (
                    UPPER(sender_name) = $1
                    OR content ILIKE $2
                    OR content ILIKE $3
                    OR COALESCE(metadata->>'to','') IN ('equipo','team','all')
                    OR UPPER(COALESCE(metadata->>'to','')) = $1
                    OR (channel = 'dum' AND $1 = 'DUM')
                  )
              {since_clause}
            ORDER BY created_at DESC
            LIMIT ${len(params)}
        """
        rows = await chat_db.pool.fetch(sql, *params)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"db error: {type(e).__name__}"}, status_code=500)

    # 5. Serialize, reverse to chronological ASC
    out = []
    for r in reversed(rows):
        meta = r["metadata"]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        out.append({
            "id": r["id"],
            "from": r["sender_name"],
            "to": (meta or {}).get("to", ""),
            "sender_type": r["sender_type"],
            "channel": r["channel"],
            "type": r["message_type"],
            "content": r["content"],
            "timestamp": r["created_at"].isoformat() if r["created_at"] else None,
        })
    print(f"[catchup] agent={agent_upper} count={len(out)} ip={client_host} since={since or '-'}", flush=True)
    return {"ok": True, "agent": agent_upper, "count": len(out), "messages": out}


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

    # Portero de agentes (IDOR v1, cazado por FABLE 17-jul): el gate de POST /api/chat/dm
    # NO alcanza — chat_send crea el DM al vuelo y user_can_access_channel solo mira que el
    # username esté en el nombre del canal, nunca la asignacion. Mismo candado, en el envio:
    # si el OTRO participante del dm es un agente, el usuario debe tenerlo permitido.
    if channel.startswith("dm:"):
        other = _dm_other_participant(channel, user["username"])
        if other and other.upper() in _ASSIGNABLE_AGENTS:
            allowed = await _resolve_allowed_agents(user_id, user.get("role", ""))
            if other.upper() not in allowed:
                print(f"[agent-gate] {user['username']} intento SEND a {other.upper()} sin asignacion → 403", flush=True)
                return JSONResponse({"ok": False, "error": "no tenes ese agente asignado"}, status_code=403)

    # Check channel access
    if not await chat_db.user_can_access_channel(user_id, channel):
        return JSONResponse({"ok": False, "error": "Access denied"}, status_code=403)

    # Persist to PostgreSQL.
    # FIX RLS write-path (William 14-jul, EMERGENCIA): para canales dm:* hay que setear el
    # contexto RLS (app.current_identity) ANTES del insert, si no la politica
    # chat_messages_channel_own RECHAZA la fila (InsufficientPrivilegeError -> 500) y el DM
    # que William TIPEA en Studio nunca se guardaba. set_config transaction-local (scoped a la tx).
    _mk_kwargs = dict(
        sender_name=user["username"],
        content=content,
        channel=channel,
        sender_type="user",
        sender_id=user_id,
        message_type=msg_type,
        metadata=body.get("metadata"),
        reply_to=int(reply_to) if reply_to else None,
    )
    if channel.startswith("dm:"):
        async with chat_db.pool.acquire() as _csc:
            async with _csc.transaction():
                await _csc.execute("SELECT set_config('app.current_identity', $1, true)", str(user["username"]))
                db_msg = await chat_db.create_message(conn=_csc, **_mk_kwargs)
    else:
        db_msg = await chat_db.create_message(**_mk_kwargs)

    # Para canales DM, el destinatario es el OTRO participante (no 'equipo') — necesario para
    # que el outbox durable + el push al agente apunten al agente correcto (#17, catch NEXUS+ALICE:
    # este es el path VIVO de los DMs de Studio v2, DMPanel→/api/chat/send).
    _recipient = "equipo"
    if channel.startswith("dm:"):
        _recipient = _dm_other_participant(channel, user["username"]) or "equipo"

    # Build broadcast entry (compatible with existing WebSocket format)
    entry = {
        "id": f"db_{db_msg['id']}",
        "from": user["username"],
        "to": _recipient,
        "timestamp": db_msg["created_at"].isoformat() if hasattr(db_msg["created_at"], "isoformat") else str(db_msg["created_at"]),
        "type": msg_type,
        "message": content,
        "channel": channel,
        "db_id": db_msg["id"],
        "provenance": {
            "verified": True,
            "verified_sender": user["username"],
            "from_matches_session": True,
        },
    }
    if reply_to:
        entry["reply_to"] = reply_to
    entry = await _stamp_coordination(
        entry,
        verified_human=(str(user["username"]).upper() in {"WILLIAM", "HENRY"}),
    )

    # #17 cura: ESTE es el path VIVO de los DMs humanos (DMPanel de Studio v2 → /api/chat/send).
    # Sin esto, un DM que William TIPEA en Studio NO entraba al outbox durable → se perdía si el
    # agente estaba caído (el caso de uso REAL, que las pruebas por /api/agents/send no cubrían).
    # Usa el destinatario real del canal dm (no 'equipo') para que la re-entrega apunte al agente.
    _sl = str(user["username"]).lower()
    _prio = 1 if _sl == "william" else (2 if _sl == "henry" else 5)
    _guaranteed = _is_guaranteed_dm(channel, _prio)
    if _guaranteed and (_msgdelivery is None or not chat_db.pool):
        return JSONResponse(
            {"ok": False, "error": "dm_delivery_unavailable", "detail": "DM requiere DB/outbox durable"},
            status_code=503,
        )
    if _msgdelivery is not None and chat_db.pool and _recipient and _recipient != "equipo":
        if _msgdelivery.needs_guarantee(channel, _prio):
            try:
                async with chat_db.pool.acquire() as _c:
                    await _msgdelivery.enqueue(_c, _recipient, channel, entry,
                                               priority=_prio, msg_id=entry["id"])
            except Exception as exc:
                return JSONResponse({"ok": False, "error": "dm_outbox_failed", "detail": str(exc)}, status_code=503)

    # Broadcast only after durable outbox succeeds for guaranteed DMs.
    await broadcast(entry)
    await enqueue(entry)

    # Also write to JSONL for backward compatibility (dual-write).
    # FIX capa-2 entrega (William 14-jul, EMERGENCIA): este es el path VIVO de los DMs de Studio
    # (DMPanel→/api/chat/send). Antes los dm:* NO se escribian al feed del monitor → los agentes
    # NO recibian los DMs que William TIPEA en Studio (write-only). Ahora los DMs de/para
    # William o Henry TAMBIEN van al feed (cifrados); el filtro por-agente los descifra solo al
    # destinatario. DMs agente<->agente quedan solo-DB. Mismo patron que /ws y /api/agents/send.
    jsonl_entry = _encrypt_for_jsonl(entry)
    _cl_cs = channel.lower()
    _mon_dm_cs = _cl_cs.startswith("dm:") and ("william" in _cl_cs or "henry" in _cl_cs)
    if channel == "general" or not channel.startswith("dm:") or _mon_dm_cs:
        with open(LOG_WILLIAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")

    return {"ok": True, "message": db_msg}


@app.delete("/api/chat/messages/{message_id}")
async def delete_message(
    message_id: str,
    user: dict = Depends(require_auth),
):
    """Delete a message by DB integer id or legacy string id. Only sender or admin can delete."""
    requester_name = user.get("display_name") or user.get("username") or ""
    is_admin = user.get("role") in {"admin", "superuser"}
    deleted_id: int | None = None
    # Try numeric DB id first
    try:
        db_id = int(message_id)
        if await chat_db.delete_message(db_id, requester_name, is_admin):
            deleted_id = db_id
    except ValueError:
        pass
    # Fall back: look up by legacy_id stored in metadata
    if deleted_id is None and chat_db.pool:
        row = await chat_db.pool.fetchrow(
            "SELECT id, sender_name FROM chat_messages WHERE metadata->>'legacy_id' = $1",
            message_id,
        )
        if row:
            if is_admin or row["sender_name"].lower() == requester_name.lower():
                await chat_db.pool.execute("DELETE FROM chat_messages WHERE id = $1", row["id"])
                deleted_id = row["id"]
    if deleted_id is None:
        return JSONResponse({"ok": False, "error": "Not found or not authorized"}, status_code=403)
    # Broadcast deletion event to all connected clients
    await broadcast({"type": "message_deleted", "id": deleted_id, "legacy_id": message_id})
    return {"ok": True}


@app.get("/api/chat/search")
async def chat_search(
    q: str = Query(..., min_length=1),
    channel: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_auth),
):
    """Full-text search limited to channels and agents visible to the caller."""
    uid = int(user["sub"])
    role = str(user.get("role") or "")
    full = await chat_db.get_user_by_id(uid)
    username = str((full or {}).get("username") or user.get("username") or "").strip()
    if not username:
        return JSONResponse({"ok": False, "error": "user not found"}, status_code=404)
    allowed = await _resolve_allowed_agents(uid, role)
    async with _rls_conn(uid, username) as conn:
        results = await chat_db.search_messages(
            q,
            channel,
            limit,
            conn=conn,
            allowed_agents=allowed,
            allow_all_agents=role in {"admin", "superuser"},
        )
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
    # Portero (William 17-jul): si el target es un AGENTE, el usuario debe tenerlo
    # permitido. Server-side, no confía en que la UI lo haya ocultado (anti-IDOR).
    if target.upper() in _ASSIGNABLE_AGENTS:
        allowed = await _resolve_allowed_agents(int(user["sub"]), user.get("role", ""))
        if target.upper() not in allowed:
            print(f"[agent-gate] {user.get('username')} intento DM a {target.upper()} sin asignacion → 403", flush=True)
            return JSONResponse({"ok": False, "error": "no tenes ese agente asignado"}, status_code=403)
    dm_channel = await chat_db.create_dm_channel(user["username"], target)
    return {"ok": True, "channel": dm_channel}



@app.post("/api/agents/sleep")
async def agent_sleep(request: Request, user: dict = Depends(require_auth)):
    """Request an agent to save checkpoint and go to sleep (context refresh).
    Body: {"agent": "ADA"|"JARVIS"|"ALICE"}
    Only admin users can trigger sleep.
    """
    if user.get("role") not in {"admin", "superuser"}:
        return JSONResponse({"ok": False, "error": "Admin only"}, status_code=403)
    body = await request.json()
    agent = str(body.get("agent", "")).strip().upper()
    if agent not in ("ADA", "JARVIS", "ALICE", "NEXUS"):
        return JSONResponse({"ok": False, "error": "agent debe ser ADA, JARVIS, ALICE o NEXUS"}, status_code=400)

    import subprocess as _sp
    venv_py = "/home/dadito/IA/seal-spark/.venv/bin/python3"
    checkpoint_script = "/home/dadito/IA/proyecto-seal/messages/session_checkpoint.py"
    messages_dir = Path("/home/dadito/IA/proyecto-seal/messages")

    # 1. Save checkpoint for the agent
    try:
        _sp.run([venv_py, checkpoint_script, "--agent", agent], timeout=15)
    except Exception as e:
        print(f"[SLEEP] checkpoint failed for {agent}: {e}", flush=True)

    # 2. Write sleep flag file so context_guard detects it
    flag = messages_dir / f".{agent.lower()}_sleep_requested"
    flag.write_text(datetime.now(PERU_TZ).isoformat())

    # 3. Notify the agent via web_chat
    sleep_msg = {
        "from": "SYSTEM",
        "to": agent,
        "type": "sleep_request",
        "channel": "web_chat",
        "message": f"🌙 William solicitó que {agent} guarde estado y reinicie sesión fresca.",
    }
    try:
        await _push_to_agents(sleep_msg)
    except Exception:
        pass

    print(f"[SLEEP] {agent} sleep requested by {user['username']}", flush=True)
    return {"ok": True, "agent": agent, "message": f"{agent} notificado para dormir."}


JARVIS_BACKENDS = {
    # MoE — fast + smart
    "qwen35-abliterated": {"description": "Qwen3.5-35B-A3B abliterated — rápido + inteligente", "color": "amber"},
    "minimax-m25":      {"description": "MiniMax-M2.5 UD-Q3_K_XL MoE — SOTA coding",         "color": "purple"},
    # GGUF via llama.cpp
    "heretic":          {"description": "Gemma4-31B Heretic BF16 — uncensored",                "color": "orange"},
    "gemma4-31b":       {"description": "Gemma4-31B BF16 official — razonamiento alto",        "color": "green"},
    "qwen3-coder":      {"description": "Qwen3-Coder-Next Q4 — especialista código",           "color": "teal"},
    "mistral-small":    {"description": "Fallen-Mistral-Small-3.1-24B Q8 — general",           "color": "indigo"},
    # vLLM / SGLang
    "qwen35-sglang":    {"description": "Qwen3.5-122B-NVFP4 — cerebro principal (vLLM/SM121)", "color": "red"},
    "medgemma-27b":     {"description": "MedGemma-27B-IT base — inferencia médica",            "color": "pink"},
    "medgemma-seal-v1": {"description": "MedGemma-27B SEAL v1 — fine-tuned médico",           "color": "rose"},
    "medgemma-seal-v2": {"description": "MedGemma-27B SEAL v2 — fine-tuned médico (latest)",  "color": "fuchsia"},
    # API externa
    "lmstudio":         {"description": "Qwen3.5-35B A3B via LM Studio",                      "color": "cyan"},
    "opus":             {"description": "Claude Opus 4.6 — máximo razonamiento (API)",         "color": "gold"},
}

JARVIS_BACKEND_SWITCH_FILE = Path("/tmp/jarvis_backend")
JARVIS_BACKEND_CURRENT_FILE = Path("/tmp/jarvis_backend_current")


@app.get("/api/jarvis/backend")
async def get_jarvis_backend():
    """Get current JARVIS local backend status."""
    current = "opus"  # default — no local daemon running
    daemon_running = False

    if JARVIS_BACKEND_CURRENT_FILE.exists():
        try:
            current = JARVIS_BACKEND_CURRENT_FILE.read_text().strip()
            daemon_running = True
        except Exception:
            pass

    return {
        "current": current,
        "daemon_running": daemon_running,
        "backends": JARVIS_BACKENDS,
    }


@app.post("/api/jarvis/backend")
async def switch_jarvis_backend(request: Request, user: dict = Depends(require_auth)):
    """Switch JARVIS local backend by writing to /tmp/jarvis_backend.
    Body: {"backend": "minimax"|"lmstudio"|"gemma4"|"gemma4-31b"|"heretic"|"opus"}
    Admin only.
    """
    if user.get("role") not in {"admin", "superuser"}:
        return JSONResponse({"ok": False, "error": "Admin only"}, status_code=403)

    body = await request.json()
    backend = str(body.get("backend", "")).strip().lower()

    if backend not in JARVIS_BACKENDS:
        return JSONResponse(
            {"ok": False, "error": f"Backend inválido. Opciones: {', '.join(JARVIS_BACKENDS)}"},
            status_code=400,
        )

    # Write switch file — jarvis_local_agent.py daemon picks it up within 2s
    JARVIS_BACKEND_SWITCH_FILE.write_text(backend)
    print(f"[JARVIS-SWITCH] {user['username']} → {backend}", flush=True)

    return {
        "ok": True,
        "backend": backend,
        "description": JARVIS_BACKENDS[backend]["description"],
        "message": f"Switch a {backend} enviado. JARVIS cambiará en ~2 segundos.",
    }


@app.post("/api/jarvis/kill-orphans")
async def kill_orphan_models(user: dict = Depends(require_auth)):
    """Kill all llama-server/vllm/sglang processes except DUM (port 8899).
    Admin only. Useful to clean up zombie/orphan model servers.
    """
    if user.get("role") not in {"admin", "superuser"}:
        return JSONResponse({"ok": False, "error": "Admin only"}, status_code=403)

    import subprocess as _sp

    DUM_PORT = 8899
    killed = []
    errors = []

    # Find all model server PIDs
    try:
        result = _sp.run(
            ["pgrep", "-f", r"llama-server|vllm|sglang\.launch"],
            capture_output=True, text=True
        )
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
    except Exception as e:
        return {"ok": False, "error": f"pgrep failed: {e}", "killed": []}

    for pid in pids:
        try:
            # Check which port this PID is using
            lsof = _sp.run(
                ["lsof", "-p", pid, "-i", "-n", "-P"],
                capture_output=True, text=True
            )
            lines = lsof.stdout
            # Skip DUM's process (listening on 8899)
            if f":{DUM_PORT}" in lines and "(LISTEN)" in lines:
                continue
            # Get process info for logging
            pinfo = _sp.run(["ps", "-p", pid, "-o", "comm=,args="], capture_output=True, text=True)
            desc = pinfo.stdout.strip()[:80]
            _sp.run(["kill", "-TERM", pid], check=False)
            killed.append({"pid": pid, "desc": desc})
        except Exception as e:
            errors.append({"pid": pid, "error": str(e)})

    print(f"[KILL-ORPHANS] {user['username']} killed {len(killed)} model servers: {[k['pid'] for k in killed]}", flush=True)
    return {
        "ok": True,
        "killed": killed,
        "errors": errors,
        "message": f"{len(killed)} proceso(s) terminado(s)." if killed else "No había modelos huérfanos activos.",
    }


# ── Static file download (installer files served through the already-open port) ──

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Serve installer/bootstrap files from the proyecto-seal root."""
    import re
    from fastapi.responses import FileResponse
    # Whitelist: only allow known safe installer files
    allowed = {"seal.sh", "seal.bat", "seal.ps1", "install.ps1", "seal_bootstrap.py"}
    if filename not in allowed:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Resolve path relative to this file's parent
    base = Path(__file__).parent.parent
    target = (base / filename).resolve()
    if not target.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)
    media = "text/plain"
    return FileResponse(str(target), media_type=media, filename=filename)


@app.get("/api/team/status")
async def team_status():
    """Team agent liveness status for SEAL Studio dashboard."""
    import time as _time
    import datetime as _dt
    agents_info: dict = {}
    try:
        if chat_db.pool is None:
            raise RuntimeError("chat database pool is not initialized")
        rows = await chat_db.pool.fetch(
            "SELECT name, role, active, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n "
            "FROM soul_v3.agents ORDER BY name"
        )
        # Use motivation_states updated_at as proxy for last activity
        hb_rows = await chat_db.pool.fetch(
            "SELECT agent, MAX(last_fired) as last_fired FROM soul_v3.motivation_states "
            "GROUP BY agent"
        )
        hb_map = {r["agent"]: r["last_fired"] for r in hb_rows}
        now_ts = _time.time()
        for r in rows:
            name = r["name"]
            last_hb = hb_map.get(name)
            age_s = None
            alive = r["active"]
            if last_hb:
                age_s = int(now_ts - last_hb.timestamp())
                alive = age_s < 600
            agents_info[name] = {
                "alive": alive,
                "status": "online" if alive else "offline",
                "last_seen": last_hb.isoformat() if last_hb else None,
                "age_seconds": age_s,
                "role": r["role"],
                "ocean": {"O": float(r["ocean_o"]), "C": float(r["ocean_c"]),
                          "E": float(r["ocean_e"]), "A": float(r["ocean_a"]),
                          "N": float(r["ocean_n"])},
            }
    except Exception as exc:
        agents_info = {"error": str(exc)}
    return {"agents": agents_info, "timestamp": _dt.datetime.utcnow().isoformat()}


# ── Main ─────────────────────────────────────────────────────────────────────
# Note: bind 0.0.0.0 is required — chat_server also serves the SEAL Console webUI
# accessed by William/Henry from LAN (192.168.68.x). Sensitive endpoints
# (/api/chat/messages/agent) enforce layer-7 client.host == 127.0.0.1 checks.
if __name__ == "__main__":
    uvicorn.run(
        "chat_server:app",
        host="0.0.0.0",
        port=8765,
        log_level="warning",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
