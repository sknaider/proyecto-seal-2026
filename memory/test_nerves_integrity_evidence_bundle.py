from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import time
import uuid

from jsonschema import Draft202012Validator, FormatChecker
import pytest

from memory.nerves_integrity_evidence_bundle import (
    EVIDENCE_SCHEMA,
    EvidenceBundleError,
    MISSION_COMPILER_REV,
    SKILL_DIR,
    admit_mission,
    build_integrity_evidence_bundle,
    skill_bundle_digest,
)
from memory.nerves_mission_shadow import (
    compile_jarvis_integrity_mission,
    write_shadow_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = (
    ROOT
    / "skills/seal-nerves-integrity-audit/scripts/collect_integrity_evidence.py"
)


def _load_collector_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_collector_test", COLLECTOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _episode_key(record: dict, anchor: str) -> str:
    material = {
        "agent": record["agent"],
        "action": record["action"],
        "state": record["state"],
        "findings": sorted(
            " ".join(item.strip().lower().split()) for item in record["findings"]
        ),
        "episode_anchor": anchor,
        "compiler_rev": MISSION_COMPILER_REV,
    }
    return hashlib.sha256(
        json.dumps(
            material, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source_dir = workspace / "evidence"
    source_dir.mkdir()
    source = source_dir / "jarvis.jsonl"
    record = {
        "ts": "2026-07-23T21:47:43.872654+00:00",
        "agent": "JARVIS",
        "action": "integrity_pulse",
        "state": "FINDING",
        "findings": ["SYSTEMD user: seal-test.service"],
        "status": "issue",
    }
    source.write_text(json.dumps(record) + "\n", encoding="utf-8")
    source.chmod(0o600)
    anchor = "2026-07-23T21:09:48.416834+00:00"
    correlation = _episode_key(record, anchor)

    skill = tmp_path / "skill"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    (skill / "SKILL.md").write_text("bounded integrity skill\n", encoding="utf-8")
    (scripts / "collect.py").write_text("print('read only')\n", encoding="utf-8")
    (skill / "SKILL.md").chmod(0o644)
    (scripts / "collect.py").chmod(0o755)

    mission_id = str(uuid.uuid4())
    mission = {
        "schema": "seal.nerves.mission.v1",
        "mission_id": mission_id,
        "idempotency_key": f"jarvis-test-{uuid.uuid4().hex}",
        "agent": "JARVIS",
        "tenant_id": "00000000-0000-0000-0000-000000000000",
        "nerve_fire_id": f"jarvis-integrity:{record['ts']}:{correlation[:16]}",
        "correlation_id": correlation,
        "nerve_layer": "AGENT_ROLE",
        "drive": "reactive",
        "specialty": "architecture_integrity_audit",
        "objective": "Classify this exact synthetic finding without mutation.",
        "risk_class": "A2_READ_ONLY",
        "source_refs": [
            "file:evidence/jarvis.jsonl",
            (
                "record_sha256:"
                + hashlib.sha256(
                    json.dumps(
                        record, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
            ),
            f"episode_anchor:{anchor}",
        ],
        "initiation_conditions": ["fresh finding"],
        "scope": {
            "workspace": str(workspace),
            "paths": ["evidence/jarvis.jsonl"],
            "services": [],
            "network": "none",
        },
        "skills": [
            {
                "id": "seal-nerves-integrity-audit",
                "version": "1",
                "sha256": skill_bundle_digest(skill),
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
        "expected_evidence": ["typed evidence"],
        "termination_conditions": ["typed evidence submitted"],
        "rollback": {"required": False, "plan": "No mutation."},
        "builder": "JARVIS@test",
        "verifier": "ADA@test",
        "confidence_prior": 0.5,
    }
    manifest = tmp_path / f"{mission_id}.json"
    manifest.write_text(json.dumps(mission), encoding="utf-8")
    manifest.chmod(0o600)
    return workspace, source, skill, manifest, mission


def _typed_evidence(mission: dict) -> dict:
    empty = hashlib.sha256(b"").hexdigest()
    return {
        "schema": "seal.nerves.integrity-evidence.v1",
        "mission_id": mission["mission_id"],
        "classification": "healthy",
        "read_only": True,
        "summary": "Synthetic deterministic read-only result.",
        "checks": [
            {
                "label": "synthetic",
                "argv": ["systemctl", "--user", "list-units"],
                "returncode": 0,
                "stdout_sha256": empty,
                "stderr_sha256": empty,
                "stdout_tail": "",
                "stderr_tail": "",
            }
        ],
    }


def test_real_compiler_manifest_admits_with_bundle_hash_after_source_append(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    artifact_dir = workspace / "evidence"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "jarvis.jsonl"
    record = {
        "ts": "2026-07-23T21:47:43.872654+00:00",
        "agent": "JARVIS",
        "action": "integrity_pulse",
        "state": "FINDING",
        "findings": ["SYSTEMD user: seal-test.service"],
    }
    artifact.write_text(json.dumps(record) + "\n", encoding="utf-8")
    artifact.chmod(0o600)
    mission = compile_jarvis_integrity_mission(
        record,
        artifact_path=artifact,
        episode_anchor="genesis",
        workspace_root=workspace,
    )
    manifest = write_shadow_manifest(mission, tmp_path / "manifests")
    artifact.write_text(
        artifact.read_text(encoding="utf-8") + '{"unrelated":true}\n',
        encoding="utf-8",
    )
    artifact.chmod(0o600)
    admission = admit_mission(
        manifest,
        workspace=workspace,
        skill_dir=SKILL_DIR,
    )
    assert admission.mission["mission_id"] == mission["mission_id"]
    assert admission.skill_bundle_sha256 == skill_bundle_digest(SKILL_DIR)
    assert admission.source_record == record


def test_admission_rejects_manifest_symlink_and_non_600(tmp_path):
    workspace, _, skill, manifest, _ = _fixture(tmp_path)
    link = tmp_path / f"{uuid.uuid4()}.json"
    link.symlink_to(manifest)
    with pytest.raises(EvidenceBundleError, match="secure_read_failed"):
        admit_mission(link, workspace=workspace, skill_dir=skill)
    manifest.chmod(0o640)
    with pytest.raises(EvidenceBundleError, match="file_mode_must_be_600"):
        admit_mission(manifest, workspace=workspace, skill_dir=skill)


def test_skill_script_drift_is_detected_even_when_skill_md_is_unchanged(tmp_path):
    workspace, _, skill, manifest, _ = _fixture(tmp_path)
    original_skill_md = (skill / "SKILL.md").read_bytes()
    (skill / "scripts/collect.py").write_text("print('drift')\n", encoding="utf-8")
    assert (skill / "SKILL.md").read_bytes() == original_skill_md
    with pytest.raises(EvidenceBundleError, match="skill_bundle_digest_mismatch"):
        admit_mission(manifest, workspace=workspace, skill_dir=skill)


def test_append_only_source_growth_is_allowed_but_exact_episode_is_enforced(tmp_path):
    workspace, source, skill, manifest, mission = _fixture(tmp_path)
    source.write_text(source.read_text() + '{"unrelated":true}\n', encoding="utf-8")
    source.chmod(0o600)
    admission = admit_mission(manifest, workspace=workspace, skill_dir=skill)
    assert admission.source_record["ts"] == "2026-07-23T21:47:43.872654+00:00"

    source.write_text(json.dumps({
        "ts": "wrong",
        "agent": "JARVIS",
        "action": "integrity_pulse",
        "state": "FINDING",
        "findings": ["SYSTEMD user: seal-test.service"],
    }) + "\n", encoding="utf-8")
    changed = deepcopy(mission)
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    manifest.chmod(0o600)
    with pytest.raises(EvidenceBundleError, match="source_episode_match_count"):
        admit_mission(manifest, workspace=workspace, skill_dir=skill)


def test_mission_commands_cannot_expand_the_fixed_A2_allowlist(tmp_path):
    workspace, _, skill, manifest, mission = _fixture(tmp_path)
    changed = deepcopy(mission)
    changed["allowed_tools"].append("network_fetch")
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    manifest.chmod(0o600)
    with pytest.raises(EvidenceBundleError, match="allowed_tools_must_equal"):
        admit_mission(manifest, workspace=workspace, skill_dir=skill)


def test_collector_uses_fixed_allowlist_and_strips_sensitive_environment():
    module = _load_collector_module()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    mission = {"mission_id": str(uuid.uuid4())}
    sensitive = {
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "SEAL_SESSION_TOKEN": "secret",
        "PGPASSWORD": "secret",
        "OPENAI_API_KEY": "secret",
        "SSH_AUTH_SOCK": "/tmp/agent",
    }
    evidence = module.collect(
        mission, subprocess_run=fake_run, environment=sensitive
    )
    expected = [list(argv) for _, argv in module.COMMAND_SPECS]
    assert [call[0] for call in calls] == expected
    assert all(call[1]["shell"] is False for call in calls)
    assert all(call[1]["stdin"] is subprocess.DEVNULL for call in calls)
    assert all(call[1]["env"] == module.SAFE_ENV for call in calls)
    rendered = json.dumps(evidence)
    assert "secret" not in rendered
    assert "summary" in evidence


def test_collector_treats_inventory_rc1_as_regression_not_instrument_failure():
    module = _load_collector_module()
    empty = {
        "returncode": 0,
        "stdout_tail": "",
        "stderr_tail": "",
    }
    checks = [
        {"label": "dependency_diff", **empty},
        {"label": "dependency_identity", **empty},
        {"label": "dependency_absolute", **{**empty, "returncode": 1}},
        {"label": "failed_user_units", **empty},
        {"label": "failed_system_units", **empty},
    ]
    assert module._classify(checks) == "real_regression"


def test_bundle_writes_schema_valid_immutable_600_evidence_and_provenance(tmp_path):
    workspace, _, skill, manifest, mission = _fixture(tmp_path)
    result = build_integrity_evidence_bundle(
        manifest,
        output_dir=tmp_path / "runs",
        workspace=workspace,
        skill_dir=skill,
        collect_fn=_typed_evidence,
    )
    evidence = json.loads(result.evidence_path.read_text())
    schema = json.loads(EVIDENCE_SCHEMA.read_text())
    assert not list(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(evidence)
    )
    provenance = json.loads(result.provenance_path.read_text())
    assert provenance["source"]["record_sha256"]
    assert provenance["evidence_sha256"] == result.evidence_sha256
    assert stat.S_IMODE(result.evidence_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.provenance_path.stat().st_mode) == 0o600
    assert result.joined is False

    def must_not_recollect(_mission):
        raise AssertionError("replay must not call the collector")

    replay = build_integrity_evidence_bundle(
        manifest,
        output_dir=tmp_path / "runs",
        workspace=workspace,
        skill_dir=skill,
        collect_fn=must_not_recollect,
    )
    assert replay.joined is True
    assert replay.evidence_sha256 == result.evidence_sha256
    assert replay.provenance_sha256 == result.provenance_sha256


def test_bundle_partial_state_fails_closed_without_collecting(tmp_path):
    workspace, _, skill, manifest, mission = _fixture(tmp_path)
    output_dir = tmp_path / "runs"
    output_dir.mkdir(mode=0o700)
    partial = output_dir / f"{mission['mission_id']}.evidence.json"
    partial.write_text("{}\n", encoding="utf-8")
    partial.chmod(0o600)
    called = False

    def collector(_mission):
        nonlocal called
        called = True
        return _typed_evidence(mission)

    with pytest.raises(EvidenceBundleError, match="partial_evidence_bundle"):
        build_integrity_evidence_bundle(
            manifest,
            output_dir=output_dir,
            workspace=workspace,
            skill_dir=skill,
            collect_fn=collector,
        )
    assert called is False


def test_bundle_replay_rejects_symlink_open_mode_and_identity_mismatch(tmp_path):
    workspace, _, skill, manifest, mission = _fixture(tmp_path)
    output_dir = tmp_path / "runs"
    result = build_integrity_evidence_bundle(
        manifest,
        output_dir=output_dir,
        workspace=workspace,
        skill_dir=skill,
        collect_fn=_typed_evidence,
    )

    result.evidence_path.chmod(0o640)
    with pytest.raises(EvidenceBundleError, match="file_mode_must_be_600"):
        build_integrity_evidence_bundle(
            manifest,
            output_dir=output_dir,
            workspace=workspace,
            skill_dir=skill,
            collect_fn=_typed_evidence,
        )
    result.evidence_path.chmod(0o600)

    provenance = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    provenance["manifest_sha256"] = "0" * 64
    result.provenance_path.write_bytes(
        json.dumps(
            provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )
    result.provenance_path.chmod(0o600)
    with pytest.raises(
        EvidenceBundleError, match="existing_provenance_identity_mismatch"
    ):
        build_integrity_evidence_bundle(
            manifest,
            output_dir=output_dir,
            workspace=workspace,
            skill_dir=skill,
            collect_fn=_typed_evidence,
        )

    symlink_case = tmp_path / "symlink_case"
    symlink_case.mkdir()
    other_workspace, _, other_skill, other_manifest, other_mission = _fixture(
        symlink_case
    )
    other_output = tmp_path / "symlink_runs"
    other_output.mkdir(mode=0o700)
    target = tmp_path / "attacker-evidence.json"
    target.write_text("{}\n", encoding="utf-8")
    target.chmod(0o600)
    (other_output / f"{other_mission['mission_id']}.evidence.json").symlink_to(
        target
    )
    sidecar = other_output / f"{other_mission['mission_id']}.provenance.json"
    sidecar.write_text("{}\n", encoding="utf-8")
    sidecar.chmod(0o600)
    with pytest.raises(EvidenceBundleError, match="secure_read_failed"):
        build_integrity_evidence_bundle(
            other_manifest,
            output_dir=other_output,
            workspace=other_workspace,
            skill_dir=other_skill,
            collect_fn=_typed_evidence,
        )


def test_eight_concurrent_builders_collect_once_and_join_same_bundle(tmp_path):
    workspace, _, skill, manifest, mission = _fixture(tmp_path)
    output_dir = tmp_path / "runs"
    calls = 0
    calls_lock = threading.Lock()

    def slow_collector(_mission):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return _typed_evidence(mission)

    def build(_index):
        return build_integrity_evidence_bundle(
            manifest,
            output_dir=output_dir,
            workspace=workspace,
            skill_dir=skill,
            collect_fn=slow_collector,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(build, range(8)))

    assert calls == 1
    assert sum(not result.joined for result in results) == 1
    assert sum(result.joined for result in results) == 7
    assert len({result.evidence_sha256 for result in results}) == 1
    assert len({result.provenance_sha256 for result in results}) == 1
