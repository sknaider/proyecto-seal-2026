#!/usr/bin/env python3
"""Read-only SOUL memory quality audit.

This report is meant for team review before any remediation. It does not update,
delete, invalidate, consolidate, or archive memories.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn


DB_URL = os.environ.get("SEAL_PG_DSN", pg_dsn(required=True))
DEFAULT_OUT_DIR = Path("memory/diagnostic/results")

CHAT_EXCERPT_RE = re.compile(r"^\[[A-ZÁÉÍÓÚÑ]+\]:")
TOKEN_RE = re.compile(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+")


@dataclass(frozen=True)
class Finding:
    id: int
    category: str
    importance: int
    created_at: str | None
    length: int
    token_count: int
    risk_score: int
    flags: list[str]
    suggested_action: str
    snippet: str


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip().lower()
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<date>", text)
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "<time>", text)
    return text


def token_count(text: str) -> int:
    return len(TOKEN_RE.findall(text or ""))


def fingerprint(text: str, chars: int = 120) -> str:
    norm = normalize_text(text)[:chars]
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def classify(row: asyncpg.Record, duplicate_group_size: int) -> tuple[int, list[str], str]:
    content = row["content"] or ""
    compact = re.sub(r"\s+", " ", content).strip()
    length = len(compact)
    tokens = token_count(compact)
    importance = int(row["importance"] or 0)
    category = row["category"] or "unknown"
    utility = float(row["utility_score"] or 0.5)
    query_count = int(row["query_count"] or 0)
    recall_count = int(row["recall_count"] or 0)
    days_old = float(row["days_old"] or 0.0)

    risk = 0
    flags: list[str] = []

    if importance >= 8 and CHAT_EXCERPT_RE.search(compact) and length < 180:
        risk += 45
        flags.append("terse_chat_excerpt_high_importance")
    if importance >= 9 and tokens < 14:
        risk += 25
        flags.append("too_few_tokens_for_high_importance")
    if duplicate_group_size >= 3:
        risk += min(25, duplicate_group_size * 4)
        flags.append(f"near_duplicate_prefix_group_{duplicate_group_size}")
    if category == "dynamic" and importance >= 8 and days_old > 14:
        risk += 25
        flags.append("stale_dynamic_high_importance")
    if utility < 0.25 and importance >= 8 and days_old > 30:
        risk += 15
        flags.append("low_utility_high_importance")
    if query_count == 0 and recall_count == 0 and importance >= 8 and days_old > 30:
        risk += 10
        flags.append("never_recalled_high_importance")
    if not row["has_embedding"]:
        risk += 20
        flags.append("missing_embedding")
    if not row["has_bm25"]:
        risk += 20
        flags.append("missing_bm25")

    if not flags:
        return 0, [], "keep"
    if "missing_embedding" in flags or "missing_bm25" in flags:
        action = "repair_index"
    elif "near_duplicate_prefix_group_" in " ".join(flags):
        action = "review_consolidate_or_lower_duplicates"
    elif "stale_dynamic_high_importance" in flags:
        action = "review_lower_importance_or_archive"
    elif "terse_chat_excerpt_high_importance" in flags:
        action = "review_lower_importance_unless_protected"
    else:
        action = "manual_review"
    return min(100, risk), flags, action


async def load_rows(conn: asyncpg.Connection, agent: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, agent, category, content, importance, created_at,
               embedding IS NOT NULL AS has_embedding,
               embedding_bm25 IS NOT NULL AS has_bm25,
               COALESCE(utility_score, 0.5) AS utility_score,
               COALESCE(query_count, 0) AS query_count,
               COALESCE(recall_count, 0) AS recall_count,
               EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0 AS days_old
        FROM soul_v3.memories
        WHERE agent = $1
          AND invalid_at IS NULL
          AND content IS NOT NULL
        ORDER BY importance DESC, created_at DESC, id DESC
        """,
        agent,
    )


def build_findings(rows: list[asyncpg.Record]) -> tuple[list[Finding], dict[str, Any]]:
    fingerprints = Counter(fingerprint(r["content"] or "") for r in rows)
    findings: list[Finding] = []
    flag_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    risk_by_category: dict[str, int] = defaultdict(int)

    for row in rows:
        compact = re.sub(r"\s+", " ", row["content"] or "").strip()
        category = row["category"] or "unknown"
        category_counts[category] += 1
        risk, flags, action = classify(row, fingerprints[fingerprint(compact)])
        if not flags:
            continue
        for flag in flags:
            flag_counts[flag] += 1
        action_counts[action] += 1
        risk_by_category[category] += 1
        findings.append(
            Finding(
                id=int(row["id"]),
                category=category,
                importance=int(row["importance"] or 0),
                created_at=row["created_at"].isoformat() if row["created_at"] else None,
                length=len(compact),
                token_count=token_count(compact),
                risk_score=risk,
                flags=flags,
                suggested_action=action,
                snippet=compact[:240],
            )
        )

    findings.sort(key=lambda f: (-f.risk_score, -f.importance, f.category, f.id))
    summary = {
        "active_memories": len(rows),
        "flagged_memories": len(findings),
        "flagged_pct": round(len(findings) / len(rows), 4) if rows else 0.0,
        "flags": dict(flag_counts.most_common()),
        "suggested_actions": dict(action_counts.most_common()),
        "active_by_category": dict(category_counts.most_common()),
        "flagged_by_category": dict(sorted(risk_by_category.items(), key=lambda x: (-x[1], x[0]))),
        "top_risk_ids": [f.id for f in findings[:25]],
    }
    return findings, summary


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, findings: list[Finding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "id",
                "category",
                "importance",
                "created_at",
                "length",
                "token_count",
                "risk_score",
                "flags",
                "suggested_action",
                "snippet",
            ],
        )
        writer.writeheader()
        for finding in findings:
            row = asdict(finding)
            row["flags"] = "|".join(finding.flags)
            writer.writerow(row)


def write_markdown(path: Path, agent: str, summary: dict[str, Any], findings: list[Finding]) -> None:
    lines = [
        f"# SOUL Memory Quality Audit — {agent}",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        "- Mode: read-only, no writes, no deletes, no invalidations",
        f"- Active memories scanned: {summary['active_memories']}",
        f"- Flagged memories: {summary['flagged_memories']} ({summary['flagged_pct'] * 100:.2f}%)",
        "",
        "## Suggested Actions",
    ]
    for action, count in summary["suggested_actions"].items():
        lines.append(f"- `{action}`: {count}")
    lines.extend(["", "## Flags"])
    for flag, count in summary["flags"].items():
        lines.append(f"- `{flag}`: {count}")
    lines.extend(["", "## Flagged By Category"])
    for category, count in summary["flagged_by_category"].items():
        lines.append(f"- `{category}`: {count}")
    lines.extend(["", "## Top Risk Samples"])
    for finding in findings[:30]:
        flags = ", ".join(f"`{flag}`" for flag in finding.flags)
        lines.append(
            f"- `#{finding.id}` `{finding.category}` imp={finding.importance} "
            f"risk={finding.risk_score} action=`{finding.suggested_action}` flags={flags} — {finding.snippet}"
        )
    lines.extend([
        "",
        "## Guardrail",
        "No remediation should run from this report without William's explicit OK and exact COUNT scope.",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


async def run(agent: str, out_dir: Path) -> dict[str, Any]:
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await load_rows(conn, agent)
    finally:
        await conn.close()

    findings, summary = build_findings(rows)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = out_dir / f"memory_quality_audit_{agent.lower()}_{timestamp}"
    latest_base = out_dir / f"memory_quality_audit_{agent.lower()}_latest"

    payload = {
        "agent": agent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "summary": summary,
        "findings": [asdict(f) for f in findings],
    }
    for target in (base, latest_base):
        write_json(target.with_suffix(".json"), payload)
        write_csv(target.with_suffix(".csv"), findings)
        write_markdown(target.with_suffix(".md"), agent, summary, findings)
    payload["files"] = {
        "json": str(latest_base.with_suffix(".json")),
        "csv": str(latest_base.with_suffix(".csv")),
        "markdown": str(latest_base.with_suffix(".md")),
    }
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only SOUL memory quality audit.")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = await run(args.agent.upper(), args.out_dir)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        summary = payload["summary"]
        print(
            f"memory_quality_audit agent={payload['agent']} active={summary['active_memories']} "
            f"flagged={summary['flagged_memories']} pct={summary['flagged_pct']:.4f}"
        )
        for action, count in summary["suggested_actions"].items():
            print(f"action.{action}={count}")
        print(f"files.json={payload['files']['json']}")
        print(f"files.csv={payload['files']['csv']}")
        print(f"files.markdown={payload['files']['markdown']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
