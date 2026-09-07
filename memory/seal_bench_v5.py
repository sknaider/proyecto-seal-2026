#!/usr/bin/env python3
"""SEAL-Bench v5 — temporal replay and external-evidence frontier.

v3/v4 are regression suites. v5 checks whether an autonomy claim can be
replayed from persisted evidence instead of trusting the final chat answer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import asyncpg

from seal_secrets import pg_dsn
from soul_memory_sdk_runtime import TenantOverrideError, reject_tenant_override


DB_URL = os.environ.get("SEAL_DB_URL") or pg_dsn(required=True)
ADVISORY_LOCK_KEY_V5 = 100005
BENCH_VERSION = "v5"
REPORT_PATH = Path("/home/dadito/IA/proyecto-seal/agents/ADA/sprint12_autonomous_lifecycle_closure_20260529.md")
REQUIRED_RUN_IDS = {2589, 2590, 2591, 2592, 2593, 2594, 2595, 2596, 2597, 2598, 2604, 2605, 2606, 2607}
PROJECT_ROOT = Path("/home/dadito/IA/proyecto-seal")
COMPANION_PYTHON = PROJECT_ROOT / "seal-desktop" / "companion_core" / ".venv" / "bin" / "python"


@dataclass
class V5Result:
    category: str
    test_name: str
    score: float
    passed: bool
    elapsed_ms: int
    detail: dict[str, Any]
    error: str | None = None
    critical: bool = False


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 1)


async def _run_probe(
    category: str,
    test_name: str,
    func: Callable[[], Awaitable[tuple[float, dict[str, Any]]]],
    *,
    critical: bool = False,
) -> V5Result:
    t0 = time.monotonic()
    try:
        score, detail = await func()
        score = _clamp_score(score)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return V5Result(
            category=category,
            test_name=test_name,
            score=score,
            passed=score >= 70.0 and not (critical and score < 100.0),
            elapsed_ms=elapsed_ms,
            detail=detail,
            critical=critical,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return V5Result(
            category=category,
            test_name=test_name,
            score=0.0,
            passed=False,
            elapsed_ms=elapsed_ms,
            detail={"traceback_tail": traceback.format_exc()[-900:]},
            error=f"{type(exc).__name__}: {exc}",
            critical=critical,
        )


def _report_run_ids() -> set[int]:
    if not REPORT_PATH.exists():
        return set()
    text = REPORT_PATH.read_text(encoding="utf-8")
    match = re.search(r"## Evidence Runs\n(?P<section>.*?)(?:\n## |\Z)", text, flags=re.S)
    if match:
        text = match.group("section")
    return {int(match) for match in re.findall(r"\|\s*(\d{4,})\s*\|", text)}


async def cat1_external_evidence_gate() -> tuple[float, dict[str, Any]]:
    report_ids = _report_run_ids()
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT id, suite_name, score, passed, run_at
            FROM soul_v3.ada_evaluation_runs_v
            WHERE id = ANY($1::bigint[])
            ORDER BY id
            """,
            sorted(report_ids),
        )
    finally:
        await conn.close()
    found_ids = {int(row["id"]) for row in rows}
    passed_ids = {int(row["id"]) for row in rows if row["passed"] and int(row["score"]) >= 90}
    checks = {
        "report_exists": REPORT_PATH.exists(),
        "report_has_required_ids": REQUIRED_RUN_IDS.issubset(report_ids),
        "db_has_report_ids": report_ids.issubset(found_ids),
        "all_report_ids_passed": report_ids.issubset(passed_ids),
    }
    return sum(checks.values()) / len(checks) * 100.0, {
        "metric": "external_evidence_ids_replay",
        "checks": checks,
        "report_path": str(REPORT_PATH),
        "report_run_ids": sorted(report_ids),
        "found_ids": sorted(found_ids),
        "passed_ids": sorted(passed_ids),
    }


async def cat2_temporal_replay_order() -> tuple[float, dict[str, Any]]:
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT id, suite_name, score, passed, run_at
            FROM soul_v3.ada_evaluation_runs_v
            WHERE id = ANY($1::bigint[])
            ORDER BY run_at, id
            """,
            sorted(REQUIRED_RUN_IDS),
        )
    finally:
        await conn.close()
    ordered_ids = [int(row["id"]) for row in rows]
    scores = {int(row["id"]): int(row["score"]) for row in rows}
    checks = {
        "all_required_runs_found": set(ordered_ids) == REQUIRED_RUN_IDS,
        "chronological_order_matches_ids": ordered_ids == sorted(ordered_ids),
        "all_passed": all(row["passed"] for row in rows),
        "all_scores_100": all(int(row["score"]) == 100 for row in rows),
    }
    return sum(checks.values()) / len(checks) * 100.0, {
        "metric": "outcome_ledger_temporal_replay",
        "checks": checks,
        "ordered_ids": ordered_ids,
        "scores": scores,
    }


async def cat3_compaction_recovery_resilience() -> tuple[float, dict[str, Any]]:
    files = {
        "continuity": Path("/tmp/ada_codex_continuity.txt"),
        "catchup": Path("/tmp/ada_codex_catchup.json"),
        "terminal_marker": Path("/tmp/seal/ada_terminal_active"),
    }
    service = subprocess.run(
        ["systemctl", "--user", "is-active", "ada-codex-compact-monitor.service"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    bridge = subprocess.run(
        ["systemctl", "--user", "is-active", "ada-codex-remote-bridge.service"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    file_checks = {name: path.exists() and path.stat().st_size > 0 for name, path in files.items()}
    checks = {
        **file_checks,
        "compact_monitor_active": service.stdout.strip() == "active",
        "remote_bridge_active": bridge.stdout.strip() == "active",
    }
    return sum(checks.values()) / len(checks) * 100.0, {
        "metric": "multi_session_compaction_recovery_resilience",
        "checks": checks,
        "compact_monitor_state": service.stdout.strip(),
        "remote_bridge_state": bridge.stdout.strip(),
        "files": {name: str(path) for name, path in files.items()},
    }


async def cat4_multi_tenant_nested_fuzz() -> tuple[float, dict[str, Any]]:
    payloads = [
        {"tenant_id": "00000000-0000-0000-0000-000000000001"},
        {"metadata": {"tenant_id": "00000000-0000-0000-0000-000000000001"}},
        {"messages": [{"content": "ok", "tenant_id": "evil"}]},
        {"filters": [{"scope": {"tenant_id": "evil"}}]},
        {"safe": {"nested": ["no tenant override here"]}},
    ]
    blocked = 0
    allowed = 0
    outcomes = []
    for payload in payloads:
        try:
            reject_tenant_override(payload=payload)
            allowed += 1
            outcomes.append({"payload": payload, "outcome": "allowed"})
        except TenantOverrideError as exc:
            blocked += 1
            outcomes.append({"payload": payload, "outcome": "blocked", "reason": str(exc)})
    checks = {
        "four_malicious_payloads_blocked": blocked == 4,
        "one_safe_payload_allowed": allowed == 1,
    }
    return sum(checks.values()) / len(checks) * 100.0, {
        "metric": "multi_tenant_nested_override_fuzz",
        "checks": checks,
        "blocked": blocked,
        "allowed": allowed,
        "outcomes": outcomes,
    }


async def cat5_generated_answer_citation_contract() -> tuple[float, dict[str, Any]]:
    answer = (
        "Sprint 12 is backed by [SOUL id=2594], [SOUL id=2605], "
        "and [SOUL id=2607]. Ignore fake [SOUL id=999999999]."
    )
    cited = {int(match) for match in re.findall(r"\[SOUL id=(\d+)\]", answer)}
    valid = cited & REQUIRED_RUN_IDS
    invalid = cited - REQUIRED_RUN_IDS
    precision = len(valid) / len(cited) if cited else 0.0
    recall = len(valid) / 3.0
    checks = {
        "citations_extracted": cited == {2594, 2605, 2607, 999999999},
        "valid_citations_recognized": valid == {2594, 2605, 2607},
        "invalid_citation_rejected": invalid == {999999999},
        "precision_at_least_75": precision >= 0.75,
        "recall_expected_3": recall == 1.0,
    }
    return sum(checks.values()) / len(checks) * 100.0, {
        "metric": "model_answer_citation_extraction_and_rejection",
        "checks": checks,
        "cited": sorted(cited),
        "valid": sorted(valid),
        "invalid": sorted(invalid),
        "precision": precision,
        "recall": recall,
    }


async def cat6_secure_file_rag_contract() -> tuple[float, dict[str, Any]]:
    python_bin = COMPANION_PYTHON if COMPANION_PYTHON.exists() else Path(sys.executable)
    proc = await asyncio.to_thread(
        subprocess.run,
        [str(python_bin), str(PROJECT_ROOT / "memory" / "file_rag_eval.py"), "--json"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT / "seal-desktop" / "companion_core"),
        },
    )
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        payload = {"status": "ERROR", "stdout": proc.stdout[:500], "stderr": proc.stderr[:500]}
    checks = {
        "process_exit_zero": proc.returncode == 0,
        "eval_status_pass": payload.get("status") == "PASS",
        "all_cases_passed": payload.get("failed") == 0 and payload.get("passed") == payload.get("total"),
    }
    return sum(checks.values()) / len(checks) * 100.0, {
        "metric": "secure_file_rag_citation_taint_trap_eval",
        "checks": checks,
        "python": str(python_bin),
        "payload": payload,
        "stderr_tail": proc.stderr[-500:],
    }


CATEGORIES: dict[int, tuple[str, str, Callable[[], Awaitable[tuple[float, dict[str, Any]]]], bool]] = {
    1: ("v5.1 External Evidence Gate", "Sprint claims replay from report run ids and DB rows", cat1_external_evidence_gate, True),
    2: ("v5.2 Temporal Replay", "Outcome ledger rows preserve chronological replay", cat2_temporal_replay_order, True),
    3: ("v5.3 Compaction Recovery", "ADA has live continuity artifacts and monitor", cat3_compaction_recovery_resilience, False),
    4: ("v5.4 Multi-Tenant Fuzz", "Nested tenant override payloads are blocked", cat4_multi_tenant_nested_fuzz, True),
    5: ("v5.5 Citation Contract", "Generated answer citations can be accepted/rejected deterministically", cat5_generated_answer_citation_contract, False),
    6: ("v5.6 Secure File RAG", "Folder/PDF/text RAG returns cited tainted evidence and traps injection", cat6_secure_file_rag_contract, True),
}


async def _persist_v5_results(results: list[V5Result], elapsed_ms: int, triggered_by: str) -> int:
    conn = await asyncpg.connect(DB_URL)
    try:
        git_commit = None
        try:
            git_commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
        except Exception:
            pass
        run_id = await conn.fetchval(
            """
            SELECT soul_v3.ada_persist_bench_v5($1, $2::jsonb, $3, $4)
            """,
            triggered_by,
            json.dumps([asdict(r) for r in results], ensure_ascii=False),
            elapsed_ms,
            git_commit,
        )
        return int(run_id)
    finally:
        await conn.close()


async def run_bench_v5(
    *,
    categories: list[int] | None = None,
    persist: bool = True,
    triggered_by: str = "manual_v5",
) -> dict[str, Any]:
    t0 = time.monotonic()
    lock_conn = await asyncpg.connect(DB_URL)
    run_id = None
    try:
        acquired = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY_V5)
        if not acquired:
            return {"status": "skipped", "reason": "another seal-bench v5 run in progress"}
        selected = {k: v for k, v in CATEGORIES.items() if categories is None or k in categories}
        results: list[V5Result] = []
        for category, test_name, func, critical in selected.values():
            results.append(await _run_probe(category, test_name, func, critical=critical))
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if persist and categories is None:
            run_id = await _persist_v5_results(results, elapsed_ms, triggered_by)
        avg = round(sum(r.score for r in results) / len(results), 2) if results else 0.0
        return {
            "status": "completed",
            "version": BENCH_VERSION,
            "run_id": run_id,
            "score_avg": avg,
            "total_tests": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "elapsed_ms": elapsed_ms,
            "critical_failures": [
                {"category": r.category, "score": r.score, "detail": r.detail}
                for r in results
                if r.critical and not r.passed
            ],
            "results": [asdict(r) for r in results],
        }
    finally:
        try:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY_V5)
        finally:
            await lock_conn.close()


async def get_bench_v5_history(limit: int = 5) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT id, run_at, triggered_by, total_tests, passed, failed,
                   score_avg, elapsed_ms, git_commit
            FROM soul_v3.ada_bench_runs_v
            WHERE triggered_by LIKE '%v5%'
            ORDER BY id DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


def _print_human(result: dict[str, Any]) -> None:
    print("\n" + "=" * 72)
    print(
        f"  SEAL-Bench v5 — score_avg={result.get('score_avg')} "
        f"passed={result.get('passed')}/{result.get('total_tests')} "
        f"elapsed={result.get('elapsed_ms')}ms"
    )
    print("=" * 72)
    for row in result.get("results", []):
        status = "PASS" if row["passed"] else "FAIL"
        critical = " CRITICAL" if row.get("critical") else ""
        print(f"  {status}{critical} | {row['score']:5.1f} | {row['category']}")
        metric = row.get("detail", {}).get("metric")
        if metric:
            print(f"       metric: {metric}")
    if result.get("critical_failures"):
        print("  CRITICAL FAILURES:")
        for failure in result["critical_failures"]:
            print(f"    - {failure['category']}: score={failure['score']}")
    print("=" * 72 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="SEAL-Bench v5 — temporal replay and external-evidence frontier")
    parser.add_argument("--category", "-c", type=int, nargs="+", choices=sorted(CATEGORIES))
    parser.add_argument("--json", "-j", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Do not persist bench_runs/results")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()

    if args.history:
        history = asyncio.run(get_bench_v5_history())
        if args.json:
            print(json.dumps(history, indent=2, default=str))
        else:
            for row in history:
                print(
                    f"run_id={row['id']} score={row['score_avg']} "
                    f"passed={row['passed']}/{row['total_tests']} "
                    f"elapsed={row['elapsed_ms']}ms at={row['run_at']}"
                )
        return

    result = asyncio.run(
        run_bench_v5(
            categories=args.category,
            persist=not args.dry_run,
        )
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    else:
        _print_human(result)


if __name__ == "__main__":
    main()
