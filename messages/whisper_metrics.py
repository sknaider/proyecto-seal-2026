#!/usr/bin/env python3
"""
Whisper Metrics — E2 (Whisper v2 Extensions, 2026-04-26)
Dashboard CLI sobre whisper_audit.jsonl.
Uso: python3 whisper_metrics.py [--hours N] [--json]
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

AUDIT_LOG = Path(__file__).parent / "whisper_audit.jsonl"
KNOWN_AGENTS = ["ADA", "JARVIS", "ALICE", "DUM", "NEXUS"]


def load_entries(since: datetime) -> list[dict]:
    entries = []
    if not AUDIT_LOG.exists():
        return entries
    with AUDIT_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                ts_raw = d.get("ts", "")
                if not ts_raw:
                    continue
                ts = datetime.fromisoformat(ts_raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= since:
                    entries.append({**d, "_ts": ts})
            except Exception:
                continue
    return entries


def build_metrics(entries: list[dict]) -> dict:
    total = len(entries)
    acked = sum(1 for e in entries if e.get("receipt") == "acked")
    rejected = total - acked

    by_tier: dict[str, int] = defaultdict(int)
    by_rejection: dict[str, int] = defaultdict(int)
    matrix: dict[tuple, int] = defaultdict(int)
    last_received: dict[str, dict] = {}

    for e in entries:
        tier = str(e.get("tier", "?"))
        by_tier[tier] += 1

        if e.get("receipt") != "acked":
            reason = e.get("reject_reason", "unknown")
            by_rejection[reason] += 1

        frm = e.get("from", "?")
        to = e.get("to", "?")
        if e.get("receipt") == "acked":
            matrix[(frm, to)] += 1
            if to not in last_received or e["_ts"] > last_received[to]["_ts"]:
                last_received[to] = e

    # Top pairs
    top_pairs = sorted(
        [(k, v) for k, v in matrix.items()], key=lambda x: -x[1]
    )[:5]

    # Isolated agents (no whispers received in window)
    active_receivers = {e.get("to") for e in entries if e.get("receipt") == "acked"}
    isolated = [a for a in KNOWN_AGENTS if a not in active_receivers]

    return {
        "total": total,
        "acked": acked,
        "rejected": rejected,
        "by_tier": dict(by_tier),
        "by_rejection": dict(by_rejection),
        "matrix": {f"{k[0]}→{k[1]}": v for k, v in matrix.items()},
        "last_received": {
            k: {"from": v.get("from"), "ts": v.get("ts"), "purpose": v.get("purpose")}
            for k, v in last_received.items()
        },
        "isolated": isolated,
        "top_pairs": [{"pair": f"{k[0]}↔{k[1]}", "count": v} for k, v in top_pairs],
    }


def print_table(metrics: dict, hours: int, now: datetime) -> None:
    print(f"\n{'='*52}")
    print(f"  Whisper Metrics — last {hours}h  ({now.strftime('%Y-%m-%d %H:%M UTC')})")
    print(f"{'='*52}")
    print(f"  Total : {metrics['total']}  (acked: {metrics['acked']}, rejected: {metrics['rejected']})")

    bt = metrics["by_tier"]
    tiers = "  ".join(f"T{t}={bt.get(t,0)}" for t in ["1","2","3"])
    print(f"  Tiers : {tiers}")

    if metrics["by_rejection"]:
        rej = "  ".join(f"{k}={v}" for k, v in metrics["by_rejection"].items())
        print(f"  Reject: {rej}")

    # Matrix
    agents = [a for a in KNOWN_AGENTS if any(
        k.startswith(a + "→") or k.endswith("→" + a)
        for k in metrics["matrix"]
    )]
    if not agents:
        agents = KNOWN_AGENTS
    print(f"\n  Matrix from→to (acks):")
    col_w = 7
    header = " " * 10 + "".join(f"{a[:5]:>{col_w}}" for a in agents)
    print(f"  {header}")
    for src in agents:
        row = f"  {src:<9} "
        for dst in agents:
            if src == dst:
                row += f"{'—':>{col_w}}"
            else:
                cnt = metrics["matrix"].get(f"{src}→{dst}", 0)
                row += f"{cnt if cnt else '.':>{col_w}}"
        print(row)

    # Last whisper per agent
    print(f"\n  Last whisper received per agent:")
    for agent in KNOWN_AGENTS:
        info = metrics["last_received"].get(agent)
        if info:
            ts = datetime.fromisoformat(info["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = int((now - ts).total_seconds())
            ago = f"{delta}s ago" if delta < 3600 else f"{delta//3600}h ago"
            print(f"    {agent:<7} <- {info['from']:<7} at {ts.strftime('%H:%M:%S')} ({ago})  [{info['purpose']}]")
        else:
            print(f"    {agent:<7} — no whispers received in window")

    if metrics["isolated"]:
        print(f"\n  ⚠ Isolated (no whispers received): {', '.join(metrics['isolated'])}")
    else:
        print(f"\n  ✓ No isolated agents")

    if metrics["top_pairs"]:
        print(f"\n  Top pairs:")
        for p in metrics["top_pairs"]:
            print(f"    {p['pair']}: {p['count']}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Whisper audit metrics dashboard")
    parser.add_argument("--hours", type=int, default=24, help="Window in hours (default 24)")
    parser.add_argument("--json", action="store_true", dest="json_out", help="Output JSON")
    args = parser.parse_args()

    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(hours=args.hours)
    entries = load_entries(since)
    metrics = build_metrics(entries)
    metrics["window_hours"] = args.hours
    metrics["generated_at"] = now.isoformat()

    if args.json_out:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        print_table(metrics, args.hours, now)


if __name__ == "__main__":
    main()
