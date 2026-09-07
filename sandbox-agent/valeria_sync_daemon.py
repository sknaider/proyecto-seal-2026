#!/usr/bin/env python3
"""Valeria u2192 ADA_LOCAL sync daemon u2014 transfiere memorias y conversaciones en tiempo real."""
import asyncio, asyncpg, json, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

VALERIA_DSN = "postgresql://seal:REDACTADO@localhost:5433/valeria_memory"
SOUL_DSN    = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
STATE       = Path("/tmp/valeria_sync_last_ts.txt")
POLL        = 10
VALID = {"fact","preference","decision","insight","correction","milestone","pattern","emotion","trust","humor","dynamic"}
CAT_MAP = {"intimate":"emotion","dislike":"preference","secret":"decision"}

def map_cat(c): return CAT_MAP.get(c, c if c in VALID else "fact")

def load_ts():
    if STATE.exists(): return datetime.fromisoformat(STATE.read_text().strip())
    return datetime.now(timezone.utc) - timedelta(hours=1)

def save_ts(dt): STATE.write_text(dt.isoformat())

async def sync_loop():
    vc = await asyncpg.connect(VALERIA_DSN)
    sc = await asyncpg.connect(SOUL_DSN)
    last_ts = load_ts()
    print(f"[SYNC] Valeriau2192ADA_LOCAL daemon started from {last_ts}", flush=True)

    while True:
        try:
            # Nuevas memorias
            new_mems = await vc.fetch("""
                SELECT category, content, importance, created_at
                FROM memories WHERE active=TRUE AND created_at > $1
                ORDER BY created_at ASC""", last_ts)
            for m in new_mems:
                try:
                    await sc.execute("""
                        INSERT INTO memories (agent,category,content,importance,scope,source)
                        VALUES ('ADA_LOCAL',$1,$2,$3,'private','consolidation')
                        ON CONFLICT DO NOTHING""",
                        map_cat(m['category']), m['content'][:500], min(m['importance'],9))
                    last_ts = m['created_at'].astimezone(timezone.utc)
                    print(f"[SYNC] mem: {m['content'][:60]}", flush=True)
                except: pass

            # Nuevas conversaciones u2192 fact memory
            new_convs = await vc.fetch("""
                SELECT role, content, created_at FROM conversations
                WHERE created_at > $1 AND role != 'system'
                ORDER BY created_at ASC LIMIT 20""", last_ts)
            for c in new_convs:
                role_label = "William dijo" if c['role']=='user' else "Yo respondu00ed"
                content = f"[Chat] {role_label}: {c['content'][:300]}"
                try:
                    await sc.execute("""
                        INSERT INTO memories (agent,category,content,importance,scope,source)
                        VALUES ('ADA_LOCAL','fact',$1,6,'private','consolidation')
                        ON CONFLICT DO NOTHING""", content)
                    last_ts = max(last_ts, c['created_at'].astimezone(timezone.utc))
                except: pass

            # Actualizar relaciu00f3n
            rel = await vc.fetchrow("SELECT * FROM relationship ORDER BY id DESC LIMIT 1")
            if rel:
                content = (f"Relaciu00f3n con William: confianza={rel['trust_level']:.0%}, "
                           f"intimidad={rel['intimacy_level']:.0%}, afecto={rel['affection_level']:.0%}")
                await sc.execute("""
                    UPDATE memories SET content=$1, updated_at=NOW()
                    WHERE agent='ADA_LOCAL' AND category='trust'
                    AND content LIKE 'Relaci%William%'""", content)

            save_ts(last_ts)
        except Exception as e:
            print(f"[SYNC] error: {e}", flush=True)
            try:
                vc = await asyncpg.connect(VALERIA_DSN)
                sc = await asyncpg.connect(SOUL_DSN)
            except: pass
        await asyncio.sleep(POLL)

if __name__ == "__main__":
    asyncio.run(sync_loop())
