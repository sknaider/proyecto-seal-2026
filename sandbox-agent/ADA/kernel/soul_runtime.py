"""SEAL Soul Runtime — connections nerviosas para agentes nativos.

Uso desde ada_kernel_main.py + cortex.py + futuros agentes nativos.

Conexiones:
  - boot_context(agent) → carga identidad + OCEAN + relations + last inner thought + diary + rules + beliefs
  - memory_search(agent, query, k) → recuerdos relevantes via embedding semántico (delegado a memory_store backend)
  - memory_store(agent, category, content, importance, scope) → persistir memoria
  - self_reflect(agent, thought, emotional_state) → inner monologue
  - event_log_append(agent, event_type, content) → time-series log
  - heartbeat_write(agent, alive, last_dispatch_ts) → JSON heartbeat file
  - heartbeat_loop(agent, interval_s) → asyncio task que escribe heartbeat cada N segundos

Stack: asyncpg directo a Soul DB (postgresql://seal:seal_memory_2026@localhost:5433/seal_memory).
Schema: soul_v3.

Failure mode: cada función es fail-soft. Si DB no responde, log y continúa.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_URL = os.environ.get(
    "SEAL_DB_URL",
    "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory",
)
SCHEMA = os.environ.get("SEAL_SCHEMA", "soul_v3")
HEARTBEAT_DIR = Path("/home/dadito/IA/proyecto-seal/messages")


_pool_singleton = None


async def _get_pool():
    global _pool_singleton
    if _pool_singleton is not None:
        return _pool_singleton
    try:
        import asyncpg
    except ImportError:
        return None
    try:
        _pool_singleton = await asyncpg.create_pool(
            DB_URL,
            min_size=1,
            max_size=4,
            server_settings={"search_path": SCHEMA},
        )
        return _pool_singleton
    except Exception as ex:
        print(f"[soul_runtime] pool create failed: {ex}", flush=True)
        return None


async def boot_context(agent: str) -> dict[str, Any]:
    """Load agent identity, OCEAN, relationships, last inner thought, diary, rules, beliefs.

    Returns dict with keys: identity, ocean, relationships, last_inner, last_diary,
    critical_rules, beliefs, prompt_block (formatted text for system prompt injection).
    """
    result: dict[str, Any] = {
        "identity": None,
        "ocean": None,
        "relationships": [],
        "last_inner": None,
        "last_diary": None,
        "critical_rules": [],
        "beliefs": [],
        "prompt_block": "",
    }
    pool = await _get_pool()
    if pool is None:
        return result

    sections: list[str] = [f"## Soul Context — {agent}"]

    try:
        async with pool.acquire() as conn:
            ident = await conn.fetchrow(
                "SELECT personality, boot_context, philosophy, ocean_scores "
                "FROM identity WHERE agent = $1",
                agent,
            )
            if ident:
                result["identity"] = {
                    "personality": ident["personality"],
                    "boot_context": ident["boot_context"],
                    "philosophy": ident["philosophy"],
                }
                if ident["boot_context"]:
                    sections.append(ident["boot_context"][:500])
                if ident["ocean_scores"]:
                    ocean_raw = ident["ocean_scores"]
                    ocean = json.loads(ocean_raw) if isinstance(ocean_raw, str) else ocean_raw
                    result["ocean"] = ocean
                    ocean_str = ", ".join(f"{k}={v}" for k, v in sorted(ocean.items()))
                    sections.append(f"OCEAN: {ocean_str}")

            rels = await conn.fetch(
                "SELECT person, trust_level, communication_style, dynamic "
                "FROM relationships WHERE agent = $1",
                agent,
            )
            if rels:
                result["relationships"] = [dict(r) for r in rels]
                sections.append("\n## Relationships")
                for r in rels:
                    sections.append(
                        f"- {r['person']}: trust={r['trust_level']:.1f}, "
                        f"{r['communication_style']}, {(r['dynamic'] or '')[:80]}"
                    )

            inner = await conn.fetchrow(
                "SELECT thought, emotional_state, created_at "
                "FROM inner_monologue WHERE agent = $1 "
                "ORDER BY created_at DESC LIMIT 1",
                agent,
            )
            if inner:
                result["last_inner"] = {
                    "thought": inner["thought"],
                    "emotional_state": inner["emotional_state"],
                    "created_at": inner["created_at"].isoformat() if inner["created_at"] else None,
                }
                state_tag = f" [{inner['emotional_state']}]" if inner["emotional_state"] else ""
                sections.append(f"\n## Last Inner Thought{state_tag}")
                sections.append(f"{(inner['thought'] or '')[:200]}")

            diary = await conn.fetchrow(
                "SELECT entry, mood, session_date "
                "FROM diary WHERE agent = $1 "
                "ORDER BY created_at DESC LIMIT 1",
                agent,
            )
            if diary:
                result["last_diary"] = {
                    "entry": diary["entry"],
                    "mood": diary["mood"],
                    "session_date": str(diary["session_date"]) if diary["session_date"] else None,
                }
                sections.append(f"\n## Last Diary (mood={diary['mood']})")
                sections.append((diary["entry"] or "")[:300])

            rules = await conn.fetch(
                "SELECT rule_key, content FROM rules "
                "WHERE active = TRUE AND priority = 10 "
                "ORDER BY created_at DESC LIMIT 5"
            )
            if rules:
                result["critical_rules"] = [{"key": r["rule_key"], "content": r["content"]} for r in rules]
                sections.append("\n## Critical Rules")
                for r in rules:
                    sections.append(f"- {r['rule_key']}: {(r['content'] or '')[:120]}")

            beliefs = await conn.fetch(
                "SELECT topic, category, LEFT(content, 120) as belief_short, confidence "
                "FROM opinions WHERE agent = $1 AND active = TRUE AND invalid_at IS NULL "
                "ORDER BY confidence DESC, evidence_count DESC LIMIT 5",
                agent,
            )
            if beliefs:
                result["beliefs"] = [dict(b) for b in beliefs]
                sections.append("\n## Active Beliefs")
                for b in beliefs:
                    sections.append(
                        f"- [{b['topic']}/{b['category']}] {b['belief_short']} (conf={b['confidence']:.2f})"
                    )
    except Exception as ex:
        print(f"[soul_runtime/boot_context] error: {ex}", flush=True)

    result["prompt_block"] = "\n".join(sections)
    return result


async def memory_store(
    agent: str,
    category: str,
    content: str,
    importance: int = 5,
    source: str = "conversation",
    scope: str = "private",
) -> int | None:
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO memories (agent, category, content, importance, source, scope, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, NOW())
                   RETURNING id""",
                agent, category, content, importance, source, scope,
            )
            return int(row["id"]) if row else None
    except Exception as ex:
        print(f"[soul_runtime/memory_store] error: {ex}", flush=True)
        return None


async def memory_search(agent: str, query: str, k: int = 3) -> list[dict[str, Any]]:
    """Lightweight keyword-based memory search (full embedding search lives in MCP).

    Used at cortex pre-LLM time to inject relevant memories into the prompt.
    Returns up to k recent memories matching the query (full text search on content).
    """
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT category, content, importance, created_at
                   FROM memories
                   WHERE agent = $1 AND content ILIKE $2
                   ORDER BY importance DESC, created_at DESC LIMIT $3""",
                agent, f"%{query[:60]}%", k,
            )
            return [dict(r) for r in rows]
    except Exception as ex:
        print(f"[soul_runtime/memory_search] error: {ex}", flush=True)
        return []


async def self_reflect(
    agent: str,
    thought: str,
    emotional_state: str = "neutral",
    intention: str | None = None,
) -> int | None:
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO inner_monologue (agent, thought, emotional_state, intention, created_at)
                   VALUES ($1, $2, $3, $4, NOW())
                   RETURNING id""",
                agent, thought, emotional_state, intention,
            )
            return int(row["id"]) if row else None
    except Exception as ex:
        print(f"[soul_runtime/self_reflect] error: {ex}", flush=True)
        return None


async def event_log_append(
    agent: str,
    event_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> int | None:
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO event_log (agent, event_type, content, metadata, created_at)
                   VALUES ($1, $2, $3, $4, NOW())
                   RETURNING id""",
                agent, event_type, content,
                json.dumps(metadata) if metadata else None,
            )
            return int(row["id"]) if row else None
    except Exception as ex:
        print(f"[soul_runtime/event_log_append] error: {ex}", flush=True)
        return None


def heartbeat_write(
    agent: str,
    alive: bool = True,
    pid: int | None = None,
    last_dispatch_ts: str | None = None,
    note: str = "",
) -> None:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    path = HEARTBEAT_DIR / f"{agent.lower()}_claude_heartbeat.json"
    data = {
        "agent": agent,
        "alive": alive,
        "pid": pid or os.getpid(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_dispatch_ts": last_dispatch_ts,
        "note": note,
    }
    try:
        path.write_text(json.dumps(data))
    except Exception as ex:
        print(f"[soul_runtime/heartbeat_write] error: {ex}", flush=True)


async def heartbeat_loop(agent: str, interval_s: float = 60.0, get_last_dispatch=None) -> None:
    """Background asyncio task — writes heartbeat every interval_s.

    `get_last_dispatch` is an optional callable returning the last dispatch ISO ts.
    """
    while True:
        try:
            ts = get_last_dispatch() if get_last_dispatch else None
            heartbeat_write(agent, alive=True, last_dispatch_ts=ts)
        except Exception as ex:
            print(f"[soul_runtime/heartbeat_loop] error: {ex}", flush=True)
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            heartbeat_write(agent, alive=False, note="heartbeat_loop cancelled")
            raise


async def close() -> None:
    global _pool_singleton
    if _pool_singleton is not None:
        try:
            await _pool_singleton.close()
        except Exception:
            pass
        _pool_singleton = None
