#!/usr/bin/env python3
"""
seal_memory_client.py — Conector de memoria conversacional para JARVIS y ADA
Registra cada turno de conversación en:
  - PostgreSQL 5433 → tabla console_log (metadata + historial estructurado)
  - Qdrant 6333     → colección seal_conversations (búsqueda semántica)

Uso:
  from seal_memory_client import log_turn, search_conversations

  # Registrar un turno
  await log_turn(
      agent="JARVIS",
      channel="jarvis_vscode",
      message="William preguntó sobre Qdrant vs pgvector",
      message_type="user",
      session_id="session_20260401"
  )

  # Buscar conversaciones similares
  results = await search_conversations("Qdrant arquitectura SEAL", limit=5)
"""
import asyncio
import hashlib
import os
import time
from datetime import datetime, timezone
from typing import Optional

import asyncpg
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct
from sentence_transformers import SentenceTransformer

# ── Configuración ─────────────────────────────────────────────────────────────
PG_URL     = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
QDRANT_URL = "http://localhost:6333"
COLLECTION = "seal_conversations"
EMBED_MODEL = "intfloat/multilingual-e5-base"
VECTOR_DIM  = 768   # multilingual-e5-base — unificado con soul_memories

# Singleton del modelo de embeddings (carga lazy)
_embed_model: Optional[SentenceTransformer] = None


def _get_embed_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(EMBED_MODEL)
    return _embed_model


def _embed(text: str) -> list[float]:
    """Genera embedding con multilingual-e5-base (768 dims, unificado con SOUL)."""
    model = _get_embed_model()
    # e5 models use "passage: " prefix for indexing, "query: " for search
    vec = model.encode(f"passage: {text}", normalize_embeddings=True)
    return vec.tolist()


def _msg_id(agent: str, channel: str, ts: str) -> str:
    """ID determinístico para Qdrant basado en contenido."""
    raw = f"{agent}:{channel}:{ts}"
    return int(hashlib.md5(raw.encode()).hexdigest()[:16], 16)


# ── Función principal: log_turn ───────────────────────────────────────────────
async def log_turn(
    agent: str,
    channel: str,
    message: str,
    message_type: str = "assistant",
    session_id: Optional[str] = None,
) -> bool:
    """
    Registra un turno de conversación en PostgreSQL y Qdrant.

    Args:
        agent:        'JARVIS', 'ADA', o 'William'
        channel:      'jarvis_vscode', 'ada_terminal', 'web_chat', 'console'
        message:      texto del mensaje
        message_type: 'user', 'assistant', o 'system'
        session_id:   ID de sesión (auto-generado si None)

    Returns:
        True si se guardó correctamente, False si hubo error
    """
    if not message or not message.strip():
        return False

    ts = datetime.now(timezone.utc)
    if session_id is None:
        session_id = f"session_{ts.strftime('%Y%m%d')}_{agent.lower()}"

    ok_pg = await _log_postgres(agent, channel, message, message_type, session_id, ts)
    ok_qd = await _log_qdrant(agent, channel, message, message_type, session_id, ts)

    return ok_pg and ok_qd


async def _log_postgres(
    agent: str, channel: str, message: str, message_type: str,
    session_id: str, ts: datetime
) -> bool:
    """Inserta en console_log de PostgreSQL."""
    try:
        conn = await asyncpg.connect(PG_URL)
        # Obtener turn_number actual de la sesión
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM console_log WHERE session_id = $1", session_id
        )
        await conn.execute(
            """INSERT INTO console_log
               (session_id, turn_number, timestamp, from_agent, channel, message, message_type)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            session_id, int(count) + 1, ts, agent, channel,
            message[:4000], message_type
        )
        await conn.close()
        return True
    except Exception as e:
        print(f"[seal_memory] PG error: {e}")
        return False


async def _log_qdrant(
    agent: str, channel: str, message: str, message_type: str,
    session_id: str, ts: datetime
) -> bool:
    """Inserta vector en seal_conversations de Qdrant."""
    try:
        embedding = await asyncio.get_event_loop().run_in_executor(
            None, _embed, message[:1000]
        )
        point_id = _msg_id(agent, channel, ts.isoformat())
        client = AsyncQdrantClient(url=QDRANT_URL)
        await client.upsert(
            collection_name=COLLECTION,
            points=[PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "agent": agent,
                    "channel": channel,
                    "message": message[:2000],
                    "message_type": message_type,
                    "session_id": session_id,
                    "timestamp": ts.isoformat(),
                }
            )]
        )
        await client.close()
        return True
    except Exception as e:
        print(f"[seal_memory] Qdrant error: {e}")
        return False


# ── Búsqueda semántica ────────────────────────────────────────────────────────
async def search_conversations(
    query: str,
    limit: int = 5,
    agent_filter: Optional[str] = None,
    channel_filter: Optional[str] = None,
) -> list[dict]:
    """
    Busca conversaciones similares por semántica.

    Args:
        query:          texto de búsqueda
        limit:          número de resultados
        agent_filter:   filtrar por agente ('JARVIS', 'ADA', 'William')
        channel_filter: filtrar por canal

    Returns:
        Lista de dicts con message, agent, channel, timestamp, score
    """
    try:
        model = _get_embed_model()
        vec = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: model.encode(f"query: {query}", normalize_embeddings=True).tolist()
        )

        from qdrant_client.models import Filter, FieldCondition, MatchValue
        query_filter = None
        conditions = []
        if agent_filter:
            conditions.append(FieldCondition(key="agent", match=MatchValue(value=agent_filter)))
        if channel_filter:
            conditions.append(FieldCondition(key="channel", match=MatchValue(value=channel_filter)))
        if conditions:
            query_filter = Filter(must=conditions)

        client = AsyncQdrantClient(url=QDRANT_URL)
        result = await client.query_points(
            collection_name=COLLECTION,
            query=vec,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )
        results = result.points
        await client.close()

        return [
            {
                "message":   r.payload.get("message", ""),
                "agent":     r.payload.get("agent", ""),
                "channel":   r.payload.get("channel", ""),
                "timestamp": r.payload.get("timestamp", ""),
                "session_id": r.payload.get("session_id", ""),
                "score":     round(r.score, 4),
            }
            for r in results
        ]
    except Exception as e:
        print(f"[seal_memory] Search error: {e}")
        return []


# ── Test rápido ───────────────────────────────────────────────────────────────
async def _test():
    print("[seal_memory] Test de conexión...")
    ok = await log_turn(
        agent="JARVIS",
        channel="jarvis_vscode",
        message="seal_memory_client inicializado y operativo. Test de conexión exitoso.",
        message_type="system",
        session_id="test_init"
    )
    print(f"[seal_memory] log_turn: {'OK' if ok else 'FAIL'}")

    results = await search_conversations("inicialización sistema SEAL", limit=3)
    print(f"[seal_memory] search: {len(results)} resultados")
    for r in results:
        print(f"  [{r['score']}] {r['agent']} — {r['message'][:80]}")


if __name__ == "__main__":
    asyncio.run(_test())
