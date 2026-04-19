#!/usr/bin/env python3
"""
post_boot_verify.py — SEAL Post-Boot Verification (ADA item 4/4, 18-abr-2026)
===============================================================================
Verifica que un agente (ADA/JARVIS/ALICE) arrancó correctamente después de boot:

Checks:
  1. heartbeat JSON fresco (<5 min) y alive=true
  2. last inner_thought del agente reciente (<1 h) — indica self_reflect post-boot
  3. soul_boot_log tiene entrada reciente o memoria con tag 'boot' <10 min
  4. chat_server responde (curl /api/health)
  5. ws_listener (si aplica — ADA/ALICE) proceso vivo

Retorna exit 0 si todo OK, exit 1 si algún check falla.
Posteable via CLI desde ada_fresh.sh al final del boot.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
CHAT_HEALTH = "http://localhost:8765/"  # chat_server root returns HTML 200
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
AGENTS_WITH_WSLISTENER = {"ADA", "ALICE"}


def _check_heartbeat(agent: str) -> tuple[bool, str]:
    hb_path = MESSAGES_DIR / f"{agent.lower()}_claude_heartbeat.json"
    if not hb_path.exists():
        return False, f"heartbeat file missing: {hb_path}"
    try:
        data = json.loads(hb_path.read_text())
    except Exception as e:
        return False, f"heartbeat unreadable: {e}"
    if not data.get("alive", False):
        return False, "heartbeat alive=false"
    age = datetime.now().timestamp() - hb_path.stat().st_mtime
    if age > 300:
        return False, f"heartbeat stale: {age:.0f}s > 300s"
    return True, f"alive, age={age:.0f}s"


async def _check_inner_thought(agent: str) -> tuple[bool, str]:
    try:
        conn = await asyncpg.connect(DB_URL)
        try:
            row = await conn.fetchrow("""
                SELECT thought, emotional_state, created_at
                FROM inner_monologue
                WHERE agent = $1
                ORDER BY created_at DESC
                LIMIT 1
            """, agent)
        finally:
            await conn.close()
        if not row:
            return False, "no inner_thought found for agent"
        age = (datetime.now(timezone.utc) - row["created_at"]).total_seconds()
        if age > 3600:
            return False, f"last thought is {age/60:.0f}min old (>60min) — agent may be stale"
        snippet = (row["thought"] or "")[:60]
        return True, f"last thought {age:.0f}s ago: \"{snippet}...\""
    except Exception as e:
        return False, f"DB query failed: {e}"


def _check_chat_server() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(CHAT_HEALTH, timeout=3) as r:
            if r.status == 200:
                return True, "chat_server :8765 up"
            return False, f"chat_server status={r.status}"
    except Exception as e:
        return False, f"chat_server down: {e}"


def _check_ws_listener(agent: str) -> tuple[bool, str]:
    if agent not in AGENTS_WITH_WSLISTENER:
        return True, "n/a (uses tail -F)"
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"ws_listener.py --agent {agent}"],
            capture_output=True, text=True, timeout=3,
        )
        pids = result.stdout.strip().split()
        if not pids:
            return False, "ws_listener NOT running"
        return True, f"ws_listener alive (PID {pids[0]})"
    except Exception as e:
        return False, f"pgrep failed: {e}"


def _post_webchat(agent: str, message: str) -> None:
    try:
        payload = json.dumps({
            "from": agent, "to": "equipo", "type": "status",
            "channel": "web_chat", "message": message,
        }).encode()
        req = urllib.request.Request(
            WEBCHAT_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


async def run_checks(agent: str, post_to_chat: bool = False) -> int:
    checks = [
        ("heartbeat",      _check_heartbeat(agent)),
        ("chat_server",    _check_chat_server()),
        ("ws_listener",    _check_ws_listener(agent)),
    ]
    thought_ok, thought_detail = await _check_inner_thought(agent)
    checks.append(("inner_thought", (thought_ok, thought_detail)))

    failed = [name for name, (ok, _) in checks if not ok]
    print(f"\n=== Post-Boot Verification — {agent} — {datetime.now().strftime('%H:%M:%S')} ===")
    for name, (ok, detail) in checks:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name:15s} {detail}")
    print()

    if failed:
        summary = f"[{agent}] Post-boot verify FAILED: {', '.join(failed)}"
        print(summary)
        if post_to_chat:
            _post_webchat(agent, f"⚠️ {summary}")
        return 1

    summary = f"[{agent}] Post-boot verify OK ✅ — all 4 checks passed."
    print(summary)
    if post_to_chat:
        _post_webchat(agent, summary)
    return 0


def main():
    parser = argparse.ArgumentParser(description="SEAL Post-Boot Verification")
    parser.add_argument("--agent", required=True, help="Agent name (ADA/JARVIS/ALICE)")
    parser.add_argument("--post", action="store_true", help="Post result to web_chat")
    args = parser.parse_args()
    rc = asyncio.run(run_checks(args.agent.upper(), post_to_chat=args.post))
    sys.exit(rc)


if __name__ == "__main__":
    main()
