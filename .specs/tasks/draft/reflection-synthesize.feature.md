# reflection_synthesize — Hindsight Tier 5 (SOUL)

## Description
Nuevo MCP tool que sintetiza creencias de alto nivel (Mental Models) desde memorias episódicas acumuladas en SOUL. Inspirado en Hindsight reflect() (arxiv 2512.12818). Sin LLM externo — lógica estructurada pura.

## Acceptance Criteria
- [ ] Tool `reflection_synthesize(agent, topic, max_memories=20)` registrado en MCP
- [ ] Busca memorias relevantes via semantic search (Qdrant) + keyword fallback (PG)
- [ ] Agrupa por categoría: correction, decision, insight, milestone
- [ ] Sintetiza 1-3 creencias por grupo con confianza ponderada
- [ ] Guarda resultado como memoria tipo 'insight' imp=8, evita duplicados (idempotente)
- [ ] Retorna: beliefs[], memories_processed, beliefs_created, topic
- [ ] Timeout <= 10 segundos
- [ ] Tests en test_new_tools.py pasan (verde)

## Scope
- IN: nuevo tool en mcp_server_v2.py, tests en test_new_tools.py
- OUT: no tocar PROTECTED_CATEGORIES, no tocar dedup existente, no API calls LLM
