#!/usr/bin/env python3
"""Focused tests for the standalone SOUL coordination core."""

from __future__ import annotations

import asyncio
from pathlib import Path

from soul_coordination import (
    SoulCoordinationStore,
    build_assignments,
    choose_lead,
    classify_mode,
    make_voice_grant,
    verify_voice_grant,
    voice_idempotency_key,
)


def test_classifier() -> None:
    assert classify_mode("hola") == "social"
    assert classify_mode("¿qué opinan de esta solución?") == "discussion"
    assert classify_mode("arreglen el daemon y ejecuten los tests") == "execution"
    assert classify_mode("todos respondan con una frase") == "roundtable"
    assert classify_mode("revisa esto", to="ADA") == "direct"
    # All-call has precedence: explicit multi-agent intent is never guessed from a client flag.
    assert classify_mode("equipo arreglen producción") == "execution"


def test_lead_and_assignments() -> None:
    caps = {
        "agents": {
            "ADA": {"online": True, "open_tasks": 1, "capabilities": [
                {"keywords": ["implementa", "endpoint"], "proficiency": 0.95}
            ]},
            "JARVIS": {"online": True, "open_tasks": 0, "capabilities": [
                {"keywords": ["arquitectura"], "proficiency": 1.0}
            ]},
            "NEXUS": {"online": False, "open_tasks": 0, "capabilities": [
                {"keywords": ["seguridad"], "proficiency": 1.0}
            ]},
        }
    }
    assert choose_lead("implementa el endpoint", capabilities=caps, source_id="m1") == "ADA"
    assert choose_lead("seguridad del endpoint", capabilities=caps, source_id="m2") != "NEXUS"
    assignments = build_assignments(
        "execution", lead="ADA", candidates=("ADA", "JARVIS", "NEXUS"), max_contributors=2
    )
    assert assignments[0]["role"] == "lead" and assignments[0]["public_write"] is True
    assert all(not row["public_write"] for row in assignments[1:])
    roundtable = build_assignments("roundtable", lead="ADA", candidates=("ADA", "JARVIS"))
    assert {row["role"] for row in roundtable} == {"speaker"}
    assert all(row["public_write"] for row in roundtable)


def test_voice_grants() -> None:
    secret = "test-secret-not-production"
    token = make_voice_grant(
        "api_william_1", "ADA", "final", "execution", secret,
        ttl_seconds=30, now=1000, nonce="fixed-nonce",
    )
    used: set[str] = set()
    grant = verify_voice_grant(
        token, secret, expected_source_id="api_william_1", expected_agent="ADA",
        expected_purpose="final", now=1001, consumed_nonces=used, consume=True,
    )
    assert grant.agent == "ADA" and grant.source_id == "api_william_1"
    try:
        verify_voice_grant(token, secret, now=1002, consumed_nonces=used, consume=True)
        raise AssertionError("replay accepted")
    except ValueError as exc:
        assert "consumed" in str(exc)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    try:
        verify_voice_grant(tampered, secret, now=1001)
        raise AssertionError("tampered grant accepted")
    except ValueError:
        pass
    try:
        make_voice_grant(
            "m", "ADA", "roundtable", "execution", secret, now=1000,
        )
        raise AssertionError("execution issued roundtable grant")
    except ValueError:
        pass
    assert voice_idempotency_key("m", "ADA", "final") == voice_idempotency_key("m", "ada", "final")


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.grant_consumed = False

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "INSERT INTO soul_v3.coordination_turns" in sql:
            return {"source_message_id": args[0], "mode": args[3], "lead_agent": args[4], "version": 1}
        if "UPDATE soul_v3.coordination_voice_grants" in sql:
            if self.grant_consumed:
                return None
            self.grant_consumed = True
            return {"grant_id": args[0]}
        if "UPDATE soul_v3.coordination_turns" in sql:
            return {"source_message_id": args[0]}
        return None

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "INSERT 0 1" if "INSERT" in sql else "UPDATE 1"


def test_durable_store_contract() -> None:
    async def scenario():
        conn = _FakeConnection()
        store = SoulCoordinationStore(conn)
        assignments = build_assignments("execution", lead="ADA", candidates=("ADA", "JARVIS"))
        turn = await store.create_turn(
            source_message_id="api_william_durable", requester="William",
            request_text="implementa el endpoint", mode="execution", lead_agent="ADA",
            assignments=assignments,
        )
        assert turn["source_message_id"] == "api_william_durable"
        sql = "\n".join(call[0] for call in conn.calls)
        assert "ON CONFLICT (source_message_id)" in sql
        assert "ON CONFLICT (source_message_id, agent) DO NOTHING" in sql
        token = make_voice_grant(
            "api_william_durable", "ADA", "final", "execution", "secret",
            now=1000, nonce="durable",
        )
        grant = verify_voice_grant(token, "secret", now=1001)
        assert await store.register_grant(grant)
        assert await store.consume_grant(grant) is True
        assert await store.consume_grant(grant) is False
        assert await store.complete_turn("api_william_durable", "ADA", "final-1", expected_version=1)
        assert await store.cancel_turn("api_william_cancelled")
        lifecycle_sql = "\n".join(call[0] for call in conn.calls)
        assert "SET status='done'" in lifecycle_sql
        assert "SET status='expired'" in lifecycle_sql

    asyncio.run(scenario())


def test_migration_contract() -> None:
    migration = Path(__file__).with_name("migrations").joinpath(
        "20260711_soul_coordination.sql"
    ).read_text(encoding="utf-8")
    for table in (
        "coordination_turns", "coordination_assignments", "coordination_voice_grants"
    ):
        assert f"CREATE TABLE IF NOT EXISTS soul_v3.{table}" in migration
    assert "DROP TABLE" not in migration.upper()
    assert "DELETE FROM" not in migration.upper()
    assert "ON DELETE CASCADE" not in migration.upper()


def main() -> None:
    tests = [
        test_classifier,
        test_lead_and_assignments,
        test_voice_grants,
        test_durable_store_contract,
        test_migration_contract,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"SOUL coordination core: {len(tests)}/{len(tests)} PASS")


if __name__ == "__main__":
    main()
