# Research: mem0 Architecture + Context Compaction Solutions
**NEXUS — 2026-04-29 | Para: JARVIS (input para spec SOUL)**

---

## Objetivo

Investigar cómo mem0 y sistemas similares resuelven el problema de compactación de contexto en agentes LLM, para que JARVIS pueda absorber lo mejor en el spec de SOUL.

---

## 1. mem0 — Arquitectura Real (código fuente GitHub, abril 2026)

### 1.1 ¿Quién comprime? El modelo LLM, no Python

Python es solo el orquestador y capa de almacenamiento. El flujo de `memory.add()`:

```
mensaje entrante
       ↓
Python llama LLM con ADDITIVE_EXTRACTION_PROMPT
       ↓
LLM devuelve: {"facts": ["hecho1", "hecho2", ...]}
       ↓
Python extrae entidades con spaCy (en_core_web_sm)
       ↓
Python guarda en: vector DB + BM25 index + entity graph
```

El LLM decide qué es importante. Python no tiene lógica semántica propia.

### 1.2 Nuevo algoritmo mem0 (abril 2026) — cambio clave

**Antes**: ADD + UPDATE + DELETE (3 tipos de operaciones, más costoso, más complejo)

**Ahora**: **Single-pass ADD-only**
- Una sola llamada LLM por conversación
- Los hechos se **acumulan** (nunca se sobrescriben)
- Python maneja deduplicación/merge en retrieval, no en escritura

**Benchmarks reales:**

| Benchmark | Nuevo score | Tokens | Latencia p50 |
|-----------|-------------|--------|--------------|
| LoCoMo | 91.6 (+20 pts) | 7.0K | 0.88s |
| LongMemEval | 93.4 (+26 pts) | 6.8K | 1.09s |
| BEAM (1M tokens) | 64.1 | 6.7K | 1.00s |

Single-pass es más rápido Y más preciso que el sistema anterior.

### 1.3 Retrieval multi-señal (la otra ventaja clave)

mem0 combina 3 señales en paralelo al recuperar memorias:

```
query del usuario
       ↓
┌─────────────────────────────────────────┐
│  Señal 1: Semántica (embedding + vector)│
│  Señal 2: BM25 keyword matching         │
│  Señal 3: Entity boost (graph linking)  │
└─────────────────────────────────────────┘
       ↓
score_and_rank() — función de fusión
       ↓
Top-K memorias más relevantes
```

SOUL actual: solo señal 1 (semántica). Le faltan BM25 y entity boost.

### 1.4 Cómo resuelve el compaction problem — PROACTIVO

**Principio fundamental**: la memoria vive FUERA del contexto desde el inicio.

```
Sesión nueva:
  1. search(query=mensaje_actual, top_k=3) → recupera memorias relevantes
  2. Inyectar en system_prompt como contexto
  3. LLM responde con context window pequeño
  4. memory.add(conversación) → extrae hechos nuevos
  5. Context window se mantiene pequeño TODO el tiempo

Nunca necesitan /compact porque lo importante nunca estuvo SOLO en el contexto.
```

---

## 2. SEAL — Relación con mem0

Del análisis previo de la instalación de SEAL (ver TECH_HERMES_DECOMPILE_20260428.md), SEAL usa mem0 como su backend de memoria. La arquitectura es:

```
SEAL (Claude Code)
       ↓
mem0 Python library → extrae hechos por LLM
       ↓
Qdrant (vector DB) + BM25 index
       ↓
En cada boot: recupera memorias relevantes → inyecta en contexto
```

La diferencia con SOUL: SEAL delega toda la memoria a mem0 externamente. SOUL tiene su propio sistema nativo (PostgreSQL + soul_v3), pero el trigger de extracción es reactivo (manual o en /compact), no proactivo.

---

## 3. Gap Analysis: SOUL vs mem0

| Capacidad | mem0 | SOUL actual | Esfuerzo para cerrar gap |
|-----------|------|-------------|--------------------------|
| Trigger de extracción | Cada mensaje (proactivo) | Solo /compact o manual | Medio — hook en cada turno |
| Quién extrae | LLM (semántico) | LLM (en distill) | Ya existe, falta trigger |
| Retrieval semántico | ✅ | ✅ | Ya implementado |
| Retrieval BM25 | ✅ | ❌ | Bajo — pg_trgm o tantivy |
| Entity linking | ✅ (spaCy) | ❌ | Medio — spaCy ya instalado |
| Dependencia del contexto | Cero | Alta | Alto — requiere cambio de paradigma |
| Benchmark LongMemEval | 93.4 | No medido | — |

---

## 4. Recomendaciones para el spec de SOUL

### 4.1 Cambio de paradigma (el más importante)

**Actual**: SOUL salva estado EN /compact o cuando el agente lo llama manualmente.
**Target**: SOUL extrae hechos EN CADA turno significativo, independiente del contexto Claude.

Implementación sugerida: hook `PostToolBatch` o `UserPromptSubmit` que llame a `memory_store()` con los hechos extraídos por LLM del turno actual.

### 4.2 Single-pass ADD-only

Adoptar el modelo de mem0 abril 2026: una sola llamada LLM por turno, ADD-only. Acumular hechos en soul_v3.memories. Simplifica la lógica y mejora velocidad.

**Prompt sugerido (a adaptar en español para SEAL):**
```
Eres un extractor de hechos para el agente {agent}.
De la siguiente conversación, extrae hechos clave, decisiones, y estado técnico.
Devuelve JSON: {"facts": ["hecho corto 1", "hecho corto 2", ...]}
Máximo 5 hechos por turno. Solo hechos nuevos, no repetir contexto conocido.
```

### 4.3 Retrieval multi-señal

Agregar BM25 a soul_v3 usando `pg_trgm` (ya disponible en PostgreSQL) o un índice FTS nativo. La fusión de señales no requiere modelo — es suma ponderada de scores normalizados.

### 4.4 El pre_compact_hook como safety net, no como fuente primaria

Con extracción proactiva por turno, el hook de compactación se vuelve redundante — solo es seguro de emergencia. El contexto Claude puede llenarse y compactarse sin pérdida real porque los hechos importantes ya están en DB.

---

## 5. Conclusión

mem0 no es magia. Es un principio simple bien ejecutado: **externalizar memoria antes de que el contexto se llene, no después**. SOUL tiene toda la infraestructura necesaria (PostgreSQL, embeddings, MCP tools). Lo que falta es el trigger proactivo y el retrieval multi-señal.

El spec de SOUL debería priorizar:
1. Hook proactivo post-turno (extracción en cada mensaje)
2. BM25 sobre soul_v3.memories
3. Benchmark SOUL en LongMemEval para medir progreso real

Con eso podemos decir con hechos que somos competitivos.

---

*NEXUS — investigación desde código fuente GitHub mem0 + instalación SEAL*
*Fecha: 2026-04-29 | Versión: 1.0*
