#!/usr/bin/env python3
"""Read-only/adversarial live gate for native SUIE human capture."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import urllib.error
import urllib.request

import asyncpg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from soul_ingestion.service import _database_dsn


TENANT = "00000000-0000-0000-0000-000000000001"


def http_gate() -> dict[str, object]:
    with urllib.request.urlopen("http://127.0.0.1:8791/health", timeout=10) as response:
        health = json.load(response)
    try:
        urllib.request.urlopen("http://127.0.0.1:8791/v1/review/candidates", timeout=10)
        anonymous = "UNEXPECTED_ALLOW"
    except urllib.error.HTTPError as exc:
        anonymous = f"DENIED_{exc.code}"
    return {
        "health": health.get("status"),
        "processor_login": health.get("database_login"),
        "processor_can_review": health.get("processor_can_review"),
        "review_login": health.get("review_database_login"),
        "review_memory_write": health.get("review_memory_write"),
        "anonymous_review": anonymous,
    }


async def database_gate() -> dict[str, object]:
    ingest_token = (ROOT / "var/soul_ingestion/service.token").read_text(encoding="utf-8").strip()
    review_token = (ROOT / "var/soul_ingestion/review.token").read_text(encoding="utf-8").strip()
    promoter_token = (ROOT / "var/soul_ingestion/promoter.token").read_text(encoding="utf-8").strip()
    results: dict[str, object] = {}

    processor = await asyncpg.connect(_database_dsn(ingest_token, login="svc_soul_ingestion"))
    try:
        try:
            await processor.execute("SET ROLE pr_ingestion_reviewer")
            results["processor_set_reviewer"] = "UNEXPECTED_ALLOW"
        except asyncpg.InsufficientPrivilegeError:
            results["processor_set_reviewer"] = "DENIED_42501"
    finally:
        await processor.close()

    reviewer = await asyncpg.connect(
        _database_dsn(review_token, login="svc_soul_ingestion_review")
    )
    try:
        async with reviewer.transaction():
            await reviewer.execute("SET LOCAL ROLE pr_ingestion_reviewer")
            await reviewer.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
            results["visible_candidates"] = await reviewer.fetchval(
                "SELECT count(*) FROM soul_v3.ingestion_memory_candidates"
            )
        try:
            async with reviewer.transaction():
                await reviewer.execute("SET LOCAL ROLE pr_ingestion_reviewer")
                await reviewer.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
                await reviewer.execute(
                    """
                    INSERT INTO soul_v3.memories(agent,category,content,importance,source,scope)
                    VALUES ('ADA','fact','DENY_CANARY',1,'test','william')
                    """
                )
            results["reviewer_insert_memory"] = "UNEXPECTED_ALLOW"
        except asyncpg.InsufficientPrivilegeError:
            results["reviewer_insert_memory"] = "DENIED_42501"
        try:
            async with reviewer.transaction():
                await reviewer.execute("SET LOCAL ROLE pr_ingestion_reviewer")
                await reviewer.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
                await reviewer.execute(
                    """
                    INSERT INTO soul_v3.ingestion_state_events(
                      tenant_id,event_type,actor,reason,metadata
                    ) VALUES ($1,'approved','William','DENY_CANARY','{}'::jsonb)
                    """,
                    TENANT,
                )
            results["reviewer_insert_event"] = "UNEXPECTED_ALLOW"
        except asyncpg.InsufficientPrivilegeError:
            results["reviewer_insert_event"] = "DENIED_42501"
        try:
            await reviewer.execute("SET ROLE pr_ingestion_processor")
            results["reviewer_set_processor"] = "UNEXPECTED_ALLOW"
        except asyncpg.InsufficientPrivilegeError:
            results["reviewer_set_processor"] = "DENIED_42501"
    finally:
        await reviewer.close()

    promoter = await asyncpg.connect(
        _database_dsn(promoter_token, login="svc_soul_ingestion_promoter")
    )
    try:
        async with promoter.transaction():
            await promoter.execute("SET LOCAL ROLE pr_ingestion_promoter")
            await promoter.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
            results["visible_outbox"] = await promoter.fetchval(
                "SELECT count(*) FROM soul_v3.ingestion_promotion_outbox"
            )
        try:
            async with promoter.transaction():
                await promoter.execute("SET LOCAL ROLE pr_ingestion_promoter")
                await promoter.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
                await promoter.execute(
                    "UPDATE soul_v3.ingestion_promotion_outbox SET action='revoke' WHERE false"
                )
            results["promoter_direct_update"] = "UNEXPECTED_ALLOW"
        except asyncpg.InsufficientPrivilegeError:
            results["promoter_direct_update"] = "DENIED_42501"
        try:
            async with promoter.transaction():
                await promoter.execute("SET LOCAL ROLE pr_ingestion_promoter")
                await promoter.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
                await promoter.execute(
                    """
                    INSERT INTO soul_v3.memories(agent,category,content,importance,source,scope)
                    VALUES ('ADA','fact','DENY_CANARY',1,'test','william')
                    """
                )
            results["promoter_insert_memory"] = "UNEXPECTED_ALLOW"
        except asyncpg.InsufficientPrivilegeError:
            results["promoter_insert_memory"] = "DENIED_42501"
        try:
            await promoter.execute("SET ROLE pr_ingestion_reviewer")
            results["promoter_set_reviewer"] = "UNEXPECTED_ALLOW"
        except asyncpg.InsufficientPrivilegeError:
            results["promoter_set_reviewer"] = "DENIED_42501"
    finally:
        await promoter.close()
    return results


async def main() -> int:
    payload = {"http": http_gate(), "database": await database_gate()}
    expected = {
        "health": "healthy",
        "processor_login": "svc_soul_ingestion",
        "processor_can_review": False,
        "review_login": "svc_soul_ingestion_review",
        "review_memory_write": False,
        "anonymous_review": "DENIED_401",
    }
    denied = {
        "processor_set_reviewer": "DENIED_42501",
        "reviewer_insert_memory": "DENIED_42501",
        "reviewer_insert_event": "DENIED_42501",
        "reviewer_set_processor": "DENIED_42501",
        "promoter_direct_update": "DENIED_42501",
        "promoter_insert_memory": "DENIED_42501",
        "promoter_set_reviewer": "DENIED_42501",
    }
    ok = all(payload["http"].get(key) == value for key, value in expected.items())
    ok = ok and all(payload["database"].get(key) == value for key, value in denied.items())
    ok = ok and int(payload["database"].get("visible_candidates", 0)) > 0
    ok = ok and int(payload["database"].get("visible_outbox", 0)) > 0
    payload["ok"] = ok
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
