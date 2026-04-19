#!/usr/bin/env python3
"""analyze_shadow_router.py — post-shadow analytics for shadow_router.jsonl.

Reads diagnostic/shadow_router.jsonl and emits aggregate + per-pred_type
breakdown. Designed to run the Saturday 2026-04-14 22:00 UTC review in
<1 second instead of 30min of ad-hoc querying.

Safe on small n: degrades gracefully when no latent_ok rows, no rows at all,
or missing fields.

Usage:
  python3 analyze_shadow_router.py                      # full report to stdout
  python3 analyze_shadow_router.py --since-ts ISO8601   # filter
  python3 analyze_shadow_router.py --json               # machine-readable
  python3 analyze_shadow_router.py --path /custom.jsonl
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent / "diagnostic" / "shadow_router.jsonl"


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def fmt_ms(v: float | None) -> str:
    return f"{v:7.1f}" if v is not None else "    n/a"


def fmt_rate(num: int, den: int) -> str:
    if den == 0:
        return " n/a "
    return f"{100*num/den:5.1f}%"


def load_rows(path: Path, since_ts: str | None) -> list[dict]:
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(2)
    rows: list[dict] = []
    cutoff = None
    if since_ts:
        cutoff = datetime.fromisoformat(since_ts.replace("Z", "+00:00"))
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cutoff is not None:
            try:
                rts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if rts < cutoff:
                continue
        rows.append(r)
    return rows


def bucket_stats(rows: list[dict]) -> dict:
    """Aggregate stats over a row list. All safe on empty."""
    n = len(rows)
    route_counts = Counter(r.get("route") for r in rows)
    latent_status_counts = Counter(r.get("latent_status") for r in rows)

    classify_all = [r["classify_ms"] for r in rows if isinstance(r.get("classify_ms"), (int, float))]
    magma_all = [r["magma_latency_ms"] for r in rows if isinstance(r.get("magma_latency_ms"), (int, float))]

    latent_ok_rows = [r for r in rows if r.get("latent_status") == "ok"]
    latent_ok_lats = [
        r["latent_latency_ms"] for r in latent_ok_rows
        if isinstance(r.get("latent_latency_ms"), (int, float))
    ]

    # Agreement: only where latent_status=ok AND the field is boolean (not None)
    agree_top5_samples = [
        r["agreement_top5"] for r in latent_ok_rows
        if isinstance(r.get("agreement_top5"), bool)
    ]
    agree_top1_samples = [
        r["agreement_top1"] for r in latent_ok_rows
        if isinstance(r.get("agreement_top1"), bool)
    ]

    return {
        "n": n,
        "routed_latent": route_counts.get("latent", 0),
        "routed_magma": route_counts.get("magma", 0),
        "classify_p50": pct(classify_all, 50),
        "classify_p95": pct(classify_all, 95),
        "magma_p50": pct(magma_all, 50),
        "magma_p95": pct(magma_all, 95),
        "latent_status_counts": dict(latent_status_counts),
        "latent_ok_n": len(latent_ok_rows),
        "latent_p50_ok": pct(latent_ok_lats, 50),
        "latent_p95_ok": pct(latent_ok_lats, 95),
        "agreement_top5_n": len(agree_top5_samples),
        "agreement_top5_hits": sum(1 for x in agree_top5_samples if x),
        "agreement_top1_n": len(agree_top1_samples),
        "agreement_top1_hits": sum(1 for x in agree_top1_samples if x),
    }


def render_text(report: dict) -> str:
    lines: list[str] = []
    a = report["aggregate"]
    meta = report["meta"]

    lines.append("=" * 72)
    lines.append(f"SHADOW ROUTER ANALYSIS — {meta['generated_at']}")
    lines.append(f"source: {meta['path']}")
    if meta.get("since_ts"):
        lines.append(f"since:  {meta['since_ts']}")
    lines.append(f"rows:   {a['n']}")
    lines.append("=" * 72)

    if a["n"] == 0:
        lines.append("\n(no rows — nothing to analyze)")
        return "\n".join(lines)

    lines.append("\n[routing]")
    lines.append(f"  routed→latent : {a['routed_latent']:5d}  ({fmt_rate(a['routed_latent'], a['n'])})")
    lines.append(f"  routed→magma  : {a['routed_magma']:5d}  ({fmt_rate(a['routed_magma'], a['n'])})")

    lines.append("\n[classifier latency ms]")
    lines.append(f"  p50={fmt_ms(a['classify_p50'])}  p95={fmt_ms(a['classify_p95'])}")

    lines.append("\n[latent_status]")
    for status, count in sorted(a["latent_status_counts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {str(status):12s}: {count:5d}  ({fmt_rate(count, a['n'])})")

    lines.append("\n[latency ms — magma (all) vs latent (status=ok)]")
    lines.append(f"  magma  p50={fmt_ms(a['magma_p50'])}  p95={fmt_ms(a['magma_p95'])}")
    lines.append(f"  latent p50={fmt_ms(a['latent_p50_ok'])}  p95={fmt_ms(a['latent_p95_ok'])}  (n={a['latent_ok_n']})")

    lines.append("\n[agreement — where latent_status=ok]")
    lines.append(
        f"  top5 : {a['agreement_top5_hits']}/{a['agreement_top5_n']}  "
        f"({fmt_rate(a['agreement_top5_hits'], a['agreement_top5_n'])})"
    )
    lines.append(
        f"  top1 : {a['agreement_top1_hits']}/{a['agreement_top1_n']}  "
        f"({fmt_rate(a['agreement_top1_hits'], a['agreement_top1_n'])})"
    )

    # Per-pred_type breakdown
    by_type = report["by_pred_type"]
    if by_type:
        lines.append("\n[breakdown by pred_type]")
        header = (
            f"  {'pred_type':14s} {'n':>5s} {'→lat':>5s} {'→mag':>5s} "
            f"{'clsP50':>8s} {'magP50':>8s} {'latP50':>8s} {'top5':>7s} {'top1':>7s}"
        )
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for ptype in sorted(by_type.keys(), key=lambda k: -by_type[k]["n"]):
            b = by_type[ptype]
            lines.append(
                f"  {str(ptype):14s} {b['n']:5d} {b['routed_latent']:5d} {b['routed_magma']:5d} "
                f"{fmt_ms(b['classify_p50']):>8s} {fmt_ms(b['magma_p50']):>8s} {fmt_ms(b['latent_p50_ok']):>8s} "
                f"{fmt_rate(b['agreement_top5_hits'], b['agreement_top5_n']):>7s} "
                f"{fmt_rate(b['agreement_top1_hits'], b['agreement_top1_n']):>7s}"
            )

    disagreements = report.get("top_disagreements") or []
    if disagreements:
        lines.append(f"\n[top {len(disagreements)} disagreements — latent_status=ok, agreement_top5=False]")
        for i, d in enumerate(disagreements, 1):
            top1_mark = "T1=" + ("✓" if d["agreement_top1"] else "✗")
            lines.append(f"  #{i} [{d['pred_type']:10s}] {top1_mark}  {d['query']}")
            lines.append(f"      latent_ids: {d['latent_ids']}")
            lines.append(f"      magma_ids : {d['magma_ids']}")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--since-ts", default=None, help="ISO8601 cutoff")
    ap.add_argument("--json", action="store_true", help="emit JSON report")
    ap.add_argument("--top-n-disagreement", type=int, default=0,
                    help="show top-N queries where latent and magma disagree at top5 (default 0 = off)")
    args = ap.parse_args()

    rows = load_rows(args.path, args.since_ts)

    by_type_rows: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type_rows[r.get("pred_type") or "unknown"].append(r)

    disagreements: list[dict] = []
    if args.top_n_disagreement > 0:
        for r in rows:
            if r.get("latent_status") != "ok":
                continue
            if r.get("agreement_top5") is not False:
                continue
            disagreements.append({
                "ts": r.get("ts"),
                "query": (r.get("query") or "")[:80],
                "pred_type": r.get("pred_type"),
                "latent_status": r.get("latent_status"),
                "latent_ids": (r.get("latent_ids") or [])[:5],
                "magma_ids": (r.get("magma_ids") or [])[:5],
                "agreement_top5": r.get("agreement_top5"),
                "agreement_top1": r.get("agreement_top1"),
            })
        disagreements = disagreements[: args.top_n_disagreement]

    report = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "path": str(args.path),
            "since_ts": args.since_ts,
            "top_n_disagreement": args.top_n_disagreement,
        },
        "aggregate": bucket_stats(rows),
        "by_pred_type": {ptype: bucket_stats(rs) for ptype, rs in by_type_rows.items()},
        "top_disagreements": disagreements,
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
