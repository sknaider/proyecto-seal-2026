#!/usr/bin/env python3
"""Write one F3 parity artifact using only the restricted sandbox identity."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from soul_event_interface import CLAUDE_CODE_EVENT_MAP
from soul_runtime_adapter import get_adapter

KNOWN_AGENTS = {"ADA", "ALICE", "DUM", "JARVIS", "NEXUS"}
KNOWN_RUNTIMES = {"claude_code", "local_llama"}
KNOWN_EVENTS = {
    "on_boot", "on_prompt", "on_turn_end", "on_compact",
    "on_tool_call", "on_tool_result", "on_file_change",
    "on_task_create", "on_permission_denied",
}
CLAUDE_EVENT_MAP = dict(CLAUDE_CODE_EVENT_MAP)
LOCAL_EVENT_MAP = dict(get_adapter("local_llama").event_map)


def _value(payload: dict, key: str) -> str:
    parity = payload.get("soul_parity")
    if isinstance(parity, dict) and parity.get(key):
        return str(parity[key])
    return os.environ.get(f"SOUL_PARITY_{key.upper()}", "")


async def _record(payload: dict) -> dict:
    import asyncpg

    # Claude Code is launched as a one-shot parity probe, not as the long-lived
    # ADA agent runtime managed by seal_lifecycle_controller.  The dedicated
    # name prevents that controller from mistaking the probe for an ADA session;
    # the restricted PostgreSQL role below remains the authoritative identity.
    agent = (
        os.environ.get("SOUL_PARITY_AGENT")
        or os.environ.get("SEAL_AGENT", "")
    ).strip().upper()
    runtime_id = _value(payload, "runtime_id") or str(payload.get("runtime", ""))
    native_event = str(payload.get("hook_event_name") or payload.get("hookEventName") or "")
    soul_event = _value(payload, "soul_event") or str(payload.get("soul_event", ""))
    payload_runtime = str(payload.get("runtime", ""))
    if runtime_id == "claude_code":
        mapped_event = CLAUDE_EVENT_MAP.get(native_event, "")
        if not mapped_event or mapped_event != soul_event:
            raise RuntimeError("native Claude event does not match claimed SOUL event")
    elif runtime_id == "local_llama":
        if (
            payload_runtime != "local_llama"
            or LOCAL_EVENT_MAP.get(native_event, "") != soul_event
            or str(payload.get("soul_event", "")) != soul_event
        ):
            raise RuntimeError("native local event does not match claimed SOUL event")
    suite_run_id = _value(payload, "suite_run_id")
    nonce = _value(payload, "nonce")
    token = _value(payload, "token")
    script_path = str(Path(__file__).resolve())
    script_sha256 = os.environ.get("SOUL_EXECUTED_SCRIPT_SHA256", "")
    runtime_pid_raw = os.environ.get("SOUL_PARITY_RUNTIME_PID", "")

    if agent not in KNOWN_AGENTS:
        raise RuntimeError("invalid parity agent")
    if runtime_id not in KNOWN_RUNTIMES or soul_event not in KNOWN_EVENTS:
        raise RuntimeError("invalid parity runtime/event")
    if not suite_run_id or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise RuntimeError("invalid parity run/nonce")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise RuntimeError("invalid parity token")
    if not re.fullmatch(r"[0-9a-f]{64}", script_sha256):
        raise RuntimeError("executed script digest missing")
    if not runtime_pid_raw.isdigit() or int(runtime_pid_raw) <= 1:
        raise RuntimeError("runtime PID evidence missing")

    dsn = os.environ.get("SEAL_PG_DSN", "")
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("restricted PostgreSQL DSN missing")
    conn = await asyncpg.connect(dsn)
    try:
        identity = await conn.fetchrow(
            """SELECT current_database() AS db, session_user::text AS session_user,
                      current_user::text AS current_user,
                      soul_v3.mcp_session_agent() AS resolved_agent,
                      (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AS superuser"""
        )
        expected_role = f"mcp_runtime_{agent.lower()}"
        if (
            identity["db"] != "soul_v3_sandbox"
            or identity["session_user"] != expected_role
            or identity["current_user"] != expected_role
            or identity["resolved_agent"] != agent
            or identity["superuser"]
        ):
            raise RuntimeError("parity hook refused non-sandbox or privileged identity")
        await conn.execute(
            """INSERT INTO soul_f3.parity_evidence_v2
                 (suite_run_id,runtime_id,native_event,soul_event,script_path,
                  script_sha256,runtime_pid,agent,nonce,token_sha256)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            suite_run_id, runtime_id, native_event,
            soul_event, script_path, script_sha256, int(runtime_pid_raw), agent, nonce,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
        return {
            "ok": True,
            "db": identity["db"],
            "role": identity["current_user"],
            "runtime": runtime_id,
            "event": soul_event,
        }
    finally:
        await conn.close()


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        print(json.dumps(asyncio.run(_record(payload)), sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
