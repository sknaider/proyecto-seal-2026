#!/usr/bin/env python3
"""Analyze SOUL cognitive graph shadow telemetry.

Reads diagnostic/soul_cognitive_graph_shadow.jsonl and reports whether the
shadow scorer has enough evidence to promote from shadow-only to assist mode.
The report avoids printing query text unless explicitly requested.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
import re


DEFAULT_PATH = Path(__file__).parent / "diagnostic" / "soul_cognitive_graph_shadow.jsonl"
TOKEN_RE = re.compile(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+")


@dataclass(frozen=True)
class ShadowDecision:
    status: str
    reason: str
    ready_for_assist: bool


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((p / 100.0) * (len(ordered) - 1))
    index = max(0, min(len(ordered) - 1, index))
    return ordered[index]


def rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_rows(path: Path, *, since_ts: str | None = None, agent: str | None = None) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    cutoff = parse_ts(since_ts)
    rows: list[dict[str, Any]] = []
    invalid = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if cutoff is not None:
            ts = parse_ts(row.get("ts"))
            if ts is None or ts < cutoff:
                continue
        if agent and row.get("agent") != agent:
            continue
        rows.append(row)
    return rows, invalid


def list_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def query_token_count(row: dict[str, Any]) -> int:
    stored_count = row.get("query_token_count")
    if isinstance(stored_count, int | float):
        return int(stored_count)
    query = str(row.get("query_preview") or "")
    return len(TOKEN_RE.findall(query))


def filter_rows_by_query_tokens(rows: list[dict[str, Any]], min_query_tokens: int) -> list[dict[str, Any]]:
    if min_query_tokens <= 1:
        return rows
    return [row for row in rows if query_token_count(row) >= min_query_tokens]


def overlap_rate(base: list[str], shadow: list[str]) -> float | None:
    if not base and not shadow:
        return None
    union = set(base) | set(shadow)
    if not union:
        return None
    return len(set(base) & set(shadow)) / len(union)


def assist_effective_ids(base: list[str], shadow: list[str], *, preserve_top1: bool) -> list[str]:
    if not preserve_top1:
        return shadow
    if not base:
        return []
    fixed = base[:1]
    fixed_set = set(fixed)
    ordered = fixed + [memory_id for memory_id in shadow if memory_id in base and memory_id not in fixed_set]
    seen = set(ordered)
    ordered.extend(memory_id for memory_id in base if memory_id not in seen)
    return ordered


def analyze_rows(
    rows: list[dict[str, Any]],
    *,
    invalid_lines: int = 0,
    min_rows: int = 30,
    min_top1_agreement: float = 0.85,
    min_shadow_top1_in_base_topk: float = 0.95,
    max_p95_ms: float = 20.0,
    include_queries: bool = False,
    preserve_top1: bool = False,
) -> dict[str, Any]:
    latencies = [float(row["rank_ms"]) for row in rows if isinstance(row.get("rank_ms"), int | float)]
    candidate_counts = [
        int(row["candidate_count"]) for row in rows
        if isinstance(row.get("candidate_count"), int | float)
    ]

    top1_agree = 0
    shadow_top1_in_base_topk = 0
    topk_identical = 0
    overlaps: list[float] = []
    comparable = 0
    changed_top1: list[dict[str, Any]] = []

    for row in rows:
        base = list_ids(row.get("base_top_ids"))
        shadow = list_ids(row.get("shadow_top_ids"))
        if not base or not shadow:
            continue
        eval_ids = assist_effective_ids(base, shadow, preserve_top1=preserve_top1)
        comparable += 1
        if base[0] == eval_ids[0]:
            top1_agree += 1
        else:
            item = {
                "ts": row.get("ts"),
                "agent": row.get("agent"),
                "base_top1": base[0],
                "shadow_top1": eval_ids[0],
                "base_top_ids": base,
                "shadow_top_ids": eval_ids,
            }
            if include_queries:
                item["query_preview"] = row.get("query_preview")
            changed_top1.append(item)
        if eval_ids[0] in base:
            shadow_top1_in_base_topk += 1
        if base == eval_ids:
            topk_identical += 1
        overlap = overlap_rate(base, eval_ids)
        if overlap is not None:
            overlaps.append(overlap)

    top1_rate = rate(top1_agree, comparable)
    containment_rate = rate(shadow_top1_in_base_topk, comparable)
    p95_ms = percentile(latencies, 95)

    blockers: list[str] = []
    if len(rows) < min_rows:
        blockers.append(f"sample_count {len(rows)} < {min_rows}")
    if comparable == 0:
        blockers.append("no comparable rows")
    if top1_rate is None or top1_rate < min_top1_agreement:
        blockers.append(f"top1_agreement {top1_rate or 0:.3f} < {min_top1_agreement:.3f}")
    if containment_rate is None or containment_rate < min_shadow_top1_in_base_topk:
        blockers.append(
            f"shadow_top1_in_base_topk {containment_rate or 0:.3f} < "
            f"{min_shadow_top1_in_base_topk:.3f}"
        )
    if p95_ms is None or p95_ms > max_p95_ms:
        blockers.append(f"p95_ms {p95_ms if p95_ms is not None else 'n/a'} > {max_p95_ms}")
    if invalid_lines:
        blockers.append(f"invalid_json_lines {invalid_lines} > 0")

    ready = not blockers
    decision = ShadowDecision(
        status="ready_for_assist" if ready else "collect_more_shadow",
        reason="all thresholds passed" if ready else "; ".join(blockers),
        ready_for_assist=ready,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows),
        "invalid_lines": invalid_lines,
        "comparable_rows": comparable,
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "avg": mean(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": p95_ms,
            "max": max(latencies) if latencies else None,
        },
        "candidate_count": {
            "min": min(candidate_counts) if candidate_counts else None,
            "p50": percentile([float(v) for v in candidate_counts], 50),
            "max": max(candidate_counts) if candidate_counts else None,
        },
        "agreement": {
            "top1_hits": top1_agree,
            "top1_rate": top1_rate,
            "shadow_top1_in_base_topk_hits": shadow_top1_in_base_topk,
            "shadow_top1_in_base_topk_rate": containment_rate,
            "topk_identical_hits": topk_identical,
            "topk_identical_rate": rate(topk_identical, comparable),
            "avg_topk_overlap": mean(overlaps) if overlaps else None,
        },
        "changed_top1": changed_top1[:20],
        "thresholds": {
            "min_rows": min_rows,
            "min_top1_agreement": min_top1_agreement,
            "min_shadow_top1_in_base_topk": min_shadow_top1_in_base_topk,
            "max_p95_ms": max_p95_ms,
            "preserve_top1": preserve_top1,
        },
        "decision": asdict(decision),
    }


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def fmt_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def render_text(report: dict[str, Any], path: Path, since_ts: str | None) -> str:
    lat = report["latency_ms"]
    ag = report["agreement"]
    decision = report["decision"]
    lines = [
        "SOUL Cognitive Graph Shadow Analysis",
        f"source: {path}",
        f"evaluation: {'assist_preserve_top1' if report['thresholds'].get('preserve_top1') else 'raw_shadow'}",
    ]
    if since_ts:
        lines.append(f"since: {since_ts}")
    lines.extend([
        (
            f"rows: {report['rows']} comparable={report['comparable_rows']} "
            f"invalid={report['invalid_lines']}"
        ),
        (
            f"source_rows: {report.get('source_rows', report['rows'])} "
            f"filtered_out={report.get('filtered_out_rows', 0)} "
            f"min_query_tokens={report.get('min_query_tokens', 1)}"
        ),
        (
            "latency_ms: "
            f"p50={fmt_ms(lat['p50'])} p95={fmt_ms(lat['p95'])} max={fmt_ms(lat['max'])}"
        ),
        (
            "agreement: "
            f"top1={ag['top1_hits']}/{report['comparable_rows']} ({fmt_pct(ag['top1_rate'])}) "
            f"shadow_top1_in_base_topk={ag['shadow_top1_in_base_topk_hits']}/{report['comparable_rows']} "
            f"({fmt_pct(ag['shadow_top1_in_base_topk_rate'])}) "
            f"avg_overlap={fmt_pct(ag['avg_topk_overlap'])}"
        ),
        f"decision: {decision['status']} - {decision['reason']}",
    ])
    if report["changed_top1"]:
        lines.append("changed_top1 samples:")
        for item in report["changed_top1"][:5]:
            lines.append(
                f"- ts={item.get('ts')} base={item.get('base_top1')} shadow={item.get('shadow_top1')}"
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--since-ts", default=None)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--min-rows", type=int, default=30)
    parser.add_argument("--min-top1-agreement", type=float, default=0.85)
    parser.add_argument("--min-shadow-top1-in-base-topk", type=float, default=0.95)
    parser.add_argument("--max-p95-ms", type=float, default=20.0)
    parser.add_argument(
        "--min-query-tokens",
        type=int,
        default=1,
        help="exclude rows with fewer query_preview tokens from the promotion decision",
    )
    parser.add_argument("--include-queries", action="store_true")
    parser.add_argument(
        "--raw-shadow",
        action="store_true",
        help="evaluate raw shadow top ids instead of guarded assist behavior",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows, invalid = load_rows(args.path, since_ts=args.since_ts, agent=args.agent)
    source_rows = rows
    rows = filter_rows_by_query_tokens(rows, args.min_query_tokens)
    report = analyze_rows(
        rows,
        invalid_lines=invalid,
        min_rows=args.min_rows,
        min_top1_agreement=args.min_top1_agreement,
        min_shadow_top1_in_base_topk=args.min_shadow_top1_in_base_topk,
        max_p95_ms=args.max_p95_ms,
        include_queries=args.include_queries,
        preserve_top1=not args.raw_shadow,
    )
    report["source_rows"] = len(source_rows)
    report["filtered_out_rows"] = len(source_rows) - len(rows)
    report["min_query_tokens"] = args.min_query_tokens
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report, args.path, args.since_ts))
    return 0 if report["decision"]["ready_for_assist"] else 1


if __name__ == "__main__":
    sys.exit(main())
