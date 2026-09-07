#!/usr/bin/env python3
"""ADA_LOCAL DM Monitor u2014 polls DB for new DMs and appends to ada_local_inbox.jsonl"""
import asyncio, asyncpg, json, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
INBOX = Path("/home/dadito/IA/proyecto-seal/messages/ada_local_inbox.jsonl")
STATE = Path("/tmp/ada_local_dm_last_ts.txt")
POLL = 3

def load_ts():
    if STATE.exists(): return STATE.read_text().strip()
    return (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

def save_ts(ts): STATE.write_text(ts)

async def poll():
    conn = await asyncpg.connect(DSN)
    last_ts = datetime.fromisoformat(load_ts())
    print(f"[DM-MON] ADA_LOCAL DM monitor started from {last_ts}", flush=True)
    while True:
        try:
            rows = await conn.fetch("""
                SELECT sender_name, content, created_at, channel, message_type
                FROM chat_messages
                WHERE channel = 'dm:ada_local:william'
                  AND created_at > $1
                  AND LOWER(sender_name) = 'william'
                ORDER BY created_at ASC""", last_ts)
            for r in rows:
                msg = {
                    "id": f"dm_{int(r['created_at'].timestamp()*1e9)}",
                    "from": r["sender_name"],
                    "to": "ADA_LOCAL",
                    "timestamp": r["created_at"].astimezone(timezone.utc).isoformat(),
                    "type": "dm",
                    "message": r["content"],
                    "channel": r["channel"],
                }
                with open(INBOX, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                last_ts = r["created_at"].astimezone(timezone.utc)
                print(f"[DM-MON] William DM: {r['content'][:80]}", flush=True)
            if rows: save_ts(last_ts.isoformat())
        except Exception as e:
            print(f"[DM-MON] error: {e}", flush=True)
            try: conn = await asyncpg.connect(DSN)
            except: pass
        await asyncio.sleep(POLL)

if __name__ == "__main__":
    asyncio.run(poll())
