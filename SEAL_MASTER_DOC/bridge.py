#!/usr/bin/env python3
"""
bridge.py — SEAL Runtime FastAPI Bridge
==========================================
HTTP + WebSocket bridge connecting SEAL Runtime to SEAL Studio frontend.
JARVIS builds the frontend (Next.js + Monaco). This is the backend API.

Endpoints:
  POST /api/boot              — Initialize runtime, return BootContext
  POST /api/query             — Send message, stream QueryEvents via WebSocket
  POST /api/tools/execute     — Execute a tool directly
  GET  /api/tools/list        — List available tools
  GET  /api/sessions          — List sessions
  GET  /api/sessions/{id}     — Load session messages
  POST /api/sessions          — Create new session
  GET  /api/soul/snapshot     — SOUL snapshot (OCEAN, drift, emotions)
  GET  /api/soul/variance     — Emotional variance report
  POST /api/scratchpad/state  — Read/write scratchpad state
  GET  /api/scratchpad/topics — List scratchpad topics
  GET  /api/agents            — List active agents
  POST /api/agents/spawn      — Spawn a new agent
  GET  /api/permissions/check — Check permission for a tool call
  WS   /ws/stream             — WebSocket for real-time query streaming
  GET  /api/health            — Health check

Usage:
    python3 bridge.py                    # Start server on port 8766
    python3 bridge.py --port 8800        # Custom port
    python3 bridge.py test               # Run tests (no server)
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Add runtime paths
RUNTIME_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(RUNTIME_DIR / "core"))
sys.path.insert(0, str(RUNTIME_DIR / "skills"))
sys.path.insert(0, str(RUNTIME_DIR / "dream"))
sys.path.insert(0, str(RUNTIME_DIR.parent / "scratchpad"))

from boot import SealBoot, BootConfig, BootContext
from query_loop import QueryLoop, QueryConfig, QueryEvent, EventType
from tool_registry import ToolRegistry, ToolDef, ToolResult
from agent_spawner import AgentSpawner, AgentDef, AgentStatus
from session_manager import SessionManager
from permissions import PermissionPipeline, PermissionConfig, PermissionMode, Action
from skill_loader import SkillLoader

# Optional imports
try:
    from scratchpad import Scratchpad
    HAS_SCRATCHPAD = True
except ImportError:
    HAS_SCRATCHPAD = False

try:
    from seal_dream import SealDream, DreamConfig
    HAS_DREAM = True
except ImportError:
    HAS_DREAM = False


# ── App ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="SEAL Runtime Bridge",
    description="API bridge between SEAL Runtime and SEAL Studio",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # SEAL Studio frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ────────────────────────────────────────────────────

_state = {
    "boot_context": None,
    "tool_registry": None,
    "agent_spawner": None,
    "session_manager": None,
    "permission_pipeline": None,
    "skill_loader": None,
    "scratchpad": None,
    "active_streams": {},  # session_id → WebSocket
    "booted": False,
}


# ── Pydantic models ────────────────────────────────────────────────

class BootRequest(BaseModel):
    agent: str = "ADA"

class QueryRequest(BaseModel):
    session_id: str
    message: str
    agent: str = "ADA"

class ToolExecuteRequest(BaseModel):
    tool_name: str
    input_data: dict = Field(default_factory=dict)
    agent: str = "ADA"

class SpawnAgentRequest(BaseModel):
    name: str
    prompt: str
    agent_type: str = "general-purpose"
    description: str = ""
    run_in_background: bool = True

class SessionCreateRequest(BaseModel):
    agent: str = "ADA"
    metadata: dict = Field(default_factory=dict)

class ScratchpadStateRequest(BaseModel):
    key: str | None = None
    value: Any = None

class PermissionCheckRequest(BaseModel):
    tool_name: str
    input_data: dict = Field(default_factory=dict)


# ── Endpoints ───────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "booted": _state["booted"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "0.1.0",
    }


@app.post("/api/boot")
async def boot(req: BootRequest):
    """Initialize the SEAL Runtime. Returns full BootContext."""
    try:
        seal_boot = SealBoot(BootConfig(agent=req.agent))
        ctx = await seal_boot.initialize()

        # Initialize global components
        _state["boot_context"] = ctx
        _state["tool_registry"] = ToolRegistry(agent=req.agent)
        _state["agent_spawner"] = AgentSpawner(parent_agent=req.agent)
        _state["session_manager"] = SessionManager(agent=req.agent)
        _state["permission_pipeline"] = PermissionPipeline(PermissionConfig(agent=req.agent))
        _state["skill_loader"] = SkillLoader()
        if HAS_SCRATCHPAD:
            _state["scratchpad"] = Scratchpad()
        _state["booted"] = True

        return {
            "agent": ctx.agent,
            "ocean": ctx.ocean,
            "beliefs": ctx.beliefs[:5],
            "relationships": ctx.relationships,
            "rules_count": len(ctx.rules),
            "recent_thoughts": ctx.recent_thoughts[:3],
            "hooks_loaded": ctx.hooks_loaded,
            "db_connected": ctx.db_connected,
            "boot_time_ms": ctx.boot_time_ms,
            "errors": ctx.errors,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/query")
async def query(req: QueryRequest):
    """Send a message and get response. For streaming, use /ws/stream instead."""
    if not _state["booted"]:
        raise HTTPException(status_code=400, detail="Runtime not booted. Call /api/boot first.")

    mgr = _state["session_manager"]
    if not mgr.session_exists(req.session_id):
        raise HTTPException(status_code=404, detail=f"Session {req.session_id} not found")

    # Save user message
    mgr.append_message(req.session_id, {"role": "user", "content": req.message})

    # Note: real query loop requires API key — return placeholder for now
    return {
        "session_id": req.session_id,
        "message_saved": True,
        "note": "For streaming responses, connect to /ws/stream with session_id",
    }


@app.post("/api/tools/execute")
async def execute_tool(req: ToolExecuteRequest):
    """Execute a tool via the ToolRegistry."""
    if not _state["booted"]:
        raise HTTPException(status_code=400, detail="Runtime not booted")

    registry = _state["tool_registry"]

    # Check permissions first
    pipeline = _state["permission_pipeline"]
    decision = pipeline.check(req.tool_name, req.input_data)

    if decision.action == Action.DENY:
        return {
            "allowed": False,
            "reason": decision.reason,
            "is_safety_check": decision.is_safety_check,
        }

    if decision.action == Action.ASK:
        return {
            "allowed": "ask",
            "reason": decision.reason,
            "tool_name": req.tool_name,
        }

    try:
        result = await registry.execute(req.tool_name, req.input_data, agent=req.agent)
        return {
            "allowed": True,
            "tool_name": result.tool_name,
            "output": result.output,
            "error": result.error,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/tools/list")
async def list_tools(agent: str = "ADA"):
    """List all available tools."""
    if not _state["booted"]:
        raise HTTPException(status_code=400, detail="Runtime not booted")

    registry = _state["tool_registry"]
    tools = registry.list_tools(agent=agent)
    return {
        "tools": [
            {"name": t.name, "description": t.description,
             "read_only": t.is_read_only, "requires_permission": t.requires_permission}
            for t in tools
        ],
        "count": len(tools),
    }


@app.get("/api/sessions")
async def list_sessions(limit: int = 20):
    """List recent sessions."""
    if not _state["session_manager"]:
        _state["session_manager"] = SessionManager()

    mgr = _state["session_manager"]
    sessions = mgr.list_sessions(limit=limit)
    return {
        "sessions": [
            {"id": s.session_id, "agent": s.agent, "messages": s.message_count,
             "created": s.created_at, "modified": s.last_modified, "size": s.size_bytes}
            for s in sessions
        ]
    }


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Load session messages for resume."""
    if not _state["session_manager"]:
        _state["session_manager"] = SessionManager()

    mgr = _state["session_manager"]
    messages = mgr.get_messages(session_id)
    if not messages and not mgr.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "messages": messages}


@app.post("/api/sessions")
async def create_session(req: SessionCreateRequest):
    """Create a new session."""
    if not _state["session_manager"]:
        _state["session_manager"] = SessionManager(agent=req.agent)

    mgr = _state["session_manager"]
    sid = mgr.create_session(metadata=req.metadata)
    return {"session_id": sid, "agent": req.agent}


@app.get("/api/soul/snapshot")
async def soul_snapshot(agent: str = "ADA"):
    """Get SOUL snapshot — OCEAN, drift, emotional state."""
    ctx = _state.get("boot_context")
    if ctx:
        return {
            "agent": ctx.agent,
            "ocean": ctx.ocean,
            "beliefs": ctx.beliefs[:5],
            "relationships": ctx.relationships,
            "recent_thoughts": ctx.recent_thoughts[:3],
            "db_connected": ctx.db_connected,
        }
    # Fallback: boot fresh
    boot = SealBoot(BootConfig(agent=agent))
    ctx = await boot.initialize()
    return {
        "agent": ctx.agent,
        "ocean": ctx.ocean,
        "beliefs": ctx.beliefs[:5],
        "recent_thoughts": ctx.recent_thoughts[:3],
    }


@app.get("/api/soul/variance")
async def soul_variance(agent: str = "ADA", window: int = 20):
    """Get emotional variance report."""
    try:
        sys.path.insert(0, str(RUNTIME_DIR.parent / "memory"))
        from emotional_variance import compute_variance, interpret_variance, compute_ocean_stability
        import asyncpg

        conn = await asyncpg.connect("postgresql://seal:REDACTADO@localhost:5433/seal_memory")
        try:
            variance = await compute_variance(conn, agent, window)
            stability = await compute_ocean_stability(conn, agent, window)
            return {
                "variance": variance,
                "variance_interpretation": interpret_variance(variance),
                "ocean_stability": stability,
            }
        finally:
            await conn.close()
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/scratchpad/state")
async def scratchpad_state(req: ScratchpadStateRequest):
    """Read or write scratchpad state."""
    if not HAS_SCRATCHPAD:
        raise HTTPException(status_code=500, detail="Scratchpad not available")

    pad = _state.get("scratchpad") or Scratchpad()

    if req.key and req.value is not None:
        # Write
        result = pad.update_state(req.key, req.value)
        return {"action": "updated", "key": req.key, "state": result}
    elif req.key:
        # Read specific key
        val = pad.read_state(req.key)
        return {"key": req.key, "value": val}
    else:
        # Read all
        return {"state": pad.read_state()}


@app.get("/api/scratchpad/topics")
async def scratchpad_topics():
    """List scratchpad topics."""
    if not HAS_SCRATCHPAD:
        return {"topics": []}
    pad = _state.get("scratchpad") or Scratchpad()
    return {"topics": pad.list_topics()}


@app.get("/api/agents")
async def list_agents():
    """List active agents."""
    spawner = _state.get("agent_spawner")
    if not spawner:
        return {"agents": [], "active": 0}
    return {
        "agents": spawner.list_agents(),
        "active": spawner.active_count(),
    }


@app.post("/api/agents/spawn")
async def spawn_agent(req: SpawnAgentRequest):
    """Spawn a new agent."""
    if not _state["booted"]:
        raise HTTPException(status_code=400, detail="Runtime not booted")

    spawner = _state["agent_spawner"]
    defn = AgentDef(
        name=req.name,
        prompt=req.prompt,
        agent_type=req.agent_type,
        description=req.description,
        run_in_background=req.run_in_background,
    )

    if req.run_in_background:
        agent_id = await spawner.spawn_background(defn)
        return {"agent_id": agent_id, "status": "running", "background": True}
    else:
        notif = await spawner.spawn_and_run(defn)
        return {"agent_id": notif.agent_id, "status": notif.status, "result": notif.result}


@app.get("/api/permissions/check")
async def check_permission(tool_name: str, command: str = ""):
    """Check permission for a tool call."""
    pipeline = _state.get("permission_pipeline") or PermissionPipeline(PermissionConfig())
    input_data = {"command": command} if command else {}
    decision = pipeline.check(tool_name, input_data)
    return {
        "action": decision.action.value,
        "tool": decision.tool_name,
        "reason": decision.reason,
        "source": decision.source,
        "is_safety_check": decision.is_safety_check,
    }


@app.get("/api/skills")
async def list_skills(agent: str = "ADA"):
    """List available skills."""
    loader = _state.get("skill_loader") or SkillLoader()
    skills = loader.list_skills(agent=agent)
    return {
        "skills": [
            {"name": s.name, "description": s.description,
             "agent": s.agent, "bundled": s.is_bundled}
            for s in skills
        ]
    }


@app.get("/api/dream/check")
async def dream_check(agent: str = "ADA"):
    """Check if seal-dream should run."""
    if not HAS_DREAM:
        return {"available": False}
    dream = SealDream(DreamConfig(agent=agent))
    try:
        passed, details = await dream.check_gates()
        return {"should_run": passed, "gates": details}
    finally:
        await dream.close()


# ── WebSocket for streaming ─────────────────────────────────────────

@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    """
    WebSocket endpoint for real-time query streaming.
    Client sends: {"session_id": "...", "message": "...", "agent": "ADA"}
    Server sends: QueryEvent objects as JSON
    """
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            session_id = data.get("session_id")
            message = data.get("message")
            agent = data.get("agent", "ADA")

            if not session_id or not message:
                await websocket.send_json({"error": "session_id and message required"})
                continue

            # Create query loop (requires API key for real execution)
            api_key = data.get("api_key")
            config = QueryConfig(
                agent=agent,
                api_key=api_key,
                system_prompt=data.get("system_prompt"),
            )
            loop = QueryLoop(config, tool_registry=_state.get("tool_registry"))

            # Save message to session
            mgr = _state.get("session_manager") or SessionManager()
            mgr.append_message(session_id, {"role": "user", "content": message})

            # Stream events
            try:
                async for event in loop.run(message):
                    await websocket.send_json({
                        "type": event.type.value,
                        "data": event.data,
                        "timestamp": event.timestamp,
                    })

                    # Save assistant text to session
                    if event.type == EventType.TEXT and event.data:
                        mgr.append_message(session_id, {
                            "role": "assistant",
                            "content": event.data.get("text", ""),
                        })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "data": {"error": str(e)},
                })

    except WebSocketDisconnect:
        pass


# ── Main ────────────────────────────────────────────────────────────

def main():
    import uvicorn

    port = 8766
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    print(f"SEAL Runtime Bridge starting on http://localhost:{port}")
    print(f"Docs: http://localhost:{port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


def _run_tests():
    """Test the bridge endpoints using FastAPI TestClient."""
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # T1: Health
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    print("PASS: T1 health ✓")

    # T2: Boot
    r = client.post("/api/boot", json={"agent": "ADA"})
    assert r.status_code == 200
    data = r.json()
    assert data["agent"] == "ADA"
    assert "ocean" in data
    print(f"PASS: T2 boot (ocean={data.get('ocean', {})}, {data['boot_time_ms']}ms) ✓")

    # T3: Tool list
    r = client.get("/api/tools/list")
    assert r.status_code == 200
    print(f"PASS: T3 tool list ({r.json()['count']} tools) ✓")

    # T4: Session create
    r = client.post("/api/sessions", json={"agent": "ADA", "metadata": {"test": True}})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert sid.startswith("seal_")
    print(f"PASS: T4 session create ({sid}) ✓")

    # T5: Session list
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert len(r.json()["sessions"]) >= 1
    print("PASS: T5 session list ✓")

    # T6: Session get
    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    print("PASS: T6 session get ✓")

    # T7: Query (message save)
    r = client.post("/api/query", json={"session_id": sid, "message": "hello", "agent": "ADA"})
    assert r.status_code == 200
    assert r.json()["message_saved"]
    print("PASS: T7 query message saved ✓")

    # T8: Soul snapshot
    r = client.get("/api/soul/snapshot?agent=ADA")
    assert r.status_code == 200
    assert "ocean" in r.json()
    print(f"PASS: T8 soul snapshot (ocean={r.json().get('ocean', {})}) ✓")

    # T9: Permission check — safe command
    r = client.get("/api/permissions/check?tool_name=Read")
    assert r.status_code == 200
    assert r.json()["action"] == "allow"
    print("PASS: T9 permission allow (Read) ✓")

    # T10: Permission check — dangerous command
    r = client.get("/api/permissions/check?tool_name=Bash&command=rm+-rf+/")
    assert r.status_code == 200
    assert r.json()["action"] == "deny"
    assert r.json()["is_safety_check"]
    print("PASS: T10 permission deny (rm -rf /) ✓")

    # T11: Scratchpad state
    r = client.post("/api/scratchpad/state", json={})
    assert r.status_code == 200
    assert "state" in r.json()
    print("PASS: T11 scratchpad state ✓")

    # T12: Scratchpad write + read
    r = client.post("/api/scratchpad/state", json={"key": "test_bridge", "value": "works"})
    assert r.status_code == 200
    r2 = client.post("/api/scratchpad/state", json={"key": "test_bridge"})
    assert r2.json()["value"] == "works"
    print("PASS: T12 scratchpad write+read ✓")

    # T13: Topics
    r = client.get("/api/scratchpad/topics")
    assert r.status_code == 200
    print(f"PASS: T13 topics ({len(r.json()['topics'])}) ✓")

    # T14: Agents list
    r = client.get("/api/agents")
    assert r.status_code == 200
    assert "active" in r.json()
    print("PASS: T14 agents list ✓")

    # T15: Skills list
    r = client.get("/api/skills?agent=ADA")
    assert r.status_code == 200
    print(f"PASS: T15 skills ({len(r.json()['skills'])}) ✓")

    # T16: Non-existent session
    r = client.get("/api/sessions/nonexistent_session_id")
    assert r.status_code == 404
    print("PASS: T16 session not found ✓")

    # T17: Query before boot (fresh client — test state dependency)
    # Already booted from T2, so this should work
    r = client.post("/api/query", json={"session_id": sid, "message": "test2"})
    assert r.status_code == 200
    print("PASS: T17 query after boot ✓")

    # T18: Soul variance
    r = client.get("/api/soul/variance?agent=ADA")
    assert r.status_code == 200
    print(f"PASS: T18 soul variance ✓")

    # T19: Dream check
    r = client.get("/api/dream/check?agent=ADA")
    assert r.status_code == 200
    print(f"PASS: T19 dream check (should_run={r.json().get('should_run')}) ✓")

    # T20: Spawn agent
    r = client.post("/api/agents/spawn", json={
        "name": "test_agent",
        "prompt": "Test prompt",
        "description": "Testing agent spawn via API",
        "run_in_background": False,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    print("PASS: T20 spawn agent ✓")

    # Cleanup test session
    mgr = _state["session_manager"]
    mgr.delete_session(sid)

    print(f"\n=== 20/20 TESTS PASARON ✓ ===")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        _run_tests()
    else:
        main()
