"""SPECTRE reasoning_logger — D5 reasoning trace invariant (§3.3).

Ref: spec_spectre_contract_v2.md §3.3 invariantes:
  "Reasoning trace obligatorio (D5): cada acción escribe
   (evidence_used, rule_matched, llm_rationale_if_any, outcome)
   a reasoning_traces antes de aplicar."

Sandbox v1: writes to working_state.json (local trace buffer, last 50 entries).
Production path: writes to soul_v3.reasoning_traces via asyncpg.

The trace_id returned can be used to update outcome after action completes.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).parent.parent
WORKING_STATE_PATH = _BASE / "state" / "working_state.json"
_TRACES_KEY = "reasoning_traces"
_MAX_LOCAL_TRACES = 50

AGENT_ID = "SPECTRE"


# ── Local (sandbox) trace store ───────────────────────────────────────────────

def _read_state() -> dict:
    try:
        if WORKING_STATE_PATH.exists():
            return json.loads(WORKING_STATE_PATH.read_text())
    except Exception:
        pass
    return {}


def _write_state(state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    WORKING_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── Core API ──────────────────────────────────────────────────────────────────

def trace_store(
    task: str,
    premises: list[str],
    reasoning: str,
    conclusion: str,
    confidence: float = 0.7,
    action_type: str = "general",
) -> str:
    """Write a D5 reasoning trace BEFORE the action is applied.

    Returns trace_id (UUID str) — pass to trace_update() after action completes.

    task: short name of the action/task being reasoned about
    premises: list of assumptions held as true
    reasoning: why those premises support the conclusion
    conclusion: what SPECTRE is about to do
    confidence: estimated certainty [0.0, 1.0]
    action_type: "emit" | "escalate" | "filter" | "plan" | "general"
    """
    trace_id = str(uuid.uuid4())
    entry: dict[str, Any] = {
        "trace_id": trace_id,
        "agent": AGENT_ID,
        "task": task,
        "action_type": action_type,
        "premises": premises,
        "reasoning": reasoning,
        "conclusion": conclusion,
        "confidence": confidence,
        "outcome": None,
        "outcome_success": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": None,
    }

    state = _read_state()
    traces: list[dict] = state.get(_TRACES_KEY, [])
    traces.append(entry)
    state[_TRACES_KEY] = traces[-_MAX_LOCAL_TRACES:]
    _write_state(state)

    print(
        f"[SPECTRE/D5] trace stored: {trace_id[:8]}… task={task} conf={confidence:.2f}",
        flush=True,
    )
    return trace_id


def trace_update(
    trace_id: str,
    outcome: str,
    outcome_success: bool,
) -> bool:
    """Update an existing trace with its actual outcome.

    Returns True if trace was found and updated, False if not found.
    """
    state = _read_state()
    traces: list[dict] = state.get(_TRACES_KEY, [])

    for t in reversed(traces):
        if t.get("trace_id") == trace_id:
            t["outcome"] = outcome
            t["outcome_success"] = outcome_success
            t["updated_at"] = datetime.now(timezone.utc).isoformat()
            state[_TRACES_KEY] = traces
            _write_state(state)
            result_icon = "✅" if outcome_success else "❌"
            print(
                f"[SPECTRE/D5] trace updated: {trace_id[:8]}… {result_icon} {outcome[:60]}",
                flush=True,
            )
            return True

    print(f"[SPECTRE/D5] trace_id not found: {trace_id[:8]}…", flush=True)
    return False


def trace_search(
    task_prefix: str = "",
    action_type: str = "",
    limit: int = 10,
) -> list[dict]:
    """Search local reasoning traces. Returns most recent matches first."""
    state = _read_state()
    traces: list[dict] = state.get(_TRACES_KEY, [])

    matches = [
        t for t in reversed(traces)
        if (not task_prefix or t.get("task", "").startswith(task_prefix))
        and (not action_type or t.get("action_type") == action_type)
    ]
    return matches[:limit]


def trace_stats() -> dict[str, Any]:
    """Summary of local reasoning traces."""
    state = _read_state()
    traces: list[dict] = state.get(_TRACES_KEY, [])
    completed = [t for t in traces if t.get("outcome_success") is not None]
    successes = sum(1 for t in completed if t.get("outcome_success"))
    return {
        "total": len(traces),
        "completed": len(completed),
        "pending": len(traces) - len(completed),
        "successes": successes,
        "failures": len(completed) - successes,
        "success_rate": round(successes / len(completed), 3) if completed else 0.0,
    }
