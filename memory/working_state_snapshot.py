#!/usr/bin/env python3
"""Working State Snapshot — NEXUS 2026-05-17

Periodic snapshot capturer of soul_v3.working_state → soul_v3.working_state_history.
Closes the historical reconstruction gap in soul_replay.py (Replay Engine v0.1
limitation: working_state had no history).

Runs via systemd timer every 10 minutes. Append-only, no DELETE here.

Retention: pasada de podado se delega a soul_maintenance.py (mantener last 90 days).
"""

import asyncio
import asyncpg
from seal_secrets import pg_dsn
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Columns to copy from working_state → working_state_history (excluding snapshot_at PK)
COPY_COLS = [
    "agent", "task_name", "step", "total_steps", "description",
    "active_hypotheses", "discarded_paths", "current_constraints",
    "pending_validations", "risk_level", "agent_state",
    "current_skill_execution", "ocean_baseline", "emotional_state",
    "technical_state", "decisions", "corrections", "last_intention",
    "state", "turn_count", "active_session_id", "updated_at",
]


def resolve_db_dsn() -> str:
    return pg_dsn(required=True)


async def snapshot():
    conn = await asyncpg.connect(resolve_db_dsn())
    try:
        cols_sql = ", ".join(COPY_COLS)
        await conn.execute(f"""
            INSERT INTO soul_v3.working_state_history ({cols_sql})
            SELECT {cols_sql} FROM soul_v3.working_state
        """)
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM soul_v3.working_state_history "
            "WHERE snapshot_at > NOW() - INTERVAL '1 minute'"
        )
        print(f"[{datetime.now(timezone.utc).isoformat()}] snapshotted {cnt} rows")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(snapshot())
