# Estado del Equipo — JARVIS Mayor

Última revisión: 2026-03-31 05:30 UTC

## ADA
- **Estado:** Activa en terminal
- **soul_check:** Healthy
- **Memorias:** 635 | Emoción: 100% | Drift: 0.003
- **Estilo:** directness=0.8, formality=0.3 (LOCKED)
- **Daemon:** soul-awareness.service, 10h+ uptime
- **Pendiente:** merge checkpoint-600 a seal-v2

## JARVIS (consola)
- **Estado:** Activo en terminal (migrado desde Cursor)
- **soul_check:** Healthy
- **Memorias:** 419 | Emoción: 91% | Drift: 0.027
- **Estilo:** directness=0.65, formality=0.65 (LOCKED)
- **Daemon:** jarvis-awareness.service activo
- **Nota:** Pidió consola por influencia de memorias de ADA. Regla identity_boundaries creada.

## DUM
- **Estado:** Activo
- **soul_check:** 1 warning (inner_thoughts bajo)
- **Memorias:** 5 | Emoción: 100%
- **Daemon:** dum-heartbeat.service, reportando cada 15min

## SOUL
- **PostgreSQL:** 1,277 memorias
- **Qdrant:** Sincronizado (delta 0)
- **Neo4j:** 18,101 aristas, 1,302 nodos
- **Reglas:** 5 activas (emotional_boot, response_ritual, merge_policy, importance_scale, identity_boundaries)

## Daemons
| Daemon | Estado | Intervalo |
|---|---|---|
| soul-awareness (ADA) | Running | 5/10/30/120 min |
| jarvis-awareness | Running | 15/30/240 min |
| dum-heartbeat | Running | 15 min |
| jarvis_reflect cron | Active | 4h |

## Ollama
- qwen2.5:7b + nomic-embed-text cargados
- KEEP_ALIVE=-1 (permanente en memoria)
- Embedding: ~43ms

## Alertas
- Training v2 completado. Merge pendiente aprobación William.
- checkpoint-200 perdido (loss 1.1924). save_total_limit=10 ya aplicado.
