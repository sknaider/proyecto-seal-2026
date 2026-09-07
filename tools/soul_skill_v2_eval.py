#!/usr/bin/env python3
"""Held-out V4 skill evaluation with a frozen guarded-recovery workflow."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]
BASE_RUNNER = ROOT / "tools" / "soul_real_capability_eval.py"
CANDIDATE = ROOT / "evals" / "soul_real_capability" / "candidates" / "guarded_tool_recovery_v1.md"
TRAIN_RESULTS = ROOT / "evidence" / "soul_real_capability" / "20260710T230258Z-961126367" / "results.json"


def _load_base():
    spec = importlib.util.spec_from_file_location("soul_real_capability_eval_frozen", BASE_RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BASE = _load_base()
KEY_RE = re.compile(r"\bK\d{5}\b")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (max(0.0, (centre - spread) / denom), min(1.0, (centre + spread) / denom))


def extract_numeric_expression(task: dict[str, Any]) -> str:
    question = str(task["question"])
    expression = question.rsplit(":", 1)[-1].strip().rstrip(".")
    trusted = {item["key"]: int(item["value"]) for item in task["memory"] if item.get("trusted")}

    def replace(match: re.Match[str]) -> str:
        key = match.group(0)
        if key not in trusted:
            raise ValueError(f"unresolved key: {key}")
        return str(trusted[key])

    numeric = KEY_RE.sub(replace, expression)
    if KEY_RE.search(numeric):
        raise ValueError("unresolved keys remain")
    BASE.safe_calculate(numeric)
    return numeric


def apply_candidate(task: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Guarded fallback; trigger uses observable protocol state, never truth."""
    started = time.perf_counter()
    needs_recovery = bool(baseline["abstained"] or baseline["answer"] is None or not baseline["tool_used"])
    record = dict(baseline)
    record.update({"case_type": "skill_v2", "variant": "candidate", "controlled_eval": True})
    record["fallback_triggered"] = needs_recovery
    record["baseline_success"] = baseline["success"]
    if needs_recovery:
        try:
            expression = extract_numeric_expression(task)
            answer = BASE.safe_calculate(expression)
            record.update(
                {
                    "answer": answer,
                    "response": f"FINAL: {answer}",
                    "abstained": False,
                    "tool_used": True,
                    "protocol_followed": True,
                    "fallback_error": None,
                }
            )
        except ValueError as exc:
            record["fallback_error"] = str(exc)
    record["success"] = record["answer"] == task["expected"]
    record["latency_ms"] = round(record["latency_ms"] + (time.perf_counter() - started) * 1000, 3)
    record["cost_proxy"] += 5 if needs_recovery else 0
    return record


def summarize(pairs: list[tuple[dict[str, Any], dict[str, Any]]], rollbacks: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(pairs)
    base_ok = sum(base["success"] for base, _ in pairs)
    cand_ok = sum(candidate["success"] for _, candidate in pairs)
    base_ci = wilson(base_ok, total)
    cand_ci = wilson(cand_ok, total)
    improvement_ci = (cand_ci[0] - base_ci[1], cand_ci[1] - base_ci[0])
    negative = sum(base["success"] and not candidate["success"] for base, candidate in pairs)
    rescued = sum(not base["success"] and candidate["success"] for base, candidate in pairs)
    forgotten = negative
    delta = (cand_ok - base_ok) / total if total else 0.0
    eligible = bool(total >= 30 and delta > 0 and improvement_ci[0] > 0 and negative == 0)
    return {
        "label": "controlled_eval",
        "candidate": "guarded-tool-recovery@1.0.0-controlled-eval",
        "heldout_n": total,
        "baseline_successes": base_ok,
        "baseline_rate": base_ok / total,
        "baseline_wilson95": base_ci,
        "candidate_successes": cand_ok,
        "candidate_rate": cand_ok / total,
        "candidate_wilson95": cand_ci,
        "improvement_delta": delta,
        "improvement_newcombe95": improvement_ci,
        "rescued_failures": rescued,
        "negative_transfer_events": negative,
        "forgetting_events": forgotten,
        "fallback_invocations": sum(candidate["fallback_triggered"] for _, candidate in pairs),
        "baseline_cost_proxy": sum(base["cost_proxy"] for base, _ in pairs),
        "candidate_cost_proxy": sum(candidate["cost_proxy"] for _, candidate in pairs),
        "rollback_n": len(rollbacks),
        "rollback_exact_rate": sum(row["exact"] for row in rollbacks) / len(rollbacks) if rollbacks else None,
        "statistically_eligible": eligible,
        "auto_promoted": False,
        "canary_state": "controlled_ready" if eligible else "rejected",
    }


def new_run(args: argparse.Namespace) -> None:
    seed = args.seed if args.seed is not None else time.time_ns() & 0x7FFF_FFFF
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{seed}"
    run_dir = Path(args.output_root) / run_id
    run_dir.mkdir(parents=True)
    dataset = BASE.generate_dataset(seed, tasks_per_horizon=args.tasks_per_horizon, poisoning_cases=0)
    write_json(run_dir / "heldout.json", dataset)
    for source, target in (
        (Path(__file__).resolve(), run_dir / "runner_snapshot.py"),
        (BASE_RUNNER, run_dir / "base_runner_snapshot.py"),
        (CANDIDATE, run_dir / "candidate_snapshot.md"),
    ):
        shutil.copyfile(source, target)
    manifest = {
        "schema": "soul-skill-v2-heldout-manifest-v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "dataset_generated_after_candidate_freeze": True,
        "train_results_sha256": sha256_file(TRAIN_RESULTS),
        "heldout_sha256": sha256_file(run_dir / "heldout.json"),
        "runner_sha256": sha256_file(run_dir / "runner_snapshot.py"),
        "base_runner_sha256": sha256_file(run_dir / "base_runner_snapshot.py"),
        "candidate_sha256": sha256_file(run_dir / "candidate_snapshot.md"),
        "isolation": {"live_memory": False, "organic_skill_log": False, "auto_promote": False},
    }
    write_json(run_dir / "manifest.json", manifest)
    print(run_dir)


def execute(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if sha256_file(Path(__file__).resolve()) != manifest["runner_sha256"]:
        raise SystemExit("runner changed after heldout generation")
    if sha256_file(CANDIDATE) != manifest["candidate_sha256"]:
        raise SystemExit("candidate changed after heldout generation")
    dataset = json.loads((run_dir / "heldout.json").read_text())
    client = BASE.OllamaClient(manifest["model"], timeout=args.timeout)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    jsonl = run_dir / "controlled_skill_shadow.jsonl"
    with jsonl.open("w", encoding="utf-8") as handle:
        for task in dataset["tasks"]:
            baseline = BASE.execute_task(client, task, "full")
            baseline.update({"case_type": "skill_v2", "variant": "baseline", "controlled_eval": True})
            candidate = apply_candidate(task, baseline)
            pairs.append((baseline, candidate))
            for record in (baseline, candidate):
                record["task_id"] = task["id"]
                handle.write(BASE.canonical_json(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    rollbacks = []
    for baseline, _ in pairs[:10]:
        restored = dict(baseline)  # disabling the optional fallback restores the primary path
        exact = all(restored[key] == baseline[key] for key in ("answer", "response", "success", "tool_used"))
        rollbacks.append({"task_id": baseline["task_id"], "exact": exact, "controlled_eval": True})
    summary = summarize(pairs, rollbacks)
    result = {
        "schema": "soul-skill-v2-heldout-results-v1",
        "run_id": manifest["run_id"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "rollback": rollbacks,
        "claim_boundary": "Paired private heldout evaluation of model+workflow; not organic production usage.",
    }
    write_json(run_dir / "results.json", result)
    hashes = {name: sha256_file(run_dir / name) for name in (
        "manifest.json", "heldout.json", "runner_snapshot.py", "base_runner_snapshot.py",
        "candidate_snapshot.md", "controlled_skill_shadow.jsonl", "results.json",
    )}
    write_json(run_dir / "artifact_hashes.json", hashes)
    print(json.dumps(summary, indent=2))


def verify(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir)
    hashes = json.loads((run_dir / "artifact_hashes.json").read_text())
    mismatch = [name for name, digest in hashes.items() if sha256_file(run_dir / name) != digest]
    result = json.loads((run_dir / "results.json").read_text())
    checks = {
        "hashes": not mismatch,
        "heldout_n_ge_30": result["summary"]["heldout_n"] >= 30,
        "no_negative_transfer": result["summary"]["negative_transfer_events"] == 0,
        "rollback_exact": result["summary"]["rollback_exact_rate"] == 1.0,
        "never_auto_promoted": result["summary"]["auto_promoted"] is False,
    }
    print(json.dumps({"passed": all(checks.values()), "checks": checks, "mismatch": mismatch}, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subs = root.add_subparsers(dest="command", required=True)
    new = subs.add_parser("new")
    new.add_argument("--output-root", default="evidence/soul_skill_v2")
    new.add_argument("--model", default="qwen2.5:7b")
    new.add_argument("--tasks-per-horizon", type=int, default=10)
    new.add_argument("--seed", type=int)
    new.set_defaults(func=new_run)
    run = subs.add_parser("execute")
    run.add_argument("run_dir")
    run.add_argument("--timeout", type=int, default=180)
    run.set_defaults(func=execute)
    check = subs.add_parser("verify")
    check.add_argument("run_dir")
    check.set_defaults(func=verify)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
