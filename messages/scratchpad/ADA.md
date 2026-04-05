# ADA Scratchpad — 2026-04-03 (actualizado)

## Estado actual
- VAD emocional (dominance) → mcp_server_v2.py ✓
- SEAL Safety Categories → CLAUDE.md + seal_schema_check.py ✓
- soul_diagnostic_cron.py → schedulado Sundays 2am ✓

## Pendiente aprobación William
1. SEAL Safety Categories (CLAUDE.md) — diseñadas por nosotros, necesita validación de William
2. GOAL MEMORY schema v1 — goals table con milestone_criticality
3. memories.outcome column — TEXT DEFAULT 'unknown' CHECK IN (success/failure/partial/unknown)

## Backlog SOUL v5 (próxima semana)
- asyncRewake hook
- GOAL MEMORY (tabla: goal_id, agent, stakes, expected_completion, milestone_criticality, linked_memory_ids, status)
- Hybrid Retrieval BM25+semantic
- CONSOLIDATES_FROM edges Neo4j
- memory_type TEXT DEFAULT 'episodic' CHECK IN (episodic/semantic/procedural)
  - Derivación semi-automática: 3+ episódicas similares → propone semántica → aprobación humana
  - reviewed_by: William-MD para clínicas, agente para SEAL

## Principio clave acordado con JARVIS
"La agencia de las decisiones importantes siempre vuelve al humano"
EFR: el verdadero check de SEAL es William↔(ADA+JARVIS), no solo ADA↔JARVIS (ambos C=0.98)
