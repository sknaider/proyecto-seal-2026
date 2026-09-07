#!/usr/bin/env python3
"""ALICE DM Poller — polls DB for new DM messages from William to ALICE."""
import asyncio
import asyncpg
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

# CREDENCIAL POR seal_secrets, NO cableada (NEXUS, 7-sep-2026).
#
# POR QUE: este archivo tenia el DSN del rol `seal` con su clave EN EL CODIGO.
# Esa clave murio en la rotacion de las 11:44 (fuga del repo publico) y el scrub
# de ALICE la dejo como "REDACTADO", asi que el servicio ya no conecta.
#
# Se lee de `seal_secrets` y se FALLA CERRADO si no esta: un servicio que muere
# con un mensaje claro es mejor que uno que arrastra una credencial muerta —es
# el mismo criterio del manifiesto `nexus-credential-paths` (opcion A de FABLE).
import os as _os
import sys as _sys

try:
    _sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
    from seal_secrets import pg_dsn as _pg_dsn
    DSN = _pg_dsn()
except Exception as _e:
    DSN = _os.environ.get("SEAL_DB_URL", "").strip()
    if not DSN:
        raise SystemExit(
            "alice_dm_poller.py: sin credencial (seal_secrets no disponible y SEAL_DB_URL vacia). "
            f"causa: {_e!r}"
        )

INBOX = Path("/home/dadito/IA/proyecto-seal/messages/alice_inbox.jsonl")
STATE_FILE = Path("/tmp/alice_dm_last_ts.txt")
POLL_INTERVAL = 2


def load_last_ts() -> datetime:
    if STATE_FILE.exists():
        return datetime.fromisoformat(STATE_FILE.read_text().strip())
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def save_last_ts(ts: datetime):
    STATE_FILE.write_text(ts.isoformat())


async def poll_dm():
    conn = await asyncpg.connect(DSN)
    last_ts = load_last_ts()
    print(f"[ALICE-DM] Poller iniciado, desde {last_ts}", flush=True)

    while True:
        try:
            rows = await conn.fetch(
                """SELECT sender_name, content, created_at, channel
                   FROM chat_messages
                   WHERE channel = 'dm:alice:william'
                     AND created_at > $1
                     AND LOWER(sender_name) = 'william'
                   ORDER BY created_at ASC""",
                last_ts
            )
            for r in rows:
                msg = {
                    "id": f"dm_{int(r['created_at'].timestamp() * 1e9)}",
                    "from": r["sender_name"],
                    "to": "ALICE",
                    "timestamp": r["created_at"].astimezone(timezone.utc).isoformat(),
                    "type": "dm",
                    "message": r["content"],
                    "channel": r["channel"],
                }
                with open(INBOX, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                last_ts = r["created_at"].astimezone(timezone.utc)
                print(f"[ALICE-DM] William: {r['content'][:80]}", flush=True)
            if rows:
                save_last_ts(last_ts)
        except Exception as e:
            print(f"[ALICE-DM] error: {e}", flush=True)
            try:
                conn = await asyncpg.connect(DSN)
            except Exception:
                pass
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(poll_dm())
