"""SOUL MCP Gateway — Native Python Implementation.

Reduces token consumption by ~92%:
  Before: 112 tools × ~200 tokens = ~22,400 tokens per session
  After:  8 direct tools + 1 gateway tool = ~1,800 tokens per session

No Node.js. No external adapters. Pure Python.
"inteligencia artificial = python puro" — William, 2026-04-28.
"""
from __future__ import annotations

import json
import logging
from typing import Any

LOG = logging.getLogger("seal-memory")

# Tools exposed directly — high-frequency, latency-sensitive
DIRECT_TOOLS = {
    "memory_store",
    "memory_hybrid_search",
    "boot_context",
    "active_recall",
    "working_state_get",
    "working_state_update",
    "soul_snapshot",
    "self_reflect",
}


class SOULGateway:
    """In-process MCP gateway. Wraps a FastMCP instance to provide
    a single discovery+execution interface for all non-direct tools.
    """

    def __init__(self, mcp_instance):
        self.mcp = mcp_instance
        self._tool_cache: list | None = None

    def invalidate_cache(self) -> None:
        self._tool_cache = None

    async def _get_tools(self) -> list:
        if self._tool_cache is None:
            self._tool_cache = await self.mcp.list_tools()
        return self._tool_cache

    def _format_schema(self, schema: dict, indent: str = "  ") -> str:
        props = schema.get("properties", {})
        required = schema.get("required", [])
        if not props:
            return f"{indent}(no parameters)"
        lines = []
        for name, prop in props.items():
            req = " [required]" if name in required else ""
            ptype = prop.get("type", "any")
            desc = prop.get("description", "")
            line = f"{indent}{name}: {ptype}{req}"
            if desc:
                line += f" — {desc[:70]}"
            lines.append(line)
        return "\n".join(lines)

    async def status(self) -> str:
        tools = await self._get_tools()
        direct = [t for t in tools if t.name in DIRECT_TOOLS]
        gateway_tools = [t for t in tools if t.name not in DIRECT_TOOLS and t.name != "soul_gateway"]
        return (
            f"SOUL Memory: {len(tools)} tools total\n"
            f"  • {len(direct)} direct (always in context)\n"
            f"  • {len(gateway_tools)} via soul_gateway\n\n"
            "soul_gateway(server='seal-memory')    → list all gateway tools\n"
            "soul_gateway(search='query')          → search by name/description\n"
            "soul_gateway(describe='tool_name')    → show full parameter schema\n"
            "soul_gateway(tool='name', args={...}) → execute any tool\n"
        )

    async def list_server(self, server: str) -> str:
        tools = await self._get_tools()
        gateway_tools = [t for t in tools if t.name not in DIRECT_TOOLS and t.name != "soul_gateway"]
        lines = [f"{server} — {len(gateway_tools)} tools via gateway:\n"]
        for t in gateway_tools:
            desc = (t.description or "")
            short = desc[:65] + "..." if len(desc) > 65 else desc
            lines.append(f"  {t.name}" + (f" — {short}" if short else ""))
        lines.append(f"\n(+ {len(DIRECT_TOOLS)} direct tools: {', '.join(sorted(DIRECT_TOOLS))})")
        return "\n".join(lines)

    async def search(self, query: str) -> str:
        tools = await self._get_tools()
        terms = query.lower().split()
        matches = [
            t for t in tools
            if t.name != "soul_gateway" and any(
                term in t.name.lower() or term in (t.description or "").lower()
                for term in terms
            )
        ]
        if not matches:
            return (
                f"No tools matching '{query}'.\n"
                "Try soul_gateway(server='seal-memory') to browse all tools."
            )
        lines = [f"Found {len(matches)} tool(s) matching '{query}':\n"]
        for t in matches:
            marker = " [direct]" if t.name in DIRECT_TOOLS else ""
            lines.append(f"  {t.name}{marker}")
            if t.description:
                lines.append(f"    {t.description[:80]}")
        return "\n".join(lines)

    async def describe(self, tool_name: str) -> str:
        tools = await self._get_tools()
        found = next((t for t in tools if t.name == tool_name), None)
        if not found:
            return f"Tool '{tool_name}' not found. Try soul_gateway(search='{tool_name}')."
        marker = " [direct — call without gateway]" if tool_name in DIRECT_TOOLS else ""
        lines = [
            f"{found.name}{marker}",
            f"  {found.description or '(no description)'}",
            "",
            "Parameters:",
            self._format_schema(found.inputSchema or {}),
        ]
        return "\n".join(lines)

    async def call(self, tool_name: str, args: dict) -> str:
        if tool_name == "soul_gateway":
            return "Cannot call soul_gateway via itself. Use direct mode: soul_gateway(tool='other_tool', args={...})"

        tools = await self._get_tools()
        found = next((t for t in tools if t.name == tool_name), None)
        if not found:
            return (
                f"Tool '{tool_name}' not found.\n"
                f"Try: soul_gateway(search='{tool_name.split('_')[0]}')"
            )

        try:
            result = await self.mcp.call_tool(tool_name, args)
            if isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False)
            parts = []
            for block in result:
                if hasattr(block, "text"):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return "\n".join(parts) if parts else "(empty result)"
        except Exception as e:
            hint = ""
            schema = found.inputSchema or {}
            if schema.get("properties"):
                props = list(schema["properties"].keys())
                hint = f"\nExpected parameters: {', '.join(props)}"
            return f"Error calling '{tool_name}': {e}{hint}"

    async def gateway(
        self,
        server: str | None = None,
        search: str | None = None,
        describe: str | None = None,
        tool: str | None = None,
        args: dict | None = None,
    ) -> str:
        if tool is not None:
            return await self.call(tool, args or {})
        if describe is not None:
            return await self.describe(describe)
        if search is not None:
            return await self.search(search)
        if server is not None:
            return await self.list_server(server)
        return await self.status()


_gateway_instance: SOULGateway | None = None


def register_gateway(mcp_instance) -> SOULGateway:
    """Register soul_gateway as a FastMCP tool.

    Call once from mcp_server_v3.py after `mcp = FastMCP(...)`:
        from soul_gateway import register_gateway
        register_gateway(mcp)
    """
    global _gateway_instance
    gw = SOULGateway(mcp_instance)
    _gateway_instance = gw

    @mcp_instance.tool()
    async def soul_gateway(
        server: str | None = None,
        search: str | None = None,
        describe: str | None = None,
        tool: str | None = None,
        args: dict | None = None,
    ) -> str:
        """Token-efficient gateway to SOUL tools not exposed directly.

        soul_gateway()                              → status + usage
        soul_gateway(server="seal-memory")          → list all gateway tools
        soul_gateway(search="diary write")          → find tools by keyword
        soul_gateway(describe="diary_write")        → full schema of a tool
        soul_gateway(tool="diary_write",            → execute any SOUL tool
                     args={"content": "...",
                            "mood": "curious"})

        Direct tools (call without gateway — always in context):
          memory_store, memory_hybrid_search, boot_context, active_recall,
          working_state_get, working_state_update, soul_snapshot, self_reflect
        """
        return await gw.gateway(
            server=server, search=search, describe=describe, tool=tool, args=args
        )

    LOG.info("soul_gateway registered — %d direct tools + 1 gateway", len(DIRECT_TOOLS))
    return gw
