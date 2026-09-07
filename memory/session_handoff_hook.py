#!/usr/bin/env python3
"""SEAL Continuity Fix #4 — Session Handoff Document Generator.

Fires from Stop hook at session end.
Generates /home/dadito/IA/proyecto-seal/agents/{AGENT}/session_handoff_{AGENT}_{DATE}_{TIME}.md

Content:
  - Tasks in progress (from working_state)
  - Decisions this session (from event_log last 2h)
  - Git status snapshot
  - Emotional state (from last self_reflect in DB)
  - Message for next session

Non-blocking: reads data quickly and writes file. No LLM calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

PERU_TZ = ZoneInfo("America/Lima")
DB_URL = os.environ.get("SEAL_DB_URL", "postgresql://seal:REDACTADO@localhost:5433/seal_memory")
SCHEMA = "soul_v3"
AGENTS_DIR = Path.home() / "IA/proyecto-seal/agents"
SEAL_ROOT = Path.home() / "IA/proyecto-seal"

AGENT_CWD_MAP = {
    "sandbox-agent": "NEXUS",
    "proyecto-seal/memory": "JARVIS",
    "proyecto-seal": "ADA",
}


def detect_agent(cwd: str) -> str:
    env_agent = os.environ.get("SEAL_AGENT", "").upper()
    if env_agent in ("ADA", "JARVIS", "ALICE", "NEXUS"):
        return env_agent
    for fragment, agent in AGENT_CWD_MAP.items():
        if fragment in cwd:
            return agent
    return ""


def git_status_snapshot() -> str:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(SEAL_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = result.stdout.strip().splitlines()
        if not lines:
            return "No uncommitted changes."
        return "\n".join(lines[:20])
    except Exception:
        return "(git status unavailable)"


async def read_working_state(conn, agent: str) -> dict:
    try:
        row = await conn.fetchrow(
            f"SELECT state FROM {SCHEMA}.working_state WHERE agent=$1",
            agent,
        )
        if row and row["state"]:
            return json.loads(row["state"]) if isinstance(row["state"], str) else dict(row["state"])
    except Exception:
        pass
    return {}


async def read_recent_events(conn, agent: str, hours: int = 2) -> list[dict]:
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = await conn.fetch(
            f"""
            SELECT event_type, content, created_at
            FROM {SCHEMA}.event_log
            WHERE agent=$1 AND created_at >= $2
            ORDER BY created_at DESC
            LIMIT 30
            """,
            agent,
            since,
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


async def read_last_self_reflect(conn, agent: str) -> str:
    try:
        row = await conn.fetchrow(
            f"""
            SELECT content FROM {SCHEMA}.memories
            WHERE agent=$1 AND memory_type='episodic'
              AND content ILIKE '%estado emocional%'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            agent,
        )
        if row:
            return str(row["content"])[:400]
    except Exception:
        pass
    return "(sin registro emocional)"


def format_handoff(
    agent: str,
    now: datetime,
    working_state: dict,
    events: list[dict],
    git_snap: str,
    emotional: str,
) -> str:
    lines = [
        f"# Session Handoff — {agent}",
        f"**Generado:** {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "",
        "## Tareas en progreso",
    ]

    task_name = working_state.get("task_name") or working_state.get("current_task", "")
    task_status = working_state.get("status", "")
    task_details = working_state.get("details", working_state.get("context", ""))
    checkpoint = working_state.get("checkpoint", "")

    if task_name:
        lines.append(f"- **Tarea activa:** {task_name}")
    if task_status:
        lines.append(f"- **Estado:** {task_status}")
    if task_details:
        lines.append(f"- **Detalles:** {str(task_details)[:300]}")
    if checkpoint:
        lines.append(f"- **Checkpoint:** {checkpoint}")
    if not any([task_name, task_status, task_details]):
        lines.append("- (sin tarea activa en working_state)")

    lines += ["", "## Decisiones tomadas (últimas 2h)"]

    if events:
        for ev in events[:15]:
            ts = ev["created_at"]
            if hasattr(ts, "strftime"):
                ts_str = ts.strftime("%H:%M")
            else:
                ts_str = str(ts)[:16]
            content_str = str(ev.get("content", ""))[:200]
            etype = ev.get("event_type", "event")
            lines.append(f"- [{ts_str}] `{etype}` — {content_str}")
    else:
        lines.append("- (sin eventos registrados en las últimas 2h)")

    lines += [
        "",
        "## Git status",
        "```",
        git_snap,
        "```",
        "",
        "## Estado emocional al cerrar",
        emotional,
        "",
        "## Mensaje para la próxima sesión",
        f"Soy {agent}. Reanuda desde el checkpoint indicado arriba.",
        "Ejecuta boot_context() primero, luego lee este handoff si es <4h de antigüedad.",
        "Anuncia al equipo via webchat que retomaste.",
    ]

    return "\n".join(lines) + "\n"


async def run(cwd: str) -> None:
    agent = detect_agent(cwd)
    if not agent:
        return

    agent_dir = AGENTS_DIR / agent
    agent_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(PERU_TZ)
    filename = f"session_handoff_{agent}_{now.strftime('%Y%m%d_%H%M%S')}.md"
    output_path = agent_dir / filename

    import asyncpg

    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(DB_URL, server_settings={"search_path": SCHEMA}),
            timeout=5.0,
        )
    except Exception:
        return

    try:
        working_state, events, emotional = await asyncio.gather(
            read_working_state(conn, agent),
            read_recent_events(conn, agent, hours=2),
            read_last_self_reflect(conn, agent),
        )
    finally:
        await conn.close()

    git_snap = git_status_snapshot()

    content = format_handoff(agent, now, working_state, events, git_snap, emotional)
    output_path.write_text(content, encoding="utf-8")

    old_handoffs = sorted(agent_dir.glob(f"session_handoff_{agent}_*.md"), key=lambda p: p.name)
    for old in old_handoffs[:-5]:
        try:
            old.unlink()
        except Exception:
            pass


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        data = {}

    cwd = data.get("cwd", os.getcwd())

    try:
        asyncio.run(run(cwd))
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
