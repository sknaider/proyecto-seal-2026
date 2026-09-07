#!/usr/bin/env python3
"""Canonical event recorders for conditional SOUL tables.

These helpers are intentionally small and idempotent-friendly. They make
conditional tables usable without fabricating rows: callers should invoke them
only when a real denial/block or skill execution happens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(pg_dsn(required=True))


async def record_denial(
    conn: asyncpg.Connection,
    *,
    agent: str,
    denied_action: str,
    denial_reason: str = "",
    source: str = "self",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not agent.strip():
        raise ValueError("agent is required")
    if not denied_action.strip():
        raise ValueError("denied_action is required")
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.denial_tracker
            (agent, denied_action, denial_reason, source, context, resolved)
        VALUES ($1, $2, $3, $4, $5::jsonb, false)
        RETURNING id, agent, denied_action, denial_reason, source, resolved, created_at
        """,
        agent,
        denied_action,
        denial_reason or None,
        source,
        json.dumps(context or {}, ensure_ascii=False, sort_keys=True),
    )
    return dict(row)


async def record_skill_use(
    conn: asyncpg.Connection,
    *,
    agent: str,
    skill_name: str | None = None,
    skill_id: int | None = None,
    params: dict[str, Any] | None = None,
    success: bool,
    error: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    if not agent.strip():
        raise ValueError("agent is required")
    if skill_id is None:
        if not skill_name:
            raise ValueError("skill_name or skill_id is required")
        skill_id = await conn.fetchval(
            """
            SELECT id
            FROM soul_v3.skills
            WHERE invalid_at IS NULL
              AND (agent = $1 OR agent = 'TEAM')
              AND name = $2
            ORDER BY CASE WHEN agent = $1 THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            agent,
            skill_name,
        )
    if skill_id is None:
        raise KeyError(f"skill not found for agent={agent} skill_name={skill_name}")
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.skill_use_log
            (skill_id, agent, params, success, error, duration_ms)
        VALUES ($1, $2, $3::jsonb, $4, $5, $6)
        RETURNING id, skill_id, agent, success, error, duration_ms, created_at
        """,
        int(skill_id),
        agent,
        json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
        bool(success),
        error,
        duration_ms,
    )
    await conn.execute(
        """
        UPDATE soul_v3.skills
        SET success_count = COALESCE(success_count, 0) + CASE WHEN $2 THEN 1 ELSE 0 END,
            failure_count = COALESCE(failure_count, 0) + CASE WHEN $2 THEN 0 ELSE 1 END
        WHERE id=$1
        """,
        int(skill_id),
        bool(success),
    )
    return dict(row)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record conditional SOUL events")
    sub = parser.add_subparsers(dest="command", required=True)
    deny = sub.add_parser("denial")
    deny.add_argument("--agent", required=True)
    deny.add_argument("--action", required=True)
    deny.add_argument("--reason", default="")
    deny.add_argument("--source", default="self")
    deny.add_argument("--context", default="{}")
    skill = sub.add_parser("skill-use")
    skill.add_argument("--agent", required=True)
    skill.add_argument("--skill-name")
    skill.add_argument("--skill-id", type=int)
    skill.add_argument("--params", default="{}")
    skill.add_argument("--success", action="store_true")
    skill.add_argument("--error")
    skill.add_argument("--duration-ms", type=int)
    return parser


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    conn = await connect_db()
    try:
        if args.command == "denial":
            try:
                context = json.loads(args.context)
            except json.JSONDecodeError:
                context = {"raw": args.context}
            return await record_denial(
                conn,
                agent=args.agent,
                denied_action=args.action,
                denial_reason=args.reason,
                source=args.source,
                context=context,
            )
        if args.command == "skill-use":
            try:
                params = json.loads(args.params)
            except json.JSONDecodeError:
                params = {"raw": args.params}
            return await record_skill_use(
                conn,
                agent=args.agent,
                skill_name=args.skill_name,
                skill_id=args.skill_id,
                params=params,
                success=args.success,
                error=args.error,
                duration_ms=args.duration_ms,
            )
        raise ValueError(args.command)
    finally:
        await conn.close()


def main() -> None:
    args = build_parser().parse_args()
    print(json.dumps(asyncio.run(main_async(args)), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
