"""SPECTRE goal_planner — Nivel 4 diversity heuristic (v2 sandbox).

Ref: spec_spectre_contract_v2.md §NIVEL 4:
  "LLM produce N avenues > M plans > K seleccionados con criterio explícito
   (innovation, max performance, min performance)"

Produces 3 GoalCandidates per goal:
  - innovative:   novel approach, unexplored path, higher variance
  - max_gain:     highest expected value, proven patterns
  - conservative: minimal risk, reversible, safe baseline

select_next_goal() → [innovative, max_gain, conservative]
apply_diversity_selection(candidates, mode) → picks one
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class GoalCandidate:
    """A strategy candidate for pursuing a goal."""

    name: str
    strategy_type: str          # "innovative" | "max_gain" | "conservative"
    rationale: str
    confidence: float           # [0.0, 1.0] estimated success probability
    context_match: float        # [0.0, 1.0] fit to current context/goal
    estimated_cost: str         # "low" | "medium" | "high"
    reversible: bool = True
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def score(self) -> float:
        """Simple composite score for ranking."""
        cost_penalty = {"low": 0.0, "medium": 0.1, "high": 0.2}.get(self.estimated_cost, 0.1)
        return self.confidence * 0.6 + self.context_match * 0.4 - cost_penalty


# ── Candidate generators ──────────────────────────────────────────────────────

def _innovative_candidate(goal: dict[str, Any], context: str) -> GoalCandidate:
    """Generate an innovative (exploratory) strategy for the goal.

    Characteristics:
    - Tries unexplored approaches or combinations
    - Higher variance: could significantly outperform or underperform baseline
    - Lower initial confidence, higher potential upside
    """
    goal_name = goal.get("name", "unknown_goal")
    goal_desc = goal.get("description", "")

    # Innovative: invert the usual approach — solve constraints last, not first
    rationale = (
        f"Tackle '{goal_name}' via lateral exploration: identify non-obvious "
        f"starting points and defer typical constraints to iteration 2+. "
        f"Accept higher variance for potential breakthrough. Context: {context[:80] or 'none'}"
    )

    priority = goal.get("priority", 5)
    confidence = max(0.30, min(0.65, 0.50 - (priority - 5) * 0.03))
    context_match = 0.55 if context else 0.40

    return GoalCandidate(
        name=f"{goal_name}__innovative",
        strategy_type="innovative",
        rationale=rationale,
        confidence=confidence,
        context_match=context_match,
        estimated_cost="high",
        reversible=False,
    )


def _max_gain_candidate(goal: dict[str, Any], context: str) -> GoalCandidate:
    """Generate a max-gain (highest expected value) strategy for the goal.

    Characteristics:
    - Proven patterns, direct path to goal
    - Optimized for outcome quality, not risk
    - Higher confidence, medium cost
    """
    goal_name = goal.get("name", "unknown_goal")

    rationale = (
        f"Pursue '{goal_name}' along the highest-value path: "
        f"apply proven patterns, maximize output quality, accept medium cost. "
        f"Priority: {goal.get('priority', 5)}. "
        f"Context fit optimized."
    )

    priority = goal.get("priority", 5)
    confidence = max(0.55, min(0.85, 0.70 + (10 - priority) * 0.01))
    context_match = 0.80 if context else 0.65

    return GoalCandidate(
        name=f"{goal_name}__max_gain",
        strategy_type="max_gain",
        rationale=rationale,
        confidence=confidence,
        context_match=context_match,
        estimated_cost="medium",
        reversible=True,
    )


def _conservative_candidate(goal: dict[str, Any], context: str) -> GoalCandidate:
    """Generate a conservative (safe baseline) strategy for the goal.

    Characteristics:
    - Minimal risk, fully reversible
    - Slightly below max performance but near-certain to succeed
    - Low cost, easy rollback
    """
    goal_name = goal.get("name", "unknown_goal")

    rationale = (
        f"Pursue '{goal_name}' conservatively: minimal-footprint approach, "
        f"fully reversible actions, abort on first anomaly. "
        f"Prefer 80% of max gain with 95% confidence over optimized but fragile path."
    )

    priority = goal.get("priority", 5)
    confidence = max(0.70, min(0.95, 0.82 + (10 - priority) * 0.01))
    context_match = 0.70 if context else 0.60

    return GoalCandidate(
        name=f"{goal_name}__conservative",
        strategy_type="conservative",
        rationale=rationale,
        confidence=confidence,
        context_match=context_match,
        estimated_cost="low",
        reversible=True,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def select_next_goal(
    goal_stack: list[dict[str, Any]],
    context: str = "",
) -> list[GoalCandidate]:
    """Generate 3 diversity candidates for the top goal in goal_stack.

    Returns [innovative, max_gain, conservative] in that order.
    Returns [] if goal_stack is empty.

    goal_stack: list of goal dicts (each may have keys: name, description, priority).
    context: free-form string from working_state or current event (improves scoring).
    """
    if not goal_stack:
        return []

    goal = goal_stack[0]  # top-of-stack = current focus goal

    return [
        _innovative_candidate(goal, context),
        _max_gain_candidate(goal, context),
        _conservative_candidate(goal, context),
    ]


def apply_diversity_selection(
    candidates: list[GoalCandidate],
    mode: str = "max_gain",
) -> GoalCandidate | None:
    """Pick one candidate from the diversity set by strategy_type.

    mode: "innovative" | "max_gain" | "conservative" | "auto"
      auto → picks the candidate with highest composite score().

    Returns None if candidates is empty or mode not found.
    """
    if not candidates:
        return None

    if mode == "auto":
        return max(candidates, key=lambda c: c.score)

    for candidate in candidates:
        if candidate.strategy_type == mode:
            return candidate

    return None


def rank_candidates(candidates: list[GoalCandidate]) -> list[GoalCandidate]:
    """Return candidates sorted by composite score (highest first)."""
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def candidates_summary(candidates: list[GoalCandidate]) -> dict[str, Any]:
    """Compact summary of a candidate set for working_state storage."""
    return {
        "count": len(candidates),
        "strategies": [c.strategy_type for c in candidates],
        "scores": {c.strategy_type: round(c.score, 3) for c in candidates},
        "top": candidates[0].strategy_type if candidates else None,
        "goal_name": candidates[0].name.rsplit("__", 1)[0] if candidates else None,
    }
