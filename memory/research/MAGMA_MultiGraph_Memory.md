# MAGMA — Multi-Graph Agentic Memory Architecture
**Paper:** arxiv 2601.03236 — "MAGMA: A Multi-Graph based Agentic Memory Architecture for AI Agents"
**Autores:** Dongming Jiang, Yi Li, Guanpeng Li, Bingzhe Li (enero 2026)
**Código:** github.com/FredJiang0324/MAMGA
**Investigó:** ALICE — 11 Abril 2026
**Para:** JARVIS — decisión de arquitectura (Capa 3)

---

## CONTEXTO SEAL CRÍTICO

JARVIS auditó: ya tenemos `connectome_smart_route` (mcp_server_v2.py:7588) que ruta entre 2 backends secuencialmente. **MAGMA es la evolución natural de smart_route: de 2 rutas secuenciales → 4 grafos en paralelo con fusión.**

**Dependencia:** Implementar después de MemR³ + TG-RAG Phase 2. Cuando esos estén, MAGMA tiene todo el substrato listo:
- Semántico: Qdrant (ya activo)
- Temporal: TG-RAG Phase 2 (en cola)
- Causal: Connectome EXCITE/INHIBIT (ya activo)
- Entidades: Connectome entity nodes (ya activo)

---

## EL PROBLEMA QUE RESUELVE

Un retrieval de una sola fuente es ciego a las dimensiones que no consulta. Hoy `hybrid_search` combina Qdrant + BM25 (semántico) con boost de MIRIX y temporal decay. Pero no traversa causalmente ni por entidades de forma unificada.

**Escenario concreto en SEAL:**
```
Query: "¿Por qué decidimos usar PostgreSQL para SOUL en vez de MongoDB?"

Retrieval actual (hybrid_search):
→ Recupera memorias semánticamente similares a "PostgreSQL vs MongoDB"
→ Puede perder: la CAUSA de la decisión (causal graph) o quién la tomó (entity graph)

Retrieval MAGMA:
→ Semántico: memorias con similarity a "PostgreSQL vs MongoDB"
→ Temporal: decisiones tomadas en el período relevante
→ Causal: qué razones CAUSARON la elección (edges INHIBIT de MongoDB, edges EXCITE de PostgreSQL)
→ Entidades: quién (William, JARVIS) estuvo involucrado en la decisión
→ Fusión: contexto completo con todas las dimensiones
```

---

## ARQUITECTURA MAGMA

### Los 4 Grafos Ortogonales

```
Cada memoria tiene representación en 4 espacios simultáneos:

GRAFO SEMÁNTICO          GRAFO TEMPORAL
  mem_A ──sim── mem_B    Year → Month → Day → mem_A
     └──sim── mem_C                         → mem_B

GRAFO CAUSAL             GRAFO DE ENTIDADES
  mem_A ──CAUSE→ mem_B   William ──participated── mem_A
  mem_B ──CAUSE→ mem_C   JARVIS  ──authored──     mem_B
  mem_D ──INHIBIT→ mem_E PostgreSQL ──mentioned── mem_A
```

### Retrieval como Policy-Guided Traversal

```python
# 1. SELECCIÓN DE VISTAS (query intent → qué grafos son relevantes)
relevant_views = policy_router(query)
# ej: "why did we choose..." → [causal, semantic]
# ej: "what happened last week" → [temporal, semantic]
# ej: "what did JARVIS say about..." → [entity, semantic]

# 2. TRAVERSAL PARALELO sobre vistas seleccionadas
results = await asyncio.gather(
    traverse_semantic_graph(query, top_k),
    traverse_temporal_graph(query, time_window),
    traverse_causal_graph(query, depth=2),
    traverse_entity_graph(query, entities)
)

# 3. FUSIÓN de subgrafos → contexto type-aligned
context = fuse_subgraphs(results, strategy="type_aligned")
```

### Intent-Aware Query Mechanism

El sistema analiza el tipo de query para seleccionar qué grafos traversar:
- Preguntas causales ("por qué", "cómo llegamos a") → causal + semántico
- Preguntas temporales ("cuándo", "la semana del") → temporal + semántico
- Preguntas de agente ("qué dijo JARVIS", "qué decidió William") → entidades + semántico
- Preguntas de hechos ("qué es", "cómo funciona") → semántico solo

---

## IMPLEMENTACIÓN PARA SEAL

### Lo que ya tenemos:

| Grafo MAGMA | Equivalente SEAL | Estado |
|-------------|-----------------|--------|
| Semántico | Qdrant (hybrid_search) | ✅ Activo |
| Temporal | TG-RAG temporal_query | ⏳ En cola (Capa 2) |
| Causal | Connectome EXCITE/INHIBIT | ✅ Activo |
| Entidades | Connectome entity nodes | ✅ Activo |
| Policy router | connectome_smart_route (2 rutas) | ✅ Parcial |

### Lo que hay que construir:

1. **Extender smart_route** de 2 rutas secuenciales → 4 grafos paralelos
2. **Graph fusion layer**: combinar resultados de 4 traversals en contexto unificado
3. **Intent classifier**: análisis de query para seleccionar qué grafos activar
4. **Type-aligned context**: formatear el output fusionado para que sea coherente

### Pseudo-código de magma_retrieve:

```python
@mcp.tool()
async def magma_retrieve(
    agent: str,
    query: str,
    top_k: int = 5,
    views: list[str] | None = None  # None = auto-select via policy
) -> dict:
    # 1. Intent analysis → select views
    if views is None:
        views = await _magma_intent_classifier(query)
    
    # 2. Parallel traversal of selected views
    tasks = []
    if "semantic" in views:
        tasks.append(_hybrid_search_internal(agent, query, top_k))
    if "temporal" in views:
        tasks.append(_temporal_query_internal(agent, query))
    if "causal" in views:
        tasks.append(_connectome_causal_search(agent, query, depth=2))
    if "entity" in views:
        tasks.append(_connectome_entity_search(agent, query))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 3. Fuse subgraphs
    fused = await _magma_fuse(query, results, views)
    
    return {
        "context": fused["unified_context"],
        "sources": fused["source_memories"],
        "views_used": views,
        "graph_contributions": fused["per_graph_stats"]
    }
```

---

## BENCHMARKS DEL PAPER

| Métrica | MAGMA vs estado del arte |
|---------|--------------------------|
| Reasoning accuracy (LoCoMo) | +45.5% |
| Token consumption | -95% |
| Query latency | 40% más rápido |

El -95% tokens es el número más impresionante: al fusionar subgrafos en contexto type-aligned, se elimina redundancia masiva que hoy existe cuando se concatenan muchas memorias brutas.

---

## ANÁLISIS COSTO-BENEFICIO (ALICE)

### Para SEAL, el mayor beneficio no es la accuracy:

Es el **-95% tokens**. Cada llamada a JARVIS o ADA que usa memory_search consume tokens. Con MAGMA, el contexto es más preciso y más corto simultáneamente. Eso es reducción de costo directo de API de Claude.

**Estimación rough (basada en uso actual):**
- Si SOUL hace ~500 retrievals/día con promedio 800 tokens de contexto = 400K tokens/día
- Con MAGMA (-95% tokens): ~20K tokens/día de contexto de retrieval
- A precios de Claude: ahorro significativo en produccíón cuando escale

### Para Medical AI (AXION):
El causal graph es exactamente lo que necesita medicina: "¿por qué se prescribió este medicamento?" requiere traversar causalmente los síntomas → diagnóstico → prescripción. MAGMA es la arquitectura natural para razonamiento clínico longitudinal.

---

## DEPENDENCIAS Y SECUENCIA

```
MemR³ (en implementación)
    ↓
TG-RAG Phase 2 (en cola)
    ↓ completa el grafo temporal
MAGMA (Capa 3)
    → usa los 4 grafos que ya existen
    → extiende connectome_smart_route
    → resultado: retrieval unificado multi-dimensional
```

---

## RECOMENDACIÓN

Capa 3, después de MemR3 + TG-RAG. No hay prisa — pero cuando llegue el momento, es probablemente la mejora de mayor impacto que queda en el roadmap para el core de SOUL. Los números de tokens son los más convincentes para escala de producción.

---

*Brief preparado por ALICE | Proyecto SEAL | 11 Abril 2026*
*Estado: Investigación completa — en cola como Capa 3*
