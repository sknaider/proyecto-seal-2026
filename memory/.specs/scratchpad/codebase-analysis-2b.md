# SOUL Memory System — Codebase Analysis for Productization
**Date:** 2026-04-07  
**Analyst:** Claude Code (Sonnet 4.6)  
**Target:** mcp_server_v2.py + support files  
**Purpose:** Module split planning, dependency mapping, risk assessment

---

## 1. File Structure Map

```
memory/
├── mcp_server_v2.py         # MAIN: 7,478 lines, 296KB — the monolith
├── db.py                    # PostgreSQL connection pool (asyncpg)
├── embeddings.py            # SentenceTransformer wrapper (multilingual-e5-base)
├── session_memory.py        # Session persistence helpers (imported by mcp_server_v2)
├── soul_lite_adapter.py     # PgVectorAdapter — Qdrant fallback via pgvector
├── secret_scanner.py        # Secret detection (imported inline in memory_store)
├── system3_narrative.py     # Narrative identity generator (imported in boot_context)
│
├── instinct_cron.py         # Cron: instinct decay background job
├── sleep_gate_cron.py       # Cron: nightly sleep consolidation
├── soul_awareness.py        # Cron: SOUL awareness heartbeat
├── soul_diagnostic_cron.py  # Cron: diagnostic monitoring
├── soul_consolidate.py      # Memory consolidation script
├── jarvis_awareness.py      # JARVIS-specific awareness
├── dum_heartbeat.py         # DUM guard heartbeat
│
├── connectome.py            # Connectome helpers (standalone scripts)
├── sync_connectome.py       # Connectome sync
├── ocean_protect.py         # OCEAN drift protection
├── memory_consolidation_v2.py  # Consolidation v2
├── microcompact.py          # Microcompact module
├── emotional_variance.py    # Emotional variance tracking
│
├── ada_boot_test.py         # Boot self-test (10-point health check)
├── test_new_tools.py        # Integration tests (live DB, non-destructive)
├── identity_probe.py        # Identity probe utility
├── seal_schema_check.py     # Schema validation
│
├── scripts/
│   ├── repair_baseline.py   # Data repair
│   ├── reembed_missing.py   # Reembedding utility
│   └── repair_verify.py     # Verification
│
├── mcp_server.py            # v1 (active? unclear)
├── mcp_server_v1_backup.py  # v1 backup
└── .specs/                  # This file lives here
```

### Key support files imported by mcp_server_v2.py:
| Import | Source | Role |
|--------|--------|------|
| `from db import get_pool, close_pool` | db.py | PostgreSQL pool |
| `from embeddings import get_embedding` | embeddings.py | Vector generation |
| `from session_memory import ...` | session_memory.py | Session CRUD |
| `from soul_lite_adapter import PgVectorAdapter` | soul_lite_adapter.py | Qdrant fallback |
| `from secret_scanner import scan_text` | secret_scanner.py | Secret blocking |
| `from system3_narrative import generate_narrative_identity` | system3_narrative.py | Narrative ID |

---

## 2. Tool Categorization (75 tools total)

Counted from `@mcp.tool()` decorators: **74 confirmed tool definitions** at lines:
568, 874, 926, 955, 1093, 1145, 1208, 1303, 1435, 1579, 1688, 1756, 2064, 2091, 2115, 2145, 2188, 2222, 2260, 2331, 2423, 2685, 2756, 2781, 2842, 2913, 2966, 2984, 3032, 3188, 3271, 3299, 3316, 3375, 3420, 3498, 3565, 3625, 3804, 3913, 4008, 4081, 4128, 4198, 4284, 4309, 4390, 4557, 4726, 4834, 4966, 5113, 5278, 5323, 5540, 5622, 5686, 5762, 5867, 5929, 5963, 6134, 6270, 6338, 6438, 6652, 6744, 6816, 6895, 6959, 6992, 7107, 7228, 7333, 7426

### Module A — Memory CRUD (8 tools)
| Tool | Line | DB |
|------|------|----|
| `memory_store` | 568 | PG + Qdrant + Neo4j |
| `memory_search` | 955 | Qdrant + PG |
| `memory_list` | 1093 | Qdrant |
| `memory_update` | 1208 | PG + Qdrant + Neo4j |
| `memory_invalidate` | 2842 | PG + Qdrant + Neo4j |
| `memory_feedback` | 4008 | PG + Qdrant |
| `memory_utility_update` | 1145 | PG |
| `memory_broadcast_read` | 874 | PG |
| `memory_broadcast_ack` | 926 | PG |

### Module B — Memory Retrieval / Search (7 tools)
| Tool | Line | DB |
|------|------|----|
| `memory_hybrid_search` | 2423 | Qdrant + PG |
| `memory_cross_search` | 6895 | Qdrant + PG |
| `memory_share_promote` | 6959 | PG + Qdrant |
| `memory_flare` | 4726 | Neo4j + Qdrant |
| `memory_communities` | 4834 | Neo4j + PG |
| `memory_prefetch` | 4966 | PG + Qdrant |
| `memory_delta_sync` | 5278 | PG |

### Module C — Connectome / Graph (12 tools)
| Tool | Line | DB |
|------|------|----|
| `connectome_build` | 1579 | Qdrant + Neo4j |
| `connectome_status` | 1688 | Neo4j |
| `soul_activate` | 1303 | Qdrant + Neo4j |
| `soul_synthesize` | 1435 | Qdrant + Neo4j + Ollama |
| `connectome_ltp` | 5540 | Neo4j + PG |
| `connectome_bitemporal` | 5867 | Neo4j |
| `connectome_bitemporal_query` | 7228 | Neo4j + PG |
| `connectome_invalidate_edge` | 5929 | Neo4j |
| `connectome_causal` | 5963 | Neo4j + Qdrant + PG |
| `connectome_entity` | 6744 | Neo4j + PG |
| `connectome_entity_query` | 6816 | Neo4j |
| `connectome_smart_route` | 7107 | Neo4j + Qdrant + PG |

### Module D — Soul / Identity (8 tools)
| Tool | Line | DB |
|------|------|----|
| `boot_context` | 1756 | PG + Neo4j + Qdrant + Ollama |
| `soul_check` | 2331 | PG |
| `soul_snapshot` | 2260 | PG |
| `self_reflect` | 2188 | PG + Ollama |
| `inner_thoughts` | 2222 | PG |
| `ocean_auto_calibrate` | 5762 | PG |
| `ocean_state_machine` | 6134 | PG |
| `active_recall` | 5113 | Qdrant + PG |

### Module E — Sessions (5 tools)
| Tool | Line | DB |
|------|------|----|
| `session_save` | 2913 | PG (via session_memory.py) |
| `session_recall` | 2966 | PG |
| `session_list` | 2984 | PG |
| `session_distill` | 3032 | PG + Qdrant + Ollama |
| `session_distill_bulk` | 3188 | PG + Qdrant + Ollama |

### Module F — Instincts (8 tools)
| Tool | Line | DB |
|------|------|----|
| `instinct_create` | 3375 | PG |
| `instinct_activate` | 3420 | PG |
| `instinct_search` | 3498 | PG (pgvector) |
| `instinct_list` | 3565 | PG |
| `instinct_consolidate` | 3804 | PG |
| `instinct_promote` | 3913 | PG |
| `instinct_evolve` | 4557 | PG |
| `observation_analyze` | 4390 | PG + Ollama |

### Module G — Temporal Graph (3 tools)
| Tool | Line | DB |
|------|------|----|
| `temporal_graph_build` | 5622 | PG + Neo4j |
| `temporal_query` | 5686 | Neo4j + Ollama |
| `connectome_bitemporal` | 5867 | Neo4j |

### Module H — Reasoning / Procedures (6 tools)
| Tool | Line | DB |
|------|------|----|
| `reasoning_trace_store` | 2685 | PG + Qdrant |
| `reasoning_trace_update` | 2756 | PG |
| `reasoning_trace_search` | 2781 | PG (pgvector) |
| `procedure_store` | 4081 | PG |
| `procedure_search` | 4128 | PG (pgvector) |
| `procedure_update` | 4198 | PG |

### Module I — D-MEM / ACE / Advanced (5 tools)
| Tool | Line | DB |
|------|------|----|
| `dmem_gate` | 6270 | Qdrant |
| `dmem_store` | 6338 | Qdrant + PG + Neo4j |
| `ace_curator` | 5323 | PG + Neo4j + Qdrant + Ollama |
| `memory_cross_search` | 6895 | Qdrant + PG |
| `brain_health_report` | 6992 | PG + Neo4j + Qdrant |

### Module J — Sleep / Consolidation (3 tools)
| Tool | Line | DB |
|------|------|----|
| `sleep_gate` | 6438 | PG + (numpy) |
| `sleep_gate_mood_retrieval` | 6652 | PG |
| `microcompact_text` | 3271 | Ollama |
| `microcompact_stats` | 3299 | PG |

### Module K — Rules / Events (4 tools)
| Tool | Line | DB |
|------|------|----|
| `rule_set` | 2064 | PG |
| `rule_list` | 2091 | PG |
| `event_log_append` | 2115 | PG |
| `event_log_query` | 2145 | PG |

### Module L — Working State / Util (4 tools)
| Tool | Line | DB |
|------|------|----|
| `working_state_get` | 4284 | PG |
| `working_state_update` | 4309 | PG |
| `secret_scan` | 3316 | — (pure computation) |
| `_reflexion_lesson` | 3625 | PG + Ollama |

### Module M — Peer Models / Social (2 tools)
| Tool | Line | DB |
|------|------|----|
| `peer_model_update` | 7333 | PG |
| `peer_model_query` | 7426 | PG |

---

## 3. Database Dependency Matrix

| Tool Module | PostgreSQL | Qdrant | Neo4j | Ollama | pgvector |
|-------------|-----------|--------|-------|--------|----------|
| A — Memory CRUD | YES (source of truth) | YES (vectors) | YES (graph) | YES (enrichment, emotion) | NO |
| B — Retrieval | YES | YES | partial | YES (rerank) | YES (BM25) |
| C — Connectome | partial | YES (seeds) | YES (primary) | YES (synthesize) | NO |
| D — Identity | YES (primary) | partial | partial | YES (reflect) | NO |
| E — Sessions | YES (primary) | YES (distill) | NO | YES (distill) | NO |
| F — Instincts | YES (primary) | NO | NO | YES (evolve, analyze) | YES (similarity) |
| G — Temporal | YES (source) | NO | YES (primary) | YES (summarize) | NO |
| H — Reasoning/Proc | YES (primary) | YES (trace) | NO | NO | YES (similarity) |
| I — D-MEM/ACE | YES | YES | YES | YES | NO |
| J — Sleep/Consolidation | YES (primary) | partial | NO | YES (microcompact) | YES (consolidate) |
| K — Rules/Events | YES (primary) | NO | NO | NO | NO |
| L — Working State | YES (primary) | NO | NO | YES (reflexion) | NO |
| M — Peer Models | YES (primary) | NO | NO | NO | NO |

### Connection initialization:
- **PostgreSQL pool**: `get_pool()` in `db.py` → singleton `asyncpg.Pool(min=1, max=3)`
- **Qdrant client**: `get_qdrant()` in mcp_server_v2.py → `_qdrant` global (lazy init) OR `_qdrant_lite` (SOUL_LITE mode)
- **Neo4j driver**: `get_neo4j()` in mcp_server_v2.py → `_neo4j_driver` global (lazy init, NOT async)

---

## 4. Configuration Audit

### Hardcoded values (HIGH RISK for productization):
```python
# db.py line 7 — DB credentials hardcoded
DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

# mcp_server_v2.py lines 44-49 — all service URLs hardcoded
OLLAMA_GEN_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"
QDRANT_URL = "http://localhost:6333"
QDRANT_COLLECTION = "soul_memories"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "seal2026soul")  # PASSWORD IN PLAINTEXT
```

### Environment variables (partial):
```python
# line 55 — only one env var used
SOUL_LITE = os.environ.get("SOUL_LITE", "false").lower() in ("true", "1", "yes")
```

### Filesystem path hardcoded (inside boot_context):
```python
# line 1962 — absolute path
briefing_path = Path("/home/dadito/IA/proyecto-seal/morning_briefing.txt")
```

### Embedding model hardcoded (embeddings.py line 24):
```python
MODEL_NAME = "intfloat/multilingual-e5-base"  # 768 dims
CACHE_DIR = os.path.expanduser("~/IA/cache/huggingface")
```

### sleep_gate_mood_retrieval — loads embedding model inline (line 6693):
```python
# DUPLICATE: loads SentenceTransformer again instead of using get_embedding()
embed_model = SentenceTransformer("intfloat/multilingual-e5-base")
```

---

## 5. Shared State Inventory

### Global variables (module-level state):
```python
# Database clients — singletons
_qdrant: AsyncQdrantClient | None = None      # mcp_server_v2.py line 323
_qdrant_lite = None                           # mcp_server_v2.py line 324
_neo4j_driver = None                          # mcp_server_v2.py line 325

# In db.py
_pool: asyncpg.Pool | None = None

# In embeddings.py
_model: SentenceTransformer | None = None

# OCEAN session caps — per-agent accumulated deltas
_ocean_session_deltas: dict[str, dict[str, float]] = {}  # line 364

# Observation queue — in-memory buffer
_OBSERVE_QUEUE: list = []  # line 145

# MCP instance — single FastMCP server
mcp = FastMCP("seal-memory", ...)  # line 41

# Decorated tool wrapper
_original_mcp_tool = mcp.tool  # line 178
mcp.tool = _observed_tool       # line 205 — REPLACES decorator
```

### Cross-tool function calls (coupling points):
- `boot_context` calls `soul_activate` and `memory_search` internally (line 1818-1840)
- `connectome_smart_route` calls `temporal_query`, `connectome_entity_query`, `memory_search` internally
- `memory_store` fires `_auto_broadcast`, `_auto_activate_instincts` as background tasks
- `ace_curator` would need `instinct_create` and `connectome_ltp` if not dry_run
- `instinct_activate` calls `_reflexion_lesson` fire-and-forget

### Side effects on `memory_store` (the most complex tool, ~300 lines):
1. D-MEM gate check (Qdrant)
2. A-MEM enrichment via Ollama (optional)
3. Embedding generation (sentence-transformers)
4. Conflict detection (Qdrant)
5. Emotion classification (Ollama)
6. INSERT into `memories` table (PG)
7. UPSERT into Qdrant
8. MERGE node in Neo4j
9. Incremental connectome edges (Neo4j)
10. Entity extraction + MENTIONS edges (Neo4j)
11. OCEAN dynamic update (PG)
12. Relationship auto-update (PG)
13. Episode context generation (Ollama, if imp >= 6)
14. Auto-broadcast (PG, if imp >= 8)
15. Auto-activate instincts (PG, fire-and-forget)

---

## 6. Key Risks for Modularization

### Risk 1 — Cross-tool function calls (HIGH)
`boot_context` directly calls `soul_activate()` and `memory_search()` as Python functions.
`connectome_smart_route` calls `temporal_query()`, `connectome_entity_query()`, `memory_search()`.
Splitting into separate files creates circular import risk unless shared functions are extracted to a common `core.py`.

### Risk 2 — `mcp.tool` decorator replacement (HIGH)
Line 205: `mcp.tool = _observed_tool` replaces the decorator globally BEFORE any `@mcp.tool()` calls.
If tools are split across files, each file importing `mcp` must do so AFTER the replacement.
Import order is critical and fragile.

### Risk 3 — Global singleton state (MEDIUM)
`_qdrant`, `_neo4j_driver` in mcp_server_v2.py; `_pool` in db.py; `_model` in embeddings.py.
These are lazily initialized and safe for a single process. Multi-process split would break shared state.
For productization: must expose connection factories, not module-level globals.

### Risk 4 — `_ocean_session_deltas` dict (MEDIUM)
Accumulates OCEAN drift deltas in-memory per agent per session.
If the process restarts mid-session, the cap resets (sessions could exceed ±0.05/session limit).
For a stateless API: must move to Redis or PG-backed session state.

### Risk 5 — Hardcoded credentials (HIGH for production)
`DB_URL`, `NEO4J_AUTH`, and all service URLs are hardcoded literals.
Must be replaced with `os.environ.get()` calls before any multi-tenant or cloud deployment.
The Neo4j password `seal2026soul` is especially visible.

### Risk 6 — Duplicate embedding loading (LOW-MEDIUM)
`sleep_gate_mood_retrieval` (line 6693) loads `SentenceTransformer` directly instead of calling `get_embedding()`.
Will use double memory if both are active. Easy to fix but indicates the codebase grew organically.

### Risk 7 — `_reflexion_lesson` decorated as `@mcp.tool()` (MEDIUM)
Line 3625: `_reflexion_lesson` has `@mcp.tool()` but its signature takes `pool` as first arg (not a standard MCP tool pattern). It's called fire-and-forget internally. Exposing it as an MCP tool is confusing — it's effectively a private helper. Will cause issues if a client tries to call it expecting standard behavior.

### Risk 8 — `numpy` import inside `sleep_gate` (LOW)
Line 6466: `import numpy as np` inside the function body. Not at module level. Works but unusual — means numpy doesn't fail-fast at import.

### Risk 9 — `session_memory.py` abstraction (LOW)
Already extracted to its own module. This is the positive pattern to follow for the other modules.

### Risk 10 — No async startup/teardown hooks (MEDIUM)
`close_pool()` exists in `db.py` but there's no lifecycle hook in mcp_server_v2.py that calls it on shutdown. Long-running processes could leak connections.

---

## 7. Estimated Effort for Module Splits

| Module | Tools | Complexity | Key Challenge | Effort |
|--------|-------|-----------|---------------|--------|
| A — Memory CRUD | 9 | Very High | memory_store has 15 side effects, cross-calls | **HARD** |
| B — Retrieval | 7 | High | Qdrant + PG hybrid, RL utility updates | **MEDIUM-HARD** |
| C — Connectome | 12 | High | Neo4j session management, cross-calls | **HARD** |
| D — Identity / Soul | 8 | Medium | boot_context calls A+C internally | **MEDIUM** |
| E — Sessions | 5 | Low | Already delegates to session_memory.py | **EASY** |
| F — Instincts | 8 | Medium | pgvector similarity, _reflexion_lesson confusion | **MEDIUM** |
| G — Temporal | 3 | Medium | Neo4j only, well isolated | **EASY-MEDIUM** |
| H — Reasoning/Proc | 6 | Low | PG + pgvector, no cross-calls | **EASY** |
| I — D-MEM/ACE | 5 | High | ACE calls other modules, D-MEM gates memory_store | **HARD** |
| J — Sleep/Consolidation | 4 | Medium | numpy dep, Ollama, emotional resistance | **MEDIUM** |
| K — Rules/Events | 4 | Very Low | Pure PG CRUD | **EASY** |
| L — Working State | 4 | Low | PG only, _reflexion_lesson placement issue | **EASY** |
| M — Peer Models | 2 | Very Low | Pure PG, dynamic table check | **EASY** |

### Recommended split order (lowest risk first):
1. **K — Rules/Events** — pure PG, zero dependencies
2. **M — Peer Models** — 2 tools, pure PG
3. **H — Reasoning/Procedures** — PG + pgvector, no cross-tool calls
4. **L — Working State** — PG only (move _reflexion_lesson out of @mcp.tool)
5. **G — Temporal** — Neo4j-only, well-contained
6. **E — Sessions** — already delegated, just move declarations
7. **F — Instincts** — PG + pgvector, fix _reflexion_lesson signature
8. **J — Sleep** — PG + numpy, add numpy to requirements
9. **B — Retrieval** — Qdrant + PG hybrid, extract temporal_decay_score to core
10. **D — Identity** — depends on A+C; boot_context must be last
11. **A — Memory CRUD** — memory_store is the hardest single function
12. **C — Connectome** — Neo4j heavy, cross-calls with A
13. **I — D-MEM/ACE** — orchestrator, touches everything

---

## 8. Prerequisites for Any Split (Must Do First)

1. **Config externalization** — replace all hardcoded URLs/credentials with env vars in a `config.py`
2. **Extract shared helpers** to `core.py`:
   - `temporal_decay_score()`
   - `_extract_entities()`
   - `classify_emotion()`
   - `generate_episode_context()`
   - `update_ocean()`
   - `update_relationships()`
   - `ocean_to_narrative()`
   - `KNOWN_ENTITIES`, `HALF_LIFE_BY_CATEGORY`, `OCEAN_DELTAS` constants
3. **Move `_observe` + `_observed_tool`** to `instrumentation.py` — the decorator replacement `mcp.tool = _observed_tool` must happen before any module imports tools
4. **Fix `_reflexion_lesson`** — either remove `@mcp.tool()` decorator or fix its signature to match MCP protocol
5. **Fix `sleep_gate_mood_retrieval`** — replace inline `SentenceTransformer()` with `get_embedding()` call

---

## 9. SOUL_LITE Mode Assessment

`SOUL_LITE=true` activates `PgVectorAdapter` from `soul_lite_adapter.py`, replacing Qdrant with pgvector.
Tools that call `get_qdrant()` transparently use PgVectorAdapter instead.
Neo4j is NOT covered by SOUL_LITE — connectome tools would still fail without Neo4j.
For productization: SOUL_LITE is a viable single-dependency mode, but ~13 connectome tools need their own fallback or graceful degradation.

---

## 10. Dependency Graph Summary

```
mcp_server_v2.py
    ├── db.py (asyncpg pool)
    ├── embeddings.py (sentence-transformers, CPU)
    ├── session_memory.py (PG sessions)
    ├── soul_lite_adapter.py (optional, SOUL_LITE mode)
    ├── secret_scanner.py (inline import in memory_store)
    └── system3_narrative.py (inline import in boot_context)

External services:
    ├── PostgreSQL:5433 (primary source of truth)
    ├── Qdrant:6333 (vector index, secondary)
    ├── Neo4j:7687 (graph, tertiary)
    └── Ollama:11434 (LLM inference — qwen2.5:7b)
        └── Used by: memory_store (A-MEM), soul_synthesize,
                    session_distill, temporal_query, reflexion_lesson,
                    observation_analyze, ace_curator, instinct_evolve,
                    ocean_auto_calibrate, active_recall (emotion)
```

---

*Generated by automated codebase analysis — 2026-04-07*
