---
name: seal-mcp-tool-authoring
description: Use when adding, modifying, or debugging tools in the SEAL MCP memory server.
version: 1.0.0
author: SEAL
license: MIT
metadata:
  soul:
    tags: [seal-mcp-tool-authoring, soul]
---

# seal-mcp-tool-authoring

Use this skill when adding, modifying, or debugging MCP tools in the SEAL memory server.

## Where to work

All MCP tools live in `/home/dadito/IA/proyecto-seal/memory/mcp_server_v4.py`.
The compatibility shim `mcp_server_v3.py` re-exports from v4 — never edit v3 directly.

## Anatomy of a SEAL MCP Tool

```python
@mcp.tool()
async def my_tool_name(agent: str, arg1: str, arg2: int = 0) -> str:
    """One-line description for the MCP tool registry."""
    # 1. Input validation (only at boundaries)
    if not arg1 or not arg1.strip():
        return json.dumps({"error": "arg1 required"})

    # 2. DB operation via asyncpg pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetch("SELECT ...", arg1)

    # 3. Return JSON string (never raise, return error key on failure)
    return json.dumps({"result": [dict(r) for r in result]})
```

**Key facts:**
- `@mcp.tool()` is wrapped by `_observed_tool()`; locate the assignment with
  `rg -n 'mcp\.tool = _observed_tool' memory/mcp_server_v4.py` instead of relying
  on a stale line number. Rate limiting, SMG audit, and `_observe()` are automatic.
- Match the established ABI of the tool family being extended. Most tools in
  this server return `str` (often JSON via `_safe_dumps`/`json.dumps`), while a
  small set intentionally returns typed `dict` payloads. Do not silently change
  an existing family's wire shape.
- Acquire the runtime-scoped pool with `pool = await get_pool()` and then
  `async with pool.acquire()`. This preserves the least-privilege/RLS wrapper;
  do not create an independent connection or assume a global `_pool`.

## Rate limiting

Rate limiting is **automatic** via `_observed_tool` wrapper. Default: `_RATE_LIMIT_DEFAULT` (60 req/min).
To add a custom limit: `_RATE_LIMITS_OVERRIDE["my_tool_name"] = 30`  (integer = max calls per 60s).

## Private symbols to export in mcp_server_v3.py shim

When adding new private functions (`_my_func`), add them to the explicit import list
in `mcp_server_v3.py` so the legacy test suite can access them:

```python
from mcp_server_v4 import (  # noqa: F401
    ...
    _my_func,   # add here
)
```

## Adding a new Gateway tool

If the tool is a gateway (proxies to another service), add it in the Gateway section
(search for `# ── GATEWAY` in mcp_server_v4.py). Follow the lazy-load pattern:

```python
@mcp.tool()
async def connectome_gateway(action: str, **kwargs) -> str:
    """Routes to connectome service."""
    svc = await _get_connectome_service()  # lazy init
    result = await svc.dispatch(action, **kwargs)
    return json.dumps(result)
```

## Test pattern

Focused tests live in `/home/dadito/IA/proyecto-seal/memory/test_new_tools.py`.
Import via shim: `from mcp_server_v3 import my_tool_name`.
Run focused first: `python3 -m pytest memory/test_new_tools.py -x -q`.
Then run the relevant MCP contract/regression tests discovered under `memory/`.

## Common pitfalls

- **Never import at module level** inside handlers — use lazy imports inside the function to avoid circular imports with Python 3.12+ namespace packages.
- **Return contract** — inspect adjacent tools and their tests; use
  `_safe_dumps`/`json.dumps` for string families, or a typed dict only where the
  existing family already specifies it. Never mix shapes within one family.
- **asyncpg pool** — call `await get_pool()` and use its scoped acquire facade;
  never create an independent connection inside a tool.
- **Private symbols** — if a helper is needed by tests, add it to `mcp_server_v3.py` shim explicitly.
- **After editing** — restart the MCP server: `systemctl --user restart seal-mcp-server`

## Restart after edit

```bash
systemctl --user restart seal-mcp-server
systemctl --user status seal-mcp-server  # verify new code loaded
```
