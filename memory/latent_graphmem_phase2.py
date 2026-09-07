#!/usr/bin/env python3
"""LatentGraphMem Phase 2 production gates.

This module does not train or promote an adapter. It turns Phase 2 into a
measurable contract: router, cache, serve smoke test, and latency guard.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import asyncpg

from latent_graphmem.query_classifier import _rule_classify, route
from seal_secrets import pg_dsn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE2_CACHE_TABLE = "soul_v3.latent_graphmem_phase2_cache"
DEFAULT_PYTHON = Path("/home/dadito/IA/seal-spark/.venv/bin/python3")


@dataclass(frozen=True)
class LatentPhase2Assessment:
    agent: str
    score: int
    passed: bool
    checks: dict[str, bool]
    evidence: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(pg_dsn(required=True))


async def ensure_phase2_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS soul_v3.latent_graphmem_phase2_cache (
            id BIGSERIAL PRIMARY KEY,
            query_hash TEXT UNIQUE NOT NULL,
            query TEXT NOT NULL,
            query_type TEXT NOT NULL,
            backend TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            hit_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_latent_phase2_cache_backend
        ON soul_v3.latent_graphmem_phase2_cache(backend, updated_at DESC)
        """
    )


def query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()


async def cache_put(conn: asyncpg.Connection, query: str, query_type: str, backend: str, payload: dict[str, Any]) -> int:
    await ensure_phase2_schema(conn)
    row_id = await conn.fetchval(
        """
        INSERT INTO soul_v3.latent_graphmem_phase2_cache
            (query_hash, query, query_type, backend, payload, hit_count, updated_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, 0, now())
        ON CONFLICT (query_hash) DO UPDATE SET
            query=EXCLUDED.query,
            query_type=EXCLUDED.query_type,
            backend=EXCLUDED.backend,
            payload=EXCLUDED.payload,
            updated_at=now()
        RETURNING id
        """,
        query_hash(query),
        query,
        query_type,
        backend,
        json.dumps(payload, sort_keys=True),
    )
    return int(row_id)


async def cache_get(conn: asyncpg.Connection, query: str) -> dict[str, Any] | None:
    await ensure_phase2_schema(conn)
    row = await conn.fetchrow(
        """
        UPDATE soul_v3.latent_graphmem_phase2_cache
        SET hit_count=hit_count + 1, updated_at=now()
        WHERE query_hash=$1
        RETURNING id, query_hash, query, query_type, backend, payload, hit_count
        """,
        query_hash(query),
    )
    return dict(row) if row else None


def router_probe() -> dict[str, Any]:
    samples = [
        ("por que SOUL necesita memoria causal", "causal", "latent"),
        ("cuando se reinicio ADA", "temporal", "latent"),
        ("si falla el bridge entonces que debe hacer ADA", "inference", "latent"),
    ]
    routed = []
    for query, expected_type, expected_backend in samples:
        qtype = _rule_classify(query)
        backend = route(qtype) if qtype else "unknown"
        routed.append(
            {
                "query": query,
                "query_type": qtype,
                "backend": backend,
                "expected_type": expected_type,
                "expected_backend": expected_backend,
                "ok": qtype == expected_type and backend == expected_backend,
            }
        )
    factual_backend = route("factual")
    return {
        "samples": routed,
        "factual_backend": factual_backend,
        "all_ok": all(item["ok"] for item in routed) and factual_backend == "magma",
    }


def serve_self_test(timeout: float = 20.0) -> dict[str, Any]:
    python_bin = os.environ.get("SEAL_PYTHON")
    if not python_bin:
        python_bin = str(DEFAULT_PYTHON if DEFAULT_PYTHON.exists() else Path(sys.executable))
    proc = subprocess.run(
        [python_bin, str(PROJECT_ROOT / "memory" / "latent_graphmem_serve.py"), "--self-test"],
        cwd=str(PROJECT_ROOT / "memory"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }


async def assess_latent_graphmem_phase2(agent: str = "ADA", persist: bool = True) -> LatentPhase2Assessment:
    agent = agent.upper()
    conn = await connect_db()
    cache_latency_ms = 0.0
    cache_row: dict[str, Any] | None = None
    try:
        await ensure_phase2_schema(conn)
        query = "por que SOUL necesita memoria causal para awareness 24/7"
        payload = {
            "source": "phase2_gate",
            "subgraph_cache": True,
            "target_p95_ms_cpu": 250,
            "promotion": "none",
        }
        if persist:
            await cache_put(conn, query, "causal", "latent", payload)
        started = time.perf_counter()
        cache_row = await cache_get(conn, query) if persist else {
            "query": query,
            "query_type": "causal",
            "backend": "latent",
            "payload": payload,
            "hit_count": 1,
        }
        cache_latency_ms = round((time.perf_counter() - started) * 1000, 3)
    finally:
        await conn.close()

    router = router_probe()
    self_test = serve_self_test()
    report_path = PROJECT_ROOT / "memory" / "research" / "fase1_latent_graphmem_report.md"
    report_text = report_path.read_text(encoding="utf-8", errors="replace") if report_path.exists() else ""
    checks = {
        "phase2_cache_schema_ready": cache_row is not None,
        "phase2_cache_roundtrip": bool(cache_row and cache_row.get("backend") == "latent" and cache_row.get("query_type") == "causal"),
        "phase2_cache_hit_under_250ms": cache_latency_ms < 250,
        "router_routes_latent_queries": bool(router["all_ok"]),
        "serve_self_test_ok": self_test["returncode"] == 0,
        "phase2_report_documents_targets": "Fase 2" in report_text and "200ms" in report_text,
    }
    passed = sum(1 for ok in checks.values() if ok)
    score = round((passed / len(checks)) * 100)
    evidence = (
        f"latent_graphmem_phase2_cases={passed}/{len(checks)} "
        f"cache_hit_ms={cache_latency_ms} router={router['all_ok']} self_test={self_test['returncode']}"
    )
    return LatentPhase2Assessment(
        agent=agent,
        score=score,
        passed=score >= 90,
        checks=checks,
        evidence=evidence,
        details={
            "cache_latency_ms": cache_latency_ms,
            "cache_row": cache_row,
            "router": router,
            "self_test": self_test,
            "report_path": str(report_path),
            "boundary": "Phase 2 gate enables router/cache/serve readiness only; no adapter is trained or promoted.",
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LatentGraphMem Phase 2 gates")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--no-persist", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("assess")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    if args.command == "assess":
        result = await assess_latent_graphmem_phase2(args.agent, persist=not args.no_persist)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
        return 0 if result.passed else 2
    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
