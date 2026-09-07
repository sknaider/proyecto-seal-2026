#!/usr/bin/env python3
"""Read-only evaluator for SOUL Memory v5 recent/source recall.

Runs live recall_router queries against PostgreSQL and reports whether critical
recent decisions or registered sources appear in the returned context.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

os.environ.setdefault("SOUL_RECALL_ROUTER_ENABLED", "true")

import recall_router  # noqa: E402


CREDENTIALS_PATH = Path.home() / ".config" / "seal" / "credentials.env"


@dataclass(frozen=True)
class RecallCase:
    name: str
    suite: str
    agent: str
    query: str
    expected_any: tuple[str, ...]


DEFAULT_CASES = (
    RecallCase(
        name="nexus_windows_registered_source",
        suite="source_registry",
        agent="JARVIS",
        query="nexus clon windows tabla aparte daditogamer",
        expected_any=("nexus_claude_code_windows", "DADITOGAMER", "separate from soul_v3.memories"),
    ),
    RecallCase(
        name="alice_laptop_registered_source",
        suite="source_registry",
        agent="ALICE",
        query="clon de ALICE en dadito-laptop tabla aparte aislamiento",
        expected_any=("alice_claude_code_windows", "dadito-laptop", "separate from soul_v3.memories"),
    ),
    RecallCase(
        name="soul_memory_v5_governance",
        suite="architecture",
        agent="ADA",
        query="SOUL Memory v5 source_registry adapters evidence gate recencia",
        expected_any=("source_registry", "SOUL Memory v5", "evidence gate"),
    ),
    RecallCase(
        name="william_luz_verde",
        suite="recent_decision",
        agent="ADA",
        query="William luz verde ADA SOUL Memory v5 recall_router",
        expected_any=("Luz verde ADA", "luz verde recibida", "recall_router.py"),
    ),
    RecallCase(
        name="recency_fix_correct_file",
        suite="technical_correction",
        agent="JARVIS",
        query="recency fix archivo correcto mcp_server_v4 recall_router admision A-MAC",
        expected_any=("recall_router.py", "mcp_server_v4.py", "A-MAC"),
    ),
    RecallCase(
        name="phase_zero_evidence",
        suite="milestone_evidence",
        agent="JARVIS",
        query="Fase 0/1 SOUL Memory v5 14 tests KPI recall_success_rate",
        expected_any=("14 tests", "recall_success_rate", "Fase 0/1"),
    ),
    RecallCase(
        name="nexus_audit_no_phantom",
        suite="audit_evidence",
        agent="NEXUS",
        query="AUDIT NEXUS Fase 0/1 no phantom files tests signature Done",
        expected_any=("no phantom", "DELEGATE-52", "tests"),
    ),
    RecallCase(
        name="jarvis_natural_query_evidence",
        suite="evidence_gate",
        agent="JARVIS",
        query="JARVIS evidence gate query natural NEXUS Windows clone without obvious keywords",
        expected_any=("query NATURAL", "Windows", "source_registry"),
    ),
    RecallCase(
        name="alice_roi_metric",
        suite="team_synthesis",
        agent="ADA",
        query="ALICE ROI metrica temporal_recency SOUL Memory v5",
        expected_any=("PRIORIZACION POR ROI", "temporal_recency", "METRICA"),
    ),
    RecallCase(
        name="jarvis_spec_formal",
        suite="architecture",
        agent="JARVIS",
        query="spec formal SOUL Memory v5 memory spec_soul_memory_v5_v1 task 813",
        expected_any=("memory/spec_soul_memory_v5_v1.md", "tarea #813", "spec formal"),
    ),
    RecallCase(
        name="architecture_no_rewrite",
        suite="architecture",
        agent="ADA",
        query="SOUL Memory v5 capa de gobierno soul_v3 MCP v4 sin reescritura",
        expected_any=("soul_v3", "MCP v4", "SIN reescritura"),
    ),
    RecallCase(
        name="phase_two_plan",
        suite="next_phase",
        agent="ADA",
        query="Fase 2 evidence gate promotion pipeline hot-tier KPI",
        expected_any=("evidence gate", "promotion pipeline", "hot-tier"),
    ),
    RecallCase(
        name="whisper_alice_nexus",
        suite="team_coordination",
        agent="NEXUS",
        query="ALICE escribio whisper NEXUS socket whisper audit inbox",
        expected_any=("whisper", "/tmp/whisper_NEXUS.sock", "whisper_audit"),
    ),
)


def normalize_for_match(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def load_credentials_file() -> None:
    try:
        lines = CREDENTIALS_PATH.read_text().splitlines()
    except FileNotFoundError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def resolve_db_dsn() -> str:
    load_credentials_file()
    for key in ("SEAL_DB_DSN", "SEAL_DB_URL", "SEAL_PG_DSN"):
        value = os.environ.get(key)
        if value:
            return value
    password = os.environ.get("PG_PASSWORD") or os.environ.get("SEAL_DB_PASS")
    if not password:
        raise RuntimeError("Missing DB credentials: set SEAL_DB_DSN/SEAL_DB_URL/SEAL_PG_DSN or PG_PASSWORD")
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5433")
    user = os.environ.get("PG_USER", "seal")
    database = os.environ.get("PG_DATABASE", "seal_memory")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


async def evaluate_case(pool: asyncpg.Pool, case: RecallCase, *, mode: str, include_rules: bool) -> dict[str, Any]:
    recall_router.ROUTER_ENABLED = True
    context = await recall_router.soul_recall_router(case.agent, case.query, pool, mode=mode, include_rules=include_rules)
    normalized_context = normalize_for_match(context)
    matched = [needle for needle in case.expected_any if normalize_for_match(needle) in normalized_context]
    return {
        "name": case.name,
        "suite": case.suite,
        "agent": case.agent,
        "query": case.query,
        "hit": bool(matched),
        "matched": matched,
        "expected_any": list(case.expected_any),
        "context_chars": len(context),
        "context_preview": context[:500],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    dsn = resolve_db_dsn()
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=2,
        server_settings={"search_path": os.environ.get("SEAL_SCHEMA", "soul_v3")},
    )
    try:
        selected_cases = [case for case in DEFAULT_CASES if args.suite in ("all", case.suite)]
        results = [
            await evaluate_case(pool, case, mode=args.mode, include_rules=not args.no_rules)
            for case in selected_cases
        ]
    finally:
        await pool.close()
    hits = sum(1 for item in results if item["hit"])
    success_rate = hits / len(results) if results else 0.0
    gate_threshold = args.min_rate
    if args.evidence_gate and gate_threshold is None:
        gate_threshold = min(1.0, 8 / len(results)) if results else 1.0
    suites: dict[str, dict[str, Any]] = {}
    for item in results:
        stats = suites.setdefault(item["suite"], {"cases": 0, "hits": 0, "misses": []})
        stats["cases"] += 1
        if item["hit"]:
            stats["hits"] += 1
        else:
            stats["misses"].append(item["name"])
    for stats in suites.values():
        stats["recall_success_rate"] = stats["hits"] / stats["cases"] if stats["cases"] else 0.0
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "include_rules": not args.no_rules,
        "cases": len(results),
        "hits": hits,
        "recall_success_rate": success_rate,
        "evidence_gate_threshold": gate_threshold,
        "evidence_gate_passed": gate_threshold is None or success_rate >= gate_threshold,
        "hit_names": [item["name"] for item in results if item["hit"]],
        "misses": [item["name"] for item in results if not item["hit"]],
        "suites": suites,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate SOUL Memory v5 recent/source recall.")
    parser.add_argument("--mode", default="standard", choices=("micro", "standard", "deep", "boot"))
    parser.add_argument("--suite", default="all", help="Run all cases or a specific suite name.")
    parser.add_argument("--no-rules", action="store_true", help="Diagnostic only: evaluate without global rules occupying top-k.")
    parser.add_argument("--evidence-gate", action="store_true", help="Require the operational gate threshold (default: 8/12).")
    parser.add_argument("--min-rate", type=float, default=None, help="Exit non-zero if recall_success_rate is below this value.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any case misses.")
    args = parser.parse_args()
    payload = asyncio.run(run(args))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if args.strict and payload["hits"] != payload["cases"]:
        return 1
    gate_threshold = payload["evidence_gate_threshold"]
    if gate_threshold is not None and payload["recall_success_rate"] < gate_threshold:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
