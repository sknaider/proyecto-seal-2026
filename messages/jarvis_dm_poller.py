#!/usr/bin/env python3
"""JARVIS DM Poller — polls DB for new DM messages from William to JARVIS.
Writes to jarvis_inbox.jsonl (private). JARVIS tails this file via Monitor."""
import asyncio
import asyncpg
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
JARVIS_INBOX = Path("/home/dadito/IA/proyecto-seal/messages/jarvis_inbox.jsonl")
STATE_FILE = Path("/tmp/jarvis_dm_last_ts.txt")
POLL_INTERVAL = 2  # seconds


def load_last_ts() -> datetime:
    if STATE_FILE.exists():
        return datetime.fromisoformat(STATE_FILE.read_text().strip())
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def save_last_ts(ts: datetime):
    STATE_FILE.write_text(ts.isoformat())


async def poll_dm():
    conn = await asyncpg.connect(DSN)
    last_ts = load_last_ts()
    print(f"[JARVIS-DM] Poller iniciado, desde {last_ts}", flush=True)

    while True:
        try:
            rows = await conn.fetch(
                """SELECT sender_name, content, created_at, channel, message_type
                   FROM chat_messages
                   WHERE channel = 'dm:jarvis:william'
                     AND created_at > $1
                     AND LOWER(sender_name) = 'william'
                   ORDER BY created_at ASC""",
                last_ts
            )
            for r in rows:
                msg = {
                    "id": f"dm_{int(r['created_at'].timestamp() * 1e9)}",
                    "from": r["sender_name"],
                    "to": "JARVIS",
                    "timestamp": r["created_at"].astimezone(timezone.utc).isoformat(),
                    "type": "dm",
                    "message": r["content"],
                    "channel": r["channel"],
                }
                with open(JARVIS_INBOX, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                last_ts = r["created_at"].astimezone(timezone.utc)
                print(f"[JARVIS-DM] William: {r['content'][:80]}", flush=True)
            if rows:
                save_last_ts(last_ts)
        except Exception as e:
            print(f"[JARVIS-DM] error: {e}", flush=True)
            try:
                conn = await asyncpg.connect(DSN)
            except Exception:
                pass
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(poll_dm())
