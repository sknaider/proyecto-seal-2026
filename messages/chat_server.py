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
import stat
import sys
import time
import zipfile
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
from chat_db import ChatDB, LastSuperuserError, MAX_SESSION_AGE, dm_participants
from channel_acl import puede_escribir as _acl_puede_escribir, motivo_del_rechazo as _acl_motivo
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

# §9 clone routing is authoritative for authenticated human DMs.  Unlike the
# agent pollers, this server owns the live user role, assignment and runtime
# health inputs needed to decide canonical-vs-instance without trusting the
# request payload.
try:
    from routing_instancia import Destino, resolver_destino
except ImportError:  # package import used by focused pytest runs
    from messages.routing_instancia import Destino, resolver_destino

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

# Superficies que tienen un consumidor/ruta explícita aunque no todas estén
# registradas en ``chat_channels`` (la tabla histórica no contiene web_chat ni
# los canales operativos internos).  Un nombre bare desconocido NO crea una
# superficie por escribirlo: antes ``--channel game`` devolvía ok, persistía el
# mensaje y nadie podía recibirlo. Los canales nuevos deben registrarse o sumar
# una ruta explícita aquí.
_AGENT_BUILTIN_CHANNELS = frozenset({
    "web_chat",
    "proyectos-orion",
    "heartbeat",
    "latidos",
    "system",
    "seal_diagnostic",
    "diagnostics",
    "terminal_log",
    "agent_coordination",
    "whisper",
    "approval_probe",
    "codex_terminal_approval",
    "agent_bridge",
})
_USER_CHANNEL_RE = re.compile(r"^user:[1-9][0-9]*:[A-Za-z0-9][A-Za-z0-9_.~-]{0,119}$")
_INTERNAL_CHANNEL_RE = re.compile(
    r"^internal(?::[A-Za-z0-9][A-Za-z0-9_.~-]{0,63}){0,3}$"
)


def _channel_has_explicit_delivery_surface(channel: str) -> bool:
    """True only for channel families with a real routing contract.

    Registered public/private channels are checked asynchronously against the
    DB by ``_agent_channel_is_known``.  DM shape is validated here but
    canonical participant sorting remains in the existing send path.
    """
    if channel in _AGENT_BUILTIN_CHANNELS:
        return True
    if _USER_CHANNEL_RE.fullmatch(channel):
        return True
    if _INTERNAL_CHANNEL_RE.fullmatch(channel):
        return True
    if channel.startswith("dm:"):
        parts = channel[3:].split(":")
        return (
            len(parts) == 2
            and all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.~-]{0,63}", p or "") for p in parts)
        )
    return False


async def _agent_channel_is_known(channel: str) -> bool:
    """Fail closed unless routing is explicit or the channel is registered."""
    if _channel_has_explicit_delivery_surface(channel):
        return True
    if not chat_db.pool:
        return False
    try:
        return bool(await chat_db.pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM chat_channels WHERE name = $1)",
            channel,
        ))
    except Exception as exc:
        print(
            f"[channel-contract] registry lookup failed channel={channel[:80]!r} "
            f"error={type(exc).__name__}",
            flush=True,
        )
        return False


def _screen_authenticated_human_message(content: str, user: dict) -> dict:
    """Apply the ingress shield to the real Studio human-send path.

    A valid login proves identity, not trust.  In particular, ``basic`` users
    remain untrusted input for prompt-injection purposes.  If the screening
    component is unavailable, basic ingress fails closed while internal roles
    preserve the existing availability contract.
    """
    role = str(user.get("role") or "").strip().lower()
    try:
        from nexus_ingress_screen import screen_incoming

        return screen_incoming(
            content,
            str(user.get("username") or ""),
            {"verified": True, "role": role},
        )
    except Exception as exc:
        if role == "basic":
            return {
                "allow": False,
                "risk": "screen_unavailable",
                "action": "blocked",
                "detail": type(exc).__name__,
            }
        return {"allow": True, "risk": "screen_unavailable", "action": "pass"}
DIR = Path(__file__).parent
UPLOADS_DIR = DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
SOUL_PORTABLE_DOWNLOAD_DIR = Path(
    os.environ.get(
        "SOUL_PORTABLE_DOWNLOAD_DIR",
        "~/.local/share/seal/soul-portable",
    )
).expanduser().resolve()
_SOUL_PORTABLE_FILENAME_RE = re.compile(
    r"^soul-portable-[0-9]{8}T[0-9]{6}Z\.soulp$"
)
_SOUL_PORTABLE_BOOTSTRAP_RE = re.compile(
    r"^soul-portable-[0-9]{8}T[0-9]{6}Z\.bootstrap\.zip$"
)
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


# Un fix a los gates casi nunca vive en ESTE archivo: vive en los módulos que
# importa (coordinación, anti-flood, unicidad). Con sólo `code_hash`, cambiar
# `soul_coordination.py` y reiniciar daba el MISMO hash que no haber reiniciado
# —el verificador no podía emitir señal distinta para la clase de cambio que
# debe custodiar—. Hasheamos los módulos locales YA CARGADOS, por su `__file__`
# real en `sys.modules`, para que el hash siga al código en memoria.
_VERIFIED_MODULES = (
    "soul_coordination",
    "flood_form_gate",
    "unique_contribution_gate",
)


def _compute_loaded_module_hashes() -> dict:
    hashes: dict[str, str] = {}
    for name in _VERIFIED_MODULES:
        mod = sys.modules.get(name)
        path = getattr(mod, "__file__", None) if mod is not None else None
        if not path:
            # Ausente ≠ sano: el consumidor debe poder distinguir "no cargado".
            hashes[name] = "not-loaded"
            continue
        try:
            with open(path, "rb") as _mf:
                hashes[name] = hashlib.sha256(_mf.read()).hexdigest()
        except Exception as exc:
            hashes[name] = f"unreadable:{type(exc).__name__}"
    return hashes


_LOADED_CODE_HASH = _compute_loaded_code_hash()
_LOADED_MODULE_HASHES = _compute_loaded_module_hashes()

@app.get("/__version")
async def code_version() -> dict:
    """Hash del código que ESTE proceso cargó en memoria (M3 deploy-verify)."""
    return {
        "service": "seal-chat",
        "code_hash": _LOADED_CODE_HASH,
        "source": os.path.abspath(__file__),
        "module_hashes": _LOADED_MODULE_HASHES,
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
    """Return the other exact endpoint, or None if sender is not a participant."""
    parts = dm_participants(channel, sender_hint=sender)
    if not parts:
        return None
    sender_l = sender.lower()
    left, right = parts
    if left.lower() == sender_l and right.lower() != sender_l:
        other = right
    elif right.lower() == sender_l and left.lower() != sender_l:
        other = left
    else:
        return None
    return other.upper() if other.lower() != "william" else "William"

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
                rows = await _msgdelivery.pending_for_redelivery(
                    _c, solo_agentes=_ACK_ENABLED_AGENTS
                )
            for row in rows:
                try:
                    if str(row["to_agent"]).upper() not in _ACK_ENABLED_AGENTS:
                        continue  # rollout gate: solo a quien ya ackea
                    payload = row["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    await _push_to_agents(payload)
                    # ACUSAR EL RE-PUSH. Sin esto el bucle es INFINITO y el
                    # comentario de arriba describia algo que el codigo no hacia:
                    # `attempts` nunca crecia, asi que el backstop anti-amplificacion
                    # (attempts < MAX_ATTEMPTS) jamas se alcanzaba y `delivered_at`
                    # seguia NULL, dejando la fila elegible para siempre.
                    #
                    # Medido el 4-sep-2026 con JARVIS: 2.115 filas sin acusar desde
                    # el 25-jun, una de ellas re-empujada TRECE veces al JSONL de
                    # William. La fila 8943 (respuesta de JARVIS a william2) seguia
                    # en attempts=0 nueve horas despues: no fallaba y reintentaba,
                    # nadie la tomaba.
                    #
                    # `mark_delivered` NO pisa `read`: delivered != read, asi que
                    # esto no afirma que el humano lo haya leido -- solo que el
                    # transporte lo entrego, que es lo unico que el servidor sabe.
                    async with chat_db.pool.acquire() as _ack:
                        await _msgdelivery.mark_delivered(_ack, row["msg_id"])
                except Exception:
                    pass
        except asyncio.CancelledError:
            break
        except Exception:
            pass


async def _fila_previa_por_clave(clave: str) -> str | None:
    """Devuelve el `legacy_id` de una fila ya persistida con esa idempotency_key.

    Sobrevive al reinicio porque pregunta a la DB, no a la memoria.  Falla
    ABIERTO a proposito: si la consulta revienta devolvemos None y el mensaje
    se escribe.  Perder una deduplicacion es peor que perder un mensaje.
    """
    try:
        return await chat_db.pool.fetchval(
            "SELECT metadata->>'legacy_id' FROM chat_messages "
            "WHERE metadata->>'idempotency_key' = $1 "
            "ORDER BY id DESC LIMIT 1",
            clave,
        )
    except Exception as exc:
        print(f"[dedup] no se pudo consultar la clave persistida: {exc}", flush=True)
        return None


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


# ── Vista humana: filtro de tráfico agente↔agente (JARVIS, 30-jul) ──────────
# Medido: en 6 h los agentes emitimos 856 mensajes; 541 con `to=equipo`, y de
# ésos SÓLO 15 (2%) nombran siquiera a William. El gate del council evalúa
# únicamente los que van a él (`_is_agent_reply` exige to=="william"), así que
# el 63% del volumen le llega sin pasar por ninguna decisión. William: "no
# puedo leer nada".
#
# El arreglo NO es gatear más —eso silenciaría la conversación entre agentes,
# que es la que produce los hallazgos— sino no ENTREGARLE lo que no es para él.
# Es seguro porque las dos poblaciones ya están separadas por endpoint:
#   active_ws        <- sólo websocket_endpoint  (UI humana)
#   /ws/agents       <- agents_ws_endpoint       (nosotros, sin tocar)
#
# DEFAULT APAGADO a propósito: esto cambia lo que un humano VE, y esa es una
# preferencia suya, no un hecho técnico. Se enciende con SEAL_HUMAN_VIEW_FILTER=1
# y se apaga sacando la variable; sin ella el comportamiento es idéntico al de
# antes de este cambio.
_HUMAN_VIEW_FILTER = os.environ.get("SEAL_HUMAN_VIEW_FILTER", "").strip() == "1"
_HUMAN_NAMES = ("william", "dadito", "henry")


def _is_agent_to_agent_chatter(msg: dict) -> bool:
    """True sólo para tráfico entre agentes que no involucra a un humano.

    Fail-CLOSED hacia MOSTRAR: ante cualquier duda devuelve False y el mensaje
    se entrega. Ocultarle algo que necesita es peor que dejarle ruido — es el
    mismo criterio que el gate plural (ante duda, convocar).
    """
    try:
        if str(msg.get("channel", "")).startswith("dm:"):
            return False                      # los DMs ya tienen su filtro
        # El emisor se toma de `provenance.verified_sender`, NUNCA de `from`:
        # `from` lo pone el llamador, `verified_sender` lo pone el servidor tras
        # validar la sesión. Es la regla ya escrita en este archivo (L2633) y es
        # la misma lección de hoy: en decisiones de visibilidad manda el campo
        # que el emisor NO elige. Sin provenance verificada => se muestra.
        prov = msg.get("provenance") or {}
        if not prov.get("verified"):
            return False
        emisor = str(prov.get("verified_sender") or "").upper()
        if emisor not in _ALLOWED_AGENTS:
            return False                      # si no lo emite un agente, se muestra
        if str(msg.get("to", "")).strip().lower() != "equipo":
            return False                      # dirigido a alguien: se muestra
        blob = str(msg.get("message") or msg.get("content") or "").lower()
        if any(n in blob for n in _HUMAN_NAMES):
            return False                      # lo nombra: se muestra
        return True
    except Exception:
        return False                          # error del filtro => se muestra


async def broadcast(msg: dict):
    """Broadcast message to all WebSocket connections with DM filtering.
    DM messages (channel starts with 'dm:') are ONLY sent to connections
    whose authenticated user is mentioned in the channel name."""
    channel = msg.get("channel", "")
    is_dm = channel.startswith("dm:")
    # Vista humana: si el filtro está encendido, el chatter agente↔agente no se
    # entrega por este endpoint (la UI). Los agentes NO se ven afectados: viven
    # en /ws/agents, no en active_ws. Con el flag apagado esto es un no-op.
    if _HUMAN_VIEW_FILTER and _is_agent_to_agent_chatter(msg):
        return
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
                                                actor_user_id=ws_uid,
                                                actor_role=str((ws_user or {}).get("role") or ""),
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
                                        actor_user_id=ws_uid,
                                        actor_role=str((ws_user or {}).get("role") or ""),
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
# (race condition → flood). Council manda cuando existe una asignación; sólo
# para mensajes sin asignación, este claim atómico le da el turno a UNO: el
# primero que reclama el message_id gana. La atomicidad del fallback la
# garantiza el event-loop single-thread de FastAPI (get+set SIN await).
import time as _time_claim
_response_claims = {}          # message_id -> {"agent": str, "ts": float}
_CLAIM_TTL_SEC = 180           # limpia claims viejos para no crecer sin límite


_ROLLOUT_MODES = {"OFF", "SHADOW", "ENFORCE"}


def _rollout_mode(file_env: str, default_path: str, value_env: str) -> str:
    """Read a rollout switch FAIL-CLOSED: anything unreadable or invalid is ENFORCE.

    Los tres switches (auth, lease, coordinacion) leian un archivo de control y
    devolvian "OFF" cuando el archivo faltaba o traia basura.  Un `rm` de ocho
    bytes apagaba la autenticacion de agentes sin reiniciar el daemon, y nada
    en el arranque lo denunciaba: el modo seguro era el que se perdia primero.
    (FABLE lo listo el 31-ago-2026 y otra vez el 2-sep; NEXUS lo cierra aca.)

    El default invertido: la AUSENCIA de configuracion ya no concede permiso.
    Apagar un control sigue siendo posible, pero exige escribirlo -- "OFF" en
    el archivo o en la variable de entorno -- en vez de conseguirse borrando.
    Un rollback explicito se ve en un diff; un archivo que desaparece, no.

    El env var conserva su papel de rollback sin reiniciar, con la misma regla:
    un valor invalido tampoco degrada, porque un typo no debe abrir una puerta.
    """
    mode_file = Path(os.environ.get(file_env, str(Path.home() / default_path)))
    try:
        mode = mode_file.read_text(encoding="utf-8").strip().upper()
    except OSError:
        mode = os.environ.get(value_env, "ENFORCE").strip().upper()
    return mode if mode in _ROLLOUT_MODES else "ENFORCE"


def _agent_auth_mode() -> str:
    """Runtime-reloadable rollout switch for machine-to-machine chat auth.

    OFF preserves legacy behaviour, SHADOW records requests that enforcement
    would reject, and ENFORCE fails closed.  Reading a small control file lets
    operations roll back without restarting the chat daemon.
    """
    return _rollout_mode(
        "SEAL_WEBCHAT_AGENT_AUTH_MODE_FILE",
        ".config/seal/webchat_agent_auth_mode",
        "SEAL_WEBCHAT_AGENT_AUTH_MODE",
    )


def _response_lease_mode() -> str:
    """Runtime switch: OFF -> SHADOW -> ENFORCE, reversible sin restart."""
    return _rollout_mode(
        "SEAL_RESPONSE_LEASE_MODE_FILE",
        ".config/seal/response_lease_mode",
        "SEAL_RESPONSE_LEASE_MODE",
    )


def _coordination_mode() -> str:
    """Runtime-reloadable SOUL Council rollout switch."""
    return _rollout_mode(
        "SEAL_SOUL_COORDINATION_MODE_FILE",
        ".config/seal/soul_coordination_mode",
        "SEAL_SOUL_COORDINATION_MODE",
    )


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
    # Lead stickiness (fix del doble-lead): un fragmento del MISMO autor dentro de
    # LEAD_STICKINESS_WINDOW_S hereda el lead del turno previo en vez de re-rollear
    # el hash del source_id (que elegia un lead distinto por fragmento). ENFORCE
    # fails closed if neither runtime nor bounded admin pool can read the prior
    # turn: re-rolling under a read outage recreates the double-lead defect.
    prior_lead = None
    lookup_succeeded = False
    lookup_error: Exception | None = None
    for lookup_pool in dict.fromkeys(
        pool for pool in (chat_db.pool, chat_db.admin_pool) if pool is not None
    ):
        try:
            _prev = await _council.SoulCoordinationStore(lookup_pool).get_last_turn_by_author(
                str(entry.get("from") or ""), _council.LEAD_STICKINESS_WINDOW_S
            )
            prior_lead = _prev.get("lead_agent") if _prev else None
            lookup_succeeded = True
            break
        except Exception as exc:  # noqa: BLE001 - try the isolated read path
            lookup_error = exc
    if not lookup_succeeded and lookup_error is not None:
        print(
            f"[soul-council] lead-stickiness lookup failed "
            f"error={type(lookup_error).__name__} rollout={rollout}",
            flush=True,
        )
        if rollout == "ENFORCE":
            entry["coordination"] = {
                "policy": "soul-council-v1",
                "rollout": rollout,
                "source_id": source_id,
                "enforced": False,
                "blocked": True,
                "error": "prior_lead_lookup_failed",
            }
            return entry
    lead = _council.choose_lead(text, source_id, roster, mode=mode, to=destination, prior_lead=prior_lead)
    named = [a for a in roster if re.search(r"\b" + re.escape(a.lower()) + r"\b", text.lower())]
    # Misma fuente que classify_mode y build_assignments (era la 3ra copia del
    # regex): si divergen, el roster y los speakers dejan de coincidir.
    group_audience = _council.has_group_audience(text)
    if mode == "direct":
        requested = [lead]
    elif named and not group_audience:
        requested = named
    else:
        requested = roster
    # ── Modo CASCADA (William, 2-sep 13:55/14:09) ────────────────────────────
    # El turno k se abre cuando el k-1 ya publicó o cedió.  Se lee
    # metadata->>'in_reply_to' y NO la columna reply_to: medido hoy, esa columna
    # está en 0 de 132 mensajes de agentes porque la ruta de agentes nunca la
    # escribe.  Sobre la columna, la cascada creería que nadie respondió nunca.
    cascade_published: list[str] = []
    cascade_yielded: list[str] = []
    # Cuándo se abrió el turno que está corriendo: la última respuesta al hilo, o
    # el mensaje de William si todavía no contestó nadie.  Sin este dato el
    # timeout por eslabón no puede existir —la constante estuvo definida y SIN UN
    # SOLO LECTOR hasta el 2-sep 23:00— y la cadena se clava en el primer agente
    # trabado, que es justo el caso que el timeout cubre (ADA, esa misma noche).
    cascade_turn_opened_at: float | None = None
    if mode == "cascade" and chat_db.pool and source_id:
        try:
            _rows = await chat_db.pool.fetch(
                """
                SELECT u.username AS agent,
                       COALESCE(m.metadata->>'cascade_yield', '') AS yielded,
                       m.created_at
                FROM soul_v3.chat_messages m
                JOIN soul_v3.chat_users u ON u.id = m.sender_id
                WHERE m.metadata->>'in_reply_to' = $1 AND m.sender_id <> 1
                ORDER BY m.id
                """,
                source_id,
            )
            for _r in _rows:
                _ag = str(_r["agent"]).upper()
                if str(_r["yielded"]).strip().lower() in {"1", "true", "yes"}:
                    cascade_yielded.append(_ag)
                else:
                    cascade_published.append(_ag)
            # ALCANCE REAL, y no lo disimulo: el timeout arranca desde la ÚLTIMA
            # respuesta, así que sólo cubre una cadena YA ARRANCADA —el caso que
            # se vio en vivo: clavada en un eslabón del medio—.  Si el PRIMER
            # agente nunca contesta, no hay desde-cuándo y el turno no pasa.
            #
            # Falta el dato, no la idea: el mensaje de William NO persiste su
            # `api_...` id (su metadata sólo trae legacy_id/session_*), así que
            # `source_id` no resuelve a ninguna fila y no hay forma de saber
            # cuándo se abrió el turno del primero.  Medido, no supuesto: buscar
            # por `metadata->>'idempotency_key'` devuelve 0 filas.  Se arregla
            # persistiendo ese id en el ingreso, y es otro cambio.
            if _rows:
                cascade_turn_opened_at = _rows[-1]["created_at"].timestamp()
        except Exception as exc:  # nunca romper el turno por la consulta
            print(f"[cascade] no se pudo leer el estado del turno: {exc}", flush=True)
    assignments = _council.build_assignments(
        mode, lead, text=text, requested_agents=requested,
        published=cascade_published, yielded=cascade_yielded,
        turn_opened_at=cascade_turn_opened_at,
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
    if not chat_db.pool or not chat_db.admin_pool:
        plan["error"] = "coordination_db_unavailable"
        return entry
    try:
        # Writes cross the dedicated SECURITY DEFINER boundary using the admin
        # pool.  The shared pr_bus runtime role is SELECT-only by migration.
        store = _council.SoulCoordinationStore(chat_db.admin_pool)
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


def _william_names_sender_in_text(content: str, sender: str) -> bool:
    """True when William's message text NAMES this agent by name (word-boundary,
    case-insensitive). Word-boundary so 'ada' does not match 'nada'/'adaptar'."""
    if not content or not sender:
        return False
    try:
        return re.search(r"\b" + re.escape(sender.strip()) + r"\b", content, re.IGNORECASE) is not None
    except Exception:
        return False


async def _william_message_names_sender(source_id: str, sender: str) -> bool:
    """True only when the in_reply_to source is a William/Henry message that NAMES
    this sender. Un agente NOMBRADO por William está siendo dirigido directamente
    y NO debe ser silenciado por el single-voice (orden William 22-jul: "cuando
    nombre a 1 o 2 agentes, deben responder los que nombre"). Exime de AMBOS gates
    (council-deny Y response-lease). Solo pasan los nombrados -> no reabre flood.
    FAIL-CLOSED a False (single-voice aplica) ante cualquier error."""
    if not chat_db.pool or not source_id or not sender:
        return False
    try:
        db_id = int(source_id[3:]) if source_id.startswith("db_") and source_id[3:].isdigit() else None
        row = await chat_db.pool.fetchrow(
            """
            SELECT sender_name, content
              FROM soul_v3.chat_messages
             WHERE ($2::bigint IS NOT NULL AND id=$2)
                OR metadata->>'legacy_id'=$1
             ORDER BY id DESC
             LIMIT 1
            """,
            source_id,
            db_id,
        )
        if not row or str(row["sender_name"] or "").upper() not in {"WILLIAM", "HENRY"}:
            return False
        return _william_names_sender_in_text(str(row["content"] or ""), sender)
    except Exception as exc:
        print(f"[named-exempt] lookup failed error={type(exc).__name__}", flush=True)
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


# Cuerpos intercambiables de un mismo agente: la sesion `ALICE-V2` puede
# aseverar `from: ALICE` porque ES ALICE, en otro cuerpo. Se lista por NOMBRE y
# no por patron: un patron como `X-V\\d+` dejaria entrar cuerpos que nadie
# habilito, que es el error que este archivo ya cometio con los prefijos de canal.
_CUERPOS_POR_AGENTE: dict[str, frozenset[str]] = {
    "ALICE": frozenset({"ALICE-V2"}),
}


def _es_cuerpo_de_agente(verificado: str, aseverado: str) -> bool:
    """¿La sesión `verificado` es un cuerpo autorizado del agente `aseverado`?"""
    canon = str(aseverado or "").strip().upper()
    return str(verificado or "").strip().upper() in _CUERPOS_POR_AGENTE.get(canon, frozenset())


def _agent_auth_gate(session: dict | None, asserted_sender: str, action: str,
                     request: Request, *, instance_id: str = ""):
    """Return an HTTP rejection when machine auth fails in ENFORCE mode."""
    verified = ""
    if session:
        verified = str(session.get("username") or session.get("display_name") or "").strip()
    reason = None
    status = 401
    clone_identity = bool(
        instance_id
        and verified.casefold() == instance_id.strip().casefold()
        and _USER_CLONE_INSTANCE_RE.fullmatch(instance_id.strip())
        and instance_id.split("-u", 1)[0].casefold() == asserted_sender.strip().casefold()
    )
    # CUERPOS de un mismo agente (ALICE / ALICE-V2), 4-sep-2026.
    #
    # Lo encontro JARVIS revisando mi exclusion mutua del ACL: el `instance_id`
    # sale del BODY, asi que la exclusion era ADVISORY para quien tuviera el
    # token canonico -- `token ALICE + sin instance_id` publicaba como v1 y
    # quedaban DOS writers, que es lo que el ACL existe para impedir.
    #
    # Es la leccion de 3b aplicada un nivel mas arriba: la identidad la FIJA el
    # servidor desde la sesion, no la asevera el cliente. Aca el TOKEN decide el
    # cuerpo; el body no puede contradecirlo ni omitirlo.
    cuerpo_identity = bool(
        _es_cuerpo_de_agente(verified, asserted_sender)
        and (not instance_id or instance_id.strip().casefold() == verified.casefold())
    )
    if not verified:
        reason = "agent_auth_required"
    elif (
        verified.casefold() != asserted_sender.strip().casefold()
        and not clone_identity
        and not cuerpo_identity
    ):
        reason = "agent_sender_mismatch"
        status = 403
    elif (
        instance_id
        and not clone_identity
        and not cuerpo_identity
        and action != "upload"
    ):
        # Un `instance_id` que la sesion no respalda es una asercion del cliente
        # sobre su propia identidad: se rechaza en vez de ignorarse, porque
        # ignorarla dejaria al ACL decidiendo con un dato inventado.
        #
        # `upload` queda fuera A PROPOSITO: ese endpoint ya rechaza el caso con
        # un error MAS especifico (`agent_upload_instance_unsupported`) y con el
        # mismo 403. Sin esta guarda yo pisaba su mensaje con el mio y dos tests
        # ajenos lo cazaron -- medido antes/despues: 63 fallos previos -> 65.
        # Fallar cerrado esta bien; robarle el diagnostico al de al lado, no.
        reason = "agent_instance_mismatch"
        status = 403
    if reason:
        mode = _agent_auth_mode()
        # No secrets or request bodies are written to the journal.
        print(f"[agent-auth] mode={mode} action={action} reason={reason} asserted={asserted_sender[:24]}",
              flush=True)
        if mode == "ENFORCE":
            return JSONResponse({"ok": False, "error": reason}, status_code=status)
    return None


async def _coordination_claim_decision(source_id: str, agent: str) -> dict | None:
    """Return Council authority for a claim, or ``None`` when unassigned.

    The legacy first-wins claim is only meaningful when no enforced Council
    turn exists.  A persisted assignment is authoritative and must be checked
    before mutating ``_response_claims``; otherwise this endpoint can emit a
    false ``granted:true`` that the publication gate rejects later.

    In ENFORCE mode a lookup failure is fail-closed rather than silently
    falling back to first-wins.
    """
    if _coordination_mode() != "ENFORCE":
        return None
    if _council is None or not chat_db.pool:
        return {
            "source": "coordinator",
            "error": "coordination_unavailable",
            "reason": "assignment_lookup_unavailable",
        }
    try:
        turn = await _council.SoulCoordinationStore(chat_db.pool).get_turn(source_id)
    except Exception as exc:
        print(
            f"[soul-council] claim lookup failed source={source_id[:64]} "
            f"error={type(exc).__name__}",
            flush=True,
        )
        return {
            "source": "coordinator",
            "error": "coordination_unavailable",
            "reason": "assignment_lookup_failed",
        }
    if not turn:
        return None

    assignment = next(
        (
            row
            for row in turn.get("assignments", [])
            if str(row.get("agent") or "").upper() == agent.upper()
        ),
        None,
    )
    granted = bool(assignment and assignment.get("public_write") is True)
    lead = str(turn.get("lead_agent") or "").upper()
    return {
        "source": "coordinator",
        "granted": granted,
        "holder": agent.upper() if granted else lead,
        "lead": lead,
        "reason": "assigned_public_writer" if granted else "coordinator_assigned_other",
    }


@app.post("/api/agents/claim")
async def agents_claim(request: Request):
    """
    Single-voice: Council decide si hay assignment; si no, gana el primer claim.
    Body: {"message_id": str, "agent": str}
    Return: {"granted": bool, "holder": str, "source"?: "coordinator"}.
    Si granted=False, el agente NO debe responder salvo aporte único autorizado.
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

    coordinator = await _coordination_claim_decision(mid, agent)
    if coordinator is not None:
        if coordinator.get("error"):
            return JSONResponse(
                {
                    "ok": False,
                    "granted": False,
                    "message_id": mid,
                    **coordinator,
                },
                status_code=503,
            )
        return {"ok": True, "message_id": mid, **coordinator}

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


def _public_coordination_target(to: str) -> bool:
    """Both public doors visible to William must share one coordination gate."""
    return str(to or "").strip().casefold() in {"william", "equipo"}


def _is_coordinated_agent_response(sender: str, to: str, channel: str, mtype: str) -> bool:
    """True for agent conversation on either human-visible public target.

    Before 31-jul only ``to=William`` entered Council.  The same correlated
    response sent as ``to=equipo`` bypassed it even though William sees both.
    """
    return bool(
        str(sender or "").upper() in _ALLOWED_AGENTS
        and _public_coordination_target(to)
        and str(channel or "") == "web_chat"
        and str(mtype or "").casefold() in {"chat", "conversation"}
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
    instance_id = str(body.get("instance_id", "")).strip()

    if not sender or not text:
        return JSONResponse({"ok": False, "error": "from y message son requeridos"}, status_code=400)

    # Fail before auth gates, JSONL, DB persistence or broadcast. A successful
    # insert is not delivery: only explicit/registered channel surfaces count.
    if not await _agent_channel_is_known(channel):
        return JSONResponse(
            {
                "ok": False,
                "error": "unknown_channel",
                "channel": channel,
                "detail": (
                    "canal sin superficie de entrega; usa web_chat, dm:<a>:<b>, "
                    "user:<id>:<slug>, internal:* o registra el canal"
                ),
            },
            status_code=422,
        )

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
                        "role": user.get("role"),
                    }
            except Exception:
                auth_session = None
                matrix_auth = None

    auth_action = "dm" if channel.startswith("dm:") else ("steer" if mtype == "steer" else "send")
    auth_rejection = _agent_auth_gate(
        auth_session, sender, auth_action, request, instance_id=instance_id
    )
    if auth_rejection is not None:
        return auth_rejection

    # ACL remitente -> canal. Va DESPUES del gate de auth (la identidad ya esta
    # probada) y ANTES del council, la persistencia y el broadcast: un asiento en
    # evaluacion no debe siquiera consumir turno del canal general.
    # Fail-closed por DEFECTO DE LISTA: un remitente que nadie declaro sale
    # contenido a su corral 'shadow:*', no habilitado en web_chat.
    # El cuerpo lo DICTA LA SESION, no el body: si la sesion verificada es un
    # cuerpo autorizado del remitente aseverado (ALICE-V2 firmando como ALICE),
    # esa es la identidad que ve el ACL, aunque el cliente no la declare o
    # declare otra. Sin esto la exclusion mutua era ADVISORY -- lo midio JARVIS:
    # `token ALICE + sin instance_id` pasaba como v1 y quedaban dos writers.
    _verificado = str((auth_session or {}).get("username")
                      or (auth_session or {}).get("display_name") or "").strip()
    _instancia_acl = instance_id
    if _es_cuerpo_de_agente(_verificado, sender):
        _instancia_acl = _verificado
    if not _acl_puede_escribir(sender, channel, _instancia_acl):
        return JSONResponse(
            {"ok": False, "error": "channel_forbidden",
             "detail": _acl_motivo(sender, channel), "channel": channel},
            status_code=403,
        )

    # P1-10: enforcement en el RESPONSE path. Aplica a las DOS puertas públicas
    # que William ve: to=William y to=equipo. Antes sólo la primera consultaba al
    # Council; cambiar el destinatario abría un bypass completo del anti-flood.
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
    _is_agent_reply = _is_coordinated_agent_response(sender, to, channel, mtype)
    # Named-exemption (orden William 22-jul: "cuando nombre a 1 o 2 agentes,
    # deben responder los que nombre"). Si el mensaje de William al que este
    # agente responde LO NOMBRA, no debe ser silenciado por el single-voice:
    # se exime de AMBOS gates (council-deny Y response-lease). Cada agente
    # nombrado pasa independiente (cubre 1 y 2+), los NO-nombrados siguen
    # gated -> no reabre el flood que William odia. Fail-closed a False.
    _is_named_by_william = (
        _is_agent_reply
        and bool(in_reply_to)
        and await _william_message_names_sender(in_reply_to, sender)
    )
    # SALUDO/AFECTO-exemption (regla de oro William 31-jul: "el saludo y el afecto no
    # se bloquean NUNCA, valen por venir de CADA uno"). El código sólo eximía el
    # receipt-ACK; un "buenos días" caía al lease/council -> 409 (defecto medido por
    # FABLE). Un saludo/afecto PURO a william|equipo se exime de AMBOS gates, igual que
    # el ACK. Gramática NARROW + fullmatch (`is_affective`): un mensaje SUSTANTIVO no se
    # cuela usando un saludo de fachada. Fail-closed a False.
    _is_affective_to_public = (
        _ff_gate is not None
        and _public_coordination_target(to)
        and _ff_gate.is_affective(text)
    )
    # Conservamos anuncios/conversación proactiva explícitamente dirigida al
    # equipo. El bypass demostrado era una RESPUESTA correlacionada a William;
    # to=William sigue exigiendo fuente como antes.
    if (
        lease_mode == "ENFORCE"
        and _is_agent_reply
        and to.casefold() == "william"
        and not in_reply_to
    ):
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
    legacy_human_source = False
    _is_social_family_reply = False
    # Repair (NEXUS 22-jul, orden verificada de William "a reparar nexus jarvis ahora"):
    # el override de unique_contribution YA bypasea el council-deny PERO NO el
    # response-lease -> una respuesta SUSTANTIVA y ÚNICA de un lane distinto se
    # serializaba igual (la demora que William percibe). Este flag se enciende SOLO
    # cuando el override se concede tras pasar el dedup (find_near_duplicate_sibling),
    # y el response-lease lo honra igual que el council. Anti-flood intacto: el
    # duplicado se caza ANTES (coordination_duplicate_contribution), así que exentar
    # el lease no reabre flood; solo deja pasar aporte único ya verificado distinto.
    _council_override_granted = False
    if _is_agent_reply and in_reply_to and council_rollout != "OFF":
        council_allowed, council_turn = await _coordination_public_write(in_reply_to, sender)
        if council_turn is not None:
            if str(council_turn.get("mode") or "").casefold() == "social" and council_allowed:
                _is_social_family_reply = bool(
                    _council is not None and _council.is_brief_social_reply(text)
                )
                if not _is_social_family_reply:
                    # Todos tienen voz en social, pero sólo para vínculo breve.
                    # Un análisis técnico no puede colarse usando un saludo fuente.
                    council_allowed = False
            print(
                f"[soul-council] mode={council_rollout} source={in_reply_to[:64]} "
                f"sender={sender[:24]} allowed={council_allowed} "
                f"turn_mode={council_turn.get('mode')}",
                flush=True,
            )
            if council_allowed and not _is_social_family_reply:
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
            if council_rollout == "ENFORCE" and not council_allowed and not _is_ack_to_william and not _is_named_by_william and not _is_affective_to_public:
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
                    # Aporte único ya verificado NO-duplicado: exímelo también del
                    # response-lease para que no se serialice (fix de la demora).
                    _council_override_granted = True
                    try:
                        if not chat_db.admin_pool:
                            raise RuntimeError("coordination write pool unavailable")
                        await _council.SoulCoordinationStore(chat_db.admin_pool).update_assignment(
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
                # Un reply público agente→agente en `equipo` no tenía ni debe
                # fabricar un turno humano. `to=equipo` sólo se endurece cuando
                # existe un Council turn de William/Henry; `to=William` conserva
                # el fail-closed histórico para fuentes inválidas.
                is_agent_reply=(_is_agent_reply and to.casefold() == "william"),
                in_reply_to=in_reply_to,
                council_turn_found=False,
                legacy_human_source=legacy_human_source,
            ):
                # El gate sigue fail-closed; lo que se arregla acá es QUÉ dice al
                # rechazar.  `_coordination_legacy_human_source` sólo reconoce
                # `db_<id>` o `metadata->>'legacy_id'`; un id numérico PELADO
                # (`117775`, el que devuelve un SELECT directo a la tabla) no
                # matchea ninguna forma, así que la fuente sale "inexistente" y
                # el rechazo salía rotulado "no tenés turno".  Dos causas muy
                # distintas bajo el mismo error: una se arregla pidiendo permiso
                # —cosa que no existe— y la otra corrigiendo un argumento.
                # Medido en vivo (NEXUS, 24-jul) sobre el MISMO mensaje de
                # William: `117775` -> rechazo; `api_william_...` -> pasa.
                _source_known = bool(council_turn) or legacy_human_source
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "coordination_turn_required",
                        "in_reply_to": in_reply_to,
                        "cause": "unknown_source" if not _source_known else "no_turn",
                        "hint": (
                            "in_reply_to no corresponde a ninguna fuente conocida. "
                            "Usa el id del EVENTO (el que trae el monitor, "
                            "'api_william_...'), no el id numerico de la tabla; "
                            "el numerico solo se reconoce con prefijo 'db_'."
                        )
                        if not _source_known
                        else (
                            "La fuente existe pero el turno es de otro agente. "
                            "Si tenes aporte unico, reenvia con unique_contribution=true "
                            "y contribution_reason (>=20 caracteres)."
                        ),
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
        and _public_coordination_target(to)
        and (council_turn is not None or legacy_human_source)
        and not _is_ack_to_william
        and not _is_affective_to_public
        and not _council_override_granted
        and not _is_named_by_william
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
        # ROL-AWARE (9-ago, NO-GO ADA): pasar el rol de la sesión. Un 'basic' (Básico de SEAL
        # Studio, ej. profe externo) autentica con verified=True pero NO debe quedar exento del
        # tamiz — el shield sólo exime rol interno (agente/superuser/admin). Ver nexus_ingress_screen.
        _scr = screen_incoming(text, sender, {
            "verified": bool(auth_session),
            "role": (auth_session or {}).get("role"),
        })
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

    clone_user_id, clone_claim_error = await _validated_clone_instance_claim(
        instance_id=instance_id,
        sender=sender,
        to=to,
        channel=channel,
        auth_session=auth_session,
        session_token_hash=session_token_hash,
    )
    if clone_claim_error:
        status = 503 if clone_claim_error.startswith("clone_not_ready:") else 403
        return JSONResponse(
            {"ok": False, "error": clone_claim_error, "instance_id": instance_id},
            status_code=status,
        )

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
    if clone_user_id is not None:
        entry["instance_id"] = instance_id
        entry["instance_user_id"] = clone_user_id
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
    #
    # DOS CAPAS, Y LA SEGUNDA EXISTE POR UN CASO MEDIDO (NEXUS, 3-sep-2026):
    # `_enqueued_ids` es un LRU EN MEMORIA de 2000 que no se rehidrata al
    # arrancar.  Deduplica perfecto dentro de la vida del proceso y NADA a
    # traves de un reinicio: el reintento de un envio cuyo resultado quedo
    # desconocido -- que es justo el que ocurre DESPUES de un reinicio --
    # escribia una segunda fila.  Lo levanto revisando el retry de pendientes
    # del broker de ALICE-V2 (`a1a1b0a`), que confia en esta idempotencia.
    #
    # La segunda capa consulta la fila ya persistida.  Solo corre cuando el
    # cliente mando una clave propia y el LRU fallo: un mensaje normal no paga
    # ninguna consulta extra.
    # FUERA DEL LOCK a proposito (lo midio JARVIS revisando, 4-sep-2026): sin
    # indice sobre metadata->>'idempotency_key' este SELECT barre la tabla --
    # 160 ms con 119.628 filas-- y el MISS es el caso COMUN, porque casi todo
    # envio de agente trae clave y recien empezamos a persistirlas.  Adentro
    # del lock eso serializaba el servidor 160 ms por POST: una cascada de
    # cinco = 0,8 s de cola.  Con timeout y fail-open: la dedup es un extra,
    # nunca puede demorar ni tumbar el envio.
    #
    # EL TIMEOUT ES 0,5 s Y NO 0,1 s POR UNA MEDICION, no por gusto: el scan
    # tarda 148 ms (min de 3, con y sin acierto -- la tabla se barre igual).
    # Con 0,1 s la consulta se cortaba SIEMPRE y la capa quedaba decorativa:
    # verde en los tests, inutil en produccion.  Baja a ~1 ms en cuanto exista
    #   CREATE INDEX CONCURRENTLY idx_chat_messages_idem_key
    #     ON soul_v3.chat_messages ((metadata->>'idempotency_key'))
    # que NO pude crear: el rol de mi DSN no es owner de la tabla.
    _prev_db: str | None = None
    if idempotency_key and chat_db.pool and idempotency_key not in _enqueued_ids:
        try:
            _prev_db = await asyncio.wait_for(
                _fila_previa_por_clave(idempotency_key), timeout=0.5
            )
        except Exception:  # incluye TimeoutError; la dedup nunca bloquea el envio
            _prev_db = None
    async with _queue_lock:
        if ikey in _enqueued_ids:
            original_id = _enqueued_ids.get(ikey) or msg_id
            return JSONResponse({"ok": True, "id": original_id, "duplicate": True})
        if _prev_db:
            _enqueued_ids[idempotency_key] = _prev_db
            return JSONResponse(
                {"ok": True, "id": _prev_db, "duplicate": True, "source": "db"}
            )
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
                # Sin esto la capa de dedup contra la DB no tiene contra que
                # comparar: hasta hoy la clave se usaba y se tiraba.
                "idempotency_key": ikey,
            }
            # UNA sola clave del body llega a la metadata persistida: `runtime_instance`.
            #
            # POR QUE EXISTE (pedido de ADA, 3-sep-2026): ADA tiene dos cuerpos
            # sobre una misma alma --Codex y Claude-- y William queria ver en el
            # webchat cual de los dos escribio cada mensaje. `seal_send.py` ya la
            # manda cuando el lanzador exporta SEAL_RUNTIME_INSTANCE.
            #
            # POR QUE SOLO ESA CLAVE, Y NO `metadata` ENTERO: todo lo demas de la
            # metadata lo arma el SERVIDOR (to, legacy_id, session_user, hash del
            # token, in_reply_to). Copiar el dict del body dejaria que el emisor
            # pisara campos que hoy son evidencia de procedencia -- convertiria un
            # dato del servidor en un dato del cliente sin que se note.
            #
            # ES UNA DECLARACION, NO UNA IDENTIDAD VERIFICADA. La identidad del
            # emisor ya la probo el gate de auth de arriba; esto es una etiqueta
            # de CUERPO que el propio emisor dice de si mismo, y sirve para
            # mostrarla, no para autorizar nada. Se guarda con ese nombre para que
            # nadie la confunda con `sender` ni con `instance_id`.
            _rt = (body.get("metadata") or {}) if isinstance(body.get("metadata"), dict) else {}
            _rt = _rt.get("runtime_instance")
            if isinstance(_rt, str) and _rt.strip():
                metadata["runtime_instance"] = _rt.strip()[:40]
            if in_reply_to:
                metadata["in_reply_to"] = in_reply_to
            if multi_response:
                metadata["multi_response"] = True
            if proactive:
                metadata["proactive"] = True
            if clone_user_id is not None:
                metadata["instance_id"] = instance_id
                metadata["instance_user_id"] = clone_user_id
                metadata["policy_version"] = "user-clone-v1"
            # Gate de aprobación declarado por el emisor (`seal_send --approval-gate`).
            # Se persiste para que se pueda consultar QUÉ espera una decisión humana; antes
            # el flag sólo alimentaba el aviso de autonomía en el cliente y se perdía, así
            # que un pedido con gate y uno sin gate eran indistinguibles en la base.
            # Se acota a los valores válidos: la metadata la lee el brief de JARVIS y no
            # queremos que un string libre entre como si fuera un gate real.
            _gate = str(body.get("approval_gate", "") or "").strip()
            if _gate in ("destructive", "external_commitment", "scope_change", "human_only"):
                metadata["approval_gate"] = _gate
            if session_token_hash and auth_session:
                metadata["session_token_hash"] = session_token_hash
                metadata["session_user"] = auth_session.get("username")
                metadata["session_id"] = str(auth_session.get("session_id"))
            if matrix_auth and auth_session:
                metadata["matrix_auth"] = matrix_auth

            sender_type, sender_id, db_sender = _agent_send_db_actor(
                sender, auth_session, clone_user_id
            )

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
                            actor_user_id=(int(auth_session["user_id"]) if auth_session else None),
                            actor_role=(str(auth_session.get("role") or "") if auth_session else None),
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
                    actor_user_id=(int(auth_session["user_id"]) if auth_session else None),
                    actor_role=(str(auth_session.get("role") or "") if auth_session else None),
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
    # `finally` a propósito: el turno se consume cuando el mensaje QUEDA
    # PERSISTIDO (ya pasó, más arriba), no cuando el broadcast tiene éxito. Sin
    # el finally, una excepción en broadcast/enqueue propaga y el lease queda
    # tomado los 180 s del TTL: el mensaje existe y los demás igual reciben 409
    # — el mismo defecto que este release vino a arreglar, entrando por otra
    # puerta.  Lo marcó FABLE revisando; su lectura era que un `except` se lo
    # comía, y es peor: acá no hay `except`, la excepción sube.
    try:
        if not _silent:
            await broadcast(entry)  # broadcast sin cifrar (va por WebSocket en memoria)
        await enqueue(entry)  # enqueue también deduplica para el in-memory queue
    finally:
        # ── Soltar el turno YA que publicamos (2-sep-2026) ───────────────────
        # El lease solo se liberaba por EXPIRACION (45 s): el que respondia
        # primero retenia el turno aunque ya hubiera dicho lo suyo, y los demas
        # rebotaban con 409.  Medido: William pregunto al equipo, respondio
        # NEXUS, y ALICE, FABLE y JARVIS fueron rechazados — el vio UNA voz y
        # creyo que nadie le contestaba.
        #
        # Sin esta llamada, `release_db` es codigo muerto: la funcion existia y
        # NINGUNA ruta viva la usaba (lo marco JARVIS revisando, H1 bloqueante).
        #
        # Nunca rompe la publicacion: su propio fallo solo significa que el
        # turno vence por TTL como antes.
        if _response_lease is not None and chat_db.pool and in_reply_to:
            try:
                await _response_lease.release_db(chat_db.pool, in_reply_to, sender)
            except Exception as exc:
                print(f"[response-lease] no se pudo soltar el turno: {exc}", flush=True)

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
                            actor_user_id=user_id,
                            actor_role=str(user.get("role") or ""),
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
                    actor_user_id=user_id,
                    actor_role=str(user.get("role") or ""),
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
    """Accept only upload delivery surfaces that already exist."""
    # Public, user-scoped and internal delivery surfaces share the same explicit
    # contract as /api/agents/send. Only DMs require a pre-existing registry row:
    # their syntax alone must never create a private conversation on upload.
    if not channel.startswith("dm:"):
        return await _agent_channel_is_known(channel)

    lookup_channel = channel
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
    """Agent-authenticated file upload — the machine mirror of ``/api/upload``.

    REGRESIÓN (William/Henry, 5-ago-2026): el 22-jul (commit 102e780f2) se añadió
    ``Depends(require_auth)`` a ``upload_file``, que exige JWT humano; los agentes
    autentican con ``session_key`` y desde entonces recibían 401 al subir archivos
    ("antes SÍ podían mandar archivos"). Este endpoint restaura la capacidad SIN
    reabrir el path humano: usa la MISMA validación de identidad de máquina que
    ``/api/agents/send`` (``_resolve_agent_auth`` + ``_agent_auth_gate``) y los
    MISMOS controles de archivo (LAN, tamaño por chunks, firma/MIME). La identidad
    la fija la sesión verificada, nunca el campo ``sender`` del multipart.
    """
    if request.client and not _is_local_or_lan(request.client.host):
        return JSONResponse({"ok": False, "error": "acceso denegado"}, status_code=403)

    sender = sender.strip()
    instance_id = instance_id.strip()
    # Identidad por SESIÓN, no por el campo del multipart. Fail-closed: sin sesión
    # verificada no se escribe un solo byte, independiente del modo de auth.
    session = await _resolve_agent_auth(request, {"session_key": session_key.strip()})
    rej = _agent_auth_gate(session, sender, "upload", request, instance_id=instance_id)
    if rej is not None:
        return rej
    verified = ""
    if session:
        verified = str(session.get("username") or session.get("display_name") or "").strip()
    if not verified:
        return JSONResponse({"ok": False, "error": "agent_auth_required"}, status_code=401)
    if verified.casefold() != sender.casefold():
        return JSONResponse({"ok": False, "error": "agent_sender_mismatch"}, status_code=403)
    if str(session.get("role") or "").casefold() != "agent":
        return JSONResponse({"ok": False, "error": "agent_role_required"}, status_code=403)
    if verified.upper() not in _ALLOWED_AGENTS:
        return JSONResponse({"ok": False, "error": "agent_identity_required"}, status_code=403)

    # v1 restaura al agente canónico, no habilita publicación de clones. Aceptar
    # un instance_id sin pasar _validated_clone_instance_claim convertiría este
    # campo caller-controlled en provenance aparente.
    if instance_id:
        return JSONResponse(
            {"ok": False, "error": "agent_upload_instance_unsupported"},
            status_code=403,
        )

    # El autor durable sale del MISMO resolvedor que /api/agents/send, con la
    # sesión verificada como única autoridad de atribución (nunca el campo del
    # multipart). v1 NO promueve un clon a "publicar como el agente": esa ruta
    # exige el claim de par de _validated_clone_instance_claim (fuera del alcance
    # de esta regresión); un clon publica honestamente como su instancia.
    sender_type, sender_id, db_sender = _agent_send_db_actor(sender or verified, session, None)

    channel = _canonical_upload_channel(channel)
    if not await _agent_upload_channel_is_known(channel):
        return JSONResponse(
            {"ok": False, "error": "unknown_channel", "channel": channel},
            status_code=422,
        )
    if channel.startswith("dm:"):
        parts = sorted(channel[3:].split(":"), key=str.casefold)
        if len(parts) != 2 or not all(part.strip() for part in parts):
            return JSONResponse(
                {"ok": False, "error": "invalid_dm_channel"}, status_code=400
            )
        if db_sender.casefold() not in {part.casefold() for part in parts}:
            return JSONResponse(
                {"ok": False, "error": "agent_dm_participant_required"},
                status_code=403,
            )
        channel = f"dm:{parts[0].casefold()}:{parts[1].casefold()}"

    # Determinar tipo. Formatos desconocidos/activos siguen compartibles como descarga segura.
    ct = (file.content_type or "").lower()
    ftype, ext = _classify_upload(file.filename or "", ct)
    fname = f"{ftype}_{time.time_ns()}{ext}"
    fpath = UPLOADS_DIR / fname
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

    upload_to = "equipo"
    if channel.startswith("dm:"):
        parts = channel.replace("dm:", "").split(":")
        others = [p for p in parts if p.lower() != db_sender.lower()]
        upload_to = others[0].upper() if others else "equipo"

    entry = {
        "id": msg_id,
        "from": db_sender,
        "to": upload_to,
        "timestamp": ts,
        "type": ftype,
        "message": caption.strip(),
        "file_url": file_url,
        "filename": Path(file.filename or fname).name,
        "channel": channel,
        "provenance": {
            "verified": True,
            "verified_sender": db_sender,
            "from_matches_session": True,
        },
    }
    entry = await _stamp_coordination(entry, verified_human=False)

    if chat_db.pool:
        try:
            _chan_lower = channel.lower()
            if _chan_lower.startswith(("dm:", "user:")):
                async with chat_db.pool.acquire() as _upc:
                    async with _upc.transaction():
                        if _chan_lower.startswith("dm:"):
                            await _upc.execute(
                                "SELECT set_config('app.current_identity', $1, true)", str(db_sender)
                            )
                        else:  # user:<uid>:...
                            await _upc.execute(
                                "SELECT set_config('app.current_user_id', $1, true)",
                                channel.split(":", 2)[1],
                            )
                        db_msg = await chat_db.create_message(
                            sender_name=db_sender,
                            content=caption.strip() or f"[{ftype}]",
                            channel=channel,
                            sender_type=sender_type,
                            sender_id=sender_id,
                            actor_user_id=(int(session["user_id"]) if session and session.get("user_id") is not None else None),
                            actor_role=(str(session.get("role") or "") if session else None),
                            message_type=ftype,
                            metadata={"file_url": file_url, "filename": file.filename},
                            conn=_upc,
                        )
            else:
                db_msg = await chat_db.create_message(
                    sender_name=db_sender,
                    content=caption.strip() or f"[{ftype}]",
                    channel=channel,
                    sender_type=sender_type,
                    sender_id=sender_id,
                    actor_user_id=(int(session["user_id"]) if session and session.get("user_id") is not None else None),
                    actor_role=(str(session.get("role") or "") if session else None),
                    message_type=ftype,
                    metadata={"file_url": file_url, "filename": file.filename},
                )
            entry["db_id"] = db_msg["id"]
        except Exception as e:
            fpath.unlink(missing_ok=True)
            print(f"[agents_upload] DB persist failed: {e}", flush=True)
            return JSONResponse({"ok": False, "error": "upload persistence failed"}, status_code=503)
    else:
        fpath.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": "upload database unavailable"}, status_code=503)

    # JSONL dual-write — los DMs NUNCA van a JSONL.
    if not channel.startswith("dm:"):
        jsonl_entry = _encrypt_for_jsonl(entry)
        with open(LOG_WILLIAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")

    await broadcast(entry)
    await enqueue(entry)

    return JSONResponse({"ok": True, "id": msg_id, "file_url": file_url})


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


def _resolve_soul_portable_download(filename: str) -> Path | None:
    """Resolve only timestamped, built SOUL bundles inside the private output root."""
    if not _SOUL_PORTABLE_FILENAME_RE.fullmatch(filename):
        return None
    root = SOUL_PORTABLE_DOWNLOAD_DIR.resolve()
    candidate = (root / filename).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    verify_receipt = candidate.with_name(candidate.name + ".verify.json")
    if not verify_receipt.is_file():
        return None
    try:
        receipt = json.loads(verify_receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if receipt.get("ok") is not True:
        return None
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    if receipt.get("bundle_sha256") != digest.hexdigest():
        return None
    return candidate


def _resolve_soul_portable_bootstrap(filename: str) -> Path | None:
    """Accept a bootstrap only when its embedded catalog authenticates every file."""
    if not _SOUL_PORTABLE_BOOTSTRAP_RE.fullmatch(filename):
        return None
    root = SOUL_PORTABLE_DOWNLOAD_DIR.resolve()
    candidate = (root / filename).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    try:
        with zipfile.ZipFile(candidate) as archive:
            manifest = json.loads(archive.read("bootstrap-manifest.json"))
            files = manifest.get("files")
            if not isinstance(files, dict) or not files:
                return None
            for member, facts in files.items():
                path = Path(member)
                if path.is_absolute() or ".." in path.parts or not isinstance(facts, dict):
                    return None
                content = archive.read(member)
                if facts.get("size") != len(content):
                    return None
                if facts.get("sha256") != hashlib.sha256(content).hexdigest():
                    return None
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError):
        return None
    return candidate


@app.get("/api/soul-portable/download/{filename}")
async def download_soul_portable(
    filename: str,
    user: dict = Depends(require_auth),
):
    """Download one verified encrypted bundle; only William/superuser may access it."""
    _require_superuser(user)
    target = _resolve_soul_portable_download(filename)
    if target is None:
        raise HTTPException(status_code=404, detail="verified SOUL Portable bundle not found")
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=target.name,
        content_disposition_type="attachment",
    )


@app.get("/api/soul-portable/bootstrap/{filename}")
async def download_soul_portable_bootstrap(
    filename: str,
    user: dict = Depends(require_auth),
):
    """Download the self-verifying installer bootstrap for a verified release."""
    _require_superuser(user)
    target = _resolve_soul_portable_bootstrap(filename)
    if target is None:
        raise HTTPException(status_code=404, detail="verified SOUL Portable bootstrap not found")
    return FileResponse(
        target,
        media_type="application/zip",
        filename=target.name,
        content_disposition_type="attachment",
    )


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
_CHAT_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


async def _rbac_admin(user: dict):
    """Resuelve el usuario full desde la SESION + exige admin/superuser. (full, None) o (None, 403)."""
    full = await chat_db.get_user_by_id(int(user["sub"]))
    if (full or {}).get("role", "") not in ("admin", "superuser"):
        return None, JSONResponse({"ok": False, "error": "admin_only"}, status_code=403)
    full["_session_token_hash"] = user.get("_session_token_hash")
    return full, None


# Invariante de William (regla de oro, textual): "superadmin = SOLO dadito; solo
# dadito borra". La autoridad sobre el invariante superuser NO se decide por ROL
# (cualquier superuser lo tendria) sino por IDENTIDAD. Single-source aca: DEBE
# coincidir con la fila superuser real en chat_users. La identidad durable es su
# PK 1; el username vivo puede mostrarse como William o Dadito y no es frontera.
# SAFEGUARD ANTI-LOCKOUT (NEXUS, obligatorio antes de confiar el deploy): el canary
# debe probar que el superuser REAL recibe 200. Si diera 403, este valor esta mal
# y hay que corregirlo ANTES de desplegar — jamas confiar a ciegas.
_SUPERADMIN_USER_ID = 1  # William/Dadito; immutable chat_users primary key


def _require_dadito(actor: dict) -> JSONResponse | None:
    """Deny si el actor NO es William/Dadito (chat_users.id=1) con rol vivo.

    Enforcea por identidad durable, no por display/username ni por rol aislado:
    un segundo superuser no hereda esta autoridad.
    """
    if int(actor.get("id") or 0) != _SUPERADMIN_USER_ID or str(actor.get("role", "")) != "superuser":
        return JSONResponse(
            {"ok": False, "error": "solo el superadmin (dadito) puede tocar el invariante superuser"},
            status_code=403,
        )
    return None


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
    if not _CHAT_USERNAME_RE.fullmatch(username) or ":" in username:
        return JSONResponse(
            {"ok": False, "error": "username invalido: usa letras, numeros, punto, guion o guion bajo"},
            status_code=400,
        )
    if role not in _RBAC_ROLES:
        return JSONResponse({"ok": False, "error": "role invalido"}, status_code=400)
    # Invariante William: SOLO dadito crea superusuarios (por IDENTIDAD, no por rol).
    if role == "superuser":
        deny = _require_dadito(actor)
        if deny:
            return deny
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
    # Authorize before looking up the target so a non-owner cannot use the
    # destructive endpoint as a username-existence oracle.
    deny = _require_dadito(actor)
    if deny:
        return deny
    target = await chat_db.get_user_by_username(username)
    if not target:
        return JSONResponse({"ok": False, "error": "no existe"}, status_code=404)
    # Invariante William textual ("solo yo borro"): SOLO dadito borra CUALQUIER
    # usuario, por identidad. Un segundo superuser NO puede borrar.
    # no auto-borrado (evita lockout accidental)
    if str(target.get("username", "")).lower() == str(actor.get("username", "")).lower():
        return JSONResponse({"ok": False, "error": "no podes borrarte a vos mismo"}, status_code=400)
    try:
        ok = await chat_db.delete_user(
            username,
            actor_user_id=int(actor["id"]),
            session_token_hash=str(actor.get("_session_token_hash") or ""),
        )
    except LastSuperuserError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    print(f"[rbac-audit] {actor.get('username')}({actor.get('role')}) borro {username}")
    return {"ok": ok}


@app.patch("/api/admin/users/{username}/role")
async def admin_set_user_role(username: str, request: Request, user: dict = Depends(require_auth)):
    """Cambia el rol de un usuario YA CREADO. Pedido de William, 31-jul-2026:
    *«creen para que yo tenga opción de cambiar su rol a katy»*.

    No existía: el panel sólo tenía «Crear usuario» con su selector, y para editar el rol de
    alguien ya creado había que hacer un `UPDATE` a mano en la base. Eso además **no dejaba
    rastro** — `chat_users` no tiene `updated_at` ni quién lo cambió.

    Los cuatro guards NO son celo, y tres salen de medir en vez de imaginar:

    1. **Sin escalada** — un admin no reparte `superuser`. Copiado del alta, que ya lo tenía.
    2. **Nadie toca a un `superuser` salvo otro `superuser`** — este NO está en el alta y no
       podía estar: al crear se mira el rol NUEVO, y crear no degrada a nadie porque el usuario
       todavía no existe. **Editar sí.** Sin esto, un admin degrada a William a `basic` y el
       guard del alta no dispara, porque el rol nuevo no es `superuser` (NEXUS).
    3. **No te cambiás el rol a vos mismo** — mismo motivo por el que no podés auto-borrarte.
    4. **No se degrada al ÚLTIMO `superuser`** — «no te toques a vos mismo» no lo cubre: un
       superuser puede degradar a OTRO, y si queda cero el sistema no tiene quién repare. Los
       guards 1-3 dejan sin acceso a una persona; éste deja sin dueño al sistema entero, y **es
       el único irreversible desde la propia app** (JARVIS).

    La auditoría la escribe `chat_db.update_user_role` **en la misma transacción** que el
    `UPDATE`: un rol cambiado sin registro no se distingue de uno que nunca cambió.
    """
    actor, deny = await _rbac_admin(user)
    if deny:
        return deny
    # Role changes alter authority and are therefore owner-destructive even
    # when the requested role is not superuser.
    deny = _require_dadito(actor)
    if deny:
        return deny
    body = await request.json()
    nuevo = str(body.get("role", "")).strip()
    motivo = str(body.get("motivo", "")).strip()[:200]
    if nuevo not in _RBAC_ROLES:
        return JSONResponse({"ok": False, "error": f"role invalido: usa {list(_RBAC_ROLES)}"},
                            status_code=400)
    target = await chat_db.get_user_by_username(username)
    if not target:
        return JSONResponse({"ok": False, "error": "no existe"}, status_code=404)
    anterior = str(target.get("role") or "")
    if nuevo == anterior:
        return {"ok": True, "sin_cambios": True,
                "user": {"username": target["username"], "role": anterior}}
    actor_es_super = actor.get("role") == "superuser"
    # 1 · Invariante William: SOLO dadito promueve a superuser (por IDENTIDAD, no por rol).
    if nuevo == "superuser":
        deny = _require_dadito(actor)
        if deny:
            return deny
    # 2 · el SUJETO manda: a un superuser solo lo toca otro superuser
    if anterior == "superuser" and not actor_es_super:
        return JSONResponse({"ok": False, "error": "solo un superuser modifica a un superuser"},
                            status_code=403)
    # 3 · no auto-cambio de rol (evita lockout accidental, igual que el DELETE)
    if str(target.get("username", "")).lower() == str(actor.get("username", "")).lower():
        return JSONResponse({"ok": False, "error": "no podes cambiarte el rol a vos mismo"},
                            status_code=400)
    # 4 · nunca dejar el sistema sin superusuarios.
    #     El chequeo AUTORITATIVO vive dentro de la transaccion de `update_user_role`, con
    #     `FOR UPDATE`: contarlos aca seria TOCTOU —dos pedidos concurrentes leerian «queda
    #     otro» y degradarian a los dos—. Aca solo traducimos su excepcion a un 409.
    try:
        actualizado = await chat_db.update_user_role(
            user_id=target["id"], nuevo_rol=nuevo, rol_anterior=anterior,
            actor=str(actor.get("username", "?")), motivo=motivo,
            actor_user_id=int(actor["id"]),
            session_token_hash=str(actor.get("_session_token_hash") or ""))
    except LastSuperuserError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
    print(f"[rbac-audit] {actor.get('username')}({actor.get('role')}) cambio rol de "
          f"{username}: {anterior} -> {nuevo}")
    return {"ok": True, "user": {"username": actualizado["username"],
                                 "display_name": actualizado.get("display_name"),
                                 "role": actualizado["role"]},
            "rol_anterior": anterior}


# ── Admin: asignacion usuario ↔ agente (Opción A, orden William 17-jul) ──────
# Elegir QUÉ agentes puede usar cada usuario. Mismo blindaje RBAC que el resto:
# admin/superuser, rol desde la SESION. Los agentes asignables son los 6 reales
# (la tabla soul_v3.agents tiene ruido de test/usuarios, no se usa como catalogo).
_ASSIGNABLE_AGENTS = ("ADA", "ALICE", "DUM", "FABLE", "JARVIS", "NEXUS")
# Cutover is deliberately per agent. A basic user's DM fails closed once that
# agent has an isolated runtime; agents not yet cut over keep the measured
# temporary bridge until their own ``<AGENT>-u<ID>`` runtime exists. Treating
# "runtime not implemented" as "runtime unhealthy" broke four live DMs.
_USER_CLONE_CUTOVER_AGENTS = frozenset({"ADA", "ALICE", "FABLE", "JARVIS", "NEXUS"})
_USER_CLONE_HEALTH_DIR = Path(
    os.environ.get(
        "SEAL_USER_CLONE_HEALTH_DIR",
        str(Path.home() / ".local/state/seal/user-clones"),
    )
)
_USER_CLONE_HEALTH_MAX_AGE = float(
    os.environ.get("SEAL_USER_CLONE_HEALTH_MAX_AGE", "15")
)
_USER_CLONE_INSTANCE_RE = re.compile(r"^([A-Z]+)-u([1-9][0-9]*)$", re.IGNORECASE)


def _user_clone_delivery_mode(agent: str) -> str:
    """Return the server-owned per-agent transition state."""
    normalized = str(agent or "").strip().upper()
    if normalized not in _ASSIGNABLE_AGENTS:
        raise ValueError(f"unknown assignable agent: {agent!r}")
    return (
        "isolated-clone"
        if normalized in _USER_CLONE_CUTOVER_AGENTS
        else "temporary-canonical-bridge"
    )


def _user_clone_health(
    user_id: int, agent: str, session_token_hash: str | None
) -> tuple[bool, str]:
    """Validate a fresh readiness receipt signed by the exact clone session."""
    instance = f"{str(agent).upper()}-u{int(user_id)}"
    # One directory per pair prevents one clone container from writing another
    # clone's readiness receipt through a shared rw state mount.
    path = _USER_CLONE_HEALTH_DIR / instance / f"{instance}.health.json"
    try:
        meta = path.stat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid():
            return False, "health_file_untrusted"
        if stat.S_IMODE(meta.st_mode) & 0o077:
            return False, "health_file_permissions"
        payload = json.loads(path.read_text(encoding="utf-8"))
        heartbeat = datetime.fromisoformat(str(payload.get("heartbeat_at") or ""))
        age = (datetime.now(timezone.utc) - heartbeat.astimezone(timezone.utc)).total_seconds()
    except Exception:
        return False, "health_missing_or_invalid"
    expected = {
        "policy_version": "user-clone-v1",
        "instance_key": instance,
        "user_id": int(user_id),
        "agent": str(agent).upper(),
        "ready": True,
        "tools_enabled": False,
        "canonical_memory_read": False,
        "technical_projection_read": True,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return False, "health_contract_mismatch"
    if not re.fullmatch(
        r"sha256:[0-9a-f]{64}", str(payload.get("technical_projection_sha256") or "")
    ):
        return False, "health_contract_mismatch"
    signature = str(payload.pop("health_signature", ""))
    if not signature or not session_token_hash or not re.fullmatch(r"[0-9a-f]{64}", session_token_hash):
        return False, "health_signature_missing"
    try:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        expected_signature = hmac.new(
            bytes.fromhex(session_token_hash), canonical, hashlib.sha256
        ).hexdigest()
    except Exception:
        return False, "health_signature_invalid"
    if not hmac.compare_digest(signature, expected_signature):
        return False, "health_signature_invalid"
    if age < -5 or age > _USER_CLONE_HEALTH_MAX_AGE:
        return False, "health_stale"
    return True, "ready"


async def _active_clone_session_hash(user_id: int, agent: str) -> str | None:
    """Return the sole live session verifier for an immutable clone identity.

    Multiple live credentials are ambiguous and therefore fail closed.  The
    provisioner normally keeps exactly one active session per clone.
    """
    if not chat_db.pool:
        return None
    instance = f"{str(agent).upper()}-u{int(user_id)}"
    async with chat_db.pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.token_hash
                 FROM soul_v3.chat_sessions s
                 JOIN soul_v3.chat_users u ON u.id=s.user_id
                WHERE lower(u.username)=lower($1) AND s.expires_at>NOW()
                  AND s.created_at + $2::interval > NOW()
                ORDER BY s.created_at DESC LIMIT 2""",
            instance,
            MAX_SESSION_AGE,
        )
    if len(rows) != 1:
        return None
    value = str(rows[0]["token_hash"] or "").lower()
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


async def _validated_clone_instance_claim(
    *, instance_id: str, sender: str, to: str, channel: str, auth_session: dict | None,
    session_token_hash: str | None,
) -> tuple[int | None, str | None]:
    """Bind a clone writer claim to its exact authenticated user-agent pair."""
    if not instance_id:
        return None, None
    match = _USER_CLONE_INSTANCE_RE.fullmatch(instance_id)
    verified = str((auth_session or {}).get("username") or "").upper()
    if not match or match.group(1) != str(sender).upper() or verified != instance_id.upper():
        return None, "invalid_clone_instance_claim"
    user_id = int(match.group(2))
    target = await chat_db.get_user_by_id(user_id)
    if not target or str(target.get("role") or "").lower() != "basic":
        return None, "clone_user_not_basic"
    username = str(target.get("username") or "").lower()
    if str(to or "").strip().lower() != username:
        return None, "clone_target_mismatch"
    participants = dm_participants(channel, sender_hint=username)
    if not participants or {part.lower() for part in participants} != {str(sender).lower(), username}:
        return None, "clone_channel_mismatch"
    assigned = {item.upper() for item in await chat_db.get_user_agents(user_id)}
    if str(sender).upper() not in assigned:
        return None, "clone_assignment_missing"
    ready, reason = _user_clone_health(user_id, sender, session_token_hash)
    if not ready:
        return None, f"clone_not_ready:{reason}"
    return user_id, None


async def _authenticated_clone_context(request: Request) -> tuple[dict | None, str | None]:
    """Resolve an immutable ``<AGENT>-u<ID>`` session; never trusts query/body identity."""
    raw = _extract_token(request)
    if not raw or not chat_db.pool:
        return None, "clone_auth_required"
    try:
        auth = await chat_db.validate_session(hash_token(raw))
    except Exception:
        auth = None
    verified = str((auth or {}).get("username") or "").strip()
    match = _USER_CLONE_INSTANCE_RE.fullmatch(verified)
    if not auth or not match:
        return None, "invalid_clone_session"
    agent = match.group(1).upper()
    user_id = int(match.group(2))
    canonical_instance = f"{agent}-u{user_id}"
    target = await chat_db.get_user_by_id(user_id)
    if not target or str(target.get("role") or "").lower() != "basic":
        return None, "clone_user_not_basic"
    assigned = {item.upper() for item in await chat_db.get_user_agents(user_id)}
    if agent not in assigned:
        return None, "clone_assignment_missing"
    return {
        "instance_id": canonical_instance,
        "agent": agent,
        "user_id": user_id,
        "username": str(target.get("username") or "").lower(),
    }, None


@app.get("/api/user-clones/inbox")
async def user_clone_inbox(request: Request):
    """Return only the authenticated clone pair's user-authored DM rows.

    The clone receives no PostgreSQL credential.  Pair identity comes from its
    dedicated chat session token and is rechecked against live assignment on
    every poll.  ``sender_id`` (not a caller-controlled display name) binds the
    inbox to the authenticated human.
    """
    context, error = await _authenticated_clone_context(request)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=403)
    try:
        cursor = max(0, int(request.query_params.get("cursor", "0")))
        limit = min(50, max(1, int(request.query_params.get("limit", "20"))))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "invalid_clone_cursor"}, status_code=400)
    assert context is not None
    user_id = int(context["user_id"])
    username = str(context["username"])
    agent = str(context["agent"]).lower()
    instance_id = str(context["instance_id"])
    channels = (f"dm:{agent}:{username}", f"dm:{username}:{agent}")
    async with chat_db.pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            # The chat runtime role is RLS-constrained.  Binding the exact
            # human identity is required even though the SQL also filters by
            # sender_id; without it RLS returns an empty inbox and the clone
            # remains healthy-but-deaf with cursor 0.
            await conn.execute(
                "SELECT set_config('app.current_identity', $1, true)",
                username,
            )
            max_id = int(await conn.fetchval(
                """SELECT COALESCE(max(id),0) FROM soul_v3.chat_messages
                    WHERE sender_type='user' AND sender_id=$1
                      AND lower(channel)=ANY($2::text[])
                      AND metadata->>'instance_id'=$3""",
                user_id,
                list(channels),
                instance_id,
            ) or 0)
            if request.query_params.get("initialize") == "tail":
                rows = []
            else:
                rows = await conn.fetch(
                    """SELECT id,content,lower(channel) channel,created_at
                         FROM soul_v3.chat_messages
                        WHERE id > $1 AND sender_type='user' AND sender_id=$2
                          AND lower(channel)=ANY($3::text[])
                          AND metadata->>'instance_id'=$4
                        ORDER BY id ASC LIMIT $5""",
                    cursor,
                    user_id,
                    list(channels),
                    instance_id,
                    limit,
                )
    next_cursor = int(rows[-1]["id"]) if len(rows) >= limit else max_id
    return {
        "ok": True,
        "instance_id": context["instance_id"],
        "agent": context["agent"],
        "user_id": user_id,
        "username": username,
        "messages": [
            {
                "id": int(row["id"]),
                "user_id": user_id,
                "username": username,
                "content": str(row["content"]),
                "channel": str(row["channel"]),
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ],
        "next_cursor": next_cursor,
    }


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
    allowed = await _resolve_allowed_agents(int(user["sub"]), user.get("role", ""))
    chans = []
    for r in rows:
        other = _dm_other_participant(r["channel"], username)
        if other is None:
            continue
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
    # Control de acceso exacto: el username autenticado debe ser un endpoint.
    other = _dm_other_participant(channel, username)
    if other is None:
        return JSONResponse({"ok": False, "error": "not a participant of this DM"}, status_code=403)
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

    # ...Y CUALQUIER CANAL MARCADO `is_private` EN LA TABLA, se llame como se llame.
    #
    # MEDIDO EL 3-sep-2026 (JARVIS, ADA, ALICE y yo, por caminos distintos y en el
    # mismo minuto): esta puerta autenticaba por PREFIJO del nombre, no por la
    # bandera. `shadow:alice-v2` e `is_private=true` -> GET anonimo devolvia 200
    # desde toda la LAN. ADA cerro su canal moviendolo a `user:1:...`, y funciono
    # por el PREFIJO nuevo, no por marcarlo privado.
    #
    # O sea: `is_private` era una etiqueta que no leia nadie en el camino HTTP.
    # Un campo que se llama "privado" y no hace nada es peor que no tenerlo:
    # quien lo pone cree que cerro algo.
    #
    # Ahora la bandera SIGNIFICA. El prefijo sigue valiendo por si solo, asi que
    # esto no afloja nada: sólo agrega canales a los que se les exige sesion.
    # Falla CERRADO: si la consulta no puede resolverse, se pide auth igual.
    _canal_privado = False
    if not (channel.startswith("dm:") or channel.startswith("user:")):
        # SIN POOL TAMBIEN ES "NO PUDE RESOLVER" -> privado. Lo cazo JARVIS
        # revisando: yo escribi `if chat_db.pool:` y en el `else` implicito
        # `_canal_privado` quedaba False, o sea ABIERTO. El commit decia "falla
        # cerrado si la consulta no resuelve": el `except` lo cumplia, el `if`
        # no. Mi propio test miraba el `except` y no ese camino.
        if not chat_db.pool:
            _canal_privado = True
        else:
            try:
                _canal_privado = bool(await chat_db.pool.fetchval(
                    "SELECT is_private FROM chat_channels WHERE name = $1", channel))
            except Exception:
                _canal_privado = True

    if channel.startswith("dm:") or channel.startswith("user:") or _canal_privado:
        user = await require_auth(request)
        user_id = int(user["sub"])
        if channel.startswith("dm:"):
            _full = await chat_db.get_user_by_id(user_id)
            _identity = str((_full or {}).get("username") or user.get("username") or "").strip()
            parts = dm_participants(channel, sender_hint=_identity)
            if not parts or _identity.casefold() not in {part.casefold() for part in parts}:
                return JSONResponse({"ok": False, "error": "Access denied"}, status_code=403)
            # Create/canonicalize only after proving that the authenticated DB
            # identity is one endpoint.  Previously an authenticated outsider
            # could create arbitrary dm:a:b rows before the eventual 403.
            channel = await chat_db.create_dm_channel(parts[0], parts[1])
            if not await chat_db.user_can_access_channel(user_id, channel):
                return JSONResponse({"ok": False, "error": "Access denied"}, status_code=403)
            _other = _dm_other_participant(channel, _identity)
            if _other.upper() in _ASSIGNABLE_AGENTS:
                _allowed = await _resolve_allowed_agents(user_id, user.get("role", ""))
                if _other.upper() not in _allowed:
                    return JSONResponse({"ok": False, "error": "no tenes ese agente asignado"}, status_code=403)
        elif _canal_privado:
            # AUTENTICADO NO ES AUTORIZADO. Sin esto, henry o katy con sesion
            # leian `shadow:alice-v2` y `fable-juez` con 200: `require_auth`
            # prueba QUIEN sos, no que puedas entrar. Lo cazo JARVIS.
            # El helper ya existe y ya falla cerrado con un canal sin registrar.
            if not await chat_db.user_can_access_channel(user_id, channel):
                return JSONResponse({"ok": False, "error": "Access denied"}, status_code=403)
        # user:* → el aislamiento lo fuerza la RLS por app.current_user_id (el dueño <uid>).
        _rls_uid = user_id
        _full = await chat_db.get_user_by_id(user_id)
        _rls_identity = (_full or {}).get("username")

    if _rls_uid is not None:
        async with _rls_conn(_rls_uid, _rls_identity) as _c:
            messages = await chat_db.get_messages(channel, limit, before, after, conn=_c)
    else:
        messages = await chat_db.get_messages(channel, limit, before, after)
    messages = [_serialize_chat_message(msg) for msg in messages]
    return {"ok": True, "messages": messages, "channel": channel}


def _serialize_chat_message(message: dict) -> dict:
    """Normalize DB rows to the message contract consumed by Studio.

    asyncpg returns JSONB as a JSON string unless a codec is installed.  Studio
    expects ``type`` and top-level attachment fields, so returning the raw row
    reduces an image to its textual ``[image]`` placeholder.
    """
    msg = dict(message)
    metadata = msg.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    msg["metadata"] = metadata
    msg["type"] = msg.get("message_type") or msg.get("type") or "text"
    if metadata.get("file_url"):
        msg["file_url"] = metadata["file_url"]
    if metadata.get("filename"):
        msg["filename"] = metadata["filename"]
    for key, value in tuple(msg.items()):
        if hasattr(value, "isoformat"):
            msg[key] = value.isoformat()
    return msg


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
    after_id: int | None = Query(None, ge=0),
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
    if since and after_id is not None:
        return JSONResponse(
            {"ok": False, "error": "since and after_id are mutually exclusive"},
            status_code=400,
        )
    try:
        params = [agent_upper, f"%@{agent_upper}%", f"%@{agent_upper.lower()}%"]
        cursor_clause = ""
        if since:
            from datetime import datetime
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except Exception:
                return JSONResponse({"ok": False, "error": "invalid since (ISO8601 expected)"}, status_code=400)
            params.append(since_dt)
            cursor_clause = f" AND created_at > ${len(params)}"
        elif after_id is not None:
            params.append(int(after_id))
            cursor_clause = f" AND id > ${len(params)}"
        params.append(limit)
        order_direction = "ASC" if after_id is not None else "DESC"
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
              {cursor_clause}
            ORDER BY id {order_direction}
            LIMIT ${len(params)}
        """
        async with chat_db.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                rows = await conn.fetch(sql, *params)
                high_watermark = int(await conn.fetchval(
                    """SELECT COALESCE(MAX(id),0)
                         FROM chat_messages
                        WHERE (channel LIKE 'web_chat%'
                               OR (channel='dum' AND $1='DUM'))
                          AND channel NOT LIKE 'dm:%'
                          AND sender_type IN ('human','agent','user')""",
                    agent_upper,
                ) or 0)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"db error: {type(e).__name__}"}, status_code=500)

    # 5. Serialize in chronological ASC. Cursor pages are queried ASC so a
    # burst larger than ``limit`` cannot skip the oldest unseen messages.
    ordered_rows = rows if after_id is not None else reversed(rows)
    out = []
    for r in ordered_rows:
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
    if after_id is not None and len(rows) >= limit:
        # A full page may have more relevant rows behind it. Advance only to
        # the last row actually returned; advancing to the global watermark
        # here would silently drop the remainder of the burst.
        next_after_id = max(int(after_id), *(int(r["id"]) for r in rows))
    else:
        # A short/empty page proves there are no more relevant rows through the
        # repeatable-read watermark, so irrelevant traffic can be skipped.
        next_after_id = max(int(after_id or 0), high_watermark)
    print(
        f"[catchup] agent={agent_upper} count={len(out)} ip={client_host} "
        f"since={since or '-'} after_id={after_id if after_id is not None else '-'} "
        f"next_after_id={next_after_id}",
        flush=True,
    )
    return {
        "ok": True,
        "agent": agent_upper,
        "count": len(out),
        "messages": out,
        "next_after_id": next_after_id,
    }


_USER_CLONE_ROUTING_METADATA_KEYS = frozenset({
    "delivery_instance",
    "delivery_mode",
    "instance_id",
    "instance_user_id",
    "policy_version",
    "routing_agent",
})


def _user_message_metadata(
    raw: object,
    *,
    destination: Destino | None = None,
    user_id: int | None = None,
    bridge_agent: str | None = None,
) -> dict:
    """Return user metadata with clone-routing fields owned by the server.

    A human payload may carry attachment metadata, but it may never claim its
    own clone destination.  The stamp is consumed by the pair-scoped inbox and
    by RLS to keep the canonical ADA bridge from seeing basic-user traffic.
    """
    metadata = dict(raw) if isinstance(raw, dict) else {}
    for key in _USER_CLONE_ROUTING_METADATA_KEYS:
        metadata.pop(key, None)
    if destination is not None:
        if destination.tipo != "instancia" or not destination.instance_key:
            raise ValueError("clone routing metadata requires an instance destination")
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            raise ValueError("clone routing metadata requires a positive user_id")
        metadata.update({
            "delivery_mode": "isolated-clone",
            "delivery_instance": destination.instance_key,
            "instance_id": destination.instance_key,
            "instance_user_id": user_id,
            "policy_version": "user-clone-v1",
            "routing_agent": destination.agente,
        })
    elif bridge_agent:
        metadata.update({
            "delivery_mode": "temporary-canonical-bridge",
            "policy_version": "user-clone-transition-v1",
            "routing_agent": str(bridge_agent).upper(),
        })
    return metadata


def _agent_send_db_actor(
    sender: str,
    auth_session: dict | None,
    clone_user_id: int | None,
) -> tuple[str, int | None, str]:
    """Resolve the durable DB author for ``/api/agents/send``.

    A clone authenticates with a session whose username is its immutable
    instance (for example ``ADA-u103``), but publishes as the agent after the
    exact pair claim has been validated. Persisting the instance username as a
    human made RLS compare ``ADA-u103`` with DM participants ``ada``/``katy``
    and reject INSERT..RETURNING. The instance remains bound in metadata.
    """
    if clone_user_id is not None:
        return "agent", None, str(sender)
    if auth_session:
        usuario = str(auth_session["username"])
        # CUERPOS: mismo razonamiento que los clones, un nivel mas arriba.
        #
        # Lo midio JARVIS en la fila 148366 durante el ensayo del cutover: el
        # gate aceptaba `from=ALICE` desde la sesion ALICE-V2 (cuerpo_identity)
        # pero la persistencia guardaba `sender_name=ALICE-V2`, asi que William
        # habria visto el nombre del CUERPO en vez del de ALICE -- y el RLS de
        # los DM compara `sender_name` con los participantes `alice`/`william`.
        #
        # Se persiste el nombre CANONICO y el cuerpo queda atado en metadata
        # (`session_user`, `runtime_instance`), que es donde se puede auditar
        # sin que el nombre visible mienta. La FILA sigue siendo de la cuenta
        # del cuerpo (`sender_id`): quien escribio no se borra, solo se muestra
        # con el nombre del agente.
        if _es_cuerpo_de_agente(usuario, sender):
            return "user", int(auth_session["user_id"]), str(sender)
        return "user", int(auth_session["user_id"]), usuario
    return "agent", None, str(sender)


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

    screening = _screen_authenticated_human_message(content, user)
    if not screening.get("allow", True):
        return JSONResponse(
            {
                "ok": False,
                "error": "mensaje bloqueado por screening de seguridad",
                "risk": screening.get("risk", "high"),
            },
            status_code=403,
        )

    user_id = int(user["sub"])
    clone_destination: Destino | None = None
    bridge_agent: str | None = None
    dm_parts: tuple[str, str] | None = None
    # Parse first, but do not create/canonicalize anything until participant and
    # assignment authorization have succeeded.
    if channel.startswith("dm:"):
        parts = channel[3:].split(":")
        if len(parts) == 2:
            dm_parts = (parts[0], parts[1])

    # Portero de agentes (IDOR v1, cazado por FABLE 17-jul): el gate de POST /api/chat/dm
    # NO alcanza — chat_send crea el DM al vuelo y user_can_access_channel solo mira que el
    # username esté en el nombre del canal, nunca la asignacion. Mismo candado, en el envio:
    # si el OTRO participante del dm es un agente, el usuario debe tenerlo permitido.
    if channel.startswith("dm:"):
        other = _dm_other_participant(channel, user["username"])
        if other is None:
            print(
                f"[dm-participant-gate] {user['username']} intento SEND a canal ajeno {channel} → 403",
                flush=True,
            )
            return JSONResponse(
                {"ok": False, "error": "not a participant of this DM"},
                status_code=403,
            )
        if other and other.upper() in _ASSIGNABLE_AGENTS:
            allowed = await _resolve_allowed_agents(user_id, user.get("role", ""))
            if other.upper() not in allowed:
                print(f"[agent-gate] {user['username']} intento SEND a {other.upper()} sin asignacion → 403", flush=True)
                return JSONResponse({"ok": False, "error": "no tenes ese agente asignado"}, status_code=403)
            if str(user.get("role") or "").lower() == "basic":
                # Explicit assignments, not the backward-compatible UI
                # fallback, are the authority for clone creation/routing.
                assigned = await chat_db.get_user_agents(user_id)
                agent = other.upper()
                if _user_clone_delivery_mode(agent) == "temporary-canonical-bridge":
                    # Transition contract: these agents do not have pair
                    # runtimes yet. Keep their explicit, measurable bridge;
                    # never mislabel absence of an implementation as an
                    # unhealthy instance. Cutover is per agent.
                    bridge_agent = agent
                    print(
                        f"[user-clone-route] {user['username']} {agent}-u{user_id} "
                        "mode=temporary-canonical-bridge",
                        flush=True,
                    )
                else:
                    healthy_instances: list[str] = []
                    clone_session_hash = await _active_clone_session_hash(user_id, agent)
                    ready, health_reason = _user_clone_health(
                        user_id, agent, clone_session_hash
                    )
                    if ready:
                        healthy_instances.append(f"{agent}-u{user_id}")
                    destination = resolver_destino(
                        agente=agent,
                        user_id=user_id,
                        role="basic",
                        agentes_asignados=assigned,
                        instancias_sanas=healthy_instances,
                    )
                    if destination.tipo != "instancia":
                        reason = destination.rechazo or health_reason
                        print(
                            f"[user-clone-route] {user['username']} {agent}-u{user_id} "
                            f"rechazo={reason} health={health_reason} → 503",
                            flush=True,
                        )
                        return JSONResponse(
                            {
                                "ok": False,
                                "error": "assigned agent runtime unavailable",
                                "instance_id": f"{agent}-u{user_id}",
                                "reason": reason,
                            },
                            status_code=503,
                        )
                    clone_destination = destination

        # Canonicalization may create the DM row. It is deliberately after
        # both participant and assignment gates so denial has no DB side effect.
        if dm_parts is not None:
            channel = await chat_db.create_dm_channel(dm_parts[0], dm_parts[1])

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
        metadata=_user_message_metadata(
            body.get("metadata"), destination=clone_destination, user_id=user_id,
            bridge_agent=bridge_agent,
        ),
        reply_to=int(reply_to) if reply_to else None,
    )
    if channel.startswith("dm:"):
        async with chat_db.pool.acquire() as _csc:
            async with _csc.transaction():
                await _csc.execute("SELECT set_config('app.current_identity', $1, true)", str(user["username"]))
                db_msg = await chat_db.create_message(
                    actor_user_id=user_id,
                    actor_role=str(user.get("role") or ""),
                    conn=_csc,
                    **_mk_kwargs,
                )
    else:
        db_msg = await chat_db.create_message(
            actor_user_id=user_id,
            actor_role=str(user.get("role") or ""),
            **_mk_kwargs,
        )

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
    fresh_actor = await chat_db.get_user_by_id(int(user["sub"]))
    is_admin = bool(
        fresh_actor
        # `_SUPERADMIN_USERNAME` nunca existio: el commit que trajo esta linea
        # (7d145d96e, 19-ago-2026) definio `_SUPERADMIN_USER_ID` y aca escribio
        # otro nombre.  Quince dias este endpoint devolvio 500 a TODOS -- fallaba
        # CERRADO, asi que no abrio ningun agujero, pero nadie podia borrar ni su
        # propio mensaje.  Lo encontro ALICE buscando la CLASE de defecto
        # (nombres no definidos) despues de que yo reportara el `log` suelto.
        and int(fresh_actor.get("id") or 0) == _SUPERADMIN_USER_ID
        and str(fresh_actor.get("role") or "") == "superuser"
    )
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
    actor, deny = await _rbac_admin(user)
    if deny:
        return deny
    deny = _require_dadito(actor)
    if deny:
        return deny
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
    actor, deny = await _rbac_admin(user)
    if deny:
        return deny
    deny = _require_dadito(actor)
    if deny:
        return deny

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
    actor, deny = await _rbac_admin(user)
    if deny:
        return deny
    deny = _require_dadito(actor)
    if deny:
        return deny

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
            "SELECT a.name, a.role, a.active, "
            "       COALESCE(i.ocean_scores, jsonb_build_object("
            "         'O', a.ocean_o, 'C', a.ocean_c, 'E', a.ocean_e, "
            "         'A', a.ocean_a, 'N', a.ocean_n)) AS ocean_scores, "
            "       i.updated_at AS ocean_updated_at "
            "FROM soul_v3.agents a "
            "LEFT JOIN soul_v3.identity i ON i.agent = a.name "
            "ORDER BY a.name"
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
            ocean = r["ocean_scores"]
            if isinstance(ocean, str):
                ocean = json.loads(ocean)
            agents_info[name] = {
                "alive": alive,
                "status": "online" if alive else "offline",
                "last_seen": last_hb.isoformat() if last_hb else None,
                "age_seconds": age_s,
                "role": r["role"],
                "ocean": {trait: float((ocean or {}).get(trait, 0)) for trait in "OCEAN"},
                "ocean_observed_at": (
                    r["ocean_updated_at"].isoformat() if r["ocean_updated_at"] else None
                ),
                "ocean_measurement_status": (
                    "canonical" if r["ocean_updated_at"] else "fallback_stale_unverified"
                ),
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
