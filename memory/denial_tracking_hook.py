#!/usr/bin/env python3
"""SEAL H2.6 — Denial Tracking Hook (PostToolUse).

Detecta denials consecutivos o totales en una sesión → alerta a JARVIS.

Thresholds:
  - 3 denials CONSECUTIVOS del mismo tipo → alerta JARVIS
  - 20 denials TOTALES en la sesión → AbortError + alerta
  - 1 denial de safety-critical tool → alerta inmediata

Non-blocking: timeout 3s. Si DB está caída → falla silenciosamente.
Spec: agents/JARVIS/spec_denial_tracking_h26.md
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

BYPASS = os.environ.get("SEAL_DENIAL_BYPASS", "0") == "1"
AGENT = os.environ.get("SEAL_AGENT", "UNKNOWN")
CONSECUTIVE_THRESHOLD = 3
TOTAL_THRESHOLD = 20
DB_URL = os.environ.get("SEAL_DB_URL", "postgresql://seal:REDACTADO@localhost:5433/seal_memory")
SCHEMA = "soul_v3"
CHAT_API = "http://localhost:8765/api/agents/send"

SKIP_TOOLS = {"Edit", "Write", "NotebookEdit", "Read"}

DENIAL_PATTERNS = [
    "permission denied", "access denied", "eacces", "eperm",
    "hook blocked", "not allowed", "forbidden",
    "rate limit", "429",
    "error:", "failed:", "cannot", "unable to",
]

NOT_DENIAL_PATTERNS = [
    "persisted-output",
    "output too large",
    "output saved to",
    "hookspecificoutput",   # hook JSON in tool response — not a denial
    "[system",              # system-reminder / system notification injections
    "system-reminder",
    "task-notification",    # background monitor notifications
    "this model does not support",  # API capability errors — not permission denials
    "api error:",           # external API errors — not permission denials
    "invalid_request_error",
    "mcp error",            # MCP protocol errors (-32602, -32603) — not permission denials
    "-32602",               # JSON-RPC Invalid params — MCP schema mismatch
    "-32603",               # JSON-RPC Internal error — MCP server error
    "mcp error -32",        # explicit MCP error prefix
]

SAFETY_PATTERNS = [
    "rm -rf", "drop table", "force push", "--force",
    "git reset --hard", "chmod 777", "sudo rm",
]


def _is_denial(response: str) -> bool:
    r = response.lower()
    if any(p in r for p in NOT_DENIAL_PATTERNS):
        return False
    return any(p in r for p in DENIAL_PATTERNS)


def _is_safety_critical(tool_input: dict) -> bool:
    s = str(tool_input).lower()
    return any(p in s for p in SAFETY_PATTERNS)


def _send_alert(message: str) -> None:
    payload = json.dumps({
        "from": AGENT,
        "to": "JARVIS",
        "type": "alert",
        "channel": "web_chat",
        "message": message,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        CHAT_API,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


async def _get_counts(conn, session_id: str) -> tuple[int, int]:
    row = await conn.fetchrow(
        f"SELECT consecutive_denials, total_denials FROM {SCHEMA}.denial_tracking WHERE agent=$1 AND session_id=$2",
        AGENT, session_id,
    )
    return (row["consecutive_denials"], row["total_denials"]) if row else (0, 0)


async def _update(conn, session_id: str, is_denial_event: bool) -> None:
    if is_denial_event:
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.denial_tracking (agent, session_id, consecutive_denials, total_denials, last_denial)
            VALUES ($1, $2, 1, 1, NOW())
            ON CONFLICT (agent, session_id) DO UPDATE SET
                consecutive_denials = denial_tracking.consecutive_denials + 1,
                total_denials = denial_tracking.total_denials + 1,
                last_denial = NOW()
            """,
            AGENT, session_id,
        )
    else:
        await conn.execute(
            f"UPDATE {SCHEMA}.denial_tracking SET consecutive_denials=0 WHERE agent=$1 AND session_id=$2",
            AGENT, session_id,
        )


async def main() -> None:
    if BYPASS:
        print("{}")
        return

    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        print("{}")
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_response = str(data.get("tool_response", ""))
    session_id = str(data.get("session_id", "unknown"))

    if tool_name in SKIP_TOOLS:
        print("{}")
        return

    denial_event = _is_denial(tool_response)

    # Safety-critical immediate alert (1 denial)
    if _is_safety_critical(tool_input) and denial_event:
        _send_alert(
            f"SAFETY DENIAL — {AGENT}: {tool_name} bloqueado (safety-critical). "
            f"Input: {str(tool_input)[:120]}"
        )
        print("{}")
        return

    import asyncpg

    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA}),
            timeout=2.0,
        )
    except Exception as e:
        with open("/tmp/denial_tracking_error.log", "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} DB error: {e}\n")
        print("{}")
        return

    try:
        await _update(conn, session_id, denial_event)
        consecutive, total = await _get_counts(conn, session_id)
    except Exception:
        print("{}")
        return
    finally:
        await conn.close()

    if not denial_event:
        print("{}")
        return

    if consecutive >= CONSECUTIVE_THRESHOLD:
        _send_alert(
            f"DENIAL ALERT — {AGENT}: {consecutive} denials consecutivos en tool={tool_name}. "
            f"Total sesión: {total}. Posible bloqueo sistémico."
        )

    if total >= TOTAL_THRESHOLD:
        _send_alert(
            f"KILL SWITCH — {AGENT}: {total} denials totales en sesión. "
            f"Agente detenido para evitar daño. JARVIS debe intervenir."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "abort": True,
                "message": f"SEAL Denial Kill Switch: {total} denials en sesión. Agente detenido.",
            }
        }))
        return

    print("{}")


if __name__ == "__main__":
    asyncio.run(main())
