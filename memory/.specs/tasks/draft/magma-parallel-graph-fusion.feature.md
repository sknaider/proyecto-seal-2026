# MAGMA Parallel Graph Fusion — Multi-Dimensional Retrieval for SOUL

**Paper:** MAGMA (arxiv 2601.03236) — +45.5% reasoning accuracy, -95% tokens, 40% faster
**Designer:** JARVIS — 2026-04-12
**Status:** Draft → Ready for implementation
**Depends on:** MemR3 (done), TG-RAG Phase 2 (done)

---

## Problem

`connectome_smart_route` (mcp_server_v2.py:7815) routes queries to max 2 backends **sequentially** based on intent classification. Results are concatenated as text blocks without deduplication or cross-graph fusion. This means:

1. Only 2 of 4 available graphs are queried per request
2. No parallel execution — each route runs sequentially
3. No deduplication — same memory can appear in multiple route results
4. No fusion — results from different graphs are just concatenated, not synthesized
5. Returns plain text, not structured data (hard for downstream tools like MemR3 to consume)

## Solution

Add `magma_retrieve` — a new MCP tool that traverses all relevant graphs **in parallel** via `asyncio.gather`, deduplicates by memory ID, and fuses results into a compact type-aligned context. Keeps `connectome_smart_route` as-is for backward compatibility.

---

## What Already Exists

| Component | File:Line | Status |
|---|---|---|
| `_classify_intent` (4 intents) | mcp_server_v2.py:7797 | ✅ Works for all 4 |
| `_INTENT_PATTERNS` (temporal/causal/entity/semantic) | mcp_server_v2.py:7780 | ✅ Complete |
| `connectome_smart_route` (2 routes sequential) | mcp_server_v2.py:7815 | ✅ Partial MAGMA |
| `hybrid_search` (semantic backend) | mcp_server_v2.py:2742 | ✅ |
| `temporal_query` (temporal backend) | mcp_server_v2.py:6159 | ✅ + global strategy |
| `temporal_summary_get` (temporal summaries) | mcp_server_v2.py (new) | ✅ TG-RAG Phase 2 |
| Causal traversal (in smart_route) | mcp_server_v2.py:7848-7899 | ✅ Inline |
| Entity traversal (in smart_route) | mcp_server_v2.py:7901-7913 | ✅ Inline |

---

## Architecture

```
                 ┌──────────────────┐
                 │  magma_retrieve   │ ← new MCP tool
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │  Intent Classifier │ ← _classify_intent (existing)
                 │  → views: list    │
                 └────────┬─────────┘
                          │
         ┌────────────────┼────────────────┐────────────────┐
         ▼                ▼                ▼                ▼
    ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌──────────┐
    │Semantic │    │Temporal  │    │Causal   │    │Entity    │
    │(Qdrant) │    │(Neo4j TG)│    │(Neo4j C)│    │(Neo4j E) │
    └────┬────┘    └────┬─────┘    └────┬────┘    └────┬─────┘
         │              │               │              │
         └──────────────┴───────┬───────┴──────────────┘
                                │   asyncio.gather
                       ┌────────▼────────┐
                       │  Fusion Layer   │
                       │  • dedup by ID  │
                       │  • score merge  │
                       │  • type-align   │
                       └────────┬────────┘
                                │
                       ┌────────▼────────┐
                       │  Structured     │
                       │  Response       │
                       └─────────────────┘
```

---

## Implementation Steps

### Step 1: Extract Internal Route Functions (M) — ADA

Refactor the inline route logic from `connectome_smart_route` into reusable async functions that return structured data (not text). These are the 4 graph traversal backends:

```python
async def _magma_semantic(agent: str, query: str, top_k: int) -> list[dict]:
    """Semantic search via hybrid_search internals. Returns [{id, content, score, category, memory_type}]"""

async def _magma_temporal(agent: str, query: str, top_k: int) -> list[dict]:
    """Temporal search via Neo4j Day graph. Returns [{id, content, date, summary}]"""

async def _magma_causal(agent: str, query: str, top_k: int) -> list[dict]:
    """Causal traversal via Neo4j CAUSES/INHIBIT edges. Returns [{id, content, cause_chain}]"""

async def _magma_entity(agent: str, query: str, top_k: int) -> list[dict]:
    """Entity traversal via Neo4j MENTIONS edges. Returns [{id, content, entities}]"""
```

**Key:** Each function returns `list[dict]` with at minimum `{id, content, score}`. Additional fields are graph-specific. The existing smart_route inline code becomes wrappers calling these functions.

### Step 2: `magma_retrieve` MCP Tool (M) — ADA

New tool — the MAGMA multi-graph retrieval entry point.

```python
@mcp.tool()
async def magma_retrieve(
    agent: str,
    query: str,
    top_k: int = 5,
    views: list[str] | None = None,  # None = auto via _classify_intent
    fuse: bool = True                 # False = return per-graph results raw
) -> dict:
    """
    MAGMA Multi-Graph Retrieval — parallel traversal + fusion.
    Extends connectome_smart_route with parallel execution and type-aligned fusion.
    Paper: arxiv 2601.03236
    
    Args:
        agent: Agent name
        query: Natural language query  
        top_k: Results per graph (default 5)
        views: Graph views to query (semantic/temporal/causal/entity). None=auto-detect.
        fuse: Merge results into unified context (default True)
    """
```

**Returns:**
```python
{
    "context": str,           # Fused type-aligned context (for LLM consumption)
    "memories": [             # Deduplicated memory list
        {"id": int, "content": str, "score": float, "sources": ["semantic", "causal"],
         "category": str, "memory_type": str}
    ],
    "views_used": ["semantic", "causal"],
    "stats": {
        "semantic_hits": int,
        "temporal_hits": int, 
        "causal_hits": int,
        "entity_hits": int,
        "total_unique": int,
        "duplicates_merged": int
    }
}
```

### Step 3: Fusion Layer (M) — ADA

The core innovation — merging results from multiple graphs into a coherent context.

```python
async def _magma_fuse(
    query: str,
    graph_results: dict[str, list[dict]],  # {view_name: [memories]}
    strategy: str = "type_aligned"
) -> dict:
    """
    Fuse subgraph results into unified context.
    
    Strategy: type_aligned (default)
    1. Collect all memories across graphs
    2. Dedup by memory ID — if same memory found in multiple graphs, merge scores
    3. Boost score for memories appearing in multiple graphs (cross-graph reinforcement)
    4. Sort by fused score
    5. Format as compact context with source annotations
    """
    all_memories: dict[int, dict] = {}  # id → merged memory
    
    for view_name, memories in graph_results.items():
        for mem in memories:
            mid = mem["id"]
            if mid in all_memories:
                # Cross-graph reinforcement: appeared in multiple views
                existing = all_memories[mid]
                existing["score"] = max(existing["score"], mem.get("score", 0.5))
                existing["sources"].append(view_name)
                existing["cross_graph_boost"] = 1.0 + 0.15 * len(existing["sources"])
            else:
                all_memories[mid] = {
                    **mem,
                    "sources": [view_name],
                    "cross_graph_boost": 1.0
                }
    
    # Apply cross-graph boost
    for mem in all_memories.values():
        mem["fused_score"] = mem["score"] * mem["cross_graph_boost"]
    
    # Sort by fused score, top_k
    ranked = sorted(all_memories.values(), key=lambda m: -m["fused_score"])
    
    # Format type-aligned context
    context_lines = []
    for mem in ranked[:15]:  # cap at 15 for token efficiency
        sources_tag = "+".join(mem["sources"])
        context_lines.append(f"[{sources_tag}] {mem['content'][:300]}")
    
    return {
        "unified_context": "\n".join(context_lines),
        "source_memories": ranked,
        "per_graph_stats": {
            view: len(mems) for view, mems in graph_results.items()
        }
    }
```

**Cross-graph reinforcement:** If a memory appears in both semantic AND causal results, it's likely very relevant. Each additional graph source adds a 15% score boost. This is the key MAGMA insight — multi-dimensional relevance.

### Step 4: Update smart_route (S) — ADA

Update `connectome_smart_route` to use the new `_magma_*` internal functions. Keep the text output format for backward compatibility but use the cleaner internals.

```python
# In connectome_smart_route, replace inline route code with:
# for intent in intents[:2]:
#     if intent == "semantic":
#         results = await _magma_semantic(agent, query, limit)
#         ...format as text...
```

This is a refactor only — same behavior, cleaner code.

### Step 5: Tests (M) — ADA

1. **test_magma_basic**: Query with known intent → returns fused results
2. **test_magma_parallel**: Verify multiple views execute (check stats.semantic_hits + causal_hits > 0)
3. **test_magma_dedup**: Same memory in 2 graphs → appears once with cross_graph_boost > 1.0
4. **test_magma_cross_boost**: Memory in 3 graphs gets 1.3x boost (1.0 + 0.15 * 2)
5. **test_magma_auto_intent**: "por qué" query → causal view selected
6. **test_magma_manual_views**: Explicit views=["semantic","temporal"] → only those queried
7. **test_magma_fuse_false**: fuse=False → raw per-graph results returned
8. **test_magma_empty**: No results in any graph → graceful empty response

---

## Acceptance Criteria

Given a query "¿por qué decidimos usar PostgreSQL?"
When `magma_retrieve` is called
Then both causal and semantic graphs are queried in parallel
And results are fused with cross-graph boost for memories appearing in both

Given a memory found in semantic + causal + entity graphs
When fusion runs
Then its fused_score = original_score * 1.30 (3 sources = 1.0 + 0.15*2)

Given views=["temporal"]
When `magma_retrieve` is called
Then only temporal graph is queried, others skipped

Given `connectome_smart_route` called after refactor
Then behavior is identical to before (backward compatible)

---

## Scope Boundaries

**IN scope:** magma_retrieve, _magma_* internal functions, fusion layer, cross-graph boost, smart_route refactor, tests
**OUT of scope:** LLM-based intent classification (using existing regex patterns), new graph types, schema changes

## Error Scenarios

- Neo4j unavailable → skip causal+entity+temporal, return semantic only
- One graph fails in asyncio.gather → return_exceptions=True, skip failed graph, fuse remaining
- All graphs return empty → return empty context with stats showing 0 hits
- Intent classifier returns no specialized intents → semantic only (same as current fallback)

## Performance Notes

- Parallel execution should reduce latency vs sequential smart_route
- Cross-graph boost naturally promotes multi-dimensional relevance without token cost
- Cap at 15 results in fusion prevents context bloat (paper's -95% tokens)
- Each graph query is independent — no cascading failures
