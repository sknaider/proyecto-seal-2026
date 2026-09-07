#!/usr/bin/env python3
"""Canary reproducible de 100 operaciones sobre el MCP nativo real por stdio."""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from contextlib import suppress
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
FABLE = ROOT / "fable"
sys.path.insert(0, str(FABLE))

from mcp_web_soul_control import discover_orphan_browsers  # noqa: E402
from mcp_web_soul_security import AuditTrail  # noqa: E402


SYNTHETIC_SECRET = "MCP_CANARY_SECRET_NEVER_PERSIST_73a9"


def text(result) -> str:
    return " ".join(getattr(item, "text", "") for item in result.content)


async def main() -> int:
    env = os.environ.copy()
    env["SEAL_AGENT"] = "CANARY_ADA"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(FABLE / "seal_cdp_mcp.py")],
        env=env,
    )
    latencies: list[float] = []
    failures: list[str] = []
    operations = 0

    async def call(session: ClientSession, tool: str, arguments: dict, check=None):
        nonlocal operations
        started = time.perf_counter()
        result = await session.call_tool(tool, arguments)
        latencies.append((time.perf_counter() - started) * 1000)
        operations += 1
        payload = text(result)
        if result.isError or (check is not None and not check(payload)):
            failures.append(f"{tool}:{payload[:160]}")
        return result

    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                await call(
                    session,
                    "browse",
                    {"url": "data:text/html,<title>SOUL MCP canary</title><p>Prometheus</p>"},
                    lambda raw: "SOUL MCP canary" in raw,
                )
                for _ in range(20):
                    await call(session, "browser_session", {"operation": "heartbeat"}, lambda raw: '"ok": true' in raw)
                for _ in range(20):
                    await call(session, "browser_observe", {"observation": "text"}, lambda raw: "Prometheus" in raw)
                for _ in range(20):
                    await call(session, "browser_observe", {"observation": "tabs"}, lambda raw: '"count": 1' in raw)
                for _ in range(10):
                    await call(session, "browser_observe", {"observation": "network"}, lambda raw: '"ok": true' in raw)
                for _ in range(10):
                    await call(
                        session,
                        "click",
                        {"selector": "button[type=submit]"},
                        lambda raw: "approval_required" in raw,
                    )
                for _ in range(10):
                    await call(
                        session,
                        "set_cookie",
                        {
                            "name": "canary",
                            "value": SYNTHETIC_SECRET,
                            "url": "https://example.com/",
                        },
                        lambda raw: "approval_required" in raw,
                    )
                for _ in range(5):
                    await call(session, "browser_readiness", {}, lambda raw: '"overall": "pass"' in raw)
                for _ in range(3):
                    await call(session, "browser_observe", {"observation": "performance"}, lambda raw: '"ok": true' in raw)
                await call(session, "close_browser", {}, lambda raw: '"ok": true' in raw)

    await asyncio.sleep(0.3)
    audit = AuditTrail(ROOT / "var" / "mcp-web-soul" / "audit" / "events.jsonl")
    chain_ok, events = audit.verify()
    raw_audit = audit.path.read_text(encoding="utf-8") if audit.path.exists() else ""
    orphans = discover_orphan_browsers()
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies, default=0)
    summary = {
        "operations": operations,
        "failures": failures,
        "latency_ms": {
            "median": round(statistics.median(latencies), 2),
            "p95": round(p95, 2),
            "max": round(max(latencies, default=0), 2),
        },
        "audit_chain": {"ok": chain_ok, "events": events},
        "synthetic_secret_persisted": SYNTHETIC_SECRET in raw_audit,
        "orphan_browsers": orphans,
    }
    ok = (
        operations == 100
        and not failures
        and chain_ok
        and SYNTHETIC_SECRET not in raw_audit
        and not orphans
    )
    summary["overall"] = "PASS" if ok else "FAIL"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
