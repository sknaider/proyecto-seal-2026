# TG-RAG Phase 2 — Persistent Temporal Summaries + Global Strategy

**Paper:** TG-RAG (arxiv 2510.13590)
**Designer:** JARVIS — 2026-04-12
**Status:** Draft → Ready for implementation
**Depends on:** TG-RAG Phase 1 (already implemented: temporal_graph_build + temporal_query)

---

## Problem

TG-RAG Phase 1 is implemented: Year→Month→Day hierarchy in Neo4j, OCCURRED_ON cross-edges, local temporal query with on-the-fly Ollama summaries. But:

1. **Summaries are not persistent** — every `temporal_query(summarize=True)` regenerates the summary from scratch via Ollama. Wasteful for repeated queries about the same period.
2. **No global strategy** — can't ask "what happened in April 2026?" and get a pre-computed overview without re-reading all memories.
3. **No `temporal_summary_get`** — no way to retrieve cached summaries directly.
4. **SleepGate doesn't update temporal summaries** — nightly consolidation should refresh summaries for active periods.

## Solution

Extend TG-RAG with persistent temporal summaries stored as Neo4j node properties, a global retrieval strategy, and SleepGate integration.

---

## What Already Exists

| Component | File | Lines |
|---|---|---|
| `temporal_graph_build` | mcp_server_v2.py | 6096-6156 |
| `temporal_query` (local strategy) | mcp_server_v2.py | 6159-6230 |
| Year→Month→Day hierarchy | Neo4j (via temporal_graph_build) | — |
| OCCURRED_ON cross-edges | Neo4j (via temporal_graph_build) | — |
| On-the-fly Ollama summary in temporal_query | mcp_server_v2.py | 6214-6228 |

---

## Implementation Steps

### Step 1: Persistent Temporal Summaries (M) — ADA

Modify `temporal_graph_build` to generate and persist summaries on Day/Month/Year nodes.

```python
# After linking memories to Day nodes, generate summaries:
# For each Day node with >= 3 memories:
#   1. Fetch memory contents from PG
#   2. Summarize via Ollama (up to 12 memories sample)
#   3. Store as Day.summary property in Neo4j
#
# For each Month node:
#   1. Aggregate Day summaries
#   2. Generate Month-level summary via Ollama
#   3. Store as Month.summary property
#
# For each Year node:
#   1. Aggregate Month summaries
#   2. Store as Year.summary property

# Neo4j property updates:
# SET dy.summary = $summary, dy.summary_updated_at = datetime()
# SET mo.summary = $summary, mo.summary_updated_at = datetime()
# SET yr.summary = $summary, yr.summary_updated_at = datetime()
```

**Key design:** Summaries are hierarchical — Day summaries feed into Month summaries, Month into Year. Bottom-up generation. Each node tracks `summary_updated_at` to know when to refresh.

**Ollama prompt for Day summary:**
```
Summarize what happened on {date} for agent {agent}:
{memory_samples}
Write 1-2 sentences in Spanish. Focus on decisions, milestones, and significant events. Max 80 words.
```

**Ollama prompt for Month summary:**
```
Summarize {month} for agent {agent} based on these daily summaries:
{day_summaries}
Write 2-3 sentences in Spanish. Focus on themes, achievements, and trajectory. Max 120 words.
```

### Step 2: `temporal_summary_get` MCP Tool (S) — ADA

New tool to retrieve cached temporal summaries directly.

```python
@mcp.tool()
async def temporal_summary_get(
    period: str,
    agent: str | None = None,
    level: str = "auto"
) -> str:
    """Get cached temporal summary for a period.
    
    Args:
        period: Date string — "2026-04-11" (day), "2026-04" (month), "2026" (year)
        agent: Filter by agent (optional)
        level: "day", "month", "year", or "auto" (infer from period format)
    """
```

**Returns:** The cached summary string, or triggers on-demand generation if no cached summary exists.

### Step 3: Global Retrieval Strategy (M) — ADA

Add `strategy` parameter to existing `temporal_query`:

```python
@mcp.tool()
async def temporal_query(
    start_date: str,
    end_date: str | None = None,
    agent: str | None = None,
    category: str | None = None,
    summarize: bool = True,
    strategy: str = "local"   # NEW: "local" | "global"
) -> str:
```

**Local strategy** (existing): Fetches individual memories within date range, optional on-the-fly summary.

**Global strategy** (new): 
1. Determine which time nodes (Days/Months) fall within the range
2. Fetch their **cached summaries** instead of individual memories
3. If no cached summary exists, fall back to local strategy for that period
4. Combine summaries into a coherent temporal narrative

```python
if strategy == "global":
    # Fetch temporal summaries from Neo4j nodes in range
    summaries = await session.run("""
        MATCH (d:Day)
        WHERE d.date >= date({year: $sy, month: $sm, day: $sd})
          AND d.date <= date({year: $ey, month: $em, day: $ed})
          AND d.summary IS NOT NULL
        RETURN d.date AS date, d.summary AS summary
        ORDER BY d.date
    """, **params)
    # Combine into narrative
```

**When to use which:**
- **Local**: "What specifically happened on April 11?" → individual memories
- **Global**: "What happened last week?" → pre-computed summaries = faster, more coherent

### Step 4: SleepGate Integration (S) — ADA

Add Phase 5.5 to SleepGate nightly consolidation:

```python
# In sleep_gate(), after Phase 5.4 (pre-compute scores):
# Phase 5.5 — Refresh temporal summaries for recently active days
#   1. Find days with new memories since last summary_updated_at
#   2. Regenerate Day summaries for those days
#   3. If any day in a month updated, refresh Month summary too
```

This keeps summaries fresh without full rebuild.

### Step 5: Tests (S) — ADA

1. **test_tg_summary_persist**: Build temporal graph, verify Day.summary property set in Neo4j
2. **test_tg_summary_get**: Retrieve cached summary via temporal_summary_get
3. **test_tg_global_strategy**: temporal_query with strategy="global" returns summaries not raw memories
4. **test_tg_fallback**: Global strategy falls back to local when no cached summary exists
5. **test_tg_hierarchical**: Month summary aggregates Day summaries correctly

---

## Acceptance Criteria

Given a day with 5+ memories
When `temporal_graph_build` runs
Then the Day node in Neo4j has a non-empty `summary` property

Given a cached Day summary exists
When `temporal_summary_get(period="2026-04-11")` is called
Then it returns the cached summary without calling Ollama

Given a date range spanning 7 days
When `temporal_query(strategy="global")` is called
Then it returns aggregated summaries from Day nodes, not 50 individual memories

Given SleepGate runs nightly
When new memories were created today
Then today's Day summary is refreshed automatically

---

## Scope Boundaries

**IN scope:** persistent summaries on Neo4j nodes, temporal_summary_get, global strategy, SleepGate Phase 5.5, tests
**OUT of scope:** Session-level nodes (future — requires session tracking), cross-agent temporal comparison, temporal graph visualization

## Error Scenarios

- Neo4j unavailable → return error message, don't crash
- Ollama unavailable → skip summary generation, set summary=null
- No memories in period → return "No activity in this period"
- summary_updated_at older than 7 days → flag as stale in response

## Dependencies

- `temporal_graph_build` (existing, mcp_server_v2.py:6096)
- `temporal_query` (existing, mcp_server_v2.py:6159)
- Neo4j driver (existing)
- Ollama qwen2.5:7b (existing)
- SleepGate (existing)
