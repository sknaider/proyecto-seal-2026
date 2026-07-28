#!/usr/bin/env python3
"""Canary E2E: mismo navegador CDP -> noVNC -> lock humano -> release."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
FABLE = ROOT / "fable"
sys.path.insert(0, str(FABLE))

from mcp_web_soul_control import BrowserControlPlane  # noqa: E402
from mcp_web_soul_operator import OperatorCommand, apply_command  # noqa: E402


def payload(result) -> dict:
    raw = " ".join(getattr(item, "text", "") for item in result.content)
    return json.loads(raw)


async def main() -> int:
    env = {**os.environ, "SEAL_AGENT": "TAKEOVER_CANARY"}
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(FABLE / "seal_cdp_mcp.py")],
        env=env,
    )
    checks: dict[str, bool] = {}
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                page = payload(await session.call_tool(
                    "browse",
                    {"url": "data:text/html,<title>takeover-canary</title><button id=x>safe</button>"},
                ))
                requested = payload(await session.call_tool("browser_takeover", {"operation": "request"}))
                checks["same_browser_page"] = page.get("title") == "takeover-canary"
                checks["loopback_url"] = str(requested.get("takeover_url", "")).startswith("http://127.0.0.1:")
                try:
                    with urllib.request.urlopen(requested["takeover_url"], timeout=3) as response:
                        checks["novnc_http"] = response.status == 200 and b"noVNC" in response.read(200_000)
                except Exception:
                    checks["novnc_http"] = False

                control = BrowserControlPlane(ROOT / "var" / "mcp-web-soul" / "control.sqlite3")
                session_id = requested["session_id"]
                apply_command(control, OperatorCommand("takeover", session_id), operator="William")
                active = payload(await session.call_tool("browser_takeover", {"operation": "status"}))
                locked = payload(await session.call_tool(
                    "browser_action", {"action": "hover", "target": "#x"}
                ))
                checks["human_active"] = active.get("state") == "human_active"
                checks["same_viewer_active"] = active.get("visual", {}).get("viewer_active") is True
                checks["agent_locked"] = locked.get("error_code") == "takeover_locked"

                apply_command(control, OperatorCommand("release", session_id), operator="William")
                released = payload(await session.call_tool("browser_takeover", {"operation": "status"}))
                resumed = payload(await session.call_tool(
                    "browser_action", {"action": "hover", "target": "#x"}
                ))
                checks["released"] = released.get("state") == "ready"
                checks["viewer_stopped"] = released.get("visual", {}).get("viewer_active") is False
                checks["agent_resumed"] = resumed.get("ok") is True
                await session.call_tool("close_browser", {})
    ok = all(checks.values())
    print(json.dumps({"ok": ok, "checks": checks}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
