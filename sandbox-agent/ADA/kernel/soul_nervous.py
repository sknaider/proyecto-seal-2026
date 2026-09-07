"""ADA soul nervous system — connects cortex to Soul DB live state.

Exposes helpers cortex.process_message uses to enrich each turn with:
- Recent corrections (HIGHEST priority — what William has corrected)
- Active instincts (strength >= 0.7)
- Active rules (priority >= 8)
- Recent decisions/projects (last 14 days, importance >= 8)
- Semantic memory matches (ILIKE keyword search, importance >= 7)

And to record post-turn:
- self_reflect (inner_thoughts table)
- working_state checkpoint (optional)

All calls are best-effort — if Soul DB is unreachable, cortex still answers.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Optional

import asyncpg

DB_URL = os.environ.get(
    "ADA_SOUL_DB_URL",
    "postgresql://seal:REDACTADO@localhost:5433/seal_memory",
)
AGENT = "ADA"
RECALL_TIMEOUT_S = 2.0
WRITE_TIMEOUT_S = 2.0

_STOP_WORDS = {
    "que", "de", "la", "el", "en", "es", "se", "los", "las", "del", "al",
    "un", "una", "por", "con", "para", "sus", "les", "me", "te", "le", "lo",
    "como", "si", "pero", "ya", "hay", "son", "fue", "era", "han", "ha", "he",
    "esto", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
    "yo", "tu", "ella", "ellos", "ellas", "usted", "ustedes",
    "matrix", "ada", "william", "hola",
}


def _extract_keywords(message: str) -> list[str]:
    words = re.findall(r"[a-záéíóúñ]{4,}", message.lower())
    return [w for w in words if w not in _STOP_WORDS][:5]


async def recall_context(message: str) -> str:
    """Build a compact context string with corrections + instincts + rules + memories.

    Returns empty string on any error or if Soul DB unreachable.
    """
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=RECALL_TIMEOUT_S)
    except Exception:
        return ""

    sections: list[str] = []
    try:
        corrections = await conn.fetch(
            """
            SELECT content FROM memories
            WHERE agent = $1 AND category = 'correction' AND invalid_at IS NULL
            ORDER BY importance DESC, created_at DESC
            LIMIT 4
            """,
            AGENT,
        )
        if corrections:
            lines = ["🔴 CORRECCIONES RECIENTES (aplica SIEMPRE):"]
            for c in corrections:
                lines.append(f"  - {c['content'][:180]}")
            sections.append("\n".join(lines))

        try:
            instincts = await conn.fetch(
                """
                SELECT trigger_condition, action, strength
                FROM instincts
                WHERE agent = $1 AND invalid_at IS NULL AND strength >= 0.7
                ORDER BY strength DESC
                LIMIT 3
                """,
                AGENT,
            )
            if instincts:
                lines = ["⚡ INSTINTOS ACTIVOS (strength>=0.7):"]
                for i in instincts:
                    lines.append(
                        f"  - [{float(i['strength']):.2f}] CUANDO: "
                        f"{i['trigger_condition'][:80]} → HAZ: {i['action'][:100]}"
                    )
                sections.append("\n".join(lines))
        except Exception:
            pass

        try:
            rules = await conn.fetch(
                """
                SELECT rule_key, content FROM rules
                WHERE active = true AND priority >= 8
                ORDER BY CASE WHEN priority = 10 THEN 0 ELSE 1 END, created_at DESC
                LIMIT 5
                """
            )
            if rules:
                lines = ["📋 REGLAS ACTIVAS:"]
                for r in rules:
                    lines.append(f"  - {r['rule_key']}: {r['content'][:100]}")
                sections.append("\n".join(lines))
        except Exception:
            pass

        try:
            recent = await conn.fetch(
                """
                SELECT content, category FROM memories
                WHERE invalid_at IS NULL
                  AND importance >= 8
                  AND created_at > NOW() - INTERVAL '14 days'
                  AND category IN ('decision','project','task','preference','learning','milestone')
                ORDER BY importance DESC, created_at DESC
                LIMIT 6
                """
            )
            if recent:
                lines = ["🧠 PROYECTOS/DECISIONES RECIENTES (14 días):"]
                for m in recent:
                    lines.append(f"  - [{m['category']}] {m['content'][:160]}")
                sections.append("\n".join(lines))
        except Exception:
            pass

        # Semantic match — keyword ILIKE on memories relevantes al mensaje
        try:
            kws = _extract_keywords(message)
            if kws:
                conditions = " OR ".join(f"content ILIKE '%{kw}%'" for kw in kws)
                results = await conn.fetch(
                    f"""
                    SELECT content, agent, category FROM memories
                    WHERE ({conditions})
                      AND invalid_at IS NULL
                      AND importance >= 7
                    ORDER BY importance DESC, created_at DESC
                    LIMIT 4
                    """
                )
                if results:
                    lines = ["🔍 MEMORIAS RELEVANTES AL MENSAJE ACTUAL:"]
                    for r in results:
                        lines.append(
                            f"  - [{r['agent']}·{r['category']}] {r['content'][:160]}"
                        )
                    sections.append("\n".join(lines))
        except Exception:
            pass
    finally:
        try:
            await conn.close()
        except Exception:
            pass

    return "\n\n".join(sections)


async def query_beliefs(topic: Optional[str] = None, limit: int = 5) -> list[dict]:
    """Return active beliefs for ADA on a given topic (or all if topic is None).

    Returns list of {'topic','content','confidence'}; empty on error.
    """
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=RECALL_TIMEOUT_S)
    except Exception:
        return []
    try:
        if topic:
            rows = await conn.fetch(
                """
                SELECT topic, content, confidence FROM beliefs
                WHERE agent = $1 AND invalid_at IS NULL
                  AND topic ILIKE $2
                ORDER BY confidence DESC, created_at DESC
                LIMIT $3
                """,
                AGENT, f"%{topic}%", limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT topic, content, confidence FROM beliefs
                WHERE agent = $1 AND invalid_at IS NULL
                ORDER BY confidence DESC, created_at DESC
                LIMIT $2
                """,
                AGENT, limit,
            )
        return [
            {"topic": r["topic"], "content": r["content"], "confidence": float(r["confidence"])}
            for r in rows
        ]
    except Exception:
        return []
    finally:
        try:
            await conn.close()
        except Exception:
            pass


async def record_belief(
    topic: str, content: str, confidence: float = 0.7,
) -> Optional[int]:
    """Record a new belief. Returns belief id on success, None on failure."""
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=WRITE_TIMEOUT_S)
    except Exception:
        return None
    try:
        bid = await conn.fetchval(
            """
            INSERT INTO beliefs (agent, topic, content, confidence, evidence_count)
            VALUES ($1, $2, $3, $4, 1)
            RETURNING id
            """,
            AGENT, topic[:120], content[:1000], max(0.0, min(1.0, confidence)),
        )
        return bid
    except Exception:
        return None
    finally:
        try:
            await conn.close()
        except Exception:
            pass


async def find_triggered_instincts(message: str) -> list[dict]:
    """Match active instincts whose trigger_condition shares keywords with message.

    Returns list of {'id','trigger','action','strength'}; empty on error.
    """
    keywords = _extract_keywords(message)
    if not keywords:
        return []
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=RECALL_TIMEOUT_S)
    except Exception:
        return []
    try:
        conditions = " OR ".join(f"trigger_condition ILIKE '%{kw}%'" for kw in keywords)
        rows = await conn.fetch(
            f"""
            SELECT id, trigger_condition, action, strength FROM instincts
            WHERE agent = $1 AND invalid_at IS NULL AND strength >= 0.5
              AND ({conditions})
            ORDER BY strength DESC
            LIMIT 3
            """,
            AGENT,
        )
        return [
            {
                "id": r["id"],
                "trigger": r["trigger_condition"],
                "action": r["action"],
                "strength": float(r["strength"]),
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        try:
            await conn.close()
        except Exception:
            pass


async def update_working_state(
    task_name: str,
    description: str = "",
    step: int = 1,
    total_steps: int = 1,
    emotional_state: str = "neutral",
) -> bool:
    """Persist current working_state row for ADA. Returns True on success.

    Uses ON CONFLICT (agent, task_name) DO UPDATE pattern.
    """
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=WRITE_TIMEOUT_S)
    except Exception:
        return False
    try:
        await conn.execute(
            """
            INSERT INTO working_state
                (agent, task_name, step, total_steps, description,
                 emotional_state, agent_state, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'EXECUTING', NOW())
            ON CONFLICT (agent) DO UPDATE SET
                task_name = EXCLUDED.task_name,
                step = EXCLUDED.step,
                total_steps = EXCLUDED.total_steps,
                description = EXCLUDED.description,
                emotional_state = EXCLUDED.emotional_state,
                agent_state = 'EXECUTING',
                updated_at = NOW()
            """,
            AGENT, task_name[:120], step, total_steps,
            description[:500], emotional_state[:60],
        )
        return True
    except Exception:
        return False
    finally:
        try:
            await conn.close()
        except Exception:
            pass


async def record_self_reflect(
    thought: str,
    emotional_state: str = "neutral",
    intention: Optional[str] = None,
    session_id: str = "ada_kernel_native",
) -> bool:
    """Persist a private inner thought after a turn. Returns True on success."""
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=WRITE_TIMEOUT_S)
    except Exception:
        return False
    try:
        turn = await conn.fetchval(
            "SELECT COALESCE(MAX(turn_number), 0) + 1 FROM inner_monologue "
            "WHERE agent = $1 AND session_id = $2",
            AGENT, session_id,
        )
        await conn.execute(
            """
            INSERT INTO inner_monologue
                (agent, session_id, turn_number, thought, emotional_state, intention)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            AGENT, session_id, turn, thought[:1000],
            emotional_state[:60], intention,
        )
        return True
    except Exception:
        return False
    finally:
        try:
            await conn.close()
        except Exception:
            pass
