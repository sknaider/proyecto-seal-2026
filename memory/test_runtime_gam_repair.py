#!/usr/bin/env python3
"""Regression checks for SOUL runtime/GAM repair."""
from __future__ import annotations
from seal_secrets import pg_dsn

import asyncio
import json
import subprocess

import asyncpg


DB_URL = pg_dsn(required=True)


async def check_gam_task_mirror() -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM soul_v3.agent_tasks) AS tasks,
              (SELECT COUNT(*) FROM soul_v3.gam_event_graph WHERE metadata->>'source'='agent_task') AS task_events,
              (
                SELECT COUNT(*)
                FROM soul_v3.gam_topics t
                LEFT JOIN (
                    SELECT topic_id, COUNT(*)::int AS real_count
                    FROM soul_v3.gam_event_graph
                    GROUP BY topic_id
                ) e ON e.topic_id=t.id
                WHERE t.event_count IS DISTINCT FROM COALESCE(e.real_count, 0)
              ) AS event_count_mismatches
            """
        )
    finally:
        await conn.close()

    assert row["tasks"] == row["task_events"], dict(row)
    assert row["event_count_mismatches"] == 0, dict(row)


def check_legacy_nerves_daemon_disabled() -> None:
    enabled = subprocess.run(
        ["systemctl", "--user", "is-enabled", "seal-nerves-daemon.service"],
        text=True,
        capture_output=True,
        check=False,
    )
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "seal-nerves-daemon.service"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert enabled.stdout.strip() == "disabled", enabled.stdout + enabled.stderr
    assert active.stdout.strip() == "inactive", active.stdout + active.stderr


def check_maintenance_dry_run() -> None:
    result = subprocess.run(
        [
            "/home/dadito/IA/seal-spark/.venv/bin/python3",
            "memory/soul_maintenance.py",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"dry_run": true' in result.stdout, result.stdout


def check_runtime_topology_green() -> None:
    result = subprocess.run(
        [
            "/home/dadito/IA/seal-spark/.venv/bin/python3",
            "memory/audit_runtime_topology.py",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["risks"] == [], data


async def check_dum_continuity_green() -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        row = await conn.fetchrow(
            """
            SELECT updated_at, state
            FROM soul_v3.working_state
            WHERE agent='DUM'
            """
        )
    finally:
        await conn.close()

    assert row is not None, "DUM working_state missing"
    state = json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]
    assert state.get("status") == "OK", state
    assert state.get("services_state", {}).get("mcp_server") == "ok", state
    assert state.get("services_state", {}).get("chat_server") == "ok", state
    assert state.get("services_state", {}).get("llama_server") == "ok", state


async def main() -> None:
    await check_gam_task_mirror()
    await check_dum_continuity_green()
    check_legacy_nerves_daemon_disabled()
    check_maintenance_dry_run()
    check_runtime_topology_green()
    print("PASS runtime/GAM repair regression")


if __name__ == "__main__":
    asyncio.run(main())
