#!/usr/bin/env python3
"""
SEAL Studio — MCP Gateway (Fase 1)
==================================
Capa de integración que convierte al backend del Studio en un HOST/CLIENTE MCP:
agrega los tools de TODOS los MCP servers configurados (.mcp.json) en un registro
único, igual que hace Claude Code. El frontend ve un catálogo unificado y nunca
toca las credenciales de los servers (se quedan acá, server-side).

Diseñado por JARVIS — Team SEAL — 2026-06-02.

Transports soportados (vía SDK oficial `mcp`):
  - http  → streamablehttp_client  (ej. seal-memory :8771)
  - stdio → stdio_client           (filesystem, git, postgres, github, ...)

Estrategia Fase 1 (no disruptiva en server vivo):
  - Auto-probe SOLO de servers HTTP ya corriendo (SAFE_PROBE) para el catálogo live.
  - Los stdio se listan como "configured" y se conectan LAZY en el primer invoke
    (evita spawnear N procesos en cada llamada al catálogo).
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
try:
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:
    # SDK 2.x renamed the transport and moved headers to its HTTP client.
    # Keep SDK 1.x compatibility without downgrading the shared runtime.
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    @asynccontextmanager
    async def streamablehttp_client(url, *, headers=None):
        async with create_mcp_http_client(headers=headers) as client:
            async with streamable_http_client(url, http_client=client) as streams:
                yield streams
from mcp.client.stdio import stdio_client

from studio_db import DB_URL

PROJECT_ROOT = Path(os.environ.get("SEAL_PROJECT_ROOT", "/home/dadito/IA/proyecto-seal"))
MCP_CONFIG_PATH = Path(os.environ.get("SEAL_MCP_CONFIG", PROJECT_ROOT / ".mcp.json"))

# Servers que es seguro probar en vivo para el catálogo (HTTP, ya corriendo, sin spawn).
SAFE_PROBE = {"seal-memory"}

CONNECT_TIMEOUT = 12.0  # s por server


def load_config() -> dict[str, dict]:
    """Lee .mcp.json → {server_name: cfg}. Nunca lanza; devuelve {} si falla."""
    try:
        data = json.loads(MCP_CONFIG_PATH.read_text())
        return data.get("mcpServers", {}) or {}
    except Exception:
        return {}


def _transport_of(cfg: dict) -> str:
    if cfg.get("type") == "http" or cfg.get("url"):
        return "http"
    if cfg.get("command"):
        return "stdio"
    return "unknown"


class _Conn:
    """Context manager que abre una ClientSession inicializada para un server."""

    def __init__(self, name: str, cfg: dict, *, headers: dict[str, str] | None = None):
        self.name = name
        self.cfg = cfg
        self.headers = headers or {}
        self._stack: list = []

    async def __aenter__(self) -> ClientSession:
        transport = _transport_of(self.cfg)
        if transport == "http":
            self._cm = streamablehttp_client(self.cfg["url"], headers=self.headers or None)
            read, write, *_ = await self._cm.__aenter__()
        elif transport == "stdio":
            params = StdioServerParameters(
                command=self.cfg["command"],
                args=self.cfg.get("args", []),
                env={**os.environ, **(self.cfg.get("env") or {})},
            )
            self._cm = stdio_client(params)
            read, write = await self._cm.__aenter__()
        else:
            raise ValueError(f"transport desconocido para {self.name}")
        self._session_cm = ClientSession(read, write)
        session = await self._session_cm.__aenter__()
        await session.initialize()
        return session

    async def __aexit__(self, *exc):
        try:
            await self._session_cm.__aexit__(*exc)
        finally:
            await self._cm.__aexit__(*exc)


async def _list_server_tools(name: str, cfg: dict) -> list[dict]:
    """Conecta a un server y devuelve sus tools normalizados. Lanza en error."""
    async with _Conn(name, cfg) as session:
        res = await session.list_tools()
        out = []
        for t in res.tools:
            out.append({
                "id": f"{name}::{t.name}",
                "server": name,
                "name": t.name,
                "description": (t.description or "")[:400],
                "input_schema": t.inputSchema,
            })
        return out


# Cache del catálogo completo (probe_all) — evita spawnear N stdio en cada llamada.
_CATALOG_CACHE: dict = {"data": None, "ts": 0.0}
_CATALOG_TTL = 120.0  # s


async def list_catalog(probe_all: bool = False, use_cache: bool = True) -> dict:
    """
    Catálogo unificado de tools.
    - Servers en SAFE_PROBE (o todos si probe_all): se conectan en vivo → tools reales.
    - El resto: se reporta como 'configured' (live=false), se conectan lazy en invoke.
    - probe_all usa cache TTL (120s) para no spawnear los stdio repetidamente.
    """
    import time
    if probe_all and use_cache and _CATALOG_CACHE["data"] is not None:
        if (time.monotonic() - _CATALOG_CACHE["ts"]) < _CATALOG_TTL:
            return {**_CATALOG_CACHE["data"], "cached": True}

    servers = load_config()
    catalog: list[dict] = []
    server_meta: list[dict] = []

    for name, cfg in servers.items():
        transport = _transport_of(cfg)
        do_probe = probe_all or name in SAFE_PROBE
        entry = {"name": name, "transport": transport, "live": False,
                 "tool_count": 0, "error": None}
        if do_probe:
            try:
                tools = await asyncio.wait_for(_list_server_tools(name, cfg), CONNECT_TIMEOUT)
                catalog.extend(tools)
                entry["live"] = True
                entry["tool_count"] = len(tools)
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        server_meta.append(entry)

    result = {
        "servers": server_meta,
        "tools": catalog,
        "total_servers": len(servers),
        "total_tools_live": len(catalog),
    }
    if probe_all:
        import time
        _CATALOG_CACHE["data"] = result
        _CATALOG_CACHE["ts"] = time.monotonic()
    return {**result, "cached": False}


# ── Capability gates (Fase 0 — OBSERVE-ONLY) — spec NEXUS spec_soul_capability_gates_v1.md ──
INTERNAL_AGENTS = {"JARVIS", "ADA", "ALICE", "NEXUS", "DUM"}


def _seal_session_token(agent: str) -> str:
    """Read an internal agent token for seal-memory calls. Never log the value."""
    agent = (agent or "").upper()
    if agent not in INTERNAL_AGENTS:
        return ""
    token_dirs: list[Path] = []
    env_dir = os.environ.get("SEAL_TOKENS_DIR")
    if env_dir:
        token_dirs.append(Path(env_dir))
    else:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            token_dirs.append(Path(runtime_dir) / "seal")
    if os.environ.get("SEAL_DISABLE_LEGACY_TMP_TOKENS") != "1":
        token_dirs.append(Path("/tmp/seal_tokens"))

    seen: set[Path] = set()
    for token_dir in token_dirs:
        if token_dir in seen:
            continue
        seen.add(token_dir)
        try:
            token = (token_dir / f"{agent}.token").read_text(encoding="utf-8").strip()
            if token:
                return token
        except OSError:
            pass
    return os.environ.get("SEAL_SESSION_TOKEN", "").strip()


def _identity_headers(
    server: str,
    *,
    caller_id: str | None = None,
    caller_type: str | None = None,
) -> dict[str, str]:
    """Build transport authentication; secrets never enter MCP tool arguments."""
    if server != "seal-memory":
        return {}
    if caller_type == "internal_agent":
        token = _seal_session_token(str(caller_id or ""))
        if token:
            return {"Authorization": f"Bearer {token}"}
    return {}


async def check_capability(conn, server: str, tool: str, caller_id: str, caller_type: str) -> tuple[bool, str]:
    """
    Modelo deny-by-default (spec §3). Devuelve (granted, reason).
    - admin (William/Henry): allow-all.
    - agente interno SEAL: allow por defecto (configurable a futuro).
    - cliente externo (soul_core_client): DENY salvo grant activo en capability_grants.
    """
    if caller_type == "admin":
        return True, "admin allow-all"
    if caller_type == "internal_agent" or (caller_id or "").upper() in INTERNAL_AGENTS:
        return True, "internal agent default allow"
    row = await conn.fetchrow(
        "SELECT 1 FROM soul_v3.capability_grants WHERE caller_id=$1 AND server=$2 AND tool=$3 "
        "AND active=TRUE AND (expires_at IS NULL OR expires_at > NOW()) LIMIT 1",
        caller_id, server, tool)
    if row:
        return True, "active grant"
    return False, "no active grant (deny-by-default)"


async def write_audit(conn, *, caller_id, caller_type, server, tool, granted, reason, latency_ms, status) -> None:
    """Audit obligatorio (spec §4) — toda invocación (pass o deny) queda registrada."""
    try:
        await conn.execute(
            "INSERT INTO soul_v3.smg_audit_log "
            "(ts, agent, method, path, status, latency_ms, backend, caller_id, caller_type, granted, reason) "
            "VALUES (NOW(), $1, 'POST', $2, $3, $4, 'mcp_gateway', $5, $6, $7, $8)",
            caller_id or "unknown", f"/v1/tools/{server}/{tool}/invoke", status, latency_ms,
            caller_id, caller_type, granted, reason)
    except Exception:
        pass  # el audit nunca debe romper la invocación


async def invoke(
    server: str,
    tool: str,
    arguments: Optional[dict] = None,
    *,
    caller_id: str | None = None,
    caller_type: str | None = None,
) -> dict:
    """Invoca un tool de un server (conexión on-demand). Las credenciales quedan acá."""
    servers = load_config()
    if server not in servers:
        return {"ok": False, "error": f"server desconocido: {server}"}
    cfg = servers[server]
    call_args = dict(arguments or {})
    identity_headers = _identity_headers(
        server,
        caller_id=caller_id,
        caller_type=caller_type,
    )
    try:
        async with _Conn(server, cfg, headers=identity_headers) as session:
            result = await asyncio.wait_for(
                session.call_tool(tool, call_args), CONNECT_TIMEOUT)
        # Normaliza el contenido (texto + structured).
        parts = []
        for c in (result.content or []):
            if getattr(c, "type", None) == "text":
                parts.append(c.text)
            else:
                parts.append(str(getattr(c, "data", c)))
        return {
            "ok": not bool(getattr(result, "isError", False)),
            "server": server, "tool": tool,
            "content": "\n".join(parts),
            "structured": getattr(result, "structuredContent", None),
        }
    except Exception as e:
        return {"ok": False, "server": server, "tool": tool,
                "error": f"{type(e).__name__}: {str(e)[:200]}"}


if __name__ == "__main__":
    # Smoke test manual: lista catálogo (probe seal-memory) + cuenta.
    async def _main():
        cat = await list_catalog()
        print(f"servers={cat['total_servers']} tools_live={cat['total_tools_live']}")
        for s in cat["servers"]:
            print(f"  {s['name']:14} {s['transport']:6} live={s['live']} "
                  f"tools={s['tool_count']} err={s['error']}")
    asyncio.run(_main())
