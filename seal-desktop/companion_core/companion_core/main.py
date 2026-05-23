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
from companion_core import sub_agents as _sub_agents

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


# ── Sub-agents (v0.6 — OpenHuman absorption) ─────────────────────────────────


@app.get("/api/sub-agents")
async def list_sub_agents():
    """Catalog of specialized sub-agents available for routing."""
    return {"agents": _sub_agents.list_all(), "count": len(_sub_agents.ALL_SUB_AGENTS)}


class SubAgentInvoke(BaseModel):
    agent: str
    query: str
    user_goal: Optional[str] = None


@app.post("/api/sub-agents/invoke")
async def invoke_sub_agent(payload: SubAgentInvoke):
    """Invoke a sub-agent with full dynamic prompt context.

    Wires the LLM through the existing companion_core agent module so routing
    config (BYOK key, GEMMA 4 local fallback, etc.) is respected.
    """
    sa = _sub_agents.get(payload.agent)
    if sa is None:
        raise HTTPException(status_code=404, detail=f"sub-agent '{payload.agent}' not found")

    api_key = _config_cache.get("api_key")
    model = _config_cache.get("model")
    ocean = _config_cache.get("ocean")
    user_name = _config_cache.get("name", "")

    async def _llm(messages: list[dict], system: str) -> str:
        last = messages[-1]["content"] if messages else payload.query
        return await get_reply(
            thread_history=messages[:-1],
            new_content=last,
            api_key=api_key,
            model=model,
            user_name=user_name,
            system=system,
        )

    result = await _sub_agents.invoke_sub_agent(
        payload.agent,
        payload.query,
        call_llm=_llm,
        user_name=user_name,
        user_goal=payload.user_goal or "",
        ocean=ocean if isinstance(ocean, dict) else None,
    )
    return result


@app.get("/api/sub-agents/route")
async def suggest_sub_agent_route(query: str):
    """Heuristic suggestion of which sub-agent fits a query best."""
    return {"agent": _sub_agents.suggest_route(query), "query": query}


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


_AVATAR_DEFAULT = {
    "variant": "orb",
    "primary_color": "#a78bfa",
    "secondary_color": "#7c3aed",
    "accent_color": "#fb7185",
    "accessory": "none",
    "motion": "normal",
}
_AVATAR_VARIANTS = {"orb", "leaf", "spark"}
_AVATAR_ACCESSORIES = {"none", "halo", "headset", "badge"}
_AVATAR_MOTION = {"calm", "normal", "expressive"}


class AvatarProfileUpdate(BaseModel):
    variant: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    accessory: Optional[str] = None
    motion: Optional[str] = None


def _validate_hex_color(value: str, field: str) -> str:
    import re as _re_local
    if not _re_local.fullmatch(r"#[0-9a-fA-F]{6}", value or ""):
        raise HTTPException(status_code=400, detail=f"invalid {field}")
    return value.lower()


def _normalize_avatar_config(value: dict | None) -> dict:
    raw = {**_AVATAR_DEFAULT, **(value or {})}
    variant = str(raw.get("variant", "orb")).lower()
    accessory = str(raw.get("accessory", "none")).lower()
    motion = str(raw.get("motion", "normal")).lower()
    if variant not in _AVATAR_VARIANTS:
        variant = _AVATAR_DEFAULT["variant"]
    if accessory not in _AVATAR_ACCESSORIES:
        accessory = _AVATAR_DEFAULT["accessory"]
    if motion not in _AVATAR_MOTION:
        motion = _AVATAR_DEFAULT["motion"]
    return {
        "variant": variant,
        "primary_color": _validate_hex_color(str(raw.get("primary_color")), "primary_color"),
        "secondary_color": _validate_hex_color(str(raw.get("secondary_color")), "secondary_color"),
        "accent_color": _validate_hex_color(str(raw.get("accent_color")), "accent_color"),
        "accessory": accessory,
        "motion": motion,
    }


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


@app.get("/api/avatar/profile")
async def get_avatar_profile():
    cfg = _normalize_avatar_config(_config_cache.get("avatar_profile"))
    return {
        "ok": True,
        "avatar": cfg,
        "options": {
            "variants": sorted(_AVATAR_VARIANTS),
            "accessories": sorted(_AVATAR_ACCESSORIES),
            "motion": sorted(_AVATAR_MOTION),
        },
    }


@app.patch("/api/avatar/profile")
async def update_avatar_profile(payload: AvatarProfileUpdate):
    current = _normalize_avatar_config(_config_cache.get("avatar_profile"))
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True, "avatar": current}
    candidate = {**current, **updates}
    if "variant" in updates and str(candidate["variant"]).lower() not in _AVATAR_VARIANTS:
        raise HTTPException(status_code=400, detail="invalid variant")
    if "accessory" in updates and str(candidate["accessory"]).lower() not in _AVATAR_ACCESSORIES:
        raise HTTPException(status_code=400, detail="invalid accessory")
    if "motion" in updates and str(candidate["motion"]).lower() not in _AVATAR_MOTION:
        raise HTTPException(status_code=400, detail="invalid motion")
    avatar = _normalize_avatar_config(candidate)
    await _upsert_setting("avatar_profile", avatar)
    await _audit_log("USER", "avatar_profile_update", channel="settings",
                     target_id="avatar_profile", metadata={"updated": list(updates.keys())})
    return {"ok": True, "avatar": avatar, "updated": list(updates.keys())}


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


# ══════════════════════════════════════════════════════════════════════════════
# SPRINT 1 PORT — Sprint features adapted from PostgreSQL soul_v3 to SQLite local
# ══════════════════════════════════════════════════════════════════════════════
# All endpoints below operate on companion_core SQLite. They do NOT touch
# Soul App 2 backend at :8800. This makes SEAL App fully self-contained.

# ── BYOK Vault ────────────────────────────────────────────────────────────────

try:
    from companion_core import byok_vault  # local AES-GCM vault, OS keyring + keyfile fallback
    _BYOK_AVAILABLE = True
except Exception as _e:
    print(f"[byok_vault] disabled: {_e}")
    byok_vault = None  # type: ignore
    _BYOK_AVAILABLE = False


class BYOKSaveBody(BaseModel):
    provider: str
    api_key: str


async def _audit_log(
    agent: str,
    action: str,
    *,
    channel: Optional[str] = None,
    target_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    processed_locally: bool = True,
    provider_used: Optional[str] = None,
) -> None:
    """Append entry to companion_audit_log. Never raises."""
    try:
        db = get_db()
        await db.execute(
            """INSERT INTO companion_audit_log
               (agent, channel, action, target_id, metadata, processed_locally, provider_used)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                agent,
                channel,
                action,
                target_id,
                json.dumps(metadata) if metadata is not None else None,
                1 if processed_locally else 0,
                provider_used,
            ),
        )
        await db.commit()
    except Exception:
        pass


@app.get("/api/byok/status")
async def byok_status():
    """Vault status WITHOUT exposing key values."""
    if not _BYOK_AVAILABLE:
        return {"ok": False, "error": "byok_vault module unavailable"}
    try:
        return {"ok": True, **byok_vault.status()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/byok/key")
async def byok_save(body: BYOKSaveBody):
    """Persist an API key for a provider (encrypted at rest)."""
    if not _BYOK_AVAILABLE:
        raise HTTPException(status_code=503, detail="vault unavailable")
    try:
        byok_vault.save_key(body.provider, body.api_key)
        await _audit_log("USER", "byok_save", channel="settings",
                         target_id=body.provider.lower().strip(),
                         metadata={"success": True}, provider_used=body.provider)
        return {"ok": True, "provider": body.provider.lower().strip()}
    except byok_vault.VaultError as e:
        await _audit_log("USER", "byok_save", channel="settings",
                         target_id=body.provider,
                         metadata={"success": False, "error": str(e)}, provider_used=body.provider)
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/byok/key/{provider}")
async def byok_delete(provider: str):
    if not _BYOK_AVAILABLE:
        raise HTTPException(status_code=503, detail="vault unavailable")
    try:
        removed = byok_vault.delete_key(provider)
        await _audit_log("USER", "byok_delete", channel="settings",
                         target_id=provider.lower().strip(),
                         metadata={"removed": removed}, provider_used=provider)
        return {"ok": True, "removed": removed, "provider": provider.lower().strip()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Dreams ────────────────────────────────────────────────────────────────────

_ALLOWED_AGENTS = {"SOUL", "USER", "ADA", "JARVIS", "ALICE", "NEXUS", "DUM"}
_ALLOWED_CYCLES = {"morning", "midday", "evening", "nocturnal"}


def _decode_json(s):
    if s is None:
        return None
    if isinstance(s, (list, dict)):
        return s
    try:
        return json.loads(s)
    except Exception:
        return s


@app.get("/api/dreams")
async def list_dreams(
    agent: str = "all",
    cycle: str = "all",
    limit: int = 50,
):
    """List recent dreams from daily_dreams."""
    if agent != "all" and agent.upper() not in _ALLOWED_AGENTS:
        return {"dreams": [], "error": f"unknown agent: {agent}"}
    if cycle != "all" and cycle not in _ALLOWED_CYCLES:
        return {"dreams": [], "error": f"unknown cycle: {cycle}"}
    limit = max(1, min(int(limit), 200))

    db = get_db()
    where = ["1=1"]
    params: list = []
    if agent != "all":
        where.append("agent = ?")
        params.append(agent.upper())
    if cycle != "all":
        where.append("cycle = ?")
        params.append(cycle)
    params.append(limit)
    sql = (
        "SELECT id, agent, date, cycle, dream_narrative, key_events, "
        "emotional_arc, learnings, pending_threads, model_used, "
        "inject_to_prompt, created_at "
        "FROM daily_dreams WHERE " + " AND ".join(where) +
        " ORDER BY date DESC, created_at DESC LIMIT ?"
    )
    rows = await db.execute_fetchall(sql, tuple(params))
    return {
        "dreams": [
            {
                "id": r["id"],
                "agent": r["agent"],
                "date": r["date"],
                "cycle": r["cycle"],
                "narrative": r["dream_narrative"],
                "key_events": _decode_json(r["key_events"]) or [],
                "emotional_arc": _decode_json(r["emotional_arc"]) or {},
                "learnings": _decode_json(r["learnings"]) or [],
                "pending_threads": _decode_json(r["pending_threads"]) or [],
                "model": r["model_used"],
                "inject_to_prompt": bool(r["inject_to_prompt"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


# ── LLM Routing ───────────────────────────────────────────────────────────────

_ALLOWED_ROLES = {"reasoning", "agentic", "coding", "summary"}
_ALLOWED_PROVIDERS = {"ollama", "anthropic", "openai", "mistral", "google", "openrouter", "groq", "deepseek"}


class RoutingPatchBody(BaseModel):
    agent: str = "DEFAULT"
    role: str
    provider: str
    model: str
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    enabled: bool = True


@app.get("/api/llm-routing")
async def llm_routing_list(agent: str = "DEFAULT"):
    if agent != "DEFAULT" and agent.upper() not in _ALLOWED_AGENTS:
        return {"agent": agent, "rows": [], "error": "unknown agent"}
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT role, provider, model, fallback_provider, fallback_model,
                  max_tokens, temperature, enabled, updated_at
           FROM llm_routing WHERE agent = ?
           ORDER BY CASE role WHEN 'reasoning' THEN 1 WHEN 'agentic' THEN 2
                              WHEN 'coding' THEN 3 WHEN 'summary' THEN 4
                              ELSE 5 END""",
        (agent.upper() if agent != "DEFAULT" else agent,),
    )
    return {
        "agent": agent,
        "rows": [
            {
                "role": r["role"],
                "provider": r["provider"],
                "model": r["model"],
                "fallback_provider": r["fallback_provider"],
                "fallback_model": r["fallback_model"],
                "max_tokens": r["max_tokens"],
                "temperature": r["temperature"],
                "enabled": bool(r["enabled"]),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ],
    }


@app.patch("/api/llm-routing")
async def llm_routing_patch(body: RoutingPatchBody):
    agent_norm = body.agent.upper() if body.agent != "DEFAULT" else "DEFAULT"
    if agent_norm != "DEFAULT" and agent_norm not in _ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail="unknown agent")
    if body.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="unknown role")
    if body.provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(status_code=400, detail="unknown provider")
    if not body.model.strip():
        raise HTTPException(status_code=400, detail="model required")

    db = get_db()
    await db.execute(
        """INSERT INTO llm_routing (agent, role, provider, model, fallback_provider, fallback_model, enabled, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(agent, role) DO UPDATE SET
               provider = excluded.provider,
               model = excluded.model,
               fallback_provider = excluded.fallback_provider,
               fallback_model = excluded.fallback_model,
               enabled = excluded.enabled,
               updated_at = datetime('now')""",
        (agent_norm, body.role, body.provider, body.model.strip(),
         body.fallback_provider, body.fallback_model, 1 if body.enabled else 0),
    )
    await db.commit()
    return {"ok": True, "agent": agent_norm, "role": body.role}


# ── Capabilities ──────────────────────────────────────────────────────────────

_CAP_COLUMNS = {
    "cap_shell_commands", "cap_git", "cap_read_files", "cap_write_files",
    "cap_screen_capture", "cap_camera", "cap_web_search", "cap_browser_control",
    "cap_memory_read", "cap_memory_write", "cap_cron_jobs", "cap_notifications",
    "cap_channel_read",
}


class CapabilityPatchBody(BaseModel):
    agent: str = "SOUL"
    capability: str
    enabled: bool


@app.get("/api/capabilities")
async def capabilities_get(agent: str = "SOUL"):
    if agent.upper() not in _ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail="unknown agent")
    db = get_db()
    row = await db.execute_fetchall(
        "SELECT * FROM agent_capabilities WHERE agent = ? LIMIT 1",
        (agent.upper(),),
    )
    if not row:
        # auto-create row with safe defaults
        await db.execute("INSERT OR IGNORE INTO agent_capabilities (agent) VALUES (?)", (agent.upper(),))
        await db.commit()
        row = await db.execute_fetchall(
            "SELECT * FROM agent_capabilities WHERE agent = ? LIMIT 1",
            (agent.upper(),),
        )
    r = row[0]
    caps = {col: bool(r[col]) for col in _CAP_COLUMNS}
    return {
        "agent": agent.upper(),
        "capabilities": caps,
        "updated_at": r["updated_at"],
    }


@app.patch("/api/capabilities")
async def capabilities_patch(body: CapabilityPatchBody):
    if body.agent.upper() not in _ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail="unknown agent")
    if body.capability not in _CAP_COLUMNS:
        raise HTTPException(status_code=400, detail=f"unknown capability: {body.capability}")
    db = get_db()
    await db.execute(
        f"UPDATE agent_capabilities SET {body.capability} = ?, updated_at = datetime('now') WHERE agent = ?",
        (1 if body.enabled else 0, body.agent.upper()),
    )
    await db.commit()
    await _audit_log("USER", f"capability_toggle:{body.capability}", channel="settings",
                     target_id=body.agent.upper(), metadata={"enabled": body.enabled})
    return {"ok": True, "agent": body.agent.upper(), "capability": body.capability, "enabled": body.enabled}


# ── Audit Log ─────────────────────────────────────────────────────────────────

@app.get("/api/audit-log")
async def audit_log_list(
    agent: Optional[str] = None,
    action: Optional[str] = None,
    processed_locally: Optional[bool] = None,
    limit: int = 100,
):
    if agent and agent.upper() not in _ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail="invalid agent")
    limit = max(1, min(int(limit), 500))

    where: list[str] = ["1=1"]
    params: list = []
    if agent:
        where.append("agent = ?")
        params.append(agent.upper())
    if action:
        where.append("action LIKE ?")
        params.append(f"%{action}%")
    if processed_locally is not None:
        where.append("processed_locally = ?")
        params.append(1 if processed_locally else 0)
    params.append(limit)

    sql = (
        "SELECT id, agent, channel, action, target_id, metadata, "
        "processed_locally, provider_used, created_at "
        "FROM companion_audit_log WHERE " + " AND ".join(where) +
        " ORDER BY created_at DESC LIMIT ?"
    )
    db = get_db()
    rows = await db.execute_fetchall(sql, tuple(params))
    entries = []
    for r in rows:
        entries.append({
            "id": r["id"],
            "agent": r["agent"],
            "channel": r["channel"],
            "action": r["action"],
            "target_id": r["target_id"],
            "metadata": _decode_json(r["metadata"]),
            "processed_locally": bool(r["processed_locally"]),
            "provider_used": r["provider_used"],
            "created_at": r["created_at"],
        })
    local_count = sum(1 for e in entries if e["processed_locally"])
    return {
        "ok": True,
        "entries": entries,
        "count": len(entries),
        "stats": {"local": local_count, "egress": len(entries) - local_count},
    }


# ── Notifications ─────────────────────────────────────────────────────────────

_ALLOWED_NOTIF_TYPES = {"nerves_fire", "governance_challenge", "reflective_diagnosis", "system_alert", "integration_event", "custom"}
_ALLOWED_SEVERITY = {"critical", "warning", "info", "success"}


class NotificationCreate(BaseModel):
    agent: str = "SOUL"
    type: str
    title: str
    body: Optional[str] = None
    severity: str = "info"
    action_url: Optional[str] = None
    metadata: Optional[dict] = None


@app.get("/api/notifications")
async def notifications_list(unread_only: bool = False, limit: int = 50):
    limit = max(1, min(int(limit), 200))
    db = get_db()
    if unread_only:
        rows = await db.execute_fetchall(
            "SELECT * FROM user_notifications WHERE read = 0 AND dismissed = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM user_notifications WHERE dismissed = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    return {
        "ok": True,
        "notifications": [
            {
                "id": r["id"],
                "agent": r["agent"],
                "type": r["type"],
                "severity": r["severity"],
                "title": r["title"],
                "body": r["body"],
                "read": bool(r["read"]),
                "action_url": r["action_url"],
                "metadata": _decode_json(r["metadata"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


@app.post("/api/notifications")
async def notifications_create(body: NotificationCreate):
    if body.agent.upper() not in _ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail="unknown agent")
    if body.type not in _ALLOWED_NOTIF_TYPES:
        raise HTTPException(status_code=400, detail="unknown notification type")
    if body.severity not in _ALLOWED_SEVERITY:
        raise HTTPException(status_code=400, detail="unknown severity")
    db = get_db()
    cur = await db.execute(
        """INSERT INTO user_notifications (agent, type, severity, title, body, action_url, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (body.agent.upper(), body.type, body.severity, body.title, body.body,
         body.action_url, json.dumps(body.metadata) if body.metadata else None),
    )
    await db.commit()
    return {"ok": True, "id": cur.lastrowid}


@app.post("/api/notifications/{notif_id}/read")
async def notifications_mark_read(notif_id: int):
    db = get_db()
    await db.execute("UPDATE user_notifications SET read = 1 WHERE id = ?", (notif_id,))
    await db.commit()
    return {"ok": True, "id": notif_id}


@app.delete("/api/notifications/{notif_id}")
async def notifications_dismiss(notif_id: int):
    db = get_db()
    await db.execute("UPDATE user_notifications SET dismissed = 1 WHERE id = ?", (notif_id,))
    await db.commit()
    return {"ok": True, "id": notif_id}


# ── Memory Tree (P1 — h→d→m→y user-friendly recall) ──────────────────────────

_ALLOWED_LEVELS = {"hour", "day", "month", "year"}
_MEMORY_TREE_PARENT_LEVEL = {"hour": "day", "day": "month", "month": "year"}


def _validate_memory_tree_args(agent: str, level: Optional[str] = None) -> tuple[str, Optional[str]]:
    if agent.upper() not in _ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail="unknown agent")
    if level is not None and level not in _ALLOWED_LEVELS:
        raise HTTPException(status_code=400, detail=f"unknown level: {level}")
    return agent.upper(), level


def _memory_tree_bucket(r) -> dict:
    child_ids = _decode_json(r["child_ids"]) or []
    return {
        "id": r["id"],
        "level": r["level"],
        "bucket_key": r["bucket_start"],
        "bucket_start": r["bucket_start"],
        "bucket_end": r["bucket_end"],
        "summary": r["summary"],
        "child_ids": child_ids,
        "child_count": len(child_ids),
        "created_at": r["created_at"],
    }


@app.get("/api/memory-tree")
async def memory_tree_list(agent: str = "SOUL", level: str = "day", limit: int = 30):
    agent_norm, level = _validate_memory_tree_args(agent, level)
    limit = max(1, min(int(limit), 100))
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT id, level, bucket_start, bucket_end, summary, child_ids, created_at
           FROM memory_tree WHERE agent = ? AND level = ?
           ORDER BY bucket_start DESC LIMIT ?""",
        (agent_norm, level, limit),
    )
    return {
        "ok": True,
        "agent": agent_norm,
        "level": level,
        "buckets": [_memory_tree_bucket(r) for r in rows],
        "count": len(rows),
    }


@app.get("/api/memory-tree/search")
async def memory_tree_search(agent: str = "SOUL", q: str = "", level: Optional[str] = None, limit: int = 30):
    agent_norm, level = _validate_memory_tree_args(agent, level)
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="q required")
    limit = max(1, min(int(limit), 100))
    db = get_db()
    where = ["agent = ?", "lower(summary) LIKE ?"]
    params: list[Any] = [agent_norm, f"%{query.lower()}%"]
    if level:
        where.append("level = ?")
        params.append(level)
    params.append(limit)
    rows = await db.execute_fetchall(
        """SELECT id, level, bucket_start, bucket_end, summary, child_ids, created_at
           FROM memory_tree
           WHERE """ + " AND ".join(where) + """
           ORDER BY bucket_start DESC LIMIT ?""",
        tuple(params),
    )
    return {
        "ok": True,
        "agent": agent_norm,
        "level": level,
        "query": query,
        "buckets": [_memory_tree_bucket(r) for r in rows],
        "count": len(rows),
    }


@app.get("/api/memory-tree/buckets/{bucket_id}")
async def memory_tree_detail(bucket_id: int):
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT id, agent, level, bucket_start, bucket_end, summary, child_ids, created_at
           FROM memory_tree WHERE id = ?""",
        (bucket_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="bucket not found")
    row = rows[0]
    bucket = _memory_tree_bucket(row)
    child_ids = bucket["child_ids"]

    child_buckets = []
    memories = []
    if child_ids:
        placeholders = ",".join("?" for _ in child_ids)
        if row["level"] == "hour":
            memory_rows = await db.execute_fetchall(
                f"""SELECT id, agent, category, content, importance, created_at
                    FROM memories WHERE id IN ({placeholders})""",
                tuple(child_ids),
            )
            by_id = {r["id"]: r for r in memory_rows}
            for cid in child_ids:
                r = by_id.get(cid)
                if r:
                    memories.append({
                        "id": r["id"],
                        "agent": r["agent"],
                        "category": r["category"],
                        "content": r["content"],
                        "importance": r["importance"],
                        "created_at": r["created_at"],
                    })
        else:
            child_rows = await db.execute_fetchall(
                f"""SELECT id, level, bucket_start, bucket_end, summary, child_ids, created_at
                    FROM memory_tree WHERE id IN ({placeholders})""",
                tuple(child_ids),
            )
            by_id = {r["id"]: r for r in child_rows}
            child_buckets = [_memory_tree_bucket(by_id[cid]) for cid in child_ids if cid in by_id]

    parent = None
    parent_level = _MEMORY_TREE_PARENT_LEVEL.get(row["level"])
    if parent_level:
        parent_rows = await db.execute_fetchall(
            """SELECT id, level, bucket_start, bucket_end, summary, child_ids, created_at
               FROM memory_tree WHERE agent = ? AND level = ?
               ORDER BY bucket_start DESC LIMIT 200""",
            (row["agent"], parent_level),
        )
        for parent_row in parent_rows:
            if bucket_id in (_decode_json(parent_row["child_ids"]) or []):
                parent = _memory_tree_bucket(parent_row)
                break

    return {
        "ok": True,
        "bucket": bucket,
        "parent": parent,
        "children": child_buckets,
        "memories": memories,
        "child_count": len(child_buckets) + len(memories),
    }


class MemoryTreeRebuildBody(BaseModel):
    agent: str = "SOUL"
    level: str = "all"
    dry_run: bool = False


@app.post("/api/memory-tree/rebuild")
async def memory_tree_rebuild(body: MemoryTreeRebuildBody):
    agent_norm, _ = _validate_memory_tree_args(body.agent)
    level = body.level
    if level != "all" and level not in _ALLOWED_LEVELS:
        raise HTTPException(status_code=400, detail=f"unknown level: {level}")
    from companion_core.memory_tree_builder import build_all, build_level
    try:
        if level == "all":
            result = await build_all(agent_norm, dry_run=body.dry_run)
        else:
            result = await build_level(agent_norm, level, dry_run=body.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _audit_log("USER", "memory_tree_rebuild", channel="local",
                     target_id=f"{agent_norm}:{level}", metadata={"dry_run": body.dry_run})
    return {"ok": True, "agent": agent_norm, "level": level, "dry_run": body.dry_run, "result": result}


# ── Connections / Integrations (P2 — Add Account view) ───────────────────────

_KNOWN_CONNECTORS = {
    "gmail": {"name": "Gmail", "category": "email"},
    "gcal": {"name": "Google Calendar", "category": "calendar"},
    "gdrive": {"name": "Google Drive", "category": "storage"},
    "github": {"name": "GitHub", "category": "dev"},
    "notion": {"name": "Notion", "category": "notes"},
    "slack": {"name": "Slack", "category": "chat"},
    "telegram": {"name": "Telegram", "category": "chat"},
    "obsidian": {"name": "Obsidian Vault", "category": "notes"},
    "whatsapp": {"name": "WhatsApp", "category": "chat"},
    "ms365": {"name": "Microsoft 365", "category": "office"},
}


@app.get("/api/connections")
async def connections_list():
    """List all available connectors + their connection status (from integrations table)."""
    db = get_db()
    connected = await db.execute_fetchall("SELECT id, connected_at FROM integrations")
    connected_map = {r["id"]: r["connected_at"] for r in connected}
    items = []
    for cid, meta in _KNOWN_CONNECTORS.items():
        items.append({
            "id": cid,
            "name": meta["name"],
            "category": meta["category"],
            "connected": cid in connected_map,
            "connected_at": connected_map.get(cid),
            "status": "connected" if cid in connected_map else "available",
        })
    return {"ok": True, "connectors": sorted(items, key=lambda x: (x["category"], x["name"])), "count": len(items)}


class ConnectionAddBody(BaseModel):
    connector_id: str


@app.post("/api/connections/add")
async def connections_add(body: ConnectionAddBody):
    """Mark a connector as connected. UI may then trigger OAuth flow externally."""
    cid = body.connector_id.lower().strip()
    if cid not in _KNOWN_CONNECTORS:
        raise HTTPException(status_code=400, detail=f"unknown connector: {cid}")
    db = get_db()
    await db.execute(
        "INSERT OR IGNORE INTO integrations (id, connected_at) VALUES (?, datetime('now'))",
        (cid,),
    )
    await db.commit()
    await _audit_log("USER", "connection_add", channel="settings", target_id=cid,
                     metadata={"connector": cid})
    return {"ok": True, "connector": cid, "connected": True}


@app.delete("/api/connections/{connector_id}")
async def connections_remove(connector_id: str):
    cid = connector_id.lower().strip()
    db = get_db()
    await db.execute("DELETE FROM integrations WHERE id = ?", (cid,))
    await db.commit()
    await _audit_log("USER", "connection_remove", channel="settings", target_id=cid)
    return {"ok": True, "connector": cid, "connected": False}


# ── Screen Awareness (P3 — local capture/analyze status) ─────────────────────

@app.get("/api/screen/status")
async def screen_status():
    """Returns capability status: whether the OS supports capture + analyzer model present."""
    import shutil
    has_mss = False
    try:
        import mss  # noqa: F401
        has_mss = True
    except Exception:
        pass
    # Ollama reachable?
    has_ollama = False
    try:
        import urllib.request as _u
        req = _u.Request("http://localhost:11434/api/tags", method="GET")
        with _u.urlopen(req, timeout=2) as r:
            has_ollama = r.status == 200
    except Exception:
        pass
    return {
        "ok": True,
        "capture_available": has_mss,
        "analyzer_available": has_ollama,
        "recommended_model": "gemma3:4b" if has_ollama else None,
        "permission_note": "Screen capture solo se ejecuta cuando el usuario lo pide. La imagen NO sale del equipo.",
        "setup_hint": None if (has_mss and has_ollama) else (
            "Para activar Screen Awareness: instalar mss (pip install mss) y arrancar Ollama con un modelo de visión (ollama pull gemma3:4b)."
        ),
    }


# ── Screen Intelligence: capture + analyze + history (real impl) ─────────────

# We persist screen captures as a thumbnail in user_notifications-adjacent table
# Lightweight: keep a small ring buffer in memory + base64 thumbnails on disk.
import base64 as _b64
from pathlib import Path as _Path
import re as _re

_SCREEN_DIR = _Path.home() / ".config" / "soul-companion" / "screen_captures"
_SCREEN_RING_MAX = 30
_SCREEN_ID_RE = _re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _screen_dir_ready() -> _Path:
    _SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_SCREEN_DIR, 0o700)
    except Exception:
        pass
    return _SCREEN_DIR


def _screen_image_path(image_id: str, *, thumbnail: bool = False) -> _Path:
    if not _SCREEN_ID_RE.fullmatch(image_id):
        raise HTTPException(status_code=400, detail="invalid image_id")
    suffix = ".thumb.png" if thumbnail else ".png"
    return _screen_dir_ready() / f"{image_id}{suffix}"


class ScreenAnalyzeBody(BaseModel):
    image_id: Optional[str] = None  # if omitted, captures fresh
    prompt: str = "Describe what's on the screen in 2-3 sentences. Be specific."
    model: str = "gemma3:4b"


@app.post("/api/screen/capture")
async def screen_capture():
    """Capture current screen, save as PNG under user config, return id+thumbnail b64."""
    try:
        import mss
        from PIL import Image
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"capture deps missing: {e}")

    from datetime import datetime as _dt
    sd = _screen_dir_ready()
    image_id = _dt.now().strftime("%Y%m%dT%H%M%S%f")
    path = _screen_image_path(image_id)
    thumb_path = _screen_image_path(image_id, thumbnail=True)

    def _do_capture() -> tuple[int, int]:
        with mss.mss() as sct:
            mon = sct.monitors[1]
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.rgb)
            img.save(path, "PNG", optimize=True)
            thumb = img.copy()
            thumb.thumbnail((320, 240))
            thumb.save(thumb_path, "PNG", optimize=True)
            return img.size

    import asyncio as _asyncio
    try:
        w, h = await _asyncio.to_thread(_do_capture)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"capture failed: {e}")

    # Read thumbnail b64
    thumb_b64 = _b64.b64encode(thumb_path.read_bytes()).decode("ascii")

    # Trim ring buffer (oldest first)
    pngs = sorted(sd.glob("*.png"))
    full_pngs = [p for p in pngs if not p.name.endswith(".thumb.png")]
    excess = len(full_pngs) - _SCREEN_RING_MAX
    if excess > 0:
        for old in full_pngs[:excess]:
            old.unlink(missing_ok=True)
            old.with_suffix(".thumb.png").unlink(missing_ok=True)

    await _audit_log("USER", "screen_capture", channel="local",
                     target_id=image_id, metadata={"size": f"{w}x{h}"})
    return {
        "ok": True, "image_id": image_id, "width": w, "height": h,
        "thumbnail_b64": thumb_b64, "path": str(path), "local_only": True,
    }


@app.post("/api/screen/analyze")
async def screen_analyze(body: ScreenAnalyzeBody):
    """Send a captured screen to local Ollama vision model and get description."""
    if body.image_id:
        path = _screen_image_path(body.image_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"image not found: {body.image_id}")
    else:
        # capture fresh
        cap = await screen_capture()
        path = _Path(cap["path"])
        body.image_id = cap["image_id"]

    image_b64 = _b64.b64encode(path.read_bytes()).decode("ascii")

    import urllib.request as _u
    payload = json.dumps({
        "model": body.model,
        "prompt": body.prompt,
        "images": [image_b64],
        "stream": False,
    }).encode()
    try:
        req = _u.Request("http://localhost:11434/api/generate",
                         data=payload, headers={"Content-Type": "application/json"},
                         method="POST")
        with _u.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        description = data.get("response", "").strip()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"analyzer failed: {e}")

    await _audit_log("USER", "screen_analyze", channel="local",
                     target_id=body.image_id,
                     metadata={"model": body.model, "chars": len(description)},
                     provider_used="ollama")
    return {
        "ok": True, "image_id": body.image_id, "model": body.model,
        "description": description, "local_only": True,
    }


@app.get("/api/screen/history")
async def screen_history(limit: int = 20):
    """List recent captures (id, timestamp, thumbnail path)."""
    sd = _screen_dir_ready()
    limit = max(1, min(limit, 100))
    pngs = sorted([p for p in sd.glob("*.png") if not p.name.endswith(".thumb.png")], reverse=True)
    items = []
    for p in pngs[:limit]:
        thumb = p.with_suffix(".thumb.png")
        items.append({
            "image_id": p.stem,
            "captured_at": p.stem,
            "has_thumbnail": thumb.exists(),
            "size_bytes": p.stat().st_size,
        })
    return {"ok": True, "captures": items, "count": len(items), "local_only": True}


@app.get("/api/screen/thumbnail/{image_id}")
async def screen_thumbnail(image_id: str):
    """Return thumbnail PNG for a captured image."""
    thumb = _screen_image_path(image_id, thumbnail=True)
    if not thumb.exists():
        raise HTTPException(status_code=404, detail="thumbnail not found")
    from fastapi.responses import Response
    return Response(content=thumb.read_bytes(), media_type="image/png")


@app.delete("/api/screen/capture/{image_id}")
async def screen_delete(image_id: str):
    sd = _screen_dir_ready()
    p = sd / f"{image_id}.png"
    t = sd / f"{image_id}.thumb.png"
    deleted = 0
    for f in (p, t):
        if f.exists():
            f.unlink()
            deleted += 1
    return {"ok": True, "image_id": image_id, "files_deleted": deleted}


# ── TokenJuice rules (P5 — context compression manager) ──────────────────────

_TOKENJUICE_BUILTIN_RULES = [
    {"id": "git_status_strip", "label": "Limpiar 'git status' largo",
     "pattern": r"^On branch.*\n\nnothing to commit", "category": "git", "builtin": True, "enabled": True},
    {"id": "npm_install_quiet", "label": "Quitar ruido de npm install",
     "pattern": r"npm warn deprecated.*", "category": "npm", "builtin": True, "enabled": True},
    {"id": "docker_pull_progress", "label": "Quitar progreso de docker pull",
     "pattern": r"\w+: Pulling fs layer.*", "category": "docker", "builtin": True, "enabled": True},
    {"id": "ansi_color_codes", "label": "Quitar códigos de color ANSI",
     "pattern": r"\x1b\[[0-9;]*m", "category": "shell", "builtin": True, "enabled": True},
    {"id": "trailing_whitespace", "label": "Quitar espacios al final de líneas",
     "pattern": r"[ \t]+$", "category": "format", "builtin": True, "enabled": True},
]
_TOKENJUICE_RULE_ID_RE = _re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def _tokenjuice_builtin_ids() -> set[str]:
    return {str(r["id"]) for r in _TOKENJUICE_BUILTIN_RULES}


def _tokenjuice_validate_id(rule_id: str) -> str:
    cleaned = rule_id.strip().lower()
    if not _TOKENJUICE_RULE_ID_RE.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail="invalid rule id")
    return cleaned


def _tokenjuice_validate_pattern(pattern: str) -> str:
    import re as _re_local
    pattern = pattern or ""
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern required")
    if len(pattern) > 500:
        raise HTTPException(status_code=400, detail="pattern too long")
    try:
        _re_local.compile(pattern)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid regex: {exc}")
    return pattern


def _tokenjuice_rule_from_row(r) -> dict:
    return {
        "id": r["id"],
        "label": r["label"],
        "pattern": r["pattern"],
        "category": r["category"],
        "enabled": bool(r["enabled"]),
        "builtin": bool(r["builtin"]),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


async def _tokenjuice_custom_rules(include_disabled: bool = False) -> list[dict]:
    db = get_db()
    where = "" if include_disabled else "WHERE enabled = 1"
    rows = await db.execute_fetchall(
        f"""SELECT id, label, pattern, category, enabled, builtin, created_at, updated_at
            FROM tokenjuice_rules {where}
            ORDER BY category ASC, label ASC"""
    )
    return [_tokenjuice_rule_from_row(r) for r in rows]


async def _tokenjuice_all_rules(include_disabled: bool = False) -> list[dict]:
    custom = await _tokenjuice_custom_rules(include_disabled=include_disabled)
    custom_ids = {r["id"] for r in custom}
    builtins = [dict(r) for r in _TOKENJUICE_BUILTIN_RULES if include_disabled or r.get("enabled", True)]
    return [*builtins, *[r for r in custom if r["id"] not in _tokenjuice_builtin_ids()]]


async def _tokenjuice_record_stats(applied: list[dict], input_chars: int, output_chars: int) -> None:
    if not applied:
        return
    db = get_db()
    saved = max(0, input_chars - output_chars)
    for item in applied:
        rule_id = item["rule"]
        matches = int(item["matches"])
        await db.execute(
            """INSERT INTO tokenjuice_rule_stats (rule_id, match_count, chars_saved, last_used_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(rule_id) DO UPDATE SET
                   match_count = match_count + excluded.match_count,
                   chars_saved = chars_saved + excluded.chars_saved,
                   last_used_at = excluded.last_used_at""",
            (rule_id, matches, saved),
        )
    await db.commit()


@app.get("/api/tokenjuice/rules")
async def tokenjuice_rules(include_disabled: bool = False):
    """List active compression rules (user-friendly view of TokenJuice config)."""
    rules = await _tokenjuice_all_rules(include_disabled=include_disabled)
    return {
        "ok": True,
        "rules": rules,
        "count": len(rules),
        "user_friendly_label": "Reducir ruido del contexto",
    }


class TokenjuiceRuleBody(BaseModel):
    id: Optional[str] = None
    label: str
    pattern: str
    category: str = "custom"
    enabled: bool = True


class TokenjuiceRulePatchBody(BaseModel):
    label: Optional[str] = None
    pattern: Optional[str] = None
    category: Optional[str] = None
    enabled: Optional[bool] = None


@app.post("/api/tokenjuice/rules")
async def tokenjuice_rule_create(body: TokenjuiceRuleBody):
    import re as _re_local
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="label required")
    rule_id = _tokenjuice_validate_id(body.id or _re_local.sub(r"[^a-z0-9_-]+", "_", label.lower()).strip("_"))
    if rule_id in _tokenjuice_builtin_ids():
        raise HTTPException(status_code=400, detail="builtin rule id is protected")
    pattern = _tokenjuice_validate_pattern(body.pattern)
    category = (body.category or "custom").strip().lower()[:40] or "custom"
    db = get_db()
    await db.execute(
        """INSERT INTO tokenjuice_rules (id, label, pattern, category, enabled, builtin)
           VALUES (?, ?, ?, ?, ?, 0)
           ON CONFLICT(id) DO UPDATE SET
               label=excluded.label,
               pattern=excluded.pattern,
               category=excluded.category,
               enabled=excluded.enabled,
               updated_at=datetime('now')""",
        (rule_id, label, pattern, category, int(body.enabled)),
    )
    await db.commit()
    await _audit_log("USER", "tokenjuice_rule_upsert", channel="settings", target_id=rule_id)
    return {"ok": True, "rule": {"id": rule_id, "label": label, "pattern": pattern,
                                  "category": category, "enabled": body.enabled, "builtin": False}}


@app.patch("/api/tokenjuice/rules/{rule_id}")
async def tokenjuice_rule_update(rule_id: str, body: TokenjuiceRulePatchBody):
    rule_id = _tokenjuice_validate_id(rule_id)
    if rule_id in _tokenjuice_builtin_ids():
        raise HTTPException(status_code=400, detail="builtin rules are read-only")
    db = get_db()
    rows = await db.execute_fetchall("SELECT id FROM tokenjuice_rules WHERE id = ?", (rule_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="rule not found")
    updates = []
    params: list[Any] = []
    if body.label is not None:
        label = body.label.strip()
        if not label:
            raise HTTPException(status_code=400, detail="label required")
        updates.append("label = ?")
        params.append(label)
    if body.pattern is not None:
        updates.append("pattern = ?")
        params.append(_tokenjuice_validate_pattern(body.pattern))
    if body.category is not None:
        updates.append("category = ?")
        params.append((body.category or "custom").strip().lower()[:40] or "custom")
    if body.enabled is not None:
        updates.append("enabled = ?")
        params.append(int(body.enabled))
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(rule_id)
        await db.execute(f"UPDATE tokenjuice_rules SET {', '.join(updates)} WHERE id = ?", tuple(params))
        await db.commit()
    await _audit_log("USER", "tokenjuice_rule_update", channel="settings", target_id=rule_id)
    rows = await db.execute_fetchall(
        "SELECT id, label, pattern, category, enabled, builtin, created_at, updated_at FROM tokenjuice_rules WHERE id = ?",
        (rule_id,),
    )
    return {"ok": True, "rule": _tokenjuice_rule_from_row(rows[0])}


@app.delete("/api/tokenjuice/rules/{rule_id}")
async def tokenjuice_rule_delete(rule_id: str):
    rule_id = _tokenjuice_validate_id(rule_id)
    if rule_id in _tokenjuice_builtin_ids():
        raise HTTPException(status_code=400, detail="builtin rules are read-only")
    db = get_db()
    await db.execute("DELETE FROM tokenjuice_rule_stats WHERE rule_id = ?", (rule_id,))
    cur = await db.execute("DELETE FROM tokenjuice_rules WHERE id = ?", (rule_id,))
    await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="rule not found")
    await _audit_log("USER", "tokenjuice_rule_delete", channel="settings", target_id=rule_id)
    return {"ok": True, "id": rule_id, "deleted": True}


class TokenjuiceCompactBody(BaseModel):
    text: str


@app.post("/api/tokenjuice/compact")
async def tokenjuice_compact(body: TokenjuiceCompactBody):
    """Apply all builtin rules to compress text. Returns the compacted result + savings."""
    import re as _re
    src = body.text or ""
    out = src
    applied = []
    rules = await _tokenjuice_all_rules()
    for rule in rules:
        try:
            new, n = _re.subn(rule["pattern"], "", out, flags=_re.MULTILINE | _re.IGNORECASE)
            if n > 0:
                out = new
                applied.append({"rule": rule["id"], "matches": n})
        except Exception:
            continue
    await _tokenjuice_record_stats(applied, len(src), len(out))
    return {
        "ok": True,
        "input_chars": len(src),
        "output_chars": len(out),
        "savings_pct": round((1 - len(out) / max(len(src), 1)) * 100, 1),
        "rules_applied": applied,
        "output": out,
    }


@app.get("/api/tokenjuice/stats")
async def tokenjuice_stats():
    rules = await _tokenjuice_all_rules(include_disabled=True)
    labels = {r["id"]: r for r in rules}
    db = get_db()
    rows = await db.execute_fetchall(
        "SELECT rule_id, match_count, chars_saved, last_used_at FROM tokenjuice_rule_stats ORDER BY chars_saved DESC, match_count DESC"
    )
    stats = []
    for r in rows:
        rule = labels.get(r["rule_id"], {"id": r["rule_id"], "label": r["rule_id"], "category": "unknown", "builtin": False})
        stats.append({
            "rule_id": r["rule_id"],
            "label": rule["label"],
            "category": rule["category"],
            "builtin": bool(rule.get("builtin", False)),
            "match_count": r["match_count"],
            "chars_saved": r["chars_saved"],
            "last_used_at": r["last_used_at"],
        })
    return {"ok": True, "stats": stats, "count": len(stats)}


@app.get("/api/tokenjuice/export")
async def tokenjuice_export():
    return {
        "ok": True,
        "version": 1,
        "custom_rules": await _tokenjuice_custom_rules(include_disabled=True),
    }


class TokenjuiceImportBody(BaseModel):
    custom_rules: list[TokenjuiceRuleBody]


@app.post("/api/tokenjuice/import")
async def tokenjuice_import(body: TokenjuiceImportBody):
    imported = []
    for rule in body.custom_rules[:100]:
        created = await tokenjuice_rule_create(rule)
        imported.append(created["rule"]["id"])
    return {"ok": True, "imported": imported, "count": len(imported)}


# ── Cron Jobs (visible schedules — P3 OpenHuman doc 43) ─────────────────────

class CronCreateBody(BaseModel):
    name: str
    cron_expression: str
    handler: str
    agent: Optional[str] = None
    enabled: bool = True


@app.get("/api/cron-jobs")
async def cron_jobs_list():
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT id, name, agent, cron_expression, handler, enabled,
                  last_run_at, next_run_at, created_by, created_at
           FROM cron_jobs ORDER BY enabled DESC, name ASC"""
    )
    return {"ok": True, "jobs": [dict(r) for r in rows], "count": len(rows)}


@app.post("/api/cron-jobs")
async def cron_jobs_create(body: CronCreateBody):
    if not body.name.strip() or not body.cron_expression.strip() or not body.handler.strip():
        raise HTTPException(status_code=400, detail="name, cron_expression, handler required")
    db = get_db()
    try:
        cur = await db.execute(
            """INSERT INTO cron_jobs (name, agent, cron_expression, handler, enabled, created_by)
               VALUES (?, ?, ?, ?, ?, 'USER')""",
            (body.name.strip(), body.agent, body.cron_expression.strip(),
             body.handler.strip(), 1 if body.enabled else 0),
        )
        await db.commit()
        return {"ok": True, "id": cur.lastrowid, "name": body.name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/cron-jobs/{job_id}")
async def cron_jobs_delete(job_id: int):
    db = get_db()
    await db.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
    await db.commit()
    return {"ok": True, "id": job_id}


@app.post("/api/cron-jobs/{job_id}/toggle")
async def cron_jobs_toggle(job_id: int):
    db = get_db()
    await db.execute(
        "UPDATE cron_jobs SET enabled = 1 - enabled WHERE id = ?",
        (job_id,),
    )
    await db.commit()
    return {"ok": True, "id": job_id}


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
