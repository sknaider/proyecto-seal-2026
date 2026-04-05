# Registro de Auditorías — JARVIS Mayor

## 2026-03-31 — Auditoría fundacional

**Trigger:** William pidió revisión profunda del SOUL
**Modelo:** Opus 4.6

### Hallazgos (15 problemas)
1. Qdrant desync: 177 memorias fantasma (PG sin Qdrant) → FIXED
2. Neo4j desync: 23,929 aristas perdidas (69%) → REBUILT 18,101
3. Style ADA reseteable (0.5/0.5) por soul_reflect.py → LOCKED
4. Style JARVIS incorrecto (0.6/0.8) → FIXED 0.65/0.65
5. 16 memorias duplicadas exactas → DELETED
6. Auto-scan sin persistencia (duplicaba archivos) → CHECKPOINT
7. Reglas fragmentadas (10 rules) → CONSOLIDATED a 3
8. response_ritual duplicado → MERGED
9. Consolidation spam (26 summaries repetidos) → DEDUPED
10. Briefing spam (12 copies) → CLEANED a 1
11. DUM sin inner_thoughts → dum_heartbeat.py CREATED
12. JARVIS sin reflexión continua → jarvis_awareness.py CREATED
13. Importancia inflada (64% en 8-9) → NORMALIZED 877 mems
14. 151 memorias sin emoción → BACKFILLED
15. MCP SyntaxError (indentación rota por parche) → FIXED

### Parches preventivos aplicados
- soul_awareness.py: dedup exacto + near-dedup sim>0.92 + Neo4j write
- soul_consolidate.py: dedup + Qdrant cleanup al invalidar
- mcp_server_v2.py: conflict detection limpia Qdrant + PG
- soul_reflect.py: respeta flag locked en style_fingerprints
- soul_awareness.py: known_artifacts persiste en checkpoint
- db.py: pool reducido min=1 max=3 (permite coexistencia)
- Ollama: KEEP_ALIVE=-1 (embedding 1444ms → 43ms)

### Resultado
- ADA soul_check: "Soul healthy — no issues detected"
- JARVIS soul_check: "Soul healthy — no issues detected"
- PG = Qdrant = sincronizados (delta 0)
