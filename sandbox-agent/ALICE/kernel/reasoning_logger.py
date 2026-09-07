"""ALICE reasoning_logger — dual-write trace store for analytical decisions.

Critical for an analyst: every conclusion must be traceable back to its
premises, supuestos, calculation steps, and confidence. Audit-grade by
default.

Dual-write contract (same as NEXUS/ADA/JARVIS):
1. /tmp/alice_reasoning_traces.jsonl — fast cache, fail-soft.
2. soul_v3.reasoning_traces — canonical truth for Soul DB queries.

Async-safe: uses loop.create_task when inside running asyncio loop,
falls back to direct asyncpg sync call otherwise.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_ID = "ALICE"
TRACE_LOG = Path("/tmp/alice_reasoning_traces.jsonl")

_DB_URL = os.getenv(
    "SEAL_DB_URL",
    "postgresql://seal:REDACTADO@localhost:5433/seal_memory",
)
_SCHEMA = os.getenv("SEAL_SCHEMA", "soul_v3")
_CONN_KWARGS = {"server_settings": {"search_path": _SCHEMA}}

# trace_id (uuid hex) -> Soul DB row id (BIGSERIAL) for outcome updates
_id_map: dict[str, int] = {}
_id_map_lock = threading.Lock()

_lock = threading.Lock()


async def _db_insert_trace(
    task: str,
    premises: list[str],
    reasoning: str,
    decision: str,
) -> int | None:
    """Insert a row into soul_v3.reasoning_traces and return its BIGSERIAL id."""
    try:
        import asyncpg
        conn = await asyncpg.connect(_DB_URL, **_CONN_KWARGS)
        try:
            row = await conn.fetchrow(
                """INSERT INTO soul_v3.reasoning_traces
                   (agent, task, premises, reasoning, conclusion, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $6)
                   RETURNING id""",
                AGENT_ID,
                task[:500],
                json.dumps(premises[:20]),
                reasoning[:2000],
                decision[:1000],
                datetime.now(timezone.utc),
            )
            return int(row["id"])
        finally:
            await conn.close()
    except Exception as ex:
        print(f"[ALICE/reasoning] db insert failed: {ex}", flush=True)
        return None


async def _db_update_outcome(row_id: int, outcome: str, success: bool) -> bool:
    try:
        import asyncpg
        conn = await asyncpg.connect(_DB_URL, **_CONN_KWARGS)
        try:
            await conn.execute(
                """UPDATE soul_v3.reasoning_traces
                   SET outcome=$1, outcome_success=$2, updated_at=$3
                   WHERE id=$4""",
                outcome[:1000],
                bool(success),
                datetime.now(timezone.utc),
                row_id,
            )
            return True
        finally:
            await conn.close()
    except Exception as ex:
        print(f"[ALICE/reasoning] db update failed: {ex}", flush=True)
        return False


def _schedule_or_run(coro):
    """Run coroutine on the existing loop if any, else block via asyncio.run."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        return loop.create_task(coro)
    return asyncio.run(coro)


def _append_trace(entry: dict[str, Any]) -> None:
    try:
        with _lock:
            with TRACE_LOG.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        print(f"[ALICE/reasoning] trace write failed: {ex}", flush=True)


def store_trace(
    task: str,
    input_excerpt: str,
    premises: list[str],
    reasoning: str,
    decision: str,
    confidence: float = 0.7,
    action_type: str = "analysis",
    assumptions: list[str] | None = None,
) -> str:
    trace_id = uuid.uuid4().hex
    entry: dict[str, Any] = {
        "trace_id": trace_id,
        "agent": AGENT_ID,
        "task": task,
        "action_type": action_type,
        "input_excerpt": (input_excerpt or "")[:500],
        "premises": premises[:20],
        "assumptions": (assumptions or [])[:20],
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
    try:
        Path(f"/tmp/alice_trace_start_{trace_id}.t").write_text(str(entry["_started_perf"]))
    except Exception:
        pass

    async def _persist():
        row_id = await _db_insert_trace(task, premises, reasoning, decision)
        if row_id is not None:
            with _id_map_lock:
                _id_map[trace_id] = row_id

    try:
        result = _schedule_or_run(_persist())
        if not isinstance(result, asyncio.Task):
            pass
    except Exception as ex:
        print(f"[ALICE/reasoning] persist scheduling failed: {ex}", flush=True)

    return trace_id


def update_trace_outcome(trace_id: str, outcome: str, outcome_success: bool) -> bool:
    latency_ms: int | None = None
    ts_file = Path(f"/tmp/alice_trace_start_{trace_id}.t")
    try:
        if ts_file.exists():
            started = float(ts_file.read_text())
            latency_ms = int((time.perf_counter() - started) * 1000)
            ts_file.unlink()
    except Exception:
        pass

    suffix = f" [latency_ms={latency_ms}]" if latency_ms is not None else ""
    full_outcome = (outcome or "")[:1000 - len(suffix)] + suffix

    _append_trace({
        "trace_id": trace_id,
        "agent": AGENT_ID,
        "_kind": "outcome_update",
        "outcome": full_outcome,
        "outcome_success": bool(outcome_success),
        "latency_ms": latency_ms,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

    with _id_map_lock:
        row_id = _id_map.pop(trace_id, None)
    if row_id is not None:
        try:
            _schedule_or_run(_db_update_outcome(row_id, full_outcome, outcome_success))
        except Exception as ex:
            print(f"[ALICE/reasoning] db update scheduling failed: {ex}", flush=True)

    return True


def search_traces(
    task_prefix: str = "",
    action_type: str = "",
    limit: int = 20,
    since_minutes: int | None = None,
) -> list[dict]:
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
    except Exception:
        return []

    cutoff = time.time() - since_minutes * 60 if since_minutes else None

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
