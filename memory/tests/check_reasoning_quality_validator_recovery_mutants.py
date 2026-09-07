#!/usr/bin/env python3
"""Mutation harness for the recovered reasoning-trace persistence contract."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "memory/reasoning_quality_validator.py"
TEST = ROOT / "memory/test_reasoning_quality_validator_recovery.py"

MUTANTS = {
    "gap_validation_bypassed": (
        "gap = detect_missing_premise_gap(trace, premises)",
        'gap = {"missing_premise_detected": False, "markers": [], "missing_terms": [], "note": ""}',
    ),
    "trace_fetch_broken": (
        "row = await conn.fetchrow(",
        "row = await conn.fetchrow_REMOVED(",
    ),
    "gap_never_detected": (
        "detected = bool(markers or missing_terms)",
        "detected = False",
    ),
    "quality_not_capped": (
        'report["quality_score"] = min(float(report.get("quality_score") or 0.0), 0.35)',
        'report["quality_score"] = float(report.get("quality_score") or 0.0)',
    ),
    "annotation_not_persisted": (
        'updated_reasoning = f"{updated_reasoning}\\n{annotation}"',
        "updated_reasoning = updated_reasoning",
    ),
    "annotation_duplicated": (
        "if annotation and annotation.lower() not in updated_reasoning.lower():",
        "if annotation:",
    ),
    "unknown_trace_succeeds": (
        'raise ValueError(f"reasoning_trace not found: {trace_id}")',
        "return {}",
    ),
    "database_write_skipped": (
        "    await conn.execute(\n",
        "    if False:\n        await conn.execute(\n",
    ),
    "wrong_update_table": (
        "UPDATE soul_v3.reasoning_traces",
        "UPDATE soul_v3.memories",
    ),
}


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_test(work: pathlib.Path) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(work)
    return subprocess.run(
        ["python3", "-m", "pytest", "-q", str(work / "memory" / TEST.name)],
        cwd=work,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    ).returncode


def main() -> int:
    original = SOURCE.read_text(encoding="utf-8")
    details = []
    with tempfile.TemporaryDirectory(prefix="reasoning-validator-mutants-") as raw:
        work = pathlib.Path(raw)
        memory_dir = work / "memory"
        memory_dir.mkdir()
        (memory_dir / "__init__.py").write_text("", encoding="utf-8")
        shutil.copy2(TEST, memory_dir / TEST.name)
        (memory_dir / SOURCE.name).write_text(original, encoding="utf-8")
        positive_rc = run_test(work)
        for name, (old, new) in MUTANTS.items():
            assert original.count(old) == 1, f"costura ambigua para {name}"
            (memory_dir / SOURCE.name).write_text(original.replace(old, new), encoding="utf-8")
            rc = run_test(work)
            details.append(
                {
                    "mutant": name,
                    "rc": rc,
                    "killed": rc == 1,
                    "suspicious": rc not in (0, 1),
                }
            )

    killed = sum(row["killed"] for row in details)
    suspicious = sum(row["suspicious"] for row in details)
    evidence = {
        "schema": "seal.mutation-evidence.v1",
        "tool": "explicit mutants on an isolated copy; only pytest rc=1 kills",
        "workspace": "recovered reasoning gap detection and persistence contract",
        "killed": killed,
        "survived": len(details) - killed - suspicious,
        "suspicious": suspicious,
        "total": len(details),
        "mutation_score_percent": round(100.0 * killed / len(details), 1),
        "positive_control_unmutated_copy": {"rc": positive_rc, "ok": positive_rc == 0},
        "reviewer": "pending",
        "file_sha256": {
            str(SOURCE.relative_to(ROOT)): sha256(SOURCE),
            str(TEST.relative_to(ROOT)): sha256(TEST),
            str(pathlib.Path(__file__).resolve().relative_to(ROOT)): sha256(pathlib.Path(__file__).resolve()),
        },
        "detail": details,
    }
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if positive_rc == 0 and killed == len(details) and suspicious == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
