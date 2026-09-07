# MIRIX Memory Typing — Formal Memory Type System for SOUL

**Paper:** MIRIX (arxiv 2507.07957) — 85.38% LoCoMo vs Mem0 66.88%
**Designer:** JARVIS — 2026-04-12
**Status:** Draft → Ready for implementation

---

## Problem

SOUL stores all memories in a flat `memories` table with a `category` field (emotion, trust, correction, etc.) but no formal **memory type** distinction. MIRIX proves that explicit type routing improves retrieval accuracy by ~19% over flat systems. SOUL has 1,867+ memories that already fall into natural type clusters — they just aren't labeled.

## Solution

Add a `memory_type` enum column to memories + create a Meta Memory Router that auto-classifies incoming memories into 6 MIRIX types. Each type gets optimized retrieval behavior.

---

## Implementation Steps

### Step 1: Schema Migration (S) — JARVIS

Add `memory_type` column to memories table with mapping from existing categories.

```sql
-- Migration 010: MIRIX Memory Typing
ALTER TABLE memories ADD COLUMN IF NOT EXISTS memory_type TEXT DEFAULT 'episodic';

-- Auto-classify existing memories based on category
UPDATE memories SET memory_type = CASE
    WHEN category IN ('emotion', 'trust', 'preference') THEN 'core'
    WHEN category IN ('milestone', 'dynamic', 'humor', 'correction') THEN 'episodic'
    WHEN category IN ('insight', 'fact', 'pattern') THEN 'semantic'
    ELSE 'episodic'
END
WHERE memory_type IS NULL OR memory_type = 'episodic';

-- Index for type-filtered queries
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories (memory_type, agent);

-- Constraint for valid types
ALTER TABLE memories ADD CONSTRAINT chk_memory_type
    CHECK (memory_type IN ('core', 'episodic', 'semantic', 'procedural', 'resource', 'vault'));
```

**Procedural** already has its own table (`procedures`). **Resource** and **Vault** are new types that will use the memories table with special handling.

### Step 2: Meta Memory Router (M) — ADA

Add `_mirix_classify()` function to `memory_store` that auto-assigns `memory_type` based on:
1. Explicit `memory_type` parameter (if provided by caller)
2. Category mapping (emotion→core, milestone→episodic, insight→semantic)
3. Content analysis heuristics (detect credentials→vault, detect file refs→resource)

```python
MIRIX_CATEGORY_MAP = {
    # Core — identity, trust, preferences
    "emotion": "core", "trust": "core", "preference": "core",
    # Episodic — events, milestones, corrections
    "milestone": "episodic", "dynamic": "episodic", "humor": "episodic",
    "correction": "episodic",
    # Semantic — knowledge, insights, patterns
    "insight": "semantic", "fact": "semantic", "pattern": "semantic",
    "decision": "semantic",  # decisions are knowledge
}

def _mirix_classify(category: str, content: str, memory_type: str | None = None) -> str:
    if memory_type:
        return memory_type  # explicit override
    if _contains_secret_pattern(content):
        return "vault"
    if _contains_file_reference(content):
        return "resource"
    return MIRIX_CATEGORY_MAP.get(category, "episodic")
```

### Step 3: Type-Aware Retrieval (M) — ADA

Modify `memory_search` and `memory_hybrid_search` to accept optional `memory_type` filter:
- When `memory_type` is specified, add WHERE/filter condition
- When not specified, search all types (backward compatible)
- Core memories get 1.2x boost in retrieval (identity is always relevant)
- Vault memories require explicit type request (not returned in general search)

### Step 4: Type-Specific Behaviors (M) — ADA

- **Core memories**: Never decay below 0.3 (identity persists). Protected from SleepGate FORGET.
- **Episodic memories**: Standard decay. Eligible for Cold Archive after 90 days inactive.
- **Semantic memories**: Slow decay (knowledge). Eligible for belief synthesis (Tier 5).
- **Procedural**: Route to existing `procedure_store/search`. No change needed.
- **Resource**: Store with `file_path` metadata. Decay fast (7 days) unless accessed.
- **Vault**: Encrypted at rest (future). Never shared via broadcast. Never in cold archive. Excluded from general search.

### Step 5: Boot Context Enhancement (S) — ADA

Modify `boot_context` to load memories by type:
- Core: top 5 by importance (always loaded)
- Episodic: last 3 recent events
- Semantic: top 3 by confidence
- Procedural: count only (loaded on demand)

### Step 6: MCP Tool — memory_type_stats (S) — ADA

New tool showing distribution of memories by type per agent.

### Step 7: Tests (M) — ADA

- test_mirix_classification: verify category→type mapping
- test_mirix_retrieval_filter: verify type-filtered search
- test_mirix_core_boost: verify 1.2x boost for core memories
- test_mirix_vault_exclusion: verify vault excluded from general search
- test_mirix_boot_context: verify type-aware boot loading
- test_mirix_migration: verify existing memories classified correctly

---

## Acceptance Criteria

Given 1,867 existing memories
When migration 010 runs
Then all memories have a valid memory_type based on their category

Given a new memory with category="trust"
When memory_store is called
Then memory_type is automatically set to "core"

Given a search query with memory_type="semantic"
When memory_search runs
Then only semantic memories are returned

Given vault-type memories exist
When general memory_search runs (no type filter)
Then vault memories are NOT included

---

## Scope Boundaries

**IN scope:** memory_type column, auto-classification, type-filtered retrieval, boot enhancement, tests
**OUT of scope:** encryption for vault (future), Resource memory file indexing (future), LLM-based classification (MIRIX uses LLM router — we use heuristics first, LLM later)

## Error Scenarios

- Unknown category → defaults to "episodic"
- Vault memory accidentally in broadcast → blocked by type check
- Migration on empty DB → no-op (constraint added, no data to classify)
