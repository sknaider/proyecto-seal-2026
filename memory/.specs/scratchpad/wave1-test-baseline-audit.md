# SOUL Memory System — Test Baseline Audit (Wave 1)

**Date:** 2026-04-08
**Author:** JARVIS
**Purpose:** Establish safety net before modularization

## Summary

| Category | Count | % |
|---|---|---|
| **Total MCP tools** | **74** | 100% |
| **Covered** | **15** | 20% |
| **Partial** | **8** | 11% |
| **Uncovered** | **51** | 69% |

## Coverage by Module

| Module | Tools | Covered | Partial | Uncovered |
|---|---|---|---|---|
| memory | 18 | 5 | 4 | 9 |
| graph/connectome | 10 | 4 | 1 | 6 |
| identity/soul | 10 | 0 | 1 | 9 |
| instincts | 7 | 0 | 1 | 6 |
| sessions | 8 | 0 | 0 | 8 |
| procedures | 3 | 0 | 1 | 2 |
| rules | 2 | 0 | 0 | 2 |
| dmem | 2 | 0 | 0 | 2 |
| reasoning traces | 3 | 0 | 1 | 2 |
| sleep | 2 | 2 | 0 | 0 |
| peers | 2 | 1 | 1 | 0 |
| reflection | 2 | 1 | 0 | 1 |

## Covered Tools (15)

| Tool | Test(s) |
|---|---|
| `sleep_gate` | test_sleep_gate_dry_run, test_sleep_gate_forget_emotional_resistance, test_sleepgate_composite_resistance, test_sleepgate_error_isolation |
| `sleep_gate_mood_retrieval` | test_mood_retrieval |
| `connectome_entity` | test_entity_extraction |
| `connectome_entity_query` | test_entity_query_neo4j |
| `connectome_bitemporal` | test_bitemporal_edges |
| `connectome_bitemporal_query` | test_bitemporal_edges |
| `memory_cross_search` | test_cross_search |
| `memory_share_promote` | test_share_promote |
| `brain_health_report` | test_brain_health |
| `memory_hybrid_search` | test_hybrid_search_mood_weight |
| `memory_utility_update` | test_rl_utility_bounds |
| `memory_store` | test_vector_null_fix, test_qdrant_pg_sync |
| `procedure_search` | test_procedures_have_embeddings |
| `reflection_synthesize` | test_reflection_synthesize |
| `peer_model_update/query` | test_peer_model_table |

## Partially Covered (8)

| Tool | What's Tested | What's Missing |
|---|---|---|
| memory_store | Null-fix + sync count | Full store+embed+graph pipeline |
| memory_hybrid_search | Weight math | Live semantic query with embeddings |
| connectome_build | Edge count indirectly | Build pipeline validation |
| ocean_auto_calibrate | Freshness check | Calibration trigger + verify |
| reasoning_trace_store | DB count | Write + retrieve cycle |
| instinct_activate | Internal helper path | MCP-level activation |
| procedure_search | Embedding coverage | Semantic search query |
| peer_model_query | Schema check | Actual data query |

## Completely Uncovered (51 tools)

### Sessions (8) — CRITICAL GAP
session_save, session_recall, session_list, session_distill, session_distill_bulk, working_state_get, working_state_update, event_log_query/event_log_append

### Identity/Soul (9) — HIGH RISK
boot_context, inner_thoughts, self_reflect, soul_check, soul_snapshot, soul_activate, soul_synthesize, ocean_state_machine, observation_analyze, secret_scan

### Memory (9)
memory_list, memory_update, memory_invalidate, memory_feedback, memory_prefetch, memory_flare, memory_delta_sync, memory_broadcast_read, memory_broadcast_ack, memory_communities, active_recall, ace_curator, microcompact_text, microcompact_stats

### Instincts (6)
instinct_create, instinct_list, instinct_search, instinct_activate, instinct_promote, instinct_evolve, instinct_consolidate

### Graph (6)
connectome_build, connectome_status, connectome_causal, connectome_smart_route, connectome_ltp, connectome_invalidate_edge, temporal_graph_build, temporal_query

### Rules (2)
rule_list, rule_set

### D-MEM (2)
dmem_store, dmem_gate

### Procedures (2)
procedure_store, procedure_update

### Reasoning Traces (2)
reasoning_trace_search, reasoning_trace_update

## Key Observations

1. **Sessions module completely untested** — critical for agent continuity
2. **Instincts module zero direct tests** — complex 7-tool subsystem
3. **Tests skew toward data-layer validation** — SQL/math, not MCP interface
4. **Sleep module best covered** — 2/2 tools, 4 test functions
5. **Identity module (boot_context, soul_*) untested** — highest risk for modularization
6. **ada_boot_test.py is infrastructure health, not functional tests**

## Prioritized Test Writing Plan (for Wave 1)

### P0 — Must have before modularization
1. `boot_context` (identity module keystone — ADA's Step 9)
2. `memory_store` full pipeline (memory module keystone — ADA's Step 13)
3. `soul_snapshot` + `soul_check` (identity validation)
4. `instinct_list` + `instinct_activate` (instincts module baseline)
5. `session_save` + `session_recall` (sessions module baseline)

### P1 — Should have before Wave 5
6. `connectome_build` + `connectome_status`
7. `ocean_auto_calibrate` functional test
8. `rule_list` + `rule_set`
9. `memory_list` + `memory_invalidate`

### P2 — Nice to have
10. All remaining tools get at minimum a smoke test
