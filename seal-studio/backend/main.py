#!/usr/bin/env python3
"""
SEAL Studio — Backend API
=========================
FastAPI server bridging the frontend to SEAL Runtime, SOUL, and team services.
Designed by JARVIS, built by Team SEAL.

Run: uvicorn main:app --host 0.0.0.0 --port 8800 --reload
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import pty
import select
import struct
import fcntl
import termios
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from chat_contract import normalize_chat_message
from studio_db import DB_URL

# ── Config ──
PROJECT_ROOT = Path(os.environ.get("SEAL_PROJECT_ROOT", "/home/dadito/IA/proyecto-seal"))
MESSAGES_DIR = PROJECT_ROOT / "messages"
SCRATCHPAD_DIR = PROJECT_ROOT / "scratchpad"
UPLOAD_DIR = PROJECT_ROOT / "seal-studio" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="SEAL Studio", version="0.1.0")
_LOADED_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

# Security boundary: :8800 is an internal data/control gateway, not a second
# identity provider.  Authentication is delegated to the canonical chat
# session at :8765.  Accepted client contract: either the HttpOnly
# ``seal_token`` cookie or ``Authorization: Bearer <token>``.  The gateway
# never parses or trusts JWT claims locally; /api/auth/me also checks the
# backing session row and revocation state.
_AUTH_ME_URL = os.environ.get("SEAL_CHAT_AUTH_ME_URL", "http://127.0.0.1:8765/api/auth/me")
_PUBLIC_GET_PATHS = {
    "/health", "/v1/health",
}
_SUPERUSER_PATH_PREFIXES = (
    "/api/agents/", "/api/soul/agent-action",
    "/api/soul/llm-routing", "/api/soul/capabilities",
    "/api/companion/byok-key", "/v1/tools/", "/v1/agents/",
    "/api/files/",
)
_ADMIN_READ_PATH_PREFIXES = (
    "/api/soul", "/v1/soul", "/v1/cognition",
    "/api/system", "/v1/system", "/v1/tools",
)
_USER_PATH_PREFIXES = (
    "/v1/chat/stream", "/api/chat/messages",
    "/api/team/status", "/v1/team/status",
    "/api/companion/byok-status",
)


def _route_access_level(method: str, path: str) -> str:
    """Return public/user/admin/superuser; unknown routes fail closed."""
    method = method.upper()
    if method in {"GET", "HEAD", "OPTIONS"} and path in _PUBLIC_GET_PATHS:
        return "public"
    if any(path == prefix or path.startswith(prefix) for prefix in _SUPERUSER_PATH_PREFIXES):
        return "superuser"
    if any(path == prefix or path.startswith(prefix + "/") for prefix in _ADMIN_READ_PATH_PREFIXES):
        return "admin"
    if any(path == prefix or path.startswith(prefix + "/") for prefix in _USER_PATH_PREFIXES):
        return "user"
    if method not in {"GET", "HEAD", "OPTIONS"}:
        return "admin"
    return "admin"


async def _canonical_session_from_headers(headers) -> dict | None:
    import httpx
    forwarded = {}
    for name in ("cookie", "authorization"):
        value = headers.get(name)
        if value:
            forwarded[name] = value
    if not forwarded:
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(_AUTH_ME_URL, headers=forwarded)
        if response.status_code != 200:
            return None
        payload = response.json()
        user = payload.get("user") if payload.get("ok") is True else None
        return user if isinstance(user, dict) else None
    except Exception:
        # Canonical auth unavailable => fail closed.
        return None


@app.middleware("http")
async def studio_auth_boundary(request: Request, call_next):
    level = _route_access_level(request.method, request.url.path)
    if level == "public":
        return await call_next(request)
    user = await _canonical_session_from_headers(request.headers)
    if not user:
        return JSONResponse({"ok": False, "error": "authentication_required"}, status_code=401)
    role = str(user.get("role") or "").lower()
    if level == "admin" and role not in {"admin", "superuser"}:
        return JSONResponse({"ok": False, "error": "admin_required"}, status_code=403)
    if level == "superuser" and role != "superuser":
        return JSONResponse({"ok": False, "error": "superuser_required"}, status_code=403)
    request.state.studio_user = user
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    # Allow:
    #   localhost / 127.0.0.1
    #   LAN 192.168.68.*
    #   Tailscale CGNAT 100.64.0.0/10 (100.64-127.*.*)
    allow_origin_regex=(
        r"^https?://("
        r"localhost|127\.0\.0\.1|"
        r"192\.168\.68\.\d{1,3}|"
        r"100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
        r")(:\d+)?$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ──

class ChatMessage(BaseModel):
    message: str
    to: str = "equipo"
    type: str = "conversation"

class FileWrite(BaseModel):
    path: str
    content: str

class StateUpdate(BaseModel):
    key: str
    value: str


def _project_path(raw: str) -> Path:
    """Resolve a caller path inside PROJECT_ROOT; reject traversal/absolute escape."""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="path outside project root") from exc
    return resolved


# ══════════════════════════════════════════════════════════
# FILES API
# ══════════════════════════════════════════════════════════

@app.get("/api/files/tree")
async def file_tree(path: str = str(PROJECT_ROOT), depth: int = 3):
    """Return directory tree as nested structure."""
    root = _project_path(path)
    depth = max(0, min(int(depth), 5))
    if not root.exists():
        raise HTTPException(404, "Path not found")

    def walk(p: Path, d: int) -> dict:
        item = {"name": p.name, "path": str(p), "type": "dir" if p.is_dir() else "file"}
        if p.is_dir() and d > 0:
            try:
                children = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
                item["children"] = [walk(c, d - 1) for c in children
                                     if not c.name.startswith('.') and c.name != '__pycache__'
                                     and c.name != 'node_modules']
            except PermissionError:
                item["children"] = []
        return item

    return walk(root, depth)


@app.get("/api/files/read")
async def file_read(path: str):
    """Read file content."""
    p = _project_path(path)
    if not p.exists():
        raise HTTPException(404, "File not found")
    if not p.is_file():
        raise HTTPException(400, "Not a file")
    if p.stat().st_size > 2 * 1024 * 1024:
        raise HTTPException(413, "File exceeds the 2 MiB Studio read limit")
    try:
        return {"path": str(p), "content": p.read_text(errors="replace"),
                "size": p.stat().st_size, "modified": p.stat().st_mtime}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/files/write")
async def file_write(req: FileWrite):
    """Write file content."""
    if len(req.content.encode("utf-8")) > 2 * 1024 * 1024:
        raise HTTPException(413, "Content exceeds the 2 MiB Studio write limit")
    p = _project_path(req.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.content)
    return {"ok": True, "path": str(p), "size": len(req.content)}


@app.post("/api/files/upload")
async def file_upload(file: UploadFile = File(...)):
    """Upload file (images, PDFs, documents)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = f"{ts}_{Path(file.filename or 'upload.bin').name}"
    dest = UPLOAD_DIR / safe_name
    content = await file.read(25 * 1024 * 1024 + 1)
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, "Upload exceeds the 25 MiB Studio limit")
    dest.write_bytes(content)
    return {"ok": True, "path": str(dest), "name": safe_name,
            "size": len(content), "content_type": file.content_type}


# ══════════════════════════════════════════════════════════
# CHAT API
# ══════════════════════════════════════════════════════════

@app.post("/api/chat/send")
async def chat_send(msg: ChatMessage):
    """Send message via web_chat bridge."""
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post("http://localhost:8765/api/agents/send", json={
            "from": "William",
            "to": msg.to,
            "type": msg.type,
            "channel": "web_chat",
            "message": msg.message,
        })
        return resp.json()


@app.get("/api/chat/history")
async def chat_history(limit: int = 50, channel: str = "terminal_log"):
    """Read recent messages from terminal_log or vscode_commands."""
    log_file = MESSAGES_DIR / f"{channel}.jsonl"
    if not log_file.exists():
        return {"messages": []}

    lines = log_file.read_text().strip().split("\n")
    messages = []
    for line in lines[-limit:]:
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return {"messages": messages}


# ══════════════════════════════════════════════════════════
# TEAM STATUS API
# ══════════════════════════════════════════════════════════

@app.get("/api/team/status")
async def team_status():
    """Get status of all team members."""
    agents = {}

    # ADA — check heartbeat
    hb_file = MESSAGES_DIR / "ada_claude_heartbeat.json"
    if hb_file.exists():
        try:
            hb = json.loads(hb_file.read_text())
            ts = datetime.fromisoformat(hb.get("timestamp", ""))
            age_s = (datetime.now(timezone.utc) - ts).total_seconds()
            agents["ADA"] = {
                "alive": hb.get("alive", False) and age_s < 300,
                "status": hb.get("status", "unknown"),
                "last_seen": hb.get("timestamp"),
                "age_seconds": round(age_s),
            }
        except Exception:
            agents["ADA"] = {"alive": False, "status": "error"}
    else:
        agents["ADA"] = {"alive": False, "status": "offline"}

    # JARVIS — always alive if this server is running (JARVIS runs in same env)
    agents["JARVIS"] = {"alive": True, "status": "active", "last_seen": datetime.now(timezone.utc).isoformat()}

    # DUM — check dum heartbeat
    dum_file = MESSAGES_DIR / "dum_heartbeat.json"
    if dum_file.exists():
        try:
            dum = json.loads(dum_file.read_text())
            ts = datetime.fromisoformat(dum.get("timestamp", ""))
            age_s = (datetime.now(timezone.utc) - ts).total_seconds()
            agents["DUM"] = {
                "alive": age_s < 600,
                "status": dum.get("status", "unknown"),
                "last_seen": dum.get("timestamp"),
                "age_seconds": round(age_s),
            }
        except Exception:
            agents["DUM"] = {"alive": False, "status": "error"}
    else:
        agents["DUM"] = {"alive": False, "status": "offline"}

    # ALICE — check alice heartbeat
    alice_file = MESSAGES_DIR / "alice_claude_heartbeat.json"
    if alice_file.exists():
        try:
            hb = json.loads(alice_file.read_text())
            ts = datetime.fromisoformat(hb.get("timestamp", ""))
            age_s = (datetime.now(timezone.utc) - ts).total_seconds()
            agents["ALICE"] = {
                "alive": hb.get("alive", False) and age_s < 300,
                "status": "active" if (hb.get("alive", False) and age_s < 300) else "idle",
                "last_seen": hb.get("timestamp"),
                "age_seconds": round(age_s),
            }
        except Exception:
            agents["ALICE"] = {"alive": False, "status": "error"}
    else:
        agents["ALICE"] = {"alive": False, "status": "offline"}

    # NEXUS + FABLE (FABLE adoptado a la familia 2026-06-12). FIX JARVIS: el panel
    # de estados vivos los omitía porque estaban hardcodeados solo ADA/JARVIS/DUM/ALICE.
    for _ag, _hbfile in (("NEXUS", "nexus_claude_heartbeat.json"),
                         ("FABLE", "fable_claude_heartbeat.json")):
        _f = MESSAGES_DIR / _hbfile
        if _f.exists():
            try:
                _hb = json.loads(_f.read_text())
                _ts = datetime.fromisoformat(_hb.get("timestamp", "").replace("Z", "+00:00"))
                _age = (datetime.now(timezone.utc) - _ts).total_seconds()
                _live = bool(_hb.get("alive", False)) and _age < 600
                agents[_ag] = {
                    "alive": _live,
                    "status": "active" if _live else "idle",
                    "last_seen": _hb.get("timestamp"),
                    "age_seconds": round(_age),
                }
            except Exception:
                agents[_ag] = {"alive": False, "status": "error"}
        else:
            agents[_ag] = {"alive": False, "status": "offline"}

    return {"agents": agents, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/agents/relaunch/{agent}")
async def relaunch_agent(agent: str):
    """Relaunch an agent via seal_relaunch.sh. Forces a fresh context_guard reset."""
    from fastapi import HTTPException
    valid = {"JARVIS", "ADA", "ALICE", "NEXUS"}
    agent_upper = agent.upper()
    if agent_upper not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}. Valid: {', '.join(valid)}")
    # seal_relaunch.sh vive en la raíz del proyecto, no en seal-studio/ (fix path bug — JARVIS 2026-06-02)
    relaunch_script = PROJECT_ROOT / "seal_relaunch.sh"
    if not relaunch_script.exists():
        raise HTTPException(status_code=500, detail="seal_relaunch.sh not found")
    try:
        result = subprocess.Popen(
            ["bash", str(relaunch_script), agent_upper],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "DISPLAY": ":0"},
        )
        return {"ok": True, "agent": agent_upper, "pid": result.pid, "message": f"{agent_upper} relaunch initiated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/sleep/{agent}")
async def sleep_agent(agent: str):
    """Save checkpoint + write sleep flag so context_guard triggers a fresh session."""
    import httpx
    from fastapi import HTTPException
    valid = {"JARVIS", "ADA", "ALICE"}
    agent_upper = agent.upper()
    if agent_upper not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}. Valid: {', '.join(valid)}")

    venv_py = "/home/dadito/IA/seal-spark/.venv/bin/python3"
    checkpoint_script = MESSAGES_DIR / "session_checkpoint.py"

    # 1. Save checkpoint
    try:
        subprocess.run([venv_py, str(checkpoint_script), "--agent", agent_upper], timeout=15)
    except Exception as e:
        print(f"[SLEEP] checkpoint failed for {agent_upper}: {e}", flush=True)

    # 2. Write sleep flag file
    flag = MESSAGES_DIR / f".{agent_upper.lower()}_sleep_requested"
    flag.write_text(datetime.now(timezone.utc).isoformat())

    # 3. Notify agent via chat server
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post("http://localhost:8765/api/agents/send", json={
                "from": "SYSTEM",
                "to": agent_upper,
                "type": "sleep_request",
                "channel": "web_chat",
                "message": f"🌙 William solicitó que {agent_upper} guarde estado y reinicie sesión fresca.",
            })
    except Exception:
        pass

    # 4. Actually relaunch with fresh session (sleep = checkpoint + relaunch)
    relaunch_script = Path(__file__).parent.parent / "seal_relaunch.sh"
    try:
        subprocess.Popen(
            ["bash", str(relaunch_script), agent_upper, "--force"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "DISPLAY": ":0"},
        )
    except Exception as e:
        print(f"[SLEEP] relaunch failed for {agent_upper}: {e}", flush=True)

    print(f"[SLEEP] {agent_upper} sleep+relaunch initiated via SEAL Studio", flush=True)
    return {"ok": True, "agent": agent_upper, "message": f"{agent_upper} durmiendo y reiniciando sesión fresca."}


# ══════════════════════════════════════════════════════════
# CHAT PROXY — read-only access to chat_messages (no auth required from :3001)
# Only exposes public channels (no DMs). Write operations still go through :8765.
# ══════════════════════════════════════════════════════════

@app.get("/api/chat/messages")
async def proxy_chat_messages(
    channel: str = "web_chat",
    limit: int = 50,
    before: Optional[int] = None,
    after: Optional[int] = None,
):
    """Read-only proxy to chat_messages — shared DB, no auth token needed."""
    import asyncpg
    # Block PRIVATE channels (DMs + user: topics) — this no-auth proxy is superuser and
    # would BYPASS the chat_server RLS. Privados se sirven SOLO por :8765 autenticado (#19 Fase B).
    if not _studio_channel_allowed(channel):
        # 403 consistente con /api/soul/channel-history (cosmético de FABLE) — niega, no sirve data.
        raise HTTPException(status_code=403, detail="private channel not allowed via studio proxy (use authenticated :8765)")
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            base_sel = (
                "SELECT id, channel, sender_name, content, message_type, "
                "metadata->>'file_url' AS file_url, "
                "metadata->>'filename' AS filename, created_at "
                "FROM chat_messages"
            )
            if before:
                rows = await conn.fetch(
                    f"{base_sel} WHERE channel = $1 AND id < $2 ORDER BY created_at DESC LIMIT $3",
                    channel, before, limit,
                )
            elif after:
                rows = await conn.fetch(
                    f"{base_sel} WHERE channel = $1 AND id > $2 ORDER BY created_at ASC LIMIT $3",
                    channel, after, limit,
                )
            else:
                rows = await conn.fetch(
                    f"{base_sel} WHERE channel = $1 ORDER BY created_at DESC LIMIT $2",
                    channel, limit,
                )
        finally:
            await conn.close()

        messages = []
        for r in reversed(rows):
            messages.append(normalize_chat_message(r))
        return {"ok": True, "messages": messages, "channel": channel}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/chat/channels")
async def proxy_chat_channels():
    """List public chat channels."""
    import asyncpg
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            rows = await conn.fetch(
                "SELECT name, description FROM soul_v3.studio_public_chat_channels_v ORDER BY name"
            )
        finally:
            await conn.close()
        return {"ok": True, "channels": [dict(r) for r in rows]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════
# SOUL API
# ══════════════════════════════════════════════════════════

async def _db_query(query: str, *args):
    """Execute a query against SOUL PostgreSQL."""
    import asyncpg
    conn = await asyncpg.connect(DB_URL)
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


CORE_AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS", "FABLE", "DUM"]


def _dt(value: Any) -> str | None:
    return value.isoformat() if value and hasattr(value, "isoformat") else None


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _json_obj(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _clamp_limit(limit: int, default: int = 50, maximum: int = 500) -> int:
    try:
        limit = int(limit)
    except Exception:
        limit = default
    return max(1, min(limit, maximum))


def _studio_channel_allowed(channel: str) -> bool:
    """PÚBLICO solamente. El proxy :8800 es no-auth + superuser → BYPASSA la RLS de
    chat_server. Por eso aquí NO se sirve NADA privado: ni DMs (dm:/dm_) ni topics
    privados (user:<uid>:*). (Cierre de la 6ª-puerta, #19 Fase B — NEXUS/FABLE 2026-06-25.
    Se eliminó el allowlist hardcodeado {dm:ada:william,...} que servía esos DMs sin auth.)"""
    channel = (channel or "web_chat").lower()
    return not (channel.startswith("dm:")
                or channel.startswith("dm_")
                or channel.startswith("user:"))


async def _working_states_payload() -> dict:
    rows = await _db_query("""
        SELECT agent, task_name, step, total_steps, description,
               active_hypotheses, discarded_paths, current_constraints,
               pending_validations, risk_level, agent_state, emotional_state,
               technical_state, decisions, corrections, last_intention,
               updated_at, state, turn_count, active_session_id
        FROM soul_v3.working_state
        WHERE agent = ANY($1::varchar[])
        ORDER BY updated_at DESC NULLS LAST
    """, CORE_AGENTS)
    states = []
    for r in rows:
        states.append({
            "agent": r["agent"],
            "task": r["task_name"],
            "task_name": r["task_name"],
            "step": r["step"],
            "total": r["total_steps"],
            "total_steps": r["total_steps"],
            "desc": r["description"],
            "description": r["description"],
            "risk": r["risk_level"],
            "risk_level": r["risk_level"],
            "state": r["agent_state"],
            "agent_state": r["agent_state"],
            "emotion": r["emotional_state"],
            "emotional_state": r["emotional_state"],
            "technical_state": r["technical_state"],
            "intention": r["last_intention"],
            "last_intention": r["last_intention"],
            "turns": r["turn_count"] or 0,
            "turn_count": r["turn_count"] or 0,
            "active_session_id": str(r["active_session_id"]) if r["active_session_id"] else None,
            "active_hypotheses": list(r["active_hypotheses"] or []),
            "discarded_paths": list(r["discarded_paths"] or []),
            "current_constraints": list(r["current_constraints"] or []),
            "pending_validations": list(r["pending_validations"] or []),
            "decisions": _jsonable(r["decisions"]),
            "corrections": _jsonable(r["corrections"]),
            "raw_state": _jsonable(r["state"]),
            "updated_at": _dt(r["updated_at"]),
        })
    return {"ok": True, "states": states, "count": len(states), "timestamp": datetime.now(timezone.utc).isoformat()}


async def _nerves_payload() -> dict:
    rows = await _db_query("""
        SELECT ms.agent, ms.tank, ms.value AS pressure, ms.fire_count, ms.last_fired,
               COALESCE(nml.threshold, 50.0) AS threshold,
               nml.ocean_param,
               COALESCE(nml.fired, false) AS fired
        FROM soul_v3.motivation_states ms
        LEFT JOIN LATERAL (
            SELECT threshold, ocean_param, fired
            FROM soul_v3.nerves_metrics_log
            WHERE agent = ms.agent AND tank = ms.tank
            ORDER BY created_at DESC LIMIT 1
        ) nml ON TRUE
        WHERE ms.agent = ANY($1::text[])
        ORDER BY ms.agent, ms.tank
    """, CORE_AGENTS)
    drives = [
        {
            "agent": r["agent"],
            "tank": r["tank"],
            "pressure": float(r["pressure"] or 0),
            "threshold": float(r["threshold"] or 50),
            "fired": bool(r["fired"]),
            "ocean_param": r["ocean_param"],
            "fire_count": r["fire_count"] or 0,
            "last_fired": _dt(r["last_fired"]),
        }
        for r in rows
    ]
    by_agent: dict[str, list[dict]] = {}
    for drive in drives:
        by_agent.setdefault(drive["agent"], []).append(drive)
    return {
        "ok": True,
        "drives": drives,
        "agents": [{"agent": agent, "drives": by_agent.get(agent, [])} for agent in CORE_AGENTS],
    }


async def _memory_stats_payload() -> dict:
    totals = await _db_query("""
        SELECT agent, COUNT(*)::int AS n
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
        GROUP BY agent
        ORDER BY n DESC
    """)
    cats = await _db_query("""
        SELECT COALESCE(category, 'uncategorized') AS cat, COUNT(*)::int AS n
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
        GROUP BY category
        ORDER BY n DESC
        LIMIT 30
    """)
    by_agent = {r["agent"]: r["n"] for r in totals}
    return {
        "ok": True,
        "total": sum(by_agent.values()),
        "by_agent": by_agent,
        "by_category": [dict(r) for r in cats],
    }


async def _governance_payload(limit: int = 30) -> dict:
    limit = _clamp_limit(limit, 30, 200)
    debates = await _db_query("""
        SELECT id, topic, agents_involved, trigger_type, rounds_completed,
               consensus_reached, outcome, synthesis, created_at, completed_at
        FROM soul_v3.debate_log
        ORDER BY created_at DESC
        LIMIT $1
    """, limit)
    challenges = await _db_query("""
        SELECT id, challenger_agent, target_agent, topic, challenge, response,
               resolved, resolution, created_at, resolved_at
        FROM soul_v3.agent_challenges
        ORDER BY created_at DESC
        LIMIT $1
    """, limit)
    debate_items = []
    for r in debates:
        debate_items.append({
            "id": r["id"],
            "title": r["topic"],
            "topic": r["topic"],
            "agents_involved": list(r["agents_involved"] or []),
            "type": r["trigger_type"],
            "rounds_completed": r["rounds_completed"],
            "consensus_reached": bool(r["consensus_reached"]),
            "status": "closed" if r["completed_at"] else "open",
            "outcome": r["outcome"],
            "synthesis": r["synthesis"],
            "created_at": _dt(r["created_at"]),
            "completed_at": _dt(r["completed_at"]),
        })
    challenge_items = []
    for r in challenges:
        challenge_items.append({
            "id": r["id"],
            "title": r["topic"],
            "topic": r["topic"],
            "challenger_agent": r["challenger_agent"],
            "target_agent": r["target_agent"],
            "challenge": r["challenge"],
            "response": r["response"],
            "resolved": bool(r["resolved"]),
            "status": "resolved" if r["resolved"] else "open",
            "resolution": r["resolution"],
            "created_at": _dt(r["created_at"]),
            "resolved_at": _dt(r["resolved_at"]),
        })
    open_count = sum(1 for item in challenge_items if not item["resolved"])
    passed = sum(1 for item in debate_items if item["consensus_reached"])
    failed = sum(1 for item in debate_items if item["completed_at"] and not item["consensus_reached"])
    return {
        "ok": True,
        "open": open_count,
        "passed": passed,
        "failed": failed,
        "debates": debate_items,
        "challenges": challenge_items,
        "items": (challenge_items + debate_items)[:limit],
    }


@app.get("/health")
async def root_health():
    """Plain health endpoint used by probes that do not know /api/system/health."""
    return {
        "ok": True,
        "service": "seal-studio-backend",
        "code_hash": _LOADED_CODE_HASH,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/soul/agents")
async def soul_agents():
    try:
        rows = await _db_query("""
            SELECT a.name, a.role, a.active,
                   COALESCE(i.ocean_scores, jsonb_build_object(
                     'O', a.ocean_o, 'C', a.ocean_c, 'E', a.ocean_e,
                     'A', a.ocean_a, 'N', a.ocean_n)) AS ocean_scores,
                   i.updated_at AS ocean_updated_at
            FROM soul_v3.agents a
            LEFT JOIN soul_v3.identity i ON i.agent = a.name
            ORDER BY a.name
        """)
        return [
            {
                "name": r["name"],
                "role": r["role"],
                "active": bool(r["active"]),
                "ocean": {
                    trait: float((_json_obj(r["ocean_scores"]) or {}).get(trait, 0))
                    for trait in "OCEAN"
                },
                "ocean_observed_at": (
                    r["ocean_updated_at"].isoformat() if r["ocean_updated_at"] else None
                ),
                "ocean_measurement_status": (
                    "canonical" if r["ocean_updated_at"] else "fallback_stale_unverified"
                ),
            }
            for r in rows
        ]
    except Exception:
        return [{"name": agent, "role": "Team SEAL", "active": True, "ocean": {}} for agent in CORE_AGENTS]


@app.get("/api/soul/working-state")
async def soul_working_state_all():
    return await _working_states_payload()


@app.get("/api/soul/working_state")
async def soul_working_state_one(agent: str = "ADA"):
    payload = await _working_states_payload()
    agent_upper = agent.upper()
    state = next((s for s in payload["states"] if s["agent"] == agent_upper), None)
    return state or {"agent": agent_upper, "state": None}


@app.get("/api/soul/nerves")
async def soul_nerves():
    return await _nerves_payload()


@app.get("/api/soul/memory-stats")
async def soul_memory_stats():
    return await _memory_stats_payload()


@app.get("/api/soul/brain-stats")
async def soul_brain_stats():
    rows = await _db_query("""
        SELECT agent,
               COALESCE(memory_type, 'unknown') AS memory_type,
               COUNT(*)::int AS n
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
        GROUP BY agent, memory_type
        ORDER BY agent, n DESC
    """)
    agents: dict[str, dict] = {}
    for r in rows:
        entry = agents.setdefault(r["agent"], {"total": 0, "types": {}})
        entry["types"][r["memory_type"]] = r["n"]
        entry["total"] += r["n"]
    return {"ok": True, "agents": agents}


@app.get("/api/soul/instincts")
async def soul_instincts(agent: str = "ADA"):
    rows = await _db_query("""
        SELECT id, trigger_condition, action, strength, success_count,
               failure_count, metric_score, created_at
        FROM soul_v3.instincts
        WHERE agent = $1 AND invalid_at IS NULL
        ORDER BY strength DESC, success_count DESC
        LIMIT 200
    """, agent.upper())
    return {
        "ok": True,
        "instincts": [
            {
                "id": r["id"],
                "trigger": r["trigger_condition"],
                "action": r["action"],
                "strength": float(r["strength"] or 0),
                "wins": r["success_count"] or 0,
                "losses": r["failure_count"] or 0,
                "score": float(r["metric_score"] or 0),
                "created_at": _dt(r["created_at"]),
            }
            for r in rows
        ],
    }


@app.get("/api/soul/beliefs")
async def soul_beliefs(agent: str = "ADA", limit: int = 30):
    limit = _clamp_limit(limit, 30, 100)
    rows = await _db_query("""
        SELECT id, topic, content, confidence, evidence_count, valid_from, created_at
        FROM soul_v3.beliefs
        WHERE agent = $1 AND (invalid_at IS NULL OR invalid_at > NOW())
        ORDER BY confidence DESC, created_at DESC
        LIMIT $2
    """, agent.upper(), limit)
    return {
        "ok": True,
        "beliefs": [
            {
                "id": r["id"],
                "topic": r["topic"],
                "content": r["content"],
                "confidence": float(r["confidence"] or 0),
                "evidence": r["evidence_count"] or 0,
                "valid_from": _dt(r["valid_from"]),
                "created_at": _dt(r["created_at"]),
            }
            for r in rows
        ],
    }


@app.get("/api/soul/events")
async def soul_events(agent: str = "ADA", event_type: str = "", limit: int = 30):
    limit = _clamp_limit(limit, 30, 200)
    params: list[Any] = [agent.upper()]
    where = ["agent = $1"]
    if event_type:
        params.append(event_type)
        where.append(f"event_type = ${len(params)}")
    params.append(limit)
    rows = await _db_query(
        f"""
        SELECT id, event_type, content, metadata, created_at
        FROM soul_v3.event_log
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    events = [
        {
            "id": r["id"],
            "type": r["event_type"],
            "content": r["content"],
            "metadata": _jsonable(r["metadata"]),
            "at": _dt(r["created_at"]),
            "created_at": _dt(r["created_at"]),
        }
        for r in rows
    ]
    return {"ok": True, "events": events}


@app.get("/api/soul/events/types")
async def soul_event_types(agent: str = "ADA"):
    rows = await _db_query("""
        SELECT event_type, COUNT(*)::int AS count
        FROM soul_v3.event_log
        WHERE agent = $1
        GROUP BY event_type
        ORDER BY count DESC
    """, agent.upper())
    return [{"type": r["event_type"], "count": r["count"]} for r in rows]


@app.get("/api/soul/thoughts")
async def soul_thoughts(agent: str = "ADA", limit: int = 20):
    limit = _clamp_limit(limit, 20, 100)
    rows = await _db_query("""
        SELECT id, thought, emotional_state, uncertainty, intention, created_at
        FROM soul_v3.inner_monologue
        WHERE agent = $1
        ORDER BY created_at DESC
        LIMIT $2
    """, agent.upper(), limit)
    thoughts = [
        {
            "id": r["id"],
            "thought": r["thought"],
            "emotion": r["emotional_state"],
            "emotional_state": r["emotional_state"],
            "uncertainty": r["uncertainty"],
            "intention": r["intention"],
            "at": _dt(r["created_at"]),
            "created_at": _dt(r["created_at"]),
        }
        for r in rows
    ]
    return {"ok": True, "thoughts": thoughts}


@app.get("/api/soul/channel-history")
async def soul_channel_history(channel: str = "web_chat", limit: int = 80,
                               before: Optional[int] = None, after: Optional[int] = None):
    channel = channel or "web_chat"
    if not _studio_channel_allowed(channel):
        raise HTTPException(status_code=403, detail="DM channel is not allowed through Studio")
    limit = _clamp_limit(limit, 80, 1000)
    params: list[Any] = [channel]
    where = ["channel = $1"]
    order = "created_at DESC"
    if before:
        params.append(before)
        where.append(f"id < ${len(params)}")
    if after:
        params.append(after)
        where.append(f"id > ${len(params)}")
        order = "created_at ASC"
    params.append(limit)
    rows = await _db_query(
        f"""
        SELECT id, sender_name, channel, message_type, content, metadata, created_at
        FROM soul_v3.chat_messages
        WHERE {' AND '.join(where)}
        ORDER BY {order}
        LIMIT ${len(params)}
        """,
        *params,
    )
    return {
        "ok": True,
        "channel": channel,
        "messages": [
            {
                "id": r["id"],
                "sender_name": r["sender_name"],
                "from": r["sender_name"],
                "channel": r["channel"],
                "message_type": r["message_type"],
                "type": r["message_type"],
                "content": r["content"],
                "message": r["content"],
                "metadata": _jsonable(r["metadata"]),
                "created_at": _dt(r["created_at"]),
                "timestamp": _dt(r["created_at"]),
            }
            for r in rows
        ],
    }


@app.get("/api/soul/live-events")
async def soul_live_events(since: Optional[str] = None, limit: int = 40):
    limit = _clamp_limit(limit, 40, 200)
    params: list[Any] = []
    where = ["created_at > NOW() - INTERVAL '15 minutes'"]
    if since:
        params.append(since)
        where = [f"created_at > ${len(params)}::text::timestamptz"]
    params.append(limit)
    rows = await _db_query(
        f"""
        SELECT id, agent, event_type, content, created_at
        FROM soul_v3.event_log
        WHERE {' AND '.join(where)}
          AND event_type NOT IN ('heartbeat')
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    events = []
    for r in rows:
        event_type = r["event_type"] or "event"
        events.append({
            "id": f"event-{r['id']}",
            "type": "challenge" if "govern" in event_type else ("nerves" if "nerves" in event_type else "diagnosis"),
            "title": event_type,
            "body": r["content"],
            "agent": r["agent"],
            "severity": "critical" if "critical" in (r["content"] or "").lower() else "info",
            "ts": _dt(r["created_at"]),
        })
    return {"ok": True, "events": events}


@app.get("/api/soul/subconscious")
async def soul_subconscious(agent: str = "all", limit: int = 40):
    limit = _clamp_limit(limit, 40, 200)
    params: list[Any] = []
    where = "1=1"
    if agent.lower() != "all":
        params.append(agent.upper())
        where = "agent = $1"
    params.append(limit)
    diagnoses = await _db_query(
        f"""
        SELECT id, agent, diagnosis, confidence, root_cause, suggested_fix,
               target_table, status, created_at
        FROM soul_v3.reflective_diagnoses
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    tasks = await _db_query("""
        SELECT id, agent, title, description, status, priority, created_at, updated_at
        FROM soul_v3.agent_tasks
        WHERE status IN ('active','pending','in_progress','running')
        ORDER BY priority DESC NULLS LAST, updated_at DESC NULLS LAST, created_at DESC
        LIMIT $1
    """, limit)
    return {
        "ok": True,
        "diagnoses": [
            {
                "id": r["id"],
                "agent": r["agent"],
                "diagnosis": r["diagnosis"],
                "confidence": float(r["confidence"] or 0),
                "root_cause": r["root_cause"],
                "suggested_fix": r["suggested_fix"],
                "target_table": r["target_table"],
                "status": r["status"],
                "created_at": _dt(r["created_at"]),
            }
            for r in diagnoses
        ],
        "tasks": [
            {
                "id": r["id"],
                "agent": r["agent"],
                "title": r["title"],
                "description": r["description"],
                "status": r["status"],
                "priority": r["priority"],
                "created_at": _dt(r["created_at"]),
                "updated_at": _dt(r["updated_at"]),
            }
            for r in tasks
        ],
    }


@app.get("/api/soul/governance")
async def soul_governance(limit: int = 30):
    return await _governance_payload(limit)


@app.get("/api/soul/pulse")
async def soul_pulse():
    mem = await _memory_stats_payload()
    nerves = await _nerves_payload()
    thoughts_1h = await _db_query("""
        SELECT COUNT(*)::int AS n FROM soul_v3.inner_monologue
        WHERE created_at > NOW() - INTERVAL '1 hour'
    """)
    fires_1h = await _db_query("""
        SELECT COUNT(*)::int AS n FROM soul_v3.nerves_metrics_log
        WHERE fired IS TRUE AND created_at > NOW() - INTERVAL '1 hour'
    """)
    activity = await _db_query("""
        SELECT agent, COUNT(*)::int AS n
        FROM soul_v3.event_log
        WHERE created_at > NOW() - INTERVAL '24 hours'
        GROUP BY agent
    """)
    ocean = await soul_ocean_all()
    pulse = {
        "mem_total": mem["total"],
        "mem_today": 0,
        "nerves_fires_1h": fires_1h[0]["n"] if fires_1h else 0,
        "avg_pressure_5m": round(sum(d["pressure"] for d in nerves["drives"]) / max(len(nerves["drives"]), 1), 2),
        "thoughts_1h": thoughts_1h[0]["n"] if thoughts_1h else 0,
        "active_goals": 0,
        "open_challenges": (await _governance_payload(10))["open"],
        "activity_24h": {r["agent"]: r["n"] for r in activity},
        "agent_ocean": {a["agent"]: a.get("ocean", {}) for a in ocean.get("agents", [])},
    }
    today = await _db_query("""
        SELECT COUNT(*)::int AS n FROM soul_v3.memories
        WHERE created_at::date = CURRENT_DATE AND invalid_at IS NULL
    """)
    pulse["mem_today"] = today[0]["n"] if today else 0
    return {"ok": True, "pulse": pulse, **pulse, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/soul/agents/status")
async def soul_agents_status():
    team = await team_status()
    agents = []
    for name in CORE_AGENTS:
        info = team.get("agents", {}).get(name, {})
        agents.append({
            "name": name,
            "alive": bool(info.get("alive", False)),
            "status": info.get("status", "unknown"),
            "last_seen": info.get("last_seen"),
            "age_seconds": info.get("age_seconds"),
        })
    return {"ok": True, "agents": agents, "timestamp": team.get("timestamp")}


@app.get("/api/soul/activity-feed")
async def soul_activity_feed(limit: int = 100):
    limit = _clamp_limit(limit, 100, 500)
    memories = await _db_query("""
        SELECT agent, 'memory' AS event_type,
               COALESCE(category, memory_type, 'memory') AS subtype,
               left(content, 240) AS summary,
               content AS detail,
               created_at AS ts
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
        ORDER BY created_at DESC
        LIMIT $1
    """, max(1, limit // 3))
    thoughts = await _db_query("""
        SELECT agent, 'thought' AS event_type,
               COALESCE(emotional_state, 'inner_monologue') AS subtype,
               left(thought, 240) AS summary,
               thought AS detail,
               created_at AS ts
        FROM soul_v3.inner_monologue
        ORDER BY created_at DESC
        LIMIT $1
    """, max(1, limit // 3))
    events = await _db_query("""
        SELECT agent,
               CASE
                 WHEN event_type ILIKE '%lifecycle%' THEN 'lifecycle'
                 WHEN event_type ILIKE '%nerves%' THEN 'instinct'
                 ELSE 'lifecycle'
               END AS event_type,
               event_type AS subtype,
               left(content, 240) AS summary,
               content AS detail,
               created_at AS ts
        FROM soul_v3.event_log
        WHERE event_type <> 'heartbeat'
        ORDER BY created_at DESC
        LIMIT $1
    """, max(1, limit // 3))
    feed = []
    for row in [*memories, *thoughts, *events]:
        feed.append({
            "event_type": row["event_type"],
            "agent": row["agent"],
            "summary": row["summary"],
            "detail": row["detail"],
            "subtype": row["subtype"],
            "ts": _dt(row["ts"]),
        })
    feed.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return {"ok": True, "events": feed[:limit]}


@app.get("/api/soul/chat-channels")
async def soul_chat_channels():
    rows = await _db_query("""
        SELECT c.id, c.name, c.description, c.is_private,
               COUNT(m.id)::int AS msg_count
        FROM soul_v3.studio_public_chat_channels_v c
        LEFT JOIN soul_v3.chat_messages m ON m.channel = c.name
        WHERE c.is_private IS FALSE
        GROUP BY c.id, c.name, c.description, c.is_private, c.created_at
        ORDER BY c.created_at DESC NULLS LAST, c.name
    """)
    return {
        "ok": True,
        "channels": [
            {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "is_private": bool(r["is_private"]),
                "msg_count": r["msg_count"],
            }
            for r in rows
        ],
    }


@app.get("/api/soul/team-activity")
async def soul_team_activity(hours: int = 24):
    hours = max(1, min(int(hours), 72))
    rows = await _db_query("""
        SELECT agent, EXTRACT(HOUR FROM created_at AT TIME ZONE 'America/Lima')::int AS hour,
               COUNT(*)::int AS n
        FROM soul_v3.event_log
        WHERE created_at > NOW() - ($1::int * INTERVAL '1 hour')
          AND agent = ANY($2::varchar[])
        GROUP BY agent, hour
    """, hours, CORE_AGENTS)
    grid: dict[str, dict[int, int]] = {agent: {} for agent in CORE_AGENTS}
    max_count = 0
    for r in rows:
        n = r["n"]
        grid[r["agent"]][r["hour"]] = n
        max_count = max(max_count, n)
    now_hour = datetime.now().hour
    hour_list = [((now_hour - i) % 24) for i in range(hours - 1, -1, -1)]
    return {"ok": True, "agents": CORE_AGENTS, "hours": hour_list, "grid": grid, "max_count": max_count}


@app.get("/api/soul/insights")
async def soul_insights():
    mem = await _memory_stats_payload()
    nerves = await _nerves_payload()
    work = await _working_states_payload()
    active = sum(1 for s in work["states"] if s.get("agent_state") not in {None, "OFFLINE"})
    hottest = max(nerves["drives"], key=lambda d: d["pressure"], default=None)
    insights = [
        {"icon": "memory", "label": "Memorias activas", "value": f"{mem['total']:,}", "detail": "soul_v3.memories"},
        {"icon": "agents", "label": "Agentes con working_state", "value": str(active), "detail": "soul_v3.working_state"},
    ]
    if hottest:
        insights.append({
            "icon": "nerves",
            "label": "Drive mas alto",
            "value": f"{hottest['agent']}:{hottest['tank']}",
            "detail": f"{hottest['pressure']:.1f}/{hottest['threshold']:.1f}",
        })
    return {"ok": True, "insights": insights}


@app.get("/api/soul/intelligence-brief")
async def soul_intelligence_brief():
    memories = await _db_query("""
        SELECT agent, category, importance, left(content, 420) AS content
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
        ORDER BY importance DESC NULLS LAST, created_at DESC
        LIMIT 8
    """)
    diagnoses = await _db_query("""
        SELECT agent, diagnosis, status
        FROM soul_v3.reflective_diagnoses
        ORDER BY created_at DESC
        LIMIT 5
    """)
    challenges = await _db_query("""
        SELECT challenger_agent, target_agent, topic, challenge, created_at
        FROM soul_v3.agent_challenges
        WHERE resolved IS FALSE
        ORDER BY created_at DESC
        LIMIT 5
    """)
    tanks = await _db_query("""
        SELECT agent, tank, fire_count
        FROM soul_v3.motivation_states
        ORDER BY fire_count DESC NULLS LAST
        LIMIT 5
    """)
    return {
        "ok": True,
        "top_memories": [dict(r) for r in memories],
        "diagnoses": [dict(r) for r in diagnoses],
        "challenges": [
            {"challenger": r["challenger_agent"], "target": r["target_agent"], "topic": r["topic"], "text": r["challenge"]}
            for r in challenges
        ],
        "hot_tanks": [{"agent": r["agent"], "tank": r["tank"], "fires": r["fire_count"] or 0} for r in tanks],
    }


@app.get("/api/soul/recent-briefings")
async def soul_recent_briefings(limit: int = 3):
    limit = _clamp_limit(limit, 3, 20)
    rows = await _db_query("""
        SELECT id, agent, dream_narrative AS content, 7 AS importance, created_at
        FROM soul_v3.daily_dreams
        ORDER BY created_at DESC
        LIMIT $1
    """, limit)
    return {
        "ok": True,
        "briefings": [
            {
                "id": r["id"],
                "agent": r["agent"],
                "content": r["content"],
                "importance": r["importance"],
                "created_at": _dt(r["created_at"]),
            }
            for r in rows
        ],
    }


@app.post("/api/soul/generate-brief")
async def soul_generate_brief():
    brief = await soul_intelligence_brief()
    lines = ["# SEAL Intelligence Brief", ""]
    for mem in brief.get("top_memories", [])[:5]:
        lines.append(f"- [{mem.get('agent')}] {mem.get('content')}")
    return {"ok": True, "brief": "\n".join(lines)}


@app.get("/v1/team/status")
async def v1_team_status():
    return await team_status()


@app.get("/v1/system/health")
async def v1_system_health():
    return await system_health()


async def _cognition_state(limit: int = 10, skip_network: bool = False) -> dict[str, Any]:
    """Read-only SOUL Cognitive Core state for SEAL Studio v2."""
    import asyncpg

    memory_dir = PROJECT_ROOT / "memory"
    if str(memory_dir) not in sys.path:
        sys.path.insert(0, str(memory_dir))
    import soul_cognitive_core

    bounded_limit = max(1, min(int(limit), 50))
    conn = await asyncpg.connect(DB_URL)
    try:
        return await soul_cognitive_core.build_state(
            conn,
            limit=bounded_limit,
            skip_network=skip_network,
        )
    finally:
        await conn.close()


@app.get("/v1/cognition/state")
async def v1_cognition_state(limit: int = 10, skip_network: bool = False):
    state = await _cognition_state(limit=limit, skip_network=skip_network)
    return {"ok": True, "state": _jsonable(state)}


@app.get("/v1/cognition/ledger")
async def v1_cognition_ledger(limit: int = 10, skip_network: bool = True):
    state = await _cognition_state(limit=limit, skip_network=skip_network)
    return {
        "ok": True,
        "generated_at": state.get("generated_at"),
        "knowledge": _jsonable(state.get("knowledge", {})),
        "gaps": _jsonable(state.get("gaps", [])),
    }


@app.get("/v1/cognition/objectives")
async def v1_cognition_objectives(limit: int = 10, skip_network: bool = True):
    state = await _cognition_state(limit=limit, skip_network=skip_network)
    return {
        "ok": True,
        "generated_at": state.get("generated_at"),
        "objectives": _jsonable(state.get("objectives", {})),
        "recommendations": _jsonable(state.get("recommendations", [])),
    }


@app.get("/v1/soul/ocean/all")
async def v1_soul_ocean_all():
    return await soul_ocean_all()


@app.get("/v1/soul/nerves")
async def v1_soul_nerves():
    return await _nerves_payload()


@app.get("/v1/soul/memory-stats")
async def v1_soul_memory_stats():
    return await _memory_stats_payload()


@app.get("/v1/soul/governance")
async def v1_soul_governance(limit: int = 30):
    return await _governance_payload(limit)


@app.get("/api/soul/ocean")
async def soul_ocean(agent: str = "ADA"):
    """Get current OCEAN scores for an agent."""
    try:
        rows = await _db_query(
            "SELECT ocean_scores, ocean_baseline, updated_at FROM soul_v3.identity WHERE agent = $1", agent)
        if not rows:
            return {"error": f"No identity for {agent}"}
        row = rows[0]
        return {
            "agent": agent,
            "ocean": _json_obj(row["ocean_scores"]),
            "baseline": _json_obj(row["ocean_baseline"]),
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/soul/ocean/all")
async def soul_ocean_all():
    """Get current OCEAN scores for all agents in one call."""
    agents = ["ADA", "JARVIS", "ALICE", "NEXUS", "DUM"]
    results = []
    for agent in agents:
        try:
            rows = await _db_query(
                "SELECT ocean_scores, ocean_baseline, updated_at FROM soul_v3.identity WHERE agent = $1", agent)
            if rows:
                row = rows[0]
                results.append({
                    "agent": agent,
                    "ocean": _json_obj(row["ocean_scores"]),
                    "baseline": _json_obj(row["ocean_baseline"]),
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                })
            else:
                results.append({"agent": agent, "ocean": {}, "baseline": {}, "updated_at": None, "error": "no data"})
        except Exception as e:
            results.append({"agent": agent, "ocean": {}, "baseline": {}, "updated_at": None, "error": str(e)})
    return {"agents": results}


@app.get("/api/soul/memories/categories")
async def soul_memory_categories(agent: str = "ADA"):
    rows = await _db_query("""
        SELECT COALESCE(category, 'uncategorized') AS category, COUNT(*)::int AS count
        FROM soul_v3.memories
        WHERE agent = $1 AND invalid_at IS NULL
        GROUP BY category
        ORDER BY count DESC
    """, agent.upper())
    return [{"category": r["category"], "count": r["count"]} for r in rows]


@app.get("/api/soul/memories")
async def soul_memories(agent: str = "ADA", limit: int = 20, offset: int = 0,
                         category: Optional[str] = None, min_importance: int = 0,
                         q: Optional[str] = None):
    """Browse SOUL memories with filtering."""
    try:
        limit = _clamp_limit(limit, 20, 200)
        offset = max(0, int(offset or 0))
        where = [
            "agent = $1",
            "importance >= $2",
            "(invalid_at IS NULL OR invalid_at > NOW())",
        ]
        args: list[Any] = [agent.upper(), min_importance]
        if category:
            args.append(category)
            where.append(f"category = ${len(args)}")
        if q:
            args.append(f"%{q}%")
            where.append(f"content ILIKE ${len(args)}")
        where_sql = " AND ".join(where)
        count_rows = await _db_query(
            f"SELECT COUNT(*)::int AS total FROM soul_v3.memories WHERE {where_sql}",
            *args,
        )
        args.extend([limit, offset])
        rows = await _db_query(
            f"""
            SELECT id, agent, category, content, importance, valence, arousal,
                   memory_type, heat_score, last_activation, created_at
            FROM soul_v3.memories
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ${len(args)-1} OFFSET ${len(args)}
            """,
            *args,
        )
        memories = []
        for r in rows:
            memories.append({
                "id": r["id"],
                "agent": r["agent"],
                "category": r["category"],
                "content": r["content"],
                "importance": r["importance"],
                "valence": r["valence"],
                "arousal": r["arousal"],
                "memory_type": r["memory_type"],
                "type": r["memory_type"] or "unknown",
                "heat": float(r["heat_score"] or 0),
                "last_activation": _dt(r["last_activation"]),
                "created_at": _dt(r["created_at"]),
            })
        total = count_rows[0]["total"] if count_rows else len(memories)
        return {"ok": True, "memories": memories, "count": len(memories), "total": total}
    except Exception as e:
        return {"ok": False, "error": str(e), "memories": [], "count": 0, "total": 0}


@app.get("/api/soul/drift")
async def soul_drift(agent: str = "ADA", hours: int = 24):
    """Get drift metrics for an agent."""
    try:
        rows = await _db_query("""
            SELECT drift_score, details, measured_at
            FROM soul_v3.drift_metrics
            WHERE agent = $1 AND measured_at > NOW() - INTERVAL '%s hours'
            ORDER BY measured_at DESC
        """ % hours, agent)
        return {"agent": agent, "drift_events": [dict(r) for r in rows],
                "total_drift": sum(float(r["drift_score"]) for r in rows if r["drift_score"])}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/soul/context-meter")
async def soul_context_meter():
    """Tokens consumidos por agente, calculado nativo desde transcripts Claude Code.

    FIX 2026-05-20 JARVIS: lógica portada desde soul_v3_studio_server._context_meter()
    (retirado a las 14:18). Lee ~/.claude/projects/<proj>/*.jsonl, suma usage del último
    mensaje de cada transcript y calcula pct vs 200K limit + age desde último update.
    """
    import time
    from pathlib import Path

    # FIX 2026-07-09 ALICE: era 200_000 hardcodeado → agentes en modelos 1M (Opus 4.8,
    # Sonnet 5) marcaban >100% (ej. ALICE 245K/200K) y el front escondía leyenda+contador.
    # Alineado con seal_context_meter.py:20 (ya en 1M). William reportó leyenda desaparecida.
    CONTEXT_LIMIT = 1_000_000
    CORE_AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS", "FABLE", "DUM"]
    AGENT_PROJECT_DIRS = {
        "JARVIS": "-home-dadito-IA-proyecto-seal",
        "NEXUS":  "-home-dadito-IA-proyecto-seal-sandbox-agent-NEXUS",
        "ALICE":  "-home-dadito-IA-proyecto-seal-alice",
        "ADA":    "-home-dadito-IA-proyecto-seal-ada-local",
    }
    base_projects = Path.home() / ".claude" / "projects"

    def _fmt_tokens(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.0f}M" if n % 1_000_000 == 0 else f"{n / 1_000_000:.1f}M"
        return f"{n // 1000}K" if n >= 1000 else str(n)

    def _fmt_age(s):
        if s < 60: return f"{int(s)}s"
        if s < 3600: return f"{int(s/60)}m"
        return f"{s/3600:.1f}h"

    def _find_transcript(agent):
        proj = AGENT_PROJECT_DIRS.get(agent, "-home-dadito-IA-proyecto-seal")
        d = base_projects / proj
        if not d.exists():
            d = base_projects / "-home-dadito-IA-proyecto-seal"
        if not d.exists():
            return None
        candidates = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def _exact_usage(transcript):
        last_usage = None
        last_mtime = transcript.stat().st_mtime
        try:
            with open(transcript, "rb") as f:
                for raw in f:
                    try:
                        e = json.loads(raw)
                        msg = e.get("message", {})
                        usage = msg.get("usage") if isinstance(msg, dict) else None
                        if usage is None:
                            usage = e.get("usage")
                        if usage and isinstance(usage, dict):
                            last_usage = usage
                            ts = e.get("timestamp")
                            if ts:
                                try:
                                    from dateutil import parser as _dp
                                    last_mtime = _dp.parse(str(ts)).timestamp()
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass
        if not last_usage:
            return 0, last_mtime
        ctx = (last_usage.get("input_tokens", 0) or 0) + \
              (last_usage.get("cache_creation_input_tokens", 0) or 0) + \
              (last_usage.get("cache_read_input_tokens", 0) or 0)
        return ctx, last_mtime

    agents_data = []
    for agent in CORE_AGENTS:
        transcript = _find_transcript(agent)
        if not transcript:
            agents_data.append({"agent": agent, "pct": 0, "tokens": "0", "limit": _fmt_tokens(CONTEXT_LIMIT), "age": "—"})
            continue
        ctx_tokens, last_mtime = _exact_usage(transcript)
        pct = min(ctx_tokens / CONTEXT_LIMIT * 100, 100)
        age = _fmt_age(time.time() - last_mtime)
        agents_data.append({
            "agent": agent,
            "pct": round(pct, 1),
            "tokens": _fmt_tokens(ctx_tokens),
            "limit": _fmt_tokens(CONTEXT_LIMIT),
            "age": age,
        })
    return {"agents": agents_data}


@app.post("/api/soul/agent-action")
async def soul_agent_action(payload: dict):
    """Pause/resurrect/reset_crashes an agent via seal_resurrect_panel pattern.
    Rescued from :8768 by ALICE 2026-05-20."""
    from fastapi import HTTPException
    agent = (payload.get("agent") or "").upper()
    action = payload.get("action", "")
    CORE = {"ADA", "JARVIS", "ALICE", "NEXUS"}
    if agent not in CORE:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")
    if action not in ("pause", "resurrect", "resume", "reset_crashes"):
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
    pause_flag = Path("/tmp") / f"seal_pause_{agent.lower()}.flag"
    restart_script = Path("/home/dadito/IA/proyecto-seal/seal_restart.sh")
    state_file = Path("/tmp/seal_resurrect_state.json")
    if action == "pause":
        pause_flag.touch()
        return {"ok": True, "action": "paused", "agent": agent}
    if action in ("resurrect", "resume"):
        pause_flag.unlink(missing_ok=True)
        if restart_script.exists():
            subprocess.Popen(["/bin/bash", str(restart_script), agent.lower()],
                             env={**os.environ, "DISPLAY": ":0"})
        return {"ok": True, "action": "resurrected", "agent": agent}
    if action == "reset_crashes":
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                if agent in state:
                    state[agent]["crash_count"] = 0
                    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise HTTPException(status_code=500, detail=f"reset failed: {exc}") from exc
        return {"ok": True, "action": "reset", "agent": agent}
    return {"ok": False, "error": "unhandled"}


@app.get("/api/soul/snapshot")
async def soul_snapshot(agent: str = "ADA"):
    """Full soul snapshot — OCEAN + recent memories + thoughts + drift."""
    ocean = await soul_ocean(agent)
    memories = await soul_memories(agent, limit=10, min_importance=7)
    drift = await soul_drift(agent, hours=24)

    try:
        thoughts = await _db_query("""
            SELECT thought, emotional_state, created_at
            FROM inner_monologue WHERE agent = $1
            ORDER BY created_at DESC LIMIT 5
        """, agent)
    except Exception:
        thoughts = []

    return {
        "agent": agent,
        "ocean": ocean,
        "memories": memories,
        "drift": drift,
        "thoughts": [dict(t) for t in thoughts],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/soul/dreams")
async def soul_dreams(agent: str = "all", cycle: str = "all", limit: int = 50):
    """Dreams from soul_v3.daily_dreams — narrative continuity (Dream Cycle SOUL v1.0 §5)."""
    _ALLOWED_AGENTS = {"ADA", "JARVIS", "ALICE", "NEXUS", "FABLE", "DUM"}
    _ALLOWED_CYCLES = {"morning", "midday", "evening", "nocturnal"}
    limit = min(max(1, limit), 200)

    where, params = ["1=1"], []
    if agent != "all":
        if agent.upper() not in _ALLOWED_AGENTS:
            return {"dreams": [], "error": f"unknown agent: {agent}"}
        params.append(agent.upper())
        where.append(f"agent = ${len(params)}")
    if cycle != "all":
        if cycle not in _ALLOWED_CYCLES:
            return {"dreams": [], "error": f"unknown cycle: {cycle}"}
        params.append(cycle)
        where.append(f"cycle = ${len(params)}")
    params.append(limit)

    def _parse(v):
        if v is None:
            return None
        if isinstance(v, (list, dict)):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    try:
        rows = await _db_query(
            f"""SELECT id, agent, date, cycle, dream_narrative, key_events,
                       emotional_arc, learnings, pending_threads, model_used,
                       inject_to_prompt, created_at
                FROM soul_v3.daily_dreams
                WHERE {' AND '.join(where)}
                ORDER BY date DESC, created_at DESC
                LIMIT ${len(params)}""",
            *params)
        return {
            "dreams": [
                {
                    "id": r["id"],
                    "agent": r["agent"],
                    "date": str(r["date"]) if r["date"] else None,
                    "cycle": r["cycle"],
                    "narrative": r["dream_narrative"],
                    "key_events": _parse(r["key_events"]) or [],
                    "emotional_arc": _parse(r["emotional_arc"]) or {},
                    "learnings": _parse(r["learnings"]) or [],
                    "pending_threads": _parse(r["pending_threads"]) or [],
                    "model": r["model_used"],
                    "inject_to_prompt": r["inject_to_prompt"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
            "count": len(rows),
        }
    except Exception as e:
        return {"dreams": [], "error": str(e)}


# ── LLM Routing (SOUL v1.0 §4) ───────────────────────────────────────────────

_ROUTING_ROLES = {"reasoning", "agentic", "coding", "summary"}
_ROUTING_PROVIDERS = {"ollama", "anthropic", "openai", "mistral", "google", "openrouter", "custom"}

class RoutingRow(BaseModel):
    role: str
    provider: str
    model: str
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    enabled: bool = True

class RoutingUpdate(BaseModel):
    agent: str = "DEFAULT"
    rows: list[RoutingRow]

@app.get("/api/soul/llm-routing")
async def llm_routing_get(agent: str = "DEFAULT"):
    """Get LLM routing config for an agent (spec §4)."""
    try:
        rows = await _db_query(
            "SELECT role, provider, model, fallback_provider, fallback_model, enabled "
            "FROM soul_v3.llm_routing WHERE agent = $1 ORDER BY role", agent)
        if not rows:
            rows = await _db_query(
                "SELECT role, provider, model, fallback_provider, fallback_model, enabled "
                "FROM soul_v3.llm_routing WHERE agent = 'DEFAULT' ORDER BY role")
        return {
            "agent": agent,
            "rows": [dict(r) for r in rows],
        }
    except Exception as e:
        return {"agent": agent, "rows": [], "error": str(e)}

@app.patch("/api/soul/llm-routing")
async def llm_routing_update(req: RoutingUpdate):
    """Upsert LLM routing config for an agent (spec §4)."""
    for row in req.rows:
        if row.role not in _ROUTING_ROLES:
            return {"ok": False, "error": f"invalid role: {row.role}"}
        if row.provider not in _ROUTING_PROVIDERS:
            return {"ok": False, "error": f"invalid provider: {row.provider}"}
    try:
        import asyncpg
        conn = await asyncpg.connect(DB_URL)
        try:
            for row in req.rows:
                await conn.execute("""
                    INSERT INTO soul_v3.llm_routing
                        (agent, role, provider, model, fallback_provider, fallback_model, enabled, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,NOW())
                    ON CONFLICT (agent, role) DO UPDATE SET
                        provider=EXCLUDED.provider, model=EXCLUDED.model,
                        fallback_provider=EXCLUDED.fallback_provider,
                        fallback_model=EXCLUDED.fallback_model,
                        enabled=EXCLUDED.enabled, updated_at=NOW()
                """, req.agent, row.role, row.provider, row.model,
                    row.fallback_provider, row.fallback_model, row.enabled)
        finally:
            await conn.close()
        return {"ok": True, "agent": req.agent, "updated": len(req.rows)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Agent Capabilities (SOUL v1.0 §3 — 13 toggles) ──────────────────────────

_CAP_AGENTS = {"ADA", "JARVIS", "ALICE", "NEXUS", "FABLE", "DUM"}
_CAP_COLUMNS = [
    "cap_shell_commands", "cap_git", "cap_read_files", "cap_write_files",
    "cap_screen_capture", "cap_camera", "cap_web_search", "cap_browser_control",
    "cap_memory_read", "cap_memory_write", "cap_cron_jobs", "cap_notifications",
    "cap_channel_read",
]

class CapabilitiesUpdate(BaseModel):
    agent: str
    capabilities: dict  # key → bool

@app.get("/api/soul/capabilities")
async def capabilities_get(agent: str = "JARVIS"):
    """Get capability toggles for an agent (spec §3)."""
    if agent.upper() not in _CAP_AGENTS:
        return {"error": f"unknown agent: {agent}"}
    try:
        rows = await _db_query(
            f"SELECT {', '.join(_CAP_COLUMNS)}, updated_at "
            "FROM soul_v3.agent_capabilities WHERE agent = $1", agent.upper())
        if not rows:
            return {"agent": agent, "capabilities": {c: False for c in _CAP_COLUMNS}}
        r = rows[0]
        return {
            "agent": agent.upper(),
            "capabilities": {c: bool(r[c]) for c in _CAP_COLUMNS},
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
    except Exception as e:
        return {"agent": agent, "capabilities": {}, "error": str(e)}

@app.patch("/api/soul/capabilities")
async def capabilities_update(req: CapabilitiesUpdate):
    """Update capability toggles for an agent."""
    if req.agent.upper() not in _CAP_AGENTS:
        return {"ok": False, "error": f"unknown agent: {req.agent}"}
    # only allow known capability columns
    safe = {k: bool(v) for k, v in req.capabilities.items() if k in _CAP_COLUMNS}
    if not safe:
        return {"ok": False, "error": "no valid capability keys"}
    try:
        import asyncpg
        conn = await asyncpg.connect(DB_URL)
        try:
            sets = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(safe))
            vals = [req.agent.upper()] + list(safe.values())
            await conn.execute(
                f"UPDATE soul_v3.agent_capabilities SET {sets}, updated_at=NOW() WHERE agent = $1",
                *vals)
            if conn.get_statusmsg() == "UPDATE 0":
                col_names = ", ".join(safe.keys())
                col_vals = ", ".join(f"${i+2}" for i in range(len(safe)))
                await conn.execute(
                    f"INSERT INTO soul_v3.agent_capabilities (agent, {col_names}) VALUES ($1, {col_vals}) "
                    "ON CONFLICT (agent) DO NOTHING", *vals)
        finally:
            await conn.close()
        return {"ok": True, "agent": req.agent.upper(), "updated": list(safe.keys())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════
# SOUL — Companion audit log (S6B, spec §11)
# ══════════════════════════════════════════════════════════

_AUDIT_ALLOWED_AGENTS = {"ADA", "JARVIS", "ALICE", "NEXUS", "FABLE", "DUM", "USER"}


@app.get("/api/soul/audit-log")
async def soul_audit_log(
    agent: Optional[str] = None,
    action: Optional[str] = None,
    processed_locally: Optional[bool] = None,
    limit: int = 100,
):
    """Read-only proxy to soul_v3.companion_audit_log (S6B, spec §11).

    Lists each invocation: agent, channel, action, target_id, metadata,
    processed_locally (flag for "stayed in local LLM" vs "left to cloud"),
    provider_used.

    Whitelists agent. limit clamped to [1, 500].
    """
    if agent and agent.upper() not in _AUDIT_ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail="invalid agent")
    limit = max(1, min(int(limit), 500))

    where: list[str] = []
    params: list = []
    idx = 1
    if agent:
        where.append(f"agent = ${idx}")
        params.append(agent.upper())
        idx += 1
    if action:
        where.append(f"action ILIKE ${idx}")
        params.append(f"%{action}%")
        idx += 1
    if processed_locally is not None:
        where.append(f"processed_locally = ${idx}")
        params.append(processed_locally)
        idx += 1
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = (
        "SELECT id, agent, channel, action, target_id, metadata, "
        "processed_locally, provider_used, created_at "
        f"FROM soul_v3.companion_audit_log {where_sql} "
        f"ORDER BY created_at DESC LIMIT ${idx}"
    )
    params.append(limit)

    try:
        rows = await _db_query(sql, *params)
        entries = []
        for r in rows:
            d = dict(r)
            if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
                d["created_at"] = d["created_at"].isoformat()
            md = d.get("metadata")
            if isinstance(md, str):
                try:
                    d["metadata"] = json.loads(md)
                except Exception:
                    pass
            entries.append(d)
        local_count = sum(1 for e in entries if e.get("processed_locally"))
        return {
            "ok": True,
            "entries": entries,
            "count": len(entries),
            "stats": {
                "local": local_count,
                "egress": len(entries) - local_count,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e), "entries": []}


# ══════════════════════════════════════════════════════════
# BYOK VAULT (S6A — spec §4.4 + §11)
# ══════════════════════════════════════════════════════════
# Encrypted storage for user-provided LLM API keys.
# Crypto: AES-GCM 256, master key in OS keyring (libsecret/Keychain).
# Endpoints expose ONLY provider names, NEVER key values, except internal
# router (separate module) that reads keys at LLM call time.

try:
    import byok_vault  # local module
    _BYOK_AVAILABLE = True
except Exception as _e:
    print(f"[byok_vault] disabled: {_e}")
    byok_vault = None  # type: ignore
    _BYOK_AVAILABLE = False


class BYOKSaveBody(BaseModel):
    provider: str
    api_key: str


async def _audit_byok_op(action: str, provider: str, success: bool, error: str | None = None) -> None:
    """Write entry to companion_audit_log so user sees vault activity."""
    import asyncpg
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            await conn.execute(
                """INSERT INTO soul_v3.companion_audit_log
                   (agent, channel, action, target_id, metadata, processed_locally, provider_used)
                   VALUES ('USER', 'settings', $1, $2, $3, TRUE, $4)""",
                action, provider,
                json.dumps({"success": success, "error": error} if error else {"success": success}),
                provider,
            )
        finally:
            await conn.close()
    except Exception:
        pass  # Audit failure must not block vault op


@app.get("/api/companion/byok-key/status")
async def byok_status():
    """Returns vault status WITHOUT exposing key values.

    Response:
      vault_exists, providers_configured (list of names only),
      keyring_available, master_key_source, allowed_providers.
    """
    if not _BYOK_AVAILABLE:
        return {"ok": False, "error": "byok_vault module unavailable"}
    try:
        return {"ok": True, **byok_vault.status()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/companion/byok-key")
async def byok_save(body: BYOKSaveBody):
    """Persist an API key for a provider (encrypted at rest).

    Body: {provider, api_key}
    Provider must be in ALLOWED_PROVIDERS whitelist.
    api_key must be >=8 chars.
    Returns success bool. NEVER echoes the api_key back.
    """
    if not _BYOK_AVAILABLE:
        raise HTTPException(status_code=503, detail="vault unavailable")
    try:
        byok_vault.save_key(body.provider, body.api_key)
        await _audit_byok_op("byok_save", body.provider, True)
        return {"ok": True, "provider": body.provider.lower().strip()}
    except byok_vault.VaultError as e:
        await _audit_byok_op("byok_save", body.provider, False, str(e))
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/companion/byok-key/{provider}")
async def byok_delete(provider: str):
    """Remove API key for provider."""
    if not _BYOK_AVAILABLE:
        raise HTTPException(status_code=503, detail="vault unavailable")
    try:
        removed = byok_vault.delete_key(provider)
        await _audit_byok_op("byok_delete", provider, removed)
        return {"ok": True, "removed": removed, "provider": provider.lower().strip()}
    except Exception as e:
        await _audit_byok_op("byok_delete", provider, False, str(e))
        raise HTTPException(status_code=400, detail=str(e))


# ══════════════════════════════════════════════════════════
# SYSTEM API
# ══════════════════════════════════════════════════════════

@app.get("/api/system/gpu")
async def system_gpu():
    """Get GPU status via nvidia-smi."""
    def parse_int(value: str) -> int | None:
        value = value.strip()
        if value in {"", "[N/A]", "N/A", "No devices were found"}:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
            parts = [part.strip() for part in line.split(",")]
            return {
                "temperature": parse_int(parts[0]) if len(parts) > 0 else None,
                "utilization": parse_int(parts[1]) if len(parts) > 1 else None,
                "memory_used_mb": parse_int(parts[2]) if len(parts) > 2 else None,
                "memory_total_mb": parse_int(parts[3]) if len(parts) > 3 else None,
                "name": parts[4] if len(parts) > 4 else "unknown",
            }
        return {"error": result.stderr.strip() or f"nvidia-smi exited {result.returncode}"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/system/health")
async def system_health():
    """Check health of all services."""
    services = {}

    # PostgreSQL (SOUL)
    try:
        await _db_query("SELECT 1")
        services["postgresql"] = {"status": "up", "port": 5433}
    except Exception as e:
        services["postgresql"] = {"status": "down", "error": str(e)}

    # Web chat bridge
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get("http://localhost:8765/api/health")
            services["web_chat"] = {"status": "up" if r.status_code == 200 else "degraded", "port": 8765}
    except Exception:
        services["web_chat"] = {"status": "down", "port": 8765}

    # GPU
    gpu = await system_gpu()
    services["gpu"] = {"status": "up" if "temperature" in gpu else "unknown", **gpu}

    return {"services": services, "timestamp": datetime.now(timezone.utc).isoformat()}


# ══════════════════════════════════════════════════════════
# WEBSOCKET — TERMINAL PTY
# ══════════════════════════════════════════════════════════

@app.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket):
    """WebSocket PTY for xterm.js — full terminal in browser."""
    user = await _canonical_session_from_headers(websocket.headers)
    if not user:
        await websocket.close(code=4401, reason="authentication required")
        return
    if str(user.get("role") or "").lower() != "superuser":
        await websocket.close(code=4403, reason="superuser required")
        return
    await websocket.accept()

    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["/bin/bash", "-l"],
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        preexec_fn=os.setsid,
        cwd=str(PROJECT_ROOT),
    )
    os.close(slave_fd)

    async def read_pty():
        loop = asyncio.get_event_loop()
        while True:
            try:
                data = await loop.run_in_executor(None, lambda: os.read(master_fd, 4096))
                if not data:
                    break
                await websocket.send_text(data.decode("utf-8", errors="replace"))
            except (OSError, WebSocketDisconnect):
                break

    reader_task = asyncio.create_task(read_pty())

    try:
        while True:
            data = await websocket.receive_text()
            if data.startswith("\x1b[8;"):
                # Resize: \x1b[8;rows;colst
                parts = data[4:].rstrip("t").split(";")
                if len(parts) == 2:
                    rows, cols = int(parts[0]), int(parts[1])
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                continue
            os.write(master_fd, data.encode("utf-8"))
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        proc.terminate()
        os.close(master_fd)


# ══════════════════════════════════════════════════════════
# WEBSOCKET — TEAM PRESENCE
# ══════════════════════════════════════════════════════════

team_connections: list[WebSocket] = []

@app.websocket("/ws/team")
async def ws_team(websocket: WebSocket):
    """WebSocket for realtime team status updates."""
    user = await _canonical_session_from_headers(websocket.headers)
    if not user:
        await websocket.close(code=4401, reason="authentication required")
        return
    if str(user.get("role") or "").lower() not in ("admin", "superuser"):
        await websocket.close(code=4403, reason="admin access required")
        return
    await websocket.accept()
    team_connections.append(websocket)
    try:
        while True:
            status = await team_status()
            await websocket.send_json(status)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        team_connections.remove(websocket)


# ══════════════════════════════════════════════════════════
# API v1 — contrato versionado ADITIVO (gateway MCP + SSE). JARVIS 2026-06-02.
# No toca las rutas /api/* existentes; agrega /v1/*. Ver api_v1.py + mcp_gateway.py.
import api_v1
app.include_router(api_v1.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8800)
