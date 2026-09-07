#!/usr/bin/env python3
"""Read-only pre-screener for SleepGate remediation candidates.

This is the bridge between agent rubrics and remediation. Default mode only
reports candidate batches and exact counts. It never mutates SOUL DB.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_rubric import load_agent_rubric
from seal_secrets import pg_dsn


DB_URL = os.environ.get("SEAL_PG_DSN", pg_dsn(required=True))
DEFAULT_OUT = Path("memory/diagnostic/results/memory_prescreener_latest.json")
CHAT_EXCERPT_RE = re.compile(r"^\[[A-ZÁÉÍÓÚÑ]+\]:")


@dataclass(frozen=True)
class CandidateBatch:
    action: str
    count: int
    risk: str
    reversible: bool
    where: str
    sample_ids: list[int]
    rationale: str


@dataclass(frozen=True)
class CandidateRecord:
    agent: str
    memory_id: int
    action: str
    risk: str
    reversible: bool
    matched_rule: str
    batch_count: int
    current: dict[str, Any]
    proposed: dict[str, Any]
    content_hash: str
    excerpt: str
    group: dict[str, Any] | None = None


def content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def safe_excerpt(content: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", content or "").strip()
    return compact[:limit]


def _rubric_payload(agent: str) -> dict[str, Any]:
    rubric = load_agent_rubric(agent)
    data = asdict(rubric)
    data.pop("raw", None)
    return data


async def _count(conn: asyncpg.Connection, sql: str, *args: object) -> int:
    return int(await conn.fetchval(sql, *args) or 0)


async def _sample_ids(conn: asyncpg.Connection, sql: str, *args: object) -> list[int]:
    rows = await conn.fetch(sql, *args)
    return [int(r["id"]) for r in rows]


def _row_current(row: asyncpg.Record) -> dict[str, Any]:
    created_at = row.get("created_at")
    return {
        "category": row.get("category"),
        "importance": row.get("importance"),
        "recall_count": row.get("recall_count") or 0,
        "query_count": row.get("query_count") or 0,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
    }


async def build_candidate_records(agent: str, batch_counts: dict[str, int]) -> list[CandidateRecord]:
    agent = agent.upper()
    rubric = load_agent_rubric(agent)
    conn = await asyncpg.connect(DB_URL)
    records: list[CandidateRecord] = []
    try:
        chat_action = f"lower_chat_excerpt_importance_to_{rubric.chat_excerpt_importance_cap}"
        rows = await conn.fetch(
            """
            SELECT id, category, importance, content, created_at,
                   COALESCE(recall_count, 0) AS recall_count,
                   COALESCE(query_count, 0) AS query_count
            FROM soul_v3.memories
            WHERE agent=$1 AND invalid_at IS NULL
              AND importance > $2
              AND length(regexp_replace(coalesce(content,''), '\\s+', ' ', 'g')) < $3
              AND coalesce(content,'') ~ '^\\[[A-ZÁÉÍÓÚÑ]+\\]:'
              AND category <> ALL($4::text[])
            ORDER BY importance DESC, created_at DESC, id DESC
            """,
            agent,
            rubric.chat_excerpt_importance_cap,
            rubric.short_memory_char_threshold,
            list(rubric.chat_excerpt_override_categories),
        )
        for row in rows:
            content = row["content"] or ""
            records.append(
                CandidateRecord(
                    agent=agent,
                    memory_id=int(row["id"]),
                    action=chat_action,
                    risk="low",
                    reversible=True,
                    matched_rule="short_chat_excerpt_importance_cap",
                    batch_count=batch_counts.get(chat_action, len(rows)),
                    current=_row_current(row),
                    proposed={"importance": rubric.chat_excerpt_importance_cap},
                    content_hash=content_hash(content),
                    excerpt=safe_excerpt(content),
                )
            )

        rows = await conn.fetch(
            """
            WITH ranked AS (
              SELECT id, content, category, importance, created_at,
                     COALESCE(recall_count, 0) AS recall_count,
                     COALESCE(query_count, 0) AS query_count,
                     row_number() OVER (
                       PARTITION BY content
                       ORDER BY importance DESC, created_at DESC, id DESC
                     ) AS rn,
                     first_value(id) OVER (
                       PARTITION BY content
                       ORDER BY importance DESC, created_at DESC, id DESC
                     ) AS keep_id,
                     count(*) OVER (PARTITION BY content) AS group_size
              FROM soul_v3.memories
              WHERE agent=$1 AND invalid_at IS NULL
            )
            SELECT *
            FROM ranked
            WHERE group_size >= 2 AND rn > 1
            ORDER BY group_size DESC, content, rn
            """,
            agent,
        )
        dupe_action = "invalidate_exact_duplicate_rows_keep_best_copy"
        for row in rows:
            content = row["content"] or ""
            records.append(
                CandidateRecord(
                    agent=agent,
                    memory_id=int(row["id"]),
                    action=dupe_action,
                    risk="medium",
                    reversible=True,
                    matched_rule="exact_content_duplicate_keep_best_copy",
                    batch_count=batch_counts.get(dupe_action, len(rows)),
                    current=_row_current(row),
                    proposed={"invalid_at": "NOW()", "superseded_by": int(row["keep_id"])},
                    content_hash=content_hash(content),
                    excerpt=safe_excerpt(content),
                    group={"keep_id": int(row["keep_id"]), "group_size": int(row["group_size"])},
                )
            )

        stale_action = "review_lower_or_archive_stale_dynamic"
        rows = await conn.fetch(
            """
            SELECT id, category, importance, content, created_at,
                   COALESCE(recall_count, 0) AS recall_count,
                   COALESCE(query_count, 0) AS query_count
            FROM soul_v3.memories
            WHERE agent=$1 AND invalid_at IS NULL
              AND category='dynamic'
              AND importance >= 8
              AND created_at < now() - interval '14 days'
            ORDER BY created_at ASC, id ASC
            """,
            agent,
        )
        for row in rows:
            content = row["content"] or ""
            records.append(
                CandidateRecord(
                    agent=agent,
                    memory_id=int(row["id"]),
                    action=stale_action,
                    risk="medium",
                    reversible=True,
                    matched_rule="stale_dynamic_high_importance",
                    batch_count=batch_counts.get(stale_action, len(rows)),
                    current=_row_current(row),
                    proposed={"manual_review": True},
                    content_hash=content_hash(content),
                    excerpt=safe_excerpt(content),
                )
            )
    finally:
        await conn.close()
    return records


def write_candidate_jsonl(path: Path, records: list[CandidateRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True) + "\n")


async def build_plan(agent: str) -> dict[str, Any]:
    agent = agent.upper()
    rubric = load_agent_rubric(agent)
    conn = await asyncpg.connect(DB_URL)
    batches: list[CandidateBatch] = []
    try:
        chat_cap_where = (
            "agent=$1 AND invalid_at IS NULL "
            "AND importance > $2 "
            "AND length(regexp_replace(coalesce(content,''), '\\s+', ' ', 'g')) < $3 "
            "AND coalesce(content,'') ~ '^\\[[A-ZÁÉÍÓÚÑ]+\\]:' "
            "AND category <> ALL($4::text[])"
        )
        chat_cap_args = (
            agent,
            rubric.chat_excerpt_importance_cap,
            rubric.short_memory_char_threshold,
            list(rubric.chat_excerpt_override_categories),
        )
        chat_cap_count = await _count(conn, f"SELECT count(*) FROM soul_v3.memories WHERE {chat_cap_where}", *chat_cap_args)
        chat_cap_ids = await _sample_ids(
            conn,
            f"SELECT id FROM soul_v3.memories WHERE {chat_cap_where} ORDER BY importance DESC, created_at DESC LIMIT 25",
            *chat_cap_args,
        )
        batches.append(
            CandidateBatch(
                action=f"lower_chat_excerpt_importance_to_{rubric.chat_excerpt_importance_cap}",
                count=chat_cap_count,
                risk="low",
                reversible=True,
                where=chat_cap_where,
                sample_ids=chat_cap_ids,
                rationale="Short chat excerpts are provenance/evidence, not automatically high-value semantic memories.",
            )
        )

        exact_dupe_rows = await conn.fetch(
            """
            WITH grouped AS (
              SELECT content, array_agg(id ORDER BY importance DESC, created_at DESC, id DESC) AS ids, count(*) AS n
              FROM soul_v3.memories
              WHERE agent=$1 AND invalid_at IS NULL
              GROUP BY content
              HAVING count(*) >= 2
            )
            SELECT COALESCE(sum(n - 1), 0)::int AS removable,
                   COALESCE(count(*), 0)::int AS groups
            FROM grouped
            """,
            agent,
        )
        removable = int(exact_dupe_rows[0]["removable"] or 0)
        groups = int(exact_dupe_rows[0]["groups"] or 0)
        dupe_ids = await _sample_ids(
            conn,
            """
            WITH grouped AS (
              SELECT content, (array_agg(id ORDER BY importance DESC, created_at DESC, id DESC))[2:26] AS drop_ids
              FROM soul_v3.memories
              WHERE agent=$1 AND invalid_at IS NULL
              GROUP BY content
              HAVING count(*) >= 2
            )
            SELECT unnest(drop_ids) AS id
            FROM grouped
            LIMIT 25
            """,
            agent,
        )
        batches.append(
            CandidateBatch(
                action="invalidate_exact_duplicate_rows_keep_best_copy",
                count=removable,
                risk="medium",
                reversible=True,
                where=f"exact content duplicates across {groups} groups; keep highest importance/newest row",
                sample_ids=dupe_ids,
                rationale="Exact duplicate rows add retrieval noise while preserving the same semantic content in the kept row.",
            )
        )

        stale_dynamic_where = (
            "agent=$1 AND invalid_at IS NULL "
            "AND category='dynamic' AND importance >= 8 "
            "AND created_at < now() - interval '14 days'"
        )
        stale_dynamic_count = await _count(conn, f"SELECT count(*) FROM soul_v3.memories WHERE {stale_dynamic_where}", agent)
        stale_dynamic_ids = await _sample_ids(
            conn,
            f"SELECT id FROM soul_v3.memories WHERE {stale_dynamic_where} ORDER BY created_at ASC LIMIT 25",
            agent,
        )
        batches.append(
            CandidateBatch(
                action="review_lower_or_archive_stale_dynamic",
                count=stale_dynamic_count,
                risk="medium",
                reversible=True,
                where=stale_dynamic_where,
                sample_ids=stale_dynamic_ids,
                rationale="Dynamic memories age quickly and should not stay high-importance without explicit agent rubric protection.",
            )
        )
    finally:
        await conn.close()

    return {
        "agent": agent,
        "mode": "read_only",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rubric": _rubric_payload(agent),
        "batches": [asdict(b) for b in batches],
        "guardrail": "Do not apply without explicit William OK, backup/archive, and post-run retrieval_eval_live.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build read-only memory remediation pre-screen plan.")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--candidates-out", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = await build_plan(args.agent)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidates_path = args.candidates_out
    candidate_count = None
    if candidates_path:
        batch_counts = {batch["action"]: int(batch["count"]) for batch in payload["batches"]}
        records = await build_candidate_records(payload["agent"], batch_counts)
        write_candidate_jsonl(candidates_path, records)
        candidate_count = len(records)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"memory_prescreener agent={payload['agent']} mode=read_only")
        for batch in payload["batches"]:
            print(f"action.{batch['action']}={batch['count']} risk={batch['risk']} reversible={batch['reversible']}")
        print(f"out={args.out}")
        if candidates_path:
            print(f"candidates_jsonl={candidates_path} records={candidate_count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
