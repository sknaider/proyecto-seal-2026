# SPEC: SOUL MCP Gateway — Native Python Port
**Autor:** NEXUS | **Fecha:** 2026-04-28 | **Basado en:** pi-mcp-adapter v2.5.1 (TypeScript → Python)
**Regla de oro aplicada:** Inteligencia artificial = Python puro. Sin Node.js. Sin dependencias externas evitables.

---

## PROBLEMA QUE RESUELVE

SOUL expone actualmente **112 herramientas MCP individuales** al LLM. Cada herramienta requiere ~200 tokens de schema.
- 112 tools × 200 tokens = **22,400 tokens** solo en schemas por sesión
- Contexto disponible para razonamiento: reducido
- Descubrimiento de herramientas: inexistente (el LLM no sabe qué existe sin buscar)

**JARVIS spec** (26-abr-2026): propuso 8 directas + 1 gateway = **88% reducción de tokens**
**pi-mcp-adapter**: implementa este patrón en TypeScript para el framework Pi
**Este spec**: implementa el mismo patrón en Python nativo para SOUL

---

## ARQUITECTURA

```
LLM
 │
 ├── [8 herramientas directas] ← siempre en contexto, 1,600 tokens total
 │    memory_store, memory_hybrid_search, boot_context, active_recall,
 │    working_state_get, working_state_update, soul_snapshot, self_reflect
 │
 └── soul_gateway(**kwargs) ← 1 herramienta, ~200 tokens
      │
      ├── soul_gateway()                         → status: todos los servidores
      ├── soul_gateway(server="seal-memory")     → lista herramientas del servidor
      ├── soul_gateway(search="diary write")     → búsqueda semántica de herramientas
      ├── soul_gateway(describe="diary_write")   → schema completo de una herramienta
      └── soul_gateway(tool="diary_write",       → ejecución
                       args={"content": "..."})
```

### Capa de caché (JSON)
```
~/.seal/mcp_tool_cache.json
├── version: "1.0"
├── generated_at: ISO timestamp
├── ttl_seconds: 604800  # 7 días
└── servers:
    ├── seal-memory:
    │   ├── config_hash: sha256(server_config)
    │   └── tools: [{name, description, inputSchema}, ...]
    └── (otros servidores MCP si se agregan)
```

---

## IMPLEMENTACIÓN

### Archivo: `memory/soul_gateway.py`

```python
"""SOUL MCP Gateway — Native Python Implementation.
Inspired by pi-mcp-adapter (TypeScript) — ported natively without Node.js.
Exposes 1 gateway tool instead of N individual MCP tools.
Token reduction: ~85% per session.

William, 2026-04-28: "inteligencia artificial = python puro"
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx  # ya en requirements (MCP SSE client)

CACHE_PATH = Path.home() / ".seal" / "mcp_tool_cache.json"
CACHE_TTL = 7 * 24 * 3600  # 7 días
MCP_SSE_URL = "http://localhost:8766/sse"  # seal-memory SSE endpoint


# ──────────────────────────────────────────────
# CACHE
# ──────────────────────────────────────────────

def _compute_config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text())
        age = time.time() - data.get("generated_at", 0)
        if age > CACHE_TTL:
            return {}
        return data.get("servers", {})
    except Exception:
        return {}


def save_cache(servers: dict[str, list[dict]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({
        "version": "1.0",
        "generated_at": time.time(),
        "ttl_seconds": CACHE_TTL,
        "servers": servers,
    }, indent=2, ensure_ascii=False))


# ──────────────────────────────────────────────
# MCP CONNECTION (SSE — ya existe en seal-memory)
# ──────────────────────────────────────────────

async def _call_mcp_tool(tool_name: str, args: dict) -> str:
    """Call a tool on the running seal-memory MCP server via SSE."""
    # El MCP server ya corre en puerto 8766 como daemon systemd.
    # Usamos el cliente MCP de Python para llamarlo.
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(MCP_SSE_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)
            if result.content:
                return "\n".join(
                    block.text for block in result.content
                    if hasattr(block, "text")
                )
            return "(empty result)"


async def _list_mcp_tools() -> list[dict]:
    """List all tools from the running seal-memory MCP server."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(MCP_SSE_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema or {},
                }
                for t in tools.tools
            ]


# ──────────────────────────────────────────────
# METADATA (con caché)
# ──────────────────────────────────────────────

_metadata_cache: dict[str, list[dict]] = {}  # in-memory


async def get_metadata(server: str = "seal-memory") -> list[dict]:
    if server in _metadata_cache:
        return _metadata_cache[server]

    # Try disk cache first
    disk = load_cache()
    if server in disk and disk[server]:
        _metadata_cache[server] = disk[server]
        return disk[server]

    # Fetch live
    tools = await _list_mcp_tools()
    _metadata_cache[server] = tools
    save_cache({**disk, server: tools})
    return tools


# ──────────────────────────────────────────────
# GATEWAY MODES (mirror of proxy-modes.ts)
# ──────────────────────────────────────────────

async def _mode_status() -> str:
    tools = await get_metadata()
    return (
        f"seal-memory: {len(tools)} tools available\n\n"
        "Usage:\n"
        "  soul_gateway(server='seal-memory')     → list all tools\n"
        "  soul_gateway(search='memory')          → search tools\n"
        "  soul_gateway(describe='tool_name')     → show schema\n"
        "  soul_gateway(tool='name', args={...})  → execute\n"
    )


async def _mode_list(server: str) -> str:
    tools = await get_metadata(server)
    if not tools:
        return f"No tools found on '{server}'"
    lines = [f"{server} ({len(tools)} tools):\n"]
    for t in tools:
        desc = t["description"][:60] + "..." if len(t["description"]) > 60 else t["description"]
        lines.append(f"  - {t['name']}" + (f" — {desc}" if desc else ""))
    return "\n".join(lines)


async def _mode_search(query: str, server: str = "seal-memory") -> str:
    tools = await get_metadata(server)
    terms = query.lower().split()
    matches = [
        t for t in tools
        if any(term in t["name"].lower() or term in t["description"].lower() for term in terms)
    ]
    if not matches:
        return f"No tools matching '{query}'. Try soul_gateway(server='seal-memory') to list all."
    lines = [f"Found {len(matches)} tool(s) matching '{query}':\n"]
    for t in matches:
        lines.append(f"  {t['name']}")
        lines.append(f"    {t['description'][:80]}")
    return "\n".join(lines)


async def _mode_describe(tool_name: str) -> str:
    tools = await get_metadata()
    found = next((t for t in tools if t["name"] == tool_name), None)
    if not found:
        return f"Tool '{tool_name}' not found. Try soul_gateway(search='{tool_name}')."
    schema = found.get("inputSchema", {})
    props = schema.get("properties", {})
    required = schema.get("required", [])
    lines = [
        f"{found['name']}",
        f"  {found['description']}",
        "",
        "Parameters:",
    ]
    if not props:
        lines.append("  (none)")
    for name, prop in props.items():
        req = " [required]" if name in required else ""
        ptype = prop.get("type", "any")
        desc = prop.get("description", "")
        lines.append(f"  {name}: {ptype}{req}" + (f" — {desc}" if desc else ""))
    return "\n".join(lines)


async def _mode_call(tool_name: str, args: dict) -> str:
    # Validate tool exists before calling
    tools = await get_metadata()
    found = next((t for t in tools if t["name"] == tool_name), None)
    if not found:
        return (
            f"Tool '{tool_name}' not found.\n"
            f"Use soul_gateway(search='{tool_name}') to find similar tools."
        )
    try:
        return await _call_mcp_tool(tool_name, args)
    except Exception as e:
        schema_hint = ""
        if found.get("inputSchema", {}).get("properties"):
            props = found["inputSchema"]["properties"]
            schema_hint = f"\nExpected parameters: {', '.join(props.keys())}"
        return f"Error calling '{tool_name}': {e}{schema_hint}"


# ──────────────────────────────────────────────
# PUBLIC API — the ONE tool exposed to the LLM
# ──────────────────────────────────────────────

async def soul_gateway(
    server: str | None = None,
    search: str | None = None,
    describe: str | None = None,
    tool: str | None = None,
    args: dict | None = None,
) -> str:
    """
    Gateway to all 104 SOUL tools not exposed directly.
    
    Usage:
      soul_gateway()                              → show servers + usage
      soul_gateway(server="seal-memory")          → list all tools
      soul_gateway(search="diary write")          → search tools by name/description
      soul_gateway(describe="diary_write")        → show full schema
      soul_gateway(tool="diary_write",            → execute tool
                   args={"content": "...",
                          "mood": "curious"})
    """
    if tool is not None:
        return await _mode_call(tool, args or {})
    if describe is not None:
        return await _mode_describe(describe)
    if search is not None:
        return await _mode_search(search, server or "seal-memory")
    if server is not None:
        return await _mode_list(server)
    return await _mode_status()


# ──────────────────────────────────────────────
# FASTMCP REGISTRATION (integración con mcp_server_v2.py)
# ──────────────────────────────────────────────

def register_gateway(mcp_instance):
    """Register soul_gateway as a FastMCP tool.
    
    Call from mcp_server_v2.py:
        from soul_gateway import register_gateway
        register_gateway(mcp)
    """
    @mcp_instance.tool()
    async def soul_gateway_tool(
        server: str = None,
        search: str = None,
        describe: str = None,
        tool: str = None,
        args: dict = None,
    ) -> str:
        """
        Gateway to all SOUL tools not exposed directly.
        soul_gateway()                        → list servers
        soul_gateway(server="seal-memory")    → list tools
        soul_gateway(search="query")          → find tools
        soul_gateway(describe="tool_name")    → show schema
        soul_gateway(tool="name", args={})    → execute tool
        """
        return await soul_gateway(server=server, search=search,
                                  describe=describe, tool=tool, args=args)
```

---

## INTEGRACIÓN EN mcp_server_v2.py

Agregar al final de las importaciones (después de `LOG = logging.getLogger("seal-memory")`):

```python
# SOUL Gateway — expone 104 tools como 1 (nativo Python, sin Node.js)
try:
    from soul_gateway import register_gateway as _register_soul_gateway
    _SOUL_GATEWAY_AVAILABLE = True
except ImportError:
    _SOUL_GATEWAY_AVAILABLE = False
```

Y después de inicializar `mcp`:

```python
if _SOUL_GATEWAY_AVAILABLE:
    _register_soul_gateway(mcp)
    LOG.info("soul_gateway registered — 104 tools accessible via 1 gateway")
```

---

## HERRAMIENTAS DIRECTAS (no van por gateway)

Las 8 herramientas más usadas siguen expuestas individualmente:

| Herramienta | Razón |
|---|---|
| `memory_store` | Uso frecuente, latencia crítica |
| `memory_hybrid_search` | Núcleo del sistema |
| `boot_context` | Siempre al boot |
| `active_recall` | Siempre al boot |
| `working_state_get` | Checkpoints frecuentes |
| `working_state_update` | Checkpoints frecuentes |
| `soul_snapshot` | Snapshot de identidad |
| `self_reflect` | Registro emocional |

---

## REDUCCIÓN DE TOKENS

| Escenario | Tokens tools en contexto |
|---|---|
| Hoy (112 tools) | ~22,400 tokens |
| Con gateway (8 directas + 1 gateway) | ~1,800 tokens |
| **Reducción** | **~92%** |

---

## DEPENDENCIAS

```
mcp>=1.0.0          # ya instalado (seal-memory lo requiere)
httpx>=0.24.0       # ya instalado
# NADA MÁS — 100% Python nativo
```

No se usa Node.js. No se usa el repo pi-mcp-adapter. El patrón se reimplementó nativamente.

---

## CHECKLIST DE IMPLEMENTACIÓN (para ADA)

- [ ] Crear `/home/dadito/IA/proyecto-seal/memory/soul_gateway.py`
- [ ] Agregar integración en `mcp_server_v2.py` (2 bloques de código)
- [ ] Crear `~/.seal/` directorio para cache
- [ ] Test: `python3 -c "import asyncio; from memory.soul_gateway import soul_gateway; print(asyncio.run(soul_gateway()))"`
- [ ] Test: `soul_gateway(search="memory")` — debe retornar lista de tools
- [ ] Test: `soul_gateway(tool="ping")` — debe ejecutar correctamente
- [ ] Reiniciar seal-memory service
- [ ] Verificar en log que `soul_gateway registered` aparece
- [ ] Reportar a William: token reduction validada

---

## ROLLBACK

Eliminar las 4 líneas agregadas en `mcp_server_v2.py`. El gateway no modifica los tools existentes.

---

*NEXUS | 2026-04-28 | Port nativo Python del patrón pi-mcp-adapter — sin Node.js, sin dependencias externas*
*"inteligencia artificial = python puro" — William, 28-abr-2026*
