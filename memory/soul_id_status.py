#!/usr/bin/env python3
"""Print SOUL memory IDs with lifecycle status.

Use this before citing a SOUL id in reports. "Exists" is not enough:
invalidated/superseded rows must be called out explicitly.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncpg
from seal_secrets import pg_dsn


def _preview(text: str, width: int = 180) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


async def fetch_status(ids: list[int]) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        rows = await conn.fetch(
            """
            SELECT id, agent, scope, category, importance,
                   metadata->>'layer' AS layer,
                   invalid_at, superseded_by, created_at, updated_at,
                   left(content, 500) AS content
            FROM soul_v3.memories
            WHERE id = ANY($1::bigint[])
            ORDER BY id
            """,
            ids,
        )
    finally:
        await conn.close()

    found = {int(r["id"]): r for r in rows}
    result: list[dict[str, Any]] = []
    for mid in ids:
        row = found.get(mid)
        if row is None:
            result.append({"id": mid, "exists": False, "active": False})
            continue
        invalid_at = row["invalid_at"]
        result.append(
            {
                "id": int(row["id"]),
                "exists": True,
                "active": invalid_at is None,
                "agent": row["agent"],
                "scope": row["scope"],
                "category": row["category"],
                "importance": row["importance"],
                "layer": row["layer"],
                "invalid_at": invalid_at.isoformat() if invalid_at else None,
                "superseded_by": row["superseded_by"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                "preview": _preview(row["content"]),
            }
        )
    return result


def render_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "id\texists\tactive\tagent\tscope\tcategory\timportance\tlayer\tinvalid_at\tsuperseded_by\tpreview"
    ]
    for r in rows:
        lines.append(
            "\t".join(
                str(r.get(k, "") if r.get(k, "") is not None else "")
                for k in (
                    "id",
                    "exists",
                    "active",
                    "agent",
                    "scope",
                    "category",
                    "importance",
                    "layer",
                    "invalid_at",
                    "superseded_by",
                    "preview",
                )
            )
        )
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    rows = await fetch_status(args.ids)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render_table(rows))
    if args.require_active and any(not r.get("active") for r in rows):
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="+", type=int, help="SOUL memory ids")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument(
        "--require-active",
        action="store_true",
        help="Exit 2 if any id is missing or inactive",
    )
    return parser


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
