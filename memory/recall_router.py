"""SOUL Recall Router v1 — unified retrieval across all SOUL sources.

Sources: memories (BM25), chat_messages (FTS), distilled_exchanges (FTS),
         session_memory, rules/opinions.

Integrates with active_recall() via SOUL_RECALL_ROUTER_ENABLED env flag (default: false).

Fixes applied (NEXUS audit 2026-05-17):
  BUG1: _safe() now uses per-label timeouts (TIMEOUT_* constants are no longer dead code).
  BUG2: _recall_session_memory() queries soul_v3.session_memory; distilled_exchanges as fallback.
  BUG3: All FTS uses websearch_to_tsquery('simple') — uniform, accepts free text.
  BUG4: soul_v3.recall_audit table + INSERT on every router call.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from embeddings import get_embedding

try:
    from soul_cognitive_graph import shadow_rank_memories
except Exception:  # pragma: no cover - shadow is optional and must not block recall
    shadow_rank_memories = None  # type: ignore[assignment]

# ── Feature flag ──────────────────────────────────────────────────────────────
ROUTER_ENABLED = os.environ.get("SOUL_RECALL_ROUTER_ENABLED", "false").lower() == "true"
COGNITIVE_GRAPH_SHADOW_ENABLED = os.environ.get("SOUL_COGNITIVE_GRAPH_SHADOW", "false").lower() == "true"
COGNITIVE_GRAPH_MODE = os.environ.get(
    "SOUL_COGNITIVE_GRAPH_MODE",
    "shadow" if COGNITIVE_GRAPH_SHADOW_ENABLED else "off",
).lower()
COGNITIVE_GRAPH_SHADOW_LOG_QUERY = os.environ.get("SOUL_COGNITIVE_GRAPH_SHADOW_LOG_QUERY", "false").lower() == "true"
COGNITIVE_GRAPH_SHADOW_LOG = Path(__file__).parent / "diagnostic" / "soul_cognitive_graph_shadow.jsonl"
COGNITIVE_GRAPH_QUERY_TOKEN_RE = re.compile(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+")
COGNITIVE_GRAPH_ASSIST_MIN_QUERY_TOKENS = int(os.environ.get("SOUL_COGNITIVE_GRAPH_ASSIST_MIN_QUERY_TOKENS", "3"))
COGNITIVE_GRAPH_ASSIST_MAX_HITS = int(os.environ.get("SOUL_COGNITIVE_GRAPH_ASSIST_MAX_HITS", "5"))
COGNITIVE_GRAPH_ASSIST_PRESERVE_TOP1 = (
    os.environ.get("SOUL_COGNITIVE_GRAPH_ASSIST_PRESERVE_TOP1", "true").lower() == "true"
)
COGNITIVE_GRAPH_CRITICAL_RE = re.compile(
    r"dm:ada:william|web_chat|chat general|silencio|silent|privacidad|william|henry|codex app windows",
    re.IGNORECASE,
)

# ── Timeouts per source (seconds) — BUG1 fix: actually used in _safe() ────────
TIMEOUT_QDRANT   = 0.35
TIMEOUT_PG_FTS   = 0.25
TIMEOUT_DISTILLED = 5.00
TIMEOUT_SESSION  = 0.20
TIMEOUT_RULES    = 0.20

_LABEL_TIMEOUT = {
    "memories":  TIMEOUT_QDRANT,
    "chat":      TIMEOUT_PG_FTS,
    "distilled": TIMEOUT_DISTILLED,
    "session":   TIMEOUT_SESSION,
    "rules":     TIMEOUT_RULES,
}

# ── Token budget per mode (chars ≈ tokens×4) ──────────────────────────────────
BUDGET = {
    "micro":    3200,
    "standard": 9000,
    "deep":    20000,
    "boot":    10000,
}

# ── Frente 3: max hits returned per mode (quality > quantity) ────────────────
# Caps the number of entries in _format_context regardless of budget headroom.
# Goal: 3-5 highly relevant hits instead of 10 lukewarm ones.
MAX_HITS = {
    "micro":     3,
    "standard":  5,
    "deep":     10,
    "boot":      7,
}

# ── Authority scores ──────────────────────────────────────────────────────────
AUTHORITY = {
    "william": 1.00,
    "henry":   0.90,
    "NEXUS":   0.75,
    "JARVIS":  0.70,
    "ALICE":   0.70,
    "ADA":     0.70,
    "DUM":     0.55,
    "system":  0.80,
}

# ── Half-life days by category ────────────────────────────────────────────────
HALF_LIFE = {
    "correction": 3650,
    "rule":       3650,
    "decision":   730,
    "milestone":  365,
    "fact":       180,
    "insight":    180,
    "chat":        45,
    "session":      7,
    "default":    180,
}


class RecallHit(TypedDict):
    source: str
    id: str
    agent: str | None
    channel: str | None
    created_at: str | None
    category: str | None
    content: str
    summary: str | None
    score_semantic: float
    score_keyword: float
    score_recency: float
    score_importance: float
    score_authority: float
    score_final: float


@dataclass(frozen=True)
class _ShadowMemoryRow:
    id: int
    category: str
    layer: str
    importance: int
    content: str
    created_at: object | None


# ── Intent classifier ─────────────────────────────────────────────────────────

_OLD_CONV_KW = [
    "ayer", "antes", "antigu", "recuerdas", "hablamos", "dijiste", "dijimos",
    "yesterday", "remember", "we said", "you said", "earlier", "last time",
    "el 13", "el 14", "el 15", "el 16", "el 17", "mayo", "abril",
]
_RULE_KW = [
    "regla", "nunca", "siempre", "orden", "correccion", "corregiste",
    "rule", "never", "always", "correction", "policy", "protocol",
]
_DECISION_KW = [
    "decidimos", "decision", "acuerdo", "autorizo", "aprobamos", "acordamos",
    "decided", "agreed", "approved", "authorized",
]
_RECENT_KW = [
    "continua", "pendiente", "estado", "en que estabamos", "que teniamos",
    "continue", "pending", "status", "what were we", "resume",
]
_AUDIT_KW = [
    "evidencia", "quien dijo", "cuando dijo", "mensaje exacto", "prueba",
    "evidence", "who said", "exact message", "prove", "trace",
]
_TECH_KW = [
    "donde esta", "donde se", "por que fallo", "como funciona", "error",
    "where is", "why did", "how does", "exception", "bug",
]


def classify_recall_intent(query: str) -> set[str]:
    q = query.lower()
    intents: set[str] = set()

    if any(w in q for w in _OLD_CONV_KW):
        intents.add("old_conversation")
    if any(w in q for w in _RULE_KW):
        intents.add("rule_policy")
    if any(w in q for w in _DECISION_KW):
        intents.add("decision")
    if any(w in q for w in _RECENT_KW):
        intents.add("recent_context")
    if any(w in q for w in _AUDIT_KW):
        intents.add("audit_trace")
    if any(w in q for w in _TECH_KW):
        intents.add("technical_fact")

    if not intents:
        intents.add("technical_fact")

    return intents


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _recency_score(created_at_str: str | None, category: str = "default") -> float:
    if not created_at_str:
        return 0.5
    try:
        from datetime import datetime, timezone
        if hasattr(created_at_str, "isoformat"):
            dt = created_at_str
        else:
            dt = datetime.fromisoformat(str(created_at_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        half_life = HALF_LIFE.get(category, HALF_LIFE["default"])
        return 1.0 / (1.0 + days_old / half_life)
    except Exception:
        return 0.5


def _authority_score(agent: str | None, sender: str | None = None) -> float:
    name = (sender or agent or "").lower()
    for key, score in AUTHORITY.items():
        if key.lower() == name:
            return score
    return 0.60


def _intent_boost(source: str, intents: set[str]) -> float:
    boosts = {
        "audit_trace":      {"chat_messages": 0.25},
        "old_conversation": {"distilled_exchanges": 0.20, "chat_messages": 0.15},
        "rule_policy":      {"rules": 0.30, "memories": 0.25},
        "recent_context":   {"session_memory": 0.30, "chat_messages": 0.20},
        "decision":         {"memories": 0.25, "distilled_exchanges": 0.15},
        "technical_fact":   {"memories": 0.20},
    }
    total = 0.0
    for intent in intents:
        total += boosts.get(intent, {}).get(source, 0.0)
    return min(total, 0.30)


def _final_score(
    score_semantic: float,
    score_keyword: float,
    score_importance: float,
    score_recency: float,
    score_authority: float,
    intent_boost: float,
) -> float:
    return (
        0.30 * score_semantic
        + 0.25 * score_keyword
        + 0.15 * score_importance
        + 0.10 * score_recency
        + 0.10 * score_authority
        + 0.10 * intent_boost
    )


def _dedup(hits: list[RecallHit]) -> list[RecallHit]:
    seen: set[str] = set()
    result = []
    for h in hits:
        key = h["content"][:120].strip().lower()
        if key not in seen:
            seen.add(key)
            result.append(h)
    return result


def _rank(hits: list[RecallHit], intents: set[str]) -> list[RecallHit]:
    for h in hits:
        boost = _intent_boost(h["source"], intents)
        h["score_final"] = _final_score(
            h["score_semantic"],
            h["score_keyword"],
            h["score_importance"],
            h["score_recency"],
            h["score_authority"],
            boost,
        )
    return sorted(hits, key=lambda x: x["score_final"], reverse=True)


def _shadow_layer_for_hit(hit: RecallHit) -> str:
    category = (hit.get("category") or "").lower()
    if category in {"emotion", "trust"}:
        return "emotional"
    return "operational"


def _shadow_created_at(value: str | None) -> object | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _shadow_rows_from_hits(hits: list[RecallHit]) -> list[_ShadowMemoryRow]:
    rows: list[_ShadowMemoryRow] = []
    for hit in hits:
        if hit.get("source") != "memories":
            continue
        try:
            memory_id = int(str(hit["id"]))
        except (TypeError, ValueError):
            continue
        rows.append(
            _ShadowMemoryRow(
                id=memory_id,
                category=hit.get("category") or "",
                layer=_shadow_layer_for_hit(hit),
                importance=max(0, min(10, int(round((hit.get("score_importance") or 0.0) * 10)))),
                content=hit.get("content") or "",
                created_at=_shadow_created_at(hit.get("created_at")),
            )
        )
    return rows


def _run_cognitive_graph_shadow(agent: str, query: str, ranked: list[RecallHit], *, limit: int = 5) -> dict | None:
    """Run cognitive graph ranking in shadow mode without changing router output.

    This function uses only already-fetched memory hits. It does not query MCP,
    does not call active_recall/memory_hybrid_search, and does not write to DB.
    """
    if not (COGNITIVE_GRAPH_SHADOW_ENABLED or COGNITIVE_GRAPH_MODE in {"shadow", "assist"}):
        return None
    if shadow_rank_memories is None:
        return None
    t0 = time.monotonic()
    try:
        rows = _shadow_rows_from_hits(ranked)
        if not rows:
            return None
        shadow = shadow_rank_memories(query, rows, k=limit)
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 3)
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "agent": agent,
            "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
            "query_token_count": len(COGNITIVE_GRAPH_QUERY_TOKEN_RE.findall(query)),
            "rank_ms": elapsed_ms,
            "base_top_ids": [hit["id"] for hit in ranked[:limit]],
            "shadow_top_ids": [str(item.id) for item in shadow],
            "candidate_count": len(rows),
        }
        if COGNITIVE_GRAPH_SHADOW_LOG_QUERY:
            payload["query_preview"] = query[:160]
        try:
            COGNITIVE_GRAPH_SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
            with COGNITIVE_GRAPH_SHADOW_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return payload
    except Exception:
        return None


def _is_cognitive_graph_assist_enabled() -> bool:
    return COGNITIVE_GRAPH_MODE == "assist"


def _query_token_count(query: str) -> int:
    return len(COGNITIVE_GRAPH_QUERY_TOKEN_RE.findall(query))


def _is_critical_memory_hit(hit: RecallHit) -> bool:
    if hit.get("source") != "memories":
        return False
    category = (hit.get("category") or "").lower()
    if category not in {"rule", "correction", "operational_anchor", "decision"}:
        return False
    content = hit.get("content") or ""
    return bool(COGNITIVE_GRAPH_CRITICAL_RE.search(content))


def _assist_keeps_critical_top_memory(base_memory_hits: list[RecallHit], shadow_ids: list[str]) -> bool:
    if not base_memory_hits or not shadow_ids:
        return True
    critical = [hit for hit in base_memory_hits if _is_critical_memory_hit(hit)]
    if not critical:
        return True
    first_critical_id = str(critical[0]["id"])
    first_shadow_id = shadow_ids[0]
    first_base_memory_id = str(base_memory_hits[0]["id"])
    if first_base_memory_id == first_critical_id and first_shadow_id != first_critical_id:
        return False
    return True


def _apply_cognitive_graph_assist(query: str, ranked: list[RecallHit], *, limit: int = 5) -> list[RecallHit]:
    """Optionally re-order memory hits inside the existing top-k.

    Guardrails:
    - disabled unless SOUL_COGNITIVE_GRAPH_MODE=assist;
    - uses only already-ranked memory candidates;
    - does not move rules/chat/session/distilled hits;
    - ignores short queries;
    - preserves base top-1 by default;
    - never demotes a critical top memory for channel/privacy/William/Codex.
    """
    if not _is_cognitive_graph_assist_enabled() or shadow_rank_memories is None:
        return ranked
    if _query_token_count(query) < COGNITIVE_GRAPH_ASSIST_MIN_QUERY_TOKENS:
        return ranked

    top_limit = max(1, min(limit, COGNITIVE_GRAPH_ASSIST_MAX_HITS, len(ranked)))
    fixed_prefix = ranked[:1] if COGNITIVE_GRAPH_ASSIST_PRESERVE_TOP1 and ranked else []
    assist_start = len(fixed_prefix)
    head = ranked[assist_start:top_limit]
    tail = ranked[top_limit:]
    rows = _shadow_rows_from_hits(head)
    if len(rows) < 2:
        return ranked
    try:
        shadow = shadow_rank_memories(query, rows, k=len(rows))
    except Exception:
        return ranked

    memory_by_id = {str(hit["id"]): hit for hit in head if hit.get("source") == "memories"}
    base_memory_hits = [hit for hit in head if hit.get("source") == "memories"]
    shadow_ids = [str(item.id) for item in shadow if str(item.id) in memory_by_id]
    if not shadow_ids or not _assist_keeps_critical_top_memory(base_memory_hits, shadow_ids):
        return ranked

    reordered_memory_hits = [memory_by_id[memory_id] for memory_id in shadow_ids]
    seen = set(shadow_ids)
    reordered_memory_hits.extend(hit for hit in base_memory_hits if str(hit["id"]) not in seen)

    memory_iter = iter(reordered_memory_hits)
    assisted_head: list[RecallHit] = []
    for hit in head:
        if hit.get("source") == "memories":
            assisted_head.append(next(memory_iter))
        else:
            assisted_head.append(hit)
    return fixed_prefix + assisted_head + tail


# ── recall_audit table (BUG4) ─────────────────────────────────────────────────

_recall_audit_ready = False

_CREATE_RECALL_AUDIT = """
CREATE TABLE IF NOT EXISTS soul_v3.recall_audit (
    id          BIGSERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    query       TEXT NOT NULL,
    mode        TEXT NOT NULL,
    intents     TEXT[],
    hits_total  INTEGER,
    hits_returned INTEGER,
    elapsed_ms  INTEGER,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS recall_audit_agent_ts
    ON soul_v3.recall_audit(agent, created_at DESC);
"""


async def _ensure_recall_audit(pool) -> None:
    global _recall_audit_ready
    if _recall_audit_ready:
        return
    try:
        async with pool.acquire() as conn:
            for stmt in _CREATE_RECALL_AUDIT.strip().split(";"):
                s = stmt.strip()
                if s:
                    await conn.execute(s)
        _recall_audit_ready = True
    except Exception:
        pass


async def _write_recall_audit(
    pool,
    agent: str,
    query: str,
    mode: str,
    intents: set[str],
    hits_total: int,
    hits_returned: int,
    elapsed_ms: int,
) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO soul_v3.recall_audit
                    (agent, query, mode, intents, hits_total, hits_returned, elapsed_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                agent,
                query[:500],
                mode,
                list(sorted(intents)),
                hits_total,
                hits_returned,
                elapsed_ms,
            )
    except Exception:
        pass


# ── Per-source retrieval ──────────────────────────────────────────────────────

async def _recall_memories(
    query: str, agent: str, pool, intents: set[str], limit: int = 8
) -> list[RecallHit]:
    """BM25 search on memories using embedding_bm25 tsvector + websearch_to_tsquery (BUG3 fix)."""
    try:
        rows = await pool.fetch(
            """
            SELECT id, agent, category, content, importance, created_at, scope,
                   ts_rank(embedding_bm25,
                            websearch_to_tsquery('simple', $1)) AS kw_rank
            FROM memories
            WHERE (agent = $2 OR scope IN ('shared','team'))
              AND invalid_at IS NULL
              AND embedding_bm25 @@ websearch_to_tsquery('simple', $1)
            ORDER BY importance DESC, kw_rank DESC
            LIMIT $3
            """,
            query, agent, limit,
        )
        hits = []
        for r in rows:
            imp = (r["importance"] or 5) / 10.0
            hits.append(RecallHit(
                source="memories",
                id=str(r["id"]),
                agent=r["agent"],
                channel=None,
                created_at=str(r["created_at"]),
                category=r["category"],
                content=r["content"] or "",
                summary=None,
                score_semantic=0.0,
                score_keyword=float(r["kw_rank"] or 0.5),
                score_recency=_recency_score(r["created_at"], r["category"] or "default"),
                score_importance=imp,
                score_authority=_authority_score(r["agent"]),
                score_final=0.0,
            ))
        return hits
    except Exception:
        return []


async def _recall_chat_messages(
    query: str, agent: str, pool, intents: set[str], limit: int = 6
) -> list[RecallHit]:
    """FTS on web_chat using websearch_to_tsquery (BUG3 fix). Privacy: only web_chat in v1."""
    try:
        rows = await pool.fetch(
            """
            SELECT id, sender_name, content, created_at,
                   ts_rank(to_tsvector('simple', coalesce(content,'')),
                            websearch_to_tsquery('simple', $1)) AS kw_rank
            FROM soul_v3.chat_messages
            WHERE channel = 'web_chat'
              AND to_tsvector('simple', coalesce(content,''))
                  @@ websearch_to_tsquery('simple', $1)
            ORDER BY kw_rank DESC, created_at DESC
            LIMIT $2
            """,
            query, limit,
        )
        hits = []
        for r in rows:
            sender = r["sender_name"] or ""
            hits.append(RecallHit(
                source="chat_messages",
                id=str(r["id"]),
                agent=None,
                channel="web_chat",
                created_at=str(r["created_at"]),
                category="chat",
                content=r["content"] or "",
                summary=None,
                score_semantic=0.0,
                score_keyword=float(r["kw_rank"] or 0.3),
                score_recency=_recency_score(r["created_at"], "chat"),
                score_importance=0.6 if sender.lower() in ("william", "henry") else 0.4,
                score_authority=_authority_score(None, sender),
                score_final=0.0,
            ))
        return hits
    except Exception:
        return []


async def _recall_distilled_exchanges(
    query: str, agent: str, pool, intents: set[str], limit: int = 5
) -> list[RecallHit]:
    """Recall distilled_exchanges with FTS plus pgvector when available."""
    hits: list[RecallHit] = []
    try:
        rows = await pool.fetch(
            """
            SELECT id, agent, summary, exchange_core, specific_context, created_at,
                   ts_rank(
                     to_tsvector('simple',
                       coalesce(summary,'') || ' ' ||
                       coalesce(exchange_core,'') || ' ' ||
                       coalesce(specific_context,'')),
                     websearch_to_tsquery('simple', $1)
                   ) AS kw_rank
            FROM soul_v3.distilled_exchanges
            WHERE to_tsvector('simple',
                    coalesce(summary,'') || ' ' ||
                    coalesce(exchange_core,'') || ' ' ||
                    coalesce(specific_context,''))
                  @@ websearch_to_tsquery('simple', $1)
            ORDER BY kw_rank DESC, created_at DESC
            LIMIT $2
            """,
            query, limit,
        )
        for r in rows:
            content = r["exchange_core"] or r["summary"] or ""
            hits.append(RecallHit(
                source="distilled_exchanges",
                id=str(r["id"]),
                agent=r["agent"],
                channel=None,
                created_at=str(r["created_at"]),
                category="milestone",
                content=content[:500],
                summary=r["summary"],
                score_semantic=0.0,
                score_keyword=float(r["kw_rank"] or 0.3),
                score_recency=_recency_score(r["created_at"], "milestone"),
                score_importance=0.6,
                score_authority=_authority_score(r["agent"]),
                score_final=0.0,
            ))
    except Exception:
        pass

    try:
        emb = json.dumps(await get_embedding(query))
        rows_vec = await pool.fetch(
            """
            SELECT id, agent, summary, exchange_core, specific_context, created_at,
                   1 - (embedding <=> $1::vector) AS sim
            FROM soul_v3.distilled_exchanges
            WHERE embedding IS NOT NULL
              AND ($2::text IS NULL OR agent = $2)
            ORDER BY embedding <=> $1::vector
            LIMIT $3
            """,
            emb, agent, limit,
        )
        for r in rows_vec:
            content = r["exchange_core"] or r["summary"] or r["specific_context"] or ""
            hits.append(RecallHit(
                source="distilled_exchanges",
                id=str(r["id"]),
                agent=r["agent"],
                channel=None,
                created_at=str(r["created_at"]),
                category="milestone",
                content=content[:500],
                summary=r["summary"],
                score_semantic=float(r["sim"] or 0.0),
                score_keyword=0.0,
                score_recency=_recency_score(r["created_at"], "milestone"),
                score_importance=0.6,
                score_authority=_authority_score(r["agent"]),
                score_final=0.0,
            ))
    except Exception:
        pass

    return _dedup(hits)[:limit]


async def _recall_session_memory(
    agent: str, pool, intents: set[str]
) -> list[RecallHit]:
    """Fetch latest session from soul_v3.session_memory; fallback distilled_exchanges (BUG2 fix)."""
    if "recent_context" not in intents and "old_conversation" not in intents:
        return []
    try:
        # Primary: real session_memory table
        row = await pool.fetchrow(
            """
            SELECT id, agent, summary, key_decisions, created_at
            FROM soul_v3.session_memory
            WHERE agent = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            agent,
        )
        if row:
            content = row["summary"] or ""
            return [RecallHit(
                source="session_memory",
                id=str(row["id"]),
                agent=agent,
                channel=None,
                created_at=str(row["created_at"]),
                category="session",
                content=content[:600],
                summary=content[:200],
                score_semantic=0.0,
                score_keyword=0.5,
                score_recency=_recency_score(row["created_at"], "session"),
                score_importance=0.7,
                score_authority=_authority_score(agent),
                score_final=0.0,
            )]

        # Fallback: distilled_exchanges as proxy if no session_memory row
        row_fb = await pool.fetchrow(
            """
            SELECT id, agent, summary, created_at
            FROM soul_v3.distilled_exchanges
            WHERE agent = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            agent,
        )
        if not row_fb:
            return []
        content = row_fb["summary"] or ""
        return [RecallHit(
            source="session_memory",
            id=f"de:{row_fb['id']}",
            agent=agent,
            channel=None,
            created_at=str(row_fb["created_at"]),
            category="session",
            content=content[:600],
            summary=content[:200],
            score_semantic=0.0,
            score_keyword=0.4,
            score_recency=_recency_score(row_fb["created_at"], "session"),
            score_importance=0.6,
            score_authority=_authority_score(agent),
            score_final=0.0,
        )]
    except Exception:
        return []


async def _recall_rules_opinions(
    query: str, agent: str, pool, intents: set[str], limit: int = 5
) -> list[RecallHit]:
    """Load critical rules (priority>=8)."""
    hits = []
    try:
        rows = await pool.fetch(
            """
            SELECT rule_key, content, priority, created_at
            FROM rules
            WHERE active = true AND priority >= 8
            ORDER BY priority DESC
            LIMIT $1
            """,
            limit,
        )
        for r in rows:
            hits.append(RecallHit(
                source="rules",
                id=r["rule_key"],
                agent="system",
                channel=None,
                created_at=str(r["created_at"]) if r["created_at"] else None,
                category="rule",
                content=r["content"] or "",
                summary=None,
                score_semantic=0.0,
                score_keyword=0.8,
                score_recency=_recency_score(r["created_at"], "rule"),
                score_importance=(r["priority"] or 8) / 10.0,
                score_authority=1.0,
                score_final=0.0,
            ))
    except Exception:
        pass
    return hits


# ── Main router ───────────────────────────────────────────────────────────────

async def soul_recall_router(
    agent: str,
    query: str,
    pool,
    mode: str = "standard",
    include_chat: bool = True,
    include_distilled: bool = True,
    include_session: bool = True,
    include_rules: bool = True,
    limit: int = 20,
) -> str:
    """Query all SOUL sources and return ranked, formatted context block.

    Returns empty string if disabled (SOUL_RECALL_ROUTER_ENABLED != 'true').
    Never raises — degrades gracefully on any source failure.
    Writes one row to soul_v3.recall_audit on each invocation (BUG4 fix).
    """
    if not ROUTER_ENABLED:
        return ""

    # Ensure audit table exists (idempotent, cached after first call)
    await _ensure_recall_audit(pool)

    intents = classify_recall_intent(query)
    t0 = time.monotonic()

    # BUG1 fix: _safe uses per-label timeout from _LABEL_TIMEOUT
    async def _safe(coro, label: str) -> list:
        timeout = _LABEL_TIMEOUT.get(label, TIMEOUT_PG_FTS)
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception:
            return []

    tasks = [
        _safe(_recall_memories(query, agent, pool, intents, max(1, limit // 2)), "memories"),
    ]
    if include_chat:
        tasks.append(_safe(_recall_chat_messages(query, agent, pool, intents, max(1, limit // 3)), "chat"))
    if include_distilled:
        tasks.append(_safe(_recall_distilled_exchanges(query, agent, pool, intents, max(1, limit // 4)), "distilled"))
    if include_session:
        tasks.append(_safe(_recall_session_memory(agent, pool, intents), "session"))
    if include_rules:
        tasks.append(_safe(_recall_rules_opinions(query, agent, pool, intents), "rules"))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_hits: list[RecallHit] = []
    for r in results:
        if isinstance(r, list):
            all_hits.extend(r)

    hits_total = len(all_hits)
    deduped = _dedup(all_hits)
    ranked = _rank(deduped, intents)
    output_limit = MAX_HITS.get(mode, MAX_HITS["standard"])
    _run_cognitive_graph_shadow(agent, query, ranked, limit=output_limit)
    ranked = _apply_cognitive_graph_assist(query, ranked, limit=output_limit)

    budget = BUDGET.get(mode, BUDGET["standard"])
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    output = _format_context(ranked, budget, intents, elapsed_ms, mode)
    hits_returned = output.count("\n- [") if output else 0

    # BUG4 fix: write audit row (fire-and-forget)
    asyncio.create_task(_write_recall_audit(
        pool, agent, query, mode, intents, hits_total, hits_returned, elapsed_ms
    ))

    return output


def _format_context(
    hits: list[RecallHit],
    budget: int,
    intents: set[str],
    elapsed_ms: int,
    mode: str,
) -> str:
    if not hits:
        return ""

    sections: dict[str, list[str]] = {
        "rules": [],
        "memories": [],
        "distilled_exchanges": [],
        "chat_messages": [],
        "session_memory": [],
    }

    chars_used = 0
    hits_count = 0
    max_hits = MAX_HITS.get(mode, MAX_HITS["standard"])
    for h in hits:
        if hits_count >= max_hits:
            break
        src = h["source"]
        if src not in sections:
            continue
        snippet = h["content"][:300].replace("\n", " ")
        agent_tag = f"{h['agent']}, " if h.get("agent") else ""
        cat_tag = f"{h['category']}, " if h.get("category") else ""
        score_tag = f"score={h['score_final']:.2f}"
        line = f"- [{src} #{h['id']}, {agent_tag}{cat_tag}{score_tag}] {snippet}"
        if chars_used + len(line) > budget:
            break
        sections[src].append(line)
        chars_used += len(line)
        hits_count += 1

    parts = []
    if sections["rules"]:
        parts.append("## Active Rules\n" + "\n".join(sections["rules"]))
    if sections["memories"]:
        parts.append("## Relevant Memories\n" + "\n".join(sections["memories"]))
    if sections["distilled_exchanges"]:
        parts.append("## Past Conversations\n" + "\n".join(sections["distilled_exchanges"]))
    if sections["chat_messages"]:
        parts.append("## Webchat History\n" + "\n".join(sections["chat_messages"]))
    if sections["session_memory"]:
        parts.append("## Session Continuity\n" + "\n".join(sections["session_memory"]))

    if not parts:
        return ""

    intents_str = "+".join(sorted(intents))
    header = f"[recall_router: mode={mode}, intents={intents_str}, {elapsed_ms}ms]"
    return header + "\n" + "\n\n".join(parts)
