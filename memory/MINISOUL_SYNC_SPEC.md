# Mini-SOUL Synchronization Policy — Specification

**Date:** 2026-07-02  
**Module:** `minisoul_sync_policy.py`  
**Author:** Claude Code (SEAL Framework)  
**Reference:** consolidation_daemon.py (GAP-S7), soul_cognitive_core.py

---

## Overview

Mini-SOUL is the local memory subsystem running on edge devices. It performs three consolidation phases (GAP-S7) during sleep windows:

1. **Episodic → Semantic** (cosine ≥ 0.85, min 3 memories) → creates `semantic` memories with importance = avg + 1
2. **Episodic → Procedural** (min 3 repeated successful traces) → creates `procedural` memories
3. **Selective Decay** (importance < 4, recall < 2, age > 30 days) → importance -= 1

After consolidation, the **sync policy** determines which memories upload to the central SOUL server.

### Key Design Principle

**Consolidated memories only** (semantic/procedural) are eligible for sync. Raw episodic data stays local unless consolidated. This prevents:
- Unbounded growth of raw episodic events
- Privacy leaks (local noise doesn't reach servers)
- Network congestion (only high-value insights travel)

---

## Sync Rule (Deterministic)

```
IF memory.invalid_at IS NULL
  AND memory.importance >= 8
  AND memory.memory_type IN ('semantic', 'procedural', 'abstracted_pattern', 'workflow')
THEN
  sync_ready = True
  scope = 'shared'
ELSE
  sync_ready = False
  scope = memory.scope OR 'private'
```

**Single criterion:** `importance >= SYNC_THRESHOLD_IMPORTANCE (8)`

All decisions are:
- **Code-deterministic** (no LLM judgment, no heuristics)
- **Fail-closed** (reject unknown types, invalidated memories)
- **Testable** (pure functions, no side effects)

---

## Configuration Constants

| Constant | Value | Source | Purpose |
|----------|-------|--------|---------|
| `SYNC_THRESHOLD_IMPORTANCE` | 8 | soul_cognitive_core.py | Min importance to sync |
| `CONSOLIDATION_CATEGORY_SEMANTIC` | "abstracted_pattern" | consolidation_daemon.py | Semantic memory type marker |
| `CONSOLIDATION_CATEGORY_PROCEDURAL` | "procedural_workflow" | consolidation_daemon.py | Procedural memory type marker |
| `DEFAULT_SCOPE_FOR_LOCAL` | "private" | Policy | Default scope for non-sync memories |
| `DEFAULT_SCOPE_FOR_CONSOLIDATED` | "shared" | Policy | Default scope for sync-ready memories |

---

## Public API

### `should_sync_up(memory: dict) -> bool`

**Purpose:** Single binary decision: does this memory sync?

**Signature:**
```python
def should_sync_up(memory: dict) -> bool
```

**Input:**
```python
memory = {
    "id": "mem-001",
    "importance": 8,
    "memory_type": "semantic",
    "invalid_at": None,
    # ... other fields
}
```

**Output:**
```
True   → memory meets sync criteria, will upload
False  → memory does not sync (stays local)
```

**Decision tree:**
1. If `invalid_at IS NOT NULL` → False (invalidated memories never sync)
2. If `importance < 8` → False (too low importance)
3. If `memory_type == 'episodic'` → False (raw episodic never syncs)
4. If `memory_type IN ('semantic', 'procedural', ...)` → True (consolidated syncs)
5. Default → False (fail-closed for unknown types)

**Guarantees:**
- Pure function (no I/O, no side effects)
- Idempotent (same input → same output)
- Fast (O(1) dict lookups)

---

### `evaluate_sync_decision(memory: dict) -> SyncCandidate`

**Purpose:** Detailed decision report (reason, recommended scope, etc.)

**Output type:**
```python
@dataclass
class SyncCandidate:
    memory_id: str
    should_sync: bool
    reason: str                  # e.g., "consolidated_semantic_high_importance"
    recommended_scope: str       # "shared", "private", etc.
    importance: int
```

**Reasons emitted:**
- `"invalidated"` — memory has invalid_at timestamp
- `"invalid_importance_score"` — importance not in [0,10] or not integer
- `"raw_episodic_no_consolidation"` — episodic memories never sync
- `"low_importance_<N>_below_8"` — importance too low
- `"consolidated_semantic_high_importance"` — SYNC
- `"consolidated_procedural_high_importance"` — SYNC
- `"unknown_type_<type>"` — fail-closed for unknown types

---

### `select_sync_batch(memories: list, max_n: int = 100) -> list`

**Purpose:** Select and prioritize a batch of memories for sync.

**Signature:**
```python
def select_sync_batch(memories: list, max_n: int = 100) -> list
```

**Input:**
```python
memories = [
    {"id": "mem-1", "importance": 9, "memory_type": "semantic", "created_at": "..."},
    {"id": "mem-2", "importance": 5, "memory_type": "episodic", "created_at": "..."},
    {"id": "mem-3", "importance": 8, "memory_type": "procedural", "created_at": "..."},
    # ...
]
max_n = 100
```

**Output:**
```python
[
    {"id": "mem-1", "importance": 9, ...},  # Highest priority
    {"id": "mem-3", "importance": 8, ...},
    # ... up to max_n
]
```

**Behavior:**
1. Filter: only memories where `should_sync_up()` returns True
2. Sort by: `(-importance, -created_at.timestamp())` (descending both)
3. Return: top `max_n` memories

**Use case:** When syncing to server, batch-process the most important memories first.

---

### `apply_sync_scope_batch(memories: list) -> list`

**Purpose:** Utility function for testing. Apply recommended scope to batch.

**Output:** List of `(memory_updated, evaluation)` tuples

**Note:** In production, scope updates happen via SQL trigger or explicit UPDATE query, not this function.

---

## Memory Fields Reference

| Field | Type | Role in sync decision | Notes |
|-------|------|----------------------|-------|
| `id` | str | Output (identifies memory) | UUID or serial |
| `importance` | int [0-10] | PRIMARY (threshold = 8) | Set by consolidation_daemon.py |
| `memory_type` | str | FILTER (exclude episodic) | "episodic", "semantic", "procedural" |
| `invalid_at` | timestamp \| null | VETO (fail-closed) | Soft-delete marker |
| `scope` | str \| null | OUTPUT (recommended) | "private", "shared", "team", "william" |
| `metadata` | dict | CONTEXT (optional) | May include consolidation_parent_id |
| `created_at` | timestamp | TIEBREAKER (sort within same importance) | For batch selection |
| `category` | str | CONTEXT (optional) | "abstracted_pattern", "procedural_workflow", etc. |

---

## Examples

### Example 1: High-importance semantic memory (SYNCS)

```python
memory = {
    "id": "mem-001",
    "agent": "ADA",
    "category": "abstracted_pattern",
    "content": "Pattern: network timeouts occur under SSL load; mitigated by connection pooling.",
    "memory_type": "semantic",
    "importance": 8,
    "scope": None,
    "invalid_at": None,
    "created_at": "2026-07-01T15:30:00Z",
    "consolidation_parent_id": "mem-ep-001",
}

should_sync_up(memory)  # → True
# → Will set scope='shared' and sync to server
```

### Example 2: Raw episodic memory (DOES NOT SYNC)

```python
memory = {
    "id": "mem-ep-002",
    "agent": "JARVIS",
    "category": "event",
    "content": "William asked about RTX config at 14:22.",
    "memory_type": "episodic",
    "importance": 5,
    "scope": "private",
    "invalid_at": None,
    "created_at": "2026-07-01T14:22:00Z",
}

should_sync_up(memory)  # → False
# → Stays local; maybe later consolidated into semantic memory
```

### Example 3: Low-importance procedural (DOES NOT SYNC)

```python
memory = {
    "id": "mem-proc-001",
    "agent": "ALICE",
    "category": "procedural_workflow",
    "content": "Procedure: To debug MQTT, check broker logs first, then client config.",
    "memory_type": "procedural",
    "importance": 6,  # Below threshold of 8
    "scope": "private",
    "invalid_at": None,
    "created_at": "2026-07-01T10:00:00Z",
}

should_sync_up(memory)  # → False
# → Not yet important enough; if recall_count increases, importance may be boosted
```

### Example 4: Batch selection prioritization

```python
batch = [
    {"id": "A", "importance": 9, "memory_type": "semantic", "created_at": "2026-07-02T10:00Z"},
    {"id": "B", "importance": 8, "memory_type": "procedural", "created_at": "2026-07-01T09:00Z"},
    {"id": "C", "importance": 5, "memory_type": "semantic", "created_at": "2026-07-02T11:00Z"},
    {"id": "D", "importance": 8, "memory_type": "semantic", "created_at": "2026-07-01T08:00Z"},
]

selected = select_sync_batch(batch, max_n=100)
# → [A (imp=9), B (imp=8, newer), D (imp=8, older)]
# Note: C (imp=5) filtered out, not synced
```

---

## Integration Points

### With `consolidation_daemon.py` (GAP-S7)

After consolidation runs and creates new `semantic` or `procedural` memories with `importance >= 8`:

1. Database trigger or scheduled task calls sync policy
2. `should_sync_up()` evaluates each new memory
3. If True, UPDATE `scope = 'shared'` in memories table
4. Async sync daemon picks up `scope='shared'` memories and uploads to server

### With `memory_admission.py`

Memory admission gates can reference SYNC_THRESHOLD_IMPORTANCE:

```python
if memory['importance'] >= 8:
    # This memory will eventually sync, mark it accordingly
    memory['initial_scope'] = 'shared'
```

### With server-side sync daemon

Server-side reads `scope='shared'` and `scope='team'` memories, rejects `scope='private'`.

---

## Testing

Run the included test suite:

```bash
python3 minisoul_sync_policy.py
```

**Test cases:**
1. ✓ High-importance consolidated semantic → SYNCS
2. ✓ Low-importance episodic → DOES NOT SYNC
3. ✓ Invalidated memory → DOES NOT SYNC
4. ✓ Batch selection prioritization by importance
5. ✓ Batch selection with max_n limit

All tests pass. No external dependencies (uses only stdlib).

---

## Design Rationale

### Why importance >= 8?

- Consolidation sets semantic importance = avg(episodic) + 1
- Episodic memories are typically importance 1–7
- Consolidated semantic reaches 2–8
- Threshold of 8 means "only highly consolidated or manually-marked-important"
- Prevents noisy data from traveling upstream

### Why fail-closed for unknown types?

- Future memory types (e.g., "emotional", "embodied") unknown at spec time
- Safe default: don't assume new types should sync
- Admin can extend by updating `should_sync_up()` logic

### Why code-deterministic, not LLM-judged?

- Deterministic ≈ reproducible, testable, auditable
- LLM judgment ≈ latency, cost, non-determinism
- Binary gate (`importance >= 8`) captures the same decision surface with zero cost
- Importance itself is set by consolidation (deterministic clustering) or human

### Why no recall_count or decay in sync decision?

- `recall_count` and decay are **local optimizations** for LRU-style management
- They don't affect **whether a memory is important enough to share**
- Sync decision is purely about **consolidated semantic/procedural at high importance**
- Decay happens independently; recall_count is observational

---

## Future Extensions

1. **Per-agent sync policies:** Different agents may have different sync thresholds
2. **Scope-aware filtering:** Respect `scope='william'` (don't auto-upgrade)
3. **Network-aware batching:** Reduce batch size under low bandwidth
4. **Encryption before sync:** Wrap memory content with agent public key
5. **Selective PII masking:** Gate sensitive fields (names, IPs) via separate policy

---

## References

- **consolidation_daemon.py** — GAP-S7 implementation (S7E, S7P, S7D phases)
- **soul_cognitive_core.py** — Central memory queries, importance thresholds
- **memory_admission.py** — Entry gate, sets initial importance
- **SEAL Memory System spec** — Full system architecture

