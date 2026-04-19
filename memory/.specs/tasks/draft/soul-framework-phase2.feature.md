# soul-framework Phase 2 — Advanced Capabilities

## Architecture Overview

Extend soul-framework with 6 advanced features, ordered by value and dependency:

### Implementation Order (10 steps)

| Step | Feature | New Files | Deps | Tests |
|------|---------|-----------|------|-------|
| 1 | Schema: add relevance_score, last_activation, utility_score columns | schema.py update | None | test_backend_sqlite.py |
| 2 | SleepGate class (4-phase consolidation) | consolidation/sleep_gate.py, types.py | Step 1 | test_sleep_gate.py |
| 3 | ProceduralStore (store + search + trie) | procedures/store.py, types.py | embedding | test_procedures.py |
| 4 | QdrantBackend (vector search backend) | backend/qdrant.py | qdrant-client extra | test_qdrant.py |
| 5 | D-MEM Gate (surprise routing) | dmem/gate.py, types.py | Step 4 + embedding | test_dmem.py |
| 6 | Neo4jBackend (graph operations) | graph/neo4j.py, types.py | neo4j extra | test_graph.py |
| 7 | ConnectomeBuilder (entity + similarity edges) | graph/connectome.py | Step 6 | test_connectome.py |
| 8 | Soul class integration (wire all into Soul) | soul.py update | Steps 1-7 | test_soul.py update |
| 9 | pyproject.toml extras (qdrant, graph, all) | pyproject.toml update | None | — |
| 10 | MCP bridge adapter (monolith imports framework) | bridge/adapter.py | All | test_bridge.py |

### Key Design Decisions

1. **SleepGate is PG-only** — no Qdrant/Neo4j needed. Uses pgvector `<=>` for consolidation similarity. SQLite fallback: Python cosine sim.
2. **D-MEM needs Qdrant** — surprise scoring requires fast vector search on recent memories. Optional extra.
3. **Procedures use existing TrieIndex** from trees module for prefix matching + backend for semantic search.
4. **All new features are optional** — Soul works with just SQLite. Advanced features activate when backends are available.
5. **Neo4j connectome is optional extra** — `soul-framework[graph]` adds neo4j driver.

### New Package Structure

```
src/soul_framework/
├── consolidation/
│   ├── __init__.py
│   ├── sleep_gate.py      # SleepGate class (4 phases)
│   └── types.py           # ConsolidationReport, PhaseResult
├── procedures/
│   ├── __init__.py
│   ├── store.py           # ProceduralStore (store/search/trie)
│   └── types.py           # Procedure, ProcedureSearchResult
├── dmem/
│   ├── __init__.py
│   ├── gate.py            # DMemGate (surprise routing)
│   └── types.py           # DMemRoute, DMemResult
├── graph/
│   ├── __init__.py
│   ├── neo4j.py           # Neo4jBackend
│   ├── connectome.py      # ConnectomeBuilder
│   └── types.py           # Entity, Edge, ConnectomeStats
├── backend/
│   ├── qdrant.py          # QdrantBackend (NEW)
│   └── ... (existing)
└── bridge/
    ├── __init__.py
    └── adapter.py          # MCPBridgeAdapter
```
