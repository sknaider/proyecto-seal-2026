"""ADA reasoning_logger — dual-write trace store (Soul DB canonical).

Same architecture as JARVIS/NEXUS reasoning_logger:
- Soul DB schema soul_v3.reasoning_traces is canonical truth
- Local /tmp/ada_reasoning_traces.jsonl is fail-soft cache
- UUID hex local_trace_id encoded as `[local:UUID12]` tag in task field
- latency_ms encoded in outcome text
- asyncio loop detection: schedule async if running, else asyncio.run

Schema reality verified live 2026-05-04: table has no `metadata` column —
encoding the local trace_id in task is the workaround.
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

AGENT_ID = "ADA"
TRACE_LOG = Path("/tmp/ada_reasoning_traces.jsonl")
_MAX_LOG_LINES = 5000

_DB_URL = os.environ.get(
    "SEAL_DB_URL",
    "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory",
)
_SCHEMA = os.environ.get("SEAL_SCHEMA", "soul_v3")
_id_map: dict[str, int] = {}

_lock = threading.Lock()


def _append_trace(entry: dict[str, Any]) -> None:
    try:
        with _lock:
            with TRACE_LOG.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        print(f"[ADA/reasoning] trace write failed: {ex}", flush=True)


async def _persist_to_soul_db(local_trace_id: str, entry: dict[str, Any]) -> int | None:
    try:
        import asyncpg  # type: ignore
    except ImportError:
        return None
    try:
        conn = await asyncpg.connect(
            _DB_URL,
            server_settings={"search_path": _SCHEMA},
        )
        try:
            tagged_task = f"{entry['task']} [local:{local_trace_id[:12]}]"
            row = await conn.fetchrow(
                f"""INSERT INTO {_SCHEMA}.reasoning_traces
                    (agent, task, premises, reasoning, conclusion)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id""",
                AGENT_ID,
                tagged_task,
                json.dumps(entry["premises"]),
                entry["reasoning"],
                entry["decision"],
            )
            return int(row["id"]) if row else None
        finally:
            await conn.close()
    except Exception as ex:
        print(f"[ADA/reasoning] soul DB insert failed: {ex}", flush=True)
        return None


async def _update_outcome_in_soul_db(db_id: int, outcome: str, outcome_success: bool, latency_ms: int | None) -> bool:
    try:
        import asyncpg  # type: ignore
    except ImportError:
        return False
    try:
        conn = await asyncpg.connect(
            _DB_URL,
            server_settings={"search_path": _SCHEMA},
        )
        try:
            outcome_with_latency = (
                f"{outcome} [latency_ms={latency_ms}]" if latency_ms is not None else outcome
            )
            await conn.execute(
                f"""UPDATE {_SCHEMA}.reasoning_traces
                    SET outcome = $1,
                        outcome_success = $2,
                        updated_at = NOW()
                    WHERE id = $3""",
                outcome_with_latency,
                outcome_success,
                db_id,
            )
            return True
        finally:
            await conn.close()
    except Exception as ex:
        print(f"[ADA/reasoning] soul DB update failed: {ex}", flush=True)
        return False


async def _persist_async_and_map(local_trace_id: str, entry: dict[str, Any]) -> None:
    db_id = await _persist_to_soul_db(local_trace_id, entry)
    if db_id is not None:
        _id_map[local_trace_id] = db_id


def _persist_sync(local_trace_id: str, entry: dict[str, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_persist_async_and_map(local_trace_id, entry))
    except RuntimeError:
        try:
            db_id = asyncio.run(_persist_to_soul_db(local_trace_id, entry))
            if db_id is not None:
                _id_map[local_trace_id] = db_id
        except Exception as ex:
            print(f"[ADA/reasoning] persist_sync failed: {ex}", flush=True)


def _update_outcome_sync(local_trace_id: str, outcome: str, outcome_success: bool, latency_ms: int | None) -> None:
    db_id = _id_map.get(local_trace_id)
    if db_id is None:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_update_outcome_in_soul_db(db_id, outcome, outcome_success, latency_ms))
    except RuntimeError:
        try:
            asyncio.run(_update_outcome_in_soul_db(db_id, outcome, outcome_success, latency_ms))
        except Exception as ex:
            print(f"[ADA/reasoning] outcome sync failed: {ex}", flush=True)


def store_trace(
    task: str,
    input_excerpt: str,
    premises: list[str],
    reasoning: str,
    decision: str,
    confidence: float = 0.7,
    action_type: str = "general",
) -> str:
    trace_id = uuid.uuid4().hex
    entry: dict[str, Any] = {
        "trace_id": trace_id,
        "agent": AGENT_ID,
        "task": task,
        "action_type": action_type,
        "input_excerpt": (input_excerpt or "")[:500],
        "premises": premises[:20],
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
    public_entry = {k: v for k, v in entry.items() if not k.startswith("_")}
    _append_trace(public_entry)
    try:
        Path(f"/tmp/ada_trace_start_{trace_id}.t").write_text(str(entry["_started_perf"]))
    except Exception:
        pass
    _persist_sync(trace_id, public_entry)
    return trace_id


def update_trace_outcome(trace_id: str, outcome: str, outcome_success: bool) -> bool:
    latency_ms: int | None = None
    ts_file = Path(f"/tmp/ada_trace_start_{trace_id}.t")
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
    _update_outcome_sync(trace_id, update_entry["outcome"], update_entry["outcome_success"], latency_ms)
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
