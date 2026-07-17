#!/usr/bin/env python3
"""Live, non-destructive acceptance test for the SSAI registry migration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "memory"))

from seal_secrets import pg_dsn  # noqa: E402

RUNTIME_CRED = ROOT / ".seal_mcp_runtime_cred"


async def must_reject(conn: asyncpg.Connection, statement: str, label: str) -> None:
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute(statement)
    except asyncpg.PostgresError:
        await transaction.rollback()
        return
    await transaction.rollback()
    raise AssertionError(f"{label} was not rejected")


async def main() -> int:
    admin = await asyncpg.connect(pg_dsn(required=True))
    runtime = await asyncpg.connect(RUNTIME_CRED.read_text(encoding="utf-8").strip())
    try:
        counts = await admin.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM soul_v3.ssai_registry) AS registry,
              (SELECT count(*) FROM soul_v3.ssai_candidate_projections) AS projections,
              (SELECT count(*) FROM soul_v3.ssai_rollout_state) AS rollout
            """
        )
        assert counts["registry"] == 9, counts
        assert counts["projections"] == 9, counts
        assert counts["rollout"] == 9, counts

        await must_reject(
            admin,
            "UPDATE soul_v3.ssai_candidate_projections SET projection_version='tampered'",
            "append-only UPDATE",
        )
        await must_reject(
            admin,
            "DELETE FROM soul_v3.ssai_candidate_projections",
            "append-only DELETE",
        )
        await must_reject(
            admin,
            "TRUNCATE soul_v3.ssai_verification_runs",
            "append-only TRUNCATE",
        )
        await must_reject(
            admin,
            """
            UPDATE soul_v3.ssai_rollout_state
            SET dual_verify_since=dual_verify_since - interval '1 day'
            WHERE agent='ADA'
            """,
            "immutable observation timestamps",
        )
        await must_reject(
            admin,
            """
            UPDATE soul_v3.ssai_rollout_state
            SET mode='ENFORCE', enforce_eligible_after=now() - interval '1 second'
            WHERE agent='ADA'
            """,
            "premature ENFORCE",
        )

        # A caller cannot backdate a new canary to manufacture 14 elapsed days.
        transaction = admin.transaction()
        await transaction.start()
        normalized = await admin.fetchrow(
            """
            UPDATE soul_v3.ssai_rollout_state
            SET mode='DUAL_VERIFY',
                dual_verify_since=now() - interval '30 days',
                enforce_eligible_after=now() - interval '16 days'
            WHERE agent='ADA_LOCAL'
            RETURNING dual_verify_since, enforce_eligible_after
            """
        )
        now = datetime.now(timezone.utc)
        assert abs((now - normalized["dual_verify_since"]).total_seconds()) < 5
        assert (
            normalized["enforce_eligible_after"] - normalized["dual_verify_since"]
        ).days == 14
        await transaction.rollback()

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.agent', 'ADA', true)")
            visible = await runtime.fetch("SELECT agent FROM soul_v3.ssai_registry")
            assert [row["agent"] for row in visible] == ["ADA"]
            other = await runtime.fetchval(
                "SELECT count(*) FROM soul_v3.ssai_registry WHERE agent='ALICE'"
            )
            assert other == 0

        can_update = await runtime.fetchval(
            "SELECT has_table_privilege(current_user, 'soul_v3.ssai_events', 'UPDATE')"
        )
        can_delete = await runtime.fetchval(
            "SELECT has_table_privilege(current_user, 'soul_v3.ssai_events', 'DELETE')"
        )
        assert can_update is False and can_delete is False
    finally:
        await runtime.close()
        await admin.close()

    print(
        "ssai_registry_acceptance=ok registry=9 projections=9 rls=isolated "
        "update=denied delete=denied truncate=denied gate_dates=immutable "
        "backdate=normalized premature_enforce=denied"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
