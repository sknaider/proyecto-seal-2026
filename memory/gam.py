"""GAM — Goal-Action Model v1.0
Spec: memory/spec_gam_v1.md (JARVIS, aprobado William 09-may-2026)
Implementa: ALICE (10-may-2026)

Schema real verificado (difiere del spec):
  gam_topics:      id, agent, topic, summary, relevance_score, event_count, first_seen, last_updated
  gam_event_graph: id, agent, topic_id, event, event_timestamp, related_event_ids, causal_direction, metadata, created_at

DAG mediante related_event_ids (ARRAY de IDs) + causal_direction.
Status de eventos en metadata JSONB: {"status": "pending|in_progress|completed|cancelled"}.
"""
from __future__ import annotations

try:
    from .db import get_pool
except ImportError:
    from db import get_pool


async def create_goal(
    agent: str,
    topic: str,
    summary: str = "",
    relevance_score: float = 0.5,
) -> int:
    """Crea una meta de alto nivel en gam_topics. Retorna topic_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.gam_topics (agent, topic, summary, relevance_score)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            agent,
            topic,
            summary or None,
            float(relevance_score),
        )
        return row["id"]


async def add_action(
    agent: str,
    topic_id: int,
    action: str,
    parent_action_ids: list[int] | None = None,
    causal_direction: str = "cause",
    metadata: dict | None = None,
) -> int:
    """Agrega una acción/nodo al grafo de eventos. Retorna action_id (event_id).

    parent_action_ids: IDs de acciones previas (predecessors en el grafo).
    causal_direction: 'cause' | 'effect' | 'related' (CHECK constraint BD).
    """
    import json as _json
    meta = dict(metadata or {})
    meta.setdefault("status", "pending")
    pool = await get_pool()
    async with pool.acquire() as conn:
        owns_topic = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM soul_v3.gam_topics WHERE id=$1 AND agent=$2)",
            topic_id,
            agent,
        )
        if not owns_topic:
            raise PermissionError(f"GAM topic {topic_id} is not owned by {agent}")
        parent_ids = sorted(set(parent_action_ids or []))
        if parent_ids:
            owned_parent_count = await conn.fetchval(
                """SELECT count(*) FROM soul_v3.gam_event_graph
                   WHERE id = ANY($1::bigint[]) AND agent = $2""",
                parent_ids,
                agent,
            )
            if owned_parent_count != len(parent_ids):
                raise PermissionError("one or more GAM parent actions are not owned by the caller")
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.gam_event_graph
                (agent, topic_id, event, related_event_ids, causal_direction, metadata)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            agent,
            topic_id,
            action,
            parent_action_ids or [],
            causal_direction or None,
            _json.dumps(meta),
        )
        await conn.execute(
            """UPDATE soul_v3.gam_topics
               SET event_count = event_count + 1, last_updated = now()
               WHERE id = $1 AND agent = $2""",
            topic_id,
            agent,
        )
        return row["id"]


async def complete_action(agent: str, action_id: int, result: str = "") -> None:
    """Marca una acción como completada en metadata."""
    import json as _json
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT metadata FROM soul_v3.gam_event_graph WHERE id = $1 AND agent = $2",
            action_id,
            agent,
        )
        if row is None:
            raise PermissionError(f"GAM action {action_id} is not owned by {agent}")
        meta = _json.loads(row["metadata"]) if row["metadata"] else {}
        meta["status"] = "completed"
        if result:
            meta["result"] = result
        await conn.execute(
            "UPDATE soul_v3.gam_event_graph SET metadata = $1 WHERE id = $2 AND agent = $3",
            _json.dumps(meta),
            action_id,
            agent,
        )


async def get_active_goals(agent: str | None = None) -> list[dict]:
    """Retorna metas activas (relevance_score > 0) por agente."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if agent:
            rows = await conn.fetch(
                """
                SELECT id, agent, topic, summary, relevance_score, event_count, first_seen, last_updated
                FROM soul_v3.gam_topics
                WHERE agent = $1 AND relevance_score > 0
                ORDER BY relevance_score DESC, last_updated DESC
                """,
                agent,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, agent, topic, summary, relevance_score, event_count, first_seen, last_updated
                FROM soul_v3.gam_topics
                WHERE relevance_score > 0
                ORDER BY relevance_score DESC, last_updated DESC
                """
            )
        return [dict(r) for r in rows]


async def get_actions(agent: str, topic_id: int, status: str | None = None) -> list[dict]:
    """Retorna acciones de una meta. Filtra por status si se provee."""
    import json as _json
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, agent, event, related_event_ids, causal_direction, metadata, created_at
            FROM soul_v3.gam_event_graph
            WHERE topic_id = $1 AND agent = $2
            ORDER BY created_at ASC
            """,
            topic_id,
            agent,
        )
    result = []
    for r in rows:
        d = dict(r)
        d["metadata"] = _json.loads(d["metadata"]) if d["metadata"] else {}
        if status and d["metadata"].get("status") != status:
            continue
        result.append(d)
    return result


async def close_goal(agent: str, topic_id: int) -> None:
    """Cierra una meta (relevance_score → 0)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            """UPDATE soul_v3.gam_topics
               SET relevance_score = 0, last_updated = now()
               WHERE id = $1 AND agent = $2""",
            topic_id,
            agent,
        )
        if status == "UPDATE 0":
            raise PermissionError(f"GAM topic {topic_id} is not owned by {agent}")
