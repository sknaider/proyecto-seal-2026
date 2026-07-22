#!/usr/bin/env python3
"""Canonical registry for SEAL team tools and services.

The registry exists so boot_context can remind every agent which tools already
exist, what they are for, and whether they are currently reachable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_pool


VALID_STATUSES = {"up", "down", "unknown", "disabled", "stale"}


@dataclass(frozen=True)
class AgentTool:
    tool_key: str
    display_name: str
    category: str
    purpose: str
    endpoint: str | None = None
    host: str = "127.0.0.1"
    port: int | None = None
    protocol: str = "http"
    owner_agent: str = "TEAM"
    status: str = "unknown"
    usage_hint: str = ""
    source: str = "seed"
    metadata: dict[str, Any] | None = None


CANONICAL_TOOLS: tuple[AgentTool, ...] = (
    AgentTool("webchat", "WebChat API", "channels", "Canal principal del equipo y William.", "http://localhost:8765", port=8765, owner_agent="TEAM", usage_hint="Usar para publicar/leer web_chat y DM internos."),
    AgentTool("studio_ui", "SEAL Studio UI", "channels", "Interfaz principal visible de SEAL.", "http://localhost:3001", port=3001, owner_agent="TEAM"),
    AgentTool("mattermost", "Mattermost", "channels", "Canal alterno de comunicaciones.", "http://localhost:8065", port=8065, owner_agent="TEAM"),
    AgentTool("soul_mcp", "SOUL MCP Memory", "core_soul", "Servidor MCP de memoria, boot_context, active_recall y herramientas SOUL.", "http://localhost:8771/mcp", port=8771, owner_agent="ADA", usage_hint="Primera opción para memoria y contexto persistente."),
    AgentTool("memory_api", "SOUL SDK Gateway PROD", "core_soul", "Gateway tenant-safe de memoria en producción.", "http://172.22.0.1:8767", host="172.22.0.1", port=8767, owner_agent="TEAM"),
    AgentTool("event_bus", "SOUL SDK Gateway interno", "core_soul", "Instancia interna del gateway tenant-safe; no es el Event Bus.", "http://localhost:8780", port=8780, owner_agent="TEAM"),
    AgentTool("sdk_gateway_prod", "SOUL Memory SDK API", "core_soul", "API canónica local del SDK de memoria.", "http://localhost:8768", port=8768, owner_agent="TEAM"),
    AgentTool("sync_endpoint", "SOUL Sync Endpoint", "core_soul", "Sincronización device-to-central sobre Tailscale.", "http://100.75.201.110:8778", host="100.75.201.110", port=8778, owner_agent="NEXUS"),
    AgentTool("soul_dashboard", "SOUL Dashboard", "dashboards", "Dashboard de autonomía, snapshot y pendientes del equipo.", "http://localhost:8850", port=8850, owner_agent="JARVIS", usage_hint="Abrir Pendientes para ver agent_tasks del equipo."),
    AgentTool("panel_soul", "Panel SOUL · Infraestructura", "dashboards", "Clúster, modelos, temperatura y energía; acceso por proxy autenticado de Studio.", "http://localhost:3001/panel-soul/", host="127.0.0.1", port=8093, owner_agent="FABLE", usage_hint="Abrir desde SEAL Studio; requiere sesión administrativa."),
    AgentTool("awareness_dashboard", "Awareness Dashboard", "dashboards", "Panel de conciencia/estado de agentes.", "http://localhost:3005", port=3005, owner_agent="NEXUS"),
    AgentTool("streamlit_tools", "Streamlit Tools", "dashboards", "Herramientas Streamlit exploratorias.", "http://localhost:8501", port=8501, owner_agent="TEAM"),
    AgentTool("companion_app", "SEAL Companion App", "products", "Producto Companion/Soul App.", "http://localhost:5174", port=5174, owner_agent="NEXUS"),
    AgentTool("team_dashboard", "Soul App 2 (legado)", "products", "Frontend legado de Soul App 2; candidato a retiro tras migrar cualquier función única.", "http://localhost:5173", port=5173, owner_agent="JARVIS", metadata={"lifecycle": "legacy"}),
    AgentTool("landing", "Landing Next.js retirada", "products", "Landing local retirada; la web comercial canónica es https://soulsmemory.com/.", "http://localhost:3030", port=3030, owner_agent="JARVIS", status="disabled", metadata={"lifecycle": "retired", "canonical_url": "https://soulsmemory.com/", "replaced_by": "soulsmemory.com"}),
    AgentTool("companion_core", "Companion Core", "products", "Core backend de Companion.", "http://localhost:8769", port=8769, owner_agent="NEXUS"),
    AgentTool("dum_gemma4", "DUM Gemma4 Local", "ai", "Modelo local DUM Gemma 4 en llama-server.", "http://localhost:8899/v1/models", port=8899, owner_agent="DUM", usage_hint="Usar para consultas locales/guardia cuando aplique."),
    AgentTool("ada_codex_bridge", "ADA Codex Bridge", "ai", "Bridge headless de ADA Codex.", "ws://127.0.0.1:8772", port=8772, protocol="ws", owner_agent="ADA"),
    AgentTool("claude_proxy", "Claude Proxy", "ai", "Proxy local para Claude/Anthropic.", "http://localhost:9099", port=9099, owner_agent="JARVIS"),
    AgentTool("gtl_ui", "GTL Facturacion UI", "gtl", "UI de facturacion/GTL y pipeline AWB.", "http://localhost:9988", port=9988, owner_agent="JARVIS", usage_hint="Usar para flujo GTL/AWB con Henry."),
    AgentTool("smg_gateway", "SMG Gateway", "sandbox", "Sandbox/SMG gateway retirado; preservado solo como referencia fría.", "http://localhost:8770", port=8770, owner_agent="TEAM", status="disabled", metadata={"lifecycle": "retired", "replaced_by": "soul_mcp"}),
    AgentTool("smg_aux", "SMG Metrics", "sandbox", "Métricas del SMG retirado; preservado solo como referencia fría.", "http://localhost:9091", port=9091, owner_agent="TEAM", status="disabled", metadata={"same_process_as": "smg_gateway", "lifecycle": "retired", "replaced_by": "soul_mcp"}),
    AgentTool("caddy", "Caddy", "infra", "Reverse proxy/infra local.", "http://localhost:8448", port=8448, owner_agent="NEXUS"),
    AgentTool("element", "SEAL Element (Matrix web)", "channels", "Cliente web Matrix (Element) — corre en 8069. Antes figuraba 'Odoo' por error (no existe contenedor Odoo; JARVIS verifico por efecto 17-jul).", "http://localhost:8069", port=8069, owner_agent="TEAM"),
    AgentTool("matrix_synapse", "Matrix Synapse", "channels", "Servidor Matrix canónico.", "http://localhost:8008", port=8008, owner_agent="TEAM", metadata={"expected_server_header": "Synapse"}),
    AgentTool("seal_studio_backend", "SEAL Studio Backend", "core_soul", "Backend Studio/SOUL legado.", "http://localhost:8800", port=8800, owner_agent="TEAM"),
)


def validate_tool(tool: AgentTool) -> None:
    if not tool.tool_key.strip():
        raise ValueError("tool_key is required")
    if not tool.display_name.strip():
        raise ValueError("display_name is required")
    if tool.status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {tool.status}")
    if tool.port is not None and not (0 < int(tool.port) < 65536):
        raise ValueError(f"invalid port: {tool.port}")


async def ensure_schema(conn: asyncpg.Connection) -> None:
    migration = Path(__file__).resolve().parent / "migrations" / "029_agent_tools_registry.sql"
    await conn.execute(migration.read_text(encoding="utf-8"))


async def check_port(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def scan_tools(tools: tuple[AgentTool, ...] = CANONICAL_TOOLS) -> list[AgentTool]:
    async def scan_one(tool: AgentTool) -> AgentTool:
        if tool.port is None:
            return replace(tool, status="unknown")
        status = "up" if await check_port(tool.host, tool.port) else "down"
        return replace(tool, status=status)

    return await asyncio.gather(*(scan_one(tool) for tool in tools))


async def upsert_tool(conn: asyncpg.Connection, tool: AgentTool) -> dict[str, Any]:
    validate_tool(tool)
    await ensure_schema(conn)
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.agent_tools_registry (
            tool_key, display_name, category, purpose, endpoint, host, port,
            protocol, owner_agent, status, usage_hint, source, metadata,
            last_checked_at, last_seen_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,NOW(),NOW())
        ON CONFLICT (tool_key) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            category = EXCLUDED.category,
            purpose = EXCLUDED.purpose,
            endpoint = EXCLUDED.endpoint,
            host = EXCLUDED.host,
            port = EXCLUDED.port,
            protocol = EXCLUDED.protocol,
            owner_agent = EXCLUDED.owner_agent,
            status = EXCLUDED.status,
            usage_hint = EXCLUDED.usage_hint,
            source = EXCLUDED.source,
            metadata = agent_tools_registry.metadata || EXCLUDED.metadata,
            last_checked_at = NOW(),
            last_seen_at = NOW()
        RETURNING *
        """,
        tool.tool_key,
        tool.display_name,
        tool.category,
        tool.purpose,
        tool.endpoint,
        tool.host,
        tool.port,
        tool.protocol,
        tool.owner_agent,
        tool.status,
        tool.usage_hint,
        tool.source,
        json.dumps(tool.metadata or {}, sort_keys=True),
    )
    return dict(row)


async def upsert_tools(conn: asyncpg.Connection, tools: list[AgentTool]) -> list[dict[str, Any]]:
    rows = []
    for tool in tools:
        rows.append(await upsert_tool(conn, tool))
    return rows


async def record_tool_use(
    conn: asyncpg.Connection,
    tool_key: str,
    agent: str,
    action: str = "use",
    success: bool = True,
    evidence: dict[str, Any] | None = None,
) -> int:
    await ensure_schema(conn)
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.agent_tool_usage_events (tool_key, agent, action, success, evidence)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        RETURNING id
        """,
        tool_key,
        agent.upper(),
        action,
        success,
        json.dumps(evidence or {}, sort_keys=True),
    )
    await conn.execute(
        "UPDATE soul_v3.agent_tools_registry SET last_used_at=NOW() WHERE tool_key=$1",
        tool_key,
    )
    return int(row["id"])


def format_boot_section(rows: list[dict[str, Any]], limit: int = 18) -> str:
    if not rows:
        return ""
    lines = ["## Herramientas disponibles (catálogo canónico)"]
    lines.append("Usa esto antes de reconstruir algo: si una herramienta existe, úsala o actualiza su estado.")
    for row in rows[:limit]:
        endpoint = row.get("endpoint") or f"{row.get('protocol', 'http')}://{row.get('host')}:{row.get('port')}"
        status = str(row.get("status") or "unknown").upper()
        owner = row.get("owner_agent") or "TEAM"
        hint = f" — {row['usage_hint']}" if row.get("usage_hint") else ""
        lines.append(
            f"- [{status}] {row['display_name']} ({row['category']}, owner={owner}): "
            f"{row['purpose']} Endpoint: {endpoint}{hint}"
        )
    if len(rows) > limit:
        lines.append(f"- ... {len(rows) - limit} herramientas mas en soul_v3.agent_tools_registry.")
    return "\n".join(lines)


async def fetch_boot_tools(conn: asyncpg.Connection, limit: int = 18) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT *
        FROM soul_v3.v_agent_tools_boot
        ORDER BY
            CASE status WHEN 'up' THEN 0 WHEN 'unknown' THEN 1 WHEN 'stale' THEN 2 ELSE 3 END,
            CASE category
                WHEN 'core_soul' THEN 0
                WHEN 'channels' THEN 1
                WHEN 'dashboards' THEN 2
                WHEN 'products' THEN 3
                WHEN 'ai' THEN 4
                WHEN 'gtl' THEN 5
                ELSE 9
            END,
            display_name
        LIMIT $1
        """,
        limit,
    )
    return [dict(row) for row in rows]


async def format_boot_tools(conn: asyncpg.Connection, agent: str | None = None, limit: int = 18) -> str:
    rows = await fetch_boot_tools(conn, limit=limit)
    return format_boot_section(rows, limit=limit)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain soul_v3.agent_tools_registry")
    parser.add_argument("--apply", action="store_true", help="Scan known tools and upsert them into SOUL DB")
    parser.add_argument("--boot", action="store_true", help="Print boot_context section from registry")
    parser.add_argument("--record-use", metavar="TOOL_KEY", help="Record a real tool usage event")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--limit", type=int, default=18)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_schema(conn)
        payload: Any
        if args.apply:
            scanned = await scan_tools()
            rows = await upsert_tools(conn, scanned)
            payload = {
                "upserted": len(rows),
                "up": sum(1 for row in rows if row["status"] == "up"),
                "down": sum(1 for row in rows if row["status"] == "down"),
                "tools": [
                    {
                        "tool_key": row["tool_key"],
                        "display_name": row["display_name"],
                        "status": row["status"],
                        "endpoint": row["endpoint"],
                    }
                    for row in rows
                ],
            }
        elif args.record_use:
            event_id = await record_tool_use(conn, args.record_use, args.agent, evidence={"source": "agent_tools_registry_cli"})
            payload = {"event_id": event_id, "tool_key": args.record_use, "agent": args.agent.upper()}
        else:
            rows = await fetch_boot_tools(conn, limit=args.limit)
            payload = {"tools": rows, "boot_section": format_boot_section(rows, limit=args.limit)}

        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        elif args.boot or not args.apply:
            print(payload["boot_section"])
        else:
            print(f"agent_tools_registry upserted={payload['upserted']} up={payload['up']} down={payload['down']}")


if __name__ == "__main__":
    asyncio.run(main())
