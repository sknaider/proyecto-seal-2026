from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

import pytest

from quality_gate import gate


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "quality@example.invalid")
    _git(repo, "config", "user.name", "SEAL Quality Test")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "quality").mkdir()
    (repo / "src/app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    (repo / "tests/test_app.py").write_text("def test_answer():\n    assert 42 == 42\n", encoding="utf-8")
    (repo / "quality/patterns.json").write_text(json.dumps({
        "schema": gate.SCHEMA_PATTERNS,
        "patterns": [{
            "key": "create-true", "regex": r"create\s*=\s*True",
            "extensions": [".py"], "reason": "test",
            "bait": "patch(x, create=True)", "clean": "patch(x)",
        }],
    }), encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    return repo


def _policy() -> dict:
    return {
        "schema": gate.SCHEMA_POLICY,
        "required_qa_arms": sorted(gate.REQUIRED_ARMS),
        "independent_review_required": True,
        "source_suffixes": sorted(gate.SOURCE_SUFFIXES),
        "source_filenames": sorted(gate.SOURCE_FILENAMES),
        "known_defect_patterns": "quality/patterns.json",
        "critical_paths": ["memory/**"],
        "thresholds": {
            "coverage_floor_percent": 0.0,
            "critical_mutation_score_percent": 0.0,
            "committed_test_ratio_floor": 0.0,
        },
        "required_tracked_paths": ["src/app.py"],
        "manifests": ["quality/change.json"],
    }


def _manifest() -> dict:
    manifest = {
        "schema": gate.SCHEMA_MANIFEST,
        "owner": "ADA",
        "independent_reviewer": "FABLE",
        "review": {
            "status": "approved", "evidence": "adversarial-test",
            "receipt": {
                "schema": "seal.independent-review-receipt.v1",
                "reviewer": "FABLE", "evidence": "review-run-1",
                "reviewed_sha256": {
                    "src/app.py": hashlib.sha256(b"def answer():\n    return 42\n").hexdigest(),
                    "tests/test_app.py": hashlib.sha256(b"def test_answer():\n    assert 42 == 42\n").hexdigest(),
                },
            },
        },
        "deletions": [],
        "subjects": ["src/app.py"],
        "tests": ["tests/test_app.py"],
        "commands": [
            {"id": kind, "kind": kind, "argv": ["python3", "-m", "pytest", "-q", "tests/test_app.py", "-k", kind], "expected_exit": 0}
            for kind in sorted(gate.REQUIRED_ARMS)
        ],
        "coverage": {
            "source": ["src"], "omit": ["tests/*"],
            "pytest_args": ["-q", "tests/test_app.py"], "baseline_percent": 0.0,
        },
        "delivery": {
            "status": "verified", "evidence": "consumer observed result",
            "command": {
                "id": "effect", "argv": ["python3", "-m", "pytest", "-q", "tests/test_app.py"],
                "expected_exit": 0, "expected_stdout_contains": "passed",
            },
        },
    }
    manifest["review"]["receipt"]["manifest_digest"] = gate._review_digest(manifest)
    return manifest


def _refresh_review(manifest: dict) -> None:
    manifest["review"]["receipt"]["manifest_digest"] = gate._review_digest(manifest)


def _mutation_payload(repo: Path, manifest: dict, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "schema": gate.SCHEMA_MUTATION,
        "tool": "mutmut-test",
        "killed": 7,
        "survived": 2,
        "no_tests": 1,
        "skipped": 0,
        "suspicious": 0,
        "timeout": 0,
        "total": 10,
        "mutation_score_percent": 70.0,
        "reviewer": manifest["independent_reviewer"],
        "file_sha256": {
            raw: gate._sha256(repo / raw)
            for raw in [*manifest["subjects"], *manifest["tests"]]
        },
    }
    payload.update(overrides)
    return payload


def test_valid_manifest_passes_static_gate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = gate.verify_manifest(repo, _policy(), path, execute=False)
    assert result["ok"] is True
    assert result["status"] == "STATIC_OK"


def test_unindexed_subject_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src/untracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = _manifest()
    manifest["subjects"] = ["src/untracked.py"]
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = gate.verify_manifest(repo, _policy(), path, execute=False)
    assert result["ok"] is False
    assert "unindexed_subject:src/untracked.py" in result["errors"]


def test_owner_cannot_review_own_gate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["independent_reviewer"] = "ADA"
    manifest["review"]["receipt"]["reviewer"] = "ADA"
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = gate.verify_manifest(repo, _policy(), path, execute=False)
    assert result["ok"] is False
    assert "reviewer_must_differ_from_owner" in result["errors"]


def test_pending_review_is_not_green(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["review"]["status"] = "pending"
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = gate.verify_manifest(repo, _policy(), path, execute=False)
    assert result["ok"] is False
    assert "independent_review_pending" in result["errors"]


def test_subject_check_reports_untracked_and_missing_tests(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src/new.py").write_text("VALUE = 1\n", encoding="utf-8")
    policy = _policy()
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    result = gate.check_subject(repo, policy_path, "src/new.py")
    assert result["ok"] is False
    assert result["errors"] == [
        "subject_unindexed:src/new.py",
        "subject_without_quality_manifest:src/new.py",
        "subject_without_tests:src/new.py",
    ]


def test_command_runner_requires_expected_outcome(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    rows = gate._execute_commands(repo, [
        {"id": "positive", "kind": "qa_positive", "argv": ["python3", "-c", "raise SystemExit(3)"], "expected_exit": 0}
    ])
    assert len(rows) == 1
    assert rows[0].ok is False
    assert rows[0].observed_exit == 3


def test_unsafe_command_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(gate.QualityGateError, match="unsafe_quality_command"):
        gate._run(["rm", "anything"], repo)


def test_inventory_distinguishes_tracked_from_local_tests(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "tests/test_local.py").write_text("def test_local(): pass\n", encoding="utf-8")
    result = gate.inventory(repo)
    assert result["tests"] == {"total": 2, "indexed": 1, "committed": 1, "committed_ratio": 0.5}


def test_inventory_does_not_follow_external_symlink(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "tests/external_test.py").symlink_to(tmp_path / "outside_test.py")

    result = gate.inventory(repo)

    assert result["tests"] == {"total": 1, "indexed": 1, "committed": 1, "committed_ratio": 1.0}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# create=True", True),
        ("prefix\n  // create=True", True),
        ("first\nsecond\n * create=True", True),
        ("prefix\ncode create=True", False),
    ],
)
def test_known_defect_comment_detection_uses_current_line(text: str, expected: bool) -> None:
    assert gate._match_is_comment(text, text.index("create=True")) is expected


def test_staged_gate_rejects_code_without_manifest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy = _policy()
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    (repo / "src/new.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "src/new.py")

    result = gate.staged(repo, policy_path)

    assert result["ok"] is False
    assert "subject_without_quality_manifest:src/new.py" in result["errors"]
    assert "subject_without_tests:src/new.py" in result["errors"]


def test_staged_gate_accepts_non_code_change(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy = _policy()
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    (repo / "README.md").write_text("documentation only\n", encoding="utf-8")
    _git(repo, "add", "README.md")

    result = gate.staged(repo, policy_path)

    assert result["ok"] is True
    assert result["subjects"] == []


def test_mutation_evidence_score_must_derive_from_counts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["mutation_evidence"] = "quality/mutation.json"
    (repo / "quality/mutation.json").write_text(
        json.dumps(_mutation_payload(repo, manifest, mutation_score_percent=99.0)),
        encoding="utf-8",
    )

    with pytest.raises(gate.QualityGateError, match="mutation_score_not_derived"):
        gate._verify_mutation(repo, _policy(), manifest)


def test_mutation_evidence_is_bound_to_current_bytes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["mutation_evidence"] = "quality/mutation.json"
    payload = _mutation_payload(repo, manifest)
    payload["file_sha256"] = {"src/app.py": "stale"}
    (repo / "quality/mutation.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(gate.QualityGateError, match="mutation_evidence_stale"):
        gate._verify_mutation(repo, _policy(), manifest)


def test_mutation_audit_rejects_runtime_that_differs_from_baseline(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["mutation_evidence"] = "quality/mutation.json"
    _refresh_review(manifest)
    manifest_path = repo / "quality/change.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    evidence = _mutation_payload(repo, manifest)
    (repo / "quality/mutation.json").write_text(json.dumps(evidence), encoding="utf-8")
    stats = dict(evidence)
    stats.update({"killed": 6, "survived": 3})
    stats_path = repo / "quality/stats.json"
    stats_path.write_text(json.dumps(stats), encoding="utf-8")
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")

    result = gate.mutation_audit(repo, policy_path, manifest_path, stats_path)

    assert result["ok"] is False
    assert result["errors"][0].startswith("mutation_runtime_differs:")


def test_known_defect_detector_uses_positive_and_clean_controls(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src/bad.py").write_text("patch.object(mod, 'x', create=True)\n", encoding="utf-8")
    policy = _policy()
    payload = json.loads((repo / "quality/patterns.json").read_text(encoding="utf-8"))
    payload["patterns"][0].update({
        "regex": r"patch(?:\.object)?\([^)]*create\s*=\s*True",
        "bait": "patch.object(mod, 'x', create=True)",
        "clean": "patch.object(mod, 'x')",
    })
    (repo / "quality/patterns.json").write_text(json.dumps(payload), encoding="utf-8")

    errors = gate.scan_known_defects(repo, policy, ["src/bad.py"])

    assert errors == ["known_defect:create-true:src/bad.py:1"]


def test_dead_known_defect_detector_is_no_medible(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    payload = json.loads((repo / "quality/patterns.json").read_text(encoding="utf-8"))
    payload["patterns"][0]["bait"] = "clean input"
    (repo / "quality/patterns.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(gate.QualityGateError, match="known_defect_detector_dead"):
        gate.scan_known_defects(repo, _policy(), ["src/app.py"])


def test_staged_gate_covers_go_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    (repo / "src/main.go").write_text("package main\n", encoding="utf-8")
    _git(repo, "add", "src/main.go")

    result = gate.staged(repo, policy_path)

    assert result["ok"] is False
    assert "subject_without_quality_manifest:src/main.go" in result["errors"]


def test_staged_gate_rejects_unmanifested_deletion(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    (repo / "src/app.py").unlink()
    _git(repo, "add", "src/app.py")

    result = gate.staged(repo, policy_path)

    assert result["ok"] is False
    assert "deletion_without_quality_manifest:src/app.py" in result["errors"]


def test_policy_floor_cannot_be_lowered_below_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy_path = repo / "quality/policy.json"
    original = _policy()
    original["thresholds"]["coverage_floor_percent"] = 75.0
    policy_path.write_text(json.dumps(original), encoding="utf-8")
    _git(repo, "add", "quality/policy.json")
    _git(repo, "commit", "-qm", "policy baseline")
    lowered = _policy()
    lowered["thresholds"]["coverage_floor_percent"] = 10.0
    policy_path.write_text(json.dumps(lowered), encoding="utf-8")

    errors = gate._policy_ratchet_errors(repo, policy_path, lowered)

    assert errors == ["policy_ratchet_regression:coverage_floor_percent:10.0<75.0"]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("source_suffixes", [], "source_suffixes_incomplete"),
        ("source_filenames", [], "source_filenames_incomplete"),
        ("required_tracked_paths", [], "required_tracked_paths_empty"),
        ("manifests", [], "manifests_empty"),
        ("critical_paths", [], "critical_paths_empty"),
    ],
)
def test_policy_cannot_disable_detection_surfaces(tmp_path: Path, field: str, value: list, error: str) -> None:
    repo = _repo(tmp_path)
    policy = _policy()
    policy[field] = value
    path = repo / "quality/policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(gate.QualityGateError, match=error):
        gate.load_policy(path)


def test_diff_ratchet_compares_policy_to_requested_base(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy_path = repo / "quality/policy.json"
    original = _policy()
    original["thresholds"].update(
        coverage_floor_percent=75.0,
        critical_mutation_score_percent=60.0,
        committed_test_ratio_floor=0.5,
    )
    policy_path.write_text(json.dumps(original), encoding="utf-8")
    _git(repo, "add", "quality/policy.json")
    _git(repo, "commit", "-qm", "policy baseline")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    lowered = _policy()
    policy_path.write_text(json.dumps(lowered), encoding="utf-8")
    _git(repo, "add", "quality/policy.json")
    _git(repo, "commit", "-qm", "lower ratchets")

    result = gate.diff_gate(repo, policy_path, base)

    assert "policy_ratchet_regression:coverage_floor_percent:0.0<75.0" in result["errors"]
    assert "policy_ratchet_regression:critical_mutation_score_percent:0.0<60.0" in result["errors"]
    assert "policy_ratchet_regression:committed_test_ratio_floor:0.0<0.5" in result["errors"]


def test_diff_ratchet_checks_changed_manifest_against_requested_base(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    manifest = _manifest()
    manifest["coverage"]["baseline_percent"] = 80.0
    manifest["mutation_baseline_percent"] = 70.0
    _refresh_review(manifest)
    manifest_path = repo / "quality/change.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _git(repo, "add", "quality/policy.json", "quality/change.json")
    _git(repo, "commit", "-qm", "manifest baseline")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    manifest["coverage"]["baseline_percent"] = 10.0
    manifest["mutation_baseline_percent"] = 20.0
    _refresh_review(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _git(repo, "add", "quality/change.json")
    _git(repo, "commit", "-qm", "lower manifest ratchets")

    result = gate.diff_gate(repo, policy_path, base)
    errors = result["manifests"][0]["errors"]

    assert "manifest_ratchet_regression:baseline_percent:10.0<80.0" in errors
    assert "manifest_ratchet_regression:mutation_baseline_percent:20.0<70.0" in errors


def test_approved_review_requires_byte_bound_receipt_shape(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["review"].pop("receipt")
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = gate.verify_manifest(repo, _policy(), path, execute=False)

    assert "independent_review_receipt_required" in result["errors"]


def test_independent_review_receipt_rejects_changed_bytes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    (repo / "src/app.py").write_text("def answer():\n    return 7\n", encoding="utf-8")
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = gate.verify_manifest(repo, _policy(), path, execute=False)

    assert "independent_review_bytes_stale" in result["errors"]


def test_delivery_effect_is_mandatory(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest.pop("delivery")
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = gate.verify_manifest(repo, _policy(), path, execute=False)

    assert "delivery_effect_required" in result["errors"]


def test_delivery_effect_requires_observable_output_assertion(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["delivery"]["command"] = {"id": "vacuous", "argv": ["/bin/true"], "expected_exit": 0}
    _refresh_review(manifest)
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = gate.verify_manifest(repo, _policy(), path, execute=False)

    assert "delivery_effect_assertion_required" in result["errors"]


def test_qa_arms_must_execute_pytest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    for index, row in enumerate(manifest["commands"]):
        row["argv"] = ["/bin/true", f"tests/test_app.py:{index}"]
    _refresh_review(manifest)
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = gate.verify_manifest(repo, _policy(), path, execute=False)

    assert sum(error.startswith("qa_command_must_run_pytest:") for error in result["errors"]) == 4


def test_command_output_assertion_is_enforced(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    rows = gate._execute_commands(repo, [{
        "id": "effect", "kind": "delivery_effect", "argv": ["/bin/echo", "actual"],
        "expected_exit": 0, "expected_stdout_contains": "required-marker",
    }])

    assert rows[0].observed_exit == 0
    assert rows[0].ok is False


def test_quality_python_falls_back_after_missing_candidates(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.delenv("SEAL_QUALITY_PYTHON", raising=False)
    monkeypatch.setattr(gate.sys, "executable", "/missing/system-python")
    attempted: list[str] = []

    def fake_run(argv, _repo, timeout=180, env=None):
        attempted.append(argv[0])
        if argv[0] == "python3":
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise gate.QualityGateError("missing")

    monkeypatch.setattr(gate, "_run", fake_run)

    assert gate._quality_python(repo) == "python3"
    assert attempted[0] == "/missing/system-python"
    assert attempted[-1] == "python3"


def test_quality_python_fails_closed_without_tools(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("SEAL_QUALITY_PYTHON", "/same-python")
    monkeypatch.setattr(gate.sys, "executable", "/same-python")
    monkeypatch.setattr(
        gate,
        "_run",
        lambda argv, _repo, timeout=180, env=None: subprocess.CompletedProcess(argv, 1, "", "missing"),
    )

    with pytest.raises(gate.QualityGateError, match="quality_python_with_coverage_and_pytest_not_found"):
        gate._quality_python(repo)


def test_inline_qa_command_is_rejected_as_vacuous(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["commands"][0]["argv"] = ["python3", "-c", "raise SystemExit(0)"]
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = gate.verify_manifest(repo, _policy(), path, execute=False)

    assert any(error.startswith("inline_qa_command_forbidden") for error in result["errors"])


def test_verify_manifest_execute_runs_qa_coverage_and_delivery(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["commands"] = [
        {
            "id": kind, "kind": kind,
            "argv": ["python3", "-m", "pytest", "-q", "tests/test_app.py", "-k", f"answer and not missing_{kind}"],
            "expected_exit": 0,
        }
        for kind in sorted(gate.REQUIRED_ARMS)
    ]
    _refresh_review(manifest)
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = gate.verify_manifest(repo, _policy(), path, execute=True)

    assert result["ok"] is True
    assert len(result["commands"]) == 4
    assert result["delivery"][0]["ok"] is True
    assert result["coverage"]["tests_exit"] == 0


def test_diff_gate_checks_committed_change_against_base(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    _git(repo, "add", "quality/policy.json")
    _git(repo, "commit", "-qm", "policy")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    (repo / "src/main.go").write_text("package main\n", encoding="utf-8")
    _git(repo, "add", "src/main.go")
    _git(repo, "commit", "-qm", "unmanifested go")

    result = gate.diff_gate(repo, policy_path, base)

    assert result["ok"] is False
    assert "subject_without_quality_manifest:src/main.go" in result["errors"]


def test_hook_status_proves_effective_versioned_hook(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    hooks = repo / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\npython3 scripts/seal_quality_gate.py staged\n"
        "python3 scripts/seal_core_guard.py --pre-commit\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", ".githooks")

    result = gate.hook_status(repo)

    assert result["ok"] is True
    assert result["status"] == "ACTIVE"
    assert result["schema"] == "seal.quality-hook-status.v1"


def test_hook_status_rejects_default_hook_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".githooks").mkdir()
    hook = repo / ".githooks/pre-commit"
    hook.write_text("#!/bin/sh\n", encoding="utf-8")
    hook.chmod(0o755)

    result = gate.hook_status(repo)

    assert result["ok"] is False
    assert any(error.startswith("quality_hook_not_active") for error in result["errors"])


def test_policy_validation_errors_are_exact(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    broken = _policy()
    broken["schema"] = "wrong"
    broken["required_qa_arms"] = []
    broken["independent_review_required"] = False
    path.write_text(json.dumps(broken), encoding="utf-8")

    with pytest.raises(gate.QualityGateError) as caught:
        gate.load_policy(path)

    assert str(caught.value) == (
        "invalid_policy_schema;required_qa_arms_incomplete;independent_review_not_required"
    )


def test_policy_requires_all_numeric_thresholds(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    broken = _policy()
    broken["thresholds"] = {}
    path.write_text(json.dumps(broken), encoding="utf-8")

    with pytest.raises(gate.QualityGateError) as caught:
        gate.load_policy(path)

    assert str(caught.value) == (
        "missing_threshold:coverage_floor_percent;"
        "missing_threshold:critical_mutation_score_percent;"
        "missing_threshold:committed_test_ratio_floor"
    )


def test_pattern_contract_rejects_missing_path_and_extensions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(gate.QualityGateError, match="^known_defect_patterns_required$"):
        gate._load_patterns(repo, {"known_defect_patterns": ""})
    payload = json.loads((repo / "quality/patterns.json").read_text(encoding="utf-8"))
    payload["patterns"][0]["extensions"] = []
    (repo / "quality/patterns.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(gate.QualityGateError, match="^invalid_known_defect_extensions:create-true$"):
        gate._load_patterns(repo, _policy())


def test_inventory_counts_workflow_index_and_history(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    workflow = repo / ".github/workflows/quality.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: quality\n", encoding="utf-8")
    _git(repo, "add", ".github/workflows/quality.yml")

    staged = gate.inventory(repo)
    assert staged["workflows"] == {"total": 1, "indexed": 1, "committed": 0}

    _git(repo, "commit", "-qm", "workflow")
    committed = gate.inventory(repo)
    assert committed["workflows"] == {"total": 1, "indexed": 1, "committed": 1}


def test_static_manifest_validates_declared_deletion_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["deletions"] = ["src/app.py"]
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = gate.verify_manifest(repo, _policy(), path, execute=False)

    assert "declared_deletion_still_exists:src/app.py" in result["errors"]


def test_diff_gate_rejects_committed_deletion_without_manifest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    _git(repo, "add", "quality/policy.json")
    _git(repo, "commit", "-qm", "policy")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    (repo / "src/app.py").unlink()
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-qm", "delete source")

    result = gate.diff_gate(repo, policy_path, base)

    assert result["deletions"] == ["src/app.py"]
    assert "deletion_without_quality_manifest:src/app.py" in result["errors"]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda m: m.update(schema="wrong"), "invalid_manifest_schema"),
        (lambda m: m.update(owner=""), "owner_and_reviewer_required"),
        (lambda m: m.update(independent_reviewer="ADA"), "reviewer_must_differ_from_owner"),
        (lambda m: m["review"].update(status="pending"), "independent_review_pending"),
        (lambda m: m["review"].pop("receipt"), "independent_review_receipt_required"),
        (lambda m: m["review"]["receipt"].update(schema="wrong"), "invalid_independent_review_receipt"),
        (lambda m: m["review"]["receipt"].update(reviewer="ALICE"), "independent_review_receipt_reviewer_mismatch"),
        (lambda m: m["review"]["receipt"].update(evidence=""), "independent_review_receipt_evidence_required"),
        (lambda m: m["review"]["receipt"].update(manifest_digest="stale"), "independent_review_receipt_stale"),
        (lambda m: m.update(subjects=[]), "subjects_required"),
        (lambda m: m.update(tests=[]), "tests_required"),
        (lambda m: m.update(deletions="bad"), "deletions_not_list"),
        (lambda m: m.update(deletions=[123]), "invalid_deletion_path"),
        (lambda m: m.update(deletions=["src/never.py"]), "deletion_not_in_base_history:src/never.py"),
        (lambda m: m.update(commands="bad"), "commands_not_list"),
        (lambda m: m.update(commands=[]), "missing_qa_arm:unit"),
        (lambda m: m.update(coverage=None), "coverage_contract_required"),
        (lambda m: m.pop("delivery"), "delivery_effect_required"),
        (lambda m: m["delivery"].update(status="pending"), "delivery_effect_unverified"),
        (lambda m: m["delivery"].update(command=None), "delivery_effect_command_required"),
    ],
)
def test_static_manifest_contract_is_fail_closed(tmp_path: Path, mutate, expected: str) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    mutate(manifest)
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = gate._static_manifest_errors(repo, _policy(), manifest, path)

    assert expected in errors


def test_static_manifest_rejects_missing_and_unindexed_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["subjects"] = ["src/missing.py"]
    manifest["tests"] = ["tests/missing.py"]
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = gate._static_manifest_errors(repo, _policy(), manifest, path)

    assert "missing_subject:src/missing.py" in errors
    assert "unindexed_subject:src/missing.py" in errors
    assert "missing_test:tests/missing.py" in errors
    assert "unindexed_test:tests/missing.py" in errors


def test_static_manifest_requires_distinct_test_bound_qa_commands(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["commands"][0]["argv"] = ["python3", "-c", "pass"]
    manifest["commands"][1]["argv"] = list(manifest["commands"][2]["argv"])
    manifest["commands"][1]["id"] = manifest["commands"][2]["id"]
    path = repo / "quality/change.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = gate._static_manifest_errors(repo, _policy(), manifest, path)

    assert any(value.startswith("inline_qa_command_forbidden") for value in errors)
    assert any(value.startswith("qa_command_not_bound_to_test") for value in errors)
    assert "command_ids_must_be_unique" in errors
    assert "qa_arms_must_use_distinct_commands" in errors


def test_mutation_evidence_valid_result_and_all_failure_modes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["mutation_evidence"] = "quality/mutation.json"
    valid = _mutation_payload(repo, manifest)
    path = repo / "quality/mutation.json"
    path.write_text(json.dumps(valid), encoding="utf-8")

    assert gate._verify_mutation(repo, _policy(), manifest) == valid

    cases = [
        ({"schema": "wrong"}, "invalid_mutation_schema"),
        ({"total": 0}, "invalid_mutation_counts"),
        ({"killed": -1}, "invalid_mutation_counts"),
        ({"reviewer": "ALICE"}, "mutation_reviewer_mismatch"),
        ({"mutation_score_percent": 1.0}, "mutation_score_not_derived"),
    ]
    for override, expected in cases:
        payload = dict(valid)
        payload.update(override)
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(gate.QualityGateError, match=expected):
            gate._verify_mutation(repo, _policy(), manifest)


def test_mutation_floor_is_enforced(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["mutation_evidence"] = "quality/mutation.json"
    payload = _mutation_payload(repo, manifest, killed=1, survived=9, mutation_score_percent=10.0)
    (repo / "quality/mutation.json").write_text(json.dumps(payload), encoding="utf-8")
    policy = _policy()
    policy["thresholds"]["critical_mutation_score_percent"] = 20.0

    with pytest.raises(gate.QualityGateError, match="mutation_score_below_floor:10.0<20.0"):
        gate._verify_mutation(repo, policy, manifest)


def test_source_classifier_covers_declared_languages_and_names() -> None:
    assert gate._is_source_path("cmd/main.go") is True
    assert gate._is_source_path("src/lib.rs") is True
    assert gate._is_source_path("Dockerfile") is True
    assert gate._is_source_path("Makefile") is True
    assert gate._is_source_path("tests/test_main.py") is False
    assert gate._is_source_path("README.md") is False
    policy = {"source_suffixes": [".zig"], "source_filenames": ["Buildfile"]}
    assert gate._is_source_path("src/main.zig", policy) is True
    assert gate._is_source_path("Buildfile", policy) is True
    assert gate._is_source_path("src/main.go", policy) is False


def test_audit_passes_committed_manifest_and_reports_schema(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest_path = repo / "quality/change.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    policy = _policy()
    policy["manifests"] = ["quality/change.json"]
    policy["required_tracked_paths"] = ["src/app.py", "tests/test_app.py", "quality/change.json"]
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    _git(repo, "add", "quality/change.json", "quality/policy.json")
    _git(repo, "commit", "-qm", "quality contract")

    result = gate.audit(repo, policy_path, execute=False)

    assert result["schema"] == "seal.quality-audit.v1"
    assert result["ok"] is True
    assert result["status"] == "PASSED"
    assert result["errors"] == []
    assert result["manifests"][0]["status"] == "STATIC_OK"


def test_audit_rejects_committed_ratio_and_missing_required_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "tests/test_local.py").write_text("def test_local(): pass\n", encoding="utf-8")
    policy = _policy()
    policy["thresholds"]["committed_test_ratio_floor"] = 0.75
    policy["required_tracked_paths"] = ["missing.py"]
    policy_path = repo / "quality/policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    result = gate.audit(repo, policy_path, execute=False)

    assert "committed_test_ratio_regression:0.5<0.75" in result["errors"]
    assert "required_path_missing:missing.py" in result["errors"]


def test_cli_main_routes_actions_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    hooks = repo / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\npython3 scripts/seal_quality_gate.py staged\n"
        "python3 scripts/seal_core_guard.py --pre-commit\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", ".githooks")

    assert gate.main(["--repo", str(repo), "inventory"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "seal.quality-inventory.v1"
    assert gate.main(["--repo", str(repo), "hook-status"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ACTIVE"
    assert gate.main(["--repo", str(repo), "audit", "--policy", "missing.json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


def _minimal_spec(repo: Path, manifest: dict) -> dict:
    """Spec minimo que declara un mutante con ancla existente en el sujeto."""
    return {
        "schema": "seal.mutation-spec.v1",
        "mutants": [
            {
                "id": "T1-test-spec",
                "anchor": "def answer():",
                "replacement": "def answer():  # mutado",
                "esperado": "MUERTO",
            }
        ],
    }


def _minimal_evidence_with_spec(repo: Path, manifest: dict) -> dict:
    """Evidence que incluye el mutante del spec con ancla."""
    payload = _mutation_payload(repo, manifest)
    payload["mutants"] = [
        {
            "id": "T1-test-spec",
            "result": "KILLED",
            "ancla": "def answer():",
            "reemplazo": "def answer():  # mutado",
            "sujeto": "src/app.py",
            "observed": "1 failed",
        }
    ]
    return payload


def test_mutation_spec_ausente_es_error(tmp_path: Path) -> None:
    """D2: silenciar el error de spec ausente hace que un expediente con spec
    inexistente pase el gate. El test mata a D2."""
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["mutation_evidence"] = "quality/mutation.json"
    manifest["mutation_spec"] = "quality/mutation_spec.json"  # no existe en disco
    _refresh_review(manifest)
    payload = _mutation_payload(repo, manifest)
    (repo / "quality/mutation.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(gate.QualityGateError, match="mutation_spec_ausente"):
        gate._verify_mutation(repo, _policy(), manifest)


def test_mutation_spec_presente_activa_verificacion(tmp_path: Path) -> None:
    """D1: desactivar el bloque spec_raw hace que un expediente con spec cargado
    salte la verificacion completa. El test mata a D1 comprobando que se lanza
    el error cuando hay un mutante declarado en el spec pero no en el evidence."""
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["mutation_evidence"] = "quality/mutation.json"
    manifest["mutation_spec"] = "quality/mutation_spec.json"
    _refresh_review(manifest)
    spec = _minimal_spec(repo, manifest)
    (repo / "quality/mutation_spec.json").write_text(json.dumps(spec), encoding="utf-8")
    payload = _mutation_payload(repo, manifest)
    payload["mutants"] = []  # no hay mutantes corridos -> el spec declara uno no corrido
    (repo / "quality/mutation.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(gate.QualityGateError,
                       match="mutation_spec_mutante_incompleto|mutation_declarado_no_corrido|mutation_evidencia_sin_lista"):
        gate._verify_mutation(repo, _policy(), manifest)


def test_mutation_spec_errores_acumulan_en_gate_error(tmp_path: Path) -> None:
    """D3: si errors.extend se reemplaza por list(...) los errores del spec no
    llegan al QualityGateError. El test mata a D3 comprobando que el error
    del spec se propaga hacia afuera."""
    repo = _repo(tmp_path)
    manifest = _manifest()
    manifest["mutation_evidence"] = "quality/mutation.json"
    manifest["mutation_spec"] = "quality/mutation_spec.json"
    _refresh_review(manifest)
    spec = _minimal_spec(repo, manifest)
    (repo / "quality/mutation_spec.json").write_text(json.dumps(spec), encoding="utf-8")
    payload = _minimal_evidence_with_spec(repo, manifest)
    # Ancla del spec apunta a texto real del sujeto; el mutante esta corrido.
    # Cambiar el ancla del evidence para disparar discrepancia spec<->evidence:
    payload["mutants"][0]["ancla"] = "def respuesta():"  # ancla que no existe en el sujeto
    (repo / "quality/mutation.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(gate.QualityGateError) as exc_info:
        gate._verify_mutation(repo, _policy(), manifest)
    # El error del spec debe propagarse — si D3 sobrevive, no llega.
    assert "mutation_spec" in str(exc_info.value) or "ancla_no_aplica" in str(exc_info.value)


def test_parser_exposes_every_quality_action() -> None:
    parser = gate.build_parser()
    assert parser.parse_args(["inventory"]).action == "inventory"
    assert parser.parse_args(["audit"]).action == "audit"
    assert parser.parse_args(["verify", "quality/change.json"]).action == "verify"
    assert parser.parse_args(["check-subject", "src/app.py"]).action == "check-subject"
    assert parser.parse_args(["staged"]).action == "staged"
    assert parser.parse_args(["diff", "--base", "HEAD~1"]).action == "diff"
    assert parser.parse_args(["hook-status"]).action == "hook-status"
    assert parser.parse_args(["mutation-audit", "quality/change.json"]).action == "mutation-audit"
