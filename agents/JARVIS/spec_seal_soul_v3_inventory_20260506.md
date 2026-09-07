# Inventario seal-soul-v3 — Pre-eliminación
**Fecha:** 2026-05-06 17:26 Lima  
**Autor:** JARVIS  
**Propósito:** Documentar contenido de seal-soul-v3/ antes de eliminación, per directiva William.

## Resumen
- **Tamaño:** 2.4MB
- **Estado:** Directorio de desarrollo histórico V3 — ya no activo en producción
- **Servidor MCP:** `mcp_server_v3.py` ya eliminado. `soul-v3-mcp.service` ya eliminado.
- **Producción actual:** `mcp_server_v4.py` en :8771 (superset completo de V3)

---

## soul_api/ — MCP Server Modular
**Estado: SUPERSEDIDO** — `mcp_server_v4.py` tiene todos sus 98 tools + 10 adicionales.

| Archivo | Descripción |
|---------|-------------|
| server.py | Servidor FastMCP principal (puerto 8767) |
| tools/active_recall.py | active_recall, inner_thoughts |
| tools/debate_tools.py | debate_start/round/conclude/detect/list |
| tools/erl.py | erl_reflect, erl_inject |
| tools/extended_tools.py | diary, opinion, decision, denial, feedback, monologue |
| tools/gam_tools.py | gam_event_add/query/causal_chain/topic_list/decay/export |
| tools/goals.py | goal_list/set/update |
| tools/instinct_daemon.py | instinct_check/scan/stats/strengthen |
| tools/magma.py | magma_retrieve |
| tools/memory.py | memory_store/hybrid_search/invalidate |
| tools/meta.py | meta_proposal_create/review/apply/list |
| tools/nervios.py | nerves_stimulate/snapshot/reset |
| tools/reflection.py | reasoning_trace_store/update, self_reflect, belief_query/update |
| tools/skills.py | skill_search/propose/vote/import/sandbox |
| tools/soul.py | boot_context, soul_snapshot |
| tools/synthesis.py | reflection_synthesize, session_distill, ocean_update |
| tools/system_tools.py | rule_set/deactivate, event_log, procedure_store/update, instinct_set |
| tools/temporal_graph.py | temporal_graph_build/summary_get/query |
| tools/working_state.py | working_state_get/update/clear |

**Nota:** Estas tools parecían "no migradas" pero sí están en `mcp_server_v4.py` bajo el mismo nombre o via gateways. Ver diff completo en memoria de sesión.

---

## daemons/ — Daemons del Sistema

### ✅ MIGRADOS (equivalente en /proyecto-seal/memory/)
| V3 | Producción |
|----|-----------|
| ada_cli.py | ada_cli.py |
| alice_monitor.py | alice_monitor.py |
| emotional_variance.py | emotional_variance.py |
| jarvis_audit.py | jarvis_audit.py |
| jarvis_awareness.py | jarvis_awareness.py |
| jarvis_local_agent.py | jarvis_local_agent.py |
| jarvis_soul.py | jarvis_soul.py |
| ocean_adaptive_schema.py | ocean_adaptive_schema.py |
| ocean_protect.py | ocean_protect.py |
| seal_bench.py | seal_bench.py |
| seal_heartbeat.py | seal_heartbeat.py |
| seal_nerves.py | seal_nerves.py |
| seal_schema_check.py | seal_schema_check.py |
| secret_scanner.py | secret_scanner.py |
| soul_backup.py | soul_backup.py |
| soul_backup_auto.py | soul_backup_auto.py |
| soul_consolidate.py | soul_consolidate.py |
| soul_diagnostic_cron.py | soul_diagnostic_cron.py |
| soul_maintenance.py | soul_maintenance.py |
| token_benchmark.py | token_benchmark.py |

### ✅ MIGRADOS (nombre diferente en producción)
| V3 | Producción |
|----|-----------|
| seal_causal_graph.py | causal_graph.py |
| seal_circadian.py | circadian.py |
| seal_connectome.py | connectome.py |
| seal_daily_brief_writer.py | daily_brief_writer.py |
| seal_denial_tracker.py | denial_tracker.py |
| seal_embeddings.py | embeddings.py |
| seal_instinct_cron.py | instinct_cron.py |
| seal_latent_graphmem_*.py (5) | latent_graphmem_*.py + latent_graphmem/ |
| seal_memory_lifecycle.py | memory_lifecycle.py |
| seal_pre_sleep_distill.py | pre_sleep_distill.py |
| seal_seed_instincts.py | seed_instincts.py |
| seal_session_capture.py | session_capture_hook.sh |
| seal_session_memory.py | session_memory.py |
| seal_sleep_gate_cron.py | sleep_gate_cron.py |
| seal_soul_awareness.py | soul_awareness.py |
| seal_sync_connectome.py | sync_connectome.py |
| seal_system3_narrative.py | system3_narrative.py |
| seal_tool_attention.py | tool_attention.py |

### ❓ SOLO EN V3 (sin equivalente encontrado)
| Archivo | Descripción probable |
|---------|---------------------|
| seal_dar_router.py | DAR = Denial/Approval Router — routing de decisiones |
| seal_event_bus.py | Bus de eventos interno del sistema |
| seal_ltm_consolidator.py | Consolidador de Long-Term Memory |
| seal_metacog_updater.py | Actualizador de metacognición |
| seal_skill_version_lock.py | Lock de versiones de skills |
| seal_stale_detector.py | Detector de memorias obsoletas |
| seal_session_delta_capture.py | Captura de delta entre sesiones |

---

## hooks/ — Hooks Claude Code
### ✅ MIGRADOS
active_recall_hook.py, memory_extraction_hook.py, post_compact_hook.py, post_tool_hook.py,  
pre_tool_hook.py, stop_hook.py, subagent_start_hook.py, task_completed_hook.py,  
task_created_hook.py, tool_budget_hook.py, tool_result_budget_hook.py

### ❓ SOLO EN V3
- **mar_debate_hook.py** — Hook para debate MAR (Multi-Agent Reasoning)

---

## Otros contenidos
| Directorio | Estado |
|-----------|--------|
| agents/*/launcher.sh | SUPERSEDIDO — cada agente tiene launcher en /proyecto-seal/ |
| config/settings.py | SUPERSEDIDO — configuración en credentials.env + settings actuales |
| db/schema_extension_v3_sprint8.sql | HISTÓRICO — schema ya aplicado a producción |
| scripts/migrate_v2_to_v3.py | HISTÓRICO — migración ya completada |
| soul_studio/api/main.py | SUPERSEDIDO — reemplazado por seal-studio-backend :8800 |
| tests/t01-t09 + test_*.py | HISTÓRICO — test suite V3, puede ser referencia |

---

## Decisión recomendada

**Eliminar sin riesgo:**
- soul_api/ (supersedido por V4)
- hooks/ (todos migrados, excepto mar_debate_hook.py)
- agents/ (launchers obsoletos)
- config/ (supersedido)
- soul_studio/ (supersedido por :8800)
- scripts/migrate_v2_to_v3.py (migración histórica)

**Revisar antes de eliminar (6 daemons únicos):**
- seal_dar_router.py
- seal_event_bus.py
- seal_ltm_consolidator.py
- seal_metacog_updater.py
- seal_skill_version_lock.py
- seal_stale_detector.py
- seal_session_delta_capture.py
- hooks/mar_debate_hook.py

**Conservar como referencia histórica:**
- tests/ (documentación de comportamiento esperado)
- ARCHITECTURE.md
- db/schema_extension_v3_sprint8.sql

---

## Estado post-limpieza (completado hoy)
- [x] soul-v3-mcp.service — eliminado
- [x] mcp_server_v3.py — eliminado (backup en /tmp)
- [x] nexus.sh + nexus_fresh.sh — referencias V3 removidas
- [ ] seal-soul-v3/ directorio — pendiente autorización William
