#!/usr/bin/env python3
"""Cross-tenant smoke fuzz for SOUL Memory SDK Phase 2.

Default mode runs entirely inside a transaction and rolls back all tenant,
memory, audit, and agent rows at the end.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import secrets
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn
from soul_memory_sdk_runtime import (
    TenantOverrideError,
    api_key_record,
    create_memory,
    derive_tenant_from_api_key,
    get_memory,
    list_memories,
    recall_memories,
    reject_tenant_override,
    set_tenant_context,
)


DB_URL = os.environ.get("SEAL_PG_DSN", pg_dsn(required=True))


@dataclass(frozen=True)
class FuzzResult:
    iterations: int
    cross_read_blocked: int
    cross_recall_blocked: int
    override_rejected: int
    residues_after_rollback: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "iterations": self.iterations,
            "cross_read_blocked": self.cross_read_blocked,
            "cross_recall_blocked": self.cross_recall_blocked,
            "override_rejected": self.override_rejected,
            "residues_after_rollback": self.residues_after_rollback,
            "ok": all(v == 0 for v in self.residues_after_rollback.values()),
        }


def _override_case(other_tenant: str) -> tuple[str, dict[str, str]]:
    cases = [
        ("headers", {"X-Tenant-ID": other_tenant}),
        ("headers", {"x-tenant": other_tenant}),
        ("query", {"tenant_id": other_tenant}),
        ("query", {"org_id": other_tenant}),
        ("payload", {"tenant": other_tenant}),
        ("payload", {"tenant_id": other_tenant}),
    ]
    return random.choice(cases)


async def run_db_fuzz(iterations: int, *, seed: int = 260526) -> FuzzResult:
    random.seed(seed)
    conn = await asyncpg.connect(DB_URL)
    tx = conn.transaction()
    await tx.start()
    agent = "fuzz_agent"
    tenant_ids: list[str] = []
    cross_read_blocked = 0
    cross_recall_blocked = 0
    override_rejected = 0
    try:
        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())
        tenant_ids = [tenant_a, tenant_b]
        key_a = "sk-soul-fuzz-a-" + secrets.token_urlsafe(8)
        key_b = "sk-soul-fuzz-b-" + secrets.token_urlsafe(8)
        await conn.execute(
            "INSERT INTO soul_v3.tenants (id,name,is_internal,api_keys,quotas) VALUES ($1::uuid,$2,false,$3::jsonb,'{}'::jsonb)",
            tenant_a,
            "FUZZ A",
            json.dumps([api_key_record(key_a)]),
        )
        await conn.execute(
            "INSERT INTO soul_v3.tenants (id,name,is_internal,api_keys,quotas) VALUES ($1::uuid,$2,false,$3::jsonb,'{}'::jsonb)",
            tenant_b,
            "FUZZ B",
            json.dumps([api_key_record(key_b)]),
        )
        ta = await derive_tenant_from_api_key(conn, key_a)
        tb = await derive_tenant_from_api_key(conn, key_b)

        await set_tenant_context(conn, ta)
        ma = await create_memory(
            conn,
            ta,
            {
                "agent_id": agent,
                "content": "tenant A fuzz sentinel shared_phrase",
                "category": "fact",
                "importance": 7,
            },
        )
        await set_tenant_context(conn, tb)
        mb = await create_memory(
            conn,
            tb,
            {
                "agent_id": agent,
                "content": "tenant B fuzz sentinel shared_phrase",
                "category": "fact",
                "importance": 7,
            },
        )

        tenants = [(ta, ma, mb), (tb, mb, ma)]
        for _ in range(iterations):
            tenant, own, other = random.choice(tenants)
            await set_tenant_context(conn, tenant)

            if await get_memory(conn, int(other["id"])) is None:
                cross_read_blocked += 1
            recall = await recall_memories(
                conn,
                tenant,
                query_text="shared_phrase",
                agent_id=agent,
                limit=10,
            )
            ids = {int(m["id"]) for m in recall["memories"]}
            if int(other["id"]) not in ids and int(own["id"]) in ids:
                cross_recall_blocked += 1

            target_kind, values = _override_case(str(other["id"]))
            try:
                reject_tenant_override(**{target_kind: values})
            except TenantOverrideError:
                override_rejected += 1

        if cross_read_blocked != iterations:
            raise AssertionError(f"cross_read_blocked={cross_read_blocked}/{iterations}")
        if cross_recall_blocked != iterations:
            raise AssertionError(f"cross_recall_blocked={cross_recall_blocked}/{iterations}")
        if override_rejected != iterations:
            raise AssertionError(f"override_rejected={override_rejected}/{iterations}")
    finally:
        await tx.rollback()
        residues = {
            "tenants": await conn.fetchval(
                "SELECT count(*) FROM soul_v3.tenants WHERE id = ANY($1::uuid[])",
                tenant_ids,
            )
            if tenant_ids
            else 0,
            "memories": await conn.fetchval(
                "SELECT count(*) FROM soul_v3.memories WHERE tenant_id = ANY($1::uuid[])",
                tenant_ids,
            )
            if tenant_ids
            else 0,
            "audit": await conn.fetchval(
                "SELECT count(*) FROM soul_v3.memory_retrieval_log WHERE tenant_id = ANY($1::uuid[])",
                tenant_ids,
            )
            if tenant_ids
            else 0,
            "agents": await conn.fetchval(
                "SELECT count(*) FROM soul_v3.agents WHERE name=$1",
                agent,
            ),
        }
        await conn.close()

    return FuzzResult(
        iterations=iterations,
        cross_read_blocked=cross_read_blocked,
        cross_recall_blocked=cross_recall_blocked,
        override_rejected=override_rejected,
        residues_after_rollback={k: int(v or 0) for k, v in residues.items()},
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--seed", type=int, default=260526)
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    result = await run_db_fuzz(args.iterations, seed=args.seed)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.to_dict()["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
