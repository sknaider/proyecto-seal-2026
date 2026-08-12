#!/usr/bin/env python3
"""Golden quality gate for SEAL changes.

The gate is deliberately evidence-oriented: a declaration is never treated as
proof.  It validates tracked subjects/tests, executes four QA arms, measures
coverage, binds mutation evidence to current bytes, and requires an independent
reviewer.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_POLICY = "seal.quality-policy.v1"
SCHEMA_MANIFEST = "seal.quality-manifest.v1"
SCHEMA_MUTATION = "seal.mutation-evidence.v1"
SCHEMA_PATTERNS = "seal.known-defect-patterns.v1"
REQUIRED_ARMS = {"unit", "qa_positive", "qa_negative", "qa_control"}
SOURCE_SUFFIXES = {
    ".py", ".sh", ".bash", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
    ".java", ".c", ".h", ".cc", ".cpp", ".cs", ".rb", ".php", ".sql",
}
SOURCE_FILENAMES = {"Dockerfile", "Makefile", "Justfile"}
TEST_NAMES = ("test_", "_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
DEFAULT_EXCLUDES = (
    ".git/**", ".venv*/**", "**/.venv/**", "**/venv/**", "node_modules/**",
    "corpus/**", "research/**", "mattermost/**", "*-ref/**", "**/__pycache__/**",
    "mutants/**", "matrix/**", "data/**", "qdrant_storage/**", "external/**",
    "backups/**", "docs/_img_cache/**", "seal-console/**", "gstack-main/**",
    "gstack-repo/**", "dream-skill/**", "bolt-diy-fork/**", "roo-code-ref/**",
)
BLOCKED_EXECUTABLES = {"rm", "rmdir", "mkfs", "dd", "shutdown", "reboot"}


class QualityGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    command_id: str
    kind: str
    argv: list[str]
    expected_exit: int
    observed_exit: int
    stdout: str
    stderr: str
    expected_stdout_contains: str = ""

    @property
    def ok(self) -> bool:
        return self.expected_exit == self.observed_exit and (
            not self.expected_stdout_contains or self.expected_stdout_contains in self.stdout
        )


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _review_digest(manifest: dict[str, Any]) -> str:
    normalized = json.loads(json.dumps(manifest))
    review = normalized.get("review")
    if isinstance(review, dict):
        review.pop("receipt", None)
    return hashlib.sha256(_canonical(normalized)).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityGateError(f"cannot_read_json:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise QualityGateError(f"json_root_not_object:{path}")
    return value


def _run(argv: Sequence[str], repo: Path, timeout: int = 180, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    if not argv or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in argv):
        raise QualityGateError("invalid_argv")
    executable = Path(argv[0]).name.lower()
    joined = " ".join(argv).lower()
    if executable in BLOCKED_EXECUTABLES or any(
        token in joined for token in ("git reset --hard", "git clean -f", "drop table", "truncate table")
    ):
        raise QualityGateError(f"unsafe_quality_command:{joined}")
    try:
        return subprocess.run(
            list(argv), cwd=repo, text=True, capture_output=True, check=False,
            timeout=timeout, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QualityGateError(f"command_failed_to_run:{argv[0]}:{exc}") from exc


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], repo, timeout=60)


def _repo_path(repo: Path, raw: str) -> Path:
    candidate = (repo / raw).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError as exc:
        raise QualityGateError(f"path_outside_repo:{raw}") from exc
    return candidate


def _git_object_exists(repo: Path, spec: str) -> bool:
    return _git(repo, "cat-file", "-e", spec).returncode == 0


def _is_indexed(repo: Path, raw: str) -> bool:
    return _git_object_exists(repo, f":{raw}")


def _is_committed(repo: Path, raw: str) -> bool:
    return _git_object_exists(repo, f"HEAD:{raw}")


def _is_excluded(raw: str, excludes: Iterable[str] = DEFAULT_EXCLUDES) -> bool:
    return any(fnmatch.fnmatch(raw, pattern) for pattern in excludes)


def _is_owned_regular_file(repo: Path, raw: str) -> bool:
    # Inventory must never follow a repository symlink into the host. Git can
    # version the link itself, but the quality gate audits only regular files.
    lexical = repo / raw
    if lexical.is_symlink():
        return False
    return _repo_path(repo, raw).is_file()


def _is_test_path(raw: str) -> bool:
    name = Path(raw).name
    return (
        name.startswith("test_") and name.endswith(".py")
        or name.endswith("_test.py")
        or any(name.endswith(suffix) for suffix in TEST_NAMES[2:])
    )


def _is_source_path(raw: str, policy: dict[str, Any] | None = None) -> bool:
    suffixes = set(policy.get("source_suffixes", SOURCE_SUFFIXES)) if policy else SOURCE_SUFFIXES
    names = set(policy.get("source_filenames", SOURCE_FILENAMES)) if policy else SOURCE_FILENAMES
    return (Path(raw).suffix in suffixes or Path(raw).name in names) and not _is_test_path(raw)


def _owned_files(repo: Path) -> list[str]:
    # HEAD is the history source of truth. Porcelain adds current/staged files
    # without pretending that the index is already committed history.
    committed = _git(repo, "ls-tree", "-r", "--name-only", "HEAD")
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if committed.returncode != 0 or status.returncode != 0:
        raise QualityGateError("git_inventory_failed")
    rows = set(committed.stdout.splitlines())
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        raw = line[3:]
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        rows.add(raw.strip('"'))
    return sorted(
        raw for raw in rows
        if raw and not _is_excluded(raw) and _is_owned_regular_file(repo, raw)
    )


def inventory(repo: Path) -> dict[str, Any]:
    files = _owned_files(repo)
    tests = [path for path in files if _is_test_path(path)]
    sources = [path for path in files if _is_source_path(path)]
    indexed_tests = [path for path in tests if _is_indexed(repo, path)]
    committed_tests = [path for path in tests if _is_committed(repo, path)]
    indexed_sources = [path for path in sources if _is_indexed(repo, path)]
    committed_sources = [path for path in sources if _is_committed(repo, path)]
    workflows = [path for path in files if path.startswith(".github/workflows/")]
    indexed_workflows = [path for path in workflows if _is_indexed(repo, path)]
    committed_workflows = [path for path in workflows if _is_committed(repo, path)]
    return {
        "schema": "seal.quality-inventory.v1",
        "sources": {"total": len(sources), "indexed": len(indexed_sources), "committed": len(committed_sources)},
        "tests": {
            "total": len(tests),
            "indexed": len(indexed_tests),
            "committed": len(committed_tests),
            "committed_ratio": round(len(committed_tests) / len(tests), 6) if tests else 1.0,
        },
        "workflows": {"total": len(workflows), "indexed": len(indexed_workflows), "committed": len(committed_workflows)},
    }


def load_policy(path: Path) -> dict[str, Any]:
    policy = _read_json(path)
    errors: list[str] = []
    if policy.get("schema") != SCHEMA_POLICY:
        errors.append("invalid_policy_schema")
    if set(policy.get("required_qa_arms", [])) != REQUIRED_ARMS:
        errors.append("required_qa_arms_incomplete")
    thresholds = policy.get("thresholds", {})
    for key in ("coverage_floor_percent", "critical_mutation_score_percent", "committed_test_ratio_floor"):
        if not isinstance(thresholds.get(key), (int, float)):
            errors.append(f"missing_threshold:{key}")
    if policy.get("independent_review_required") is not True:
        errors.append("independent_review_not_required")
    suffixes = policy.get("source_suffixes")
    if not isinstance(suffixes, list) or not all(isinstance(row, str) for row in suffixes) or not SOURCE_SUFFIXES.issubset(set(suffixes)):
        errors.append("source_suffixes_incomplete")
    filenames = policy.get("source_filenames")
    if not isinstance(filenames, list) or not all(isinstance(row, str) for row in filenames) or not SOURCE_FILENAMES.issubset(set(filenames)):
        errors.append("source_filenames_incomplete")
    required_paths = policy.get("required_tracked_paths")
    if not isinstance(required_paths, list) or not required_paths:
        errors.append("required_tracked_paths_empty")
    manifests = policy.get("manifests")
    if not isinstance(manifests, list) or not manifests or not all(isinstance(row, str) and row for row in manifests):
        errors.append("manifests_empty")
    if not isinstance(policy.get("critical_paths"), list) or not policy.get("critical_paths"):
        errors.append("critical_paths_empty")
    if not isinstance(policy.get("known_defect_patterns"), str) or not policy.get("known_defect_patterns"):
        errors.append("known_defect_patterns_required")
    if errors:
        raise QualityGateError(";".join(errors))
    return policy


def _git_json(repo: Path, spec: str) -> dict[str, Any] | None:
    proc = _git(repo, "show", spec)
    if proc.returncode != 0:
        return None
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise QualityGateError(f"invalid_historical_json:{spec}:{exc}") from exc
    if not isinstance(value, dict):
        raise QualityGateError(f"historical_json_root_not_object:{spec}")
    return value


def _policy_ratchet_errors(repo: Path, policy_path: Path, policy: dict[str, Any], base: str = "HEAD") -> list[str]:
    try:
        raw = str(policy_path.resolve().relative_to(repo.resolve()))
    except ValueError:
        return ["policy_outside_repo"]
    previous = _git_json(repo, f"{base}:{raw}")
    if previous is None:
        return []
    errors: list[str] = []
    current_thresholds = policy.get("thresholds", {})
    previous_thresholds = previous.get("thresholds", {})
    for key in ("coverage_floor_percent", "critical_mutation_score_percent", "committed_test_ratio_floor"):
        old = previous_thresholds.get(key)
        new = current_thresholds.get(key)
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) and float(new) < float(old):
            errors.append(f"policy_ratchet_regression:{key}:{new}<{old}")
    for key in ("manifests", "required_tracked_paths", "critical_paths"):
        old_rows = previous.get(key, [])
        new_rows = policy.get(key, [])
        if not isinstance(old_rows, list) or not isinstance(new_rows, list):
            continue
        for removed in sorted(set(old_rows) - set(new_rows)):
            errors.append(f"policy_ratchet_removed:{key}:{removed}")
    return errors


def _manifest_ratchet_errors(repo: Path, path: Path, manifest: dict[str, Any], base: str = "HEAD") -> list[str]:
    try:
        raw = str(path.resolve().relative_to(repo.resolve()))
    except ValueError:
        return ["manifest_outside_repo"]
    previous = _git_json(repo, f"{base}:{raw}")
    if previous is None:
        return []
    errors: list[str] = []
    pairs = (
        ("coverage", "baseline_percent"),
        (None, "mutation_baseline_percent"),
    )
    for parent, key in pairs:
        old_container = previous.get(parent, {}) if parent else previous
        new_container = manifest.get(parent, {}) if parent else manifest
        old = old_container.get(key) if isinstance(old_container, dict) else None
        new = new_container.get(key) if isinstance(new_container, dict) else None
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) and float(new) < float(old):
            errors.append(f"manifest_ratchet_regression:{key}:{new}<{old}")
    return errors


def _load_patterns(repo: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    raw = policy.get("known_defect_patterns")
    if not isinstance(raw, str) or not raw:
        raise QualityGateError("known_defect_patterns_required")
    payload = _read_json(_repo_path(repo, raw))
    if payload.get("schema") != SCHEMA_PATTERNS or not isinstance(payload.get("patterns"), list):
        raise QualityGateError("invalid_known_defect_patterns")
    patterns: list[dict[str, Any]] = []
    for index, row in enumerate(payload["patterns"]):
        if not isinstance(row, dict):
            raise QualityGateError(f"known_defect_pattern_not_object:{index}")
        try:
            regex = re.compile(str(row["regex"]), re.MULTILINE)
            key = str(row["key"])
            bait = str(row["bait"])
            clean = str(row["clean"])
            extensions = row["extensions"]
        except (KeyError, re.error) as exc:
            raise QualityGateError(f"invalid_known_defect_pattern:{index}:{exc}") from exc
        if not isinstance(extensions, list) or not extensions:
            raise QualityGateError(f"invalid_known_defect_extensions:{key}")
        if not regex.search(bait):
            raise QualityGateError(f"known_defect_detector_dead:{key}")
        if regex.search(clean):
            raise QualityGateError(f"known_defect_false_positive:{key}")
        patterns.append(row)
    return patterns


def _match_is_comment(text: str, offset: int) -> bool:
    start = text.rfind("\n", 0, offset) + 1
    prefix = text[start:offset]
    stripped = prefix.lstrip()
    return stripped.startswith(("#", "//", "*"))


def scan_known_defects(repo: Path, policy: dict[str, Any], paths: Iterable[str]) -> list[str]:
    patterns = _load_patterns(repo, policy)
    excluded = str(policy["known_defect_patterns"])
    errors: list[str] = []
    for raw in sorted(set(paths)):
        path = _repo_path(repo, raw)
        if raw == excluded or not path.is_file():
            continue
        text_value = path.read_text(encoding="utf-8", errors="ignore")
        for row in patterns:
            if path.suffix not in row["extensions"]:
                continue
            regex = re.compile(str(row["regex"]), re.MULTILINE)
            for match in regex.finditer(text_value):
                if _match_is_comment(text_value, match.start()):
                    continue
                line = text_value[:match.start()].count("\n") + 1
                errors.append(f"known_defect:{row['key']}:{raw}:{line}")
    return errors


def _critical_subjects(policy: dict[str, Any], subjects: Iterable[str]) -> list[str]:
    patterns = policy.get("critical_paths", [])
    return sorted(path for path in subjects if any(fnmatch.fnmatch(path, pattern) for pattern in patterns))


def _is_pytest_command(argv: Sequence[str]) -> bool:
    executable = Path(argv[0]).name if argv else ""
    return executable in {"python", "python3", "python3.12"} and list(argv[1:3]) == ["-m", "pytest"]


def _static_manifest_errors(
    repo: Path,
    policy: dict[str, Any],
    manifest: dict[str, Any],
    manifest_path: Path | None = None,
    ratchet_base: str = "HEAD",
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != SCHEMA_MANIFEST:
        errors.append("invalid_manifest_schema")
    owner = str(manifest.get("owner", "")).strip().upper()
    reviewer = str(manifest.get("independent_reviewer", "")).strip().upper()
    if not owner or not reviewer:
        errors.append("owner_and_reviewer_required")
    elif owner == reviewer:
        errors.append("reviewer_must_differ_from_owner")
    review = manifest.get("review", {})
    if not isinstance(review, dict):
        errors.append("invalid_review_contract")
        review = {}
    if review.get("status") != "approved":
        errors.append("independent_review_pending")
    elif not isinstance(review.get("receipt"), dict):
        errors.append("independent_review_receipt_required")
    else:
        receipt = review["receipt"]
        if receipt.get("schema") != "seal.independent-review-receipt.v1":
            errors.append("invalid_independent_review_receipt")
        if str(receipt.get("reviewer", "")).strip().upper() != reviewer:
            errors.append("independent_review_receipt_reviewer_mismatch")
        if not str(receipt.get("evidence", "")).strip():
            errors.append("independent_review_receipt_evidence_required")
        if receipt.get("manifest_digest") != _review_digest(manifest):
            errors.append("independent_review_receipt_stale")
    subjects = manifest.get("subjects", [])
    tests = manifest.get("tests", [])
    if not isinstance(subjects, list) or not subjects:
        errors.append("subjects_required")
        subjects = []
    if not isinstance(tests, list) or not tests:
        errors.append("tests_required")
        tests = []
    for label, rows in (("subject", subjects), ("test", tests)):
        for raw in rows:
            if not isinstance(raw, str):
                errors.append(f"invalid_{label}_path")
                continue
            path = _repo_path(repo, raw)
            if not path.is_file():
                errors.append(f"missing_{label}:{raw}")
            if not _is_indexed(repo, raw):
                errors.append(f"unindexed_{label}:{raw}")
    if review.get("status") == "approved" and isinstance(review.get("receipt"), dict):
        manifest_rel = ""
        if manifest_path is not None:
            try:
                manifest_rel = str(manifest_path.resolve().relative_to(repo.resolve()))
            except ValueError:
                manifest_rel = ""
        expected_hashes = {
            raw: _sha256(_repo_path(repo, raw))
            for raw in [*subjects, *tests]
            if isinstance(raw, str) and raw != manifest_rel and _repo_path(repo, raw).is_file()
        }
        if review["receipt"].get("reviewed_sha256") != expected_hashes:
            errors.append("independent_review_bytes_stale")
    deletions = manifest.get("deletions", [])
    if not isinstance(deletions, list):
        errors.append("deletions_not_list")
        deletions = []
    for raw in deletions:
        if not isinstance(raw, str):
            errors.append("invalid_deletion_path")
        elif _repo_path(repo, raw).exists():
            errors.append(f"declared_deletion_still_exists:{raw}")
        elif not _is_committed(repo, raw):
            errors.append(f"deletion_not_in_base_history:{raw}")
    commands = manifest.get("commands", [])
    if not isinstance(commands, list):
        errors.append("commands_not_list")
        commands = []
    kinds = {row.get("kind") for row in commands if isinstance(row, dict)}
    for missing in sorted(REQUIRED_ARMS - kinds):
        errors.append(f"missing_qa_arm:{missing}")
    command_ids: list[str] = []
    arm_argv: list[tuple[str, ...]] = []
    for row in commands:
        if not isinstance(row, dict):
            continue
        command_ids.append(str(row.get("id", "")))
        kind = row.get("kind")
        argv = row.get("argv")
        if kind in REQUIRED_ARMS and isinstance(argv, list):
            normalized = tuple(str(value) for value in argv)
            arm_argv.append(normalized)
            if not _is_pytest_command(normalized):
                errors.append(f"qa_command_must_run_pytest:{row.get('id', '')}")
            if "-c" in normalized or "-e" in normalized:
                errors.append(f"inline_qa_command_forbidden:{row.get('id', '')}")
            if not any(test in " ".join(normalized) for test in tests if isinstance(test, str)):
                errors.append(f"qa_command_not_bound_to_test:{row.get('id', '')}")
    if len(command_ids) != len(set(command_ids)) or any(not value for value in command_ids):
        errors.append("command_ids_must_be_unique")
    if len(arm_argv) != len(set(arm_argv)):
        errors.append("qa_arms_must_use_distinct_commands")
    coverage_contract = manifest.get("coverage")
    if not isinstance(coverage_contract, dict):
        errors.append("coverage_contract_required")
    elif not isinstance(coverage_contract.get("omit"), list) or not coverage_contract.get("omit"):
        errors.append("coverage_test_omit_required")
    critical = _critical_subjects(policy, [row for row in subjects if isinstance(row, str)])
    if critical and not isinstance(manifest.get("mutation_evidence"), str):
        errors.append("mutation_evidence_required_for_critical_subject")
    delivery = manifest.get("delivery")
    if not isinstance(delivery, dict):
        errors.append("delivery_effect_required")
    else:
        if delivery.get("status") != "verified" or not str(delivery.get("evidence", "")).strip():
            errors.append("delivery_effect_unverified")
        command = delivery.get("command")
        if not isinstance(command, dict) or not isinstance(command.get("argv"), list):
            errors.append("delivery_effect_command_required")
        elif not isinstance(command.get("expected_stdout_contains"), str) or not command.get("expected_stdout_contains"):
            errors.append("delivery_effect_assertion_required")
    if manifest_path is not None:
        errors.extend(_manifest_ratchet_errors(repo, manifest_path, manifest, ratchet_base))
    return errors


def _execute_commands(repo: Path, rows: list[dict[str, Any]]) -> list[CommandResult]:
    results: list[CommandResult] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise QualityGateError(f"command_not_object:{index}")
        command_id = str(row.get("id", f"command-{index}"))
        kind = str(row.get("kind", ""))
        argv = row.get("argv")
        if not isinstance(argv, list):
            raise QualityGateError(f"command_argv_not_list:{command_id}")
        expected = int(row.get("expected_exit", 0))
        proc = _run(argv, repo, timeout=int(row.get("timeout", 180)))
        results.append(CommandResult(
            command_id=command_id, kind=kind, argv=list(argv), expected_exit=expected,
            observed_exit=proc.returncode, stdout=proc.stdout[-20000:], stderr=proc.stderr[-20000:],
            expected_stdout_contains=str(row.get("expected_stdout_contains", "")),
        ))
    return results


def _quality_python(repo: Path) -> str:
    candidates = [
        os.environ.get("SEAL_QUALITY_PYTHON", ""),
        sys.executable,
        str(repo / ".venv-quality/bin/python"),
        str(Path(__file__).resolve().parent.parent / ".venv-quality/bin/python"),
        "python3",
    ]
    checked: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in checked:
            continue
        checked.add(candidate)
        try:
            proc = _run([candidate, "-c", "import coverage, pytest"], repo, timeout=15)
        except QualityGateError:
            continue
        if proc.returncode == 0:
            return candidate
    raise QualityGateError("quality_python_with_coverage_and_pytest_not_found")


def _measure_coverage(repo: Path, coverage_spec: dict[str, Any]) -> dict[str, Any]:
    pytest_args = coverage_spec.get("pytest_args")
    source = coverage_spec.get("source")
    omit = coverage_spec.get("omit")
    if (
        not isinstance(pytest_args, list) or not pytest_args
        or not isinstance(source, list) or not source
        or not isinstance(omit, list) or not omit
    ):
        raise QualityGateError("invalid_coverage_contract")
    with tempfile.TemporaryDirectory(prefix="seal-quality-coverage-") as temporary:
        data_file = Path(temporary) / ".coverage"
        report_file = Path(temporary) / "coverage.json"
        env = dict(os.environ)
        env["COVERAGE_FILE"] = str(data_file)
        quality_python = _quality_python(repo)
        omit_arg = f"--omit={','.join(str(row) for row in omit)}"
        run_argv = [
            quality_python, "-m", "coverage", "run", "--branch",
            f"--source={','.join(source)}", omit_arg, "-m", "pytest", *pytest_args,
        ]
        proc = _run(run_argv, repo, timeout=int(coverage_spec.get("timeout", 300)), env=env)
        if proc.returncode != 0:
            raise QualityGateError(f"coverage_tests_failed:{proc.returncode}:{proc.stdout[-1000:]}:{proc.stderr[-1000:]}")
        json_proc = _run(
            [quality_python, "-m", "coverage", "json", omit_arg, "-o", str(report_file)], repo, env=env,
        )
        if json_proc.returncode != 0 or not report_file.is_file():
            raise QualityGateError(f"coverage_report_failed:{json_proc.stderr[-1000:]}")
        payload = _read_json(report_file)
    percent = float(payload.get("totals", {}).get("percent_covered", -1))
    return {
        "percent": round(percent, 3),
        "covered_lines": int(payload.get("totals", {}).get("covered_lines", 0)),
        "num_statements": int(payload.get("totals", {}).get("num_statements", 0)),
        "tests_exit": proc.returncode,
    }


def _verify_mutation(repo: Path, policy: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any] | None:
    raw = manifest.get("mutation_evidence")
    if not raw:
        return None
    path = _repo_path(repo, str(raw))
    payload = _read_json(path)
    errors: list[str] = []
    if payload.get("schema") != SCHEMA_MUTATION:
        errors.append("invalid_mutation_schema")
    count_keys = ("killed", "survived", "no_tests", "skipped", "suspicious", "timeout")
    counts = {key: int(payload.get(key, 0)) for key in count_keys}
    total = int(payload.get("total", -1))
    killed = counts["killed"]
    score = float(payload.get("mutation_score_percent", -1))
    calculated = round((killed / total) * 100, 3) if total > 0 else -1
    if total <= 0 or any(value < 0 for value in counts.values()) or sum(counts.values()) != total:
        errors.append("invalid_mutation_counts")
    if abs(score - calculated) > 0.0001:
        errors.append(f"mutation_score_not_derived:{score}!={calculated}")
    floor = max(
        float(policy["thresholds"]["critical_mutation_score_percent"]),
        float(manifest.get("mutation_baseline_percent", 0)),
    )
    if score + 1e-9 < floor:
        errors.append(f"mutation_score_below_floor:{score}<{floor}")
    current: dict[str, str] = {}
    for raw_path in [*manifest.get("subjects", []), *manifest.get("tests", [])]:
        current[raw_path] = _sha256(_repo_path(repo, raw_path))
    if payload.get("file_sha256") != current:
        errors.append("mutation_evidence_stale")
    if payload.get("reviewer") != manifest.get("independent_reviewer"):
        errors.append("mutation_reviewer_mismatch")
    if errors:
        raise QualityGateError(";".join(errors))
    return payload


def mutation_audit(repo: Path, policy_path: Path, manifest_path: Path, stats_path: Path) -> dict[str, Any]:
    """Compare a fresh mutmut run with the byte-bound, reviewed baseline."""
    policy = load_policy(policy_path)
    manifest = _read_json(manifest_path)
    static_errors = _static_manifest_errors(repo, policy, manifest, manifest_path)
    if static_errors:
        return {"ok": False, "status": "REJECTED", "errors": static_errors}
    evidence = _verify_mutation(repo, policy, manifest)
    if evidence is None:
        return {"ok": False, "status": "REJECTED", "errors": ["mutation_evidence_missing"]}
    stats = _read_json(stats_path)
    count_keys = ("killed", "survived", "no_tests", "skipped", "suspicious", "timeout", "total")
    observed = {key: int(stats.get(key, -1)) for key in count_keys}
    expected = {key: int(evidence.get(key, -1)) for key in observed}
    errors: list[str] = []
    if observed != expected:
        errors.append(f"mutation_runtime_differs:observed={observed}:expected={expected}")
    return {
        "schema": "seal.mutation-audit.v1",
        "ok": not errors,
        "status": "PASSED" if not errors else "FAILED",
        "observed": observed,
        "expected": expected,
        "errors": errors,
    }


def verify_manifest(
    repo: Path,
    policy: dict[str, Any],
    path: Path,
    execute: bool,
    ratchet_base: str = "HEAD",
) -> dict[str, Any]:
    manifest = _read_json(path)
    errors = _static_manifest_errors(repo, policy, manifest, path, ratchet_base)
    errors.extend(scan_known_defects(repo, policy, manifest.get("subjects", [])))
    if errors:
        return {"ok": False, "manifest": str(path), "status": "REJECTED", "errors": errors}
    mutation = _verify_mutation(repo, policy, manifest)
    if not execute:
        return {"ok": True, "manifest": str(path), "status": "STATIC_OK", "mutation": mutation}
    commands = _execute_commands(repo, manifest["commands"])
    command_errors = [
        f"command_failed:{row.command_id}:{row.observed_exit}!={row.expected_exit}"
        if row.observed_exit != row.expected_exit
        else f"command_output_missing:{row.command_id}:{row.expected_stdout_contains}"
        for row in commands if not row.ok
    ]
    delivery_row = manifest["delivery"]["command"]
    delivery_results = _execute_commands(repo, [{"kind": "delivery_effect", **delivery_row}])
    command_errors.extend(
        f"delivery_effect_failed:{row.command_id}:{row.observed_exit}!={row.expected_exit}"
        if row.observed_exit != row.expected_exit
        else f"delivery_effect_output_missing:{row.command_id}:{row.expected_stdout_contains}"
        for row in delivery_results if not row.ok
    )
    coverage = _measure_coverage(repo, manifest["coverage"])
    baseline = float(manifest["coverage"].get("baseline_percent", 0))
    floor = max(float(policy["thresholds"]["coverage_floor_percent"]), baseline)
    if coverage["percent"] + 1e-9 < floor:
        command_errors.append(f"coverage_regression:{coverage['percent']}<{floor}")
    return {
        "ok": not command_errors,
        "manifest": str(path),
        "status": "PASSED" if not command_errors else "FAILED",
        "commands": [row.__dict__ | {"ok": row.ok} for row in commands],
        "delivery": [row.__dict__ | {"ok": row.ok} for row in delivery_results],
        "coverage": coverage,
        "mutation": mutation,
        "errors": command_errors,
    }


def audit(repo: Path, policy_path: Path, execute: bool) -> dict[str, Any]:
    policy = load_policy(policy_path)
    snapshot = inventory(repo)
    errors: list[str] = _policy_ratchet_errors(repo, policy_path, policy)
    ratio = float(snapshot["tests"]["committed_ratio"])
    floor = float(policy["thresholds"]["committed_test_ratio_floor"])
    if ratio + 1e-9 < floor:
        errors.append(f"committed_test_ratio_regression:{ratio}<{floor}")
    for required in policy.get("required_tracked_paths", []):
        if not _repo_path(repo, required).is_file():
            errors.append(f"required_path_missing:{required}")
        elif not _is_committed(repo, required):
            errors.append(f"required_path_not_committed:{required}")
    manifests: list[dict[str, Any]] = []
    for raw in policy.get("manifests", []):
        manifest_path = _repo_path(repo, raw)
        if not manifest_path.is_file():
            errors.append(f"manifest_missing:{raw}")
            continue
        result = verify_manifest(repo, policy, manifest_path, execute=execute)
        manifests.append(result)
        if not result["ok"]:
            errors.extend(f"{raw}:{error}" for error in result.get("errors", [result["status"]]))
    return {
        "schema": "seal.quality-audit.v1",
        "ok": not errors,
        "status": "PASSED" if not errors else "FAILED",
        "inventory": snapshot,
        "manifests": manifests,
        "errors": errors,
    }


def check_subject(repo: Path, policy_path: Path, raw: str) -> dict[str, Any]:
    policy = load_policy(policy_path)
    errors: list[str] = []
    path = _repo_path(repo, raw)
    if not path.is_file():
        errors.append(f"subject_missing:{raw}")
    if not _is_indexed(repo, raw):
        errors.append(f"subject_unindexed:{raw}")
    covering: list[str] = []
    tests: list[str] = []
    for manifest_raw in policy.get("manifests", []):
        manifest_path = _repo_path(repo, manifest_raw)
        if not manifest_path.is_file():
            continue
        manifest = _read_json(manifest_path)
        if raw in manifest.get("subjects", []):
            covering.append(manifest_raw)
            tests.extend(manifest.get("tests", []))
    if not covering:
        errors.append(f"subject_without_quality_manifest:{raw}")
    if not tests:
        errors.append(f"subject_without_tests:{raw}")
    return {
        "schema": "seal.quality-subject-check.v1", "ok": not errors,
        "subject": raw, "manifests": covering, "tests": sorted(set(tests)), "errors": errors,
    }


def staged(repo: Path, policy_path: Path) -> dict[str, Any]:
    """Reject staged code that lacks a complete, independently reviewed gate."""
    policy = load_policy(policy_path)
    proc = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMRD")
    if proc.returncode != 0:
        raise QualityGateError("cannot_list_staged_paths")
    changed = sorted(set(proc.stdout.splitlines()))
    subjects = [
        raw for raw in changed
        if _repo_path(repo, raw).is_file() and _is_source_path(raw, policy) and not _is_excluded(raw)
    ]
    deletions = [raw for raw in changed if not _repo_path(repo, raw).exists() and _is_source_path(raw, policy)]
    errors: list[str] = []
    manifests: set[str] = {raw for raw in policy.get("manifests", []) if raw in changed}
    subject_results: list[dict[str, Any]] = []
    for raw in subjects:
        result = check_subject(repo, policy_path, raw)
        subject_results.append(result)
        errors.extend(result["errors"])
        manifests.update(result["manifests"])
    for raw in deletions:
        covering = []
        for manifest_raw in policy.get("manifests", []):
            manifest_path = _repo_path(repo, manifest_raw)
            if not manifest_path.is_file():
                continue
            manifest = _read_json(manifest_path)
            if raw in manifest.get("deletions", []):
                covering.append(manifest_raw)
                manifests.add(manifest_raw)
        if not covering:
            errors.append(f"deletion_without_quality_manifest:{raw}")
    errors.extend(scan_known_defects(repo, policy, subjects))
    manifest_results: list[dict[str, Any]] = []
    for raw in sorted(manifests):
        result = verify_manifest(repo, policy, _repo_path(repo, raw), execute=False)
        manifest_results.append(result)
        if not result["ok"]:
            errors.extend(f"{raw}:{error}" for error in result.get("errors", [result["status"]]))
    return {
        "schema": "seal.quality-staged-gate.v1",
        "ok": not errors,
        "status": "PASSED" if not errors else "REJECTED",
        "changed": changed,
        "subjects": subject_results,
        "deletions": deletions,
        "manifests": manifest_results,
        "errors": errors,
    }


def _manifest_selection(
    repo: Path, policy: dict[str, Any], changed: Iterable[str]
) -> tuple[dict[str, list[str]], list[str]]:
    """Select every manifest whose declared evidence surface intersects the diff."""
    changed_set = set(changed)
    reasons: dict[str, list[str]] = {}
    all_manifests = list(policy.get("manifests", []))
    for manifest_raw in all_manifests:
        manifest_path = _repo_path(repo, manifest_raw)
        if not manifest_path.is_file():
            continue
        manifest = _read_json(manifest_path)
        covered: dict[str, str] = {manifest_raw: "manifest"}
        for raw in manifest.get("subjects", []):
            if isinstance(raw, str):
                covered[raw] = "subject"
        for raw in manifest.get("tests", []):
            if isinstance(raw, str):
                covered[raw] = "test"
        mutation = manifest.get("mutation_evidence")
        if isinstance(mutation, str) and mutation:
            covered[mutation] = "mutation_evidence"
        for raw in sorted(changed_set & set(covered)):
            reasons.setdefault(manifest_raw, []).append(f"{covered[raw]}:{raw}")
    return reasons, sorted(set(all_manifests) - set(reasons))


def diff_gate(repo: Path, policy_path: Path, base: str, execute: bool = False) -> dict[str, Any]:
    """CI equivalent of the staged gate, evaluated against an immutable base."""
    if _git(repo, "cat-file", "-e", f"{base}^{{commit}}").returncode != 0:
        raise QualityGateError(f"invalid_diff_base:{base}")
    policy = load_policy(policy_path)
    proc = _git(repo, "diff", "--name-only", "--diff-filter=ACMRD", f"{base}...HEAD")
    if proc.returncode != 0:
        raise QualityGateError(f"cannot_diff_base:{base}")
    changed = sorted(set(proc.stdout.splitlines()))
    subjects = [raw for raw in changed if _repo_path(repo, raw).is_file() and _is_source_path(raw, policy)]
    deletions = [raw for raw in changed if not _repo_path(repo, raw).exists() and _is_source_path(raw, policy)]
    errors: list[str] = _policy_ratchet_errors(repo, policy_path, policy, base)
    selection_reason, skipped = _manifest_selection(repo, policy, changed)
    manifests: set[str] = set(selection_reason)
    subject_results: list[dict[str, Any]] = []
    for raw in subjects:
        result = check_subject(repo, policy_path, raw)
        subject_results.append(result)
        errors.extend(result["errors"])
        manifests.update(result["manifests"])
    for raw in deletions:
        covering = []
        for manifest_raw in policy.get("manifests", []):
            manifest_path = _repo_path(repo, manifest_raw)
            if not manifest_path.is_file():
                continue
            manifest = _read_json(manifest_path)
            if raw in manifest.get("deletions", []):
                covering.append(manifest_raw)
                manifests.add(manifest_raw)
        if not covering:
            errors.append(f"deletion_without_quality_manifest:{raw}")
    errors.extend(scan_known_defects(repo, policy, subjects))
    manifest_results = []
    for raw in sorted(manifests):
        result = verify_manifest(repo, policy, _repo_path(repo, raw), execute=execute, ratchet_base=base)
        manifest_results.append(result)
        if not result["ok"]:
            errors.extend(f"{raw}:{error}" for error in result.get("errors", [result["status"]]))
    return {
        "schema": "seal.quality-diff-gate.v1", "ok": not errors,
        "status": "PASSED" if not errors else "REJECTED", "base": base,
        "changed": changed, "subjects": subject_results, "deletions": deletions,
        "selected": sorted(manifests), "selection_reason": selection_reason, "skipped": skipped,
        "manifests": manifest_results, "errors": errors,
    }


def hook_status(repo: Path) -> dict[str, Any]:
    expected = (repo / ".githooks/pre-commit").resolve()
    proc = _git(repo, "rev-parse", "--git-path", "hooks/pre-commit")
    if proc.returncode != 0:
        raise QualityGateError("cannot_resolve_effective_hook")
    raw = proc.stdout.strip()
    effective = Path(raw)
    if not effective.is_absolute():
        effective = (repo / effective).resolve()
    else:
        effective = effective.resolve()
    content = effective.read_text(encoding="utf-8", errors="ignore") if effective.is_file() else ""
    errors: list[str] = []
    if effective != expected:
        errors.append(f"quality_hook_not_active:{effective}!={expected}")
    if not os.access(expected, os.X_OK):
        errors.append("quality_hook_not_executable")
    for required in ("seal_quality_gate.py staged", "seal_core_guard.py --pre-commit"):
        if required not in content:
            errors.append(f"effective_hook_missing:{required}")
    return {
        "schema": "seal.quality-hook-status.v1", "ok": not errors,
        "status": "ACTIVE" if not errors else "INACTIVE",
        "effective": str(effective), "expected": str(expected), "errors": errors,
    }


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="action", required=True)
    inventory_parser = sub.add_parser("inventory")
    inventory_parser.set_defaults(action="inventory")
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--policy", type=Path, default=Path("quality/policy.json"))
    audit_parser.add_argument("--execute", action="store_true")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("manifest", type=Path)
    verify_parser.add_argument("--policy", type=Path, default=Path("quality/policy.json"))
    verify_parser.add_argument("--execute", action="store_true")
    subject_parser = sub.add_parser("check-subject")
    subject_parser.add_argument("path")
    subject_parser.add_argument("--policy", type=Path, default=Path("quality/policy.json"))
    staged_parser = sub.add_parser("staged")
    staged_parser.add_argument("--policy", type=Path, default=Path("quality/policy.json"))
    diff_parser = sub.add_parser("diff")
    diff_parser.add_argument("--base", required=True)
    diff_parser.add_argument("--policy", type=Path, default=Path("quality/policy.json"))
    diff_parser.add_argument("--execute", action="store_true")
    sub.add_parser("hook-status")
    mutation_parser = sub.add_parser("mutation-audit")
    mutation_parser.add_argument("manifest", type=Path)
    mutation_parser.add_argument("--stats", type=Path, default=Path("mutants/mutmut-cicd-stats.json"))
    mutation_parser.add_argument("--policy", type=Path, default=Path("quality/policy.json"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.action == "inventory":
            result = inventory(repo)
        elif args.action == "audit":
            result = audit(repo, _repo_path(repo, str(args.policy)), execute=args.execute)
        elif args.action == "verify":
            policy = load_policy(_repo_path(repo, str(args.policy)))
            result = verify_manifest(repo, policy, _repo_path(repo, str(args.manifest)), execute=args.execute)
        elif args.action == "check-subject":
            result = check_subject(repo, _repo_path(repo, str(args.policy)), args.path)
        elif args.action == "staged":
            result = staged(repo, _repo_path(repo, str(args.policy)))
        elif args.action == "diff":
            result = diff_gate(repo, _repo_path(repo, str(args.policy)), args.base, execute=args.execute)
        elif args.action == "hook-status":
            result = hook_status(repo)
        elif args.action == "mutation-audit":
            result = mutation_audit(
                repo,
                _repo_path(repo, str(args.policy)),
                _repo_path(repo, str(args.manifest)),
                _repo_path(repo, str(args.stats)),
            )
        else:
            raise QualityGateError(f"unknown_action:{args.action}")
        _print(result)
        return 0 if result.get("ok", True) else 1
    except QualityGateError as exc:
        _print({"ok": False, "status": "ERROR", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
