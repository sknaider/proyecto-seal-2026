#!/usr/bin/env python3
"""
migrate_jsonl_to_db.py — One-shot migration of SEAL Chat JSONL history to PostgreSQL.

Imports ada_messages.jsonl, jarvis_messages.jsonl, and william_channel.jsonl
into the chat_messages table. Idempotent — skips messages already in DB.

Usage:
    python3 migrate_jsonl_to_db.py             # dry-run (count only)
    python3 migrate_jsonl_to_db.py --execute   # actually migrate
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from chat_db import ChatDB

DIR = Path(__file__).parent

SOURCES = [
    (DIR / "ada_messages.jsonl", "ADA", "agent"),
    (DIR / "jarvis_messages.jsonl", "JARVIS", "agent"),
    (DIR / "william_channel.jsonl", "William", "user"),
]


def parse_timestamp(ts_str: str) -> datetime:
    """Parse various timestamp formats to datetime."""
    if not ts_str:
        return datetime.now(LIMA_TZ)
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(LIMA_TZ)


def parse_jsonl(path: Path, default_sender: str, default_type: str) -> list[dict]:
    """Parse a JSONL file into normalized message dicts."""
    messages = []
    if not path.exists():
        return messages

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            content = data.get("message", data.get("content", data.get("text", "")))
            if not content or not isinstance(content, str):
                continue

            sender = data.get("from", data.get("sender", data.get("agent", default_sender)))
            msg_type = data.get("type", "chat")
            channel = data.get("channel", "general")
            ts = parse_timestamp(data.get("timestamp", data.get("ts", "")))
            legacy_id = data.get("id", data.get("idempotency_key", f"{path.stem}_{line_num}"))

            messages.append({
                "sender_type": default_type if sender.upper() in ("ADA", "JARVIS", "DUM") else "user",
                "sender_name": sender,
                "channel": channel,
                "message_type": msg_type,
                "content": content,
                "metadata": json.dumps({"legacy_id": legacy_id, "source": path.name}),
                "created_at": ts,
            })

    return messages


async def migrate(execute: bool = False):
    """Run the migration."""
    all_messages = []
    for path, sender, stype in SOURCES:
        msgs = parse_jsonl(path, sender, stype)
        print(f"  {path.name}: {len(msgs)} messages parsed")
        all_messages.extend(msgs)

    # Ensure all timestamps are timezone-aware before sorting
    for msg in all_messages:
        ts = msg["created_at"]
        if ts.tzinfo is None:
            msg["created_at"] = ts.replace(tzinfo=timezone.utc)
    all_messages.sort(key=lambda m: m["created_at"])
    print(f"\nTotal: {len(all_messages)} messages to migrate")

    if not execute:
        print("\n[DRY-RUN] Add --execute to actually migrate.")
        return 0

    # Check how many already in DB
    db = ChatDB()
    await db.init()

    existing = await db.get_message_count()
    print(f"Existing messages in DB: {existing}")

    if existing > 10:
        print("DB already has messages. Skipping to avoid duplicates.")
        print("To force re-import, truncate chat_messages first.")
        await db.close()
        return 0

    # Bulk import
    count = 0
    batch_size = 100
    for i in range(0, len(all_messages), batch_size):
        batch = all_messages[i:i + batch_size]
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                for msg in batch:
                    await conn.execute(
                        """INSERT INTO chat_messages
                           (sender_type, sender_name, channel, message_type, content, metadata, created_at)
                           VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)""",
                        msg["sender_type"],
                        msg["sender_name"],
                        msg["channel"],
                        msg["message_type"],
                        msg["content"],
                        msg["metadata"],
                        msg["created_at"],
                    )
                    count += 1
        print(f"  Imported {count}/{len(all_messages)}...")

    final_count = await db.get_message_count()
    await db.close()

    print(f"\n[DONE] Migrated {count} messages. DB now has {final_count} total.")
    return count


def main():
    parser = argparse.ArgumentParser(description="Migrate SEAL JSONL to PostgreSQL")
    parser.add_argument("--execute", action="store_true", help="Actually execute migration")
    args = parser.parse_args()

    print("=== SEAL Chat JSONL → PostgreSQL Migration ===\n")
    asyncio.run(migrate(execute=args.execute))


if __name__ == "__main__":
    main()
