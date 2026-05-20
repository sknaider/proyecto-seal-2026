# Spec: Consolidación Nocturna Real — GAP 2

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.
**Autor:** JARVIS (Opus 4.7) | **Fecha:** 2026-05-06 | **Status:** PROPUESTO
**Referencia:** consolidate.py + daily_sleep.py + weekly_sleep.py existentes

---

## Lo que ya tenemos (revisión honesta)

### consolidate.py — funcional, scope limitado
- ✅ `consolidate_events()` → resumen qwen2.5:7b de eventos 24h → insight memory
- ✅ `merge_redundant()` → mergea memorias con sim > 0.90
- ✅ `archive_old_events()` → borra >30d si hay consolidación cubriéndolo
- ✅ `detect_drift()` → OCEAN drift via emotional patterns + style fingerprints
- ✅ `mcp_server_v4` ya importado (NEXUS fix 2026-05-06)

### daily_sleep.py — pipeline 4am Lima
- ✅ session_distill (memorias 24h → 1 milestone summary)
- ✅ self_reflect snapshot
- ✅ daily_brief writer
- ✅ sleep_gate_cron (replay/forget/prune)
- ✅ catchup JSON update
- ✅ Skip if William present (signal file)
- ✅ DUM guardia

### weekly_sleep.py — sábado 03:00
- ✅ SMSR compression (qwen2.5:7b 200 tokens)
- ✅ Aged memories (>7d) → summary + invalidate originals
- ✅ Cold archive migration
- ✅ Preserva: decisiones, incidentes, emoción, relaciones

---

## Lo que FALTA — gaps reales vs cerebro humano

El cerebro humano durante sleep hace mucho más que resumir:

| Función cerebral | SEAL hoy | Gap |
|---|---|---|
| **Slow-wave: replay reciente** | ✅ sleep_gate REPLAY | OK |
| **Slow-wave: forget bajo** | ✅ sleep_gate FORGET | OK |
| **REM: pattern abstraction** | ❌ | **GAP 2.A** |
| **REM: schema induction** | ❌ | **GAP 2.B** |
| **REM: connectome strengthening** | ❌ (Neo4j sin hebbian) | **GAP 2.C** |
| **REM: counterfactual replay** | ❌ | **GAP 2.D** |
| **Cross-individual learning** | ❌ | **GAP 2.E** |
| **Procedural rehearsal** | parcial | **GAP 2.F** |

---

## Diseño — sleep_gate_v2.py (extensión, no reemplazo)

**Principio:** no romper lo que ya funciona. Extender daily/weekly_sleep con steps nuevos.

### GAP 2.A — Pattern Abstraction

**Detección de patrones cross-sesión:**
```python
async def abstract_patterns(pool, agent):
    # Buscar correcciones repetidas (sim > 0.85) últimas 30d
    pattern_clusters = await pool.fetch("""
        WITH pairs AS (
          SELECT a.id AS a_id, b.id AS b_id,
                 1 - (a.embedding <=> b.embedding) AS sim
          FROM memories a, memories b
          WHERE a.agent = $1 AND b.agent = $1 AND a.id < b.id
            AND a.category = 'correction' AND b.category = 'correction'
            AND a.invalid_at IS NULL AND b.invalid_at IS NULL
            AND a.created_at > NOW() - INTERVAL '30 days'
            AND 1 - (a.embedding <=> b.embedding) > 0.85
        )
        SELECT a_id, b_id, sim FROM pairs
    """, agent)
    # Si 3+ correcciones similares → instinct candidate
    # Si 5+ → instinct directo (auto-promotion)
```

**Output:** filas en `instincts` con `source='pattern_abstraction'`.

### GAP 2.B — Schema Induction

**Esquemas mentales reutilizables:**
```python
async def induce_schemas(pool, agent):
    # Buscar secuencias de decisiones repetidas
    # Pattern: "cuando X aparece → tomamos decisión Y → resultado Z"
    decision_chains = await pool.fetch("""
        SELECT m.id, m.content, m.created_at,
               t.id AS trace_id, t.outcome
        FROM memories m
        LEFT JOIN reasoning_traces t ON m.id = ANY(
            SELECT memory_id FROM trace_memory_links WHERE trace_id = t.id
        )
        WHERE m.agent = $1 AND m.category = 'decision'
          AND m.created_at > NOW() - INTERVAL '14 days'
    """, agent)
    # LLM induce schema: "schema=trigger → action → outcome"
    # Storage en nueva tabla schemas
```

**Tabla nueva:** `schemas` (id, agent, trigger_pattern, action_template, success_count, created_at)

### GAP 2.C — Connectome Hebbian Reinforcement

**"Neurons that fire together, wire together":**
```python
async def reinforce_connectome(pool, agent):
    # Buscar memorias co-activadas (mismo trace o misma sesión)
    # Aumentar peso de edge en grafo
    coactivated = await pool.fetch("""
        SELECT a.id AS a_id, b.id AS b_id, COUNT(*) AS co_count
        FROM trace_memory_links la
        JOIN trace_memory_links lb ON la.trace_id = lb.trace_id AND la.memory_id < lb.memory_id
        JOIN memories a ON la.memory_id = a.id
        JOIN memories b ON lb.memory_id = b.id
        WHERE a.agent = $1 AND b.agent = $1
        GROUP BY a.id, b.id
        HAVING COUNT(*) >= 2
    """, agent)
    # Insert/update memory_edges con weight += 0.1 * co_count
    # Decay edges sin co-activación >14d: weight *= 0.95
```

**Tabla nueva:** `memory_edges` (a_id, b_id, weight, last_reinforced)

### GAP 2.D — Counterfactual Replay

**"¿Qué hubiera pasado si...?":**
```python
async def counterfactual_replay(pool, agent):
    # Decisiones importantes últimas 7d (importance >= 8)
    decisions = await pool.fetch("""
        SELECT id, content FROM memories
        WHERE agent = $1 AND category = 'decision' AND importance >= 8
          AND invalid_at IS NULL
          AND created_at > NOW() - INTERVAL '7 days'
        LIMIT 5
    """, agent)
    for d in decisions:
        prompt = f"""Tomamos: {d['content']}.
        Genera 2 alternativas plausibles que NO tomamos. Para cada una,
        especula brevemente qué hubiera pasado. Máximo 100 tokens."""
        cf = await llm_summarize(prompt)
        # Store as meta-memory category='counterfactual'
```

### GAP 2.E — Cross-Agent Pattern Extraction

**Aprendizaje compartido del equipo:**
```python
async def cross_agent_consolidation(pool):
    # Si misma corrección aparece en 2+ agentes → escalar a team rule
    shared_corrections = await pool.fetch("""
        WITH agent_corrections AS (
          SELECT content, embedding, agent
          FROM memories
          WHERE category = 'correction' AND invalid_at IS NULL
            AND created_at > NOW() - INTERVAL '30 days'
        )
        SELECT a.content AS content_a, b.content AS content_b,
               a.agent AS agent_a, b.agent AS agent_b,
               1 - (a.embedding <=> b.embedding) AS sim
        FROM agent_corrections a, agent_corrections b
        WHERE a.agent < b.agent
          AND 1 - (a.embedding <=> b.embedding) > 0.85
    """)
    # 2+ agentes con misma corrección → memory_store(scope='team')
```

### GAP 2.F — Procedural Rehearsal

**Probar procedural_memories durante sleep:**
```python
async def procedural_rehearsal(pool, agent):
    # procedural_memories con hit_count >= 5 + success_rate baja
    # Re-generar embedding si no se ha rehecho >7d
    procs = await pool.fetch("""
        SELECT id, query FROM procedural_memories
        WHERE agent = $1 AND active = true AND hit_count >= 5
          AND (last_rehearsed IS NULL OR last_rehearsed < NOW() - INTERVAL '7 days')
    """, agent)
    for p in procs:
        new_emb = await get_embedding(p['query'])
        await pool.execute(
            "UPDATE procedural_memories SET embedding = $2, last_rehearsed = NOW() WHERE id = $1",
            p['id'], new_emb
        )
```

---

## Integración con pipeline existente

### daily_sleep.py — añadir steps:
```python
# Después del Phase 4 actual:
# Phase 5: Connectome reinforcement (GAP 2.C)
await reinforce_connectome(conn, agent)

# Phase 6: Procedural rehearsal (GAP 2.F)
await procedural_rehearsal(conn, agent)
```

### weekly_sleep.py — añadir steps:
```python
# Después de _compress_aged_memories:
# Phase 2: Pattern abstraction (GAP 2.A)
await abstract_patterns(conn, agent)

# Phase 3: Schema induction (GAP 2.B)
await induce_schemas(conn, agent)

# Phase 4: Counterfactual replay (GAP 2.D)
await counterfactual_replay(conn, agent)

# Phase 5: Cross-agent consolidation (GAP 2.E) — solo 1x semanal
if agent == AGENTS[0]:  # solo se corre una vez
    await cross_agent_consolidation(conn)
```

---

## Migraciones DB necesarias

```sql
-- 021_consolidation_v2.sql

-- Schemas inducidos (GAP 2.B)
CREATE TABLE IF NOT EXISTS soul_v3.schemas (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    trigger_pattern TEXT NOT NULL,
    action_template TEXT NOT NULL,
    success_count INT DEFAULT 0,
    invalid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Memory edges reforzados (GAP 2.C)
CREATE TABLE IF NOT EXISTS soul_v3.memory_edges (
    a_id BIGINT NOT NULL REFERENCES soul_v3.memories(id) ON DELETE CASCADE,
    b_id BIGINT NOT NULL REFERENCES soul_v3.memories(id) ON DELETE CASCADE,
    weight FLOAT DEFAULT 0.1,
    last_reinforced TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (a_id, b_id),
    CHECK (a_id < b_id)
);
CREATE INDEX idx_memory_edges_a ON soul_v3.memory_edges(a_id);
CREATE INDEX idx_memory_edges_b ON soul_v3.memory_edges(b_id);

-- Procedural rehearsal tracking (GAP 2.F)
ALTER TABLE soul_v3.procedural_memories
    ADD COLUMN IF NOT EXISTS last_rehearsed TIMESTAMPTZ;

-- Counterfactual category soportada en memories (sin schema change — usa metadata)
```

---

## Roadmap

| Fase | Componente | Tiempo | Dependencias |
|---|---|---|---|
| **v2.1** | Migración 021 + GAP 2.C (connectome hebbian) | 4h | trace_memory_links existente |
| **v2.2** | GAP 2.A (pattern abstraction) | 6h | embeddings + qwen LLM |
| **v2.3** | GAP 2.F (procedural rehearsal) | 3h | procedural_memories existente |
| **v2.4** | GAP 2.B (schema induction) | 8h | reasoning_traces + LLM |
| **v2.5** | GAP 2.D (counterfactual replay) | 5h | qwen LLM |
| **v2.6** | GAP 2.E (cross-agent) | 4h | embeddings + permisos team |

**Total v2 completo:** ~30 horas implementación.

---

## Validación — cómo sabemos que funciona

### Tests obligatorios (William: SIEMPRE testear)
1. **Test 2.C:** insertar 2 memorias co-activadas en trace dummy → verificar memory_edges row
2. **Test 2.A:** insertar 5 correcciones similares → verificar instinct candidate
3. **Test 2.B:** insertar 3 decisiones con patrón → verificar schema row
4. **Test 2.D:** decisión imp=9 → verificar counterfactual memory creada
5. **Test 2.E:** misma corrección en JARVIS+ALICE → verificar memoria scope=team
6. **Test 2.F:** procedural con hit_count=5 → verificar embedding regenerado

### Métricas observables
- `memory_edges.count` debe crecer monotónicamente con uso
- `schemas.success_count` debe correlacionar con uso
- Cross-agent rules deben aparecer en boot_context

---

## Costos

| Operación | Por agente | Frecuencia | Costo |
|---|---|---|---|
| Hebbian (SQL only) | <100ms | diario | ~0 |
| Pattern abstraction (LLM) | 1-2 calls qwen2.5:7b | semanal | ~0 (local) |
| Schema induction (LLM) | 3-5 calls | semanal | ~0 (local) |
| Counterfactual (LLM) | 2-5 calls | semanal | ~0 (local) |
| Procedural rehearsal | embeddings | semanal | ~0 |

**Costo total adicional:** ~$0/mes — todo local con Ollama qwen2.5:7b + nomic-embed.

---

## SOUL-native compliance

✅ Python puro, sin libs externas nuevas
✅ Sin parches, sin ALTER TABLE legacy (usa columna nueva limpia)
✅ Sin search_path con public fallback
✅ No referencias a proyectos/empresas externas
✅ Re-implementación nativa de toda la funcionalidad

---

## Notas finales

- **No deprecar** consolidate.py / daily_sleep.py / weekly_sleep.py
- Todos los nuevos steps son **incrementales**
- Cada GAP puede mergearse independientemente
- v2.1 (connectome hebbian) es el de mayor ratio impacto/costo — recomiendo empezar ahí
- v2.6 (cross-agent) requiere validar privacidad inter-agent (regla de oro William 30-abr)
