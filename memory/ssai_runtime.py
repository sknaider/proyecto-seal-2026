#!/usr/bin/env python3
"""SSAI production SHADOW/DUAL_VERIFY runtime.

This module projects the existing identity rows into an additive SSAI registry,
records verification runs and enforces the 14-day rollout clock.  It never writes
``soul_v3.identity`` and DUAL_VERIFY never blocks the legacy boot path.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping
import uuid

import asyncpg

try:  # Package import in tests.
    from .seal_secrets import pg_dsn
    from .ssai_shadow.canonical import canonicalize
    from .ssai_shadow.manifest import generate_soul_id
except ImportError:  # Direct script execution under memory/.
    from seal_secrets import pg_dsn
    from ssai_shadow.canonical import canonicalize
    from ssai_shadow.manifest import generate_soul_id

PROJECTION_SCHEMA = "https://seal.local/schemas/ssai/identity-projection/v1"
ROLLOUT_DAYS = 14


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _commit_json(value: Any) -> str:
    return _sha256(canonicalize(_json_value(value)))


def _commit_text(value: Any) -> str:
    return _sha256(str(value or "").encode("utf-8"))


def build_identity_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a privacy-minimized deterministic projection of ``soul_v3.identity``."""

    return {
        "schema": PROJECTION_SCHEMA,
        "agent": str(row["agent"]),
        "source": {"table": "soul_v3.identity", "row_id": int(row["id"])},
        "commitments": {
            "personality": _commit_json(row["personality"]),
            "boot_context": _commit_text(row.get("boot_context")),
            "philosophy": _commit_text(row.get("philosophy")),
            "ocean_scores": _commit_json(row.get("ocean_scores")),
            "ocean_baseline": _commit_json(row.get("ocean_baseline")),
            "ocean_lock": _commit_text(row.get("ocean_lock_hash")),
        },
    }


def projection_artifact(row: Mapping[str, Any]) -> tuple[bytes, str]:
    payload = canonicalize(build_identity_projection(row))
    return payload, _sha256(payload)


async def seed_candidates(conn: asyncpg.Connection) -> dict[str, Any]:
    """Create unsigned candidates for the nine canonical identity rows."""

    created_registry = 0
    created_projection = 0
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('ssai_seed_candidates_v1'))")
        rows = await conn.fetch(
            """
            SELECT id, agent, personality, boot_context, philosophy, ocean_scores,
                   ocean_baseline, ocean_lock_hash, updated_at
            FROM soul_v3.identity
            ORDER BY agent
            """
        )
        if len(rows) != 9:
            raise RuntimeError(f"expected 9 canonical identity rows, found {len(rows)}")

        for row in rows:
            existing = await conn.fetchrow(
                "SELECT id, soul_dni FROM soul_v3.ssai_registry WHERE agent=$1 FOR UPDATE",
                row["agent"],
            )
            if existing is None:
                soul_id = uuid.UUID(generate_soul_id())
                existing = await conn.fetchrow(
                    """
                    INSERT INTO soul_v3.ssai_registry(agent, soul_id, soul_dni)
                    VALUES ($1, $2, $3)
                    RETURNING id, soul_dni
                    """,
                    row["agent"],
                    soul_id,
                    f"urn:soul:agent:{soul_id}",
                )
                created_registry += 1

            payload, digest = projection_artifact(row)
            inserted = await conn.fetchval(
                """
                INSERT INTO soul_v3.ssai_candidate_projections(
                    registry_id, agent, source_identity_updated_at,
                    projection_jcs, projection_hash
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (registry_id, projection_hash) DO NOTHING
                RETURNING id
                """,
                existing["id"],
                row["agent"],
                row["updated_at"],
                payload,
                digest,
            )
            created_projection += int(inserted is not None)

            rollout = await conn.fetchval(
                "SELECT 1 FROM soul_v3.ssai_rollout_state WHERE registry_id=$1",
                existing["id"],
            )
            if rollout is None:
                await conn.execute(
                    """
                    INSERT INTO soul_v3.ssai_rollout_state(registry_id, agent, mode)
                    VALUES ($1, $2, 'SHADOW')
                    """,
                    existing["id"],
                    row["agent"],
                )
                await conn.execute(
                    """
                    INSERT INTO soul_v3.ssai_rollout_events(
                        registry_id, agent, mode_from, mode_to, authorized_by, reason, evidence
                    ) VALUES ($1, $2, NULL, 'SHADOW', 'ADA',
                              'Initial additive SSAI projection',
                              '{"source":"SSAI M1 to production rollout"}'::jsonb)
                    """,
                    existing["id"],
                    row["agent"],
                )

    return {
        "identity_rows": 9,
        "registry_created": created_registry,
        "projections_created": created_projection,
        "source_identity_writes": 0,
    }


async def promote_dual_verify(
    conn: asyncpg.Connection,
    agent: str,
    *,
    authorized_by: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Start the irreversible observation clock; this does not enable ENFORCE."""

    async with conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT r.id, r.lifecycle_state, s.mode, s.dual_verify_since,
                   s.enforce_eligible_after
            FROM soul_v3.ssai_registry r
            JOIN soul_v3.ssai_rollout_state s ON s.registry_id=r.id
            WHERE r.agent=$1
            FOR UPDATE OF s
            """,
            agent,
        )
        if row is None:
            raise RuntimeError(f"SSAI candidate missing for {agent}")
        if row["mode"] == "ENFORCE":
            raise RuntimeError("cannot restart DUAL_VERIFY while ENFORCE is active")
        if row["mode"] == "SHADOW":
            changed = await conn.fetchrow(
                """
                UPDATE soul_v3.ssai_rollout_state
                SET mode='DUAL_VERIFY', dual_verify_since=now(),
                    enforce_eligible_after=now() + make_interval(days => $2)
                WHERE registry_id=$1
                RETURNING dual_verify_since, enforce_eligible_after
                """,
                row["id"],
                ROLLOUT_DAYS,
            )
            await conn.execute(
                """
                INSERT INTO soul_v3.ssai_rollout_events(
                    registry_id, agent, mode_from, mode_to, authorized_by, reason, evidence
                ) VALUES ($1, $2, 'SHADOW', 'DUAL_VERIFY', $3,
                          'Start non-blocking production canary', $4::jsonb)
                """,
                row["id"],
                agent,
                authorized_by,
                json.dumps(dict(evidence), ensure_ascii=False),
            )
            dual_since = changed["dual_verify_since"]
            eligible = changed["enforce_eligible_after"]
        else:
            dual_since = row["dual_verify_since"]
            eligible = row["enforce_eligible_after"]

    return {
        "agent": agent,
        "mode": "DUAL_VERIFY",
        "dual_verify_since": dual_since.isoformat(),
        "enforce_eligible_after": eligible.isoformat(),
        "enforce_active": False,
    }


async def _latest_biv(conn: asyncpg.Connection, agent: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        WITH latest AS (
            SELECT session_id, max(created_at) AS last_at
            FROM soul_v3.boot_identity_checks
            WHERE agent=$1
            GROUP BY session_id
            ORDER BY last_at DESC
            LIMIT 1
        )
        SELECT l.session_id, l.last_at, count(b.*)::int AS block_count,
               bool_and(b.pass) AS pass
        FROM latest l
        JOIN soul_v3.boot_identity_checks b
          ON b.agent=$1 AND b.session_id IS NOT DISTINCT FROM l.session_id
        GROUP BY l.session_id, l.last_at
        """,
        agent,
    )
    if row is None:
        return {"session_id": None, "block_count": 0, "pass": None, "last_at": None}
    return dict(row)


async def verify_agent(conn: asyncpg.Connection, agent: str) -> dict[str, Any]:
    """Compare the live identity to the sealed candidate and append an audit run."""

    started = time.monotonic()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.agent', $1, true)", agent)
        candidate = await conn.fetchrow(
            """
            SELECT r.id AS registry_id, r.soul_dni, r.assurance, s.mode,
                   s.dual_verify_since, s.enforce_eligible_after,
                   p.projection_hash, p.source_identity_updated_at
            FROM soul_v3.ssai_registry r
            JOIN soul_v3.ssai_rollout_state s ON s.registry_id=r.id
            LEFT JOIN LATERAL (
                SELECT projection_hash, source_identity_updated_at
                FROM soul_v3.ssai_candidate_projections
                WHERE registry_id=r.id
                ORDER BY created_at DESC, id DESC LIMIT 1
            ) p ON true
            WHERE r.agent=$1
            """,
            agent,
        )
        identity = await conn.fetchrow(
            """
            SELECT id, agent, personality, boot_context, philosophy, ocean_scores,
                   ocean_baseline, ocean_lock_hash, updated_at
            FROM soul_v3.identity WHERE agent=$1
            """,
            agent,
        )
        biv = await _latest_biv(conn, agent)

        expected = candidate["projection_hash"] if candidate else None
        observed = projection_artifact(identity)[1] if identity else None
        mode = candidate["mode"] if candidate else "SHADOW"
        if candidate is None or expected is None or identity is None:
            status = "NO_CANDIDATE"
        elif biv["pass"] is not True or biv["block_count"] < 3:
            status = "NO_BIV"
        elif expected != observed:
            status = "DIVERGENCE"
        else:
            status = "PASS"

        latency_ms = max(0, round((time.monotonic() - started) * 1000))
        details = {
            "biv_session_id": biv["session_id"],
            "biv_block_count": biv["block_count"],
            "biv_last_at": biv["last_at"].isoformat() if biv["last_at"] else None,
            "candidate_identity_updated_at": (
                candidate["source_identity_updated_at"].isoformat()
                if candidate and candidate["source_identity_updated_at"] else None
            ),
            "observed_identity_updated_at": (
                identity["updated_at"].isoformat() if identity else None
            ),
        }
        await conn.execute(
            """
            INSERT INTO soul_v3.ssai_verification_runs(
                registry_id, agent, mode, status, expected_projection_hash,
                observed_projection_hash, biv_pass, latency_ms, details
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
            """,
            candidate["registry_id"] if candidate else None,
            agent,
            mode,
            status,
            expected,
            observed,
            biv["pass"],
            latency_ms,
            json.dumps(details),
        )

    return {
        "agent": agent,
        "mode": mode,
        "status": status,
        "soul_dni": candidate["soul_dni"] if candidate else None,
        "assurance": candidate["assurance"] if candidate else None,
        "projection_match": expected is not None and expected == observed,
        "biv_pass": biv["pass"],
        "biv_blocks": biv["block_count"],
        "latency_ms": latency_ms,
        "enforce_active": mode == "ENFORCE",
        "enforce_eligible_after": (
            candidate["enforce_eligible_after"].isoformat()
            if candidate and candidate["enforce_eligible_after"] else None
        ),
    }


async def status(conn: asyncpg.Connection) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT r.agent, r.soul_dni, r.lifecycle_state, r.assurance, s.mode,
               s.dual_verify_since, s.enforce_eligible_after,
               v.status AS last_status, v.latency_ms, v.created_at AS last_verified_at
        FROM soul_v3.ssai_registry r
        JOIN soul_v3.ssai_rollout_state s ON s.registry_id=r.id
        LEFT JOIN LATERAL (
            SELECT status, latency_ms, created_at
            FROM soul_v3.ssai_verification_runs
            WHERE registry_id=r.id ORDER BY id DESC LIMIT 1
        ) v ON true
        ORDER BY r.agent
        """
    )
    return {
        "agents": [
            {
                key: (value.isoformat() if isinstance(value, datetime) else value)
                for key, value in dict(row).items()
            }
            for row in rows
        ],
        "count": len(rows),
    }


def _runtime_dsn(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"empty runtime credential file: {path}")
    return value


async def _run(args: argparse.Namespace) -> int:
    if args.command in {"seed", "promote-dual", "status"}:
        dsn = pg_dsn(required=True)
    else:
        dsn = _runtime_dsn(args.dsn_file)
    conn = await asyncpg.connect(dsn, server_settings={"search_path": "soul_v3"})
    try:
        if args.command == "seed":
            result = await seed_candidates(conn)
        elif args.command == "promote-dual":
            result = await promote_dual_verify(
                conn,
                args.agent,
                authorized_by="William",
                evidence={
                    "source_channel": "dm:ada:william",
                    "source_chat_id": 111994,
                    "authorization": "continua y llevalo a produccion",
                },
            )
        elif args.command == "verify":
            result = await verify_agent(conn, args.agent)
        else:
            result = await status(conn)
    finally:
        await conn.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.command == "verify" and result["mode"] == "ENFORCE" and result["status"] != "PASS":
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".seal_mcp_runtime_cred",
        help="Least-privilege runtime DSN file (never printed)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="Project the nine canonical identities into SHADOW")
    verify_parser = sub.add_parser("verify", help="Run and record one identity verification")
    verify_parser.add_argument("--agent", required=True)
    promote = sub.add_parser("promote-dual", help="Start the 14-day non-blocking canary")
    promote.add_argument("--agent", required=True)
    sub.add_parser("status", help="Show rollout state and latest run")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
