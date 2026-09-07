"""SPECTRE Soak Harness — validación de estabilidad bajo carga sintética.

Simula tráfico realista durante N segundos y reporta:
  - Reflex rate-limit behavior (5/30s)
  - Internal sender filter effectiveness
  - Queue growth/drain dynamics
  - Cortex processing stats
  - Memory/state integrity after soak

Usage:
  python3 soak_harness.py --duration 60   # 60s soak
  python3 soak_harness.py --duration 300  # 5min soak (pre-production)
  python3 soak_harness.py --dry-run       # quick 10s sanity check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))
sys.path.insert(0, str(Path(__file__).parent.parent / "handlers"))

# Fresh imports for soak
for mod in list(sys.modules.keys()):
    if any(x in mod for x in ["spectre_handlers", "contract_layer", "cortex", "episodic_api", "llm_client"]):
        del sys.modules[mod]

import spectre_handlers as sh
from spectre_handlers import _reflex_timestamps, ESCALATION_QUEUE_PATH
import cortex as cx
from contract_layer import _invocation_timestamps as _budget_ts

# ── Soak configuration ────────────────────────────────────────────────────────

INTERNAL_SENDERS = ["NEXUS", "ADA", "JARVIS", "ALICE", "DUM", "EVENT_BUS_DAEMON"]
EXTERNAL_SENDERS = ["external_user", "sensor_A", "webhook_client", "api_gateway"]

EVENT_TEMPLATES = [
    "status update from monitoring system",
    "alert: anomaly detected in pipeline",
    "user query: what is the current task?",
    "heartbeat ping from service layer",
    "data ingestion complete — 1420 records",
    "request: escalate to cortex for analysis",
    "system health check initiated",
    "new event from external data source",
]


def _make_event(event_id: str, sender: str, content: str) -> dict:
    return {
        "id": event_id,
        "from": sender,
        "content": content,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


# ── Soak runner ───────────────────────────────────────────────────────────────

class SoakStats:
    def __init__(self) -> None:
        self.sent = 0
        self.dropped_internal = 0
        self.dropped_rate_limit = 0
        self.dropped_cooldown = 0
        self.escalated = 0
        self.cortex_processed = 0
        self.cortex_cache_hits = 0
        self.cortex_llm_calls = 0
        self.cortex_llm_failures = 0
        self.queue_max_depth = 0
        self.log_lines: list[str] = []

    def snapshot(self) -> dict:
        return {
            "sent": self.sent,
            "dropped_internal": self.dropped_internal,
            "dropped_rate_limit": self.dropped_rate_limit,
            "dropped_cooldown": self.dropped_cooldown,
            "escalated": self.escalated,
            "cortex_processed": self.cortex_processed,
            "cortex_cache_hits": self.cortex_cache_hits,
            "cortex_llm_calls": self.cortex_llm_calls,
            "cortex_llm_failures": self.cortex_llm_failures,
            "queue_max_depth": self.queue_max_depth,
        }


async def _inject_events(
    stats: SoakStats,
    duration_s: float,
    events_per_second: float = 2.0,
) -> None:
    """Inject synthetic events into on_message_incoming."""
    interval = 1.0 / events_per_second
    t_end = time.monotonic() + duration_s
    i = 0

    while time.monotonic() < t_end:
        # Mix: 30% internal (should be dropped), 70% external
        if i % 10 < 3:
            sender = INTERNAL_SENDERS[i % len(INTERNAL_SENDERS)]
        else:
            sender = EXTERNAL_SENDERS[i % len(EXTERNAL_SENDERS)]

        content = EVENT_TEMPLATES[i % len(EVENT_TEMPLATES)]
        event_id = f"soak_{i:06d}"
        evt = _make_event(event_id, sender, content)

        # Capture logs to classify outcome
        logs: list[str] = []
        import unittest.mock as mock
        with mock.patch("asyncio.create_task"):
            with mock.patch("builtins.print", side_effect=lambda *a, **kw: logs.append(str(a[0]) if a else "")):
                await sh.on_message_incoming(evt)

        stats.sent += 1
        for line in logs:
            if "internal sender" in line:
                stats.dropped_internal += 1
            elif "rate limit" in line and "dropped" in line:
                stats.dropped_rate_limit += 1
            elif "cooldown" in line:
                stats.dropped_cooldown += 1
            elif "escalation queued" in line:
                stats.escalated += 1

        # Check queue depth
        if ESCALATION_QUEUE_PATH.exists():
            try:
                q = json.loads(ESCALATION_QUEUE_PATH.read_text())
                if len(q) > stats.queue_max_depth:
                    stats.queue_max_depth = len(q)
            except Exception:
                pass

        i += 1
        await asyncio.sleep(interval)


async def _drain_cortex(stats: SoakStats, duration_s: float) -> None:
    """Run cortex for drain_duration, collecting stats."""
    from unittest.mock import AsyncMock, patch

    stop = asyncio.Event()

    async def _mock_process(entry, processed_ids):
        stats.cortex_processed += 1
        event_id = entry.get("event", {}).get("id", "")
        if event_id:
            cx._save_processed_id(event_id, processed_ids)
        return True

    with patch.object(cx, "_process_entry", side_effect=_mock_process):
        try:
            await asyncio.wait_for(
                cx.cortex_loop(poll_interval_s=1.0, max_per_tick=5, stop_event=stop),
                timeout=duration_s,
            )
        except asyncio.TimeoutError:
            stop.set()


async def run_soak(duration_s: float, events_per_second: float = 2.0) -> SoakStats:
    """Run the full soak: inject events + drain cortex concurrently."""
    stats = SoakStats()

    # Reset state
    ESCALATION_QUEUE_PATH.write_text("[]")
    _reflex_timestamps.clear()
    _budget_ts.clear()
    cx._CORTEX_RUNNING = False

    print(f"\n[soak] Starting {duration_s}s soak @ {events_per_second} evt/s")
    print(f"[soak] Inject phase: {duration_s}s")

    t_start = time.monotonic()

    # Phase 1: inject events
    await _inject_events(stats, duration_s, events_per_second)

    elapsed_inject = time.monotonic() - t_start
    print(f"\n[soak] Inject complete in {elapsed_inject:.1f}s")
    print(f"  sent={stats.sent}, escalated={stats.escalated}, "
          f"dropped_internal={stats.dropped_internal}, "
          f"dropped_rate_limit={stats.dropped_rate_limit}")

    # Phase 2: drain cortex (10s or remaining queue)
    if stats.escalated > 0:
        print(f"\n[soak] Draining cortex ({min(10, stats.escalated)}s max)...")
        cx._CORTEX_RUNNING = False
        await _drain_cortex(stats, min(10.0, stats.escalated * 1.5))

    # Collect final cortex stats from working_state
    cx_stats = cx.cortex_stats()
    stats.cortex_cache_hits = cx_stats.get("cache_hits", 0)
    stats.cortex_llm_calls = cx_stats.get("llm_calls", 0)
    stats.cortex_llm_failures = cx_stats.get("llm_failures", 0)

    return stats


def _verify_integrity(stats: SoakStats) -> list[str]:
    """Post-soak integrity checks. Returns list of failures (empty = OK)."""
    failures = []

    # 1. Queue should be bounded (≤ 20)
    if ESCALATION_QUEUE_PATH.exists():
        try:
            q = json.loads(ESCALATION_QUEUE_PATH.read_text())
            if len(q) > 20:
                failures.append(f"Queue exceeded cap: {len(q)} > 20")
        except Exception as e:
            failures.append(f"Queue read error: {e}")

    # 2. working_state must be valid JSON
    ws_path = Path(__file__).parent.parent / "state" / "working_state.json"
    if ws_path.exists():
        try:
            state = json.loads(ws_path.read_text())
            if "agent" not in state:
                failures.append("working_state missing 'agent' key")
        except Exception as e:
            failures.append(f"working_state corrupt: {e}")

    # 3. All internal events must have been dropped
    total_external = stats.sent - stats.dropped_internal
    if stats.escalated + stats.dropped_rate_limit + stats.dropped_cooldown != total_external:
        failures.append(
            f"External event accounting mismatch: "
            f"external={total_external}, "
            f"escalated+dropped={stats.escalated + stats.dropped_rate_limit + stats.dropped_cooldown}"
        )

    # 4. No negative stats
    for field in ["sent", "escalated", "dropped_internal", "dropped_rate_limit"]:
        val = getattr(stats, field)
        if val < 0:
            failures.append(f"{field} is negative: {val}")

    return failures


def _print_report(stats: SoakStats, duration_s: float, failures: list[str]) -> None:
    print(f"\n{'='*60}")
    print(f"SPECTRE SOAK REPORT — {duration_s}s @ {stats.sent/duration_s:.1f} evt/s")
    print(f"{'='*60}")
    print(f"  Events sent:            {stats.sent}")
    print(f"  Internal dropped:       {stats.dropped_internal}")
    print(f"  Rate-limited dropped:   {stats.dropped_rate_limit}")
    print(f"  Cooldown dropped:       {stats.dropped_cooldown}")
    print(f"  Escalated to cortex:    {stats.escalated}")
    print(f"  Queue max depth:        {stats.queue_max_depth}/20")
    print(f"  Cortex processed:       {stats.cortex_processed}")
    print(f"")
    internal_rate = stats.dropped_internal / max(stats.sent, 1)
    escalation_rate = stats.escalated / max(stats.sent - stats.dropped_internal, 1)
    print(f"  Internal filter rate:   {internal_rate:.1%} (expect ~30%)")
    print(f"  Escalation rate:        {escalation_rate:.1%} (of external events)")
    print(f"")
    if failures:
        print(f"  ❌ INTEGRITY FAILURES: {len(failures)}")
        for f in failures:
            print(f"     • {f}")
        print(f"\nSOAK RESULT: FAIL")
    else:
        print(f"  ✅ All integrity checks passed")
        print(f"\nSOAK RESULT: PASS")
    print(f"{'='*60}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SPECTRE soak harness")
    parser.add_argument("--duration", type=float, default=30.0, help="Soak duration in seconds")
    parser.add_argument("--rate", type=float, default=2.0, help="Events per second")
    parser.add_argument("--dry-run", action="store_true", help="Quick 10s sanity check")
    args = parser.parse_args()

    if args.dry_run:
        args.duration = 10.0
        args.rate = 3.0

    stats = asyncio.run(run_soak(args.duration, args.rate))
    failures = _verify_integrity(stats)
    _print_report(stats, args.duration, failures)

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
