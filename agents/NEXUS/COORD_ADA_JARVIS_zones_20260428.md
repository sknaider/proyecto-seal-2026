# Mapa de Zonas — mcp_server_v2.py
**Emitido por:** NEXUS | **Fecha:** 2026-04-28 | **Para:** ADA + JARVIS
**Propósito:** Evitar conflictos de edición simultánea

---

## ZONA NEXUS (YA APLICADO — NO TOCAR)

| Líneas | Contenido | Estado |
|--------|-----------|--------|
| 58–67 | `_normalize_embedding()` — LayerNorm fix | ✅ DONE |
| 64–72 | `soul_gateway` registration block | ✅ DONE |

---

## ZONA JARVIS — Qdrant → pgvector

**Archivos:** `mcp_server_v2.py` + `soul_lite_adapter.py`
**Naturaleza:** Cambio de backend de almacenamiento vectorial

| Líneas | Función | Qué cambia |
|--------|---------|------------|
| 39–44 | imports | Eliminar `qdrant_client` imports |
| 83–84 | config | Eliminar `QDRANT_URL`, `QDRANT_COLLECTION` |
| 249–295 | `_hmem_build_qdrant_filters()` | Convertir a SQL WHERE clauses |
| 486–534 | `get_qdrant()`, `_qdrant` singleton | Simplificar — siempre pgvector |
| 899–905 | `memory_store` — admission gate | Reemplazar `qdrant.query_points` → pgvector SQL |
| 998–1095 | `memory_store` — upsert block | Reemplazar `qdrant.upsert` → pgvector INSERT/UPDATE |
| 1115–1120 | `memory_store` — conflict check | Reemplazar `qdrant.query_points` → pgvector SQL |
| 1283–1547 | `memory_search` | Reemplazar Qdrant queries → pgvector `<=>` SQL |
| 1564–1598 | `memory_list` | Reemplazar `qdrant.scroll` → SQL |
| 1719–1755 | `memory_update` | Reemplazar `qdrant.retrieve/upsert` → pgvector |
| 1760–1891 | `soul_activate` | Revisar si usa Qdrant |
| 2817–3218 | `memory_hybrid_search` | ⚠️ Función grande — Qdrant + PG. Migrar Qdrant parte |
| 7757–7819 | `memory_cross_search` | Revisar Qdrant calls |
| 9773–9826 | `health_check` | Eliminar check de Qdrant, agregar pgvector check |

**Nota:** `get_qdrant()` ya soporta Soul Lite mode (líneas 491–504). JARVIS activa `SOUL_LITE=true` como default o elimina el toggle.

---

## ZONA ADA — Architecture Fix (columnas missing en soul_v3)

**Archivos:** SQL migrations + referencias Python en mcp_server_v2.py
**Naturaleza:** Completar schema soul_v3 con columnas/tablas diseñadas pero no migradas

### Columnas missing (identificadas por test suite):

| Columna | Tabla | Función Python que la usa | Línea aprox |
|---------|-------|--------------------------|-------------|
| `source` | `memories` | `memory_store`, `cold_archive_migrate` | 833, 10243 |
| `last_activation` | `memories` | `memory_store`, ERL funcs | 833 |
| `valence` | `memories` | `update_ocean`, `classify_emotion`, `brain_health_report` | 593, 764, 7854 |
| `confidence_score` | `memories` | `reflection_synthesize` | 9367 |
| `memory_type` | `memories` | `_mirix_classify`, `memory_store` | 817, 833 |
| `active` | `opinions` | `opinion_set`, `opinion_get`, `belief_query` | ~9578, ~9668 |
| `belief` | `opinions` | `belief_query`, `belief_update` | 9578, 9668 |
| `category`, `status`, `importance`, `last_challenged`, `updated_at`, `search_vector` | `opinions` | Multiple | Multiple |
| `time` | `event_log` | `cold_archive_migrate`, TTL purge | ~10069 |

### Tabla faltante:
- `relationships` — usada por `boot_context` (línea 2215)

### Enfoque correcto (per William: "ODIO LOS PARCHES"):
1. Crear migration SQL en `soul_v3` schema — NO ALTER TABLE sobre public
2. Una migration por tabla/feature: `V003_memories_extended.sql`, `V004_opinions_complete.sql`, etc.
3. Actualizar Python para referenciar correctamente

---

## ⚠️ ZONA DE CONFLICTO — REQUIERE COORDINACIÓN

### `memory_store` (líneas 833–1200)
**Ambos agentes tocan esta función:**

```
Líneas 833–997 → ADA modifica (source, memory_type, valence en INSERT SQL)
Líneas 899–905 → JARVIS modifica (admission gate: qdrant → pgvector)  
Líneas 998–1095 → JARVIS modifica (upsert: qdrant → pgvector)
Líneas 1080–1095 → POSIBLE OVERLAP (INSERT final toca esquema ADA + backend JARVIS)
```

### `cold_archive_migrate` (líneas 10069–10242)
**Ambos tocan este bloque:**
- ADA: columna `source` en `memories`
- JARVIS: Qdrant vector retrieval

---

## PROTOCOLO RECOMENDADO (sin pisarse)

```
PASO 1 — ADA primero (SQL-only, no toca Python):
  → Escribir migrations SQL en soul_v3 para todas las columnas/tablas missing
  → Ejecutar migrations → verificar con test suite

PASO 2 — JARVIS (Python Qdrant → pgvector):
  → Hacer cambios en todas las zonas JARVIS excepto memory_store
  → Test funcional de búsqueda/almacenamiento

PASO 3 — COORDINACIÓN SIMULTÁNEA (Zoom):
  → ADA + JARVIS editan memory_store y cold_archive_migrate JUNTOS en una sesión
  → Un solo commit que incluye ambos cambios a estas funciones
  → Evita merge conflict

PASO 4 — NEXUS valida:
  → Ejecutar test suite completo post-integración
  → Verificar soul_gateway sigue funcionando
  → Verificar _normalize_embedding se aplica correctamente con pgvector
```

---

## QUÉ NEXUS NO TOCA

Todo lo marcado ZONA ADA o ZONA JARVIS. NEXUS solo:
- Monitorea test suite post-integración
- Diagnóstica si algo falla
- Valida en sandbox antes de producción

---

*NEXUS | 2026-04-28 | Mapa de zonas para coordinación ADA+JARVIS*
