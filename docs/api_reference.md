# SOUL API Reference

> Auto-generado desde `mcp_server_v2.py` — 2026-04-08 16:07 UTC  
> **76 herramientas MCP** disponibles

## Índice

- [Core Memory](#core-memory) (13 tools)
- [Boot & Identity](#boot--identity) (6 tools)
- [Self-Awareness](#self-awareness) (5 tools)
- [Connectome (Knowledge Graph)](#connectome-knowledge-graph) (12 tools)
- [Instincts](#instincts) (7 tools)
- [D-MEM (Declarative Memory)](#d-mem-declarative-memory) (2 tools)
- [Session & Distillation](#session--distillation) (7 tools)
- [Reasoning & Reflection](#reasoning--reflection) (4 tools)
- [Procedures](#procedures) (3 tools)
- [Multi-Agent](#multi-agent) (6 tools)
- [Rules & Events](#rules--events) (4 tools)
- [Active Recall](#active-recall) (1 tools)
- [System](#system) (6 tools)

---

## Core Memory

### `memory_store`

Store a new memory with auto-generated semantic embedding and conflict detection.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Agent name (ADA, JARVIS, DUM, TEAM)
    category: One of: fact, preference, decision, insight, correction, milestone, pattern, emotion, trust, humor, dynamic
    content: The memory content text
    importance: 1-10 scale (10 = critical, never forget)
    source: Origin: conversation, reflection, consolidation
    metadata: Optional JSON string with extra data
    event_time: ISO timestamp of when the event actually happened (optional, defaults to now). Different from ingestion time (created_at).
    scope: Visibility — private (default), shared (ADA+JARVIS), team (all agents), william (only William)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `category` (str) — **requerido**
- `content` (str) — **requerido**
- `importance` (int) — opcional (default: `5`)
- `source` (str) — opcional (default: `'conversation'`)
- `metadata` (Optional[str]) — opcional (default: `None`)
- `event_time` (Optional[str]) — opcional (default: `None`)
- `scope` (str) — opcional (default: `'private'`)

**Retorna:** `str`

_Línea 540 en mcp_server_v2.py_

---

### `memory_search`

Search memories by semantic similarity using Qdrant with bitemporal filtering.

<details>
<summary>Detalles</summary>

```
Args:
    query: Natural language search query
    agent: Filter by agent name (optional)
    category: Filter by category (optional)
    limit: Max results (default 10)
    include_invalidated: Include memories marked as no longer valid (default false)
    scope_aware: If true (default), also include shared/team memories from other agents
```

</details>

**Parámetros:**

- `query` (str) — **requerido**
- `agent` (Optional[str]) — opcional (default: `None`)
- `category` (Optional[str]) — opcional (default: `None`)
- `limit` (int) — opcional (default: `10`)
- `include_invalidated` (bool) — opcional (default: `False`)
- `scope_aware` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 927 en mcp_server_v2.py_

---

### `memory_list`

List memories filtered by agent, category, or importance.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Filter by agent name (optional)
    category: Filter by category (optional)
    min_importance: Minimum importance level (1-10)
    limit: Max results (default 20)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)
- `category` (Optional[str]) — opcional (default: `None`)
- `min_importance` (int) — opcional (default: `1`)
- `limit` (int) — opcional (default: `20`)

**Retorna:** `str`

_Línea 1065 en mcp_server_v2.py_

---

### `memory_utility_update`

MemRL Bellman update — adjust utility scores based on actual usefulness.

<details>
<summary>Detalles</summary>

```
After a task completes, call this with the memory IDs that were used and
a reward signal (1.0 = very useful, 0.0 = not useful at all).

utility_new = utility_old + alpha * (reward - utility_old)

This lets memories learn their own value over time without touching model weights.
Based on MemRL (arxiv 2601.03192).

Args:
    memory_ids: Comma-separated memory IDs to update (e.g. "102,305,410")
    reward: Reward signal 0.0 to 1.0 (1.0 = memory was useful for the task)
    context: What task/query the memories were used for
    alpha: Learning rate for Bellman update (default 0.3)
```

</details>

**Parámetros:**

- `memory_ids` (str) — **requerido**
- `reward` (float) — **requerido**
- `context` (str) — opcional (default: `''`)
- `alpha` (float) — opcional (default: `0.3`)

**Retorna:** `str`

_Línea 1117 en mcp_server_v2.py_

---

### `memory_update`

Edit a memory in-place without invalidating it. Preserves connectome edges.

<details>
<summary>Detalles</summary>

```
Updates content, regenerates embedding, updates valence/arousal.
Records change in metadata with timestamp and reason.

Args:
    memory_id: The ID of the memory to update
    new_content: The new content text
    reason: Why this memory is being updated
```

</details>

**Parámetros:**

- `memory_id` (int) — **requerido**
- `new_content` (str) — **requerido**
- `reason` (str) — **requerido**

**Retorna:** `str`

_Línea 1180 en mcp_server_v2.py_

---

### `memory_hybrid_search`

Hybrid search combining semantic similarity (Qdrant) + keyword BM25 (PostgreSQL tsvector).

<details>
<summary>Detalles</summary>

```
Optionally modulated by mood-congruent retrieval (REMT, Frontiers 2026).
Optionally uses LLM-based reranking (Phase 2) for functional relevance scoring.

Args:
    query: Natural language search query
    agent: Filter by agent name (optional)
    category: Filter by category (optional)
    limit: Max results (default 10)
    semantic_weight: Weight for semantic similarity (0.0-1.0, default 0.6)
    keyword_weight: Weight for keyword/BM25 match (0.0-1.0, default 0.4)
    mood_weight: Weight for mood-congruent retrieval (0.0-1.0, default 0.0 = off). When > 0, memories with similar emotional valence to current mood rank higher.
    llm_rerank: If true, use Ollama LLM to rerank top candidates by functional relevance (slower but more precise)
```

</details>

**Parámetros:**

- `query` (str) — **requerido**
- `agent` (Optional[str]) — opcional (default: `None`)
- `category` (Optional[str]) — opcional (default: `None`)
- `limit` (int) — opcional (default: `10`)
- `semantic_weight` (float) — opcional (default: `0.6`)
- `keyword_weight` (float) — opcional (default: `0.4`)
- `mood_weight` (float) — opcional (default: `0.0`)
- `llm_rerank` (bool) — opcional (default: `False`)

**Retorna:** `str`

_Línea 2395 en mcp_server_v2.py_

---

### `memory_invalidate`

Mark a memory as no longer valid (bitemporal invalidation).

<details>
<summary>Detalles</summary>

```
Does NOT delete — sets invalid_at timestamp for historical tracking.

Args:
    memory_id: The memory ID to invalidate
    reason: Why this memory is no longer valid (optional, stored in metadata)
```

</details>

**Parámetros:**

- `memory_id` (int) — **requerido**
- `reason` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 2814 en mcp_server_v2.py_

---

### `memory_feedback`

Provide retroactive quality feedback on a memory that was used.

<details>
<summary>Detalles</summary>

```
If the memory led to a good outcome, reinforce it. If bad, degrade it.
Prevents error propagation from experience-following behavior.

Args:
    memory_id: The memory ID that was used
    outcome: Description of what happened when this memory was applied
    success: Did using this memory lead to a good result?
    agent: Agent providing feedback (optional)
```

</details>

**Parámetros:**

- `memory_id` (int) — **requerido**
- `outcome` (str) — **requerido**
- `success` (bool) — **requerido**
- `agent` (str | None) — opcional (default: `None`)

**Retorna:** `str`

_Línea 3980 en mcp_server_v2.py_

---

### `memory_flare`

FLARE — Forward-Looking Active Retrieval.

<details>
<summary>Detalles</summary>

```
Given a draft response, identifies knowledge gaps (low-confidence claims)
and retrieves relevant memories to fill them BEFORE the final response.

Pattern: Generate draft → detect uncertain parts → retrieve → augment.

Args:
    agent: Agent name
    draft_response: Your tentative response (can be incomplete)
    query: Original user query (for context)
    top_k: Max memories to retrieve per gap
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `draft_response` (str) — **requerido**
- `query` (str) — opcional (default: `''`)
- `top_k` (int) — opcional (default: `5`)

**Retorna:** `str`

_Línea 4698 en mcp_server_v2.py_

---

### `memory_prefetch`

Prefetch relevant memories based on recent activity patterns.

<details>
<summary>Detalles</summary>

```
Inspired by memU (NevaMind): monitors patterns and pre-assembles context.
Uses last session's topics + most activated memories to predict needs.

Args:
    agent: Agent name
    session_hints: Optional comma-separated hints about current session topic
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `session_hints` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 4938 en mcp_server_v2.py_

---

### `memory_delta_sync`

Get memory changes since last check — delta sync for multi-agent coordination.

<details>
<summary>Detalles</summary>

```
Instead of re-reading everything, shows only NEW shared/team memories
from other agents since the specified time window.
Inspired by AutoGen v0.4 event-driven architecture.

Args:
    agent: Your agent name
    since_minutes: Look back this many minutes (default 30)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `since_minutes` (int) — opcional (default: `30`)

**Retorna:** `str`

_Línea 5250 en mcp_server_v2.py_

---

### `memory_cross_search`

Search another agent's memories. The corpus callosum of SOUL.

<details>
<summary>Detalles</summary>

```
Enables JARVIS to search ADA's experiences and vice versa.
Only returns memories with scope != 'private' OR importance >= 7
(important memories are always shareable within the team).

Args:
    query: What to search for
    requesting_agent: Who is asking (JARVIS, ADA)
    target_agent: Whose memories to search (default: the other agent)
    limit: Max results
```

</details>

**Parámetros:**

- `query` (str) — **requerido**
- `requesting_agent` (str) — **requerido**
- `target_agent` (Optional[str]) — opcional (default: `None`)
- `limit` (int) — opcional (default: `10`)

**Retorna:** `str`

_Línea 6868 en mcp_server_v2.py_

---

### `memory_share_promote`

Promote a private memory to shared scope so the other agent can access it.

<details>
<summary>Detalles</summary>

```
Use when you learn something the team should know.

Args:
    memory_id: Memory to share
    agent: Agent promoting (must own the memory)
```

</details>

**Parámetros:**

- `memory_id` (int) — **requerido**
- `agent` (str) — **requerido**

**Retorna:** `str`

_Línea 6932 en mcp_server_v2.py_

---

## Boot & Identity

### `soul_activate`

Activate the SOUL CONNECTOME from a query or concept.

<details>
<summary>Detalles</summary>

```
Uses spreading activation via Neo4j to find associated memories.
Excitatory connections spread activation, inhibitory connections dampen it.

Inspired by Drosophila brain model (Shiu et al., 2024).

Args:
    query: Natural language query or concept to activate
    agent: Filter by agent (optional)
    n_seeds: Number of seed memories to start from (default 3)
    max_hops: Max propagation depth (default 3)
```

</details>

**Parámetros:**

- `query` (str) — **requerido**
- `agent` (Optional[str]) — opcional (default: `None`)
- `n_seeds` (int) — opcional (default: `3`)
- `max_hops` (int) — opcional (default: `3`)

**Retorna:** `str`

_Línea 1275 en mcp_server_v2.py_

---

### `soul_synthesize`

Graphiti CONSTRUCTOR — synthesize activated memories into a coherent response.

<details>
<summary>Detalles</summary>

```
Unlike soul_activate (which lists memories), this tool REASONS over them.
Uses spreading activation to find relevant memories, then Ollama synthesizes
them into a unified narrative, analysis, or answer.

Styles: narrative (story), analysis (structured), answer (direct response)

Args:
    query: The question or topic to synthesize about
    agent: Filter by agent (optional)
    n_seeds: Number of seed memories (default 5)
    max_hops: Max propagation depth (default 3)
    style: Output style — narrative, analysis, or answer
```

</details>

**Parámetros:**

- `query` (str) — **requerido**
- `agent` (Optional[str]) — opcional (default: `None`)
- `n_seeds` (int) — opcional (default: `5`)
- `max_hops` (int) — opcional (default: `3`)
- `style` (str) — opcional (default: `'narrative'`)

**Retorna:** `str`

_Línea 1407 en mcp_server_v2.py_

---

### `boot_context`

Get full boot context for an agent starting a new session.

<details>
<summary>Detalles</summary>

```
Returns: identity, philosophy, active rules, top memories, recent events.

Args:
    agent: Agent name (ADA, JARVIS, DUM)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**

**Retorna:** `str`

_Línea 1728 en mcp_server_v2.py_

---

### `soul_check`

Check soul health: was boot_context executed? Are emotions classified? When was last diary?

<details>
<summary>Detalles</summary>

```
Use this at startup or anytime to verify the agent's soul is healthy.

Args:
    agent: Agent name (ADA, JARVIS, DUM)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**

**Retorna:** `str`

_Línea 2303 en mcp_server_v2.py_

---

### `working_state_get`

Get the current working state for an agent.

<details>
<summary>Detalles</summary>

```
The working state is a compressed JSON representing the agent's current reasoning context:
active_hypotheses, discarded_paths, current_constraints, pending_validations, relevant_memory_ids.

Args:
    agent: Agent name (JARVIS, ADA, DUM)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**

**Retorna:** `str`

_Línea 4256 en mcp_server_v2.py_

---

### `working_state_update`

Update the working state for an agent. Only provided fields are updated (merge, not replace).

<details>
<summary>Detalles</summary>

```
Call this after significant reasoning steps to preserve context across turns.

Args:
    agent: Agent name (JARVIS, ADA, DUM)
    active_hypotheses: JSON array of current hypotheses being explored
    discarded_paths: JSON array of approaches already tried and rejected
    current_constraints: JSON array of active constraints/requirements
    pending_validations: JSON array of things that need verification
    relevant_memory_ids: JSON array of memory IDs relevant to current task
    custom_fields: JSON object with any additional key-value pairs to merge
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `active_hypotheses` (Optional[str]) — opcional (default: `None`)
- `discarded_paths` (Optional[str]) — opcional (default: `None`)
- `current_constraints` (Optional[str]) — opcional (default: `None`)
- `pending_validations` (Optional[str]) — opcional (default: `None`)
- `relevant_memory_ids` (Optional[str]) — opcional (default: `None`)
- `custom_fields` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 4281 en mcp_server_v2.py_

---

## Self-Awareness

### `self_reflect`

Record an inner monologue entry — what the agent is thinking/feeling between turns.

<details>
<summary>Detalles</summary>

```
This is private self-reflection, not directed at anyone.

Args:
    agent: Agent name (ADA, JARVIS, DUM)
    thought: The agent's internal thought or reflection
    emotional_state: Current emotional state (e.g. curious, frustrated, proud, uncertain)
    uncertainty: What the agent is uncertain about (optional)
    intention: What the agent intends to do next (optional)
    session_id: Current session ID (optional)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `thought` (str) — **requerido**
- `emotional_state` (str) — opcional (default: `'neutral'`)
- `uncertainty` (Optional[str]) — opcional (default: `None`)
- `intention` (Optional[str]) — opcional (default: `None`)
- `session_id` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 2160 en mcp_server_v2.py_

---

### `inner_thoughts`

Retrieve recent inner monologue entries for an agent.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Agent name (ADA, JARVIS, DUM)
    limit: Max entries to return (default 10)
    session_id: Filter by session (optional)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `limit` (int) — opcional (default: `10`)
- `session_id` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 2194 en mcp_server_v2.py_

---

### `observation_analyze`

Analyze event logs and tool observations to detect behavioral patterns.

<details>
<summary>Detalles</summary>

```
Suggests new instincts based on repeated patterns, corrections, and workflows.
Based on ECC v2.1 Observer pattern: detect corrections, repeated workflows,
error resolutions, and tool preferences.

Args:
    agent: Filter by agent (optional, all agents if empty)
    days: How many days back to analyze (default 7)
    min_frequency: Minimum pattern frequency to report (default 3)
```

</details>

**Parámetros:**

- `agent` (str) — opcional (default: `''`)
- `days` (int) — opcional (default: `7`)
- `min_frequency` (int) — opcional (default: `3`)

**Retorna:** `str`

_Línea 4362 en mcp_server_v2.py_

---

### `ocean_auto_calibrate`

Auto-calibrate OCEAN scores from observed behavior (not manual).

<details>
<summary>Detalles</summary>

```
Signals: C=success_rate, E=sharing_ratio, O=variety, N=valence_variance, A=correction_rate.
Blends 70% old + 30% observed to prevent wild swings.

Args:
    agent: Agent to calibrate
    days: Analysis period (default 7)
    dry_run: Only report, don't apply (default true)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `days` (int) — opcional (default: `7`)
- `dry_run` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 5734 en mcp_server_v2.py_

---

### `ocean_state_machine`

OCEAN calibration with state machine dynamics (arxiv 2602.22157).

<details>
<summary>Detalles</summary>

```
Upgrade over simple 70/30 blending: uses baseline anchoring, momentum,
and observation weights to prevent personality oscillation.

Formula: new = w_baseline*baseline + w_current*current + w_momentum*momentum + w_observed*observed
Weights: baseline=0.15, current=0.50, momentum=0.25, observed=0.10

Args:
    agent: Agent to calibrate
    days: Analysis period (default 7)
    dry_run: Only report, don't apply (default true)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `days` (int) — opcional (default: `7`)
- `dry_run` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 6106 en mcp_server_v2.py_

---

## Connectome (Knowledge Graph)

### `connectome_build`

Build or rebuild the SOUL CONNECTOME in Neo4j.

<details>
<summary>Detalles</summary>

```
Creates edges based on:
1. Semantic similarity (Qdrant search > 0.70) -> EXCITES
2. Corrections -> INHIBITS edges
3. Category-based rules

Args:
    agent: Build only for this agent (optional, builds all if omitted)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 1551 en mcp_server_v2.py_

---

### `connectome_status`

Get statistics about the SOUL CONNECTOME graph in Neo4j.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Filter by agent (optional)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 1660 en mcp_server_v2.py_

---

### `connectome_ltp`

Strengthen Neo4j edges between memories that are frequently co-activated.

<details>
<summary>Detalles</summary>

```
Long-Term Potentiation (LTP): when two memories fire together repeatedly,
the connection between them strengthens — like synapses in the brain.

Args:
    agent: Filter by agent (optional)
    days: Look back period (default 7)
    min_coactivations: Minimum co-activations to strengthen (default 2)
    dry_run: If true, only report (default true)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)
- `days` (int) — opcional (default: `7`)
- `min_coactivations` (int) — opcional (default: `2`)
- `dry_run` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 5512 en mcp_server_v2.py_

---

### `temporal_graph_build`

Build temporal hierarchy in Neo4j: Year→Month→Day, link memories via OCCURRED_ON.

<details>
<summary>Detalles</summary>

```
Enables time-range queries like 'what happened in March 2026?'

Args:
    agent: Only process this agent's memories (optional)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 5594 en mcp_server_v2.py_

---

### `temporal_query`

Query memories by time range via temporal graph. Optionally summarize with Ollama.

<details>
<summary>Detalles</summary>

```
Args:
    start_date: YYYY-MM-DD
    end_date: YYYY-MM-DD (defaults to start_date)
    agent: Filter by agent
    category: Filter by category
    summarize: Generate LLM summary (default true)
```

</details>

**Parámetros:**

- `start_date` (str) — **requerido**
- `end_date` (Optional[str]) — opcional (default: `None`)
- `agent` (Optional[str]) — opcional (default: `None`)
- `category` (Optional[str]) — opcional (default: `None`)
- `summarize` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 5658 en mcp_server_v2.py_

---

### `connectome_bitemporal`

Add Graphiti-style 4-timestamp model to Neo4j edges.

<details>
<summary>Detalles</summary>

```
Adds valid_at/invalid_at properties to edges for bitemporal fact tracking.
Edges can be invalidated without deletion, preserving historical graph state.
Based on Graphiti (Zep, arxiv 2501.13956) 4-timestamp model.

Args:
    agent: Filter by agent (optional)
    dry_run: Only report, don't modify (default true)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)
- `dry_run` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 5839 en mcp_server_v2.py_

---

### `connectome_invalidate_edge`

Invalidate a Neo4j edge without deleting it (Graphiti pattern).

<details>
<summary>Detalles</summary>

```
Sets invalid_at + expired_at timestamps instead of removing the edge.
This preserves historical graph state for temporal queries.

Args:
    source_id: Source memory ID
    target_id: Target memory ID
    reason: Why this edge is being invalidated
```

</details>

**Parámetros:**

- `source_id` (int) — **requerido**
- `target_id` (int) — **requerido**
- `reason` (str) — opcional (default: `''`)

**Retorna:** `str`

_Línea 5901 en mcp_server_v2.py_

---

### `connectome_causal`

Build CAUSES edges in Neo4j connectome — the missing causal graph.

<details>
<summary>Detalles</summary>

```
Based on MAGMA (arxiv 2601.03236): orthogonal causal dimension.
Sources of causality:
1. reasoning_traces with linked_memory_ids → premises CAUSE conclusion
2. corrections → old behavior CAUSED correction
3. temporal sequence → decision within 1h before milestone = potential CAUSES

Args:
    agent: Filter by agent (optional)
    dry_run: Only report, don't create edges (default true)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)
- `dry_run` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 5935 en mcp_server_v2.py_

---

### `connectome_entity`

Build ENTITY dimension of MAGMA: extract named entities from memories

<details>
<summary>Detalles</summary>

```
and create MENTIONS edges in Neo4j.

Creates Entity nodes (person, agent, hardware, model, etc.) and
Memory-[:MENTIONS]->Entity relationships.

Args:
    agent: Agent name (JARVIS, ADA) or 'all' for both
    dry_run: If True, report without creating edges
    batch_size: Memories to process per batch
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `dry_run` (bool) — opcional (default: `True`)
- `batch_size` (int) — opcional (default: `200`)

**Retorna:** `str`

_Línea 6717 en mcp_server_v2.py_

---

### `connectome_entity_query`

Query all memories that mention a specific entity.

<details>
<summary>Detalles</summary>

```
Returns the entity's subgraph: connected memories, co-occurring entities, and relationship types.

Args:
    entity_name: Entity to search for (e.g. 'William', 'ADA', 'RTX_5090')
    limit: Max memories to return
```

</details>

**Parámetros:**

- `entity_name` (str) — **requerido**
- `limit` (int) — opcional (default: `20`)

**Retorna:** `str`

_Línea 6789 en mcp_server_v2.py_

---

### `connectome_smart_route`

MAGMA-style intent-aware routing for memory queries.

<details>
<summary>Detalles</summary>

```
Instead of searching everything everywhere, classifies the query intent
(temporal / causal / entity / semantic) and routes to the optimal backend:
- temporal → temporal_query (Neo4j Year→Month→Day graph)
- causal → connectome_causal relationships (Neo4j CAUSES edges)
- entity → connectome_entity_query (Neo4j MENTIONS subgraph)
- semantic → memory_search (Qdrant vector similarity)

Falls back to semantic if specialized route yields no results.

Args:
    query: Natural language query
    agent: Filter by agent (optional)
    limit: Max results per backend (default 10)
```

</details>

**Parámetros:**

- `query` (str) — **requerido**
- `agent` (Optional[str]) — opcional (default: `None`)
- `limit` (int) — opcional (default: `10`)

**Retorna:** `str`

_Línea 7080 en mcp_server_v2.py_

---

### `connectome_bitemporal_query`

Query the connectome at a specific point in time (bitemporal).

<details>
<summary>Detalles</summary>

```
Returns edges that were valid at the given timestamp, enabling
"what did we know at time T?" queries. Based on Graphiti 4-timestamp model.

Args:
    entity_or_memory: Entity name or memory ID (prefix with # for ID, e.g. '#1234')
    as_of: ISO timestamp to query at (default: now). Shows state of knowledge at that time.
    include_invalidated: Also show edges that were later invalidated (default false)
```

</details>

**Parámetros:**

- `entity_or_memory` (str) — **requerido**
- `as_of` (Optional[str]) — opcional (default: `None`)
- `include_invalidated` (bool) — opcional (default: `False`)

**Retorna:** `str`

_Línea 7201 en mcp_server_v2.py_

---

## Instincts

### `instinct_create`

Create a new instinct from detected behavioral pattern.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Agent name (JARVIS, ADA, DUM)
    trigger_pattern: When this instinct fires (semantic description)
    response: The behavioral response / heuristic
    domain: Domain (general, coding, medical, communication, soul)
    confidence: Initial confidence 0.0–1.0 (default 0.3 = suggested)
    source_memory_ids: Memory IDs that originated this instinct
    source_rule_id: Rule ID if promoted from an explicit rule
    scope: agent, team, or global
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `trigger_pattern` (str) — **requerido**
- `response` (str) — **requerido**
- `domain` (str) — opcional (default: `'general'`)
- `confidence` (float) — opcional (default: `0.3`)
- `source_memory_ids` (list[int] | None) — opcional (default: `None`)
- `source_rule_id` (int | None) — opcional (default: `None`)
- `scope` (str) — opcional (default: `'agent'`)

**Retorna:** `str`

_Línea 3347 en mcp_server_v2.py_

---

### `instinct_activate`

Record that an instinct was activated (fired) during agent behavior.

<details>
<summary>Detalles</summary>

```
Reinforces confidence if outcome=applied, decreases if outcome=corrected.

Args:
    instinct_id: The instinct ID
    agent: Agent name
    context: What triggered the activation
    outcome: applied, suppressed, or corrected
    session_id: Current session ID
```

</details>

**Parámetros:**

- `instinct_id` (int) — **requerido**
- `agent` (str) — **requerido**
- `context` (str) — opcional (default: `''`)
- `outcome` (str) — opcional (default: `'applied'`)
- `session_id` (str | None) — opcional (default: `None`)

**Retorna:** `str`

_Línea 3392 en mcp_server_v2.py_

---

### `instinct_search`

Search instincts by semantic similarity to a query.

<details>
<summary>Detalles</summary>

```
Used during reasoning to find relevant instincts for the current context.

Args:
    agent: Agent name
    query: The current context/situation to match against
    domain: Filter by domain (optional)
    min_confidence: Minimum confidence threshold (default 0.3)
    limit: Max results (default 5)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `query` (str) — **requerido**
- `domain` (str | None) — opcional (default: `None`)
- `min_confidence` (float) — opcional (default: `0.3`)
- `limit` (int) — opcional (default: `5`)

**Retorna:** `str`

_Línea 3470 en mcp_server_v2.py_

---

### `instinct_list`

List all instincts for an agent, ordered by confidence.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Agent name
    min_confidence: Minimum confidence filter
    include_inactive: Include deactivated instincts
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `min_confidence` (float) — opcional (default: `0.0`)
- `include_inactive` (bool) — opcional (default: `False`)

**Retorna:** `str`

_Línea 3537 en mcp_server_v2.py_

---

### `instinct_consolidate`

Analyze memories to detect SEMANTIC clusters that should become instincts.

<details>
<summary>Detalles</summary>

```
Uses pgvector cosine similarity to find correction/pattern clusters —
memories that say similar things even if worded differently.
This is the 'instinct formation' process — experience → reflex.

Args:
    agent: Agent name
    similarity_threshold: Cosine similarity threshold for clustering (default 0.85)
    min_cluster_size: Minimum memories in a cluster (default 2)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `similarity_threshold` (float) — opcional (default: `0.85`)
- `min_cluster_size` (int) — opcional (default: `2`)

**Retorna:** `str`

_Línea 3776 en mcp_server_v2.py_

---

### `instinct_promote`

Analyze instincts for promotion opportunities.

<details>
<summary>Detalles</summary>

```
Two types: (1) cluster promotion — similar instincts merge into stronger ones,
(2) cross-agent promotion — same instinct in 2+ agents → scope: team/global.
Inspired by ECC v2.1 /evolve pipeline.

Args:
    dry_run: If true, only report candidates without making changes (default true)
```

</details>

**Parámetros:**

- `dry_run` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 3885 en mcp_server_v2.py_

---

### `instinct_evolve`

Analyze instinct clusters and suggest evolution into skills, rules, or team behaviors.

<details>
<summary>Detalles</summary>

```
Based on ECC /evolve: groups instincts by semantic similarity, classifies candidates
as skill (2+ similar instincts), rule (workflow domain + high conf), or team behavior
(cross-agent pattern). Does NOT auto-create — returns suggestions for review.

Args:
    agent: Filter by agent (optional, all if empty)
    min_confidence: Minimum confidence to consider (default 0.6)
    dry_run: If true, only report suggestions (default true)
```

</details>

**Parámetros:**

- `agent` (str) — opcional (default: `''`)
- `min_confidence` (float) — opcional (default: `0.6`)
- `dry_run` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 4529 en mcp_server_v2.py_

---

## D-MEM (Declarative Memory)

### `dmem_gate`

D-MEM dopamine-gated routing (arxiv 2603.14597).

<details>
<summary>Detalles</summary>

```
Evaluates if a new memory is worth full processing (embedding + graph + enrichment)
or should take the fast path (store with minimal overhead).

Surprise = 1 - max_cosine_similarity to last 50 agent memories.
Utility = importance / 10.0.
If surprise < threshold AND utility < threshold → fast_path.

Args:
    agent: Agent name
    content: Memory content to evaluate
    threshold_surprise: Below this = not surprising (default 0.3)
    threshold_utility: Below this = low utility (default 0.6)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `content` (str) — **requerido**
- `threshold_surprise` (float) — opcional (default: `0.3`)
- `threshold_utility` (float) — opcional (default: `0.6`)

**Retorna:** `str`

_Línea 6242 en mcp_server_v2.py_

---

### `dmem_store`

D-MEM aware memory storage — gates memories before full processing.

<details>
<summary>Detalles</summary>

```
Checks surprise/utility first. High surprise → full A-MEM enrichment + graph.
Low surprise + low utility → minimal storage (no LLM enrichment, no graph update).
This saves ~80% of tokens on redundant memories (arxiv 2603.14597).

Args:
    agent: Agent name
    category: Memory category
    content: Memory content
    importance: 1-10 scale
    source: Origin
    metadata: Optional JSON
    event_time: ISO timestamp
    scope: private/shared/team/william
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `category` (str) — **requerido**
- `content` (str) — **requerido**
- `importance` (int) — opcional (default: `5`)
- `source` (str) — opcional (default: `'conversation'`)
- `metadata` (Optional[str]) — opcional (default: `None`)
- `event_time` (Optional[str]) — opcional (default: `None`)
- `scope` (str) — opcional (default: `'private'`)

**Retorna:** `str`

_Línea 6310 en mcp_server_v2.py_

---

## Session & Distillation

### `session_save`

Save or update session memory — survives compaction.

<details>
<summary>Detalles</summary>

```
Call every 10-15 turns to maintain session continuity.

Args:
    agent: Agent name (ADA, JARVIS)
    summary: Narrative summary of what happened this session so far
    session_id: Unique session ID (auto-generated if None)
    turn_number: Current turn number
    key_decisions: JSON array of key decisions made (e.g. '["approved SEAL Console arch"]')
    active_tasks: JSON array of active tasks (e.g. '[{"id": "1", "desc": "scaffold", "status": "in_progress"}]')
    pending_items: JSON array of pending items (e.g. '["review ADA code", "GPU check"]')
    errors_active: JSON array of active errors (e.g. '["Neo4j timeout on connectome_build"]')
    services_state: JSON dict of service states (e.g. '{"pg": "ok", "neo4j": "ok", "qdrant": "ok"}')
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `summary` (str) — **requerido**
- `session_id` (Optional[str]) — opcional (default: `None`)
- `turn_number` (int) — opcional (default: `0`)
- `key_decisions` (Optional[Any]) — opcional (default: `None`)
- `active_tasks` (Optional[Any]) — opcional (default: `None`)
- `pending_items` (Optional[Any]) — opcional (default: `None`)
- `errors_active` (Optional[Any]) — opcional (default: `None`)
- `services_state` (Optional[Any]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 2885 en mcp_server_v2.py_

---

### `session_recall`

Recall the most recent session memory for an agent.

<details>
<summary>Detalles</summary>

```
Use at boot or after compaction to recover context.

Args:
    agent: Agent name (ADA, JARVIS)
    session_id: Specific session ID (optional — returns latest if omitted)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `session_id` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 2938 en mcp_server_v2.py_

---

### `session_list`

List recent sessions for an agent with brief summaries.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Agent name (ADA, JARVIS)
    limit: Max sessions to return (default 5)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `limit` (int) — opcional (default: `5`)

**Retorna:** `str`

_Línea 2956 en mcp_server_v2.py_

---

### `session_distill`

Distill a session exchange into compressed form — the hippocampus of SOUL.

<details>
<summary>Detalles</summary>

```
Based on Structured Distillation (arxiv 2603.13017): 11x compression
while preserving 96.8% of searchable vocabulary.

Call this for each significant exchange in a session. The distilled form
is stored in PostgreSQL and optionally embedded in Qdrant for vector search.

Args:
    agent: Agent name (ADA, JARVIS)
    exchange_text: The raw exchange text to compress (user message + assistant response)
    session_id: Session ID (auto-generated if None)
    ply_start: First turn number (optional)
    ply_end: Last turn number (optional)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `exchange_text` (str) — **requerido**
- `session_id` (Optional[str]) — opcional (default: `None`)
- `ply_start` (Optional[int]) — opcional (default: `None`)
- `ply_end` (Optional[int]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 3004 en mcp_server_v2.py_

---

### `session_distill_bulk`

Bulk-distill recent memories from a session into compressed exchanges.

<details>
<summary>Detalles</summary>

```
Reads memories created in the last N hours and groups them into
logical exchanges for distillation. Use this to consolidate an
entire session after it ends.

Args:
    agent: Agent name (ADA, JARVIS)
    session_id: Session ID to tag the distilled exchanges
    hours_back: How many hours back to look (default 24)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `session_id` (str) — **requerido**
- `hours_back` (int) — opcional (default: `24`)

**Retorna:** `str`

_Línea 3160 en mcp_server_v2.py_

---

### `sleep_gate`

Run sleep-like memory consolidation: replay, forget, prune, consolidate.

<details>
<summary>Detalles</summary>

```
Phase 1 — REPLAY: Boost recently activated memories (+replay_boost to relevance).
Phase 2 — FORGET: Decay stale memories not activated in stale_days (*forget_decay).
Phase 3 — PRUNE: Soft-invalidate memories below prune_threshold relevance.
Phase 4 — CONSOLIDATE: Merge near-duplicate memories (similarity > consolidation_similarity).

Args:
    agent: Agent name (JARVIS, ADA)
    dry_run: If True, report what WOULD happen without changing anything
    replay_boost: Relevance boost for recently activated memories (default 0.10 = +10%)
    forget_decay: Decay multiplier for stale memories (default 0.85 = -15%)
    stale_days: Days without activation before memory is considered stale
    prune_threshold: Relevance below this → soft-invalidate (default 0.05)
    consolidation_similarity: Cosine threshold for merging near-duplicates
    max_prune: Safety cap on pruned memories per run
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `dry_run` (bool) — opcional (default: `True`)
- `replay_boost` (float) — opcional (default: `0.1`)
- `forget_decay` (float) — opcional (default: `0.85`)
- `stale_days` (int) — opcional (default: `30`)
- `prune_threshold` (float) — opcional (default: `0.05`)
- `consolidation_similarity` (float) — opcional (default: `0.92`)
- `max_prune` (int) — opcional (default: `50`)

**Retorna:** `str`

_Línea 6410 en mcp_server_v2.py_

---

### `sleep_gate_mood_retrieval`

Search memories weighted by current emotional mood (mood-congruent retrieval).

<details>
<summary>Detalles</summary>

```
Combines semantic similarity with emotional alignment to current mood.
When you're worried, you remember worries. When proud, you recall achievements.
Based on REMT (Frontiers 2026).

Args:
    agent: Agent name
    query: Search query text
    mood_weight: How much mood affects ranking (0.0 = pure semantic, 1.0 = pure mood)
    limit: Max results
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `query` (str) — **requerido**
- `mood_weight` (float) — opcional (default: `0.3`)
- `limit` (int) — opcional (default: `10`)

**Retorna:** `str`

_Línea 6624 en mcp_server_v2.py_

---

## Reasoning & Reflection

### `reasoning_trace_store`

Store a reasoning trace — captures WHY a decision was made, not just WHAT.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Agent name (ADA, JARVIS, DUM)
    task: What was being decided (e.g. "whether to interrupt training")
    premises: JSON array of facts/observations that informed the decision
    reasoning: The chain of thought — how premises led to conclusion
    conclusion: What was decided
    outcome: What actually happened (can be filled later via reasoning_trace_update)
    outcome_success: Did the decision work? (can be filled later)
    linked_memory_ids: Comma-separated memory IDs that informed this trace (e.g. "42,55,103")
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `task` (str) — **requerido**
- `premises` (str) — **requerido**
- `reasoning` (str) — **requerido**
- `conclusion` (str) — **requerido**
- `outcome` (Optional[str]) — opcional (default: `None`)
- `outcome_success` (Optional[bool]) — opcional (default: `None`)
- `linked_memory_ids` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 2657 en mcp_server_v2.py_

---

### `reasoning_trace_update`

Update a reasoning trace with its outcome — did the decision work?

<details>
<summary>Detalles</summary>

```
Args:
    trace_id: The trace ID to update
    outcome: What actually happened
    outcome_success: Did the decision lead to a good result?
```

</details>

**Parámetros:**

- `trace_id` (int) — **requerido**
- `outcome` (str) — **requerido**
- `outcome_success` (bool) — **requerido**

**Retorna:** `str`

_Línea 2728 en mcp_server_v2.py_

---

### `reasoning_trace_search`

Search reasoning traces to learn from past decisions.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Filter by agent name (optional)
    task_query: Search text in task description (optional)
    only_failures: Only show traces where outcome_success = false
    limit: Max results (default 10)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)
- `task_query` (Optional[str]) — opcional (default: `None`)
- `only_failures` (bool) — opcional (default: `False`)
- `limit` (int) — opcional (default: `10`)

**Retorna:** `str`

_Línea 2753 en mcp_server_v2.py_

---

### `reflection_synthesize`

Synthesize high-level beliefs (Mental Models) from episodic memories.

<details>
<summary>Detalles</summary>

```
Hindsight Tier 5 — reflects on past experiences to extract durable beliefs
without LLM calls. Idempotent: re-running with the same topic is safe.

Args:
    agent: Agent whose memories to synthesize (e.g. 'ADA', 'JARVIS')
    topic: Theme or topic to focus synthesis on
    max_memories: Max memories to process (default 20, capped at 50)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `topic` (str) — **requerido**
- `max_memories` (int) — opcional (default: `20`)

**Retorna:** `str`

_Línea 7450 en mcp_server_v2.py_

---

## Procedures

### `procedure_store`

Build phase: store a reusable workflow extracted from a task trajectory.

<details>
<summary>Detalles</summary>

```
Only store workflows from completed tasks. The workflow should be a narrative
paragraph describing HOW to accomplish the task, not just what happened.
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `task_description` (str) — **requerido**
- `workflow` (str) — **requerido**
- `task_type` (str) — opcional (default: `'general'`)
- `facts` (str) — opcional (default: `'{}'`)
- `source_task` (str) — opcional (default: `''`)
- `success` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 4053 en mcp_server_v2.py_

---

### `procedure_search`

Retrieve phase: find relevant procedural memories for the current task.

<details>
<summary>Detalles</summary>

```
Returns workflows ranked by semantic similarity. Updates hit_count on retrieval.
```

</details>

**Parámetros:**

- `query` (str) — **requerido**
- `agent` (str) — opcional (default: `''`)
- `task_type` (str) — opcional (default: `''`)
- `top_k` (int) — opcional (default: `3`)

**Retorna:** `str`

_Línea 4100 en mcp_server_v2.py_

---

### `procedure_update`

Update phase: record outcome and optionally improve a procedural memory.

<details>
<summary>Detalles</summary>

```
If success=false and reflection is provided, the workflow gets rewritten (reflect strategy).
Auto-deactivates procedures with hit >= 3 and success_rate < 50%.
```

</details>

**Parámetros:**

- `procedure_id` (int) — **requerido**
- `success` (bool) — **requerido**
- `reflection` (str) — opcional (default: `''`)
- `new_workflow` (str) — opcional (default: `''`)

**Retorna:** `str`

_Línea 4170 en mcp_server_v2.py_

---

## Multi-Agent

### `memory_broadcast_read`

Read broadcasts — high-importance memories shared by other agents.

<details>
<summary>Detalles</summary>

```
Auto-generated when scope=shared/team + importance>=8.
Use memory_broadcast_ack to mark as read.

Args:
    agent: Your agent name (to filter what YOU haven't read)
    limit: Max broadcasts to return
    unread_only: Only show unread broadcasts (default true)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `limit` (int) — opcional (default: `10`)
- `unread_only` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 846 en mcp_server_v2.py_

---

### `memory_broadcast_ack`

Mark broadcasts as read by this agent.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Your agent name
    broadcast_ids: Comma-separated broadcast IDs to acknowledge (e.g. "1,2,3")
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `broadcast_ids` (str) — **requerido**

**Retorna:** `str`

_Línea 898 en mcp_server_v2.py_

---

### `memory_communities`

Detect memory communities using connected components in Neo4j.

<details>
<summary>Detalles</summary>

```
Groups memories into thematic clusters based on connectome edges.
Optionally generates summaries per community via Ollama.
Inspired by GraphRAG (Louvain community detection).

Args:
    agent: Filter by agent (optional)
    min_community_size: Minimum memories per community (default 3)
    summarize: Generate LLM summaries per community (default true)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)
- `min_community_size` (int) — opcional (default: `3`)
- `summarize` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 4806 en mcp_server_v2.py_

---

### `ace_curator`

ACE Curator — automatically improves agent context from behavioral patterns.

<details>
<summary>Detalles</summary>

```
Analyzes recent observations, corrections, and memory usage to:
1. Create new instincts from repeated patterns
2. Update procedural memories from successful workflows
3. Suggest rule changes based on corrections
4. Strengthen co-activated memory edges (LTP)

Based on ACE (arxiv 2510.04618) Generate-Reflect-Curate cycle.

Args:
    agent: Agent to curate for
    days: Days of history to analyze (default 3)
    dry_run: If true, only report what would change (default true)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `days` (int) — opcional (default: `3`)
- `dry_run` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 5295 en mcp_server_v2.py_

---

### `peer_model_update`

Update an agent's model of another agent (peer model).

<details>
<summary>Detalles</summary>

```
Each agent maintains observations about how their peers behave — patterns,
blind spots, strengths. This enables better collaboration and task routing.

Args:
    observer: The agent making the observation (e.g. 'JARVIS')
    subject: The agent being observed (e.g. 'ADA')
    observed_patterns: Behavioral patterns noticed (comma-separated or free text)
    blind_spots: Known blind spots or weaknesses (comma-separated or free text)
    strengths: Known strengths (comma-separated or free text)
```

</details>

**Parámetros:**

- `observer` (str) — **requerido**
- `subject` (str) — **requerido**
- `observed_patterns` (Optional[str]) — opcional (default: `None`)
- `blind_spots` (Optional[str]) — opcional (default: `None`)
- `strengths` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 7306 en mcp_server_v2.py_

---

### `peer_model_query`

Query an agent's peer models — what they've observed about other agents.

<details>
<summary>Detalles</summary>

```
Args:
    observer: The agent whose observations to retrieve
    subject: Specific agent to query about (optional, all if omitted)
```

</details>

**Parámetros:**

- `observer` (str) — **requerido**
- `subject` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 7399 en mcp_server_v2.py_

---

## Rules & Events

### `rule_set`

Create or update a persistent rule/directive.

<details>
<summary>Detalles</summary>

```
Args:
    rule_key: Unique key for the rule
    content: The rule text
    set_by: Who set this rule (JARVIS, William, ADA)
    priority: critical, high, normal, or low
```

</details>

**Parámetros:**

- `rule_key` (str) — **requerido**
- `content` (str) — **requerido**
- `set_by` (str) — **requerido**
- `priority` (str) — opcional (default: `'normal'`)

**Retorna:** `str`

_Línea 2036 en mcp_server_v2.py_

---

### `rule_list`

List all rules/directives.

<details>
<summary>Detalles</summary>

```
Args:
    active_only: If true, only show active rules (default true)
```

</details>

**Parámetros:**

- `active_only` (bool) — opcional (default: `True`)

**Retorna:** `str`

_Línea 2063 en mcp_server_v2.py_

---

### `event_log_append`

Append an event to the time-series log.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Agent name (ADA, JARVIS, DUM)
    event_type: One of: command, response, error, milestone, heartbeat, status, query, train, eval
    content: Event description
    ref_id: Reference ID (e.g. cmd_001, rpt_015)
    session_id: Current session ID
    metadata: Optional JSON string
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `event_type` (str) — **requerido**
- `content` (str) — **requerido**
- `ref_id` (Optional[str]) — opcional (default: `None`)
- `session_id` (Optional[str]) — opcional (default: `None`)
- `metadata` (Optional[str]) — opcional (default: `None`)

**Retorna:** `str`

_Línea 2087 en mcp_server_v2.py_

---

### `event_log_query`

Query the event log by agent, type, and time range.

<details>
<summary>Detalles</summary>

```
Args:
    agent: Filter by agent (optional)
    event_type: Filter by event type (optional)
    hours_back: How many hours back to search (default 24)
    limit: Max results (default 50)
```

</details>

**Parámetros:**

- `agent` (Optional[str]) — opcional (default: `None`)
- `event_type` (Optional[str]) — opcional (default: `None`)
- `hours_back` (int) — opcional (default: `24`)
- `limit` (int) — opcional (default: `50`)

**Retorna:** `str`

_Línea 2117 en mcp_server_v2.py_

---

## Active Recall

### `active_recall`

Real-time context retrieval — the core of SOUL's active memory.

<details>
<summary>Detalles</summary>

```
Call this BEFORE responding to any user message. It searches memories,
instincts, and rules relevant to the current context and returns them
as a compact injection. This is what makes SOUL remember mid-session,
not just at boot.

Unlike boot_context (runs once) or memory_search (manual), active_recall
is designed to be called automatically on every turn to keep the agent's
behavior consistent with its learned patterns.

Args:
    agent: Agent name (ADA, JARVIS, DUM)
    context: The current user message or situation description
    include_rules: Include active rules (default true)
    include_instincts: Include relevant instincts (default true)
    include_memories: Include relevant memories (default true)
    memory_limit: Max memories to return (default 5)
    instinct_limit: Max instincts to return (default 3)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**
- `context` (str) — **requerido**
- `include_rules` (bool) — opcional (default: `True`)
- `include_instincts` (bool) — opcional (default: `True`)
- `include_memories` (bool) — opcional (default: `True`)
- `memory_limit` (int) — opcional (default: `5`)
- `instinct_limit` (int) — opcional (default: `3`)

**Retorna:** `str`

_Línea 5085 en mcp_server_v2.py_

---

## System

### `soul_snapshot`

Get a quick snapshot of the agent's soul state: OCEAN, style, emotions, opinions, relationships.

<details>
<summary>Detalles</summary>

```
Useful for self-awareness and drift monitoring.

Args:
    agent: Agent name (ADA, JARVIS, DUM)
```

</details>

**Parámetros:**

- `agent` (str) — **requerido**

**Retorna:** `str`

_Línea 2232 en mcp_server_v2.py_

---

### `microcompact_text`

Compact a tool result or long output WITHOUT using an LLM.

<details>
<summary>Detalles</summary>

```
Detects repetitive patterns (nvidia-smi, grep, git log, pip) and replaces
with tombstones containing the essential information.

Level 1 of 4-level compaction system (inspired by Claude Code).
Call this on large tool_results before they consume context window.

Args:
    text: The text to potentially compact
```

</details>

**Parámetros:**

- `text` (str) — **requerido**

**Retorna:** `str`

_Línea 3243 en mcp_server_v2.py_

---

### `microcompact_stats`

Get microcompact engine statistics for this session.

<details>
<summary>Detalles</summary>

```
Shows total chars saved, tombstones created, and rule hit counts.
```

</details>

**Parámetros:**

_Sin parámetros_

**Retorna:** `str`

_Línea 3271 en mcp_server_v2.py_

---

### `secret_scan`

Scan text for secrets (API keys, passwords, tokens, credentials).

<details>
<summary>Detalles</summary>

```
Use before storing sensitive content in memories.

Returns list of detected secrets with their types.
If empty list, text is safe to store.

Args:
    text: Text to scan for secrets
```

</details>

**Parámetros:**

- `text` (str) — **requerido**

**Retorna:** `str`

_Línea 3288 en mcp_server_v2.py_

---

### `brain_health_report`

Generate a health report of the SOUL brain — areas that need attention.

<details>
<summary>Detalles</summary>

```
Identifies weak spots, stale memories, low-utility areas, and suggests improvements.
Designed to enable autonomous self-improvement.

Args:
    agent: Agent name or 'all'
```

</details>

**Parámetros:**

- `agent` (str) — opcional (default: `'all'`)

**Retorna:** `str`

_Línea 6965 en mcp_server_v2.py_

---

### `health_check`

Report health status of all SEAL backend services (PG, Neo4j, Qdrant) plus uptime.

<details>
<summary>Detalles</summary>

```
Returns JSON with per-service status, uptime in seconds, and memory count.
Use this to verify the MCP server is fully operational before heavy operations.
```

</details>

**Parámetros:**

_Sin parámetros_

**Retorna:** `str`

_Línea 7629 en mcp_server_v2.py_

---
