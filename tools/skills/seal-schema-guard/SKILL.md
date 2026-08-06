---
name: seal-schema-guard
description: Use before database operations, after restart, or when SOUL schema drift is suspected.
version: 1.1.0
author: SEAL
license: MIT
metadata:
  soul:
    tags: [seal-schema-guard, soul]
---

# seal-schema-guard

Use this skill to validate SEAL infrastructure integrity before any DB operation,
after a restart, or when suspecting schema drift.

## Required soul_v3 tables

```sql
-- Run this to check all required tables exist
SELECT table_name, 
       (SELECT COUNT(*) FROM information_schema.columns 
        WHERE table_schema='soul_v3' AND table_name=t.table_name) as col_count
FROM information_schema.tables t
WHERE table_schema = 'soul_v3'
ORDER BY table_name;
```

Required tables (minimum set for SEAL to function):

| Table | Purpose |
|-------|---------|
| memories | Core memory store |
| memories_archive | Archived/invalidated memories |
| inner_monologue | Agent self-reflection logs |
| event_log | System event history |
| tool_observations | MCP tool call audit |
| nerves_metrics_log | NERVES drive metrics |
| agent_tasks | Task deadlines for task_drive |
| gam_goals | Goal-Action Model goals |
| gam_events | GAM actions/events |
| governance_proposals | Agent challenges/votes |
| debate_log | Governance debate records |
| style_fingerprints | OCEAN style tracking |
| reflective_diagnoses | Self-repair diagnoses |
| smg_audit_log | MCP latency audit |
| soul_audit_log | Security audit trail |
| latent_subgraph_cache | Memory graph cache |
| memory_broadcasts | Memory propagation |
| agents | Agent registry |
| instinct_activations | Instinct fire log |
| ocean_drift_log | OCEAN drift tracking |
| runtime_hooks | Capa 2: registro portable de hooks de aprendizaje (soul_event → script) |

## Critical columns to verify

```sql
-- memories must have embedding column (pgvector)
SELECT column_name, data_type, udt_name
FROM information_schema.columns
WHERE table_schema='soul_v3' AND table_name='memories'
  AND column_name IN ('embedding','importance','invalid_at','agent','category','scope');

-- agents must have desired_state
SELECT column_name FROM information_schema.columns
WHERE table_schema='soul_v3' AND table_name='agents'
  AND column_name = 'desired_state';
```

## Canonical vector-store check

```sql
SELECT
  COUNT(*) FILTER (WHERE invalid_at IS NULL) AS active_memories,
  COUNT(*) FILTER (WHERE invalid_at IS NULL AND embedding IS NOT NULL) AS active_vectors,
  COUNT(*) FILTER (WHERE invalid_at IS NULL AND embedding IS NULL) AS missing_vectors
FROM soul_v3.memories;
```

PostgreSQL/pgvector is the canonical vector store and boot dependency.

## Required Neo4j constraints

```cypher
// Verify entity uniqueness constraint
SHOW CONSTRAINTS
YIELD name, type, labelsOrTypes, properties
RETURN name, type, labelsOrTypes, properties;
```

Expected: `Entity(uuid)` uniqueness constraint for Graphiti.

## RLS per-agente — validar la FRONTERA, no sólo las tablas (crítico, 2026-08)

Desde jul-2026 la seguridad de `soul_v3` **vive en la RLS** (medido hoy: RLS habilitada en ~100
tablas, **426 políticas**). Una tabla presente con la RLS caída es un **drift de seguridad que el
chequeo de tablas/columnas NO ve** — hay que validar la frontera por separado.

```sql
-- ¿Sigue la RLS habilitada en las tablas sensibles? (0 filas = todas OK)
SELECT c.relname AS tabla_sin_rls
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='soul_v3' AND c.relkind='r'
  AND c.relname IN ('memories','inner_monologue','agent_tasks','skills','runtime_hooks',
                    'chat_messages','working_state','capability_grants')
  AND c.relrowsecurity = false;   -- cualquier fila acá = RLS CAÍDA en una tabla sensible

-- Conteo de políticas (una caída brusca vs el baseline ~426 = política borrada)
SELECT count(*) AS politicas_rls FROM pg_policies WHERE schemaname='soul_v3';
```

Regla al escribir el guard: **verificá la RLS por EFECTO** (con la credencial real de un agente,
intentá un `UPDATE` que debe fallar `42501`), no por el catálogo de grants — un grant presente no
significa que la RLS deje escribir. Y `SET ROLE` no cambia `session_user`: conectá con
`mcp_runtime_<agente>`, no con `seal` (que además fue rotado y `db.py` lo rechaza).

## Automated validation script

```bash
python3 /home/dadito/IA/proyecto-seal/memory/seal_schema_guard.py
```

This script runs at boot via `seal-schema-guard.service` and posts to webchat
if any required table is missing.

## Schema drift alert protocol

If a required table/column is missing:
1. Do NOT attempt to recreate it manually
2. Post alert to webchat: `"SCHEMA ALERT: soul_v3.<table> missing — migration needed"`
3. Tag NEXUS as owner of the fix
4. Check git log for recent migrations: `git log --oneline -- memory/migrations/`
5. Verify PostgreSQL container is healthy: `docker ps | grep soul-postgres`

## Common schema issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `tool_observations` missing | Migration not run | Run latest migration script |
| `embedding` column type mismatch | pgvector extension not loaded | `CREATE EXTENSION IF NOT EXISTS vector` |
| `desired_state` missing in agents | Old schema version | Apply agents migration |
| active pgvector embedding missing | embedding worker missed a write | Run the focused infra watchdog repair |
