"""
Soul Lite — PgVector Adapter v2
Replaces AsyncQdrantClient with PostgreSQL + pgvector queries.
PostgreSQL is the source of truth (memories table).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger("seal-memory.lite")


# ── Qdrant-compatible response types (drop-in replacements) ──

@dataclass
class ScoredPoint:
    """Mimics qdrant_client.models.ScoredPoint"""
    id: int
    score: float
    payload: dict = field(default_factory=dict)
    vector: list | None = None


@dataclass
class Record:
    """Mimics qdrant_client.models.Record"""
    id: int
    payload: dict = field(default_factory=dict)
    vector: list | None = None


@dataclass
class QueryResponse:
    """Mimics qdrant_client.models.QueryResponse"""
    points: list[ScoredPoint] = field(default_factory=list)


class PgVectorAdapter:
    """
    Drop-in replacement for AsyncQdrantClient using PostgreSQL + pgvector.

    Supports all filter patterns used in mcp_server_v3.py:
      - must/must_not FieldCondition (match, range)
      - HasIdCondition (id IN ...)
      - Nested Filter(should=[...]) inside must (OR logic)
      - Special 'invalid' key → invalid_at IS NULL / IS NOT NULL
    """

    def __init__(self, get_pool_fn):
        self._get_pool = get_pool_fn

    @staticmethod
    async def _rescate_exacto(conn, sql: str, params, rows_ann):
        """Repite la búsqueda SIN el índice aproximado cuando el filtro lo dejó vacío.

        **El defecto (medido por ADA el 9-sep-2026).** La búsqueda vectorial rápida usa
        un índice HNSW. Cuando además hay un filtro —«sólo memorias de ADA»— el índice
        recorre un vecindario y el filtro se lleva puesto todo lo que encontró: no
        devuelve pocos resultados, devuelve **cero**. Aislado, mismo filtro, mismo
        agente y con 11.302 memorias disponibles:

            pregunta limpia         sin filtro 10   con filtro 5
            con relleno emocional   sin filtro 10   con filtro 0   <- se vacia

        En la literatura se llama *recall cliff* del ANN filtrado. Efecto sobre William,
        que escribe con carga emocional: 13 de 20 preguntas técnicas se quedaban **sin
        ninguna respuesta**.

        **Esto ya estaba diagnosticado y nunca se arregló.** El test
        `test_filtered_ann_starvation_uses_exact_fallback` nombra el problema y exige
        esta solución; estaba en ROJO y ningún manifiesto lo declaraba, así que nadie se
        enteró. El parche vivía en `seal_bench.py:440` —dentro del benchmark— y nunca
        cruzó a producción.

        Se dispara sólo cuando el camino rápido trajo MENOS de lo pedido, así que el
        caso normal no paga nada. Cuando se dispara, un barrido exacto es caro; es el
        precio de devolver algo en vez de mentir con un cero.
        """
        try:
            async with conn.transaction():
                # `hnsw.iterative_scan` (pgvector >= 0.8) hace que el índice siga
                # recorriendo hasta juntar los k que sobreviven al filtro.
                await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                await conn.execute("SET LOCAL enable_indexscan = off")
                rows = await conn.fetch(sql, *params)
        except Exception:
            # Un pgvector viejo no conoce ese parámetro y aborta la transacción. El
            # barrido exacto NO depende de él, así que se reintenta sin esa línea: sin
            # este camino, un servidor viejo se quedaría con el cero del índice.
            async with conn.transaction():
                await conn.execute("SET LOCAL enable_indexscan = off")
                rows = await conn.fetch(sql, *params)
        # El rescate nunca puede devolver MENOS que el camino rápido.
        return rows if len(rows) >= len(rows_ann) else rows_ann

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
        where_clauses, params = _build_where(query_filter)

        vec_literal = "[" + ",".join(str(x) for x in query) + "]"

        score_filter = ""
        if score_threshold is not None:
            score_filter = f"AND (1 - (embedding <=> '{vec_literal}'::vector)) >= {score_threshold}"

        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        # Exclusión de memorias envenenadas, con el mismo patrón que ya usa la rama
        # léxica en `active_recall_hook.py`. Faltaba acá: medido el 9-sep-2026, hay 66
        # memorias con embebido que la rama léxica descarta por `review` o
        # `quarantine_candidate` y que la rama VECTORIAL sí podía devolver. Dos caminos
        # hacia la misma memoria no pueden tener distinta idea de qué es seguro.
        sin_veneno = """
              AND NOT EXISTS (
                SELECT 1 FROM soul_v3.memory_poisoning_feedback poison
                WHERE poison.memory_id = memories.id
                  AND poison.content_hash_sha256 = trim(memories.content_hash_sha256)
                  AND poison.decision IN ('review','quarantine_candidate')
              )
        """

        sql = f"""
            SELECT id, content, agent, category, importance, source,
                   created_at, valence, arousal, dominance, scope,
                   confidence_score, metadata,
                   (1 - (embedding <=> '{vec_literal}'::vector)) as score
            FROM memories
            WHERE embedding IS NOT NULL AND {where_sql} {score_filter}
            {sin_veneno}
            ORDER BY embedding <=> '{vec_literal}'::vector
            LIMIT {limit}
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            if len(rows) < limit:
                rows = await self._rescate_exacto(conn, sql, params, rows)

        points = []
        for row in rows:
            score = float(row["score"])
            if score_threshold is not None and score < score_threshold:
                continue
            payload = _row_to_payload(row) if with_payload else {}
            vec = json.loads(row["embedding"]) if with_vectors and row.get("embedding") else None
            points.append(ScoredPoint(id=row["id"], score=score, payload=payload, vector=vec))

        return QueryResponse(points=points)

    async def upsert(self, collection_name: str, points: list) -> None:
        """Update embeddings in PostgreSQL (PG is source of truth)."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            for point in points:
                vec = getattr(point, 'vector', None)
                pid = getattr(point, 'id', None)
                if vec is not None and pid is not None:
                    await conn.execute(
                        "UPDATE memories SET embedding = $1 WHERE id = $2",
                        json.dumps(vec), pid
                    )

    async def set_payload(
        self, collection_name: str, payload: dict, points: list[int]
    ) -> None:
        """Update payload fields → corresponding PostgreSQL columns."""
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
        """Scroll through records matching filter."""
        pool = await self._get_pool()
        where_clauses, params = _build_where(scroll_filter)

        if offset is not None:
            where_clauses.append(f"id > ${len(params) + 1}")
            params.append(offset)

        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"
        extra_cols = ", embedding" if with_vectors else ""

        sql = f"""
            SELECT id, content, agent, category, importance, source,
                   created_at, valence, arousal, dominance, scope,
                   confidence_score, metadata{extra_cols}
            FROM memories
            WHERE {where_sql}
            ORDER BY id
            LIMIT {limit}
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        records = []
        for row in rows:
            payload = _row_to_payload(row) if with_payload else {}
            vec = json.loads(row["embedding"]) if with_vectors and row.get("embedding") else None
            records.append(Record(id=row["id"], payload=payload, vector=vec))

        next_offset = records[-1].id if records else None
        return records, next_offset

    async def retrieve(
        self,
        collection_name: str,
        ids: list[int],
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> list[Record]:
        """Retrieve specific records by ID."""
        if not ids:
            return []
        pool = await self._get_pool()
        placeholders = ", ".join(f"${i+1}" for i in range(len(ids)))
        extra_cols = ", embedding" if with_vectors else ""

        sql = f"""
            SELECT id, content, agent, category, importance, source,
                   created_at, valence, arousal, dominance, scope,
                   confidence_score, metadata{extra_cols}
            FROM memories
            WHERE id IN ({placeholders})
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *ids)

        records = []
        for row in rows:
            payload = _row_to_payload(row) if with_payload else {}
            vec = json.loads(row["embedding"]) if with_vectors and row.get("embedding") else None
            records.append(Record(id=row["id"], payload=payload, vector=vec))
        return records

    async def delete(self, collection_name: str, points_selector: Any) -> None:
        """Soft-delete: mark records as invalid_at = NOW()."""
        pool = await self._get_pool()
        ids = _extract_point_ids(points_selector)
        if ids:
            placeholders = ", ".join(f"${i+1}" for i in range(len(ids)))
            async with pool.acquire() as conn:
                await conn.execute(
                    f"UPDATE memories SET invalid_at = NOW() WHERE id IN ({placeholders})",
                    *ids
                )

    async def close(self) -> None:
        """No-op: connection pool managed by db.py."""
        pass


# ─────────────────────────────────────────────────────────────────────────────
# WHERE clause builder
# Converts Qdrant Filter objects → PostgreSQL WHERE conditions + params
# ─────────────────────────────────────────────────────────────────────────────

def _build_where(query_filter) -> tuple[list[str], list]:
    """Build (where_clauses, params) from a Qdrant Filter object.

    When query_filter is None: default to excluding invalidated records.
    When filter is provided: must/must_not are processed explicitly.

    Supported patterns:
      - FieldCondition(key, match=MatchValue)   → col = value
      - FieldCondition(key, range=Range)        → col >= / <= value
      - FieldCondition(key='invalid', match=True) → invalid_at IS NULL/NOT NULL
      - HasIdCondition(has_id=[...])            → id IN (...)
      - Filter(should=[...]) inside must        → (cond1 OR cond2 ...)
    """
    if query_filter is None:
        return ["invalid_at IS NULL"], []

    clauses: list[str] = []
    params: list = []
    pidx = [1]  # mutable counter for $N placeholders

    def add_field_cond(cond, negate: bool) -> None:
        key = getattr(cond, 'key', None)
        if not key:
            return

        # 'invalid' maps to invalid_at timestamp (not a boolean column)
        if key == "invalid":
            match = getattr(cond, 'match', None)
            if match is not None and getattr(match, 'value', None) is True:
                clauses.append("invalid_at IS NULL" if negate else "invalid_at IS NOT NULL")
            return

        col = _payload_key_to_column(key)
        if not col:
            return

        match = getattr(cond, 'match', None)
        rng = getattr(cond, 'range', None)

        if match is not None:
            val = getattr(match, 'value', None)
            if val is None:
                return
            op = "!=" if negate else "="
            clauses.append(f"{col} {op} ${pidx[0]}")
            params.append(val)
            pidx[0] += 1

        elif rng is not None and not negate:
            for attr, sql_op in [("gte", ">="), ("gt", ">"), ("lte", "<="), ("lt", "<")]:
                val = getattr(rng, attr, None)
                if val is not None:
                    clauses.append(f"{col} {sql_op} ${pidx[0]}")
                    params.append(val)
                    pidx[0] += 1

    def add_has_id_cond(cond, negate: bool) -> None:
        ids = getattr(cond, 'has_id', None)
        if not ids:
            return
        id_list = list(ids)
        placeholders = ", ".join(f"${pidx[0] + i}" for i in range(len(id_list)))
        op = "NOT IN" if negate else "IN"
        clauses.append(f"id {op} ({placeholders})")
        params.extend(id_list)
        pidx[0] += len(id_list)

    def add_should_filter(filt, negate: bool) -> None:
        """Nested Filter(should=[...]) → (cond1 OR cond2 OR ...)"""
        should = getattr(filt, 'should', None) or []
        sub_parts: list[str] = []
        for sub in should:
            if not hasattr(sub, 'key'):
                continue
            col = _payload_key_to_column(sub.key)
            if not col:
                continue
            match = getattr(sub, 'match', None)
            if match is not None:
                val = getattr(match, 'value', None)
                if val is not None:
                    sub_parts.append(f"{col} = ${pidx[0]}")
                    params.append(val)
                    pidx[0] += 1
        if sub_parts:
            or_expr = " OR ".join(sub_parts)
            clauses.append(f"NOT ({or_expr})" if negate else f"({or_expr})")

    def process(cond, negate: bool) -> None:
        if hasattr(cond, 'key'):
            add_field_cond(cond, negate)
        elif hasattr(cond, 'has_id'):
            add_has_id_cond(cond, negate)
        elif hasattr(cond, 'should'):
            add_should_filter(cond, negate)

    for cond in (getattr(query_filter, 'must', None) or []):
        process(cond, negate=False)

    for cond in (getattr(query_filter, 'must_not', None) or []):
        process(cond, negate=True)

    return clauses, params


# ── Helpers ──────────────────────────────────────────────────────────────────

def _payload_key_to_column(key: str) -> str | None:
    """Map Qdrant payload key → PostgreSQL column name."""
    return {
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
        "utility": "importance",  # utility → importance in PG
        "pg_id": "id",
    }.get(key)


def _row_to_payload(row) -> dict:
    """Convert asyncpg Row → Qdrant-style payload dict."""
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
    if row.get("metadata"):
        try:
            meta = (json.loads(row["metadata"])
                    if isinstance(row["metadata"], str)
                    else row["metadata"])
            payload.update(meta)
        except (json.JSONDecodeError, TypeError):
            pass
    return payload


def _extract_point_ids(points_selector) -> list[int]:
    """Extract IDs from various Qdrant selector types."""
    if hasattr(points_selector, 'points'):
        return list(points_selector.points)
    if isinstance(points_selector, list):
        return points_selector
    return []
