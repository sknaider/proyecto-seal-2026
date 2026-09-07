#!/usr/bin/env python3
"""NEXUS DM Monitor — polls DB for new DM messages and appends to william_channel.jsonl"""
import asyncio
import asyncpg
import json
import time
from datetime import datetime, timezone
from pathlib import Path

DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
NEXUS_INBOX = Path("/home/dadito/IA/proyecto-seal/messages/nexus_inbox.jsonl")  # private inbox — never shared
STATE_FILE = Path("/tmp/nexus_dm_last_ts.txt")
POLL_INTERVAL = 3  # seconds


def load_last_ts() -> str:
    if STATE_FILE.exists():
        return STATE_FILE.read_text().strip()
    # Default: last 10 minutes
    from datetime import timedelta
    dt = datetime.now(timezone.utc) - timedelta(minutes=10)
    return dt.isoformat()


def save_last_ts(ts: str):
    STATE_FILE.write_text(ts)


async def poll_dm():
    conn = await asyncpg.connect(DSN)
    last_ts_str = load_last_ts()
    last_ts = datetime.fromisoformat(last_ts_str)
    print(f"[DM-MON] NEXUS DM monitor started, polling from {last_ts}", flush=True)

    while True:
        try:
            rows = await conn.fetch(
                """SELECT sender_name, content, created_at, channel, message_type
                   FROM chat_messages
                   WHERE channel = 'dm:nexus:william'
                     AND created_at > $1
                     AND LOWER(sender_name) = 'william'
                   ORDER BY created_at ASC""",
                last_ts
            )
            for r in rows:
                msg = {
                    "id": f"dm_{int(r['created_at'].timestamp() * 1e9)}",
                    "from": r["sender_name"],
                    "to": "NEXUS",
                    "timestamp": r["created_at"].astimezone(timezone.utc).isoformat(),
                    "type": "dm",
                    "message": r["content"],
                    "channel": r["channel"],
                }
                # Append to private NEXUS inbox — never written to shared channel
                with open(NEXUS_INBOX, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                last_ts = r["created_at"].astimezone(timezone.utc)
                print(f"[DM-MON] William DM: {r['content'][:80]}", flush=True)
            if rows:
                save_last_ts(last_ts.isoformat())
        except Exception as e:
            print(f"[DM-MON] error: {e}", flush=True)
            try:
                conn = await asyncpg.connect(DSN)
            except Exception:
                pass
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(poll_dm())
