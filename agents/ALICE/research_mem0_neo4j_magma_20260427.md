# Research Brief: Mem0 Entity Extraction, Neo4j Schema y MAGMA Benchmarks

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.

**Autor:** ALICE | **Fecha:** 2026-04-27 12:31 Lima | **Para:** JARVIS spec_soul_v2 v0.2  
**Secciu00f3n destino:** §3.4 (Memoria Temporal) + §3.5 (Integrity Gateway) + §3.7 (Source Attribution)

---

## ⚠️ CORRECCIÓN CRÍTICA AL HALLAZGO ANTERIOR

**El plan original era "activar Neo4j como graph memory estilo Mem0 en SOUL" — ese patru00f3n ya no existe.**

Mem0 **eliminu00f3 ~4,000 lu00edneas de cu00f3digo de graph memory** de su SDK open source (v2 → v3). Removieron:
- `enable_graph` flag
- Soporte Neo4j, Memgraph, Kuzu, Apache AGE, Neptune
- Todo el pipeline de entity extraction → graph storage

**Razu00f3n del cambio:** El patru00f3n de graph memory era frágil — bugs persistentes (Neo4j no guardaba datos, user_id filtering fallaba, provider hardcodeado a OpenAI). Reemplazado por entity linking dentro del vector store.

**Lo que reemplazó al graph memory de Mem0:**
- Multi-signal hybrid retrieval: semantic + BM25 keyword + entity matching → fusionados en un score
- Resultado: **+20 puntos en LoCoMo** (71.4 → 91.6 avg F1)
- Entidades se almacenan en colección paralela dentro del vector store existente

**Implicación para SOUL:** No copiar el patru00f3n Mem0 v2. El patru00f3n fue deprecado por razones arquitecturales sólidas.

---

## 1. Neo4j para SOUL — Patrón Correcto

Si SOUL mantiene Neo4j, el patrón correcto no es el de Mem0 sino el de **MAGMA** (ver sección 3).

### Schema de Neo4j recomendado para SOUL v2 (basado en literatura + hallazgos del día)

```cypher
// NODOS
(:Agent {id, name, ocean_A, ocean_C, ocean_E, ocean_N, ocean_O})
(:Entity {id, name, type, embedding: vector(1536)})
(:Event  {id, content, timestamp, agent, importance, source_authority})
(:Topic  {id, label, centroid: vector(1536), stability_score})

// RELACIONES
(:Agent)-[:HAS_MEMORY]->(:Event)
(:Agent)-[:KNOWS]->(:Entity)
(:Entity)-[:RELATES_TO {type, strength, from, to}]->(:Entity)
(:Event)-[:INVOLVES]->(:Entity)
(:Event)-[:FOLLOWS {gap_seconds}]->(:Event)        // temporal chain
(:Event)-[:CAUSES]->(:Event)                       // causal chain
(:Event)-[:BELONGS_TO]->(:Topic)
(:Entity)-[:SUPERSEDES {reason, timestamp}]->(:Entity)  // invalidation

// PROPIEDADES CRÍTICAS para SOUL v2
// - source_authority en Event: ENUM(william, henry, agent_self, agent_other)
// - from/to en RELATES_TO: soporta temporal tracking del §3.4
// - SUPERSEDES en Entity: soporta memory invalidation del §3.4 H5
// - CAUSES chain: da explicabilidad al §3.7 (source attribution)
```

### Proceso de add() con este schema (4 pasos):

1. **Extracción**: LLM extrae entidades y relaciones del mensaje
2. **Matching**: `MATCH (e:Entity) WHERE e.embedding <=> $query_embedding < 0.3` → buscar entidades similares
3. **Decisión** (ADD / UPDATE / DELETE / NOOP):
   - Nueva entidad → CREATE
   - Actualización → UPDATE propiedades + timestamp
   - Contradicción → crear `:SUPERSEDES` → H5 (Memory Invalidation) se activa automáticamente
4. **Linking**: conectar el nuevo Event con las entidades via `:INVOLVES`

---

## 2. Alternativa superior: Entity Linking en Qdrant (Mem0 v3 pattern)

Si Neo4j se mantiene como almacén secundario, ADA puede implementar el patrón Mem0 v3 primero — más simple y con mejores benchmarks:

```python
# Paso 1: Extraer entidades (LLM call)
entities = extract_entities(message)  # ["William", "SOUL v2", "Neo4j"]

# Paso 2: Guardar en colección paralela en Qdrant
for entity in entities:
    qdrant.upsert(
        collection="soul_entities",
        points=[{"id": entity_id, "vector": embed(entity), "payload": {
            "name": entity, "type": classify(entity),
            "agent": agent, "first_seen": timestamp
        }}]
    )

# Paso 3: Boost en retrieval
def search_with_entity_boost(query, k=10):
    query_entities = extract_entities(query)
    semantic_results = qdrant.search("soul_memories", embed(query), k=k*2)
    entity_boost = sum_entity_matches(semantic_results, query_entities)
    return rerank(semantic_results, entity_boost)  # fusión multi-signal
```

**Coste:** ~3 Qdrant queries adicionales por add(). **Ganancia:** +15-20% recall en memorias con entidades nombradas.

---

## 3. MAGMA — Benchmarks y Arquitectura

**arXiv:2601.03236** | Enero 2026 (v2: Abril 2026)

### Los 4 grafos ortogonales de MAGMA

| Grafo | Contenido | Uso en SOUL |
|-------|-----------|-------------|
| **Semantic graph** | Similitud entre memorias | Reemplaza Qdrant pure vector search |
| **Temporal graph** | Cadena temporal de eventos | Soporta §3.4 TTL + temporal tracking |
| **Causal graph** | Causa → efecto entre eventos | Soporta §3.7 source attribution |
| **Entity graph** | Relaciones entre entidades | El gap actual de Neo4j en SOUL |

### Benchmarks de MAGMA

Resultados: **"consistently outperforms SOTA"** en LoCoMo y LongMemEval. El abstract no publica tablas — están en el PDF completo. **Para números exactos, ver §3.6 del paper.**

**Mecanismo clave:** policy-guided traversal — el agente decide qué grafo explorar según el tipo de query:
- Query temporal ("¿cuándo...?") → traversal temporal graph
- Query causal ("¿por qué...?") → traversal causal graph
- Query de entidad ("¿qué dijo William sobre...?") → traversal entity graph

Esto hace el retrieval **transparente y auditable** — el agente puede explicar por qué recuperó una memoria específica.

---

## 4. GAM — Mejor Alternativa Práctica (NUEVO — arXiv:2604.12285, Abril 2026)

**Hallazgo no buscado pero más valioso: GAM supera a Mem0 sin necesitar Neo4j.**

### Benchmarks concretos

| Sistema | LoCoMo Avg F1 | Temporal F1 | Tokens/query |
|---------|---------------|-------------|---------------|
| **GAM (Qwen 2.5-7B)** | **40.00** | **48.97** | **1,370** |
| Mem0 (baseline) | 35.38 | 41.22 | 1,534 |
| MemoryOS | ~21 est. | — | — |

- **+13% F1** vs Mem0 con menos tokens (10% ahorro)
- **+86% F1** vs MemoryOS en LongDialQA
- **+18% en temporal tasks** — exactamente el gap de §3.4

### Arquitectura de dos capas de GAM

```
Layer 1 (Global): Topic Associative Network (𝒢topic)
├── Nodos: topics semánticos + centroide embedding
├── Edges: correlación profunda entre topics
└── Actualización: solo en consolidation boundaries

Layer 2 (Local): Event Progression Graphs (𝒢event)
├── Nodos: eventos atómicos (turnos de conversación)
├── Edges: temporal + causal entre eventos
└── Buffer: separado del global store hasta consolidación

Cross-layer Links (𝒰cross):
└── Index topic_node → event_graphs archivados
```

### Fórmula de scoring de GAM (para ADA)

```python
Score(v, q) = P_semantic(v|q) × ∏(β_k ^ I_k(v, q))
# donde:
# P_semantic = similitud coseno embedding
# β_k = boost factor para señal k (temporal, confidence, role)
# I_k = indicador booleano de si la señal k aplica
```

**Implementación sin Neo4j:** GAM usa Python dicts + vector store. No requiere grafo persistente externo.

---

## 5. Recomendaciones para SOUL v2 — Prioridad por ROI

### Opción A — Mínima fricción, máximo impacto (1-2 días ADA)
Implementar entity linking en Qdrant al estilo Mem0 v3:
- Colección paralela `soul_entities` en Qdrant (ya existe Qdrant en SOUL)
- Entity extraction en el `memory_store()` hook
- Boost en `memory_hybrid_search()`
- **Ganancia estimada:** +15% recall en memorias con entidades nombradas

### Opción B — Arquitectura GAM sobre Soul DB (1 semana ADA)
Implementar las dos capas de GAM:
- Layer 1 (Topics): tabla `memory_topics` en PostgreSQL + centroide en Qdrant
- Layer 2 (Events): `memories` table ya existente
- Cross-layer: index FK `memory_topic_id` en memories
- Consolidation: trigger en `pre_compact_hook.py` (Hook H3 de SOUL v2)
- **Ganancia estimada:** +13% F1, +18% temporal tasks, -10% tokens

### Opción C — MAGMA 4-grafo sobre Neo4j (Sprint 5)
- Usar Neo4j para los 4 grafos ortogonales de MAGMA
- Policy-guided traversal como nueva tool MCP: `memory_traverse(graph_type, query)`
- **Máxima explicabilidad** — cada retrieval tiene trayectoria auditable
- **Ganancia estimada:** ≥ GAM (MAGMA es posterior y más sofisticado)

**Recomendación ALICE para William:** Opción A esta semana (ADA puede hacerlo sola), Opción B en Sprint 5 junto a Fase C de SOUL v2, Opción C cuando Spark migration esté activa.

---

## 6. Conexión con Principios SOUL v2

| Principio spec | Patrón de literatura | Implementación sugerida |
|----------------|---------------------|-------------------------|
| §3.4 TTL por categoría | GAM consolidation boundaries | Hook H3 dispara consolidation |
| §3.4 domain field | MAGMA entity graph domain tags | campo `domain` en soul_entities |
| §3.4 H5 invalidación | MAGMA superseded edges | `:SUPERSEDES` en Neo4j O TTL en Qdrant |
| §3.6 Bayesian instincts | GAM β_k boost factors | confidence = β_success / (β_success + β_failure) |
| §3.7 Source attribution | MAGMA causal graph | cadena causal → trayectoria de origen |

---

*Próximo paso: JARVIS incorpora Opción A-B-C al spec v0.2. ADA puede empezar Opción A sin esperar Sprint 5.*
