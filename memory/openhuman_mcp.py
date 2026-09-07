#!/usr/bin/env python3
"""OpenHuman MCP Wrapper v1 — expone la API JSON-RPC :7788 de OpenHuman como tools MCP.

Permite a los agentes SEAL interactuar con OpenHuman instalado en DGX Spark.

Endpoint: POST http://HOST:7788/rpc  (Bearer token from ~/.openhuman/core.token or /tmp/openhuman_rpc_token.txt)
Schema:   GET  http://HOST:7788/schema  (no auth)

Namespaces expuestos: core, agent, memory_tree, subconscious, local_ai, memory, heartbeat

Usage:
    python3 openhuman_mcp.py                    # stdio (MCP default)
    OPENHUMAN_HOST=192.168.68.200 python3 openhuman_mcp.py

Env vars:
    OPENHUMAN_HOST    — IP/hostname de OpenHuman (default: 192.168.68.200)
    OPENHUMAN_PORT    — Puerto RPC (default: 7788)
    OPENHUMAN_TOKEN   — Bearer token (auto-loaded from /tmp/openhuman_rpc_token.txt if unset)
    OPENHUMAN_TIMEOUT — Timeout segundos (default: 30)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

OPENHUMAN_HOST = os.environ.get("OPENHUMAN_HOST", "192.168.68.200")
OPENHUMAN_PORT = int(os.environ.get("OPENHUMAN_PORT", "7788"))
OPENHUMAN_TIMEOUT = float(os.environ.get("OPENHUMAN_TIMEOUT", "30"))
RPC_URL = f"http://{OPENHUMAN_HOST}:{OPENHUMAN_PORT}/rpc"

# Token resolution: env var → /tmp/openhuman_rpc_token.txt (refreshed on each OpenHuman start)
_TOKEN_FILE = Path("/tmp/openhuman_rpc_token.txt")
_SPARK_TOKEN_FILE = Path(f"/home/dadito/.openhuman/core.token")


def _load_token() -> Optional[str]:
    token = os.environ.get("OPENHUMAN_TOKEN")
    if token:
        return token.strip()
    # Try local /tmp file first (if running on Spark directly)
    if _TOKEN_FILE.exists():
        t = _TOKEN_FILE.read_text().strip()
        if t:
            return t
    # Try SSH-based: fetch token from Spark via SSH if running on the main machine
    try:
        import subprocess
        result = subprocess.run(
            ["ssh", "dadito@192.168.68.200", f"cat {_TOKEN_FILE}"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            t = result.stdout.strip()
            if t:
                return t
    except Exception:
        pass
    return None


mcp = FastMCP("openhuman")
_rpc_id = 0


def _next_id() -> int:
    global _rpc_id
    _rpc_id += 1
    return _rpc_id


def _is_remote() -> bool:
    """True when OpenHuman runs on a remote host (not localhost/127.0.0.1)."""
    return OPENHUMAN_HOST not in ("localhost", "127.0.0.1", "::1")


async def _rpc_remote(method: str, params: Optional[dict], token: Optional[str]) -> Any:
    """Execute RPC via SSH subprocess when OpenHuman is on a remote host.

    Uses a remote Python one-liner that reads the JSON payload from stdin,
    making it immune to shell quoting issues regardless of param content.
    """
    import subprocess
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "id": _next_id(),
        "params": params or {},
    })
    # Remote Python one-liner: reads payload from stdin, posts via urllib (stdlib only)
    token_line = f"hdrs['Authorization'] = 'Bearer {token}'" if token else ""
    remote_py = (
        "import sys,json,urllib.request;"
        "data=sys.stdin.buffer.read();"
        f"url='http://localhost:{OPENHUMAN_PORT}/rpc';"
        "hdrs={'Content-Type':'application/json'};"
        f"{token_line};"
        "req=urllib.request.Request(url,data=data,headers=hdrs,method='POST');"
        "print(urllib.request.urlopen(req).read().decode())"
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", f"dadito@{OPENHUMAN_HOST}", f"python3 -c \"{remote_py}\""],
        input=payload,
        capture_output=True, text=True, timeout=OPENHUMAN_TIMEOUT
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or "SSH command failed"}
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": f"Invalid JSON from remote: {result.stdout[:200]}"}
    if "error" in body:
        err = body["error"]
        return {"error": err.get("message", str(err)), "code": err.get("code")}
    return body.get("result")


async def _rpc(method: str, params: Optional[dict] = None) -> Any:
    """Execute a JSON-RPC 2.0 call against OpenHuman core.

    Uses SSH subprocess when OpenHuman is on a remote host (e.g. DGX Spark).
    Uses httpx directly when running locally.
    """
    token = _load_token()

    if _is_remote():
        return await _rpc_remote(method, params, token)

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "id": _next_id(),
        "params": params or {},
    }
    async with httpx.AsyncClient(timeout=OPENHUMAN_TIMEOUT) as client:
        resp = await client.post(RPC_URL, json=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()

    if "error" in body:
        err = body["error"]
        return {"error": err.get("message", str(err)), "code": err.get("code")}
    return body.get("result")


# ── Core ──────────────────────────────────────────────────────────────────────

@mcp.tool()
async def openhuman_ping() -> str:
    """Check if OpenHuman core RPC server is reachable."""
    result = await _rpc("core.ping")
    return json.dumps({"reachable": True, "host": OPENHUMAN_HOST, "port": OPENHUMAN_PORT, "result": result})


@mcp.tool()
async def openhuman_version() -> str:
    """Get the running OpenHuman version."""
    result = await _rpc("core.version")
    return json.dumps(result)


# ── Agent Chat ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def openhuman_chat(message: str, temperature: float = 0.7) -> str:
    """Send a message to OpenHuman's AI agent and get a response.

    Uses the cloud model (configured in OpenHuman settings).
    For local-only inference use openhuman_local_chat.

    Args:
        message: The message to send to the agent
        temperature: Sampling temperature 0.0-1.0 (default 0.7)
    """
    result = await _rpc("openhuman.agent_chat", {
        "message": message,
        "temperature": temperature,
    })
    return json.dumps(result)


@mcp.tool()
async def openhuman_local_chat(messages: list[dict], max_tokens: int = 1024) -> str:
    """Multi-turn chat via OpenHuman's local Ollama model (no cloud needed).

    Args:
        messages: List of {role, content} dicts (OpenAI format)
        max_tokens: Max tokens to generate
    """
    result = await _rpc("openhuman.local_ai_chat", {
        "messages": messages,
        "max_tokens": max_tokens,
    })
    return json.dumps(result)


@mcp.tool()
async def openhuman_local_prompt(prompt: str, max_tokens: int = 512) -> str:
    """Run a direct prompt through OpenHuman's local AI model.

    Args:
        prompt: The prompt text
        max_tokens: Max tokens to generate
    """
    result = await _rpc("openhuman.local_ai_prompt", {
        "prompt": prompt,
        "max_tokens": max_tokens,
    })
    return json.dumps(result)


@mcp.tool()
async def openhuman_summarize(text: str, max_tokens: int = 256) -> str:
    """Summarize text using OpenHuman's local AI.

    Args:
        text: Text to summarize
        max_tokens: Max tokens for summary
    """
    result = await _rpc("openhuman.local_ai_summarize", {
        "text": text,
        "max_tokens": max_tokens,
    })
    return json.dumps(result)


# ── Memory Tree ────────────────────────────────────────────────────────────────

@mcp.tool()
async def openhuman_memory_recall(query: str, k: int = 10) -> str:
    """Semantic recall from OpenHuman's memory tree.

    Uses cosine rerank against query embedding. Returns most relevant chunks.

    Args:
        query: Natural language query
        k: Number of results to return (default 10)
    """
    result = await _rpc("openhuman.memory_tree_recall", {
        "query": query,
        "k": k,
    })
    return json.dumps(result)


@mcp.tool()
async def openhuman_memory_search(query: str, k: int = 10) -> str:
    """Keyword LIKE-search over OpenHuman's memory chunks.

    Cheap, deterministic. Complement with openhuman_memory_recall for semantic.

    Args:
        query: Keyword string to search
        k: Max results (default 10)
    """
    result = await _rpc("openhuman.memory_tree_search", {
        "query": query,
        "k": k,
    })
    return json.dumps(result)


@mcp.tool()
async def openhuman_top_entities(kind: Optional[str] = None, limit: int = 20) -> str:
    """Get most-frequent canonical entities in OpenHuman's workspace.

    Useful for understanding what topics/people dominate their memory.

    Args:
        kind: Filter by entity kind (person, org, topic) — None for all
        limit: Max entities to return (default 20)
    """
    params: dict = {"limit": limit}
    if kind:
        params["kind"] = kind
    result = await _rpc("openhuman.memory_tree_top_entities", params)
    return json.dumps(result)


@mcp.tool()
async def openhuman_list_chunks(
    query: Optional[str] = None,
    limit: int = 20,
    source_kinds: Optional[list[str]] = None,
) -> str:
    """List memory chunks with optional filters.

    Args:
        query: Optional text filter
        limit: Max chunks (default 20)
        source_kinds: Filter by source kind (e.g. ['slack', 'web'])
    """
    params: dict = {"limit": limit}
    if query:
        params["query"] = query
    if source_kinds:
        params["source_kinds"] = source_kinds
    result = await _rpc("openhuman.memory_tree_list_chunks", params)
    return json.dumps(result)


@mcp.tool()
async def openhuman_memory_global_digest(window_days: int = 7) -> str:
    """Get OpenHuman's global memory digest for the last N days.

    Args:
        window_days: Days to look back (default 7)
    """
    result = await _rpc("openhuman.memory_tree_query_global", {"window_days": window_days})
    return json.dumps(result)


@mcp.tool()
async def openhuman_search_entities(query: str, limit: int = 15) -> str:
    """Free-text search over OpenHuman's entity index.

    Args:
        query: Entity name or partial match
        limit: Max results (default 15)
    """
    result = await _rpc("openhuman.memory_tree_search_entities", {
        "query": query,
        "limit": limit,
    })
    return json.dumps(result)


# ── Subconscious ───────────────────────────────────────────────────────────────

@mcp.tool()
async def openhuman_subconscious_status() -> str:
    """Get OpenHuman's subconscious engine status.

    Returns running state, last tick time, and task counts.
    Equivalent to SEAL's reflective_diagnoses + seal_nerves status.
    """
    result = await _rpc("openhuman.subconscious_status")
    return json.dumps(result)


@mcp.tool()
async def openhuman_reflections(limit: int = 10) -> str:
    """List recent subconscious reflections from OpenHuman.

    These are auto-generated insights with proposed_action fields —
    analogous to SEAL's reflective_diagnoses.

    Args:
        limit: Max reflections to return (default 10)
    """
    result = await _rpc("openhuman.subconscious_reflections_list", {"limit": limit})
    return json.dumps(result)


@mcp.tool()
async def openhuman_subconscious_trigger() -> str:
    """Manually trigger an OpenHuman subconscious tick.

    Forces the background reflection engine to run immediately.
    """
    result = await _rpc("openhuman.subconscious_trigger")
    return json.dumps(result)


@mcp.tool()
async def openhuman_subconscious_tasks() -> str:
    """List all background subconscious tasks configured in OpenHuman."""
    result = await _rpc("openhuman.subconscious_tasks_list", {"enabled_only": False})
    return json.dumps(result)


# ── Local AI ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def openhuman_local_ai_status() -> str:
    """Get OpenHuman's local Ollama AI service status and configured models."""
    result = await _rpc("openhuman.local_ai_status")
    return json.dumps(result)


@mcp.tool()
async def openhuman_embed(inputs: list[str]) -> str:
    """Generate text embeddings using OpenHuman's local AI.

    Args:
        inputs: List of strings to embed (max ~20 per call)
    """
    result = await _rpc("openhuman.local_ai_embed", {"inputs": inputs})
    return json.dumps(result)


@mcp.tool()
async def openhuman_analyze_sentiment(message: str) -> str:
    """Classify emotion and sentiment of a message using OpenHuman's local AI.

    Returns emotion label, sentiment polarity, and confidence.

    Args:
        message: Text to classify
    """
    result = await _rpc("openhuman.local_ai_analyze_sentiment", {"message": message})
    return json.dumps(result)


# ── Memory (unified namespace) ────────────────────────────────────────────────

@mcp.tool()
async def openhuman_memory_query(namespace: str, query: str, limit: int = 10) -> str:
    """Semantic query against an OpenHuman memory namespace.

    Args:
        namespace: Memory namespace (e.g. 'default', 'work', 'personal')
        query: Natural language query
        limit: Max results (default 10)
    """
    result = await _rpc("openhuman.memory_query_namespace", {
        "namespace": namespace,
        "query": query,
        "limit": limit,
    })
    return json.dumps(result)


@mcp.tool()
async def openhuman_memory_namespaces() -> str:
    """List all memory namespaces in OpenHuman's unified memory store."""
    result = await _rpc("openhuman.memory_list_namespaces")
    return json.dumps(result)


@mcp.tool()
async def openhuman_memory_ingest(
    namespace: str,
    key: str,
    title: str,
    content: str,
    source_type: str = "manual",
    priority: str = "normal",
    category: str = "general",
    tags: Optional[list[str]] = None,
) -> str:
    """Ingest a document into OpenHuman's unified memory store.

    Triggers entity/relation extraction and chunk embedding.

    Args:
        namespace: Target namespace
        key: Unique document key
        title: Document title
        content: Document body
        source_type: 'manual', 'web', 'slack', etc.
        priority: 'critical', 'high', 'normal', 'low'
        category: Document category
        tags: Optional tag list
    """
    result = await _rpc("openhuman.memory_doc_ingest", {
        "namespace": namespace,
        "key": key,
        "title": title,
        "content": content,
        "source_type": source_type,
        "priority": priority,
        "category": category,
        "tags": tags or [],
        "metadata": "{}",
    })
    return json.dumps(result)


# ── Heartbeat ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def openhuman_heartbeat_tick() -> str:
    """Run one immediate heartbeat planner tick in OpenHuman.

    Forces a proactive notification check for meetings, reminders, and events.
    """
    result = await _rpc("openhuman.heartbeat_tick_now")
    return json.dumps(result)


@mcp.tool()
async def openhuman_heartbeat_settings() -> str:
    """Read OpenHuman's heartbeat proactive notification settings."""
    result = await _rpc("openhuman.heartbeat_settings_get")
    return json.dumps(result)


# ── Full RPC passthrough ──────────────────────────────────────────────────────

@mcp.tool()
async def openhuman_rpc(method: str, params: str = "{}") -> str:
    """Direct JSON-RPC call to OpenHuman — for methods not wrapped above.

    Use for any of the 392 methods in the API.
    See full schema at http://OPENHUMAN_HOST:7788/schema

    Args:
        method: Full method name e.g. 'openhuman.composio_list_apps'
        params: JSON string of parameters e.g. '{"limit": 10}'
    """
    try:
        params_dict = json.loads(params)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON params: {e}"})
    result = await _rpc(method, params_dict)
    return json.dumps(result)


if __name__ == "__main__":
    mcp.run()
