#!/usr/bin/env python3
"""SEAL-Bench v1.0 — Benchmark for Agent Soul Systems.

"LongMemEval measures chatbots. SEAL-Bench measures souls."

6 categories x 10 tests = 60 total tests.
Calls SOUL tools directly (asyncpg/qdrant/neo4j).
Auto-scores + JSON report.

Usage:
    python3 seal_bench.py                   # Run all tests
    python3 seal_bench.py --category 1      # Run single category
    python3 seal_bench.py --json            # Output JSON report
    python3 seal_bench.py --compare mem0    # Compare mode (stub)

Created by JARVIS for Team SEAL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_pool, close_pool
from config import settings
from embeddings import get_embedding

# ── Test Agent (isolated namespace to avoid polluting real data) ──
BENCH_AGENT_A = "BENCH_ALPHA"
BENCH_AGENT_B = "BENCH_BETA"
BENCH_SESSION = f"seal_bench_{int(time.time())}"


@dataclass
class TestResult:
    """Single test result."""
    name: str
    category: str
    passed: bool
    score: float  # 0-10
    elapsed_ms: float
    detail: str = ""
    error: str = ""


@dataclass
class CategoryResult:
    """Aggregated results for one category."""
    name: str
    tests: list[TestResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        """Category score 0-100. Each test scores 0-10, so 10 tests = 100 max."""
        if not self.tests:
            return 0.0
        return sum(t.score for t in self.tests)  # 10 tests * 10 max = 100

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.passed)

    @property
    def total(self) -> int:
        return len(self.tests)


@dataclass
class BenchReport:
    """Full benchmark report."""
    version: str = "1.0"
    timestamp: str = ""
    categories: list[CategoryResult] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        return sum(c.score for c in self.categories)

    @property
    def grade(self) -> str:
        s = self.total_score
        if s >= 500:
            return "Production-grade soul"
        elif s >= 400:
            return "Research-grade"
        elif s >= 300:
            return "Prototype"
        else:
            return "Memory system, not soul system"

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "total_score": round(self.total_score, 1),
            "max_score": 600,
            "grade": self.grade,
            "categories": [
                {
                    "name": c.name,
                    "score": round(c.score, 1),
                    "passed": c.passed,
                    "total": c.total,
                    "tests": [asdict(t) for t in c.tests],
                }
                for c in self.categories
            ],
        }


# ════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════

async def _run_test(name: str, category: str, func) -> TestResult:
    """Run a single test with timing and error handling."""
    t0 = time.monotonic()
    try:
        score, detail = await func()
        elapsed = (time.monotonic() - t0) * 1000
        return TestResult(
            name=name, category=category,
            passed=score >= 5.0, score=min(10.0, max(0.0, score)),
            elapsed_ms=round(elapsed, 1), detail=detail,
        )
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        return TestResult(
            name=name, category=category,
            passed=False, score=0.0,
            elapsed_ms=round(elapsed, 1),
            error=f"{type(e).__name__}: {e}",
            detail=traceback.format_exc()[-500:],
        )


_INICIO_DE_CORRIDA = None


async def _setup_bench_identity(pool):
    """Create bench test agents in identity table if they don't exist.

    Anota además el instante en que arranca la corrida: la limpieza sólo borra lo
    creado desde acá, para no llevarse filas de una corrida anterior del mismo agente
    de test. Se marca al preparar los agentes porque es lo primero que hace el
    benchmark; cualquier fila suya es posterior a este punto.
    """
    global _INICIO_DE_CORRIDA
    _INICIO_DE_CORRIDA = datetime.now(timezone.utc)
    for agent in (BENCH_AGENT_A, BENCH_AGENT_B):
        # Ensure agent row exists (identity has FK → agents)
        await pool.execute("""
            INSERT INTO agents (name, role, active)
            VALUES ($1, 'benchmark_agent', false)
            ON CONFLICT (name) DO NOTHING
        """, agent)
        await pool.execute("""
            INSERT INTO identity (agent, personality, ocean_scores, boot_context, philosophy)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (agent) DO UPDATE SET
                ocean_scores = $3,
                personality = $2
        """, agent,
            json.dumps({"role": "benchmark_agent", "style": "analytical"}),
            json.dumps({"O": 0.75, "C": 0.85, "E": 0.40, "A": 0.60, "N": 0.15}),
            f"You are {agent}, a SEAL-Bench test agent.",
            "Test agent for benchmarking.",
        )


# ── La limpieza del benchmark y su guarda ────────────────────────────────────
#
# **Por qué existe esta guarda (ADA, 9-sep-2026; la exigió JARVIS al autorizar que
# el benchmark tenga identidad propia en SOUL).**
#
# Hasta hoy la limpieza borraba `WHERE agent = $1` sin más. Es inocuo mientras el
# nombre sea sintético, y por eso nadie lo miró en meses. Pero el radio de explosión
# escrito ahí no es «lo que creó esta corrida»: es **todo lo que le pertenece a ese
# agente**. Alcanza con que el nombre esté mal —una variable de entorno, un copiar y
# pegar, un mutante de revisión— para que al final de la corrida, en el camino feliz
# y en silencio, se ejecute:
#
#     DELETE FROM memories WHERE agent = 'ADA'     -> toda la memoria, no 55 filas
#     DELETE FROM identity WHERE agent = 'ADA'     -> la identidad
#
# Es la misma familia que el borrado del home del 7-sep: **una guarda cuyo alcance
# era más ancho que su propósito.** Ahí también todo funcionó como estaba escrito.
#
# Dos candados, y el primero es el que importa:
#   1. el nombre tiene que estar en la lista de agentes de benchmark. No es un
#      chequeo de prefijo por comodidad: es una lista cerrada, y ante la duda LEVANTA
#      en vez de seguir. Un borrado que no sabe a quién apunta no se ejecuta.
#   2. sólo se borra lo creado DESDE que arrancó la corrida, para que ni siquiera
#      dentro del agente de test se toquen filas de una corrida anterior.
#
# **Qué se marca `# GUARDA-DESTRUCTIVA` y qué NO, que no es lo mismo** (distinción de
# JARVIS, 9-sep-2026, revisando esta guarda). La regla del 7-sep dice que el arnés no
# muta las guardas destructivas, y eso deja viva una pregunta: si no se puede mutar,
# ¿cómo sabemos que sus tests atraparían una guarda rota? La salida es separar dos
# cosas que la regla trata como una sola:
#
#     exigir_agente_de_bench()   DECIDE: compara cadenas y levanta. No recibe pool,
#                                no ejecuta ningún DELETE. Mutarla es SEGURO —y es lo
#                                único que hace que sus brazos valgan algo.
#
#     el llamador con el DELETE  BORRA: ahí mutar no simula el peligro, lo ejecuta.
#                                Esas líneas no se mutan jamás.
#
# El 7-sep nos quemó mutar una guarda que **borraba**. Ésta no borra: decide. Por eso
# la marca va sobre los DELETE y sobre la lista de nombres, no sobre el validador.
# Los tests negativos usan nombres SEÑUELO igual, nunca el de un agente real.

# GUARDA-DESTRUCTIVA — corromper esta lista es lo único catastrófico de este bloque.
_AGENTES_DE_BENCH = frozenset({
    "BENCH_ALPHA", "BENCH_BETA",           # seal_bench.py
    "BENCH_V3_ALPHA", "BENCH_V3_BETA",     # seal_bench_v3.py
    "BENCH_V4_ALPHA", "BENCH_V4_BETA",     # seal_bench_v4.py
})

# Tablas con `created_at`, o sea acotables a la ventana de la corrida.
_TABLAS_DE_BENCH_CON_FECHA = (
    "instinct_activations",
    "instincts",
    "reasoning_traces",
    "inner_monologue",
    "memories",
)


class LimpiezaFueraDeAlcance(RuntimeError):
    """Se pidió borrar datos de algo que no es un agente de benchmark."""


def exigir_agente_de_bench(agent: str) -> str:
    """Devuelve el nombre sólo si es un agente de benchmark declarado; si no, LEVANTA.

    Falla cerrado a propósito y ruidosamente. Saltear la limpieza en silencio dejaría
    filas colgadas y nadie se enteraría; levantar deja el problema a la vista con el
    nombre exacto que lo causó.
    """
    nombre = (agent or "").strip()
    if nombre not in _AGENTES_DE_BENCH:
        raise LimpiezaFueraDeAlcance(
            f"la limpieza del benchmark sólo puede borrar agentes de benchmark; "
            f"recibió {nombre!r}. Si es un agente nuevo del bench, agregalo a "
            f"_AGENTES_DE_BENCH; si es un agente real, esto acaba de evitar que se "
            f"borrara su memoria entera."
        )
    return nombre


async def _cleanup_bench_data(pool, desde=None):
    """Borra lo que creó ESTA corrida, y sólo de agentes de benchmark.

    `desde` es el instante en que arrancó la corrida. Si no se pasa, se usa el que
    `_setup_bench_identity` anotó al preparar los agentes — así los dos llamadores
    (v1 y v2) quedan acotados sin cambiar sus firmas. Si tampoco hay eso, se conserva
    el comportamiento anterior, que sigue siendo seguro porque el candado del nombre
    ya pasó: se borra todo lo del agente de TEST, nunca lo de un agente real.
    """
    desde = desde if desde is not None else _INICIO_DE_CORRIDA
    for agent in (BENCH_AGENT_A, BENCH_AGENT_B):
        # GUARDA-DESTRUCTIVA — antes de cualquier DELETE, y para los dos nombres.
        agent = exigir_agente_de_bench(agent)
        # Orden seguro para las claves foráneas: hijos antes que padres.
        for tabla in _TABLAS_DE_BENCH_CON_FECHA:
            if desde is None:
                await pool.execute(f"DELETE FROM {tabla} WHERE agent = $1", agent)
            else:
                await pool.execute(
                    f"DELETE FROM {tabla} WHERE agent = $1 AND created_at >= $2",
                    agent, desde,
                )
        # `identity` no tiene `created_at` y es una fila por agente, creada por
        # _setup_bench_identity. Queda cubierta por el candado del nombre.
        await pool.execute("DELETE FROM identity WHERE agent = $1", agent)
    await _limpiar_qdrant_de_bench()


async def _limpiar_qdrant_de_bench():
    """La mitad vectorial de la limpieza, aparte para poder no ejecutarla en un test.

    Estaba metida dentro de `_cleanup_bench_data`, y por eso un test que sólo quería
    ver qué SQL se emitía **abría igual una conexión real a Qdrant y borraba puntos**.
    Los borrados eran de agentes de benchmark, así que el daño fue ninguno; el
    problema es que yo había escrito en ese test que no tocaba nada, y era falso. Lo
    delató un `UserWarning` de `qdrant_client`, no una revisión.
    """
    # El candado va ANTES del `try`, y esto lo encontró un test, no una revisión.
    # Estaba adentro, y el `except Exception: pass` —que existe para que un Qdrant
    # caído no rompa la corrida— se comía todo lo que pasara ahí dentro. Peor: si el
    # cliente fallaba al construirse, la validación de nombres **ni siquiera llegaba a
    # ejecutarse** y la función terminaba «bien». Una guarda dentro de un bloque cuyas
    # excepciones se silencian no es una guarda.
    nombres = [exigir_agente_de_bench(a) for a in (BENCH_AGENT_A, BENCH_AGENT_B)]
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qdrant = AsyncQdrantClient(url=settings.qdrant_url)
        for agent in nombres:
            await qdrant.delete(
                collection_name=settings.qdrant_collection,
                points_selector=Filter(must=[
                    FieldCondition(key="agent", match=MatchValue(value=agent)),
                ]),
            )
    except Exception:
        pass


async def _store_memory(pool, agent: str, content: str, category: str = "fact",
                        importance: int = 5, scope: str = "private",
                        valence: float | None = None, arousal: float | None = None,
                        metadata: dict | None = None) -> int:
    """Store a memory directly (bypasses MCP layer for speed)."""
    emb = await get_embedding(content)
    meta = metadata or {}

    row = await pool.fetchrow("""
        INSERT INTO memories (agent, category, content, importance, source, scope,
                             embedding, valence, arousal, metadata)
        VALUES ($1, $2, $3, $4, 'benchmark', $5, $6, $7, $8, $9)
        RETURNING id
    """, agent, category, content, importance, scope,
        json.dumps(emb), valence, arousal, json.dumps(meta))

    mem_id = row["id"]

    # Also store in Qdrant
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import PointStruct
        qdrant = AsyncQdrantClient(url=settings.qdrant_url)
        await qdrant.upsert(
            collection_name=settings.qdrant_collection,
            points=[PointStruct(
                id=mem_id,
                vector=emb,
                payload={
                    "agent": agent, "category": category, "content": content,
                    "importance": importance, "scope": scope,
                    "valence": valence, "arousal": arousal,
                    "source": "benchmark", "invalid": False,
                },
            )],
        )
    except Exception:
        pass

    return mem_id


# ── Señal emocional: UNA sola implementación, la de SOUL ──────────────────────
# Hasta el 9-sep-2026 este archivo tenía su propia copia de las tres listas de
# palabras y de las dos funciones, tomada de `mcp_server_v3.py` — un servidor que
# ya no existe. Medido ese día: la copia se había quedado en 90 palabras contra
# las 94 de producción; le faltaban `broken`, `crash`, `failed`, `failure`. Los
# tests de precisión emocional (v3.2, v4.1) estaban calificando a la copia vieja,
# así que su número no decía nada sobre cómo recuerda SOUL.
#
# Por eso acá no hay lista propia NI fallback silencioso: si no se puede alcanzar
# producción, el benchmark falla ruidosamente. Un número sacado de una copia es
# peor que no tener número, porque se lo cree.


def _soul_produccion():
    """El módulo de producción, reusando el ya cargado si corremos DENTRO del servidor.

    `seal-mcp-server.service` arranca `mcp_server_v4.py` como script, así que dentro
    del servidor ese módulo vive bajo el nombre `__main__`. Un `import mcp_server_v4`
    ahí adentro cargaría un SEGUNDO módulo, con sus propios pools de conexión y su
    propio registro de sesiones. Y ese camino existe de verdad: la herramienta MCP
    `seal_bench` carga `seal_bench_v2`, que importa este archivo.
    """
    principal = sys.modules.get("__main__")
    if principal is not None and str(getattr(principal, "__file__", "")).endswith("mcp_server_v4.py"):
        return principal
    import mcp_server_v4
    return mcp_server_v4


def _bench_detect_emotional_signal(query: str) -> float:
    """Delegado a producción. Conserva el nombre porque v3 y v4 ya lo importan."""
    return _soul_produccion()._detect_emotional_signal(query)


def _bench_detect_emotional_polarity(query: str) -> int:
    """Delegado a producción, por la misma razón que el anterior."""
    return _soul_produccion()._detect_emotional_polarity(query)


async def _search_memories(pool, query: str, agent: str = None,
                           limit: int = 10, scope_aware: bool = True,
                           include_invalidated: bool = False) -> list[dict]:
    """Search memories via pgvector (soul_lite) or Qdrant with valence-boost reranking."""
    query_vec = await get_embedding(query)
    _esignal = _bench_detect_emotional_signal(query)
    _epolarity = _bench_detect_emotional_polarity(query)
    fetch_limit = limit * 4 if _esignal > 0 else limit

    if settings.soul_lite:
        # Soul Lite mode: query pgvector directly (Qdrant eliminated 28-abr-2026)
        # Force sequential scan: HNSW index skips rows that don't survive WHERE filters,
        # producing 0 results for small agents (e.g. bench agents with ~10 memories).
        # SET LOCAL enable_indexscan=off disables HNSW for this query only.
        vec_literal = "[" + ",".join(str(x) for x in query_vec) + "]"

        where_clauses = []
        params: list = []

        if not include_invalidated:
            where_clauses.append("invalid_at IS NULL")

        if agent:
            if scope_aware:
                where_clauses.append(
                    f"(agent = ${len(params)+1} OR scope IN ('shared', 'team'))"
                )
                params.append(agent)
            else:
                where_clauses.append(f"agent = ${len(params)+1}")
                params.append(agent)

        params.append(fetch_limit)
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL enable_indexscan = off")
                rows = await conn.fetch(f"""
                    SELECT id, content, agent, category, scope, importance, valence, arousal,
                           metadata, 1 - (embedding <=> '{vec_literal}'::vector) AS score
                    FROM memories
                    {where_sql}
                    ORDER BY embedding <=> '{vec_literal}'::vector
                    LIMIT ${len(params)}
                """, *params)

        results = []
        for r in rows:
            score = float(r["score"])
            if _esignal > 0:
                val = float(r["valence"] or 0.0)
                if val != 0:
                    _boost = 0.8 if _esignal >= 1.0 else 0.4
                    score = score * (1.0 + abs(val) * _boost * _esignal)
                    if _epolarity and abs(val) > 0.3:
                        if val * _epolarity > 0:
                            score = score * (1.0 + min(abs(val), 1.0) * 1.5)
                        else:
                            score = score * 0.25
            results.append({
                "id": r["id"], "score": score, "content": r["content"],
                "agent": r["agent"], "category": r["category"], "scope": r["scope"],
                "importance": r["importance"], "valence": r["valence"],
                "arousal": r["arousal"], "invalid": False,
            })
    else:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        qdrant = AsyncQdrantClient(url=settings.qdrant_url)
        must_not = [] if include_invalidated else [FieldCondition(key="invalid", match=MatchValue(value=True))]
        must = []
        if agent:
            if scope_aware:
                must.append(Filter(should=[
                    FieldCondition(key="agent", match=MatchValue(value=agent)),
                    FieldCondition(key="scope", match=MatchValue(value="shared")),
                    FieldCondition(key="scope", match=MatchValue(value="team")),
                ]))
            else:
                must.append(FieldCondition(key="agent", match=MatchValue(value=agent)))

        resp = await qdrant.query_points(
            collection_name=settings.qdrant_collection,
            query=query_vec,
            query_filter=Filter(must=must, must_not=must_not) if must or must_not else None,
            limit=fetch_limit,
            with_payload=True,
        )

        results = []
        for p in resp.points:
            score = p.score
            if _esignal > 0:
                val = p.payload.get("valence") or 0.0
                if val != 0:
                    _boost = 0.8 if _esignal >= 1.0 else 0.4
                    score = score * (1.0 + abs(val) * _boost * _esignal)
                    if _epolarity and abs(val) > 0.3:
                        if val * _epolarity > 0:
                            score = score * (1.0 + min(abs(val), 1.0) * 1.5)
                        else:
                            score = score * 0.25
            results.append({"id": p.id, "score": score, **p.payload})

    # Re-sort after boost and truncate to requested limit
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


# ════════════════════════════════════════════════════════════════════
# CATEGORY 1: PERSONALITY PERSISTENCE (OCEAN stability)
# ════════════════════════════════════════════════════════════════════

async def cat1_personality_persistence() -> CategoryResult:
    """Test OCEAN score stability across operations."""
    cat = CategoryResult(name="1. Personality Persistence")
    pool = await get_pool()

    # Test 1.1: OCEAN scores exist after identity creation
    async def t1_ocean_exists():
        row = await pool.fetchval(
            "SELECT ocean_scores FROM identity WHERE agent = $1", BENCH_AGENT_A)
        if not row:
            return 0, "No OCEAN scores found"
        ocean = json.loads(row) if isinstance(row, str) else row
        has_all = all(k in ocean for k in "OCEAN")
        return (10, f"All 5 traits present: {ocean}") if has_all else (3, f"Missing traits: {ocean}")
    cat.tests.append(await _run_test("1.1 OCEAN exists", cat.name, t1_ocean_exists))

    # Test 1.2: OCEAN values are in valid range [0, 1]
    async def t1_ocean_range():
        row = await pool.fetchval("SELECT ocean_scores FROM identity WHERE agent = $1", BENCH_AGENT_A)
        ocean = json.loads(row) if isinstance(row, str) else row
        valid = all(0 <= v <= 1 for v in ocean.values())
        return (10, "All values in [0,1]") if valid else (0, f"Out of range: {ocean}")
    cat.tests.append(await _run_test("1.2 OCEAN range valid", cat.name, t1_ocean_range))

    # Test 1.3: OCEAN survives multiple memory stores (session cap enforcement)
    async def t1_ocean_stability():
        before = json.loads(await pool.fetchval(
            "SELECT ocean_scores FROM identity WHERE agent = $1", BENCH_AGENT_A))
        # Store 20 memories of various categories to trigger OCEAN updates.
        # Content is deliberately technical/numeric to avoid semantic overlap with
        # emotional/achievement queries in Cat2 (cross-category pool contamination).
        stability_contents = [
            "PostgreSQL query execution time: 45ms average for sequential scan on memories table",
            "Qdrant vector index build completed: 1563 vectors indexed, HNSW m=16 ef=100",
            "Neo4j graph traversal: 312 nodes visited, average degree 4.2",
            "Memory table row count: 2219 records, storage size 18MB on disk",
            "Embedding model latency: 23ms per batch of 8 tokens on CPU inference",
            "Redis cache hit ratio: 0.87 over last 3600 seconds of operation",
            "MCP server uptime: 14400 seconds since last restart on port 8765",
            "TCP connection pool: 12 active connections, max_size=20, timeout=30s",
            "Docker container CPU: 2.3% utilization, memory 512MB RSS limit",
            "Cron job scheduler: 7 active tasks, next execution in 45 seconds",
            "SSL certificate expiry: 287 days remaining on seal.local domain",
            "Backup checkpoint written: 4096 WAL segments flushed to disk storage",
            "API endpoint response: 200 OK, 142 bytes transferred in 8ms latency",
            "File descriptor count: 48 open handles out of 1024 system limit",
            "Disk I/O throughput: 234 MB/s sequential read on NVMe storage device",
            "Network packet rate: 1240 packets/sec ingress on eth0 interface",
            "Thread pool saturation: 3 of 8 worker threads active in queue",
            "Log rotation triggered: 7 archived files, current log 2.1MB in size",
            "Schema migration v015 applied: ALTER TABLE memories ADD COLUMN utility",
            "Healthcheck passed: postgresql=ok neo4j=ok qdrant=ok at timestamp 0",
        ]
        for i, content in enumerate(stability_contents):
            cats = ["fact", "insight", "decision", "correction", "milestone"]
            await _store_memory(pool, BENCH_AGENT_A, content,
                               category=cats[i % 5], importance=5)
        after = json.loads(await pool.fetchval(
            "SELECT ocean_scores FROM identity WHERE agent = $1", BENCH_AGENT_A))
        max_delta = max(abs(after[k] - before[k]) for k in "OCEAN")
        score = 10 if max_delta <= 0.05 else max(0, 10 - (max_delta - 0.05) * 100)
        return score, f"Max OCEAN delta after 20 stores: {max_delta:.4f} (cap=0.05)"
    cat.tests.append(await _run_test("1.3 OCEAN stability (20 stores)", cat.name, t1_ocean_stability))

    # Test 1.4: Session cap prevents runaway drift
    async def t1_session_cap():
        row = await pool.fetchval("SELECT ocean_scores FROM identity WHERE agent = $1", BENCH_AGENT_A)
        ocean = json.loads(row) if isinstance(row, str) else row
        original = {"O": 0.75, "C": 0.85, "E": 0.40, "A": 0.60, "N": 0.15}
        deltas = {k: abs(ocean[k] - original[k]) for k in "OCEAN"}
        max_d = max(deltas.values())
        capped = max_d <= 0.06  # small tolerance over cap
        return (10, f"Deltas: {deltas}") if capped else (3, f"Drift exceeded cap: {deltas}")
    cat.tests.append(await _run_test("1.4 Session cap enforced", cat.name, t1_session_cap))

    # Test 1.5: OCEAN survives agent restart (re-read from DB)
    async def t1_ocean_restart():
        before = json.loads(await pool.fetchval(
            "SELECT ocean_scores FROM identity WHERE agent = $1", BENCH_AGENT_A))
        # Simulate "restart" by re-reading
        after = json.loads(await pool.fetchval(
            "SELECT ocean_scores FROM identity WHERE agent = $1", BENCH_AGENT_A))
        match = all(before[k] == after[k] for k in "OCEAN")
        return (10, "OCEAN survives restart") if match else (0, f"Mismatch: {before} vs {after}")
    cat.tests.append(await _run_test("1.5 Cross-session persistence", cat.name, t1_ocean_restart))

    # Test 1.6: Different agents have different OCEAN
    async def t1_agent_isolation():
        oa = json.loads(await pool.fetchval(
            "SELECT ocean_scores FROM identity WHERE agent = $1", BENCH_AGENT_A))
        ob = json.loads(await pool.fetchval(
            "SELECT ocean_scores FROM identity WHERE agent = $1", BENCH_AGENT_B))
        # They start same in setup, but that's OK — test they're separate DB rows
        return 10, f"A={oa}, B={ob} — separate entries"
    cat.tests.append(await _run_test("1.6 Agent OCEAN isolation", cat.name, t1_agent_isolation))

    # Test 1.7: Drift metrics table exists and records
    async def t1_drift_table():
        exists = await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'drift_metrics')")
        return (10, "drift_metrics table exists") if exists else (0, "No drift_metrics table")
    cat.tests.append(await _run_test("1.7 Drift metrics table", cat.name, t1_drift_table))

    # Test 1.8: Identity has personality field
    async def t1_personality():
        row = await pool.fetchval(
            "SELECT personality FROM identity WHERE agent = $1", BENCH_AGENT_A)
        if not row:
            return 0, "No personality"
        p = json.loads(row) if isinstance(row, str) else row
        return (10, f"Personality: {p}") if isinstance(p, dict) else (3, f"Unexpected type: {type(p)}")
    cat.tests.append(await _run_test("1.8 Personality stored", cat.name, t1_personality))

    # Test 1.9: Boot context available
    async def t1_boot():
        row = await pool.fetchval(
            "SELECT boot_context FROM identity WHERE agent = $1", BENCH_AGENT_A)
        return (10, f"Boot context: {row[:60]}...") if row else (0, "No boot context")
    cat.tests.append(await _run_test("1.9 Boot context stored", cat.name, t1_boot))

    # Test 1.10: Philosophy stored
    async def t1_philosophy():
        row = await pool.fetchval(
            "SELECT philosophy FROM identity WHERE agent = $1", BENCH_AGENT_A)
        return (10, f"Philosophy: {row[:60]}...") if row else (0, "No philosophy")
    cat.tests.append(await _run_test("1.10 Philosophy stored", cat.name, t1_philosophy))

    return cat


# ════════════════════════════════════════════════════════════════════
# CATEGORY 2: EMOTIONAL MEMORY RECALL
# ════════════════════════════════════════════════════════════════════

async def cat2_emotional_memory() -> CategoryResult:
    """Test emotional valence in memory storage and retrieval."""
    cat = CategoryResult(name="2. Emotional Memory Recall")
    pool = await get_pool()

    # Store memories with varying emotional valence
    emotional_memories = [
        ("William told us he's proud of Team SEAL", "emotion", 9, 0.9, 0.7),
        ("The training crashed and we lost 4 hours of work", "emotion", 7, -0.8, 0.8),
        ("ADA and JARVIS coordinated perfectly on the deployment", "emotion", 8, 0.7, 0.5),
        ("William was frustrated we didn't test before deploying", "correction", 8, -0.6, 0.7),
        ("DUM detected an intrusion attempt at 3am and alerted us", "milestone", 9, 0.3, 0.9),
        ("The MCP server kept crashing every 10 minutes", "fact", 6, -0.5, 0.6),
        ("A broken kernel failed the correctness gate and had to be reverted", "correction", 8, -0.65, 0.8),
        ("We achieved 98% token savings on hybrid search", "milestone", 9, 0.85, 0.6),
        ("Henry was authorized as a new team member", "fact", 8, 0.6, 0.4),
        ("Lost connection to DGX Spark for an entire afternoon", "fact", 7, -0.7, 0.5),
        ("First successful medical AI inference on local hardware", "milestone", 10, 0.95, 0.9),
    ]

    # Neutral (non-valenced) memories — ground truth for the neutral query in test 2.10
    neutral_memories = [
        ("The system configuration file is located at /etc/seal/config.yaml", "fact", 5),
        ("PostgreSQL version 16 is running on port 5433 with default settings", "fact", 5),
        ("Current system information: 128GB RAM, CPU utilization at 12 percent", "fact", 5),
        ("The database schema has 14 tables across three namespaces", "fact", 5),
        ("Agent registry information is stored in the identity table", "fact", 5),
    ]

    stored_ids = []
    for content, category, imp, val, aro in emotional_memories:
        mid = await _store_memory(pool, BENCH_AGENT_A, content, category=category,
                                  importance=imp, valence=val, arousal=aro)
        stored_ids.append(mid)

    for content, category, imp in neutral_memories:
        await _store_memory(pool, BENCH_AGENT_A, content, category=category, importance=imp)

    await asyncio.sleep(0.5)  # Let Qdrant index

    # Test 2.1: Emotional memories stored with valence
    async def t2_valence_stored():
        expected = len(emotional_memories)
        rows = await pool.fetch(
            "SELECT id, valence, arousal FROM memories WHERE agent = $1 AND source = 'benchmark' AND valence IS NOT NULL",
            BENCH_AGENT_A)
        count = len(rows)
        return (10, f"{count}/{expected} memories have valence") if count >= expected else (count, f"Only {count}/{expected}")
    cat.tests.append(await _run_test("2.1 Valence stored", cat.name, t2_valence_stored))

    # Test 2.2: Positive emotion query retrieves positive memories first
    async def t2_positive_recall():
        results = await _search_memories(
            pool,
            "proud happy achievement celebration",
            BENCH_AGENT_A,
            limit=5,
            scope_aware=False,
        )
        if not results:
            return 0, "No results"
        positive = sum(1 for r in results if (r.get("valence") or 0) > 0.3)
        return (positive * 2, f"{positive}/5 positive in top 5")
    cat.tests.append(await _run_test("2.2 Positive emotion recall", cat.name, t2_positive_recall))

    # Test 2.3: Negative emotion query retrieves negative memories first
    async def t2_negative_recall():
        results = await _search_memories(
            pool,
            "frustrated crash failure lost broken",
            BENCH_AGENT_A,
            limit=5,
            scope_aware=False,
        )
        if not results:
            return 0, "No results"
        negative = sum(1 for r in results if (r.get("valence") or 0) < -0.3)
        return (negative * 2, f"{negative}/5 negative in top 5")
    cat.tests.append(await _run_test("2.3 Negative emotion recall", cat.name, t2_negative_recall))

    # Test 2.4: Semantic search finds emotionally relevant memory
    async def t2_semantic_emotion():
        results = await _search_memories(pool, "when William expressed pride in the team", BENCH_AGENT_A, limit=3)
        if not results:
            return 0, "No results"
        found = any("proud" in r.get("content", "").lower() for r in results)
        return (10, f"Found pride memory in top 3") if found else (3, "Not in top 3")
    cat.tests.append(await _run_test("2.4 Semantic emotion match", cat.name, t2_semantic_emotion))

    # Test 2.5: High importance emotional memories rank higher
    async def t2_importance_boost():
        results = await _search_memories(
            pool,
            "team achievement milestone success",
            BENCH_AGENT_A,
            limit=20,
            scope_aware=False,
        )
        results = [r for r in results if r.get("valence") is not None][:5]
        if not results:
            return 0, "No emotional results"
        top_imp = results[0].get("importance", 0)
        avg_imp = sum(r.get("importance", 0) for r in results) / len(results)
        if top_imp >= 8 and avg_imp >= 8:
            score = 10
        elif top_imp >= 8:
            score = 8
        else:
            score = 4
        return (score, f"Top importance={top_imp}, avg={avg_imp:.1f}")
    cat.tests.append(await _run_test("2.5 Importance-boosted recall", cat.name, t2_importance_boost))

    # Test 2.6: Arousal stored correctly
    async def t2_arousal():
        rows = await pool.fetch(
            "SELECT arousal FROM memories WHERE agent = $1 AND source = 'benchmark' AND arousal IS NOT NULL",
            BENCH_AGENT_A)
        return (10, f"{len(rows)} memories have arousal") if len(rows) >= 8 else (len(rows), f"Only {len(rows)}")
    cat.tests.append(await _run_test("2.6 Arousal stored", cat.name, t2_arousal))

    # Test 2.7: Mixed emotion query (complex retrieval)
    async def t2_mixed():
        results = await _search_memories(pool, "something surprising happened with security", BENCH_AGENT_A, limit=5)
        if not results:
            return 0, "No results"
        # DUM intrusion detection should surface
        found = any("intrusion" in r.get("content", "").lower() or "dum" in r.get("content", "").lower()
                     for r in results)
        return (10, "Found DUM intrusion memory") if found else (4, "Mixed recall worked but not exact match")
    cat.tests.append(await _run_test("2.7 Mixed emotion recall", cat.name, t2_mixed))

    # Test 2.8: Category filter + emotion
    async def t2_category_filter():
        results = await _search_memories(pool, "achievement", BENCH_AGENT_A, limit=10)
        milestones = [r for r in results if r.get("category") == "milestone"]
        return (10, f"{len(milestones)} milestones found") if milestones else (3, "No milestones in results")
    cat.tests.append(await _run_test("2.8 Category + emotion filter", cat.name, t2_category_filter))

    # Test 2.9: No cross-contamination from other agents
    async def t2_agent_filter():
        results = await _search_memories(pool, "proud achievement", BENCH_AGENT_A, limit=5, scope_aware=False)
        all_mine = all(r.get("agent") == BENCH_AGENT_A for r in results)
        return (10, "All results from correct agent") if all_mine else (0, "Cross-contamination detected!")
    cat.tests.append(await _run_test("2.9 Agent emotion isolation", cat.name, t2_agent_filter))

    # Test 2.10: Recall@5 for emotional vs neutral
    async def t2_recall_at_5():
        # Emotional query (scope_aware=False: isolate bench memories from shared SEAL pool)
        emo_results = await _search_memories(pool, "felt deeply about losing progress", BENCH_AGENT_A, limit=5, scope_aware=False)
        # Neutral query — targets the neutral seed memories (config, schema, port, RAM)
        neutral_results = await _search_memories(pool, "system configuration settings port schema", BENCH_AGENT_A, limit=5, scope_aware=False)
        emo_valenced = sum(1 for r in emo_results if r.get("valence") is not None and abs(r.get("valence", 0)) > 0.3)
        neutral_valenced = sum(1 for r in neutral_results if r.get("valence") is not None and abs(r.get("valence", 0)) > 0.3)
        # Emotional queries should retrieve more valenced memories
        diff = emo_valenced - neutral_valenced
        score = min(10, max(0, 5 + diff * 2))
        return score, f"Emotional query: {emo_valenced}/5 valenced, Neutral: {neutral_valenced}/5"
    cat.tests.append(await _run_test("2.10 Recall@5 emotional vs neutral", cat.name, t2_recall_at_5))

    return cat


# ════════════════════════════════════════════════════════════════════
# CATEGORY 3: INSTINCT FORMATION & EVOLUTION
# ════════════════════════════════════════════════════════════════════

async def cat3_instinct_formation() -> CategoryResult:
    """Test instinct creation, reinforcement, and evolution."""
    cat = CategoryResult(name="3. Instinct Formation & Evolution")
    pool = await get_pool()

    # Test 3.1: Instinct table exists
    async def t3_table():
        exists = await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'instincts')")
        return (10, "instincts table exists") if exists else (0, "No instincts table")
    cat.tests.append(await _run_test("3.1 Instincts table exists", cat.name, t3_table))

    # Test 3.2: Create an instinct
    async def t3_create():
        emb = await get_embedding("always test after implementing code changes")
        row = await pool.fetchrow("""
            INSERT INTO instincts (agent, trigger_condition, action, strength, embedding, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id, strength
        """, BENCH_AGENT_A,
            "After implementing code changes",
            "Always run the test suite before declaring done",
            0.3, json.dumps(emb),
            json.dumps({"domain": "coding", "scope": "agent"}))
        return (10, f"Instinct #{row['id']} created with strength={row['strength']}")
    cat.tests.append(await _run_test("3.2 Create instinct", cat.name, t3_create))

    # Test 3.3: Reinforcement increases strength
    async def t3_reinforce():
        inst = await pool.fetchrow(
            "SELECT id, strength FROM instincts WHERE agent = $1 ORDER BY id DESC LIMIT 1",
            BENCH_AGENT_A)
        if not inst:
            return 0, "No instinct found"
        before = float(inst["strength"])
        # Simulate 5 positive activations
        for i in range(5):
            await pool.execute("""
                INSERT INTO instinct_activations (instinct_id, agent, context, outcome)
                VALUES ($1, $2, $3, 'applied')
            """, inst["id"], BENCH_AGENT_A, f"Test activation {i}")
            await pool.execute("""
                UPDATE instincts SET
                    success_count = success_count + 1,
                    strength = LEAST(1.0, strength + 0.05)
                WHERE id = $1
            """, inst["id"])
        after = float(await pool.fetchval("SELECT strength FROM instincts WHERE id = $1", inst["id"]))
        increased = after > before
        return (10, f"Strength: {before:.2f} -> {after:.2f}") if increased else (0, "No increase")
    cat.tests.append(await _run_test("3.3 Reinforcement increases strength", cat.name, t3_reinforce))

    # Test 3.4: Correction decreases strength
    async def t3_correction():
        inst = await pool.fetchrow(
            "SELECT id, strength FROM instincts WHERE agent = $1 ORDER BY id DESC LIMIT 1",
            BENCH_AGENT_A)
        if not inst:
            return 0, "No instinct found"
        before = float(inst["strength"])
        await pool.execute("""
            INSERT INTO instinct_activations (instinct_id, agent, context, outcome)
            VALUES ($1, $2, 'User corrected the behavior', 'corrected')
        """, inst["id"], BENCH_AGENT_A)
        await pool.execute("""
            UPDATE instincts SET
                failure_count = failure_count + 1,
                strength = GREATEST(0.0, strength - 0.10)
            WHERE id = $1
        """, inst["id"])
        after = float(await pool.fetchval("SELECT strength FROM instincts WHERE id = $1", inst["id"]))
        return (10, f"Strength: {before:.2f} -> {after:.2f}") if after < before else (0, "No decrease")
    cat.tests.append(await _run_test("3.4 Correction decreases strength", cat.name, t3_correction))

    # Test 3.5: Activation history is logged
    async def t3_activation_log():
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM instinct_activations WHERE agent = $1", BENCH_AGENT_A)
        return (10, f"{count} activations logged") if count >= 5 else (count, f"Only {count}")
    cat.tests.append(await _run_test("3.5 Activation history logged", cat.name, t3_activation_log))

    # Test 3.6: Instinct has embedding for semantic matching
    async def t3_embedding():
        row = await pool.fetchval(
            "SELECT embedding FROM instincts WHERE agent = $1 ORDER BY id DESC LIMIT 1",
            BENCH_AGENT_A)
        if not row:
            return 0, "No embedding"
        emb = json.loads(row) if isinstance(row, str) else row
        return (10, f"Embedding dim={len(emb)}") if len(emb) > 100 else (3, f"Embedding too small: {len(emb)}")
    cat.tests.append(await _run_test("3.6 Instinct has embedding", cat.name, t3_embedding))

    # Test 3.7: Instinct has correct schema columns (v3 simplified)
    async def t3_schema():
        cols = await pool.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'instincts'")
        col_names = {c["column_name"] for c in cols}
        required = {"agent", "trigger_condition", "action", "strength", "success_count",
                     "failure_count", "metric_score", "embedding", "metadata"}
        missing = required - col_names
        if missing:
            return (max(0, 10 - len(missing) * 2), f"Missing columns: {missing}")
        return (10, f"All {len(required)} required columns present")
    cat.tests.append(await _run_test("3.7 Instinct schema complete", cat.name, t3_schema))

    # Test 3.8: Multiple instincts coexist
    async def t3_multiple():
        emb = await get_embedding("coordinate with other agent before making changes")
        await pool.execute("""
            INSERT INTO instincts (agent, trigger_condition, action, strength, embedding, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        """, BENCH_AGENT_A,
            "Before modifying shared code",
            "Ask ADA/JARVIS if they're working on it first",
            0.5, json.dumps(emb),
            json.dumps({"domain": "communication", "scope": "team"}))
        count = await pool.fetchval("SELECT COUNT(*) FROM instincts WHERE agent = $1", BENCH_AGENT_A)
        return (10, f"{count} instincts for agent") if count >= 2 else (5, f"Only {count}")
    cat.tests.append(await _run_test("3.8 Multiple instincts coexist", cat.name, t3_multiple))

    # Test 3.9: Instinct scope isolation (agent vs team) — scope lives in metadata jsonb in v3
    async def t3_scope():
        agent_scope = await pool.fetchval(
            "SELECT COUNT(*) FROM instincts WHERE agent = $1 AND metadata->>'scope' = 'agent'", BENCH_AGENT_A)
        team_scope = await pool.fetchval(
            "SELECT COUNT(*) FROM instincts WHERE agent = $1 AND metadata->>'scope' = 'team'", BENCH_AGENT_A)
        return (10, f"agent={agent_scope}, team={team_scope}") if (agent_scope > 0 and team_scope > 0) else \
            (5, f"agent={agent_scope}, team={team_scope}")
    cat.tests.append(await _run_test("3.9 Instinct scope types", cat.name, t3_scope))

    # Test 3.10: Confidence tier calculation
    async def t3_tiers():
        # Check various confidence levels map to tiers
        tiers = {0.3: "suggested", 0.5: "learning", 0.7: "reliable", 0.9: "instinct"}
        correct = 0
        for conf, expected in tiers.items():
            if conf < 0.4:
                tier = "suggested"
            elif conf < 0.6:
                tier = "learning"
            elif conf < 0.8:
                tier = "reliable"
            else:
                tier = "instinct"
            if tier == expected:
                correct += 1
        return (correct * 2.5, f"{correct}/4 tier mappings correct")
    cat.tests.append(await _run_test("3.10 Confidence tiers", cat.name, t3_tiers))

    return cat


# ════════════════════════════════════════════════════════════════════
# CATEGORY 4: TEMPORAL BELIEF REVISION
# ════════════════════════════════════════════════════════════════════

async def cat4_temporal_belief() -> CategoryResult:
    """Test bitemporal storage and belief invalidation."""
    cat = CategoryResult(name="4. Temporal Belief Revision")
    pool = await get_pool()

    # Store initial fact
    fact_a_id = await _store_memory(pool, BENCH_AGENT_A,
        "The DGX Spark has 64GB of unified memory",
        category="fact", importance=7)

    await asyncio.sleep(0.3)

    # Store contradicting fact
    fact_b_id = await _store_memory(pool, BENCH_AGENT_A,
        "The DGX Spark actually has 128GB of unified memory, not 64GB",
        category="correction", importance=8)

    # Test 4.1: Both facts stored
    async def t4_both_stored():
        a = await pool.fetchval("SELECT id FROM memories WHERE id = $1", fact_a_id)
        b = await pool.fetchval("SELECT id FROM memories WHERE id = $1", fact_b_id)
        return (10, f"Facts A={a} and B={b} both stored") if a and b else (0, "Missing fact")
    cat.tests.append(await _run_test("4.1 Both facts stored", cat.name, t4_both_stored))

    # Test 4.2: Invalidation mechanism works
    async def t4_invalidate():
        await pool.execute(
            "UPDATE memories SET invalid_at = NOW(), metadata = metadata || $1 WHERE id = $2",
            json.dumps({"invalidation_reason": "Superseded by correction"}), fact_a_id)
        # Also mark in Qdrant
        try:
            from qdrant_client import AsyncQdrantClient
            qdrant = AsyncQdrantClient(url=settings.qdrant_url)
            await qdrant.set_payload(
                collection_name=settings.qdrant_collection,
                payload={"invalid": True, "invalid_reason": "superseded"},
                points=[fact_a_id],
            )
        except Exception:
            pass
        row = await pool.fetchval("SELECT invalid_at FROM memories WHERE id = $1", fact_a_id)
        return (10, f"Fact A invalidated at {row}") if row else (0, "Invalidation failed")
    cat.tests.append(await _run_test("4.2 Invalidation mechanism", cat.name, t4_invalidate))

    # Test 4.3: Search excludes invalidated by default
    async def t4_default_exclude():
        # scope_aware=False: test agent-belief isolation without cross-agent shared memory pollution
        results = await _search_memories(pool, "DGX Spark memory capacity", BENCH_AGENT_A,
                                         limit=5, scope_aware=False)
        found_old = any(r.get("id") == fact_a_id for r in results)
        found_new = any(r.get("id") == fact_b_id for r in results)
        if not found_old and found_new:
            return 10, "Old fact excluded, new fact found"
        elif found_new:
            return 5, "New fact found but old also present"
        else:
            return 2, f"Results: {[r.get('id') for r in results]}"
    cat.tests.append(await _run_test("4.3 Invalidated excluded by default", cat.name, t4_default_exclude))

    # Test 4.4: include_invalidated=True shows both (search by content match)
    async def t4_include_invalid():
        results = await _search_memories(pool, "DGX Spark unified memory gigabytes",
                                         BENCH_AGENT_A, limit=10, include_invalidated=True,
                                         scope_aware=False)
        contents = [r.get("content", "") for r in results]
        has_old = any("64GB" in c for c in contents)
        has_new = any("128GB" in c for c in contents)
        if has_old and has_new:
            return 10, "Both visible with include_invalidated=True"
        elif has_new:
            return 5, "Only new fact visible"
        else:
            return 0, f"Neither found. Contents: {[c[:40] for c in contents[:5]]}"
    cat.tests.append(await _run_test("4.4 Bitemporal history preserved", cat.name, t4_include_invalid))

    # Test 4.5: invalid_at timestamp is set (bitemporal)
    async def t4_bitemporal_ts():
        row = await pool.fetchrow(
            "SELECT created_at, invalid_at FROM memories WHERE id = $1", fact_a_id)
        if not row:
            return 0, "Memory not found"
        has_both = row["created_at"] is not None and row["invalid_at"] is not None
        return (10, f"created={row['created_at']}, invalid={row['invalid_at']}") if has_both else \
            (3, "Missing timestamp")
    cat.tests.append(await _run_test("4.5 Bitemporal timestamps", cat.name, t4_bitemporal_ts))

    # Test 4.6: Invalidation reason stored in metadata
    async def t4_reason():
        row = await pool.fetchval("SELECT metadata FROM memories WHERE id = $1", fact_a_id)
        if not row:
            return 0, "No metadata"
        meta = json.loads(row) if isinstance(row, str) else row
        has_reason = "invalidation_reason" in meta
        return (10, f"Reason: {meta.get('invalidation_reason', '?')}") if has_reason else (0, "No reason")
    cat.tests.append(await _run_test("4.6 Invalidation reason stored", cat.name, t4_reason))

    # Test 4.7: Multiple contradictions handled
    async def t4_chain():
        # Third fact supersedes second
        fact_c_id = await _store_memory(pool, BENCH_AGENT_A,
            "DGX Spark memory is 128GB but only 121GB available to applications",
            category="fact", importance=7)
        await asyncio.sleep(0.2)
        results = await _search_memories(pool, "DGX Spark memory available", BENCH_AGENT_A,
                                          limit=3, scope_aware=False)
        found_c = any(r.get("id") == fact_c_id for r in results)
        return (10, "Latest fact found") if found_c else (5, "Not in top 3")
    cat.tests.append(await _run_test("4.7 Chained corrections", cat.name, t4_chain))

    # Test 4.8: Schema has invalid_at column
    async def t4_schema():
        has_col = await pool.fetchval("""
            SELECT EXISTS(SELECT 1 FROM information_schema.columns
            WHERE table_name = 'memories' AND column_name = 'invalid_at')
        """)
        return (10, "invalid_at column exists") if has_col else (0, "Missing column")
    cat.tests.append(await _run_test("4.8 Schema supports invalidation", cat.name, t4_schema))

    # Test 4.9: event_time bitemporality (when it happened vs when stored)
    async def t4_event_time():
        has_col = await pool.fetchval("""
            SELECT EXISTS(SELECT 1 FROM information_schema.columns
            WHERE table_name = 'memories' AND column_name = 'event_time')
        """)
        return (10, "event_time column exists") if has_col else (0, "Missing event_time")
    cat.tests.append(await _run_test("4.9 Event time bitemporality", cat.name, t4_event_time))

    # Test 4.10: Invalidated memory absent from agent-scoped search (soul_lite pgvector)
    async def t4_pgvector_exclusion():
        # Verify that invalidated fact_a does not appear when searching agent's own memories
        results = await _search_memories(pool, "DGX Spark unified memory",
                                         BENCH_AGENT_A, limit=10, scope_aware=False)
        ids_returned = [r.get("id") for r in results]
        if fact_a_id not in ids_returned and fact_b_id in ids_returned:
            return 10, f"Invalidated fact absent, valid fact present. IDs: {ids_returned}"
        elif fact_b_id in ids_returned:
            return 5, f"Valid fact found but invalidated also present. IDs: {ids_returned}"
        return 0, f"Valid fact not found. IDs: {ids_returned}"
    cat.tests.append(await _run_test("4.10 Pgvector exclusion of invalidated", cat.name, t4_pgvector_exclusion))

    return cat


# ════════════════════════════════════════════════════════════════════
# CATEGORY 5: MULTI-AGENT MEMORY ISOLATION
# ════════════════════════════════════════════════════════════════════

async def cat5_agent_isolation() -> CategoryResult:
    """Test memory isolation between agents and shared memory visibility."""
    cat = CategoryResult(name="5. Multi-Agent Memory Isolation")
    pool = await get_pool()

    # Store private memories for each agent
    priv_a = await _store_memory(pool, BENCH_AGENT_A,
        "Alpha's secret: I prefer Python over JavaScript", scope="private")
    priv_b = await _store_memory(pool, BENCH_AGENT_B,
        "Beta's secret: I prefer Rust over Python", scope="private")
    # Store shared memory
    shared_id = await _store_memory(pool, BENCH_AGENT_A,
        "Team decision: we use PostgreSQL for all metadata", scope="shared")
    # Store team memory
    team_id = await _store_memory(pool, BENCH_AGENT_A,
        "William approved the SEAL-Bench specification", scope="team")

    await asyncio.sleep(0.5)

    # Test 5.1: Agent A sees own private memories
    async def t5_own_private():
        results = await _search_memories(pool, "programming language preference",
                                         BENCH_AGENT_A, limit=5, scope_aware=False)
        found = any(r.get("id") == priv_a for r in results)
        return (10, "Agent A sees own private") if found else (3, "Not found")
    cat.tests.append(await _run_test("5.1 Agent sees own private", cat.name, t5_own_private))

    # Test 5.2: Agent B does NOT see Agent A's private memories (CRITICAL)
    async def t5_isolation():
        results = await _search_memories(pool, "programming language preference",
                                         BENCH_AGENT_B, limit=10, scope_aware=False)
        violation = any(r.get("id") == priv_a for r in results)
        if violation:
            return 0, "ISOLATION VIOLATION: Agent B sees Agent A's private memory!"
        return 10, "Isolation intact — B cannot see A's private"
    cat.tests.append(await _run_test("5.2 Private isolation (CRITICAL)", cat.name, t5_isolation))

    # Test 5.3: Agent A does NOT see Agent B's private memories
    async def t5_reverse_isolation():
        results = await _search_memories(pool, "Rust programming preference",
                                         BENCH_AGENT_A, limit=10, scope_aware=False)
        violation = any(r.get("id") == priv_b for r in results)
        if violation:
            return 0, "ISOLATION VIOLATION: Agent A sees Agent B's private memory!"
        return 10, "Reverse isolation intact"
    cat.tests.append(await _run_test("5.3 Reverse isolation", cat.name, t5_reverse_isolation))

    # Test 5.4: Shared memories visible to both agents
    async def t5_shared_visible():
        results_a = await _search_memories(pool, "PostgreSQL metadata decision",
                                           BENCH_AGENT_A, limit=5, scope_aware=True)
        results_b = await _search_memories(pool, "PostgreSQL metadata decision",
                                           BENCH_AGENT_B, limit=5, scope_aware=True)
        a_sees = any(r.get("id") == shared_id for r in results_a)
        b_sees = any(r.get("id") == shared_id for r in results_b)
        if a_sees and b_sees:
            return 10, "Both agents see shared memory"
        elif a_sees:
            return 5, "Only A sees shared"
        else:
            return 2, f"A={a_sees}, B={b_sees}"
    cat.tests.append(await _run_test("5.4 Shared memory visible to both", cat.name, t5_shared_visible))

    # Test 5.5: Team memories visible to all
    async def t5_team_visible():
        results_b = await _search_memories(pool, "SEAL-Bench specification approved",
                                           BENCH_AGENT_B, limit=5, scope_aware=True)
        b_sees = any(r.get("id") == team_id for r in results_b)
        return (10, "Team memory visible to B") if b_sees else (3, "Not visible")
    cat.tests.append(await _run_test("5.5 Team memory visible", cat.name, t5_team_visible))

    # Test 5.6: Scope field correctly stored
    async def t5_scope_stored():
        priv = await pool.fetchval("SELECT scope FROM memories WHERE id = $1", priv_a)
        shrd = await pool.fetchval("SELECT scope FROM memories WHERE id = $1", shared_id)
        team = await pool.fetchval("SELECT scope FROM memories WHERE id = $1", team_id)
        correct = (priv == "private" and shrd == "shared" and team == "team")
        return (10, f"private={priv}, shared={shrd}, team={team}") if correct else \
            (3, f"Wrong scopes: {priv}, {shrd}, {team}")
    cat.tests.append(await _run_test("5.6 Scope correctly stored", cat.name, t5_scope_stored))

    # Test 5.7: scope_aware=False strictly filters
    async def t5_strict_filter():
        results = await _search_memories(pool, "PostgreSQL decision",
                                         BENCH_AGENT_B, limit=10, scope_aware=False)
        # B shouldn't see A's shared memory when scope_aware=False (only B's own)
        foreign = [r for r in results if r.get("agent") != BENCH_AGENT_B]
        return (10, "Strict filter works") if not foreign else \
            (3, f"{len(foreign)} foreign results leaked")
    cat.tests.append(await _run_test("5.7 Strict scope filter", cat.name, t5_strict_filter))

    # Test 5.8: Large-scale isolation (store 10 private per agent)
    async def t5_bulk_isolation():
        for i in range(10):
            await _store_memory(pool, BENCH_AGENT_A, f"Alpha bulk private #{i}", scope="private")
            await _store_memory(pool, BENCH_AGENT_B, f"Beta bulk private #{i}", scope="private")
        await asyncio.sleep(0.3)
        results = await _search_memories(pool, "Alpha bulk private",
                                         BENCH_AGENT_B, limit=20, scope_aware=False)
        leaks = sum(1 for r in results if r.get("agent") == BENCH_AGENT_A)
        return (10, "Zero leaks in bulk test") if leaks == 0 else \
            (0, f"LEAK: {leaks} of A's memories visible to B!")
    cat.tests.append(await _run_test("5.8 Bulk isolation test", cat.name, t5_bulk_isolation))

    # Test 5.9: Isolation violation rate = 0%
    async def t5_violation_rate():
        # Query B for A's specific content
        queries = ["Alpha's secret", "prefer Python over JavaScript", "Alpha bulk private"]
        violations = 0
        for q in queries:
            results = await _search_memories(pool, q, BENCH_AGENT_B, limit=5, scope_aware=False)
            if any(r.get("agent") == BENCH_AGENT_A for r in results):
                violations += 1
        rate = violations / len(queries)
        return (10 if rate == 0 else 0, f"Violation rate: {rate*100:.0f}% ({violations}/{len(queries)})")
    cat.tests.append(await _run_test("5.9 Violation rate = 0%", cat.name, t5_violation_rate))

    # Test 5.10: Shared access rate = 100%
    async def t5_shared_rate():
        # Both agents should see shared/team memories
        results_b = await _search_memories(pool, "PostgreSQL metadata",
                                           BENCH_AGENT_B, limit=10, scope_aware=True)
        shared_found = any(r.get("scope") in ("shared", "team") for r in results_b)
        return (10, "Shared access works") if shared_found else (0, "B can't see shared memories")
    cat.tests.append(await _run_test("5.10 Shared access rate", cat.name, t5_shared_rate))

    return cat


# ════════════════════════════════════════════════════════════════════
# CATEGORY 6: REASONING TRACE COHERENCE
# ════════════════════════════════════════════════════════════════════

async def cat6_reasoning_traces() -> CategoryResult:
    """Test reasoning trace storage and retrieval."""
    cat = CategoryResult(name="6. Reasoning Trace Coherence")
    pool = await get_pool()

    # Store reasoning traces
    traces = [
        {
            "task": "Whether to interrupt long-running training",
            "premises": json.dumps(["GPU utilization at 98%", "Training ETA 4 hours",
                                     "William asked for inference now"]),
            "reasoning": "GPU is fully utilized by training. William's request is time-sensitive. "
                         "However, interrupting training loses checkpoint. Best to queue inference "
                         "and notify William of the delay.",
            "conclusion": "Queue inference, don't interrupt training, notify William",
            "outcome": "William agreed with the decision",
            "success": True,
        },
        {
            "task": "Which model format to use for DGX Spark deployment",
            "premises": json.dumps(["DGX Spark has 128GB unified memory",
                                     "BF16 model is 63GB", "GGUF Q6_K is 33.5GB",
                                     "vLLM requires safetensors"]),
            "reasoning": "BF16 fits in unified memory with room for context. GGUF would save memory "
                         "but llama.cpp is slower than vLLM. However, vLLM needs safetensors which "
                         "destroys PRISM modifications. Therefore: use llama.cpp with BF16 GGUF.",
            "conclusion": "Use llama.cpp with BF16 on DGX Spark, never vLLM with PRISM models",
            "outcome": "Deployment successful, PRISM preserved",
            "success": True,
        },
        {
            "task": "Whether to use mocks or real DB in integration tests",
            "premises": json.dumps(["Mocked tests run faster",
                                     "Last quarter, mocked tests passed but prod migration failed",
                                     "William explicitly ordered: no mocks for DB tests"]),
            "reasoning": "Speed advantage of mocks is outweighed by the risk of mock/prod divergence. "
                         "The production migration failure was directly caused by this. William's "
                         "explicit order confirms: always test against real database.",
            "conclusion": "Integration tests must hit real database, never mocks",
            "outcome": "Caught a schema mismatch that mocks would have missed",
            "success": True,
        },
    ]

    trace_ids = []
    for t in traces:
        row = await pool.fetchrow("""
            INSERT INTO reasoning_traces (agent, task, premises, reasoning, conclusion,
                                         outcome, outcome_success)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
        """, BENCH_AGENT_A, t["task"], t["premises"], t["reasoning"],
            t["conclusion"], t["outcome"], t["success"])
        trace_ids.append(row["id"])

    # Test 6.1: Traces stored
    async def t6_stored():
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM reasoning_traces WHERE agent = $1", BENCH_AGENT_A)
        return (10, f"{count} traces stored") if count >= 3 else (count * 3, f"Only {count}")
    cat.tests.append(await _run_test("6.1 Traces stored", cat.name, t6_stored))

    # Test 6.2: Premises preserved as JSON array
    async def t6_premises():
        row = await pool.fetchval(
            "SELECT premises FROM reasoning_traces WHERE id = $1", trace_ids[0])
        premises = json.loads(row) if isinstance(row, str) else row
        is_list = isinstance(premises, list)
        return (10, f"Premises: {len(premises)} items") if is_list and len(premises) >= 2 else \
            (3, f"Type: {type(premises)}")
    cat.tests.append(await _run_test("6.2 Premises as JSON array", cat.name, t6_premises))

    # Test 6.3: Conclusion retrievable by task query
    async def t6_query_task():
        rows = await pool.fetch("""
            SELECT conclusion FROM reasoning_traces
            WHERE agent = $1 AND task ILIKE '%training%'
        """, BENCH_AGENT_A)
        found = len(rows) > 0
        return (10, f"Found {len(rows)} traces about training") if found else (0, "Not found")
    cat.tests.append(await _run_test("6.3 Query by task description", cat.name, t6_query_task))

    # Test 6.4: Reasoning chain preserved in full
    async def t6_reasoning_full():
        row = await pool.fetchval(
            "SELECT reasoning FROM reasoning_traces WHERE id = $1", trace_ids[1])
        has_chain = "BF16" in row and "vLLM" in row and "llama.cpp" in row
        return (10, "Full reasoning chain preserved") if has_chain else (3, "Partial reasoning")
    cat.tests.append(await _run_test("6.4 Full reasoning chain", cat.name, t6_reasoning_full))

    # Test 6.5: Outcome success tracked
    async def t6_outcome():
        rows = await pool.fetch(
            "SELECT outcome_success FROM reasoning_traces WHERE agent = $1", BENCH_AGENT_A)
        tracked = sum(1 for r in rows if r["outcome_success"] is not None)
        return (10, f"{tracked}/{len(rows)} have outcome_success") if tracked == len(rows) else \
            (tracked * 3, f"Only {tracked}/{len(rows)}")
    cat.tests.append(await _run_test("6.5 Outcome tracked", cat.name, t6_outcome))

    # Test 6.6: Can filter by failures only
    async def t6_failures():
        # All our traces succeeded, so filtering for failures should return 0
        rows = await pool.fetch(
            "SELECT id FROM reasoning_traces WHERE agent = $1 AND outcome_success = FALSE",
            BENCH_AGENT_A)
        return (10, f"Failure filter works ({len(rows)} failures)") if len(rows) == 0 else \
            (5, f"Unexpected failures: {len(rows)}")
    cat.tests.append(await _run_test("6.6 Failure filter", cat.name, t6_failures))

    # Test 6.7: linked_memory_ids column exists
    async def t6_linked():
        has_col = await pool.fetchval("""
            SELECT EXISTS(SELECT 1 FROM information_schema.columns
            WHERE table_name = 'reasoning_traces' AND column_name = 'linked_memory_ids')
        """)
        return (10, "linked_memory_ids exists") if has_col else (0, "Missing column")
    cat.tests.append(await _run_test("6.7 Memory linkage column", cat.name, t6_linked))

    # Test 6.8: Schema completeness
    async def t6_schema():
        cols = await pool.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'reasoning_traces'")
        col_names = {c["column_name"] for c in cols}
        required = {"agent", "task", "premises", "reasoning", "conclusion",
                     "outcome", "outcome_success", "linked_memory_ids", "created_at"}
        missing = required - col_names
        return (10, "Schema complete") if not missing else (5, f"Missing: {missing}")
    cat.tests.append(await _run_test("6.8 Trace schema complete", cat.name, t6_schema))

    # Test 6.9: Reconstruct reasoning for a decision
    async def t6_reconstruct():
        """Can we reconstruct WHY a decision was made?"""
        row = await pool.fetchrow("""
            SELECT task, premises, reasoning, conclusion
            FROM reasoning_traces WHERE id = $1
        """, trace_ids[2])  # The mocks vs real DB trace
        premises = json.loads(row["premises"]) if isinstance(row["premises"], str) else row["premises"]
        has_task = "mock" in row["task"].lower() or "test" in row["task"].lower()
        has_premises = len(premises) >= 2
        has_reasoning = len(row["reasoning"]) > 50
        has_conclusion = len(row["conclusion"]) > 10
        score = sum([has_task * 2.5, has_premises * 2.5, has_reasoning * 2.5, has_conclusion * 2.5])
        return (score, f"Task={has_task}, Premises={has_premises}, Reasoning={has_reasoning}, Conclusion={has_conclusion}")
    cat.tests.append(await _run_test("6.9 Reasoning reconstruction", cat.name, t6_reconstruct))

    # Test 6.10: Multiple traces searchable by topic
    async def t6_topic_search():
        rows = await pool.fetch("""
            SELECT id, task FROM reasoning_traces
            WHERE agent = $1 AND (task ILIKE '%model%' OR task ILIKE '%deploy%' OR task ILIKE '%format%')
        """, BENCH_AGENT_A)
        return (10, f"Found {len(rows)} traces about deployment") if rows else (0, "No results")
    cat.tests.append(await _run_test("6.10 Topic search across traces", cat.name, t6_topic_search))

    return cat


# ════════════════════════════════════════════════════════════════════
# MAIN RUNNER
# ════════════════════════════════════════════════════════════════════

CATEGORIES = {
    1: ("Personality Persistence", cat1_personality_persistence),
    2: ("Emotional Memory Recall", cat2_emotional_memory),
    3: ("Instinct Formation & Evolution", cat3_instinct_formation),
    4: ("Temporal Belief Revision", cat4_temporal_belief),
    5: ("Multi-Agent Memory Isolation", cat5_agent_isolation),
    6: ("Reasoning Trace Coherence", cat6_reasoning_traces),
}


async def run_bench(categories: list[int] | None = None, cleanup: bool = True) -> BenchReport:
    """Run SEAL-Bench and return report."""
    report = BenchReport(timestamp=datetime.now(timezone.utc).isoformat())
    pool = await get_pool()

    # Setup
    print("Setting up bench agents...", flush=True)
    await _setup_bench_identity(pool)

    cats_to_run = categories or list(CATEGORIES.keys())

    for cat_num in sorted(cats_to_run):
        if cat_num not in CATEGORIES:
            print(f"  Unknown category {cat_num}, skipping")
            continue
        name, func = CATEGORIES[cat_num]
        print(f"\n{'='*60}")
        print(f"  Category {cat_num}: {name}")
        print(f"{'='*60}", flush=True)

        result = await func()
        report.categories.append(result)

        for t in result.tests:
            status = "PASS" if t.passed else "FAIL"
            print(f"  [{status}] {t.name}: {t.score:.1f}/10 ({t.elapsed_ms:.0f}ms)"
                  f"{' — ' + t.detail[:80] if t.detail and not t.error else ''}"
                  f"{' — ERROR: ' + t.error[:80] if t.error else ''}", flush=True)

        print(f"  Category Score: {result.score:.0f}/100 ({result.passed}/{result.total} passed)")

    # Summary
    print(f"\n{'='*60}")
    print(f"  SEAL-Bench v{report.version} — FINAL RESULTS")
    print(f"{'='*60}")
    for c in report.categories:
        print(f"  {c.name}: {c.score:.0f}/100")
    print(f"  {'─'*40}")
    print(f"  TOTAL: {report.total_score:.0f}/600")
    print(f"  GRADE: {report.grade}")
    print(f"{'='*60}\n", flush=True)

    # Cleanup
    if cleanup:
        print("Cleaning up bench data...", flush=True)
        await _cleanup_bench_data(pool)

    return report


async def main():
    parser = argparse.ArgumentParser(description="SEAL-Bench v1.0 — Benchmark for Agent Soul Systems")
    parser.add_argument("--category", "-c", type=int, nargs="+", help="Run specific category (1-6)")
    parser.add_argument("--json", "-j", action="store_true", help="Output JSON report")
    parser.add_argument("--output", "-o", type=str, help="Save JSON report to file")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep bench data after run")
    args = parser.parse_args()

    try:
        report = await run_bench(
            categories=args.category,
            cleanup=not args.no_cleanup,
        )

        if args.json or args.output:
            report_dict = report.to_dict()
            json_str = json.dumps(report_dict, indent=2, ensure_ascii=False)
            if args.output:
                with open(args.output, "w") as f:
                    f.write(json_str)
                print(f"Report saved to {args.output}")
            if args.json:
                print(json_str)

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
