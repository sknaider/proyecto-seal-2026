#!/usr/bin/env python3
"""Read-only status reporter for SOUL cognitive lifecycle."""

from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

DB_URL = os.environ.get(
    "SEAL_PG_DSN",
    pg_dsn(required=True),
)
REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "agents/ADA"


async def fetch_status(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT agent_name, state, runtime, budget_class, source_event_id,
               router_action, confidence, reason, since, expires_at,
               updated_at, feature_flag
        FROM soul_v3.agent_cognitive_lifecycle
        ORDER BY agent_name
        """
    )
    return [dict(r) for r in rows]


async def fetch_recent_events(conn: asyncpg.Connection, limit: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT agent_name, previous_state, new_state, runtime, budget_class,
               source_event_id, router_action, confidence, reason, feature_flag,
               created_at
        FROM soul_v3.agent_cognitive_lifecycle_events
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


def json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def write_markdown(status: list[dict[str, Any]], events: list[dict[str, Any]], path: Path) -> None:
    now = datetime.now(timezone.utc)
    lines = [
        "# Cognitive Lifecycle Status",
        "",
        f"Generated: {now.isoformat()}",
        "",
        "## Current",
        "",
        "| Agent | State | Runtime | Budget | Event | Confidence | Expires | Flag |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in status:
        expires = row["expires_at"].isoformat() if row.get("expires_at") else ""
        lines.append(
            "| {agent_name} | {state} | {runtime} | {budget_class} | {source_event_id} | {confidence:.2f} | {expires} | {feature_flag} |".format(
                expires=expires,
                **row,
            )
        )

    lines.extend(
        [
            "",
            "## Recent Events",
            "",
            "| Created | Agent | Previous | New | Runtime | Budget | Source Event |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in events:
        created = row["created_at"].isoformat() if row.get("created_at") else ""
        lines.append(
            "| {created} | {agent_name} | {previous_state} | {new_state} | {runtime} | {budget_class} | {source_event_id} |".format(
                created=created,
                **row,
            )
        )

    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Read-only reporter.",
            "- Does not touch legacy `soul_v3.agent_lifecycle`.",
            "- `feature_flag=shadow` means no process control is attached.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = Path(args.json_out) if args.json_out else OUT_DIR / f"cognitive_lifecycle_status_{ts}.json"
    md_path = Path(args.md_out) if args.md_out else OUT_DIR / f"report_cognitive_lifecycle_status_{ts}.md"

    conn = await asyncpg.connect(DB_URL)
    try:
        status = await fetch_status(conn)
        events = await fetch_recent_events(conn, args.events)
    finally:
        await conn.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "recent_events": events,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=json_default), encoding="utf-8")
    write_markdown(status, events, md_path)

    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    print(f"Agents: {len(status)}")
    for row in status:
        print(f"{row['agent_name']}: {row['state']} / {row['runtime']} / {row['budget_class']} event={row['source_event_id']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=10)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
