#!/usr/bin/env python3
"""Frozen synthetic held-out run for the PAST attribution harness.

This runner never opens SOUL DB, performs network I/O or imports a live model.
Its deterministic extractor and grader are fixed before opening the held-out
results.  The resulting evidence supports only attribution at the harness
boundary under this synthetic runtime; it is not a production causal claim.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from memory.past_attribution_harness import HarnessConfig, estimate_tokens, write_evidence_json
from memory.past_live_adapter import (
    FixedRuntimeContract,
    GraderResponse,
    ModelResponse,
    RetrievedMemory,
    RetrievalResult,
    load_live_dataset,
    run_readonly_live_adapter,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "corpus_rsi" / "past_heldout_v1_dataset.json"
DEFAULT_PREREG = ROOT / "corpus_rsi" / "past_heldout_v1_preregister.json"
DEFAULT_EVIDENCE = ROOT / "corpus_rsi" / "past_heldout_v1_evidence.json"

MODEL_SPEC = (
    "seal.past-heldout.deterministic-extractor.v1|"
    "extract-first-FACT-token|otherwise-UNKNOWN|no-external-state"
)
GRADER_SPEC = "seal.past-heldout.exact-token-grader.v1|case-sensitive-exact-match"
PROMPT_TEMPLATE = (
    "HELDOUT_QUERY:\n{query}\n\n"
    "HELDOUT_PERSISTENCE:\n{persistence_context}\n\n"
    "Return only the synthetic token or UNKNOWN."
)
TOKEN_RE = re.compile(r"FACT::([A-Z0-9-]+)")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _verify_preregistration(
    dataset_path: Path, prereg_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    prereg = _load_json(prereg_path)
    dataset = _load_json(dataset_path)
    if prereg.get("schema") != "seal.past-heldout.preregister.v1":
        raise ValueError("invalid held-out preregistration schema")
    if prereg.get("status") != "frozen_before_results":
        raise ValueError("held-out preregistration is not frozen")
    if prereg.get("harness_commit") != "b6d2eeaf3":
        raise ValueError("unexpected harness commit")
    expected_dataset_hash = str(prereg.get("dataset_sha256", ""))
    if _sha256_file(dataset_path) != expected_dataset_hash:
        raise ValueError("held-out dataset hash mismatch")
    for relative, expected in dict(prereg.get("frozen_file_sha256", {})).items():
        candidate = ROOT / relative
        if not candidate.is_file() or _sha256_file(candidate) != expected:
            raise ValueError(f"frozen file hash mismatch: {relative}")
    runtime = dict(prereg.get("runtime_contract", {}))
    expected_runtime = {
        "model_id": "seal-deterministic-heldout-v1",
        "model_sha256": _sha256_text(MODEL_SPEC),
        "prompt_template_sha256": _sha256_text(PROMPT_TEMPLATE),
        "grader_id": "seal-exact-token-grader-v1",
        "grader_sha256": _sha256_text(GRADER_SPEC),
    }
    if runtime != expected_runtime:
        raise ValueError("runtime contract differs from frozen preregistration")
    if dataset.get("suite") != "seal.past-heldout.synthetic.v1":
        raise ValueError("unexpected held-out dataset suite")
    custody = dict(dataset.get("custody", {}))
    if custody.get("classification") != "fully_synthetic":
        raise ValueError("held-out dataset is not fully synthetic")
    if custody.get("contains_live_soul_memory") is not False:
        raise ValueError("held-out dataset may contain live SOUL memory")
    if custody.get("contains_private_memory") is not False:
        raise ValueError("held-out dataset may contain private memory")
    commit = subprocess.run(
        ["git", "cat-file", "-e", "b6d2eeaf3^{commit}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if commit.returncode != 0:
        raise ValueError("frozen harness commit is unavailable")
    return prereg, dataset


def _runtime_contract() -> FixedRuntimeContract:
    return FixedRuntimeContract(
        model_id="seal-deterministic-heldout-v1",
        model_sha256=_sha256_text(MODEL_SPEC),
        prompt_template=PROMPT_TEMPLATE,
        prompt_template_sha256=_sha256_text(PROMPT_TEMPLATE),
        grader_id="seal-exact-token-grader-v1",
        grader_sha256=_sha256_text(GRADER_SPEC),
    )


async def run_heldout(
    *,
    dataset_path: Path = DEFAULT_DATASET,
    prereg_path: Path = DEFAULT_PREREG,
    evidence_path: Path = DEFAULT_EVIDENCE,
) -> dict[str, Any]:
    prereg, raw_dataset = _verify_preregistration(dataset_path, prereg_path)
    cases = load_live_dataset(dataset_path)
    artifacts = dict(raw_dataset.get("artifacts", {}))

    async def retriever(case) -> RetrievalResult:
        required = (*case.expected_memory_ids, *case.placebo_memory_ids)
        missing = set(required) - set(artifacts)
        if missing:
            raise ValueError(f"synthetic catalog is missing IDs: {sorted(missing)}")
        resolved = {
            memory_id: RetrievedMemory(
                memory_id=memory_id,
                source=str(artifacts[memory_id]["source"]),
                content=str(artifacts[memory_id]["content"]),
                metadata={"classification": "synthetic_benchmark"},
            )
            for memory_id in required
        }
        return RetrievalResult(
            query_hits=tuple(resolved[memory_id] for memory_id in case.expected_memory_ids),
            artifacts_by_id=resolved,
            query_sha256=_sha256_text(case.query),
            route_id="sealed-synthetic-catalog-select-only-v1",
            read_only_attested=True,
            limits=("in_memory_catalog", "no_DB", "no_network"),
        )

    def model(request) -> ModelResponse:
        matched = TOKEN_RE.search(request.prompt)
        output = matched.group(1) if matched else "UNKNOWN"
        return ModelResponse(
            output=output,
            observed_prompt_sha256=request.prompt_sha256,
            observed_model_sha256=request.model_sha256,
            input_tokens=estimate_tokens(request.prompt),
            output_tokens=estimate_tokens(output),
            metadata={"runtime": "deterministic", "external_state": False},
        )

    def grader(request) -> GraderResponse:
        expected = str(request.grader_payload["expected"])
        return GraderResponse(
            score=1.0 if request.output == expected else 0.0,
            observed_grader_sha256=request.grader_sha256,
            metadata={"method": "exact_match"},
        )

    analysis = dict(prereg["analysis_plan"])
    config = HarnessConfig(
        subject="BENCH_PAST_HELDOUT_V1",
        seed=int(analysis["seed"]),
        trials=int(analysis["trials_per_case"]),
        pass_threshold=float(analysis["pass_threshold"]),
        minimum_effect=float(analysis["minimum_effect"]),
        minimum_expected_read_coverage=float(analysis["minimum_expected_read_coverage"]),
        preserve_raw_output=False,
        placebo_token_tolerance=float(analysis["placebo_token_tolerance"]),
    )
    report = await run_readonly_live_adapter(
        cases,
        retriever=retriever,
        runtime=_runtime_contract(),
        model=model,
        grader=grader,
        harness_config=config,
    )
    expected_scores = dict(analysis["expected_arm_scores"])
    failures: list[str] = []
    for summary in report["summaries"]:
        if summary["verdict"] != analysis["required_verdict"]:
            failures.append(f"{summary['case_id']}:verdict={summary['verdict']}")
        if summary["scores"] != expected_scores:
            failures.append(f"{summary['case_id']}:scores={summary['scores']}")
        if summary["persistence_on_expected_read_coverage"] < config.minimum_expected_read_coverage:
            failures.append(f"{summary['case_id']}:read_coverage")
    expected_trials = len(cases) * len(config.arms) * config.trials
    if len(report["trials"]) != expected_trials:
        failures.append(f"trial_count={len(report['trials'])}!={expected_trials}")
    if failures:
        raise ValueError("held-out preregistered analysis failed: " + ";".join(failures))
    report["heldout_assurance"] = {
        "schema": "seal.past-heldout.assurance.v1",
        "dataset_sha256": _sha256_file(dataset_path),
        "preregister_sha256": _sha256_file(prereg_path),
        "harness_commit": prereg["harness_commit"],
        "case_count": len(cases),
        "trial_count": expected_trials,
        "controls": {
            "positive": "persistence_on succeeds",
            "negative": "persistence_off and placebo fail",
            "non_vacuous": "positive and negative scores differ for every case",
        },
        "analysis_status": "preregistered_thresholds_met",
        "claim_boundary": (
            "synthetic deterministic harness-boundary attribution; "
            "not production causal proof"
        ),
    }
    report.pop("evidence_sha256", None)
    report["evidence_sha256"] = _sha256_bytes(_canonical(report))
    write_evidence_json(report, evidence_path)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run frozen PAST held-out v1")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--preregister", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = asyncio.run(
        run_heldout(
            dataset_path=args.dataset,
            prereg_path=args.preregister,
            evidence_path=args.evidence,
        )
    )
    assurance = report["heldout_assurance"]
    print(json.dumps({
        "ok": True,
        "case_count": assurance["case_count"],
        "trial_count": assurance["trial_count"],
        "analysis_status": assurance["analysis_status"],
        "evidence": str(args.evidence),
        "evidence_sha256": report["evidence_sha256"],
        "claim_boundary": assurance["claim_boundary"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
