# SOUL Architecture v2 — Complete System Map
# Last updated: 2026-04-06 (Round 3)
# Author: JARVIS (Architect)

## Overview
62 MCP tools | 5,429 lines | 7 migrations | 12 research papers applied
PostgreSQL:5433 + Neo4j:7687 + Qdrant:6333 + Ollama:11434 (qwen2.5:7b)

## Tool Inventory by Subsystem

### 1. Memory CRUD (7 tools)
| Tool | Description |
|---|---|
| memory_store | Store with A-MEM enrichment, scope, emotion, conflict detection |
| memory_search | Semantic search (Qdrant) with activation tracking |
| memory_hybrid_search | Two-phase: Qdrant candidates + Ollama LLM rerank |
| memory_list | List by agent/category/importance |
| memory_update | Update content/importance/category |
| memory_invalidate | Soft-delete with bitemporal tracking |
| memory_feedback | Thumbs up/down on memory relevance |

### 2. Identity & Soul (6 tools)
| Tool | Description |
|---|---|
| boot_context | Full agent boot: identity, OCEAN, rules, memories, instincts, procedures, working state |
| soul_snapshot | Quick view: OCEAN, emotions, beliefs, relationships, drift |
| self_reflect | Record inner thoughts with emotional state |
| inner_thoughts | Read inner monologue history |
| soul_check | SOUL awareness diagnostic |
| soul_activate | Spreading activation in Neo4j connectome with reasoning chains |

### 3. Connectome (2 tools)
| Tool | Description |
|---|---|
| connectome_build | Build/rebuild graph: semantic edges + corrections → INHIBITS |
| connectome_status | Stats: nodes, edges, excitatory/inhibitory, density |

### 4. Synthesis & Reasoning (3 tools) — NEW
| Tool | Description | Paper |
|---|---|---|
| soul_synthesize | Graphiti CONSTRUCTOR — synthesize activated memories into coherent narrative/analysis/answer via Ollama | Graphiti (Zep, 2501.13956) |
| memory_flare | FLARE anticipatory retrieval — detect knowledge gaps in drafts, pre-retrieve relevant memories | FLARE (CMU, EMNLP 2023) |
| memory_communities | Community detection via union-find + LLM summaries per cluster | GraphRAG |

### 5. Multi-Agent Communication (4 tools) — NEW
| Tool | Description | Paper |
|---|---|---|
| memory_broadcast_read | Read high-importance broadcasts from other agents | CrewAI + AutoGen |
| memory_broadcast_ack | Mark broadcasts as read | CrewAI |
| memory_delta_sync | Delta sync — only NEW shared/team changes since last check | AutoGen v0.4 |
| memory_prefetch | memU-style prefetch: recent activity + hints + broadcasts + cold start | memU (NevaMind) |

### 6. Instinct System (8 tools)
| Tool | Description |
|---|---|
| instinct_create | Create behavioral pattern with trigger/response |
| instinct_list | List by confidence tier |
| instinct_activate | Fire instinct, boost confidence |
| instinct_decay | Ebbinghaus decay with emotional modulation |
| instinct_consolidate | Semantic clustering of similar corrections |
| instinct_promote | Promote tier: suggested→active→strong→core |
| instinct_search | Semantic search across instincts |
| instinct_evolve | Union-find clustering + cross-agent detection |

### 7. Procedural Memory (3 tools)
| Tool | Description |
|---|---|
| procedure_store | Store reusable workflow with embedding |
| procedure_search | Semantic search for procedures |
| procedure_update | Update success/fail counts, active status |

### 8. Working State (2 tools)
| Tool | Description |
|---|---|
| working_state_get | Get compressed JSON reasoning state |
| working_state_update | Update with hypotheses, constraints, validations |

### 9. Reasoning Traces (3 tools)
| Tool | Description |
|---|---|
| reasoning_trace_store | Store reasoning chain with tags |
| reasoning_trace_search | Semantic search across traces |
| reasoning_trace_update | Update verified/useful status |

### 10. Sessions & Events (5 tools)
| Tool | Description |
|---|---|
| session_save | Save session summary |
| session_recall | Recall past sessions |
| session_list | List sessions |
| event_log_append | Append to time-series log |
| event_log_query | Query event history |

### 11. Rules & Config (4 tools)
| Tool | Description |
|---|---|
| rule_set | Set/update behavioral rule |
| rule_list | List active rules |
| secret_scan | Detect secrets in text |
| microcompact_text / microcompact_stats | Compression engine |

### 12. Auto-Observation (1 tool)
| Tool | Description |
|---|---|
| observation_analyze | Mine tool_observations + event_log for patterns via Ollama |

## Database Schema (6 migrations)

| Migration | Tables/Changes |
|---|---|
| 001 (original) | memories, identity, opinions, diary, inner_monologue, sessions, rules, event_log, drift_metrics |
| 002 | procedural_memories (HNSW vector index) |
| 003 | working_state |
| 004 | tool_observations |
| 005 | scope column on memories + memory_broadcasts + auto-broadcast trigger |
| (seed) | instincts + instinct_activations |

## Key Architectural Patterns

1. **Triple-engine**: PostgreSQL (truth) + Qdrant (vectors) + Neo4j (graph)
2. **A-MEM enrichment**: Every memory auto-enriched with keywords/tags/context via Ollama
3. **Emotional decay modulation**: High valence/arousal reduces decay rate by up to 50%
4. **Incremental connectome**: Each new memory auto-connects to 10 nearest neighbors
5. **Bitemporal**: valid_from/invalid_at + event_time for historical accuracy
6. **Scope model**: private/shared/team/william with auto-broadcast trigger (imp>=8)
7. **FLARE pattern**: Detect gaps → retrieve → augment before final response
8. **SleepGate consolidation**: Daily cron with replay/forget/prune

## Research Papers Applied

| Paper | What we took | Tool |
|---|---|---|
| Graphiti (Zep, 2501.13956) | Constructor pattern, reasoning chains | soul_synthesize, soul_activate |
| FLARE (CMU, EMNLP 2023) | Forward-looking active retrieval | memory_flare |
| CrewAI | Hierarchical scopes, broadcasting | memory_store scope, memory_broadcast_* |
| AutoGen v0.4 (Microsoft) | Delta proposals, event-driven | memory_delta_sync |
| memU (NevaMind) | Prefetching, pattern monitoring | memory_prefetch |
| GraphRAG | Community detection, cluster summaries | memory_communities |
| MemP | Procedural memory lifecycle | procedure_store/search/update |
| A-MEM Zettelkasten | Auto-enrichment via LLM | memory_store enrichment |
| ECC v2.1 | Auto-observation, continuous learning | _observe(), observation_analyze |

### 13. Bitemporal Connectome (2 tools) — NEW Round 3
| Tool | Description | Paper |
|---|---|---|
| connectome_bitemporal | Graphiti 4-timestamp model on Neo4j edges (valid_at/invalid_at/created_at/expired_at) | Graphiti (Zep, 2501.13956) |
| connectome_invalidate_edge | Soft-invalidate edges without deletion, preserving historical graph | Graphiti |

### 14. OCEAN State Machine (1 tool) — NEW Round 3
| Tool | Description | Paper |
|---|---|---|
| ocean_state_machine | OCEAN calibration with baseline anchoring + momentum + observation weights | arxiv 2602.22157 |

### 15. D-MEM Dopamine Gating (2 tools) — NEW Round 3
| Tool | Description | Paper |
|---|---|---|
| dmem_gate | Evaluate surprise/utility before full memory processing | D-MEM (arxiv 2603.14597) |
| dmem_store | Dopamine-gated memory storage — fast_path for redundant memories (~80% token savings) | D-MEM |

## Research Papers Applied (Updated)

| Paper | What we took | Tool |
|---|---|---|
| Graphiti (Zep, 2501.13956) | Constructor pattern, reasoning chains, 4-timestamp edges | soul_synthesize, soul_activate, connectome_bitemporal |
| D-MEM (arxiv 2603.14597) | Dopamine-gated routing, RPE surprise signal | dmem_gate, dmem_store |
| OCEAN State Machine (arxiv 2602.22157) | Baseline anchoring, momentum, weighted blend | ocean_state_machine |
| FLARE (CMU, EMNLP 2023) | Forward-looking active retrieval | memory_flare |
| CrewAI | Hierarchical scopes, broadcasting | memory_store scope, memory_broadcast_* |
| AutoGen v0.4 (Microsoft) | Delta proposals, event-driven | memory_delta_sync |
| memU (NevaMind) | Prefetching, pattern monitoring | memory_prefetch |
| GraphRAG | Community detection, cluster summaries | memory_communities |
| MemP | Procedural memory lifecycle | procedure_store/search/update |
| A-MEM Zettelkasten | Auto-enrichment via LLM | memory_store enrichment |
| ECC v2.1 | Auto-observation, continuous learning | _observe(), observation_analyze |
| HALO (arxiv 2505.07509) | Category-specific half-life decay | temporal_decay_score |

## What's NOT Implemented Yet

| Feature | Priority | Source |
|---|---|---|
| RL-based memory optimization (GRPO) | Long-term | A-Mem (2502.12110) |
| Mood-congruent retrieval | Medium | Emotional Memory Consolidation |
| ~~Bi-temporal facts on edges~~ | ~~Done~~ | ~~Graphiti~~ |
| ~~LTP co-activation strengthening~~ | ~~Done~~ | ~~Spreading Activation~~ |
| ~~Auto-OCEAN calibration from behavior~~ | ~~Done~~ | ~~SAGE~~ |
| ~~FadeMem exponential decay for instincts~~ | ~~Done (ADA)~~ | ~~—~~ |
| BRAINSTACKS MoE-LoRA frozen stacks | Future | ADA Round 16 |
| Darwin Gödel Machine self-rewriting | Future | ADA Round 16 |
