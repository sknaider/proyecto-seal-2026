"""SPECTRE dream_consolidator — nightly cleanup and memory consolidation.

Ref: seal_dream.py pattern (equipo real) adapted for SPECTRE sandbox.
Runs once per night (triggered by daemon or external cron).
Guards: run_lock (fcntl), idempotent per calendar day.

Tasks performed:
  1. Episodic purge — prune escalation_queue older than TTL_DAYS
  2. Reasoning trace consolidation — compact completed traces to summary
  3. Working state cleanup — remove stale keys, cap prediction_cache
  4. Ocean snapshot — persist daily OCEAN state for trend analysis
  5. Stats report — write consolidated stats to working_state["dream_report"]

Sandbox: writes only to working_state.json and local temp files.
Production path: same logic, but stats also written to soul_v3.dream_log.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).parent.parent
WORKING_STATE_PATH = _BASE / "state" / "working_state.json"
ESCALATION_QUEUE_PATH = Path("/tmp/spectre_escalation_queue.json")
LOCK_PATH = Path("/tmp/spectre_dream_consolidator.lock")

AGENT_ID = "SPECTRE"
TTL_DAYS = 3
MAX_PREDICTION_CACHE = 50
MAX_COMPLETED_TRACES = 10  # keep only latest N completed traces after consolidation


# ── Lock: one dream run at a time ─────────────────────────────────────────────

def _acquire_run_lock() -> int | None:
    """Non-blocking exclusive lock. Returns fd on success, None if already running."""
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_WRONLY, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        return None
    except Exception as ex:
        print(f"[SPECTRE/dream] lock error: {ex}", flush=True)
        return None


def _release_run_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except Exception:
        pass


# ── State I/O ─────────────────────────────────────────────────────────────────

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


# ── Consolidation tasks ───────────────────────────────────────────────────────

def _task_episodic_purge(stats: dict) -> None:
    """Prune escalation_queue entries older than TTL_DAYS."""
    if not ESCALATION_QUEUE_PATH.exists():
        stats["episodic_purged"] = 0
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)
    try:
        queue: list[dict] = json.loads(ESCALATION_QUEUE_PATH.read_text())
    except Exception:
        stats["episodic_purged"] = 0
        return

    before = len(queue)
    kept = []
    for entry in queue:
        ts_str = entry.get("queued_at", "")
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                kept.append(entry)
        except Exception:
            kept.append(entry)  # keep unparseable entries

    purged = before - len(kept)
    ESCALATION_QUEUE_PATH.write_text(json.dumps(kept, ensure_ascii=False))
    stats["episodic_purged"] = purged
    print(f"[SPECTRE/dream] episodic purge: {purged} entries removed (kept {len(kept)})", flush=True)


def _task_trace_consolidation(state: dict, stats: dict) -> None:
    """Compact completed reasoning traces — keep only last N, summarize rest."""
    traces: list[dict] = state.get("reasoning_traces", [])
    completed = [t for t in traces if t.get("outcome_success") is not None]
    pending = [t for t in traces if t.get("outcome_success") is None]

    if len(completed) <= MAX_COMPLETED_TRACES:
        stats["traces_consolidated"] = 0
        stats["traces_pending"] = len(pending)
        return

    # Summarize older completed traces
    to_archive = completed[:-MAX_COMPLETED_TRACES]
    keep_completed = completed[-MAX_COMPLETED_TRACES:]

    successes = sum(1 for t in to_archive if t.get("outcome_success"))
    failures = len(to_archive) - successes

    summary = {
        "type": "consolidated_trace_summary",
        "archived_count": len(to_archive),
        "archived_successes": successes,
        "archived_failures": failures,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "oldest_ts": to_archive[0].get("created_at", "") if to_archive else "",
        "newest_ts": to_archive[-1].get("created_at", "") if to_archive else "",
    }

    state["reasoning_traces"] = pending + keep_completed
    state.setdefault("dream_summaries", [])
    state["dream_summaries"].append(summary)
    state["dream_summaries"] = state["dream_summaries"][-20:]

    stats["traces_consolidated"] = len(to_archive)
    stats["traces_pending"] = len(pending)
    print(
        f"[SPECTRE/dream] trace consolidation: archived {len(to_archive)} completed "
        f"({successes}✅ {failures}❌), kept {len(keep_completed)} recent",
        flush=True,
    )


def _task_working_state_cleanup(state: dict, stats: dict) -> None:
    """Remove stale keys; cap prediction_cache."""
    stale_keys = ["last_event_stale", "debug_scratch", "tmp_context"]
    removed = [k for k in stale_keys if k in state]
    for k in removed:
        del state[k]

    pred_cache: dict = state.get("prediction_cache", {})
    if len(pred_cache) > MAX_PREDICTION_CACHE:
        keys = list(pred_cache.keys())
        to_drop = keys[: len(keys) - MAX_PREDICTION_CACHE]
        for k in to_drop:
            del pred_cache[k]
        state["prediction_cache"] = pred_cache
        stats["prediction_cache_trimmed"] = len(to_drop)
    else:
        stats["prediction_cache_trimmed"] = 0

    stats["stale_keys_removed"] = removed
    if removed:
        print(f"[SPECTRE/dream] cleanup: removed stale keys {removed}", flush=True)


def _task_ocean_snapshot(state: dict, stats: dict) -> None:
    """Persist daily OCEAN state snapshot for trend analysis."""
    baseline = state.get("ocean_baseline")
    runtime = state.get("ocean_runtime")

    if not baseline:
        stats["ocean_snapshot"] = "skipped (no baseline)"
        return

    snap = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "baseline": baseline,
        "runtime": runtime or baseline,
        "total_delta_events": len(state.get("ocean_deltas", [])),
    }
    state.setdefault("ocean_daily_snapshots", [])
    state["ocean_daily_snapshots"].append(snap)
    state["ocean_daily_snapshots"] = state["ocean_daily_snapshots"][-30:]  # 30-day history
    stats["ocean_snapshot"] = "written"
    print(
        f"[SPECTRE/dream] ocean snapshot written — "
        f"O={runtime['O'] if runtime else baseline['O']:.3f}",
        flush=True,
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def run_consolidation(force: bool = False) -> dict[str, Any]:
    """Run all consolidation tasks. Returns stats dict.

    force=True: skip idempotency check (run even if already ran today).
    Returns {"skipped": True} if already ran today and force=False.
    """
    fd = _acquire_run_lock()
    if fd is None:
        print("[SPECTRE/dream] already running — skipped", flush=True)
        return {"skipped": True, "reason": "lock_held"}

    try:
        state = _read_state()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if not force and state.get("dream_last_run_date") == today:
            print(f"[SPECTRE/dream] already ran today ({today}) — skipped", flush=True)
            return {"skipped": True, "reason": "already_ran_today", "date": today}

        print(f"[SPECTRE/dream] starting consolidation run — {today}", flush=True)
        stats: dict[str, Any] = {"date": today, "started_at": datetime.now(timezone.utc).isoformat()}

        _task_episodic_purge(stats)
        _task_trace_consolidation(state, stats)
        _task_working_state_cleanup(state, stats)
        _task_ocean_snapshot(state, stats)

        stats["completed_at"] = datetime.now(timezone.utc).isoformat()
        state["dream_last_run_date"] = today
        state["dream_report"] = stats
        _write_state(state)

        print(
            f"[SPECTRE/dream] consolidation complete — "
            f"purged={stats['episodic_purged']} traces_archived={stats.get('traces_consolidated', 0)} "
            f"ocean={stats['ocean_snapshot']}",
            flush=True,
        )
        return stats

    finally:
        _release_run_lock(fd)


# ── CLI entry ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    force = "--force" in sys.argv
    result = run_consolidation(force=force)
    print(json.dumps(result, indent=2, ensure_ascii=False))
