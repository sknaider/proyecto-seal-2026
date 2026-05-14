from contextlib import asynccontextmanager
from typing import Optional, Any
import json
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from companion_core.db import init_db, close_db, get_db
from companion_core.settings import toml_path
from companion_core.agent import get_reply, stream_claude
from companion_core.mcp_client import McpClient

VERSION = "0.3.0"

# In-memory config cache (persisted to companion_settings table)
_config_cache: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Load config from DB into cache
    db = get_db()
    rows = await db.execute_fetchall("SELECT key, value FROM companion_settings")
    for row in rows:
        try:
            _config_cache[row[0]] = json.loads(row[1])
        except (json.JSONDecodeError, TypeError):
            _config_cache[row[0]] = row[1]
    yield
    await close_db()


app = FastAPI(title="SEAL Companion Core", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    db = get_db()
    mem_count = await db.execute_fetchall("SELECT COUNT(*) FROM memories")
    msg_count = await db.execute_fetchall("SELECT COUNT(*) FROM conversations")
    srv_count = await db.execute_fetchall("SELECT COUNT(*) FROM mcp_servers")
    return {
        "status": "ok",
        "version": VERSION,
        "service": "companion_core",
        "stats": {
            "memories": mem_count[0][0] if mem_count else 0,
            "messages": msg_count[0][0] if msg_count else 0,
            "mcp_servers": srv_count[0][0] if srv_count else 0,
        },
    }


# ── System Prompt ─────────────────────────────────────────────────────────────

_DEFAULT_SYSTEM = "You are a personal AI companion. Be helpful, concise, and honest."

_SYSTEM_PROMPT_VARIABLES = ["user_name", "today_date", "user_goal"]


class SystemPromptPayload(BaseModel):
    template: str


@app.get("/api/companion/system-prompt")
async def get_system_prompt():
    template = _config_cache.get("system_prompt", _DEFAULT_SYSTEM)
    return {
        "template": template,
        "default": _DEFAULT_SYSTEM,
        "variables": _SYSTEM_PROMPT_VARIABLES,
    }


@app.patch("/api/companion/system-prompt")
async def set_system_prompt(payload: SystemPromptPayload):
    if not payload.template.strip():
        raise HTTPException(status_code=400, detail="template required")
    await _upsert_setting("system_prompt", payload.template.strip())
    return {"ok": True}


@app.get("/api/config")
async def get_config():
    return {
        "mode": "user-product",
        "version": VERSION,
        "service": "companion_core",
        "primary_agent": _config_cache.get("primary_agent", "USER"),
        "first_run_complete": _config_cache.get("first_run_complete", False),
        "name": _config_cache.get("name", ""),
    }


class FirstRunPayload(BaseModel):
    name: str = ""
    primary_agent: str = "USER"
    ocean: Optional[dict] = None


async def _upsert_setting(key: str, value: Any) -> None:
    db = get_db()
    serialized = json.dumps(value)
    await db.execute(
        "INSERT INTO companion_settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, serialized)
    )
    await db.commit()
    _config_cache[key] = value


@app.post("/api/companion/first-run")
async def first_run(payload: FirstRunPayload):
    await _upsert_setting("name", payload.name)
    await _upsert_setting("primary_agent", payload.primary_agent)
    await _upsert_setting("first_run_complete", True)
    if payload.ocean:
        await _upsert_setting("ocean", payload.ocean)
    return {"ok": True}


@app.get("/api/companion/config")
async def get_companion_config():
    return dict(_config_cache)


class ConfigPatch(BaseModel):
    name: Optional[str] = None
    primary_agent: Optional[str] = None
    ocean: Optional[dict] = None
    api_key: Optional[str] = None


@app.patch("/api/companion/config")
async def patch_companion_config(payload: ConfigPatch):
    updates = payload.model_dump(exclude_none=True)
    for key, value in updates.items():
        await _upsert_setting(key, value)
    return {"ok": True}


# ── Memories ──────────────────────────────────────────────────────────────────

class MemoryCreate(BaseModel):
    agent: str = "USER"
    category: Optional[str] = None
    content: str
    importance: int = 5


@app.post("/api/memories")
async def create_memory(payload: MemoryCreate):
    if not payload.content.strip():
        return JSONResponse(status_code=400, content={"error": "content required"})
    db = get_db()
    cur = await db.execute(
        "INSERT INTO memories (agent, category, content, importance) VALUES (?,?,?,?)",
        (payload.agent, payload.category, payload.content.strip(), payload.importance)
    )
    await db.commit()
    return {"ok": True, "id": cur.lastrowid}


@app.get("/api/memories")
async def get_memories(agent: str = "all", search: str = "", limit: int = 20):
    db = get_db()
    limit = min(limit, 200)
    if search.strip():
        if agent == "all":
            rows = await db.execute_fetchall(
                "SELECT m.id, m.agent, m.category, m.content, m.importance, m.created_at "
                "FROM memories m JOIN memories_fts f ON f.rowid=m.id "
                "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                (search.strip(), limit)
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT m.id, m.agent, m.category, m.content, m.importance, m.created_at "
                "FROM memories m JOIN memories_fts f ON f.rowid=m.id "
                "WHERE memories_fts MATCH ? AND m.agent=? ORDER BY rank LIMIT ?",
                (search.strip(), agent, limit)
            )
    else:
        if agent == "all":
            rows = await db.execute_fetchall(
                "SELECT id, agent, category, content, importance, created_at "
                "FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT id, agent, category, content, importance, created_at "
                "FROM memories WHERE agent=? ORDER BY id DESC LIMIT ?", (agent, limit)
            )
    memories = [
        {"id": r[0], "agent": r[1], "category": r[2], "content": r[3],
         "importance": r[4], "created_at": r[5]}
        for r in rows
    ]
    return {"memories": memories, "total": len(memories)}


@app.get("/api/memories/categories")
async def list_memory_categories():
    """List all distinct memory categories with count. Must be before /{memory_id}."""
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT category, COUNT(*) as cnt FROM memories "
        "WHERE category IS NOT NULL GROUP BY category ORDER BY cnt DESC"
    )
    categories = [{"category": r[0], "count": r[1]} for r in rows]
    return {"categories": categories, "total": len(categories)}


@app.get("/api/memories/{memory_id}")
async def get_memory(memory_id: int):
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT id, agent, category, content, importance, created_at FROM memories WHERE id=?",
        (memory_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="memory not found")
    r = rows[0]
    return {"id": r[0], "agent": r[1], "category": r[2], "content": r[3],
            "importance": r[4], "created_at": r[5]}


class MemoryUpdate(BaseModel):
    category: Optional[str] = None
    content: Optional[str] = None
    importance: Optional[int] = None


@app.patch("/api/memories/{memory_id}")
async def update_memory(memory_id: int, payload: MemoryUpdate):
    db = get_db()
    rows = await db.execute_fetchall("SELECT id FROM memories WHERE id=?", (memory_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="memory not found")
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True}
    if "content" in updates and not updates["content"].strip():
        raise HTTPException(status_code=400, detail="content cannot be empty")
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [memory_id]
    await db.execute(f"UPDATE memories SET {set_clause} WHERE id=?", values)
    await db.commit()
    return {"ok": True}


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: int):
    db = get_db()
    cur = await db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"ok": True}


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    thread_id: str
    content: str
    model: Optional[str] = None


@app.get("/api/chat/threads")
async def get_threads():
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT thread_id, COUNT(*) as msg_count, MAX(ts) as last_ts "
        "FROM conversations GROUP BY thread_id ORDER BY last_ts DESC LIMIT 50"
    )
    threads = [{"thread_id": r[0], "msg_count": r[1], "last_ts": r[2]} for r in rows]
    return {"threads": threads}


@app.get("/api/chat/threads/{thread_id}/messages")
async def get_thread_messages(thread_id: str, limit: int = 50):
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT id, role, content, model, ts FROM conversations "
        "WHERE thread_id=? ORDER BY ts ASC LIMIT ?",
        (thread_id, min(limit, 200))
    )
    messages = [{"id": r[0], "role": r[1], "content": r[2], "model": r[3], "ts": r[4]} for r in rows]
    return {"messages": messages}


@app.post("/api/chat")
async def post_chat(payload: ChatMessage):
    db = get_db()
    # Load thread history (last 20 turns for context)
    history_rows = await db.execute_fetchall(
        "SELECT role, content FROM conversations WHERE thread_id=? ORDER BY ts ASC LIMIT 40",
        (payload.thread_id,)
    )
    thread_history = [{"role": r[0], "content": r[1]} for r in history_rows]

    # Persist user message
    await db.execute(
        "INSERT INTO conversations (thread_id, role, content, model) VALUES (?,?,?,?)",
        (payload.thread_id, "user", payload.content, payload.model)
    )
    await db.commit()

    api_key = _config_cache.get("api_key") or None
    model = payload.model or _config_cache.get("default_model") or None
    user_name = _config_cache.get("name", "")

    # Load agent profile (OCEAN + emotional state)
    prof_rows = await db.execute_fetchall(
        "SELECT name, persona_description, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n, valence, arousal "
        "FROM agent_profile WHERE id=1"
    )
    if prof_rows:
        p = prof_rows[0]
        agent_name = p[0] or "Companion"
        persona_desc = p[1] or ""
        o, c, e, a, n = p[2], p[3], p[4], p[5], p[6]
        valence, arousal = p[7], p[8]
        personality_traits = _ocean_to_traits(o, c, e, a, n)
        emotional_state = _valence_arousal_to_label(valence, arousal)
    else:
        agent_name, persona_desc, personality_traits, emotional_state = "Companion", "", "", "calm"
        o, c, e, a, n = 0.7, 0.6, 0.5, 0.8, 0.2

    # Load user profile
    user_prof_rows = await db.execute_fetchall("SELECT key, value FROM user_profile")
    user_profile: dict = {}
    for r in user_prof_rows:
        try:
            user_profile[r[0]] = json.loads(r[1])
        except (json.JSONDecodeError, TypeError):
            user_profile[r[0]] = r[1]

    # Build enriched system prompt
    import datetime as _dt
    system_template = _config_cache.get("system_prompt", _DEFAULT_SYSTEM)
    base_prompt = (
        system_template
        .replace("{user_name}", user_name or "User")
        .replace("{today_date}", _dt.date.today().isoformat())
        .replace("{user_goal}", _config_cache.get("user_goal", ""))
    )
    # Inject OCEAN personality layer
    ocean_layer = f"\n\nYour name is {agent_name}. Your personality: {personality_traits}."
    if persona_desc:
        ocean_layer += f" {persona_desc}"
    ocean_layer += f" Your current emotional state: {emotional_state}."
    # Inject user profile if available
    if user_profile:
        user_layer_parts = []
        if "preferred_name" in user_profile:
            user_layer_parts.append(f"the user prefers to be called {user_profile['preferred_name']}")
        if "communication_style" in user_profile:
            user_layer_parts.append(f"communication style: {user_profile['communication_style']}")
        if "topics_of_interest" in user_profile:
            topics = user_profile["topics_of_interest"]
            if isinstance(topics, list):
                user_layer_parts.append(f"interests: {', '.join(topics)}")
        if user_layer_parts:
            ocean_layer += f" About the user: {'; '.join(user_layer_parts)}."
    system_prompt = base_prompt + ocean_layer

    # Load MCP tools from cache (enabled servers that have been discovered)
    mcp_rows = await db.execute_fetchall(
        "SELECT id, command, args, env, tools_cache FROM mcp_servers "
        "WHERE enabled=1 AND tools_cache IS NOT NULL"
    )
    # Build Anthropic-format tool list and tool→server lookup
    anthropic_tools: list[dict] = []
    tool_server_map: dict[str, dict] = {}
    for row in mcp_rows:
        srv_id, command, args_j, env_j, tools_j = row
        try:
            srv_tools = json.loads(tools_j)
        except (json.JSONDecodeError, TypeError):
            continue
        for t in srv_tools:
            anthropic_tools.append({
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {"type": "object", "properties": {}}),
            })
            tool_server_map[t["name"]] = {
                "command": command,
                "args": json.loads(args_j),
                "env": json.loads(env_j),
            }

    async def _tool_executor(tool_name: str, tool_input: dict):
        cfg = tool_server_map.get(tool_name)
        if not cfg:
            raise ValueError(f"Unknown tool: {tool_name}")
        mcp = McpClient(cfg["command"], cfg["args"], cfg["env"])
        try:
            await mcp.start(timeout=15)
            return await mcp.call_tool(tool_name, tool_input, timeout=30)
        finally:
            await mcp.close()

    reply = await get_reply(
        thread_history, payload.content, api_key, model, user_name,
        tools=anthropic_tools or None,
        tool_executor=_tool_executor if anthropic_tools else None,
        system=system_prompt,
    )

    # Persist assistant reply
    await db.execute(
        "INSERT INTO conversations (thread_id, role, content, model) VALUES (?,?,?,?)",
        (payload.thread_id, "assistant", reply, payload.model)
    )
    # Update emotional state based on the conversation (infer from combined content)
    new_v, new_a = _infer_emotion_from_text(payload.content + " " + reply, n)
    await db.execute(
        "UPDATE agent_profile SET valence=?, arousal=?, updated_at=datetime('now') WHERE id=1",
        (new_v, new_a)
    )
    await db.commit()
    new_emotion = _valence_arousal_to_label(new_v, new_a)
    return {"reply": reply, "thread_id": payload.thread_id, "emotional_state": new_emotion}


@app.get("/api/chat/threads/{thread_id}/export")
async def export_thread_markdown(thread_id: str):
    """Export a conversation thread as Markdown text."""
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT role, content, ts FROM conversations WHERE thread_id=? ORDER BY ts ASC",
        (thread_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="thread not found")
    lines = [f"# Conversation: {thread_id}\n"]
    for role, content, ts in rows:
        label = "**You**" if role == "user" else "**Assistant**"
        lines.append(f"### {label}  \n_{ts}_\n\n{content}\n")
    return {"thread_id": thread_id, "markdown": "\n---\n".join(lines), "message_count": len(rows)}


@app.delete("/api/chat/threads/{thread_id}")
async def delete_thread(thread_id: str):
    db = get_db()
    cur = await db.execute("DELETE FROM conversations WHERE thread_id=?", (thread_id,))
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="thread not found")
    return {"ok": True, "deleted": cur.rowcount}


@app.get("/api/chat/search")
async def search_conversations(q: str, limit: int = 20):
    if not q.strip():
        raise HTTPException(status_code=400, detail="q required")
    db = get_db()
    limit = min(limit, 100)
    rows = await db.execute_fetchall(
        "SELECT thread_id, role, content, ts FROM conversations "
        "WHERE content LIKE ? ORDER BY ts DESC LIMIT ?",
        (f"%{q.strip()}%", limit)
    )
    results = [{"thread_id": r[0], "role": r[1], "content": r[2], "ts": r[3]} for r in rows]
    return {"results": results, "total": len(results)}


@app.post("/api/chat/stream")
async def post_chat_stream(payload: ChatMessage):
    """SSE streaming chat — yields text chunks as Claude responds."""
    db = get_db()
    history_rows = await db.execute_fetchall(
        "SELECT role, content FROM conversations WHERE thread_id=? ORDER BY ts ASC LIMIT 40",
        (payload.thread_id,)
    )
    thread_history = [{"role": r[0], "content": r[1]} for r in history_rows]

    await db.execute(
        "INSERT INTO conversations (thread_id, role, content, model) VALUES (?,?,?,?)",
        (payload.thread_id, "user", payload.content, payload.model)
    )
    await db.commit()

    api_key = _config_cache.get("api_key") or None
    model = payload.model or _config_cache.get("default_model") or None
    user_name = _config_cache.get("name", "")
    system = (
        f"You are a personal AI companion. The user's name is {user_name}."
        if user_name else "You are a personal AI companion."
    )
    messages = [*thread_history, {"role": "user", "content": payload.content}]
    chosen_model = model or "claude-haiku-4-5-20251001"

    if not api_key:
        # Non-streaming fallback
        reply = await get_reply(thread_history, payload.content, None, model, user_name)
        await db.execute(
            "INSERT INTO conversations (thread_id, role, content, model) VALUES (?,?,?,?)",
            (payload.thread_id, "assistant", reply, payload.model)
        )
        await db.commit()

        async def _stub_stream():
            yield f"data: {json.dumps({'text': reply})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(_stub_stream(), media_type="text/event-stream")

    collected: list[str] = []

    async def _claude_stream():
        try:
            async for chunk in stream_claude(messages, api_key, chosen_model, system):
                collected.append(chunk)
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            full_reply = "".join(collected)
            if full_reply:
                _db = get_db()
                await _db.execute(
                    "INSERT INTO conversations (thread_id, role, content, model) "
                    "VALUES (?,?,?,?)",
                    (payload.thread_id, "assistant", full_reply, payload.model)
                )
                await _db.commit()
            yield "data: [DONE]\n\n"

    return StreamingResponse(_claude_stream(), media_type="text/event-stream")


# ── MCP Servers ───────────────────────────────────────────────────────────────

class McpServerCreate(BaseModel):
    id: str
    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    enabled: bool = True


@app.get("/api/mcp/servers")
async def list_mcp_servers():
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT id, name, command, args, env, enabled, added_at FROM mcp_servers ORDER BY added_at ASC"
    )
    servers = [
        {
            "id": r[0], "name": r[1], "command": r[2],
            "args": json.loads(r[3]), "env": json.loads(r[4]),
            "enabled": bool(r[5]), "added_at": r[6],
        }
        for r in rows
    ]
    return {"servers": servers}


@app.post("/api/mcp/servers")
async def add_mcp_server(payload: McpServerCreate):
    if not payload.id.strip():
        raise HTTPException(status_code=400, detail="id required")
    if not payload.command.strip():
        raise HTTPException(status_code=400, detail="command required")
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="name required")
    db = get_db()
    try:
        await db.execute(
            "INSERT INTO mcp_servers (id, name, command, args, env, enabled) VALUES (?,?,?,?,?,?)",
            (payload.id.strip(), payload.name.strip(), payload.command.strip(),
             json.dumps(payload.args), json.dumps(payload.env), int(payload.enabled))
        )
        await db.commit()
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="server id already exists")
        raise
    return {"ok": True, "id": payload.id.strip()}


@app.patch("/api/mcp/servers/{server_id}")
async def toggle_mcp_server(server_id: str, enabled: bool):
    db = get_db()
    cur = await db.execute(
        "UPDATE mcp_servers SET enabled=? WHERE id=?", (int(enabled), server_id)
    )
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="server not found")
    return {"ok": True}


@app.delete("/api/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str):
    db = get_db()
    cur = await db.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,))
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="server not found")
    return {"ok": True}


# ── MCP Registry (popular pre-configured servers) ─────────────────────────

_MCP_REGISTRY: list[dict] = [
    {
        "id": "filesystem",
        "name": "Filesystem",
        "description": "Read, write, and navigate local files and directories",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "~"],
        "env": {},
        "category": "files",
        "requires_env": [],
    },
    {
        "id": "github",
        "name": "GitHub",
        "description": "Search repos, read files, manage issues and pull requests",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": ""},
        "category": "development",
        "requires_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    },
    {
        "id": "fetch",
        "name": "Web Fetch",
        "description": "Fetch and extract content from any web URL",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "env": {},
        "category": "web",
        "requires_env": [],
    },
    {
        "id": "sqlite",
        "name": "SQLite",
        "description": "Query and manage a local SQLite database",
        "command": "uvx",
        "args": ["mcp-server-sqlite", "--db-path", "~/companion.db"],
        "env": {},
        "category": "data",
        "requires_env": [],
    },
    {
        "id": "puppeteer",
        "name": "Puppeteer (Browser)",
        "description": "Control a browser — screenshot, click, fill forms",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "env": {},
        "category": "web",
        "requires_env": [],
    },
    {
        "id": "brave-search",
        "name": "Brave Search",
        "description": "Search the web using Brave Search API",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": ""},
        "category": "web",
        "requires_env": ["BRAVE_API_KEY"],
    },
    {
        "id": "memory",
        "name": "Memory (Knowledge Graph)",
        "description": "Persistent knowledge graph memory across conversations",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "env": {},
        "category": "memory",
        "requires_env": [],
    },
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "description": "Step-by-step reasoning and problem decomposition",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "env": {},
        "category": "reasoning",
        "requires_env": [],
    },
]


# ── Goals ─────────────────────────────────────────────────────────────────────

_VALID_STATUSES = {"active", "completed", "archived"}


class GoalCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: int = 5
    due_date: Optional[str] = None


class GoalUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
    due_date: Optional[str] = None


@app.post("/api/goals")
async def create_goal(payload: GoalCreate):
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="title required")
    db = get_db()
    cur = await db.execute(
        "INSERT INTO goals (title, description, priority, due_date) VALUES (?,?,?,?)",
        (payload.title.strip(), payload.description, payload.priority, payload.due_date)
    )
    await db.commit()
    return {"ok": True, "id": cur.lastrowid}


@app.get("/api/goals")
async def list_goals(status: str = "active", limit: int = 50):
    if status not in _VALID_STATUSES and status != "all":
        raise HTTPException(status_code=400, detail=f"invalid status, use: all, {', '.join(_VALID_STATUSES)}")
    db = get_db()
    limit = min(limit, 200)
    if status == "all":
        rows = await db.execute_fetchall(
            "SELECT id, title, description, status, priority, due_date, created_at, updated_at "
            "FROM goals ORDER BY priority DESC, created_at DESC LIMIT ?", (limit,)
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT id, title, description, status, priority, due_date, created_at, updated_at "
            "FROM goals WHERE status=? ORDER BY priority DESC, created_at DESC LIMIT ?",
            (status, limit)
        )
    goals = [
        {"id": r[0], "title": r[1], "description": r[2], "status": r[3],
         "priority": r[4], "due_date": r[5], "created_at": r[6], "updated_at": r[7]}
        for r in rows
    ]
    return {"goals": goals, "total": len(goals)}


@app.get("/api/goals/{goal_id}")
async def get_goal(goal_id: int):
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT id, title, description, status, priority, due_date, created_at, updated_at "
        "FROM goals WHERE id=?", (goal_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="goal not found")
    r = rows[0]
    return {"id": r[0], "title": r[1], "description": r[2], "status": r[3],
            "priority": r[4], "due_date": r[5], "created_at": r[6], "updated_at": r[7]}


@app.patch("/api/goals/{goal_id}")
async def update_goal(goal_id: int, payload: GoalUpdate):
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True}
    if "status" in updates and updates["status"] not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status")
    if "title" in updates and not updates["title"].strip():
        raise HTTPException(status_code=400, detail="title cannot be empty")
    db = get_db()
    rows = await db.execute_fetchall("SELECT id FROM goals WHERE id=?", (goal_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="goal not found")
    updates["updated_at"] = "datetime('now')"
    set_parts = [f"{k}=datetime('now')" if k == "updated_at" else f"{k}=?" for k in updates]
    values = [v for k, v in updates.items() if k != "updated_at"] + [goal_id]
    await db.execute(f"UPDATE goals SET {', '.join(set_parts)} WHERE id=?", values)
    await db.commit()
    return {"ok": True}


@app.delete("/api/goals/{goal_id}")
async def delete_goal(goal_id: int):
    db = get_db()
    cur = await db.execute("DELETE FROM goals WHERE id=?", (goal_id,))
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="goal not found")
    return {"ok": True}


# ── Agent Profile (OCEAN + emotional state) ───────────────────────────────────

_VALID_EMOTIONS = {
    # (valence, arousal) → label mapping
    # High valence, high arousal
    (True, True): "energetic",
    # High valence, low arousal
    (True, False): "calm",
    # Low valence, high arousal
    (False, True): "focused",
    # Low valence, low arousal
    (False, False): "reflective",
}

_EMOTION_KEYWORDS = {
    "curious":    (0.65, 0.60),
    "focused":    (0.45, 0.70),
    "calm":       (0.70, 0.30),
    "reflective": (0.40, 0.35),
    "energetic":  (0.80, 0.75),
    "satisfied":  (0.80, 0.45),
    "engaged":    (0.70, 0.55),
}


def _valence_arousal_to_label(valence: float, arousal: float) -> str:
    """Map Russell's circumplex coordinates to a named emotional state."""
    if valence >= 0.6 and arousal >= 0.55:
        return "energetic"
    if valence >= 0.6 and arousal < 0.55:
        return "calm" if arousal < 0.4 else "satisfied"
    if valence < 0.6 and arousal >= 0.55:
        return "focused"
    return "reflective"


def _ocean_to_traits(o: float, c: float, e: float, a: float, n: float) -> str:
    """Convert OCEAN scores to a natural-language personality description."""
    traits = []
    traits.append("curious and open-minded" if o > 0.65 else ("grounded and practical" if o < 0.4 else "balanced in curiosity"))
    traits.append("organized and thorough" if c > 0.65 else ("flexible and adaptive" if c < 0.4 else "reasonably structured"))
    traits.append("enthusiastic and engaging" if e > 0.65 else ("calm and reflective" if e < 0.4 else "measured in expression"))
    traits.append("warm and empathetic" if a > 0.65 else ("direct and objective" if a < 0.4 else "balanced in approach"))
    traits.append("emotionally stable and resilient" if n < 0.35 else ("emotionally expressive and responsive" if n > 0.65 else "emotionally aware"))
    return ", ".join(traits)


def _infer_emotion_from_text(text: str, ocean_n: float) -> tuple[float, float]:
    """Heuristic: update valence/arousal based on conversation keywords."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["interesting", "fascinating", "how does", "why", "curious", "wonder"]):
        v, a = 0.65, 0.60
    elif any(w in text_lower for w in ["solve", "analyze", "calculate", "error", "debug", "fix"]):
        v, a = 0.45, 0.70
    elif any(w in text_lower for w in ["great", "excellent", "perfect", "done", "complete", "thanks"]):
        v, a = 0.80, 0.45
    elif any(w in text_lower for w in ["think", "reflect", "consider", "perhaps", "maybe", "suggest"]):
        v, a = 0.50, 0.35
    else:
        v, a = 0.60, 0.45  # neutral/calm default
    # High neuroticism → amplify arousal slightly; low → dampen
    arousal_modifier = (ocean_n - 0.5) * 0.2
    return v, max(0.1, min(1.0, a + arousal_modifier))


class AgentProfileUpdate(BaseModel):
    name: Optional[str] = None
    persona_description: Optional[str] = None
    ocean_o: Optional[float] = None
    ocean_c: Optional[float] = None
    ocean_e: Optional[float] = None
    ocean_a: Optional[float] = None
    ocean_n: Optional[float] = None


class UserProfileUpdate(BaseModel):
    communication_style: Optional[str] = None
    topics_of_interest: Optional[list] = None
    preferred_name: Optional[str] = None
    notes: Optional[str] = None


@app.get("/api/agent/profile")
async def get_agent_profile():
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT name, persona_description, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n, "
        "valence, arousal, updated_at FROM agent_profile WHERE id=1"
    )
    if not rows:
        return {"name": "Companion", "ocean_o": 0.7, "ocean_c": 0.6, "ocean_e": 0.5,
                "ocean_a": 0.8, "ocean_n": 0.2, "valence": 0.6, "arousal": 0.4,
                "emotional_state": "calm", "persona_description": None}
    r = rows[0]
    o, c, e, a, n = r[2], r[3], r[4], r[5], r[6]
    valence, arousal = r[7], r[8]
    return {
        "name": r[0], "persona_description": r[1],
        "ocean_o": o, "ocean_c": c, "ocean_e": e, "ocean_a": a, "ocean_n": n,
        "valence": valence, "arousal": arousal,
        "emotional_state": _valence_arousal_to_label(valence, arousal),
        "personality_traits": _ocean_to_traits(o, c, e, a, n),
        "updated_at": r[9],
    }


@app.patch("/api/agent/profile")
async def update_agent_profile(payload: AgentProfileUpdate):
    db = get_db()
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True}
    # Clamp OCEAN values 0.0–1.0
    for key in ["ocean_o", "ocean_c", "ocean_e", "ocean_a", "ocean_n"]:
        if key in updates:
            updates[key] = max(0.0, min(1.0, float(updates[key])))
    updates["updated_at"] = "datetime('now')"
    set_parts = []
    values = []
    for k, v in updates.items():
        if k == "updated_at":
            set_parts.append(f"{k}=datetime('now')")
        else:
            set_parts.append(f"{k}=?")
            values.append(v)
    await db.execute(f"UPDATE agent_profile SET {', '.join(set_parts)} WHERE id=1", values)
    await db.commit()
    return {"ok": True}


@app.get("/api/agent/emotional-state")
async def get_emotional_state():
    db = get_db()
    rows = await db.execute_fetchall("SELECT valence, arousal FROM agent_profile WHERE id=1")
    if not rows:
        return {"emotional_state": "calm", "valence": 0.6, "arousal": 0.4}
    v, a = rows[0]
    return {"emotional_state": _valence_arousal_to_label(v, a), "valence": v, "arousal": a}


@app.get("/api/user/profile")
async def get_user_profile():
    db = get_db()
    rows = await db.execute_fetchall("SELECT key, value FROM user_profile")
    profile = {}
    for r in rows:
        try:
            profile[r[0]] = json.loads(r[1])
        except (json.JSONDecodeError, TypeError):
            profile[r[0]] = r[1]
    return {"profile": profile}


@app.patch("/api/user/profile")
async def update_user_profile(payload: UserProfileUpdate):
    db = get_db()
    updates = payload.model_dump(exclude_none=True)
    for k, v in updates.items():
        await db.execute(
            "INSERT INTO user_profile (key, value, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (k, json.dumps(v))
        )
    await db.commit()
    return {"ok": True, "updated": list(updates.keys())}


# ── Skills ────────────────────────────────────────────────────────────────────

class SkillCreate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_phrase: Optional[str] = None
    prompt_template: str
    category: Optional[str] = None
    enabled: bool = True


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_phrase: Optional[str] = None
    prompt_template: Optional[str] = None
    category: Optional[str] = None
    enabled: Optional[bool] = None


@app.post("/api/skills")
async def create_skill(payload: SkillCreate):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="name required")
    if not payload.prompt_template.strip():
        raise HTTPException(status_code=400, detail="prompt_template required")
    db = get_db()
    try:
        cur = await db.execute(
            "INSERT INTO skills (name, description, trigger_phrase, prompt_template, category, enabled) "
            "VALUES (?,?,?,?,?,?)",
            (payload.name.strip(), payload.description, payload.trigger_phrase,
             payload.prompt_template.strip(), payload.category, int(payload.enabled))
        )
        await db.commit()
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="skill name already exists")
        raise
    return {"ok": True, "id": cur.lastrowid}


@app.get("/api/skills")
async def list_skills(enabled_only: bool = False):
    db = get_db()
    if enabled_only:
        rows = await db.execute_fetchall(
            "SELECT id, name, description, trigger_phrase, prompt_template, category, "
            "enabled, use_count, created_at FROM skills WHERE enabled=1 ORDER BY use_count DESC"
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT id, name, description, trigger_phrase, prompt_template, category, "
            "enabled, use_count, created_at FROM skills ORDER BY use_count DESC"
        )
    skills = [
        {"id": r[0], "name": r[1], "description": r[2], "trigger_phrase": r[3],
         "prompt_template": r[4], "category": r[5], "enabled": bool(r[6]),
         "use_count": r[7], "created_at": r[8]}
        for r in rows
    ]
    return {"skills": skills, "total": len(skills)}


@app.post("/api/skills/{skill_id}/run")
async def run_skill(skill_id: int, variables: Optional[dict] = None):
    """Execute a skill — fills template variables and returns the prompt to send to chat."""
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT name, prompt_template, enabled FROM skills WHERE id=?", (skill_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="skill not found")
    name, template, enabled = rows[0]
    if not enabled:
        raise HTTPException(status_code=409, detail="skill disabled")
    # Fill variables
    prompt = template
    if variables:
        for k, v in variables.items():
            prompt = prompt.replace(f"{{{k}}}", str(v))
    # Increment use_count
    await db.execute("UPDATE skills SET use_count=use_count+1 WHERE id=?", (skill_id,))
    await db.commit()
    return {"ok": True, "skill": name, "prompt": prompt}


@app.patch("/api/skills/{skill_id}")
async def update_skill(skill_id: int, payload: SkillUpdate):
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True}
    db = get_db()
    rows = await db.execute_fetchall("SELECT id FROM skills WHERE id=?", (skill_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="skill not found")
    if "enabled" in updates:
        updates["enabled"] = int(updates["enabled"])
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [skill_id]
    await db.execute(f"UPDATE skills SET {set_clause} WHERE id=?", values)
    await db.commit()
    return {"ok": True}


@app.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: int):
    db = get_db()
    cur = await db.execute("DELETE FROM skills WHERE id=?", (skill_id,))
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="skill not found")
    return {"ok": True}


@app.get("/api/search")
async def unified_search(q: str, limit: int = 20):
    """Search across memories AND conversations in one shot."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="q required")
    db = get_db()
    limit = min(limit, 100)
    half = max(1, limit // 2)

    # Memories via FTS5
    mem_rows = await db.execute_fetchall(
        "SELECT m.id, m.content, m.category, m.importance, m.created_at "
        "FROM memories m JOIN memories_fts f ON f.rowid=m.id "
        "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
        (q.strip(), half)
    )
    memories = [
        {"type": "memory", "id": r[0], "content": r[1], "category": r[2],
         "importance": r[3], "ts": r[4]}
        for r in mem_rows
    ]

    # Conversations via LIKE (no FTS5 on conversations table)
    conv_rows = await db.execute_fetchall(
        "SELECT thread_id, role, content, ts FROM conversations "
        "WHERE content LIKE ? ORDER BY ts DESC LIMIT ?",
        (f"%{q.strip()}%", half)
    )
    convos = [
        {"type": "conversation", "thread_id": r[0], "role": r[1],
         "content": r[2], "ts": r[3]}
        for r in conv_rows
    ]

    results = memories + convos
    results.sort(key=lambda x: x.get("ts", "") or "", reverse=True)
    return {"results": results, "total": len(results), "q": q.strip()}


@app.get("/api/companion/stats")
async def companion_stats():
    """Overall user stats — memories, goals, conversations, MCP servers."""
    import datetime as _dt
    db = get_db()
    mem_total = (await db.execute_fetchall("SELECT COUNT(*) FROM memories"))[0][0]
    msg_total = (await db.execute_fetchall("SELECT COUNT(*) FROM conversations"))[0][0]
    thread_total = (await db.execute_fetchall(
        "SELECT COUNT(DISTINCT thread_id) FROM conversations"))[0][0]
    goal_active = (await db.execute_fetchall(
        "SELECT COUNT(*) FROM goals WHERE status='active'"))[0][0]
    goal_done = (await db.execute_fetchall(
        "SELECT COUNT(*) FROM goals WHERE status='completed'"))[0][0]
    mcp_enabled = (await db.execute_fetchall(
        "SELECT COUNT(*) FROM mcp_servers WHERE enabled=1"))[0][0]
    return {
        "memories": mem_total,
        "messages": msg_total,
        "threads": thread_total,
        "goals": {"active": goal_active, "completed": goal_done},
        "mcp_servers_enabled": mcp_enabled,
        "generated_at": _dt.datetime.now().isoformat(),
    }


@app.get("/api/companion/context")
async def companion_context():
    """Single-shot startup endpoint — returns everything the UI needs to bootstrap."""
    import datetime as _dt
    db = get_db()
    # Config
    user_name = _config_cache.get("name", "")
    api_key_set = bool(_config_cache.get("api_key"))
    default_model = _config_cache.get("default_model", "claude-haiku-4-5-20251001")
    first_run_complete = _config_cache.get("first_run_complete", False)
    # Agent profile
    prof_rows = await db.execute_fetchall(
        "SELECT name, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n, valence, arousal, persona_description "
        "FROM agent_profile WHERE id=1"
    )
    if prof_rows:
        p = prof_rows[0]
        o, c, e, a, n = p[1], p[2], p[3], p[4], p[5]
        v, ar = p[6], p[7]
        agent = {
            "name": p[0] or "Companion",
            "persona_description": p[8],
            "ocean": {"o": o, "c": c, "e": e, "a": a, "n": n},
            "emotional_state": _valence_arousal_to_label(v, ar),
            "valence": v, "arousal": ar,
            "personality_traits": _ocean_to_traits(o, c, e, a, n),
        }
    else:
        agent = {"name": "Companion", "emotional_state": "calm", "ocean": {}}
    # Quick counts
    mem_count = (await db.execute_fetchall("SELECT COUNT(*) FROM memories"))[0][0]
    goal_count = (await db.execute_fetchall(
        "SELECT COUNT(*) FROM goals WHERE status='active'"))[0][0]
    mcp_count = (await db.execute_fetchall(
        "SELECT COUNT(*) FROM mcp_servers WHERE enabled=1"))[0][0]
    skill_count = (await db.execute_fetchall("SELECT COUNT(*) FROM skills WHERE enabled=1"))[0][0]
    return {
        "user_name": user_name,
        "api_key_set": api_key_set,
        "default_model": default_model,
        "first_run_complete": first_run_complete,
        "agent": agent,
        "counts": {
            "memories": mem_count,
            "active_goals": goal_count,
            "mcp_servers": mcp_count,
            "skills": skill_count,
        },
        "version": VERSION,
        "generated_at": _dt.datetime.now().isoformat(),
    }


@app.post("/api/companion/briefing")
async def generate_briefing():
    """Generate a personalized daily briefing based on goals, memories, and agent state."""
    import datetime as _dt
    db = get_db()
    user_name = _config_cache.get("name", "there")
    today = _dt.date.today().strftime("%A, %B %d")

    # Load agent profile
    prof_rows = await db.execute_fetchall(
        "SELECT name, ocean_o, ocean_e, valence, arousal FROM agent_profile WHERE id=1"
    )
    agent_name = "Companion"
    emotion_label = "calm"
    if prof_rows:
        p = prof_rows[0]
        agent_name = p[0] or "Companion"
        emotion_label = _valence_arousal_to_label(p[3], p[4])

    # Active goals
    goals = await db.execute_fetchall(
        "SELECT title, priority, due_date FROM goals WHERE status='active' "
        "ORDER BY priority DESC LIMIT 5"
    )
    # Recent memories (last 24h)
    recent_mems = await db.execute_fetchall(
        "SELECT content FROM memories "
        "WHERE created_at >= datetime('now', '-1 day') "
        "ORDER BY importance DESC LIMIT 3"
    )

    # Greeting varies by emotional state
    _GREETINGS = {
        "energetic": f"Morning, {user_name}! Ready to tackle {today} together! ⚡",
        "focused":   f"Good morning, {user_name}. {today} — let's get to work.",
        "reflective": f"Good morning, {user_name}. Taking a moment to reflect on {today}.",
        "satisfied":  f"Good morning, {user_name}! Feeling good about {today}. ✨",
        "calm":       f"Good morning, {user_name}. Today is {today}.",
    }
    greeting = _GREETINGS.get(emotion_label, f"Good morning, {user_name}! Today is {today}.")
    lines = [greeting, ""]

    if goals:
        lines.append("**Your active goals:**")
        for g in goals:
            due = f" (due {g[2]})" if g[2] else ""
            lines.append(f"• {g[0]}{due}")
        lines.append("")

    if recent_mems:
        lines.append("**Recent context:**")
        for m in recent_mems:
            snippet = m[0][:100] + "…" if len(m[0]) > 100 else m[0]
            lines.append(f"• {snippet}")
        lines.append("")

    # MCP tools available
    mcp_count = (await db.execute_fetchall(
        "SELECT COUNT(*) FROM mcp_servers WHERE enabled=1"))[0][0]
    if mcp_count:
        lines.append(f"**{mcp_count} tool{'s' if mcp_count != 1 else ''} connected** and ready.")

    lines.append("\nWhat would you like to work on today?")

    return {
        "briefing": "\n".join(lines),
        "name": user_name,
        "agent_name": agent_name,
        "emotional_state": emotion_label,
        "date": today,
        "goal_count": len(goals),
        "memory_count": len(recent_mems),
    }


@app.get("/api/mcp/summary")
async def mcp_summary():
    """Dashboard summary: server count, enabled count, total cached tools."""
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT id, name, enabled, tools_cache FROM mcp_servers"
    )
    total_tools = 0
    enabled_count = 0
    servers_summary = []
    for row in rows:
        srv_id, name, enabled, tools_j = row
        tool_count = 0
        if tools_j:
            try:
                tool_count = len(json.loads(tools_j))
            except (json.JSONDecodeError, TypeError):
                pass
        total_tools += tool_count
        if enabled:
            enabled_count += 1
        servers_summary.append({
            "id": srv_id, "name": name,
            "enabled": bool(enabled), "tool_count": tool_count,
        })
    return {
        "total_servers": len(rows),
        "enabled_servers": enabled_count,
        "total_cached_tools": total_tools,
        "servers": servers_summary,
    }


class McpToolCall(BaseModel):
    tool_name: str
    arguments: dict = {}


@app.post("/api/mcp/servers/{server_id}/call")
async def call_mcp_tool_direct(server_id: str, payload: McpToolCall):
    """Directly call a tool on an MCP server — for Live MCP Debugger."""
    if not payload.tool_name.strip():
        raise HTTPException(status_code=400, detail="tool_name required")
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT command, args, env, enabled FROM mcp_servers WHERE id=?", (server_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="server not found")
    row = rows[0]
    if not row[3]:
        raise HTTPException(status_code=409, detail="server disabled")

    client = McpClient(row[0], json.loads(row[1]), json.loads(row[2]))
    try:
        await client.start(timeout=15)
        result = await client.call_tool(payload.tool_name, payload.arguments, timeout=30)
        return {"ok": True, "server_id": server_id, "tool": payload.tool_name, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Tool call failed: {exc}")
    finally:
        await client.close()


@app.get("/api/mcp/scaffold/template")
async def get_scaffold_template():
    """Return the MCP server scaffold template (Python, FastMCP style)."""
    return {
        "language": "python",
        "runtime": "fastmcp",
        "install": "pip install fastmcp",
        "template": '''#!/usr/bin/env python3
"""
{name} — MCP server generated by SEAL Companion
Run: python {filename}.py
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("{name}")


@mcp.tool()
def hello(message: str) -> str:
    """Say hello. Replace with your custom tool."""
    return f"Hello from {name}: {{message}}"


if __name__ == "__main__":
    mcp.run()
''',
    }


class ScaffoldRequest(BaseModel):
    name: str
    tools: list[dict] = []


@app.post("/api/mcp/scaffold")
async def scaffold_mcp_server(payload: ScaffoldRequest):
    """Generate a Python MCP server file from the provided spec."""
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="name required")

    safe_name = payload.name.strip().replace(" ", "_").replace("-", "_")
    filename = f"{safe_name.lower()}_mcp_server"

    tool_code_parts: list[str] = []
    for tool in payload.tools:
        tool_name = tool.get("name", "my_tool").replace("-", "_")
        tool_desc = tool.get("description", "Custom tool")
        params = tool.get("params", [])
        param_sig = ", ".join(f'{p["name"]}: str' for p in params) if params else "input: str"
        param_doc = "\n    ".join(f'Args:\n        {p["name"]}: {p.get("description", "")}' for p in params) if params else ""
        tool_code_parts.append(
            f'@mcp.tool()\ndef {tool_name}({param_sig}) -> str:\n'
            f'    """{tool_desc}{chr(10) + "    " + param_doc if param_doc else ""}"""\n'
            f'    # TODO: implement {tool_name}\n'
            f'    raise NotImplementedError("{tool_name} not yet implemented")\n'
        )

    tools_code = "\n\n".join(tool_code_parts) if tool_code_parts else (
        '@mcp.tool()\ndef hello(message: str) -> str:\n'
        '    """Say hello — replace with your custom tool."""\n'
        f'    return f"Hello from {safe_name}: {{message}}"\n'
    )

    code = (
        f'#!/usr/bin/env python3\n'
        f'"""\n{safe_name} MCP server — generated by SEAL Companion\n'
        f'Install: pip install fastmcp\nRun:     python {filename}.py\n"""\n'
        f'from mcp.server.fastmcp import FastMCP\n\n'
        f'mcp = FastMCP("{safe_name}")\n\n\n'
        f'{tools_code}\n\n'
        f'if __name__ == "__main__":\n    mcp.run()\n'
    )

    return {
        "filename": f"{filename}.py",
        "code": code,
        "install_cmd": "pip install fastmcp",
        "run_cmd": f"python {filename}.py",
    }


@app.get("/api/mcp/registry")
async def list_mcp_registry(category: str = ""):
    servers = _MCP_REGISTRY
    if category:
        servers = [s for s in servers if s["category"] == category]
    return {"servers": servers, "total": len(servers)}


@app.post("/api/mcp/registry/{registry_id}/install")
async def install_from_registry(registry_id: str):
    entry = next((s for s in _MCP_REGISTRY if s["id"] == registry_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="registry entry not found")
    db = get_db()
    try:
        await db.execute(
            "INSERT INTO mcp_servers (id, name, command, args, env, enabled) VALUES (?,?,?,?,?,1)",
            (entry["id"], entry["name"], entry["command"],
             json.dumps(entry["args"]), json.dumps(entry["env"]))
        )
        await db.commit()
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="server already installed")
        raise
    return {"ok": True, "id": registry_id, "name": entry["name"]}


class McpEnvPatch(BaseModel):
    env: dict[str, str]


@app.patch("/api/mcp/servers/{server_id}/env")
async def update_mcp_server_env(server_id: str, payload: McpEnvPatch):
    """Update environment variables for an MCP server (e.g. set API keys)."""
    db = get_db()
    rows = await db.execute_fetchall("SELECT env FROM mcp_servers WHERE id=?", (server_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="server not found")
    existing = json.loads(rows[0][0]) if rows[0][0] else {}
    merged = {**existing, **payload.env}
    await db.execute(
        "UPDATE mcp_servers SET env=? WHERE id=?", (json.dumps(merged), server_id)
    )
    await db.commit()
    return {"ok": True, "env_keys": list(merged.keys())}


@app.get("/api/mcp/servers/{server_id}/status")
async def check_mcp_server_status(server_id: str):
    """Ping an MCP server — verify it can initialize without error."""
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT command, args, env, enabled FROM mcp_servers WHERE id=?", (server_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="server not found")
    row = rows[0]
    if not row[3]:
        return {"server_id": server_id, "status": "disabled"}

    client = McpClient(row[0], json.loads(row[1]), json.loads(row[2]))
    try:
        await client.start(timeout=10)
        return {"server_id": server_id, "status": "ok"}
    except Exception as exc:
        return {"server_id": server_id, "status": "error", "detail": str(exc)}
    finally:
        await client.close()


@app.get("/api/mcp/servers/{server_id}/tools")
async def list_server_tools(server_id: str):
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT command, args, env, enabled FROM mcp_servers WHERE id=?", (server_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="server not found")
    row = rows[0]
    if not row[3]:
        raise HTTPException(status_code=409, detail="server disabled")

    client = McpClient(
        command=row[0],
        args=json.loads(row[1]),
        env=json.loads(row[2]),
    )
    try:
        await client.start(timeout=15)
        tools = await client.list_tools(timeout=10)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP server error: {exc}")
    finally:
        await client.close()

    # Persist tools cache so /api/chat can use them without re-launching the server
    await db.execute(
        "UPDATE mcp_servers SET tools_cache=? WHERE id=?",
        (json.dumps(tools), server_id)
    )
    await db.commit()

    return {"server_id": server_id, "tools": tools}


# ── Static UI serving (production) ───────────────────────────────────────────
# In dev: frontend runs on Vite :5174 with CORS.
# In production (.deb install): UI lives at /usr/share/seal-companion/ui
_UI_DIR = Path(os.environ.get("SEAL_UI_DIR", "/usr/share/seal-companion/ui"))

if _UI_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_UI_DIR / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str = ""):
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        index = _UI_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(status_code=404)
