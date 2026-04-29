#!/usr/bin/env python3
"""
seal_heartbeat.py — Single writer for SEAL agent heartbeats.

Principle: event_log is truth. One path, no JSON divergence.
All agents call beat() instead of writing JSON files directly.

Schema note: soul_v3.event_log uses 'created_at', requires 'content'.
"""
from __future__ import annotations
import json
import os
import socket
from datetime import datetime, timezone

import asyncpg

_DB_URL = os.getenv(
    "SEAL_DB_URL",
    "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
)
_SCHEMA = os.getenv("SEAL_SCHEMA", "soul_v3")
_CONN_KWARGS: dict = {"server_settings": {"search_path": _SCHEMA}}


async def beat(agent: str, metadata: dict | None = None, content: str | None = None) -> None:
    """Insert a heartbeat row into event_log. Single writer — never write JSON directly."""
    metadata = dict(metadata or {})
    metadata["pid"] = os.getpid()
    metadata["host"] = socket.gethostname()

    summary = content or f"{agent} heartbeat"

    conn = await asyncpg.connect(_DB_URL, **_CONN_KWARGS)
    try:
        await conn.execute(
            """INSERT INTO soul_v3.event_log(created_at, agent, event_type, content, metadata)
               VALUES ($1, $2, 'heartbeat', $3, $4)""",
            datetime.now(timezone.utc),
            agent,
            summary[:500],
            json.dumps(metadata),
        )
    finally:
        await conn.close()


def beat_sync(agent: str, metadata: dict | None = None, content: str | None = None) -> None:
    """Synchronous wrapper for scripts that don't run an event loop."""
    import asyncio
    asyncio.run(beat(agent, metadata, content))


def last_beat_sync(agent: str) -> dict | None:
    """Synchronous fetch of last heartbeat — for use in non-async scripts."""
    import asyncio
    return asyncio.run(last_beat(agent))


async def last_beat(agent: str) -> dict | None:
    """Fetch the most recent heartbeat for an agent from event_log."""
    conn = await asyncpg.connect(_DB_URL, **_CONN_KWARGS)
    try:
        row = await conn.fetchrow(
            """SELECT created_at, agent, content, metadata
               FROM soul_v3.event_log
               WHERE event_type = 'heartbeat' AND agent = $1
               ORDER BY created_at DESC LIMIT 1""",
            agent,
        )
        if row:
            return {
                "agent": row["agent"],
                "timestamp": row["created_at"].isoformat(),
                "content": row["content"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            }
        return None
    finally:
        await conn.close()


async def all_beats(agents: list[str] | None = None) -> list[dict]:
    """Fetch last heartbeat for each agent. Used by peer_health checks."""
    agents = agents or ["ADA", "JARVIS", "ALICE", "DUM", "NEXUS"]
    results = []
    conn = await asyncpg.connect(_DB_URL, **_CONN_KWARGS)
    try:
        for agent in agents:
            row = await conn.fetchrow(
                """SELECT created_at, agent, content, metadata
                   FROM soul_v3.event_log
                   WHERE event_type = 'heartbeat' AND agent = $1
                   ORDER BY created_at DESC LIMIT 1""",
                agent,
            )
            if row:
                results.append({
                    "agent": row["agent"],
                    "timestamp": row["created_at"].isoformat(),
                    "content": row["content"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    "alive": True,
                })
            else:
                results.append({"agent": agent, "alive": False, "timestamp": None})
    finally:
        await conn.close()
    return results


if __name__ == "__main__":
    import asyncio, sys
    agent_name = sys.argv[1] if len(sys.argv) > 1 else "TEST"
    asyncio.run(beat(agent_name, {"test": True}, f"{agent_name} manual beat"))
    print(f"Beat written for {agent_name}")
