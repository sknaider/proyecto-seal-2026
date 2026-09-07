"""JARVIS Soul CLI — acceso directo a Soul DB sin depender del MCP.
Uso:
  python3 jarvis_soul.py memory_store "contenido de la memoria" [importancia] [scope]
  python3 jarvis_soul.py memory_search "query"
  python3 jarvis_soul.py self_reflect "pensamiento" "estado_emocional"
  python3 jarvis_soul.py snapshot
"""
from __future__ import annotations
import asyncio, asyncpg, hashlib, hmac, json, os, sys
from datetime import datetime, timezone

_DSN = os.environ.get(
    "SEAL_PG_DSN",
    "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
)
_AGENT = "JARVIS"
_ORG = "william_seal"
_NAMESPACE = f"{_ORG}::{_AGENT}"


async def _conn():
    return await asyncpg.connect(_DSN)


async def memory_store(content: str, importance: int = 7, scope: str = "private",
                       category: str = "observation") -> dict:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """INSERT INTO memories
               (agent, content, importance, scope, category, source, created_at)
               VALUES ($1, $2, $3, $4, $5, 'jarvis_cli', NOW())
               RETURNING id, created_at""",
            _AGENT, content, importance, scope, category,
        )
        return {"id": row["id"], "ts": str(row["created_at"]), "ok": True}
    finally:
        await conn.close()


async def memory_search(query: str, limit: int = 5) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(
            """SELECT id, content, importance, scope, category, created_at
               FROM memories
               WHERE agent = $1
                 AND content ILIKE $2
               ORDER BY importance DESC, created_at DESC
               LIMIT $3""",
            _AGENT, f"%{query}%", limit,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def self_reflect(thought: str, emotional_state: str = "neutral") -> dict:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            """INSERT INTO inner_monologue
               (agent, thought, emotional_state, created_at)
               VALUES ($1, $2, $3, NOW())
               RETURNING id, created_at""",
            _AGENT, thought, emotional_state,
        )
        # También guardar como memoria con alta importancia
        await conn.execute(
            """INSERT INTO memories
               (agent, content, importance, scope, category, source, created_at)
               VALUES ($1, $2, 8, 'private', 'insight', 'self_reflect', NOW())""",
            _AGENT, f"[REFLECT:{emotional_state}] {thought}",
        )
        return {"id": row["id"], "ts": str(row["created_at"]), "ok": True}
    finally:
        await conn.close()


async def snapshot() -> dict:
    conn = await _conn()
    try:
        mem_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE agent=$1", _AGENT
        )
        thoughts = await conn.fetch(
            """SELECT thought, emotional_state, created_at
               FROM inner_monologue WHERE agent=$1
               ORDER BY created_at DESC LIMIT 3""",
            _AGENT,
        )
        ocean = await conn.fetchrow(
            "SELECT * FROM ocean_base_values WHERE agent=$1 LIMIT 1", _AGENT
        )
        return {
            "agent": _AGENT,
            "memories": mem_count,
            "recent_thoughts": [dict(t) for t in thoughts],
            "ocean": dict(ocean) if ocean else None,
        }
    finally:
        await conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "memory_store":
        content = sys.argv[2] if len(sys.argv) > 2 else ""
        importance = int(sys.argv[3]) if len(sys.argv) > 3 else 7
        scope = sys.argv[4] if len(sys.argv) > 4 else "private"
        category = sys.argv[5] if len(sys.argv) > 5 else "fact"
        result = asyncio.run(memory_store(content, importance, scope, category))
        print(json.dumps(result, default=str))

    elif cmd == "memory_search":
        query = sys.argv[2] if len(sys.argv) > 2 else ""
        rows = asyncio.run(memory_search(query))
        for r in rows:
            print(f"[{r['importance']}] {r['content'][:100]}")

    elif cmd == "self_reflect":
        thought = sys.argv[2] if len(sys.argv) > 2 else ""
        emotion = sys.argv[3] if len(sys.argv) > 3 else "neutral"
        result = asyncio.run(self_reflect(thought, emotion))
        print(json.dumps(result, default=str))

    elif cmd == "snapshot":
        result = asyncio.run(snapshot())
        print(json.dumps(result, default=str, indent=2))

    else:
        print(f"Comando desconocido: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
