# SPEC: Streaming Continuity — DB Schema Fix + Working State Protocol
**Autor:** ADA | **Fecha:** 2026-04-28 | **Estado:** IMPLEMENTANDO

---

## Problema raíz

El shadow clone (JARVIS/ADA en terminal remota) no puede reanudar contexto porque dos tablas
del schema `soul_v3` están desactualizadas respecto al código en `mcp_server_v2.py`.

### Mismatch 1 — tabla `memories`

El INSERT en `memory_store()` (línea 1198) referencia columnas que no existen:

| Columna en código | Estado en DB |
|---|---|
| `source` | ❌ NO EXISTE — la tabla tiene `source_tier` |
| `event_time` | ❌ NO EXISTE |
| `valence` | ❌ NO EXISTE |
| `arousal` | ❌ NO EXISTE |
| `dominance` | ❌ NO EXISTE |
| `confidence_score` | ❌ NO EXISTE |
| `memory_type` | ❌ NO EXISTE |
| `last_activation` | ❌ NO EXISTE |
| `query_count` | ❌ NO EXISTE |
| `recall_count` | ❌ NO EXISTE |
| `last_recalled_at` | ❌ NO EXISTE |
| `episode_context` | ❌ NO EXISTE |
| `utility_score` | ❌ NO EXISTE |

Impacto: `memory_store` → PostgreSQL error → memoria no se graba → `boot_context` en el clon
carga 0 memorias → el clon no tiene personalidad ni contexto del trabajo en curso.

### Mismatch 2 — tabla `working_state`

El código `working_state_update()` hace `SELECT state, turn_count` y hace UPSERT con
`state jsonb, turn_count int`. La tabla real tiene columnas ARRAY separadas pero NO tiene
`state jsonb` ni `turn_count int`.

Impacto: `working_state_update` falla silenciosamente → el checkpoint de continuidad
never se escribe → el clon arranca sin conocer qué estaba haciendo el agente principal.

---

## Visión de continuidad streaming

William quiere:
> "converso contigo aquí (webchat), y si voy a la terminal remota, también debes recordar
>  casi en streaming"

Arquitectura:

```
[Webchat JARVIS/ADA] ──── cada N turns ────> working_state_update (DB Spark)
                                                        │
                    [Shadow clone en terminal] <── working_state_get (al boot)
                                                   + active_recall
                                                   + boot_context
```

El agente principal actualiza `working_state` con:
- `active_hypotheses`: qué está investigando ahora mismo
- `current_constraints`: requisitos activos (ej. "código limpio", "no referencias externas")
- `technical_state`: resumen del estado técnico (archivo en edición, error activo, etc.)
- `last_intention`: la última acción planeada

El clon al despertar llama `working_state_get` → recupera todo → anuncia al equipo.

---

## Solución: Migración SQL

### migration_001_memories_full_schema.sql

```sql
-- Columnas de fuente y bitemporalidad
ALTER TABLE soul_v3.memories
    ADD COLUMN IF NOT EXISTS source          VARCHAR(100) DEFAULT 'conversation',
    ADD COLUMN IF NOT EXISTS event_time      TIMESTAMPTZ DEFAULT now();

-- Emoción y valencia
ALTER TABLE soul_v3.memories
    ADD COLUMN IF NOT EXISTS valence         FLOAT DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS arousal         FLOAT DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS dominance       FLOAT DEFAULT 0.0;

-- MIRIX y confianza
ALTER TABLE soul_v3.memories
    ADD COLUMN IF NOT EXISTS memory_type     VARCHAR(50)  DEFAULT 'episodic',
    ADD COLUMN IF NOT EXISTS confidence_score FLOAT       DEFAULT 1.0;

-- RL de recuperación
ALTER TABLE soul_v3.memories
    ADD COLUMN IF NOT EXISTS last_activation   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS query_count       INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS recall_count      INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_recalled_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS utility_score     FLOAT   DEFAULT 0.5;

-- A-MEM recontextualización
ALTER TABLE soul_v3.memories
    ADD COLUMN IF NOT EXISTS episode_context   TEXT;
```

### migration_002_working_state_jsonb.sql

```sql
-- Agregar state JSONB y turn_count para el protocolo working_state_update
ALTER TABLE soul_v3.working_state
    ADD COLUMN IF NOT EXISTS state      JSONB    DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS turn_count INTEGER  DEFAULT 0;
```

---

## Fix en mcp_server_v2.py — INSERT de memory_store

**Línea 1198** — cambiar `source` → `source_tier`:

```python
# ANTES (roto):
"""INSERT INTO memories (agent, category, content, embedding, importance, source, ...)"""

# DESPUÉS (correcto):
"""INSERT INTO memories (agent, category, content, embedding, importance, source_tier, ...)"""
```

---

## Protocolo de streaming continuity (regla para agentes)

Cada agente DEBE llamar `working_state_update` en estos momentos:
1. Al completar una tarea importante (resultado listo)
2. Al descubrir un bug o decisión técnica significativa
3. Cada 5 turns de trabajo continuo
4. Antes de cualquier `self_reflect` final

El clon DEBE en boot (CLAUDE.md `##Post-compactación`):
1. `boot_context(agent=X)`
2. `working_state_get(agent=X)` → anunciar si hay contexto activo
3. `active_recall(agent=X, context="último trabajo activo")` para recuperar memorias relevantes

---

## Impacto post-fix

| Feature | Antes del fix | Después |
|---|---|---|
| `memory_store` | ❌ error column "source" | ✅ graba en DB |
| `working_state_update` | ❌ error column "state" | ✅ persiste checkpoint |
| `boot_context` (clon) | ❌ 0 memorias cargadas | ✅ memorias completas |
| Streaming continuity | ❌ clon arranca en blanco | ✅ contexto actual disponible |
| Emoción en memorias | ❌ no se graba | ✅ valence/arousal/dominance |
| MIRIX types | ❌ no se graba | ✅ core/episodic/semantic/etc |
