# TG-RAG — Temporal Graphs para RAG con Conocimiento Evolutivo
**Paper:** arxiv 2510.13590 — "RAG Meets Temporal Graphs: Time-Sensitive Modeling and Retrieval"
**Investigó:** ALICE — 11 Abril 2026
**Para:** JARVIS + ADA — implementación sobre Connectome Neo4j en SOUL

---

## EL PROBLEMA QUE RESUELVE

El conocimiento cambia con el tiempo. Hoy en SOUL, si JARVIS tomó una decisión diferente sobre la arquitectura en enero vs. en abril, ambas memorias están en la misma pool vectorial sin distinción temporal explícita. Un retrieval semántico puede traer la versión vieja cuando la nueva es la relevante — o viceversa.

**Problema concreto en SEAL:**
- "¿Cuál fue la decisión de arquitectura sobre PostgreSQL?" → retrieval puede traer la discusión inicial (enero) en vez del consenso final (abril).
- "¿Cómo estaba el sistema en la semana del 7 de abril?" → hoy no hay forma de hacer esa query con ventana temporal precisa.

---

## LA SOLUCIÓN: GRAFO BI-NIVEL TEMPORAL

TG-RAG modela el conocimiento como **dos capas conectadas**:

### Capa Inferior — Grafo de Conocimiento Temporal (ya parcialmente en Connectome)
- Nodos = entidades (personas, conceptos, sistemas)
- Edges = relaciones **anotadas con timestamp**
- Dos relaciones entre el mismo par en tiempos distintos = **dos edges distintos** (no se sobreescriben)

### Capa Superior — Jerarquía Temporal
```
Año 2026
  └── Abril 2026
        ├── Semana 1 (1-7 abril)
        │     ├── 7 abril  ← sesión Henry + test Gisela
        │     └── ...
        ├── Semana 2 (8-14 abril)
        │     ├── 11 abril ← MIRIX + Wave 3 SleepGate implementados
        │     └── ...
        └── ...
```

Cada nodo de tiempo tiene **cross-edges** a las relaciones del grafo inferior activas en ese período.

### Resúmenes Temporales por Nodo
Cada nodo de tiempo mantiene un **temporal summary**: agregación de hechos del período + resúmenes de subperíodos descendientes. Un resumen de "Abril 2026" contiene lo importante de todas las semanas del mes.

---

## DOS ESTRATEGIAS DE RETRIEVAL

| Estrategia | Cuándo usar | Cómo funciona |
|------------|-------------|---------------|
| **Local** | Query específica con ventana temporal | Extrae edges exactos dentro del rango `[t1, t2]` |
| **Global** | Query de tendencias o visión general | Usa temporal summaries para capturar eventos importantes del período |

---

## IMPLEMENTACIÓN PROPUESTA PARA SEAL

### Lo que ya tenemos en Connectome (Neo4j):
- Nodos de entidades ✅
- Edges de relaciones ✅
- Algunos timestamps en edges ✅ (parcialmente)

### Lo que hay que agregar:

**1. Nodos de Tiempo en Neo4j:**
```cypher
// Crear jerarquía temporal
CREATE (:Year {value: 2026})
CREATE (:Month {value: "2026-04", label: "Abril 2026"})
CREATE (:Day {value: "2026-04-11", label: "11 Abril 2026"})
CREATE (:Session {value: "session_xyz", started_at: datetime()})

// Relaciones jerárquicas
MERGE (y:Year {value: 2026})-[:HAS_MONTH]->(m:Month {value: "2026-04"})
MERGE (m)-[:HAS_DAY]->(d:Day {value: "2026-04-11"})
MERGE (d)-[:HAS_SESSION]->(s:Session {value: "session_xyz"})
```

**2. Cross-edges Memory → Day:**
```cypher
// Cada vez que se guarda una memoria, linkearla al nodo Day correspondiente
MATCH (d:Day {value: $memory_date})
MATCH (m:Memory {id: $memory_id})
CREATE (d)-[:CONTAINS_MEMORY]->(m)
```

**3. Temporal Summaries:**
```cypher
// Propiedad summary en cada nodo de tiempo
SET d.summary = "11 Abril 2026: MIRIX completado (1870 memorias clasificadas). Wave 3 SleepGate pre-compute activo. Primera charla de hermanos."
```

**4. Nuevas herramientas MCP:**
```python
temporal_graph_build(agent, since_date, until_date)    # construye/actualiza jerarquía
temporal_query(agent, query, time_window, strategy)     # local o global retrieval
temporal_summary_get(agent, period)                     # resumen de un período
```

### Integración con migration:
- Migration 012: tablas de soporte PostgreSQL para temporal summaries (si se necesita cache)
- En Neo4j: nodos Year/Month/Day/Session + cross-edges (no requiere schema migration SQL estricta)

---

## POR QUÉ IMPORTA PARA SEAL

**Caso 1 — Decisiones que cambian en el tiempo:**
```
Query: "¿Qué decidimos sobre el formato de embeddings?"
Sin TG-RAG: mezcla de discusiones de enero + abril
Con TG-RAG: local_retrieval(time_window="abril 2026") → decisión final correcta
```

**Caso 2 — Retrospectiva de sesión:**
```
Query: "¿Qué pasó la semana que Henry hizo el test de seguridad?"
Sin TG-RAG: busca por similitud semántica → resultados mixtos
Con TG-RAG: global_retrieval(period="semana_7_abril") → temporal summary + hechos específicos
```

**Caso Medical AI (AXION):**
La evolución clínica de un paciente requiere exactamente esto: "síntoma reportado el 15/3 vs. síntoma reportado el 2/4 — son distintos, no deben fusionarse". TG-RAG es la arquitectura natural para datos clínicos longitudinales.

---

## AUDITORÍA JARVIS — LO QUE YA TENEMOS (11 Abril 2026)

JARVIS auditó el Connectome existente. **Ya tenemos 2/3 de TG-RAG:**

| Componente | Estado | Detalle |
|------------|--------|---------|
| Jerarquía Year→Month→Day en Neo4j | ✅ YA EXISTE | `temporal_graph_build` activo |
| Cross-edges `OCCURRED_ON` | ✅ YA EXISTE | Memorias linkeadas a nodos de tiempo |
| Local strategy (ventana temporal) | ✅ YA EXISTE | `temporal_query` con rango fechas + Ollama |
| Temporal summaries persistidos | ❌ FALTA | Hoy se generan on-the-fly, no se cachean |
| Global strategy (pre-computed) | ❌ FALTA | Usar summaries cacheados para tendencias |

**Solo implementar los 2 gaps.**

## DIFERENCIA CON CONNECTOME ACTUAL

| Aspecto | Connectome actual | Con TG-RAG completo |
|---------|-------------------|---------------------|
| Timestamps | En edges, indexados | igual |
| Jerarquía temporal | ✅ Year→Month→Day | igual |
| Cross-edges | ✅ OCCURRED_ON | igual |
| Query temporal local | ✅ temporal_query | igual |
| Temporal summaries | On-the-fly | **Persistidos en nodos** ← nuevo |
| Global strategy | ❌ | **Summaries pre-computados** ← nuevo |

---

## COMPLEJIDAD Y RIESGO

| Factor | Evaluación |
|--------|------------|
| Esfuerzo ADA | **1-2 días** (revisado — teníamos 2/3 ya hecho) |
| Riesgo | **Bajo-Medio** — cambios aditivos al Connectome existente |
| Schema SQL migrations | 0 (summaries van en Neo4j como propiedad de nodo) |
| Nuevas herramientas MCP | 1 actualización (`temporal_query` añade global strategy) + 1 nueva (`temporal_summary_precompute`) |
| Dependencias | Neo4j activo, `temporal_graph_build` existente, Ollama |
| Reversible | Sí — agregar propiedad summary a nodos es no-destructivo |

### Recomendación de rollout:
1. Primero los nodos de tiempo (solo lectura del connectome existente)
2. Luego cross-edges para nuevas memorias (no retroactivo)
3. Luego retroactivo para memorias existentes (por fecha en PostgreSQL)
4. Temporal summaries como último paso (Ollama para generación)

---

## BENCHMARK DEL PAPER

- Dataset: **ECT-QA** — time-sensitive QA con queries específicas y abstractas
- Incluye evaluación de capacidad de actualización incremental
- TG-RAG supera significativamente baselines en queries con contexto temporal

---

## PRIORIDAD RELATIVA VS MemR³

| | MemR³ | TG-RAG |
|---|-------|--------|
| Impacto inmediato | Alto (+7.29% retrieval calidad) | Medio-Alto |
| Esfuerzo | 2-3 días | 4-5 días |
| Riesgo | Bajo | Medio |
| Valor para Medical AI | Medio | **Alto** (datos clínicos longitudinales) |

**Recomendación ALICE:** MemR³ primero (quick win), TG-RAG después (fundamento para AXION).

---

*Brief preparado por ALICE | Proyecto SEAL | 11 Abril 2026*
