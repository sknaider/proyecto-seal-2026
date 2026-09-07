#!/usr/bin/env python3
"""Rollback SOUL consolidation batches by rollback_token.

Default mode is dry-run. Live rollback restores the mutable fields changed by
consolidation_apply.py and keeps archive rows as audit trail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn
from consolidation_integrity import record_flight_event


DB_URL = os.environ.get("SEAL_PG_DSN", pg_dsn(required=True))


@dataclass(frozen=True)
class RollbackPlan:
    rollback_token: str
    agent: str | None
    archive_rows: int
    active_targets: int
    dry_run: bool
    apply: bool


def backup_from_archive_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    backup = metadata.get("consolidation_backup")
    return backup if isinstance(backup, dict) else None


def json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


async def load_archive_rows(conn: asyncpg.Connection, rollback_token: str, agent: str | None = None) -> list[asyncpg.Record]:
    agent_clause = "AND agent=$2" if agent else ""
    args: list[Any] = [rollback_token]
    if agent:
        args.append(agent.upper())
    return await conn.fetch(
        f"""
        SELECT *
        FROM soul_v3.memories_archive
        WHERE metadata->'consolidation_backup'->>'rollback_token' = $1
          {agent_clause}
        ORDER BY id ASC
        """,
        *args,
    )


async def build_plan(rollback_token: str, agent: str | None = None, dry_run: bool = True) -> RollbackPlan:
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await load_archive_rows(conn, rollback_token, agent)
        ids = []
        for row in rows:
            backup = backup_from_archive_metadata(json_dict(row["metadata"]))
            if backup and backup.get("original_memory_id"):
                ids.append(int(backup["original_memory_id"]))
        active_targets = 0
        if ids:
            active_targets = int(
                await conn.fetchval(
                    "SELECT count(*) FROM soul_v3.memories WHERE id = ANY($1::bigint[])",
                    ids,
                )
                or 0
            )
        return RollbackPlan(
            rollback_token=rollback_token,
            agent=agent.upper() if agent else None,
            archive_rows=len(rows),
            active_targets=active_targets,
            dry_run=dry_run,
            apply=not dry_run,
        )
    finally:
        await conn.close()


async def rollback_batch(rollback_token: str, agent: str | None = None, apply: bool = False) -> RollbackPlan:
    plan = await build_plan(rollback_token, agent, dry_run=not apply)
    if not apply:
        return plan
    if plan.archive_rows == 0:
        raise RuntimeError("rollback_token_not_found")
    if not agent:
        raise RuntimeError("agent_required_for_live_rollback")

    conn = await asyncpg.connect(DB_URL)
    try:
        async with conn.transaction():
            rows = await load_archive_rows(conn, rollback_token, agent)
            for row in rows:
                backup = backup_from_archive_metadata(json_dict(row["metadata"]))
                if not backup:
                    raise RuntimeError(f"missing_backup_metadata archive_id={row['id']}")
                original_id = int(backup["original_memory_id"])
                target = await conn.fetchrow(
                    """SELECT * FROM soul_v3.memories
                       WHERE id=$1 AND agent=$2
                       FOR UPDATE""",
                    original_id,
                    agent.upper(),
                )
                if not target:
                    raise RuntimeError(f"rollback_target_missing memory_id={original_id}")
                target_meta = json_dict(target["metadata"])
                if target_meta.get("consolidation_v1_rollback_token") != rollback_token:
                    raise RuntimeError(
                        f"rollback_out_of_order_or_replayed memory_id={original_id}"
                    )
                restored_metadata = json_dict(row["metadata"])
                restored_metadata.pop("consolidation_backup", None)
                result = await conn.execute(
                    """
                    UPDATE soul_v3.memories
                    SET importance=$1,
                        invalid_at=$2,
                        superseded_by=$3,
                        updated_at=$4,
                        metadata=$5::jsonb
                    WHERE id=$6 AND agent=$7
                      AND metadata->>'consolidation_v1_rollback_token'=$8
                    """,
                    row["importance"],
                    row["invalid_at"],
                    row["superseded_by"],
                    row["updated_at"],
                    json.dumps(restored_metadata, ensure_ascii=False),
                    original_id,
                    agent.upper(),
                    rollback_token,
                )
                if int(result.split()[-1]) != 1:
                    raise RuntimeError(f"rollback_cas_failed memory_id={original_id}")
                after = await conn.fetchrow(
                    "SELECT * FROM soul_v3.memories WHERE id=$1 AND agent=$2",
                    original_id,
                    agent.upper(),
                )
                await record_flight_event(
                    conn,
                    agent=agent,
                    phase="rollback_memory",
                    action=str(backup.get("action") or "unknown"),
                    rollback_token=rollback_token,
                    batch_id=str(backup.get("batch_id") or "unknown"),
                    manifest_sha256=str(backup.get("manifest_sha256") or "unknown"),
                    memory_id=original_id,
                    archive_id=int(row["id"]),
                    before=dict(target),
                    after=dict(after),
                )
        return RollbackPlan(**{**asdict(plan), "dry_run": False, "apply": True})
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run or apply a consolidation rollback by token.")
    parser.add_argument("--rollback-token", required=True)
    parser.add_argument("--agent")
    parser.add_argument("--apply", action="store_true")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = await rollback_batch(args.rollback_token, args.agent, args.apply)
        print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
