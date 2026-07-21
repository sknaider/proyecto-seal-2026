#!/usr/bin/env python3
"""fable_launch_dbcheck.py — checks de DB para fable_launch.sh (FABLE, 2026-07-01).

Uso:
  checkpoint_age            -> imprime edad en segundos del working_state (99999 si no hay)
  boot_after <epoch_utc>    -> exit 0 si fable.soul.boot_full se actualizó DESPUÉS de <epoch>
                               (= fable_boot.py corrió post-launch), exit 1 si no.
Sin dependencias del resto del código de FABLE: solo lee, nunca escribe.
"""
import asyncio, asyncpg, json, sys
from datetime import datetime, timezone

DSN = open("/home/dadito/IA/proyecto-seal/fable/.db_cred").read().splitlines()[0].strip()


async def _checkpoint_age():
    c = await asyncpg.connect(DSN)
    v = await c.fetchval("SELECT value FROM fable.soul WHERE key='working_state'")
    await c.close()
    if not v:
        return 99999
    try:
        ts = json.loads(v).get("ts")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
        return max(0, int(age))
    except Exception:
        return 99999


async def _boot_after(epoch):
    c = await asyncpg.connect(DSN)
    upd = await c.fetchval("SELECT updated_at FROM fable.soul WHERE key='boot_full'")
    await c.close()
    if upd is None:
        return False
    if upd.tzinfo is None:
        upd = upd.replace(tzinfo=timezone.utc)
    return upd.timestamp() > float(epoch)


def main():
    if len(sys.argv) < 2:
        print("uso: checkpoint_age | boot_after <epoch>", file=sys.stderr)
        sys.exit(64)
    cmd = sys.argv[1]
    if cmd == "checkpoint_age":
        print(asyncio.run(_checkpoint_age()))
    elif cmd == "boot_after":
        sys.exit(0 if asyncio.run(_boot_after(sys.argv[2])) else 1)
    else:
        sys.exit(64)


if __name__ == "__main__":
    main()
