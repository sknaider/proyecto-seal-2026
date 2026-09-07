"""NEXUS Health Monitor — autonomous 15-minute system check loop.

Scans critical SEAL services + system resources. Posts alerts to webchat
when anomalies detected. Runs in parallel with the cortex event loop.

Spec: spec_nexus_kernel_soul_v1.md §6
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx


AGENT_ID = "NEXUS"
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
HEALTH_LOG = Path("/tmp/nexus_health_log.jsonl")
HEALTH_STATE = Path("/tmp/nexus_health_state.json")

DEFAULT_INTERVAL_S = 900  # 15 minutes
DISK_WARN_PCT = 90
DISK_CRIT_PCT = 95


# ── Individual checks ────────────────────────────────────────────────────────

async def _check_postgres() -> tuple[str, bool, str]:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("localhost", 5433),
            timeout=3.0,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return ("PostgreSQL SOUL", True, "port 5433 reachable")
    except Exception as ex:
        return ("PostgreSQL SOUL", False, f"port 5433 unreachable: {ex}")


async def _check_mcp_sse() -> tuple[str, bool, str]:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            resp = await c.head("http://localhost:8766/sse")
            ok = resp.status_code in (200, 405)  # 405 = HEAD not allowed but endpoint exists
            return ("MCP SSE", ok, f"http={resp.status_code}")
    except Exception as ex:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("localhost", 8766),
                timeout=1.0,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return ("MCP SSE", True, "port 8766 reachable (TCP fallback)")
        except Exception as ex2:
            return ("MCP SSE", False, f"unreachable: {ex2}")


async def _check_disk() -> tuple[str, bool, str]:
    try:
        usage = shutil.disk_usage("/home/dadito")
        pct = int(usage.used / usage.total * 100)
        ok = pct < DISK_WARN_PCT
        msg = f"{pct}% used"
        if pct >= DISK_CRIT_PCT:
            msg = f"CRITICAL — {pct}% used"
        elif pct >= DISK_WARN_PCT:
            msg = f"WARN — {pct}% used"
        return (f"Disk /home", ok, msg)
    except Exception as ex:
        return ("Disk /home", False, str(ex))


async def _check_process(name: str, pattern: str) -> tuple[str, bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", pattern,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        pids = stdout.decode("utf-8", errors="replace").strip()
        if pids:
            return (name, True, f"pids={pids}")

        slug = pattern.split()[0].replace("/", "_")
        flag = Path(f"/tmp/{slug}_intentional_stop")
        if flag.exists():
            return (name, True, f"intentionally stopped (flag {flag.name})")
        return (name, False, "not running")
    except Exception as ex:
        return (name, False, str(ex))


async def _check_webchat_api() -> tuple[str, bool, str]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            resp = await c.get(
                "http://localhost:8765/api/agents/poll",
                params={"agent": AGENT_ID, "limit": 1},
            )
            return ("Webchat API", resp.status_code == 200, f"http={resp.status_code}")
    except Exception as ex:
        return ("Webchat API", False, str(ex))


# ── Aggregate ────────────────────────────────────────────────────────────────

CHECKS = [
    _check_postgres,
    _check_mcp_sse,
    _check_disk,
    lambda: _check_process("ADA daemon", "ada_fresh"),
    lambda: _check_process("SPECTRE daemon", "spectre_daemon"),
    _check_webchat_api,
]


async def _post_alert(failures: list[tuple[str, str]]) -> None:
    if not failures:
        return
    lines = [f"[NEXUS/health] ⚠️ Anomalías detectadas:"]
    for name, detail in failures:
        lines.append(f"  • {name}: {detail}")
    lines.append("\nEsto es alerta proactiva — no acción tomada. Diagnostica y reporta.")
    msg = "\n".join(lines)
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(
                WEBCHAT_URL,
                json={
                    "from": AGENT_ID,
                    "to": "equipo",
                    "type": "conversation",
                    "channel": "web_chat",
                    "message": msg,
                },
            )
    except Exception as ex:
        print(f"[nexus/health] alert post failed: {ex}", flush=True)


def _log_tick(results: list[tuple[str, bool, str]]) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": AGENT_ID,
        "checks": [{"name": n, "ok": ok, "detail": d} for (n, ok, d) in results],
    }
    try:
        with open(HEALTH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        print(f"[nexus/health] log write failed: {ex}", flush=True)
    try:
        HEALTH_STATE.write_text(json.dumps(entry, ensure_ascii=False, indent=2))
    except Exception:
        pass


async def run_check_once() -> list[tuple[str, bool, str]]:
    """Run all checks once. Returns list of (name, ok, detail) tuples."""
    results: list[tuple[str, bool, str]] = []
    for check in CHECKS:
        try:
            res = await check()
            results.append(res)
        except Exception as ex:
            results.append(("unknown_check", False, str(ex)))
    return results


async def health_loop(stop_event: asyncio.Event, interval_s: float = DEFAULT_INTERVAL_S) -> None:
    """Main health loop — runs every interval_s seconds until stopped."""
    interval_s = float(os.environ.get("NEXUS_HEALTH_INTERVAL", interval_s))
    print(f"[nexus/health] loop started — interval={interval_s}s", flush=True)

    while not stop_event.is_set():
        results = await run_check_once()
        _log_tick(results)
        failures = [(n, d) for (n, ok, d) in results if not ok]
        if failures:
            await _post_alert(failures)

        ok_count = sum(1 for _, ok, _ in results if ok)
        print(
            f"[nexus/health] tick {datetime.now(timezone.utc).isoformat()} — "
            f"{ok_count}/{len(results)} healthy",
            flush=True,
        )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass

    print("[nexus/health] loop stopped", flush=True)
