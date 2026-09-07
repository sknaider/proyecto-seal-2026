"""JARVIS Action Audit Logger — firma y persiste cada acción bash en PostgreSQL."""
from __future__ import annotations
import asyncio, asyncpg, hashlib, hmac, os, time
from datetime import datetime, timezone

_DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
_SECRET = b"seal_hmac_2026"


def _sign(action_type: str, command: str) -> str:
    payload = f"{action_type}:{command}:{time.time_ns()}".encode()
    return hmac.new(_SECRET, payload, hashlib.sha256).hexdigest()


async def _log(action_type: str, command: str, result_summary: str = "") -> int:
    sig = _sign(action_type, command)
    conn = await asyncpg.connect(_DSN)
    row_id = await conn.fetchval(
        """INSERT INTO jarvis_action_log (action_type, command, result_summary, hmac_sig)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        action_type, command[:2000], result_summary[:500], sig,
    )
    await conn.close()
    return row_id


def log_action(action_type: str, command: str, result_summary: str = "") -> int:
    """Sync wrapper — llama desde bash scripts o Python sync."""
    return asyncio.run(_log(action_type, command, result_summary))


async def recent_actions(limit: int = 10) -> list[dict]:
    conn = await asyncpg.connect(_DSN)
    rows = await conn.fetch(
        "SELECT id, ts, action_type, command, result_summary FROM jarvis_action_log ORDER BY ts DESC LIMIT $1",
        limit,
    )
    await conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        rid = log_action(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
        print(f"logged id={rid}")
    else:
        rows = asyncio.run(recent_actions(5))
        for r in rows:
            print(f"[{r['ts']}] {r['action_type']}: {r['command'][:60]}")
