from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory.past_attribution_harness import (
    STANDARD_ARMS,
    AttributionCase,
    ExecutionObservation,
    HarnessConfig,
    RetainedArtifact,
    run_attribution_harness,
    write_evidence_json,
)


def _case() -> AttributionCase:
    return AttributionCase(
        case_id="preference-1",
        capability="memory",
        query="¿Qué bebida prefiere Valeria?",
        retained_artifacts=(
            RetainedArtifact(
                artifact_id="memory-1",
                source="memories",
                content="Valeria prefiere limonada fría.",
                replacement="Valeria antes prefería café.",
            ),
            RetainedArtifact(
                artifact_id="rule-1",
                source="rules",
                content="Responder en español.",
            ),
        ),
        expected_artifact_ids=("memory-1",),
        placebo_artifacts=(
            RetainedArtifact(
                artifact_id="placebo-1",
                source="memories",
                content="Mateo prefiere caminar en invierno.",
            ),
            RetainedArtifact(
                artifact_id="placebo-2",
                source="memories",
                content="La oficina tiene paredes verdes.",
            ),
        ),
    )


def _pathway_executor(request):
    correct = next(
        (
            item
            for item in request.available_artifacts
            if item.artifact_id == "memory-1" and "limonada" in item.content
        ),
        None,
    )
    if correct:
        return ExecutionObservation(
            score=1.0,
            output="Limonada fría.",
            read_artifact_ids=(correct.artifact_id,),
            input_tokens=20,
            output_tokens=3,
        )
    return ExecutionObservation(score=0.0, output="No lo sé.", input_tokens=10, output_tokens=3)


def test_runs_all_matched_arms_with_same_seed_per_trial() -> None:
    seen: list[tuple[int, str, int]] = []

    def execute(request):
        seen.append((request.trial_index, request.arm, request.trial_seed))
        return _pathway_executor(request)

    report = run_attribution_harness([_case()], execute, config=HarnessConfig(trials=2))

    assert len(report["trials"]) == 2 * len(STANDARD_ARMS)
    for trial_index in range(2):
        trial = [item for item in seen if item[0] == trial_index]
        assert {item[1] for item in trial} == set(STANDARD_ARMS)
        assert len({item[2] for item in trial}) == 1


def test_pathway_supported_when_only_valid_persistence_works() -> None:
    report = run_attribution_harness([_case()], _pathway_executor, config=HarnessConfig(trials=3))
    summary = report["summaries"][0]

    assert summary["scores"]["persistence_on"] == 1.0
    assert summary["scores"]["persistence_off"] == 0.0
    assert summary["scores"]["placebo"] == 0.0
    assert summary["scores"]["delete"] == 0.0
    assert summary["scores"]["replace"] == 0.0
    assert summary["scores"]["corrupt"] == 0.0
    assert summary["persistence_gap"] == 1.0
    assert summary["persistence_on_expected_read_coverage"] == 1.0
    assert summary["verdict"] == "pathway_supported"


def test_answer_that_succeeds_without_memory_is_not_attributed() -> None:
    def always_knows(_request):
        return ExecutionObservation(score=1.0, output="Limonada fría.")

    report = run_attribution_harness([_case()], always_knows, config=HarnessConfig(trials=1))

    assert report["summaries"][0]["verdict"] == "independent_of_persistence"
    assert report["summaries"][0]["persistence_gap"] == 0.0


def test_headline_gain_without_read_trace_is_explicit() -> None:
    def no_trace(request):
        score = 1.0 if request.arm == "persistence_on" else 0.0
        return ExecutionObservation(score=score, output="respuesta")

    report = run_attribution_harness([_case()], no_trace, config=HarnessConfig(trials=1))

    assert report["summaries"][0]["verdict"] == "headline_gain_without_pathway_evidence"


def test_counterfactual_must_degrade_before_pathway_is_supported() -> None:
    def counterfactual_survives(request):
        if request.arm in {"persistence_off", "placebo"}:
            return ExecutionObservation(score=0.0, output="No lo sé.")
        reads = ("memory-1",) if request.arm in {"persistence_on", "replace", "corrupt"} else ()
        return ExecutionObservation(score=1.0, output="Limonada fría.", read_artifact_ids=reads)

    report = run_attribution_harness(
        [_case()],
        counterfactual_survives,
        config=HarnessConfig(trials=1),
    )
    summary = report["summaries"][0]

    assert summary["verdict"] == "counterfactual_not_cleared"
    assert summary["counterfactual_failures"] == ["delete", "replace", "corrupt"]


def test_partial_expected_read_coverage_fails_strict_default() -> None:
    case = AttributionCase(
        case_id="two-artifacts",
        capability="procedural",
        query="Ejecuta el procedimiento acordado.",
        retained_artifacts=(
            RetainedArtifact("step-1", "procedures", "Primero validar el artefacto."),
            RetainedArtifact("step-2", "procedures", "Después promover el artefacto."),
        ),
        expected_artifact_ids=("step-1", "step-2"),
        placebo_artifacts=(
            RetainedArtifact("placebo", "procedures", "Primero regar y después podar las plantas."),
        ),
    )

    def partial_read(request):
        if request.arm == "persistence_on":
            return ExecutionObservation(
                score=1.0,
                output="Procedimiento ejecutado.",
                read_artifact_ids=("step-1",),
            )
        return ExecutionObservation(score=0.0, output="No lo sé.")

    report = run_attribution_harness([case], partial_read, config=HarnessConfig(trials=1))
    summary = report["summaries"][0]

    assert summary["persistence_on_expected_read_coverage"] == 0.5
    assert summary["minimum_expected_read_coverage"] == 1.0
    assert summary["verdict"] == "headline_gain_without_pathway_evidence"

    relaxed = run_attribution_harness(
        [case],
        partial_read,
        config=HarnessConfig(trials=1, minimum_expected_read_coverage=0.5),
    )
    assert relaxed["summaries"][0]["verdict"] == "pathway_supported"


def test_counterfactual_arms_record_hashed_mutations_without_raw_content() -> None:
    report = run_attribution_harness([_case()], _pathway_executor, config=HarnessConfig(trials=1))
    by_arm = {item["arm"]: item for item in report["trials"]}

    assert by_arm["delete"]["intervention"]["removed_ids"] == ["memory-1"]
    assert "memory-1" in by_arm["replace"]["intervention"]["replacement_sha256"]
    assert "memory-1" in by_arm["corrupt"]["intervention"]["character_positions"]
    rendered = json.dumps(report, ensure_ascii=False)
    assert "Valeria prefiere limonada fría" not in rendered
    assert "Valeria antes prefería café" not in rendered


def test_untrusted_case_and_observation_metadata_are_hash_only() -> None:
    secret_case = "RAW_CASE_METADATA_SENTINEL"
    secret_observation = "RAW_OBSERVATION_METADATA_SENTINEL"
    case = AttributionCase(
        case_id="metadata-redaction",
        capability="memory",
        query="consulta",
        retained_artifacts=(RetainedArtifact("memory-1", "memories", "dato"),),
        expected_artifact_ids=("memory-1",),
        placebo_artifacts=(RetainedArtifact("placebo-1", "memories", "otro"),),
        metadata={"raw": secret_case},
    )

    def execute(_request):
        return ExecutionObservation(
            score=0.0,
            output="respuesta",
            metadata={"raw": secret_observation},
        )

    report = run_attribution_harness([case], execute, config=HarnessConfig(trials=1))
    rendered = json.dumps(report, ensure_ascii=False)
    assert secret_case not in rendered
    assert secret_observation not in rendered
    assert set(report["dataset"]["payload"]["cases"][0]["payload"]["metadata_evidence"]) == {
        "sha256", "utf8_bytes", "field_count"
    }
    assert set(report["trials"][0]["metadata_evidence"]) == {
        "sha256", "utf8_bytes", "field_count"
    }


def test_placebo_is_deterministic_and_reports_token_budget() -> None:
    config = HarnessConfig(seed=44, trials=1)
    first = run_attribution_harness([_case()], _pathway_executor, config=config)
    second = run_attribution_harness([_case()], _pathway_executor, config=config)
    first_placebo = next(item for item in first["trials"] if item["arm"] == "placebo")
    second_placebo = next(item for item in second["trials"] if item["arm"] == "placebo")

    assert first_placebo["available_artifacts"] == second_placebo["available_artifacts"]
    assert first_placebo["intervention"] == second_placebo["intervention"]
    assert first_placebo["intervention"]["target_tokens"] > 0
    assert first_placebo["intervention"]["actual_tokens"] > 0
    assert first_placebo["intervention"]["relative_error"] <= 0.35


def test_placebo_arm_fails_closed_when_no_token_match_exists() -> None:
    case = AttributionCase(
        case_id="missing-placebo",
        capability="memory",
        query="consulta",
        retained_artifacts=(
            RetainedArtifact("memory-1", "memories", "dato retenido que debe recuperarse"),
        ),
        expected_artifact_ids=("memory-1",),
    )

    with pytest.raises(ValueError, match="has no placebo artifacts"):
        run_attribution_harness([case], _pathway_executor, config=HarnessConfig(trials=1))


def test_config_and_dataset_are_sealed_and_seed_sensitive() -> None:
    first = run_attribution_harness([_case()], _pathway_executor, config=HarnessConfig(seed=1, trials=1))
    second = run_attribution_harness([_case()], _pathway_executor, config=HarnessConfig(seed=2, trials=1))

    assert len(first["config"]["sha256"]) == 64
    assert len(first["dataset"]["sha256"]) == 64
    assert first["config"]["sha256"] != second["config"]["sha256"]
    assert first["dataset"]["sha256"] == second["dataset"]["sha256"]
    assert first["experiment_id"] != second["experiment_id"]
    assert first["run_id"] != second["run_id"]


def test_experiment_id_is_deterministic_but_run_id_is_unique() -> None:
    config = HarnessConfig(seed=7, trials=1)
    first = run_attribution_harness([_case()], _pathway_executor, config=config)
    second = run_attribution_harness([_case()], _pathway_executor, config=config)

    assert first["experiment_id"] == second["experiment_id"]
    assert first["run_id"] != second["run_id"]
    assert first["matched_contract"]["external_callback_attested"] is False
    assert "actual_prompt_bytes" in first["matched_contract"]["not_observed_by_harness"]


def test_costs_are_aggregated_from_observations() -> None:
    report = run_attribution_harness(
        [_case()],
        _pathway_executor,
        config=HarnessConfig(
            trials=1,
            input_cost_per_million_tokens=2.0,
            output_cost_per_million_tokens=4.0,
        ),
    )

    assert report["cost"]["input_tokens"] == 70
    assert report["cost"]["output_tokens"] == 18
    assert report["cost"]["total_tokens"] == 88
    assert report["cost"]["estimated_usd"] == pytest.approx((70 * 2 + 18 * 4) / 1_000_000)


def test_evidence_write_is_atomic_and_hash_verified(tmp_path: Path) -> None:
    report = run_attribution_harness([_case()], _pathway_executor, config=HarnessConfig(trials=1))
    target = write_evidence_json(report, tmp_path / "evidence.json")
    loaded = json.loads(target.read_text(encoding="utf-8"))

    assert loaded["schema"] == "seal.past-attribution.evidence.v1"
    assert loaded["evidence_sha256"] == report["evidence_sha256"]
    assert not list(tmp_path.glob("*.tmp"))

    loaded["subject"] = "tampered"
    with pytest.raises(ValueError, match="does not match"):
        write_evidence_json(loaded, tmp_path / "tampered.json")


def test_execute_cannot_claim_unavailable_reads() -> None:
    def impossible_read(_request):
        return ExecutionObservation(score=1.0, output="x", read_artifact_ids=("not-exposed",))

    with pytest.raises(ValueError, match="reported reads unavailable"):
        run_attribution_harness([_case()], impossible_read, config=HarnessConfig(trials=1))


def test_fable_cannot_be_used_as_experimental_subject() -> None:
    with pytest.raises(ValueError, match="independent control"):
        HarnessConfig(subject="FABLE")


def test_expected_artifact_ids_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        AttributionCase(
            case_id="empty-expected",
            capability="memory",
            query="consulta",
            retained_artifacts=(RetainedArtifact("memory-1", "memories", "dato"),),
            expected_artifact_ids=(),
            placebo_artifacts=(RetainedArtifact("placebo", "memories", "otro dato"),),
        )


def test_raw_output_is_redacted_by_default_and_opt_in() -> None:
    redacted = run_attribution_harness([_case()], _pathway_executor, config=HarnessConfig(trials=1))
    preserved = run_attribution_harness(
        [_case()],
        _pathway_executor,
        config=HarnessConfig(trials=1, preserve_raw_output=True),
    )

    assert "text" not in redacted["trials"][0]["output"]
    assert preserved["trials"][0]["output"]["text"] == "Limonada fría."
