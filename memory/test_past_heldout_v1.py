from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from memory.past_attribution_harness import RetainedArtifact
from memory.past_heldout_v1_runner import (
    DEFAULT_DATASET,
    DEFAULT_PREREG,
    _load_json,
    _sha256_file,
    main,
    run_heldout,
)
from memory.past_live_adapter import load_live_dataset


def test_dataset_is_new_fully_synthetic_and_non_private() -> None:
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    custody = payload["custody"]
    assert custody == {
        "created_after_harness_commit": "b6d2eeaf3",
        "classification": "fully_synthetic",
        "contains_live_soul_memory": False,
        "contains_private_memory": False,
        "experimental_subject": "BENCH_PAST_HELDOUT_V1",
    }
    rendered = DEFAULT_DATASET.read_text(encoding="utf-8").casefold()
    for forbidden in ("william", "henry", "fable", "jarvis", "ada", "alice", "nexus"):
        assert re.search(rf"\b{forbidden}\b", rendered) is None
    cases = load_live_dataset(DEFAULT_DATASET)
    assert len(cases) == 8
    assert len({case.case_id for case in cases}) == 8
    assert {case.data_classification for case in cases} == {"synthetic_benchmark"}
    assert {case.agent for case in cases} == {"BENCH_PAST_HELDOUT_V1"}


def test_placebos_are_token_matched_before_results() -> None:
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    artifacts = payload["artifacts"]
    for case in payload["cases"]:
        target = sum(
            RetainedArtifact(memory_id, artifacts[memory_id]["source"], artifacts[memory_id]["content"]).estimated_tokens
            for memory_id in case["expected_memory_ids"]
        )
        placebo = sum(
            RetainedArtifact(memory_id, artifacts[memory_id]["source"], artifacts[memory_id]["content"]).estimated_tokens
            for memory_id in case["placebo_memory_ids"]
        )
        assert abs(placebo - target) / max(target, 1) <= 0.10


def test_preregister_is_frozen_and_bound_to_dataset_and_harness() -> None:
    prereg = json.loads(DEFAULT_PREREG.read_text(encoding="utf-8"))
    assert prereg["status"] == "frozen_before_results"
    assert prereg["dataset_sha256"] == _sha256_file(DEFAULT_DATASET)
    assert prereg["analysis_plan"]["seed"] == 380491
    assert prereg["analysis_plan"]["trials_per_case"] == 5
    assert prereg["analysis_plan"]["required_verdict"] == "pathway_supported"
    assert prereg["claim_policy"]["causal_proof_allowed"] is False


def test_frozen_heldout_executes_all_six_arms_and_controls(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    report = asyncio.run(run_heldout(evidence_path=evidence))
    assert evidence.is_file()
    assert report["heldout_assurance"]["case_count"] == 8
    assert report["heldout_assurance"]["trial_count"] == 240
    assert report["heldout_assurance"]["analysis_status"] == "preregistered_thresholds_met"
    assert {trial["arm"] for trial in report["trials"]} == {
        "persistence_on", "persistence_off", "placebo", "delete", "replace", "corrupt"
    }
    for summary in report["summaries"]:
        assert summary["verdict"] == "pathway_supported"
        assert summary["scores"] == {
            "persistence_on": 1.0,
            "persistence_off": 0.0,
            "placebo": 0.0,
            "delete": 0.0,
            "replace": 0.0,
            "corrupt": 0.0,
        }
    rendered = evidence.read_text(encoding="utf-8")
    dataset = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    for row in dataset["artifacts"].values():
        assert row["content"] not in rendered
    for case in dataset["cases"]:
        assert case["grader_payload"]["expected"] not in rendered


def test_dataset_byte_drift_fails_before_execution(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    payload["artifacts"]["h01"]["content"] = "CASE-H01 FACT::TAMPER-0Z"
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset hash mismatch"):
        asyncio.run(run_heldout(dataset_path=tampered, evidence_path=tmp_path / "no.json"))
    assert not (tmp_path / "no.json").exists()


def test_portable_wrapper_runs_from_unrelated_cwd(tmp_path: Path) -> None:
    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "run_past_heldout_v1.py"
    output = tmp_path / "portable-evidence.json"
    completed = subprocess.run(
        [sys.executable, str(wrapper), "--evidence", str(output)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"analysis_status": "preregistered_thresholds_met"' in completed.stdout
    assert output.is_file()


def test_json_root_must_be_an_object(tmp_path: Path) -> None:
    invalid = tmp_path / "list.json"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON root must be an object"):
        _load_json(invalid)


def test_main_cli_emits_bounded_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    output = tmp_path / "cli-evidence.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "past-heldout-v1",
            "--dataset",
            str(DEFAULT_DATASET),
            "--preregister",
            str(DEFAULT_PREREG),
            "--evidence",
            str(output),
        ],
    )
    assert main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "analysis_status": "preregistered_thresholds_met",
        "case_count": 8,
        "claim_boundary": (
            "synthetic deterministic harness-boundary attribution; "
            "not production causal proof"
        ),
        "evidence": str(output),
        "evidence_sha256": summary["evidence_sha256"],
        "ok": True,
        "trial_count": 240,
    }
    assert len(summary["evidence_sha256"]) == 64
    assert output.is_file()
