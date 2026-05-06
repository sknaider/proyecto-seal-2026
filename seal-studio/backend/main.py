#!/usr/bin/env python3
"""
SEAL Studio — Backend API
=========================
FastAPI server bridging the frontend to SEAL Runtime, SOUL, and team services.
Designed by JARVIS, built by Team SEAL.

Run: uvicorn main:app --host 0.0.0.0 --port 8800 --reload
"""

import asyncio
import json
import os
import subprocess
import pty
import select
import struct
import fcntl
import termios
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Config ──
PROJECT_ROOT = Path(os.environ.get("SEAL_PROJECT_ROOT", "/home/dadito/IA/proyecto-seal"))
MESSAGES_DIR = PROJECT_ROOT / "messages"
SCRATCHPAD_DIR = PROJECT_ROOT / "scratchpad"
UPLOAD_DIR = PROJECT_ROOT / "seal-studio" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

app = FastAPI(title="SEAL Studio", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|192\.168\.68\.\d{1,3}):(3000|3001|8800|8765|8790)$",
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


# ══════════════════════════════════════════════════════════
# FILES API
# ══════════════════════════════════════════════════════════

@app.get("/api/files/tree")
async def file_tree(path: str = str(PROJECT_ROOT), depth: int = 3):
    """Return directory tree as nested structure."""
    root = Path(path)
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
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "File not found")
    if not p.is_file():
        raise HTTPException(400, "Not a file")
    try:
        return {"path": str(p), "content": p.read_text(errors="replace"),
                "size": p.stat().st_size, "modified": p.stat().st_mtime}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/files/write")
async def file_write(req: FileWrite):
    """Write file content."""
    p = Path(req.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.content)
    return {"ok": True, "path": str(p), "size": len(req.content)}


@app.post("/api/files/upload")
async def file_upload(file: UploadFile = File(...)):
    """Upload file (images, PDFs, documents)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = f"{ts}_{file.filename}"
    dest = UPLOAD_DIR / safe_name
    content = await file.read()
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

    return {"agents": agents, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/agents/relaunch/{agent}")
async def relaunch_agent(agent: str):
    """Relaunch an agent via seal_relaunch.sh. Forces a fresh context_guard reset."""
    from fastapi import HTTPException
    valid = {"JARVIS", "ADA", "ALICE"}
    agent_upper = agent.upper()
    if agent_upper not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}. Valid: {', '.join(valid)}")
    relaunch_script = Path(__file__).parent.parent / "seal_relaunch.sh"
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
    # Block DM channels — privacy protection
    if channel.startswith("dm:") or channel.startswith("dm_"):
        return {"ok": False, "error": "DM access not allowed via studio proxy"}
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            base_sel = (
                "SELECT id, channel, sender_name, content, message_type, "
                "metadata->>'file_url' AS file_url, created_at "
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
            msg = dict(r)
            if msg.get("created_at") and hasattr(msg["created_at"], "isoformat"):
                msg["created_at"] = msg["created_at"].isoformat()
            messages.append(msg)
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
                "SELECT name, description FROM chat_channels WHERE is_private = FALSE ORDER BY name"
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


@app.get("/api/soul/ocean")
async def soul_ocean(agent: str = "ADA"):
    """Get current OCEAN scores for an agent."""
    try:
        rows = await _db_query(
            "SELECT ocean_scores, ocean_baseline, updated_at FROM identity WHERE agent = $1", agent)
        if not rows:
            return {"error": f"No identity for {agent}"}
        row = rows[0]
        return {
            "agent": agent,
            "ocean": json.loads(row["ocean_scores"]) if row["ocean_scores"] else {},
            "baseline": json.loads(row["ocean_baseline"]) if row["ocean_baseline"] else {},
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
                "SELECT ocean_scores, ocean_baseline, updated_at FROM identity WHERE agent = $1", agent)
            if rows:
                row = rows[0]
                results.append({
                    "agent": agent,
                    "ocean": json.loads(row["ocean_scores"]) if row["ocean_scores"] else {},
                    "baseline": json.loads(row["ocean_baseline"]) if row["ocean_baseline"] else {},
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                })
            else:
                results.append({"agent": agent, "ocean": {}, "baseline": {}, "updated_at": None, "error": "no data"})
        except Exception as e:
            results.append({"agent": agent, "ocean": {}, "baseline": {}, "updated_at": None, "error": str(e)})
    return {"agents": results}


@app.get("/api/soul/memories")
async def soul_memories(agent: str = "ADA", limit: int = 20, offset: int = 0,
                         category: Optional[str] = None, min_importance: int = 0):
    """Browse SOUL memories with filtering."""
    try:
        query = """
            SELECT id, agent, category, content, importance, valence, arousal,
                   memory_type, identity_defining, last_activation, relevance_score, created_at
            FROM memories
            WHERE agent = $1 AND importance >= $2
              AND (invalid_at IS NULL OR invalid_at > NOW())
        """
        args = [agent, min_importance]
        if category:
            query += f" AND category = ${len(args)+1}"
            args.append(category)
        query += f" ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}"
        args.extend([limit, offset])

        rows = await _db_query(query, *args)
        return {"memories": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/soul/drift")
async def soul_drift(agent: str = "ADA", hours: int = 24):
    """Get drift metrics for an agent."""
    try:
        rows = await _db_query("""
            SELECT drift_score, details, measured_at
            FROM drift_metrics
            WHERE agent = $1 AND measured_at > NOW() - INTERVAL '%s hours'
            ORDER BY measured_at DESC
        """ % hours, agent)
        return {"agent": agent, "drift_events": [dict(r) for r in rows],
                "total_drift": sum(float(r["drift_score"]) for r in rows if r["drift_score"])}
    except Exception as e:
        return {"error": str(e)}


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


# ══════════════════════════════════════════════════════════
# SYSTEM API
# ══════════════════════════════════════════════════════════

@app.get("/api/system/gpu")
async def system_gpu():
    """Get GPU status via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            return {
                "temperature": int(parts[0]),
                "utilization": int(parts[1]),
                "memory_used_mb": int(parts[2]),
                "memory_total_mb": int(parts[3]),
                "name": parts[4] if len(parts) > 4 else "unknown",
            }
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
# STARTUP
# ══════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    print(f"""
    ╔═══════════════════════════════════════╗
    ║         SEAL Studio Backend           ║
    ║         v0.1.0 — Team SEAL            ║
    ║                                       ║
    ║  API:  http://localhost:8800          ║
    ║  Docs: http://localhost:8800/docs     ║
    ╚═══════════════════════════════════════╝
    """)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8800)
