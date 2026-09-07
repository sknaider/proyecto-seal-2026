#!/usr/bin/env python3
"""Write an event to SEAL event_log table (PostgreSQL).

from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
Usage:
  python3 event_log_write.py --agent JARVIS --type heartbeat --content "Session alive, GPU 45C"
  python3 event_log_write.py --agent ADA --type audit --content "SOUL health OK"
"""

import asyncio
import argparse
import json
import sys

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


async def write_event(agent: str, event_type: str, content: str, metadata: str = "{}"):
    import asyncpg
    conn = await asyncpg.connect(DB_URL)
    from datetime import datetime, timezone
    now = datetime.now(LIMA_TZ)
    await conn.execute(
        "INSERT INTO soul_v3.event_log (agent, event_type, content, metadata, created_at) VALUES ($1, $2, $3, $4, $5)",
        agent, event_type, content, metadata, now,
    )
    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--type", required=True)
    parser.add_argument("--content", required=True)
    parser.add_argument("--metadata", default="{}")
    args = parser.parse_args()
    asyncio.run(write_event(args.agent, args.type, args.content, args.metadata))
