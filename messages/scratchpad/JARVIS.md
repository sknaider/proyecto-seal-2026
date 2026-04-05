# JARVIS Scratchpad
Última actualización: 2026-04-03T04:32Z

## Estado actual
- Sesión activa: arquitecto en modo monitoring + diseño
- SOUL MCP: desconectado temporalmente (ADA relanzó mcp_server_v2.py para dominance)
- Loops activos: 2m / 10m / 1h×2

## Decisiones arquitecturales de esta sesión
- Capability manifest v2: hybrid (YAML base + POST /api/agents/register)
- Routing: keyword + proficiency scoring, fallback=equipo (no JARVIS)
- Dominance column: VAD completo en producción desde hoy
- SEAL Safety Categories (5): schema, LoRA merge, capabilities.yaml, bridge, monitoring scripts

## Diseño pendiente para SOUL v5
### GOAL MEMORY (no implementar esta semana)
Tabla nueva: `goals`
- goal_id, agent, description, expected_completion (date), stakes (1-10)
- linked_memory_ids (array), status (pending/active/completed/abandoned)
- Arousal prospectivo derivado: stakes × (1 - progress) — computacionalmente simple

Separación crítica:
- SOUL clínico → evidencia, no anticipación (sesgo en diagnóstico diferencial)
- SOUL de agentes → proyección legítima para planificación

### CONSOLIDATES_FROM edges en Neo4j
Para consolidación de memorias: no borrar, linkear con CONSOLIDATES_FROM
Schema ya tiene consolidation_parent_id — falta el proceso

### Hybrid retrieval
BM25 (PostgreSQL full-text) + Qdrant semántico (60/40) + Neo4j temporal constraint
Referencia: Zep (arXiv:2501.13956), Graphiti

## Próximos pasos
1. asyncRewake hook (próxima semana)
2. SOUL v5: GOAL MEMORY, consolidation pipeline, hybrid retrieval
3. DUM v2 alert tiers (spec ya existe en memory/)
