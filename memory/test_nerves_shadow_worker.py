from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

import pytest

from memory.nerves_shadow_worker import (
    EVIDENCE_SCHEMA,
    MISSION_SCHEMA,
    NervesShadowWorker,
    ROOT,
    WorkerAdmissionError,
)
from memory.ssai_shadow.ledger import ShadowLedger


SKILL_PATH = ROOT / "skills/seal-nerves-integrity-audit/SKILL.md"


def _mission() -> dict:
    mission_id = str(uuid.uuid4())
    return {
        "schema": "seal.nerves.mission.v1",
        "mission_id": mission_id,
        "idempotency_key": f"test-jarvis-integrity-{uuid.uuid4().hex}",
        "agent": "JARVIS",
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "nerve_fire_id": f"test:{uuid.uuid4().hex}",
        "correlation_id": None,
        "nerve_layer": "AGENT_ROLE",
        "drive": "reactive",
        "specialty": "architecture_integrity_audit",
        "objective": "Classify a synthetic integrity finding without mutation.",
        "risk_class": "A2_READ_ONLY",
        "source_refs": ["file:synthetic.jsonl"],
        "initiation_conditions": ["synthetic finding"],
        "scope": {
            "workspace": str(ROOT),
            "paths": ["tools/dependency_inventory.py"],
            "services": [],
            "network": "none",
        },
        "skills": [
            {
                "id": "seal-nerves-integrity-audit",
                "version": "1",
                "sha256": hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest(),
            }
        ],
        "allowed_tools": [
            "filesystem_read",
            "git_read",
            "systemctl_read",
            "journalctl_read",
            "subprocess_allowlisted_read_only",
        ],
        "budgets": {
            "wall_seconds": 30,
            "max_attempts": 1,
            "token_budget": 1000,
        },
        "expected_evidence": ["typed result"],
        "termination_conditions": ["typed evidence submitted"],
        "rollback": {"required": False, "plan": "No mutation authorized."},
        "builder": "JARVIS@test",
        "verifier": "ADA@test",
        "confidence_prior": 0.5,
    }


def _write_manifest(directory: Path, mission: dict, *, mode: int = 0o600) -> Path:
    directory.mkdir(mode=0o700)
    path = directory / f"{mission['mission_id']}.json"
    path.write_text(json.dumps(mission), encoding="utf-8")
    path.chmod(mode)
    return path


def _evidence(mission_id: str) -> dict:
    zero = "0" * 64
    return {
        "schema": "seal.nerves.integrity-evidence.v1",
        "mission_id": mission_id,
        "classification": "healthy",
        "read_only": True,
        "summary": "Synthetic read-only verification completed.",
        "checks": [
            {
                "label": "synthetic",
                "argv": ["python3", "collector.py"],
                "returncode": 0,
                "stdout_sha256": zero,
                "stderr_sha256": zero,
                "stdout_tail": "",
                "stderr_tail": "",
            }
        ],
    }


class FakeCodex:
    def __init__(
        self,
        mission_id: str,
        *,
        output_tokens: int = 120,
        returncode: int = 0,
        valid_evidence: bool = True,
        event_padding: int = 0,
        timeout: bool = False,
        forbidden_activity: bool = False,
    ) -> None:
        self.mission_id = mission_id
        self.output_tokens = output_tokens
        self.returncode = returncode
        self.valid_evidence = valid_evidence
        self.event_padding = event_padding
        self.timeout = timeout
        self.forbidden_activity = forbidden_activity
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv: list[str], **kwargs):
        self.calls.append((argv, kwargs))
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], b"", b"late")
        response = Path(argv[argv.index("--output-last-message") + 1])
        value = _evidence(self.mission_id)
        if not self.valid_evidence:
            value.pop("checks")
        response.write_text(json.dumps(value), encoding="utf-8")
        response.chmod(0o600)
        event = {
            "type": (
                "command_execution"
                if self.forbidden_activity
                else "turn.completed"
            ),
            "usage": {
                "input_tokens": 50,
                "cached_input_tokens": 0,
                "output_tokens": self.output_tokens,
            },
            "padding": "x" * self.event_padding,
        }
        return subprocess.CompletedProcess(
            argv,
            self.returncode,
            (json.dumps(event) + "\n").encode(),
            b"",
        )


def _worker(tmp_path: Path, manifest_dir: Path, fake: FakeCodex):
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\n", encoding="utf-8")
    codex.chmod(0o700)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    return NervesShadowWorker(
        manifest_dir=manifest_dir,
        runs_dir=tmp_path / "runs",
        workspace=ROOT,
        skills_root=ROOT / "skills",
        mission_schema=MISSION_SCHEMA,
        evidence_schema=EVIDENCE_SCHEMA,
        codex_binary=codex,
        codex_home=codex_home,
        subprocess_run=fake,
        execution_enabled=True,
    )


def test_default_worker_is_security_hold_and_never_calls_codex(tmp_path):
    mission = _mission()
    manifest_dir = tmp_path / "manifests"
    manifest = _write_manifest(manifest_dir, mission)
    fake = FakeCodex(mission["mission_id"])
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\n", encoding="utf-8")
    codex.chmod(0o700)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    worker = NervesShadowWorker(
        manifest_dir=manifest_dir,
        runs_dir=tmp_path / "runs",
        workspace=ROOT,
        codex_binary=codex,
        codex_home=codex_home,
        subprocess_run=fake,
    )

    with pytest.raises(WorkerAdmissionError, match="live_runner_security_hold"):
        worker.run(manifest)
    assert fake.calls == []
    assert not (tmp_path / "runs").exists()


def test_execution_enabled_cannot_cross_hold_with_real_subprocess_runner(tmp_path):
    mission = _mission()
    manifest_dir = tmp_path / "manifests"
    manifest = _write_manifest(manifest_dir, mission)
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    codex.chmod(0o700)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    worker = NervesShadowWorker(
        manifest_dir=manifest_dir,
        runs_dir=tmp_path / "runs",
        workspace=ROOT,
        codex_binary=codex,
        codex_home=codex_home,
        execution_enabled=True,
    )

    with pytest.raises(WorkerAdmissionError, match="live_runner_security_hold"):
        worker.run(manifest)
    assert not (tmp_path / "runs").exists()


def test_success_uses_exact_safe_codex_contract_and_minimal_environment(tmp_path):
    mission = _mission()
    manifest_dir = tmp_path / "manifests"
    manifest = _write_manifest(manifest_dir, mission)
    fake = FakeCodex(mission["mission_id"])
    result = _worker(tmp_path, manifest_dir, fake).run(manifest)

    assert result.status == "succeeded"
    assert result.joined is False
    assert result.evidence_path is not None
    argv, kwargs = fake.calls[0]
    assert argv[1:8] == [
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "-c",
        'approval_policy="never"',
        "--disable",
    ]
    assert argv[8] == "web_search"
    assert "--ignore-user-config" in argv
    assert "--output-schema" in argv
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == mission["budgets"]["wall_seconds"]
    assert set(kwargs["env"]) == {
        "HOME",
        "CODEX_HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "TZ",
    }
    assert not any(
        key.startswith(("SEAL_", "PG")) or "TOKEN" in key or "KEY" in key
        for key in kwargs["env"]
    )
    for path in result.run_dir.iterdir():
        if path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    verification = ShadowLedger(result.run_dir / "states.jsonl").verify()
    assert verification.ok
    assert verification.sequence == 3


def test_same_idempotency_joins_without_second_codex_call(tmp_path):
    mission = _mission()
    manifest_dir = tmp_path / "manifests"
    manifest = _write_manifest(manifest_dir, mission)
    fake = FakeCodex(mission["mission_id"])
    worker = _worker(tmp_path, manifest_dir, fake)

    first = worker.run(manifest)
    second = worker.run(manifest)

    assert first.status == second.status == "succeeded"
    assert second.joined is True
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda m: m.update(risk_class="A3_REVERSIBLE_WRITE"), "risk_class"),
        (lambda m: m["scope"].update(network="allowlist"), "network"),
        (lambda m: m["scope"].update(services=["x.service"]), "services"),
        (lambda m: m["budgets"].update(max_attempts=2), "max_attempts"),
        (lambda m: m["skills"][0].update(sha256="0" * 64), "skill_hash"),
        (lambda m: m.update(agent="ADA"), "agent"),
        (lambda m: m.update(source_refs=["postgresql://u:p@host/db"]), "secret"),
    ],
)
def test_admission_fails_closed_before_codex(tmp_path, mutate, reason):
    mission = _mission()
    mutate(mission)
    manifest_dir = tmp_path / "manifests"
    manifest = _write_manifest(manifest_dir, mission)
    fake = FakeCodex(mission["mission_id"])

    with pytest.raises(WorkerAdmissionError, match=reason):
        _worker(tmp_path, manifest_dir, fake).run(manifest)
    assert fake.calls == []


def test_manifest_must_be_owner_only_regular_and_canonical(tmp_path):
    mission = _mission()
    manifest_dir = tmp_path / "manifests"
    manifest = _write_manifest(manifest_dir, mission, mode=0o640)
    fake = FakeCodex(mission["mission_id"])
    worker = _worker(tmp_path, manifest_dir, fake)
    with pytest.raises(WorkerAdmissionError, match="permissions_too_open"):
        worker.run(manifest)

    manifest.chmod(0o600)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    moved = elsewhere / manifest.name
    moved.write_bytes(manifest.read_bytes())
    moved.chmod(0o600)
    with pytest.raises(WorkerAdmissionError, match="canonical"):
        worker.run(moved)


def test_symlink_manifest_rejected(tmp_path):
    mission = _mission()
    manifest_dir = tmp_path / "manifests"
    real = _write_manifest(manifest_dir, mission)
    link = manifest_dir / f"{uuid.uuid4()}.json"
    link.symlink_to(real)
    fake = FakeCodex(mission["mission_id"])
    with pytest.raises(WorkerAdmissionError, match="symlink"):
        _worker(tmp_path, manifest_dir, fake).run(link)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("fake_kwargs", "reason"),
    [
        ({"valid_evidence": False}, "evidence_schema_invalid"),
        ({"output_tokens": 1001}, "token_budget_exhausted"),
        ({"event_padding": 70_000}, "output_budget_exhausted"),
        ({"timeout": True}, "wall_budget_exhausted"),
        ({"returncode": 7}, "codex_exit_7"),
        ({"forbidden_activity": True}, "forbidden_codex_activity_observed"),
    ],
)
def test_claimed_execution_failures_are_terminal_and_not_retried(
    tmp_path, fake_kwargs, reason
):
    mission = _mission()
    manifest_dir = tmp_path / "manifests"
    manifest = _write_manifest(manifest_dir, mission)
    fake = FakeCodex(mission["mission_id"], **fake_kwargs)
    worker = _worker(tmp_path, manifest_dir, fake)

    first = worker.run(manifest)
    second = worker.run(manifest)

    assert first.status == second.status == "failed"
    assert first.reason is not None and reason in first.reason
    assert second.joined is True
    assert len(fake.calls) == 1
    verification = ShadowLedger(first.run_dir / "states.jsonl").verify()
    assert verification.ok
    assert verification.sequence == 3


def test_idempotency_conflict_with_different_manifest_is_rejected(tmp_path):
    first = _mission()
    manifest_dir = tmp_path / "manifests"
    first_path = _write_manifest(manifest_dir, first)
    fake = FakeCodex(first["mission_id"])
    worker = _worker(tmp_path, manifest_dir, fake)
    assert worker.run(first_path).status == "succeeded"

    second = deepcopy(first)
    second["mission_id"] = str(uuid.uuid4())
    second["objective"] = "Different content with the same idempotency key."
    second_path = manifest_dir / f"{second['mission_id']}.json"
    second_path.write_text(json.dumps(second), encoding="utf-8")
    second_path.chmod(0o600)
    with pytest.raises(WorkerAdmissionError, match="idempotency_claim_conflict"):
        worker.run(second_path)
    assert len(fake.calls) == 1
