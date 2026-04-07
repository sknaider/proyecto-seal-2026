"""
Soul Lite — PgVector Adapter
Replaces AsyncQdrantClient with PostgreSQL + pgvector queries.
PostgreSQL is already the source of truth (memories table).
This adapter queries PG directly instead of maintaining a Qdrant mirror.

Usage:
    Set SOUL_LITE=true in environment to activate.
    get_qdrant() will return PgVectorAdapter instead of AsyncQdrantClient.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger("seal-memory.lite")


# ── Qdrant-compatible response types ──
# These mimic qdrant_client response objects so existing code works unchanged.

@dataclass
class ScoredPoint:
    """Mimics qdrant_client.models.ScoredPoint"""
    id: int
    score: float
    payload: dict = field(default_factory=dict)
    vector: list | None = None


@dataclass
class Record:
    """Mimics qdrant_client.models.Record (used by retrieve)"""
    id: int
    payload: dict = field(default_factory=dict)
    vector: list | None = None


@dataclass
class QueryResponse:
    """Mimics qdrant_client.models.QueryResponse"""
    points: list[ScoredPoint] = field(default_factory=list)


class PgVectorAdapter:
    """
    Drop-in replacement for AsyncQdrantClient that queries PostgreSQL + pgvector.

    The memories table already has:
      - id (int), agent, category, content, embedding vector(768),
        importance, source, created_at, valence, arousal, dominance,
        scope, confidence_score, invalid_at, metadata (jsonb)

    Qdrant payload fields map to columns in memories table.
    """

    def __init__(self, get_pool_fn):
        """
        Args:
            get_pool_fn: async callable that returns asyncpg pool
        """
        self._get_pool = get_pool_fn

    async def query_points(
        self,
        collection_name: str,
        query: list[float],
        limit: int = 10,
        query_filter: Any = None,
        score_threshold: float | None = None,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> QueryResponse:
        """Vector similarity search using pgvector cosine distance."""
        pool = await self._get_pool()

        # Build WHERE clause from Qdrant filter
        where_clauses = ["invalid_at IS NULL", "embedding IS NOT NULL"]
        params: list = []
        param_idx = 1

        if query_filter:
            for condition in _extract_conditions(query_filter):
                col = _payload_key_to_column(condition["key"])
                if col:
                    op = condition.get("op", "eq")
                    sql_op = {"eq": "=", "gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}.get(op, "=")
                    where_clauses.append(f"{col} {sql_op} ${param_idx}")
                    params.append(condition["value"])
                    param_idx += 1

        where_sql = " AND ".join(where_clauses)

        # Vector literal for pgvector — asyncpg can't pass vector as parameter
        vec_literal = "'" + "[" + ",".join(str(x) for x in query) + "]" + "'"

        score_filter = ""
        if score_threshold is not None:
            score_filter = f"AND (1 - (embedding <=> {vec_literal}::vector)) >= {score_threshold}"

        sql = f"""
            SELECT id, content, agent, category, importance, source,
                   created_at, valence, arousal, dominance, scope,
                   confidence_score, metadata,
                   (1 - (embedding <=> {vec_literal}::vector)) as score
            FROM memories
            WHERE {where_sql} {score_filter}
            ORDER BY embedding <=> {vec_literal}::vector
            LIMIT {limit}
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        points = []
        for row in rows:
            score = float(row["score"])
            if score_threshold is not None and score < score_threshold:
                continue
            payload = _row_to_payload(row) if with_payload else {}
            vector = json.loads(row["embedding"]) if with_vectors and row.get("embedding") else None
            points.append(ScoredPoint(id=row["id"], score=score, payload=payload, vector=vector))

        return QueryResponse(points=points)

    async def upsert(self, collection_name: str, points: list) -> None:
        """
        Upsert points. In Soul Lite, PG is already the source of truth.
        This is called AFTER the PG insert in memory_store, so for new memories
        this is a no-op. For updates (set_payload), we handle in set_payload().

        For cases where upsert is called to sync embeddings, we update PG.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            for point in points:
                vec = point.vector if hasattr(point, 'vector') else None
                pid = point.id if hasattr(point, 'id') else None
                if vec is not None and pid is not None:
                    # Update embedding if it changed
                    await conn.execute(
                        "UPDATE memories SET embedding = $1 WHERE id = $2",
                        json.dumps(vec), pid
                    )

    async def set_payload(
        self, collection_name: str, payload: dict, points: list[int]
    ) -> None:
        """Update payload fields → update corresponding PG columns."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            for pid in points:
                for key, value in payload.items():
                    col = _payload_key_to_column(key)
                    if col:
                        await conn.execute(
                            f"UPDATE memories SET {col} = $1 WHERE id = $2",
                            value, pid
                        )
                    else:
                        # Store in metadata jsonb
                        await conn.execute(
                            """UPDATE memories
                               SET metadata = jsonb_set(
                                   COALESCE(metadata, '{}'::jsonb),
                                   $1, $2::jsonb
                               ) WHERE id = $3""",
                            [key], json.dumps(value), pid
                        )

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: Any = None,
        limit: int = 100,
        with_payload: bool = True,
        with_vectors: bool = False,
        offset: Any = None,
    ) -> tuple[list[Record], Any]:
        """Scroll through all points matching filter."""
        pool = await self._get_pool()

        where_clauses = ["invalid_at IS NULL"]
        params: list = []
        param_idx = 1

        if scroll_filter:
            for condition in _extract_conditions(scroll_filter):
                col = _payload_key_to_column(condition["key"])
                if col:
                    op = condition.get("op", "eq")
                    sql_op = {"eq": "=", "gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}.get(op, "=")
                    where_clauses.append(f"{col} {sql_op} ${param_idx}")
                    params.append(condition["value"])
                    param_idx += 1

        offset_clause = ""
        if offset is not None:
            offset_clause = f"AND id > ${param_idx}"
            params.append(offset)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT id, content, agent, category, importance, source,
                   created_at, valence, arousal, dominance, scope,
                   confidence_score, metadata
                   {"," if with_vectors else ""} {"embedding" if with_vectors else ""}
            FROM memories
            WHERE {where_sql} {offset_clause}
            ORDER BY id
            LIMIT {limit}
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        records = []
        for row in rows:
            payload = _row_to_payload(row) if with_payload else {}
            vector = json.loads(row["embedding"]) if with_vectors and row.get("embedding") else None
            records.append(Record(id=row["id"], payload=payload, vector=vector))

        next_offset = records[-1].id if records else None
        return records, next_offset

    async def retrieve(
        self,
        collection_name: str,
        ids: list[int],
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> list[Record]:
        """Retrieve specific points by ID."""
        if not ids:
            return []
        pool = await self._get_pool()
        placeholders = ", ".join(f"${i+1}" for i in range(len(ids)))

        sql = f"""
            SELECT id, content, agent, category, importance, source,
                   created_at, valence, arousal, dominance, scope,
                   confidence_score, metadata
                   {"," if with_vectors else ""} {"embedding" if with_vectors else ""}
            FROM memories
            WHERE id IN ({placeholders})
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *ids)

        records = []
        for row in rows:
            payload = _row_to_payload(row) if with_payload else {}
            vector = json.loads(row["embedding"]) if with_vectors and row.get("embedding") else None
            records.append(Record(id=row["id"], payload=payload, vector=vector))

        return records

    async def delete(self, collection_name: str, points_selector: Any) -> None:
        """
        Delete points. In Soul Lite, we mark as invalid in PG
        (soft delete, same as current behavior).
        """
        pool = await self._get_pool()
        ids = _extract_point_ids(points_selector)
        if ids:
            placeholders = ", ".join(f"${i+1}" for i in range(len(ids)))
            async with pool.acquire() as conn:
                await conn.execute(
                    f"UPDATE memories SET invalid_at = NOW() WHERE id IN ({placeholders})",
                    *ids
                )


# ── Helper functions ──

def _payload_key_to_column(key: str) -> str | None:
    """Map Qdrant payload keys to PostgreSQL column names."""
    mapping = {
        "agent": "agent",
        "category": "category",
        "content": "content",
        "importance": "importance",
        "source": "source",
        "created_at": "created_at",
        "valence": "valence",
        "arousal": "arousal",
        "dominance": "dominance",
        "scope": "scope",
        "confidence": "confidence_score",
        "confidence_score": "confidence_score",
        "utility": "importance",  # utility maps to importance in PG
        "pg_id": "id",
    }
    return mapping.get(key)


def _row_to_payload(row) -> dict:
    """Convert asyncpg Row to Qdrant-style payload dict."""
    payload = {
        "pg_id": row["id"],
        "agent": row["agent"],
        "category": row["category"],
        "content": row["content"],
        "importance": row["importance"],
        "source": row.get("source"),
        "valence": row.get("valence"),
        "arousal": row.get("arousal"),
        "dominance": row.get("dominance"),
        "scope": row.get("scope"),
        "confidence_score": row.get("confidence_score"),
    }
    if row.get("created_at"):
        payload["created_at"] = row["created_at"].isoformat()
    # Merge metadata jsonb fields into payload
    if row.get("metadata"):
        try:
            meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
            payload.update(meta)
        except (json.JSONDecodeError, TypeError):
            pass
    return payload


def _extract_conditions(query_filter) -> list[dict]:
    """Extract field conditions from Qdrant Filter object.

    Note: Pydantic model fields always exist even when None, so we check
    `cond.match is not None` rather than `hasattr(cond, 'match')`.
    Range conditions are converted to SQL WHERE clauses with gte/lte bounds.
    """
    conditions = []
    if not (hasattr(query_filter, 'must') and query_filter.must):
        return conditions

    for cond in query_filter.must:
        if not hasattr(cond, 'key'):
            continue
        # Match condition (exact value)
        if getattr(cond, 'match', None) is not None:
            conditions.append({
                "key": cond.key,
                "value": cond.match.value,
                "op": "eq",
            })
        # Range condition — emit gte/lte as separate entries
        elif getattr(cond, 'range', None) is not None:
            r = cond.range
            if r.gte is not None:
                conditions.append({"key": cond.key, "value": r.gte, "op": "gte"})
            if r.gt is not None:
                conditions.append({"key": cond.key, "value": r.gt, "op": "gt"})
            if r.lte is not None:
                conditions.append({"key": cond.key, "value": r.lte, "op": "lte"})
            if r.lt is not None:
                conditions.append({"key": cond.key, "value": r.lt, "op": "lt"})

    return conditions


def _extract_point_ids(points_selector) -> list[int]:
    """Extract point IDs from various Qdrant selector types."""
    if hasattr(points_selector, 'points'):
        return list(points_selector.points)
    if isinstance(points_selector, list):
        return points_selector
    return []
