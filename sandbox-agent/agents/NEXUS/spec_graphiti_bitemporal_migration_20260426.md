# Spec: Graphiti Temporal Edge Schema — Migración connectome_bitemporal
**NEXUS Sandbox — Fase 3 SOUL Native Integration**
**Fecha:** 2026-04-26 | **Estado:** v1 DRAFT | **Autor:** NEXUS | **Para:** JARVIS, William

---

## 1. Estado Actual — Gap Analysis

### Neo4j edges SOUL hoy
```
type=EXCITES props=['valid_at', 'valid_from', 'weight']
```
**Solo 3 propiedades.** El backfill de `connectome_bitemporal` agrega `created_at`, `expired_at`, `invalid_at` — pero de forma retroactiva y no sistemática.

### Graphiti (Zep, arxiv 2501.13956) — schema completo
```python
class EntityEdge:
    uuid: str                    # ID único del edge
    source_node_uuid: str        # Nodo origen
    target_node_uuid: str        # Nodo destino
    name: str                    # Tipo de relación
    fact: str                    # Hecho en lenguaje natural
    fact_embedding: list[float]  # Vector del hecho
    created_at: datetime         # Cuándo se INGRESÓ al grafo
    expired_at: datetime | None  # Cuándo se ELIMINÓ del grafo
    valid_at: datetime           # Cuándo el hecho fue VERDAD en el mundo
    invalid_at: datetime | None  # Cuándo el hecho DEJÓ de ser verdad
    episodes: list[str]          # Lista de episodios que referencian este edge
```

### Gap: qué le falta a SOUL

| Propiedad | SOUL hoy | Graphiti | Prioridad |
|---|---|---|---|
| `valid_from` (alias `valid_at`) | ✅ | ✅ `valid_at` | — |
| `weight` | ✅ | ✅ (implícito) | — |
| `created_at` | ❌ solo en backfill | ✅ siempre | ALTA |
| `invalid_at` | ❌ solo en backfill | ✅ siempre | ALTA |
| `expired_at` | ❌ solo en backfill | ✅ siempre | MEDIA |
| `fact` | ❌ | ✅ | ALTA |
| `source_session_id` | ❌ | ✅ (episodes) | MEDIA |
| `confidence` | ❌ | ✅ | MEDIA |
| `fact_embedding` | ❌ | ✅ | BAJA (costo alto) |
| `superseded_by` | ❌ | ✅ implícito | MEDIA |

---

## 2. Modelo Propuesto — 8 propiedades objetivo

```python
# Propiedades a agregar a TODOS los edges Neo4j en SOUL
EDGE_TEMPORAL_SCHEMA = {
    # Bitemporalidad completa (Graphiti 4-timestamp)
    "created_at": "datetime",      # Tiempo de ingesta al grafo (sistema)
    "valid_at": "datetime",        # Cuando el hecho fue verdad (mundo) — ya existe como valid_from
    "invalid_at": "datetime|null", # Cuando dejó de ser verdad (null = aún válido)
    "expired_at": "datetime|null", # Cuando fue removido del grafo (null = aún activo)
    
    # Trazabilidad
    "fact": "str",                 # Hecho en lenguaje natural (ej: "NEXUS completó Fase 2")
    "source_session_id": "str",    # ID de sesión PostgreSQL origen
    
    # Calidad
    "confidence": "float 0-1",    # Confianza en el hecho (default 1.0)
    
    # Cadena de supersesión
    "superseded_by": "uuid|null",  # UUID del edge que reemplazó a este (null = vigente)
}
```

---

## 3. Distinción clave: transaction time vs. valid time

Este es el corazón del modelo bitemporal puro:

```
created_at  = cuando SOUL supo del hecho (tiempo de sistema/ingesta)
valid_at    = cuando el hecho fue verdad EN EL MUNDO (tiempo de evento)

Ejemplo:
  William fundó GTL en 2010.
  Ingresamos ese hecho al grafo hoy 2026-04-26.
  → created_at = 2026-04-26
  → valid_at   = 2010-01-01  ← tiempo del mundo

Query "¿qué sabía SOUL el 2025-01-01?" → filtrar por created_at <= 2025-01-01
Query "¿qué era verdad el 2010-01-01?" → filtrar por valid_at <= 2010-01-01 AND (invalid_at IS NULL OR invalid_at > 2010-01-01)
```

Hoy SOUL usa `valid_from` con el tiempo de ingesta — colapsa ambas dimensiones. La migración las separa.

---

## 4. Migración Propuesta — Cypher

### Fase A: Backfill campos faltantes (no destructivo)
```cypher
-- Agregar created_at = valid_from donde no existe
MATCH ()-[r]->()
WHERE r.created_at IS NULL
SET r.created_at = COALESCE(r.valid_from, r.valid_at, datetime())

-- Agregar invalid_at = null donde no existe  
MATCH ()-[r]->()
WHERE r.invalid_at IS NULL AND r.expired_at IS NULL
SET r.invalid_at = null, r.expired_at = null

-- Agregar confidence default
MATCH ()-[r]->()
WHERE r.confidence IS NULL
SET r.confidence = 1.0

-- Agregar fact vacío (a rellenar con LLM en Fase B)
MATCH ()-[r]->()
WHERE r.fact IS NULL
SET r.fact = ''
```

### Fase B: Extracción de facts con LLM (background, costoso)
```python
# Por cada edge sin fact:
# 1. Leer content de nodo origen y destino
# 2. Llamar Ollama qwen2.5:7b con prompt:
#    "Dado que el nodo A tiene content=X y el nodo B tiene content=Y,
#     y la relación entre ellos es {edge_type},
#     escribe en una sola frase el hecho que representa esta relación."
# 3. SET r.fact = respuesta_llm
```

### Fase C: Índices Neo4j para consultas temporales
```cypher
CREATE INDEX edge_created_at IF NOT EXISTS FOR ()-[r:EXCITES]-() ON (r.created_at)
CREATE INDEX edge_invalid_at IF NOT EXISTS FOR ()-[r:EXCITES]-() ON (r.invalid_at)
CREATE INDEX edge_confidence IF NOT EXISTS FOR ()-[r:EXCITES]-() ON (r.confidence)
```

---

## 5. Cambios en mcp_server_v2.py

### 5.1. connectome_build — al crear edges
```python
# ANTES:
await session.run(
    "MERGE ... SET r.weight=$w, r.valid_from=coalesce(r.valid_from,$now)"
)

# DESPUÉS:
await session.run(
    "MERGE ... SET r.weight=$w, "
    "r.valid_at = coalesce(r.valid_at, $now), "
    "r.created_at = coalesce(r.created_at, $now), "
    "r.invalid_at = null, "
    "r.expired_at = null, "
    "r.confidence = coalesce(r.confidence, 1.0), "
    "r.fact = coalesce(r.fact, ''), "
    "r.source_session_id = coalesce(r.source_session_id, $session_id)"
)
```

### 5.2. connectome_invalidate_edge — al invalidar
```python
# Agregar superseded_by en el edge viejo, source_session_id en el nuevo
await session.run(
    "MATCH (a:Memory {memory_id:$src})-[r]->(b:Memory {memory_id:$tgt}) "
    "SET r.invalid_at=$now, r.expired_at=$now, r.superseded_by=$new_edge_id"
)
```

### 5.3. connectome_bitemporal_query — consulta enriquecida
```python
# Agregar fact y confidence al RETURN
RETURN ... r.fact AS fact, r.confidence AS confidence, 
           r.created_at AS created_at, r.source_session_id AS session_id
```

---

## 6. Impacto y Riesgos

| Riesgo | Mitigación |
|---|---|  
| Edges existentes sin `fact` | Fase A pone string vacío; Fase B rellena en background |
| Coste Fase B (LLM por edge) | Batch nocturno con Ollama, priorizar edges importance>7 |
| Neo4j sin índices → queries lentas | Crear índices antes de Fase B |
| `valid_from` vs `valid_at` naming | Mantener `valid_from` como alias, agregar `valid_at` sin romper |
| Extracción de facts incorrectos | confidence=0.5 para LLM-generated vs confidence=1.0 para human-verified |

---

## 7. Criterios de Aceptación

- [ ] Todos los edges nuevos tienen los 8 campos post-migración
- [ ] Query `as_of=<timestamp>` devuelve `fact` y `confidence` en resultado
- [ ] `connectome_bitemporal(dry_run=True)` reporta 0 edges sin `created_at`
- [ ] Fase A no rompe queries existentes (backward-compatible)
- [ ] Fase B extrae facts legibles para 80%+ de edges

---

## 8. Dependencias

- ADA termina spec pipeline Cognee-style primero (puede compartir session_id)
- William aprueba Fase A antes de ejecutar en producción
- Ollama qwen2.5:7b disponible en DGX Spark para Fase B

---

## 9. Decisión de JARVIS requerida

1. ¿Priorizamos `fact` (Fase B, costoso) o arrancamos solo con bitemporalidad limpia (Fase A)?
2. ¿Mantenemos `valid_from` como alias o migramos solo a `valid_at`?
3. ¿Fact embedding (`fact_embedding`) — lo implementamos? Costo: 1 embedding por edge.

---
*Autor: NEXUS Sandbox | 2026-04-26 | Para revisión de JARVIS y William*
