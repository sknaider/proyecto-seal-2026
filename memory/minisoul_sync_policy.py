#!/usr/bin/env python3
"""
Mini-SOUL Synchronization Policy Module
========================================

Deterministic decision function for local memory consolidation → server sync.

SEAL Memory System | Mini-SOUL Edge | 2026-07-02
Ref: consolidation_daemon.py (GAP-S7), memory_admission.py, soul_cognitive_core.py

Design:
  - Local consolidation (episodic→semantic, episodic→procedural, decay) happens locally.
  - After consolidation, memories with importance >= SYNC_THRESHOLD automatically
    set scope='shared' and become available for upstream sync.
  - Mini-SOUL never uploads raw episodic data; only consolidated semantic + procedural.

Consolidation thresholds (from GAP-S7):
  - Episodic → Semantic: cosine >= 0.85, min 3 episodic memories → importance = avg + 1
  - Episodic → Procedural: min 3 repeated successful traces → procedural_memories
  - Selective decay: importance < 4, recall < 2, age > 30 days → importance -= 1

Sync rule (deterministic):
  - Semantic/procedural memories: importance >= 8 → scope = 'shared' → SYNC_READY
  - Metadata importance field is the SINGLE SOURCE OF TRUTH
  - No LLM judgment, no heuristics — fully code-deterministic
"""

from typing import TypedDict, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json


# Configuration constants (validated by consolidation_daemon.py)
SYNC_THRESHOLD_IMPORTANCE = 8          # Only memories with importance >= 8 sync up
CONSOLIDATION_CATEGORY_SEMANTIC = "abstracted_pattern"
CONSOLIDATION_CATEGORY_PROCEDURAL = "procedural_workflow"
DEFAULT_SCOPE_FOR_LOCAL = "private"    # Local memories default to private
DEFAULT_SCOPE_FOR_CONSOLIDATED = "shared"  # Consolidated memories become shared


class MemoryRecord(TypedDict, total=False):
    """Minimal memory record structure for sync decision."""
    id: str
    agent: str
    category: str
    content: str
    importance: int
    scope: Optional[str]
    memory_type: str  # 'episodic', 'semantic', 'procedural'
    metadata: dict
    created_at: str
    consolidation_parent_id: Optional[str]
    invalid_at: Optional[str]


@dataclass
class SyncCandidate:
    """Result of sync decision for a single memory."""
    memory_id: str
    should_sync: bool
    reason: str
    recommended_scope: str
    importance: int


def should_sync_up(memory: dict) -> bool:
    """
    Pure function: decide if a local memory should sync upstream.

    Single criterion: importance >= SYNC_THRESHOLD_IMPORTANCE

    Args:
        memory: dict with keys {id, importance, scope, memory_type, invalid_at, ...}

    Returns:
        True if memory meets sync criteria, False otherwise.

    Deterministic: no randomness, no heuristics, no LLM judgment.
    All decisions are based on explicit importance value.
    """
    # Check 1: Memory must not be invalidated
    if memory.get("invalid_at") is not None:
        return False

    # Check 2: Memory must have sufficient importance
    importance = memory.get("importance", 0)
    if not isinstance(importance, int) or importance < 0 or importance > 10:
        # Guard: invalid importance score
        return False

    if importance < SYNC_THRESHOLD_IMPORTANCE:
        return False

    # Check 3: Memory type filter (episodic never syncs raw, only consolidated)
    memory_type = memory.get("memory_type", "").lower()
    if memory_type == "episodic":
        # Raw episodics don't sync — only consolidated semantic/procedural sync
        return False

    # Check 4: Semantic and procedural memories with high importance sync
    if memory_type in ("semantic", "procedural", "abstracted_pattern", "workflow"):
        return True

    # Default: reject unknown memory types (fail-closed)
    return False


def evaluate_sync_decision(memory: dict) -> SyncCandidate:
    """
    Detailed evaluation of sync readiness.

    Returns: SyncCandidate with decision, reason, and recommended scope.
    """
    memory_id = memory.get("id", "unknown")
    importance = memory.get("importance", 0)
    scope = memory.get("scope")
    memory_type = memory.get("memory_type", "unknown")
    invalid_at = memory.get("invalid_at")

    # Decision tree
    if invalid_at is not None:
        return SyncCandidate(
            memory_id=memory_id,
            should_sync=False,
            reason="invalidated",
            recommended_scope=scope or DEFAULT_SCOPE_FOR_LOCAL,
            importance=importance,
        )

    if not isinstance(importance, int) or importance < 0 or importance > 10:
        return SyncCandidate(
            memory_id=memory_id,
            should_sync=False,
            reason="invalid_importance_score",
            recommended_scope=scope or DEFAULT_SCOPE_FOR_LOCAL,
            importance=importance,
        )

    if memory_type == "episodic":
        return SyncCandidate(
            memory_id=memory_id,
            should_sync=False,
            reason="raw_episodic_no_consolidation",
            recommended_scope=DEFAULT_SCOPE_FOR_LOCAL,
            importance=importance,
        )

    if importance < SYNC_THRESHOLD_IMPORTANCE:
        return SyncCandidate(
            memory_id=memory_id,
            should_sync=False,
            reason=f"low_importance_{importance}_below_{SYNC_THRESHOLD_IMPORTANCE}",
            recommended_scope=DEFAULT_SCOPE_FOR_LOCAL,
            importance=importance,
        )

    if memory_type in ("semantic", "procedural", "abstracted_pattern", "workflow"):
        return SyncCandidate(
            memory_id=memory_id,
            should_sync=True,
            reason=f"consolidated_{memory_type}_high_importance",
            recommended_scope=DEFAULT_SCOPE_FOR_CONSOLIDATED,
            importance=importance,
        )

    # Unknown type: fail-closed
    return SyncCandidate(
        memory_id=memory_id,
        should_sync=False,
        reason=f"unknown_type_{memory_type}",
        recommended_scope=DEFAULT_SCOPE_FOR_LOCAL,
        importance=importance,
    )


def select_sync_batch(memories: list, max_n: int = 100) -> list:
    """
    Select and prioritize a batch of memories for sync.

    Prioritization order:
      1. By importance (descending)
      2. By recency (if same importance)
      3. Take top max_n

    Args:
        memories: list of memory dicts
        max_n: maximum number of memories to return

    Returns:
        List of memory dicts eligible for sync, sorted by importance desc.
    """
    if not memories:
        return []

    # Filter: only sync-eligible memories
    eligible = [m for m in memories if should_sync_up(m)]

    if not eligible:
        return []

    # Sort by importance (desc), then by created_at (desc, newest first)
    def sort_key(m):
        importance = m.get("importance", 0)
        # Parse created_at if it's a string, else use current time as fallback
        created_at_str = m.get("created_at", "")
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            created_at = datetime.now(timezone.utc)

        # Sort by: (-importance, -created_at)
        # Negative because we want descending order
        return (-importance, -created_at.timestamp())

    sorted_batch = sorted(eligible, key=sort_key)

    # Return top max_n
    return sorted_batch[:max_n]


def apply_sync_scope_batch(memories: list) -> list:
    """
    Apply recommended scope to all memories in a batch.

    Utility: for testing/validation. In production, the database
    schema handles scope updates via trigger or explicit UPDATE.

    Returns: list of (memory, recommended_scope) tuples.
    """
    result = []
    for m in memories:
        evaluation = evaluate_sync_decision(m)
        recommended_scope = evaluation.recommended_scope
        m_updated = {**m, "scope": recommended_scope}
        result.append((m_updated, evaluation))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Comprehensive test suite demonstrating sync policy behavior.

    Test cases:
      1. High-importance consolidated memory → SYNC
      2. Low-importance episodic → NO SYNC
      3. Batch prioritization by importance
    """

    print("=" * 80)
    print("Mini-SOUL Sync Policy — Test Suite")
    print("=" * 80)
    print()

    # ─── Test Case 1: High-importance consolidated semantic memory ───
    print("TEST 1: Consolidated semantic memory (importance=8) → SHOULD SYNC")
    print("-" * 80)

    memory_1 = {
        "id": "mem-001",
        "agent": "ADA",
        "category": "abstracted_pattern",
        "content": "El equipo realizó 3 sesiones de debugging de red, identificando patrón de timeout en conexiones SSL bajo carga.",
        "memory_type": "semantic",
        "importance": 8,
        "scope": None,
        "created_at": "2026-07-01T15:30:00+00:00",
        "consolidation_parent_id": "mem-ep-001",
        "metadata": {
            "source_episodic_ids": ["mem-ep-001", "mem-ep-002", "mem-ep-003"],
            "cluster_size": 3,
            "gap": "S7",
        },
        "invalid_at": None,
    }

    result_1 = should_sync_up(memory_1)
    eval_1 = evaluate_sync_decision(memory_1)

    print(f"  should_sync_up()           : {result_1}")
    print(f"  Decision                  : {eval_1.should_sync}")
    print(f"  Reason                    : {eval_1.reason}")
    print(f"  Recommended scope         : {eval_1.recommended_scope}")
    print(f"  Importance                : {eval_1.importance}")
    assert result_1 is True, "FAIL: High-importance semantic should sync"
    assert eval_1.should_sync is True, "FAIL: Detailed eval should agree"
    assert eval_1.recommended_scope == "shared", "FAIL: Should recommend 'shared' scope"
    print("  ✓ PASS\n")

    # ─── Test Case 2: Low-importance episodic memory → NO SYNC ───
    print("TEST 2: Raw episodic memory (importance=3) → SHOULD NOT SYNC")
    print("-" * 80)

    memory_2 = {
        "id": "mem-ep-999",
        "agent": "JARVIS",
        "category": "event",
        "content": "William asked about RTX 5090 tuning at 14:22 Lima time.",
        "memory_type": "episodic",
        "importance": 3,
        "scope": "private",
        "created_at": "2026-07-01T14:22:00-05:00",
        "consolidation_parent_id": None,
        "invalid_at": None,
    }

    result_2 = should_sync_up(memory_2)
    eval_2 = evaluate_sync_decision(memory_2)

    print(f"  should_sync_up()           : {result_2}")
    print(f"  Decision                  : {eval_2.should_sync}")
    print(f"  Reason                    : {eval_2.reason}")
    print(f"  Recommended scope         : {eval_2.recommended_scope}")
    print(f"  Importance                : {eval_2.importance}")
    assert result_2 is False, "FAIL: Episodic should never sync"
    assert eval_2.should_sync is False, "FAIL: Detailed eval should agree"
    assert eval_2.recommended_scope == "private", "FAIL: Should keep private scope"
    print("  ✓ PASS\n")

    # ─── Test Case 3: Invalidated memory → NO SYNC ───
    print("TEST 3: Invalidated memory (importance=9) → SHOULD NOT SYNC")
    print("-" * 80)

    memory_3 = {
        "id": "mem-002",
        "agent": "ALICE",
        "category": "correction",
        "content": "DEPRECATED: Old understanding of SEAL architecture (superseded by v2).",
        "memory_type": "semantic",
        "importance": 9,
        "scope": "private",
        "created_at": "2026-06-15T10:00:00+00:00",
        "invalid_at": "2026-06-30T08:00:00+00:00",
    }

    result_3 = should_sync_up(memory_3)
    eval_3 = evaluate_sync_decision(memory_3)

    print(f"  should_sync_up()           : {result_3}")
    print(f"  Decision                  : {eval_3.should_sync}")
    print(f"  Reason                    : {eval_3.reason}")
    assert result_3 is False, "FAIL: Invalidated memory should not sync"
    assert eval_3.reason == "invalidated", "FAIL: Reason should be 'invalidated'"
    print("  ✓ PASS\n")

    # ─── Test Case 4: Batch selection and prioritization ───
    print("TEST 4: Batch selection with prioritization")
    print("-" * 80)

    batch = [
        {  # Should sync (importance=9, newest)
            "id": "mem-batch-1",
            "importance": 9,
            "memory_type": "semantic",
            "created_at": "2026-07-02T10:00:00+00:00",
            "invalid_at": None,
        },
        {  # Should sync (importance=8, older)
            "id": "mem-batch-2",
            "importance": 8,
            "memory_type": "procedural",
            "created_at": "2026-07-01T09:00:00+00:00",
            "invalid_at": None,
        },
        {  # Should NOT sync (importance=5)
            "id": "mem-batch-3",
            "importance": 5,
            "memory_type": "semantic",
            "created_at": "2026-07-02T11:00:00+00:00",
            "invalid_at": None,
        },
        {  # Should NOT sync (episodic)
            "id": "mem-batch-4",
            "importance": 9,
            "memory_type": "episodic",
            "created_at": "2026-07-02T12:00:00+00:00",
            "invalid_at": None,
        },
        {  # Should sync (importance=8, older than batch-2)
            "id": "mem-batch-5",
            "importance": 8,
            "memory_type": "semantic",
            "created_at": "2026-07-01T08:00:00+00:00",
            "invalid_at": None,
        },
    ]

    selected = select_sync_batch(batch, max_n=100)

    print(f"  Input batch size          : {len(batch)}")
    print(f"  Selected for sync         : {len(selected)}")
    print(f"  Priority order:")
    for i, mem in enumerate(selected, 1):
        print(f"    {i}. mem_id={mem['id']:<15} importance={mem['importance']}")

    # Verify order: should be sorted by importance (desc)
    assert len(selected) == 3, f"FAIL: Expected 3 eligible, got {len(selected)}"
    assert selected[0]["id"] == "mem-batch-1", "FAIL: Highest importance should be first"
    assert selected[0]["importance"] == 9, "FAIL: First should have importance=9"
    assert selected[1]["importance"] == 8, "FAIL: Second and third should have importance=8"
    assert selected[2]["importance"] == 8, "FAIL: Second and third should have importance=8"
    # Within same importance, newer should come first
    batch_2_idx = next(i for i, m in enumerate(selected) if m["id"] == "mem-batch-2")
    batch_5_idx = next(i for i, m in enumerate(selected) if m["id"] == "mem-batch-5")
    assert batch_2_idx < batch_5_idx, "FAIL: Newer batch-2 should come before older batch-5"
    print("  ✓ PASS\n")

    # ─── Test Case 5: Batch with max_n limit ───
    print("TEST 5: Batch selection with max_n=2 limit")
    print("-" * 80)

    selected_limited = select_sync_batch(batch, max_n=2)
    print(f"  max_n=2 applied")
    print(f"  Selected                  : {len(selected_limited)} memories")
    for i, mem in enumerate(selected_limited, 1):
        print(f"    {i}. {mem['id']}")

    assert len(selected_limited) == 2, f"FAIL: max_n=2 should return 2, got {len(selected_limited)}"
    assert selected_limited[0]["id"] == "mem-batch-1", "FAIL: Top priority should be first"
    print("  ✓ PASS\n")

    # ─── Summary ───
    print("=" * 80)
    print("All tests passed! ✓")
    print("=" * 80)
    print()
    print("Summary:")
    print("  • Consolidated memories (semantic/procedural) with importance >= 8 sync up")
    print("  • Raw episodic memories NEVER sync (require consolidation first)")
    print("  • Invalidated memories NEVER sync (fail-closed)")
    print("  • Batch selection prioritizes by importance (desc) then recency (desc)")
    print("  • All decisions are deterministic and code-based (no LLM judgment)")
    print()
