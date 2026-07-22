from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools/skills/software-development/verify-true-green-tests/scripts/validate_evidence_manifest.py"
SPEC = importlib.util.spec_from_file_location("validate_evidence_manifest", MODULE_PATH)
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


def manifest(tmp_path: Path, **updates):
    now = datetime.now(timezone.utc)
    artifact = tmp_path / "test.log"
    artifact.write_text("4 passed\n", encoding="utf-8")
    os.utime(artifact, (now.timestamp(), now.timestamp()))
    data = {
        "schema": "soul-true-green-evidence-v1",
        "claim_id": "claim-1",
        "claim": "Feature works",
        "scope": ["feature.py"],
        "risk": "high",
        "revision": "abc123",
        "worktree_state": "declared",
        "builder": "ADA",
        "verifier": "NEXUS",
        "last_change_at": (now - timedelta(seconds=3)).isoformat(),
        "started_at": (now - timedelta(seconds=2)).isoformat(),
        "finished_at": (now + timedelta(seconds=2)).isoformat(),
        "status": "TRUE_GREEN",
        "checks": [{
            "id": "tests",
            "actor": "ADA",
            "argv": ["python3", "-m", "pytest"],
            "cwd": str(tmp_path),
            "started_at": (now - timedelta(seconds=1)).isoformat(),
            "finished_at": now.isoformat(),
            "exit_code": 0,
            "passed": 4,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "xfail": 0,
            "xpass": 0,
            "scenario_signatures": ["positive", "negative", "cleanup"],
            "artifacts": [artifact.name],
        }, {
            "id": "independent-probe",
            "actor": "NEXUS",
            "argv": ["python3", "probe.py"],
            "cwd": str(tmp_path),
            "started_at": (now - timedelta(milliseconds=900)).isoformat(),
            "finished_at": (now - timedelta(milliseconds=100)).isoformat(),
            "exit_code": 0,
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "xfail": 0,
            "xpass": 0,
            "scenario_signatures": ["independent-boundary-probe"],
            "artifacts": [artifact.name],
        }],
        "gates": [
            {"name": name, "status": "PASS", "evidence": ["independent-probe"] if name == "independence" else ["tests"]}
            if name in validator.HIGH_RISK_REQUIRED_PASS
            else {"name": name, "status": "N_A", "reason": f"The isolated Python feature scope does not create or modify a {name} surface"}
            for name in sorted(validator.REQUIRED_GATES)
        ],
        "residual_risks": [],
    }
    data.update(updates)
    return data, tmp_path / "evidence.json"


def test_true_green_manifest_passes(tmp_path: Path) -> None:
    data, path = manifest(tmp_path)
    result = validator.validate(data, path)
    assert result["valid"] is True
    assert result["classification"] == "TRUE_GREEN"


def test_builder_cannot_self_verify_high_risk_green(tmp_path: Path) -> None:
    data, path = manifest(tmp_path, verifier="ADA")
    result = validator.validate(data, path)
    assert result["valid"] is False
    assert "not-independent" in {issue["code"] for issue in result["issues"]}
    assert "false-green" in {issue["code"] for issue in result["issues"]}


def test_builder_verifier_aliases_do_not_count_as_independent(tmp_path: Path) -> None:
    for alias in ("ada", " ADA "):
        data, path = manifest(tmp_path, verifier=alias)
        data["checks"][1]["actor"] = alias
        result = validator.validate(data, path)
        assert result["valid"] is False
        assert "not-independent" in {issue["code"] for issue in result["issues"]}


def test_high_risk_cannot_mark_mandatory_gates_na(tmp_path: Path) -> None:
    data, path = manifest(tmp_path, risk="critical", claim="Production daemon security boundary works", scope=["example.service"])
    data["gates"] = [
        {"name": name, "status": "N_A", "reason": "Not applicable"}
        for name in sorted(validator.REQUIRED_GATES)
    ]
    result = validator.validate(data, path)
    codes = {issue["code"] for issue in result["issues"]}
    assert result["valid"] is False
    assert result["classification"] == "INDETERMINATE"
    assert "required-gate-not-pass" in codes
    assert "na-generic-reason" in codes


def test_high_risk_requires_verifier_run_check(tmp_path: Path) -> None:
    data, path = manifest(tmp_path)
    data["checks"][1]["actor"] = "ADA"
    result = validator.validate(data, path)
    assert result["valid"] is False
    assert "verifier-check-missing" in {issue["code"] for issue in result["issues"]}


def test_nonzero_exit_is_red_not_green(tmp_path: Path) -> None:
    data, path = manifest(tmp_path)
    data["checks"][0]["exit_code"] = 1
    result = validator.validate(data, path)
    assert result["classification"] == "RED"
    assert result["valid"] is False


def test_stale_check_is_indeterminate(tmp_path: Path) -> None:
    data, path = manifest(tmp_path)
    data["checks"][0]["started_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    result = validator.validate(data, path)
    assert result["classification"] == "INDETERMINATE"


def test_missing_artifact_fails_closed(tmp_path: Path) -> None:
    data, path = manifest(tmp_path)
    data["checks"][0]["artifacts"] = ["absent.log"]
    result = validator.validate(data, path)
    assert result["valid"] is False
    assert "artifact-missing" in {issue["code"] for issue in result["issues"]}


def test_unscoped_skip_blocks_true_green(tmp_path: Path) -> None:
    data, path = manifest(tmp_path)
    data["checks"][0]["skipped"] = 1
    result = validator.validate(data, path)
    assert result["classification"] == "YELLOW"
    assert result["valid"] is False


def test_duplicate_scenario_is_not_unique_coverage(tmp_path: Path) -> None:
    data, path = manifest(tmp_path)
    second = dict(data["checks"][0])
    second["id"] = "repeat"
    second["scenario_signatures"] = ["positive"]
    data["checks"].append(second)
    result = validator.validate(data, path)
    assert "duplicate-scenario" in {issue["code"] for issue in result["issues"]}
    assert result["classification"] == "YELLOW"


def test_secret_bearing_field_is_rejected(tmp_path: Path) -> None:
    data, path = manifest(tmp_path)
    data["session_token"] = "not-even-a-real-secret"
    result = validator.validate(data, path)
    assert "secret-field" in {issue["code"] for issue in result["issues"]}


def test_validator_never_executes_manifest_argv(tmp_path: Path) -> None:
    data, path = manifest(tmp_path)
    marker = tmp_path / "must-not-exist"
    data["checks"][0]["argv"] = ["touch", str(marker)]
    validator.validate(data, path)
    assert not marker.exists()
