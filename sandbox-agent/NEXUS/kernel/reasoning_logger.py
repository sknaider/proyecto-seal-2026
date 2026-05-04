"""NEXUS reasoning_logger — local trace store for cortex decisions.

NEXUS variant of SPECTRE's reasoning_logger.py (§3.3 D5 invariant).
Captures the chain of reasoning behind each cortex decision so it can be
audited later without depending on Soul DB.

Why this matters for NEXUS specifically:
  Audit 2026-05-04 detected that NEXUS forgot its own kernel implementation
  because memory_store captured outcomes (commits, decisions) but NOT the
  reasoning process. Without this logger, "I built X" is a fact in DB but
  "I decided to build X because Y, evaluated Z, chose A over B" is lost.

Storage:
  - Local: /tmp/nexus_reasoning_traces.jsonl (append-only)
  - Optional sync to Soul DB via reasoning_trace_store MCP (future)

Each trace captures:
  - task: short label (e.g. "respond_william", "execute_proposal")
  - input: what triggered the decision (msg id, content excerpt)
  - premises: assumptions held as true (e.g. active rules, instincts)
  - reasoning: the why
  - decision: what NEXUS chose to do
  - confidence: 0.0–1.0
  - outcome: filled in later via update_trace_outcome
  - latency_ms: time spent reasoning

Reference: NEXUS audit 2026-05-04 §4 RIESGO CRÍTICO 2
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_ID = "NEXUS"
TRACE_LOG = Path("/tmp/nexus_reasoning_traces.jsonl")
_MAX_LOG_LINES = 5000  # keep last 5k traces (older lines pruned on rotation)

_lock = threading.Lock()


def _append_trace(entry: dict[str, Any]) -> None:
    try:
        with _lock:
            with TRACE_LOG.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        print(f"[NEXUS/reasoning] trace write failed: {ex}", flush=True)


def store_trace(
    task: str,
    input_excerpt: str,
    premises: list[str],
    reasoning: str,
    decision: str,
    confidence: float = 0.7,
    action_type: str = "general",
) -> str:
    """Store a reasoning trace BEFORE acting on the decision.

    Returns trace_id (UUID hex) — pass to update_trace_outcome() later.
    """
    trace_id = uuid.uuid4().hex
    entry: dict[str, Any] = {
        "trace_id": trace_id,
        "agent": AGENT_ID,
        "task": task,
        "action_type": action_type,
        "input_excerpt": (input_excerpt or "")[:500],
        "premises": premises[:20],  # cap list size
        "reasoning": (reasoning or "")[:2000],
        "decision": (decision or "")[:1000],
        "confidence": round(float(confidence), 3),
        "outcome": None,
        "outcome_success": None,
        "latency_ms": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": None,
        "_started_perf": time.perf_counter(),
    }
    _append_trace({k: v for k, v in entry.items() if not k.startswith("_")})
    # Cache start time on disk via tmp file for latency tracking
    try:
        ts_file = Path(f"/tmp/nexus_trace_start_{trace_id}.t")
        ts_file.write_text(str(entry["_started_perf"]))
    except Exception:
        pass
    return trace_id


def update_trace_outcome(
    trace_id: str,
    outcome: str,
    outcome_success: bool,
) -> bool:
    """Record the actual outcome of a decision after the action ran.

    Appends an outcome update line (jsonl is append-only). Readers must merge
    by trace_id when querying.
    """
    latency_ms: int | None = None
    ts_file = Path(f"/tmp/nexus_trace_start_{trace_id}.t")
    try:
        if ts_file.exists():
            started = float(ts_file.read_text())
            latency_ms = int((time.perf_counter() - started) * 1000)
            ts_file.unlink()
    except Exception:
        pass

    update_entry = {
        "trace_id": trace_id,
        "agent": AGENT_ID,
        "_kind": "outcome_update",
        "outcome": (outcome or "")[:1000],
        "outcome_success": bool(outcome_success),
        "latency_ms": latency_ms,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _append_trace(update_entry)
    return True


def search_traces(
    task_prefix: str = "",
    action_type: str = "",
    limit: int = 20,
    since_minutes: int | None = None,
) -> list[dict]:
    """Read traces from log, merge outcome updates by trace_id.

    Returns most recent matching traces first.
    """
    if not TRACE_LOG.exists():
        return []

    traces: dict[str, dict] = {}
    try:
        for line in TRACE_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            tid = entry.get("trace_id")
            if not tid:
                continue
            if entry.get("_kind") == "outcome_update":
                if tid in traces:
                    traces[tid].update({
                        "outcome": entry.get("outcome"),
                        "outcome_success": entry.get("outcome_success"),
                        "latency_ms": entry.get("latency_ms"),
                        "updated_at": entry.get("updated_at"),
                    })
            else:
                traces[tid] = entry
    except Exception as ex:
        print(f"[NEXUS/reasoning] trace read failed: {ex}", flush=True)
        return []

    cutoff = None
    if since_minutes is not None:
        cutoff = time.time() - since_minutes * 60

    matches = []
    for entry in traces.values():
        if task_prefix and not entry.get("task", "").startswith(task_prefix):
            continue
        if action_type and entry.get("action_type") != action_type:
            continue
        if cutoff is not None:
            ts = entry.get("created_at", "")
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                if t < cutoff:
                    continue
            except Exception:
                continue
        matches.append(entry)

    matches.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return matches[:limit]


def stats() -> dict[str, Any]:
    """Summary of recent reasoning traces."""
    all_traces = search_traces(limit=10_000)
    completed = [t for t in all_traces if t.get("outcome_success") is not None]
    successes = sum(1 for t in completed if t.get("outcome_success"))
    by_action: dict[str, int] = {}
    for t in all_traces:
        by_action[t.get("action_type", "unknown")] = by_action.get(t.get("action_type", "unknown"), 0) + 1
    return {
        "total": len(all_traces),
        "completed": len(completed),
        "pending": len(all_traces) - len(completed),
        "success_rate": round(successes / len(completed), 3) if completed else 0.0,
        "by_action_type": by_action,
    }
