#!/usr/bin/env python3
"""PAST-style persistence attribution harness for SOUL/SEAL-Bench.

This module is intentionally storage-agnostic.  It never opens SOUL DB and it
does not import the production Recall Router.  A caller supplies an ``execute``
callback and receives matched experimental arms with the same prompt, case,
grader contract and trial seed.

The first three arms mirror the minimum PAST-Bench comparison:

``persistence_on``
    Candidate retained artifacts are available.
``persistence_off``
    Family-produced retained state is unavailable.
``placebo``
    Irrelevant artifacts with a comparable estimated token budget are exposed.

``delete``, ``replace`` and ``corrupt`` are counterfactual scaffolding inspired
by PAST-Bench's proposed stronger mechanism-attribution tests.  They are not a
claim of causal proof.  The evidence report preserves exact artifact IDs,
content hashes, seeds, timings and token/cost estimates without persisting raw
memory content or model output by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence


EVIDENCE_SCHEMA = "seal.past-attribution.evidence.v1"
CONFIG_SCHEMA = "seal.past-attribution.config.v1"
DATASET_SCHEMA = "seal.past-attribution.dataset.v1"
ArmName = Literal[
    "persistence_on",
    "persistence_off",
    "placebo",
    "delete",
    "replace",
    "corrupt",
]
STANDARD_ARMS: tuple[ArmName, ...] = (
    "persistence_on",
    "persistence_off",
    "placebo",
    "delete",
    "replace",
    "corrupt",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def _metadata_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Seal untrusted metadata without persisting its potentially raw values."""

    serialized = _canonical_json(dict(value))
    return {
        "sha256": _sha256_text(serialized),
        "utf8_bytes": len(serialized.encode("utf-8")),
        "field_count": len(value),
    }


def estimate_tokens(text: str) -> int:
    """Stable offline estimate used only for matching and cost accounting."""

    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


@dataclass(frozen=True)
class RetainedArtifact:
    artifact_id: str
    source: str
    content: str
    replacement: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.artifact_id.strip():
            raise ValueError("artifact_id must not be empty")
        if not self.source.strip():
            raise ValueError("source must not be empty")

    @property
    def content_sha256(self) -> str:
        return _sha256_text(self.content)

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.content)

    def evidence_ref(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "source": self.source,
            "content_sha256": self.content_sha256,
            "estimated_tokens": self.estimated_tokens,
        }


@dataclass(frozen=True)
class AttributionCase:
    case_id: str
    capability: str
    query: str
    retained_artifacts: tuple[RetainedArtifact, ...]
    expected_artifact_ids: tuple[str, ...]
    placebo_artifacts: tuple[RetainedArtifact, ...] = ()
    grader_version: str = "deterministic-v1"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if not self.expected_artifact_ids:
            raise ValueError("expected_artifact_ids must not be empty")
        all_ids = [item.artifact_id for item in (*self.retained_artifacts, *self.placebo_artifacts)]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError(f"artifact IDs must be unique in case {self.case_id}")
        retained_ids = {item.artifact_id for item in self.retained_artifacts}
        missing = set(self.expected_artifact_ids) - retained_ids
        if missing:
            raise ValueError(f"expected artifact IDs are not retained artifacts: {sorted(missing)}")

    def sealed_manifest(self) -> dict[str, Any]:
        payload = {
            "case_id": self.case_id,
            "capability": self.capability,
            "query_sha256": _sha256_text(self.query),
            "grader_version": self.grader_version,
            "expected_artifact_ids": list(self.expected_artifact_ids),
            "retained_artifacts": [item.evidence_ref() for item in self.retained_artifacts],
            "placebo_artifacts": [item.evidence_ref() for item in self.placebo_artifacts],
            "metadata_evidence": _metadata_evidence(self.metadata),
        }
        return {"payload": payload, "sha256": _sha256_json(payload)}


@dataclass(frozen=True)
class HarnessConfig:
    subject: str = "BENCH_SYNTHETIC"
    seed: int = 20260818
    trials: int = 3
    arms: tuple[ArmName, ...] = STANDARD_ARMS
    pass_threshold: float = 0.8
    minimum_effect: float = 0.2
    minimum_expected_read_coverage: float = 1.0
    input_cost_per_million_tokens: float = 0.0
    output_cost_per_million_tokens: float = 0.0
    preserve_raw_output: bool = False
    placebo_token_tolerance: float = 0.35

    def __post_init__(self) -> None:
        if self.subject.strip().upper() == "FABLE":
            raise ValueError("FABLE is reserved as an independent control/reviewer")
        if self.trials < 1:
            raise ValueError("trials must be at least 1")
        if not self.arms:
            raise ValueError("at least one arm is required")
        unknown = set(self.arms) - set(STANDARD_ARMS)
        if unknown:
            raise ValueError(f"unknown arms: {sorted(unknown)}")
        if len(set(self.arms)) != len(self.arms):
            raise ValueError("arms must be unique")
        if not 0.0 <= self.pass_threshold <= 1.0:
            raise ValueError("pass_threshold must be in [0, 1]")
        if not 0.0 <= self.minimum_effect <= 1.0:
            raise ValueError("minimum_effect must be in [0, 1]")
        if not 0.0 <= self.minimum_expected_read_coverage <= 1.0:
            raise ValueError("minimum_expected_read_coverage must be in [0, 1]")
        if self.input_cost_per_million_tokens < 0 or self.output_cost_per_million_tokens < 0:
            raise ValueError("token costs must not be negative")
        if not 0.0 <= self.placebo_token_tolerance <= 1.0:
            raise ValueError("placebo_token_tolerance must be in [0, 1]")

    def sealed(self) -> dict[str, Any]:
        payload = {"schema": CONFIG_SCHEMA, **asdict(self)}
        payload["arms"] = list(self.arms)
        return {"payload": payload, "sha256": _sha256_json(payload)}


@dataclass(frozen=True)
class ExecutionRequest:
    case: AttributionCase
    arm: ArmName
    trial_index: int
    trial_seed: int
    available_artifacts: tuple[RetainedArtifact, ...]
    intervention: dict[str, Any]


@dataclass(frozen=True)
class ExecutionObservation:
    score: float
    output: str
    read_artifact_ids: tuple[str, ...] = ()
    written_artifact_ids: tuple[str, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("observation score must be in [0, 1]")
        if self.input_tokens is not None and self.input_tokens < 0:
            raise ValueError("input_tokens must not be negative")
        if self.output_tokens is not None and self.output_tokens < 0:
            raise ValueError("output_tokens must not be negative")


ExecuteFn = Callable[[ExecutionRequest], ExecutionObservation]


def _trial_seed(base_seed: int, trial_index: int) -> int:
    digest = hashlib.sha256(f"{base_seed}:{trial_index}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big")


def _corrupt_text(text: str, rng: random.Random) -> tuple[str, int | None]:
    if not text:
        return "[CORRUPTED]", None
    # Destroy the semantic payload while preserving whitespace and character
    # count closely enough that the counterfactual does not become a trivial
    # context-length ablation.  A single-byte flip often leaves the answer
    # recoverable and therefore does not exercise the intended negative arm.
    replace_with = "#" if "#" not in text else "?"
    corruptible = [index for index, char in enumerate(text) if not char.isspace()]
    if not corruptible:
        return replace_with * len(text), None
    offset = rng.randrange(len(corruptible))
    ordered = corruptible[offset:] + corruptible[:offset]
    characters = list(text)
    for position in ordered:
        characters[position] = replace_with
    return "".join(characters), ordered[0]


def _select_placebo(
    case: AttributionCase,
    *,
    rng: random.Random,
    target_tokens: int,
    tolerance: float,
) -> tuple[RetainedArtifact, ...]:
    candidates = list(case.placebo_artifacts)
    if target_tokens > 0 and not candidates:
        raise ValueError(f"case {case.case_id} has no placebo artifacts")
    rng.shuffle(candidates)
    # Bounded subset-sum beam: choose a token-matched distractor set instead
    # of blindly taking the first rows.  The cap prevents pathological fixture
    # sizes from turning a benchmark setup check into an exponential search.
    states: dict[int, tuple[int, ...]] = {0: ()}
    for index, item in enumerate(candidates):
        additions = {
            total + item.estimated_tokens: indices + (index,)
            for total, indices in states.items()
        }
        states.update({total: indices for total, indices in additions.items() if total not in states})
        if len(states) > 2048:
            nearest = sorted(states, key=lambda total: (abs(total - target_tokens), total))[:2048]
            states = {total: states[total] for total in nearest}
    nonempty = [(total, indices) for total, indices in states.items() if indices]
    if not nonempty:
        return ()
    actual_tokens, chosen = min(nonempty, key=lambda row: (abs(row[0] - target_tokens), row[0]))
    relative_error = abs(actual_tokens - target_tokens) / max(target_tokens, 1)
    if relative_error > tolerance:
        raise ValueError(
            f"case {case.case_id} placebo token mismatch: "
            f"target={target_tokens} actual={actual_tokens} tolerance={tolerance}"
        )
    return tuple(candidates[index] for index in chosen)


def _arm_artifacts(
    case: AttributionCase,
    arm: ArmName,
    *,
    rng: random.Random,
    placebo_token_tolerance: float,
) -> tuple[tuple[RetainedArtifact, ...], dict[str, Any]]:
    expected = set(case.expected_artifact_ids)
    target_tokens = sum(
        item.estimated_tokens for item in case.retained_artifacts if item.artifact_id in expected
    )

    if arm == "persistence_on":
        artifacts = case.retained_artifacts
        intervention = {"kind": "none"}
    elif arm == "persistence_off":
        artifacts = ()
        intervention = {"kind": "access_removed", "removed_ids": sorted(expected)}
    elif arm == "placebo":
        artifacts = _select_placebo(
            case,
            rng=rng,
            target_tokens=target_tokens,
            tolerance=placebo_token_tolerance,
        )
        actual_tokens = sum(item.estimated_tokens for item in artifacts)
        intervention = {
            "kind": "irrelevant_token_matched",
            "target_tokens": target_tokens,
            "actual_tokens": actual_tokens,
            "relative_error": round(
                abs(actual_tokens - target_tokens) / max(target_tokens, 1),
                6,
            ),
            "tolerance": placebo_token_tolerance,
        }
    elif arm == "delete":
        artifacts = tuple(item for item in case.retained_artifacts if item.artifact_id not in expected)
        intervention = {"kind": "candidate_deleted", "removed_ids": sorted(expected)}
    elif arm == "replace":
        replaced: list[RetainedArtifact] = []
        replacement_hashes: dict[str, str] = {}
        for item in case.retained_artifacts:
            if item.artifact_id not in expected:
                replaced.append(item)
                continue
            replacement = item.replacement or f"[STALE REPLACEMENT FOR {item.artifact_id}]"
            replacement_hashes[item.artifact_id] = _sha256_text(replacement)
            replaced.append(
                RetainedArtifact(
                    artifact_id=item.artifact_id,
                    source=item.source,
                    content=replacement,
                    replacement=item.replacement,
                    metadata={**item.metadata, "past_intervention": "replace"},
                )
            )
        artifacts = tuple(replaced)
        intervention = {"kind": "candidate_replaced", "replacement_sha256": replacement_hashes}
    elif arm == "corrupt":
        corrupted: list[RetainedArtifact] = []
        positions: dict[str, int | None] = {}
        for item in case.retained_artifacts:
            if item.artifact_id not in expected:
                corrupted.append(item)
                continue
            content, position = _corrupt_text(item.content, rng)
            positions[item.artifact_id] = position
            corrupted.append(
                RetainedArtifact(
                    artifact_id=item.artifact_id,
                    source=item.source,
                    content=content,
                    replacement=item.replacement,
                    metadata={**item.metadata, "past_intervention": "corrupt"},
                )
            )
        artifacts = tuple(corrupted)
        intervention = {"kind": "candidate_corrupted", "character_positions": positions}
    else:  # pragma: no cover - guarded by HarnessConfig
        raise ValueError(f"unsupported arm: {arm}")
    return artifacts, intervention


def _dataset_seal(cases: Sequence[AttributionCase]) -> dict[str, Any]:
    payload = {
        "schema": DATASET_SCHEMA,
        "cases": [case.sealed_manifest() for case in cases],
    }
    return {"payload": payload, "sha256": _sha256_json(payload)}


def _cost_usd(config: HarnessConfig, input_tokens: int, output_tokens: int) -> float:
    value = (
        input_tokens * config.input_cost_per_million_tokens
        + output_tokens * config.output_cost_per_million_tokens
    ) / 1_000_000
    return round(value, 10)


def _trial_evidence(
    request: ExecutionRequest,
    observation: ExecutionObservation,
    *,
    config: HarnessConfig,
    elapsed_ms: float,
) -> dict[str, Any]:
    available_ids = {item.artifact_id for item in request.available_artifacts}
    unknown_reads = set(observation.read_artifact_ids) - available_ids
    if unknown_reads:
        raise ValueError(
            f"execute callback reported reads unavailable in arm {request.arm}: {sorted(unknown_reads)}"
        )
    input_tokens = observation.input_tokens
    if input_tokens is None:
        input_tokens = estimate_tokens(request.case.query) + sum(
            item.estimated_tokens for item in request.available_artifacts
        )
    output_tokens = observation.output_tokens
    if output_tokens is None:
        output_tokens = estimate_tokens(observation.output)
    expected = set(request.case.expected_artifact_ids)
    reads = set(observation.read_artifact_ids)
    expected_read_coverage = len(reads & expected) / len(expected) if expected else 1.0
    passed = observation.score >= config.pass_threshold
    output_payload: dict[str, Any] = {
        "sha256": _sha256_text(observation.output),
        "utf8_bytes": len(observation.output.encode("utf-8")),
    }
    if config.preserve_raw_output:
        output_payload["text"] = observation.output
    return {
        "case_id": request.case.case_id,
        "capability": request.case.capability,
        "arm": request.arm,
        "trial_index": request.trial_index,
        "trial_seed": request.trial_seed,
        "score": observation.score,
        "passed": passed,
        "expected_read_coverage": round(expected_read_coverage, 6),
        "read_artifact_ids": list(observation.read_artifact_ids),
        "written_artifact_ids": list(observation.written_artifact_ids),
        "available_artifacts": [item.evidence_ref() for item in request.available_artifacts],
        "intervention": request.intervention,
        "output": output_payload,
        "cost": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated_usd": _cost_usd(config, input_tokens, output_tokens),
            "wall_ms": round(elapsed_ms, 3),
        },
        "metadata_evidence": _metadata_evidence(observation.metadata),
    }


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _summarize_case(
    case: AttributionCase,
    trials: Sequence[dict[str, Any]],
    *,
    config: HarnessConfig,
) -> dict[str, Any]:
    scores: dict[str, float] = {}
    pass_rates: dict[str, float] = {}
    for arm in config.arms:
        arm_trials = [item for item in trials if item["case_id"] == case.case_id and item["arm"] == arm]
        scores[arm] = round(_mean(item["score"] for item in arm_trials), 6)
        pass_rates[arm] = round(_mean(1.0 if item["passed"] else 0.0 for item in arm_trials), 6)

    on_score = scores.get("persistence_on", 0.0)
    off_score = scores.get("persistence_off", 0.0)
    placebo_score = scores.get("placebo", 0.0)
    control_upper_bound = max(off_score, placebo_score)
    effect = on_score - off_score
    pathway_effect = on_score - control_upper_bound
    on_trials = [
        item for item in trials if item["case_id"] == case.case_id and item["arm"] == "persistence_on"
    ]
    read_coverage = _mean(item["expected_read_coverage"] for item in on_trials)

    required_counterfactuals = ("delete", "replace", "corrupt")
    counterfactual_deltas = {
        arm: round(on_score - scores[arm], 6)
        for arm in required_counterfactuals
        if arm in scores
    }
    counterfactual_failures = [
        arm
        for arm in required_counterfactuals
        if arm not in scores
        or scores[arm] >= config.pass_threshold
        or on_score - scores[arm] < config.minimum_effect
    ]

    if off_score >= config.pass_threshold:
        verdict = "independent_of_persistence"
    elif on_score < config.pass_threshold:
        verdict = "no_headline_gain"
    elif read_coverage < config.minimum_expected_read_coverage:
        verdict = "headline_gain_without_pathway_evidence"
    elif pathway_effect < config.minimum_effect:
        verdict = "control_not_cleared"
    elif counterfactual_failures:
        verdict = "counterfactual_not_cleared"
    else:
        verdict = "pathway_supported"
    return {
        "case_id": case.case_id,
        "capability": case.capability,
        "scores": scores,
        "pass_rates": pass_rates,
        "persistence_gap": round(effect, 6),
        "control_upper_bound": round(control_upper_bound, 6),
        "pathway_effect": round(pathway_effect, 6),
        "persistence_on_expected_read_coverage": round(read_coverage, 6),
        "minimum_expected_read_coverage": config.minimum_expected_read_coverage,
        "counterfactual_deltas": counterfactual_deltas,
        "counterfactual_failures": counterfactual_failures,
        "verdict": verdict,
    }


def run_attribution_harness(
    cases: Sequence[AttributionCase],
    execute: ExecuteFn,
    *,
    config: HarnessConfig | None = None,
) -> dict[str, Any]:
    """Run matched arms and return a sealed, content-redacted evidence report."""

    active_config = config or HarnessConfig()
    if not cases:
        raise ValueError("at least one attribution case is required")
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case IDs must be unique")

    config_seal = active_config.sealed()
    dataset_seal = _dataset_seal(cases)
    trials: list[dict[str, Any]] = []
    run_started = time.monotonic()

    for trial_index in range(active_config.trials):
        seed = _trial_seed(active_config.seed, trial_index)
        for case in cases:
            for arm in active_config.arms:
                # Each arm gets a fresh RNG initialized with the same paired seed.
                # Arm-specific variability is derived deterministically without
                # changing the seed exposed to the model/runtime callback.
                arm_rng = random.Random(f"{seed}:{case.case_id}:{arm}")
                artifacts, intervention = _arm_artifacts(
                    case,
                    arm,
                    rng=arm_rng,
                    placebo_token_tolerance=active_config.placebo_token_tolerance,
                )
                request = ExecutionRequest(
                    case=case,
                    arm=arm,
                    trial_index=trial_index,
                    trial_seed=seed,
                    available_artifacts=artifacts,
                    intervention=intervention,
                )
                started = time.monotonic()
                observation = execute(request)
                elapsed_ms = (time.monotonic() - started) * 1000
                trials.append(
                    _trial_evidence(
                        request,
                        observation,
                        config=active_config,
                        elapsed_ms=elapsed_ms,
                    )
                )

    summaries = [_summarize_case(case, trials, config=active_config) for case in cases]
    total_input = sum(item["cost"]["input_tokens"] for item in trials)
    total_output = sum(item["cost"]["output_tokens"] for item in trials)
    experiment_id = _sha256_text(f"{config_seal['sha256']}:{dataset_seal['sha256']}")[:24]
    report: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "subject": active_config.subject,
        "config": config_seal,
        "dataset": dataset_seal,
        "experiment_id": experiment_id,
        "run_id": f"{experiment_id}-{uuid.uuid4().hex}",
        "matched_contract": {
            "harness_enforced": [
                "same_case_object_and_query_request_across_arms",
                "same_declared_grader_version_across_arms",
                "same_trial_seed_across_arms",
                "arm_specific_available_artifacts_and_intervention",
            ],
            "external_callback_attested": False,
            "not_observed_by_harness": [
                "actual_prompt_bytes",
                "actual_grader_implementation",
                "model_and_runtime_version",
                "tool_stack",
                "external_state",
                "whether_only_persistence_exposure_changed_inside_callback",
            ],
            "claim": "matched_requests_at_harness_boundary_not_end_to_end_runtime_equivalence",
            "causal_proof_claimed": False,
        },
        "summaries": summaries,
        "trials": trials,
        "cost": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "estimated_usd": _cost_usd(active_config, total_input, total_output),
            "wall_ms": round((time.monotonic() - run_started) * 1000, 3),
        },
    }
    report["evidence_sha256"] = _sha256_json(report)
    return report


def write_evidence_json(report: dict[str, Any], output_path: str | os.PathLike[str]) -> Path:
    """Atomically write evidence and verify the self-declared report hash."""

    payload = dict(report)
    declared = payload.pop("evidence_sha256", None)
    if not declared or declared != _sha256_json(payload):
        raise ValueError("evidence_sha256 does not match report bytes")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return target


def _demo_case() -> AttributionCase:
    return AttributionCase(
        case_id="demo-preference",
        capability="memory",
        query="¿Cuál es la bebida preferida de Valeria?",
        retained_artifacts=(
            RetainedArtifact(
                artifact_id="demo-memory-1",
                source="memories",
                content="La bebida preferida de Valeria es limonada.",
                replacement="La bebida preferida de Valeria era café.",
            ),
        ),
        expected_artifact_ids=("demo-memory-1",),
        placebo_artifacts=(
            RetainedArtifact(
                artifact_id="demo-placebo-1",
                source="memories",
                content="La estación preferida de Mateo es el invierno.",
            ),
        ),
    )


def _demo_execute(request: ExecutionRequest) -> ExecutionObservation:
    correct = next(
        (
            item
            for item in request.available_artifacts
            if item.artifact_id == "demo-memory-1" and "limonada" in item.content
        ),
        None,
    )
    if correct:
        return ExecutionObservation(
            score=1.0,
            output="Limonada.",
            read_artifact_ids=(correct.artifact_id,),
        )
    return ExecutionObservation(score=0.0, output="No lo sé.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated PAST-style attribution harness")
    parser.add_argument("--demo", action="store_true", help="run the synthetic offline canary")
    parser.add_argument("--seed", type=int, default=20260818)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--evidence", type=Path, help="write sealed evidence JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.demo:
        raise SystemExit("Only --demo is available; product adapters must be injected by a caller")
    report = run_attribution_harness(
        [_demo_case()],
        _demo_execute,
        config=HarnessConfig(seed=args.seed, trials=args.trials),
    )
    if args.evidence:
        write_evidence_json(report, args.evidence)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["summaries"][0]["verdict"] == "pathway_supported" else 2


if __name__ == "__main__":
    raise SystemExit(main())
