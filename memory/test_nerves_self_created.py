from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import nerves_self_created as self_created
from nerves_self_created import (
    SelfCreatedNerveError,
    _payload_hash,
    build_sibling_review,
    compile_self_created_mission,
    load_active_self_created_nerves,
    promote_self_created_nerve,
    propose_self_created_nerve,
)

REAL_VERIFY_TRUSTED_CHAT_AUTHORITY = (
    self_created._verify_trusted_chat_authority
)
FAKE_SYSTEMD_ATTESTATIONS: dict[str, dict] = {}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _skill(root: Path) -> Path:
    path = root / "skills" / "learned-safe-probe"
    (path / "scripts").mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\n"
        "name: learned-safe-probe\n"
        "description: Read-only learned probe used for an isolated NERVES canary.\n"
        "---\n\n"
        "# Learned safe probe\n\n"
        "Collect typed evidence without tools, network, or mutations.\n",
        encoding="utf-8",
    )
    (path / "scripts" / "probe.py").write_text(
        "print('{\"ok\":true,\"mutations\":0}')\n",
        encoding="utf-8",
    )
    return path


def _observations(pattern: str = "repeated-safe-audit") -> list[dict]:
    return [
        {
            "observation_id": "obs-1",
            "pattern_id": pattern,
            "observed_at": "2026-07-22T10:00:00+00:00",
            "evidence_sha256": _sha("evidence-1"),
            "verified": True,
        },
        {
            "observation_id": "obs-2",
            "pattern_id": pattern,
            "observed_at": "2026-07-22T18:00:00+00:00",
            "evidence_sha256": _sha("evidence-2"),
            "verified": True,
        },
        {
            "observation_id": "obs-3",
            "pattern_id": pattern,
            "observed_at": "2026-07-23T10:00:00+00:00",
            "evidence_sha256": _sha("evidence-3"),
            "verified": True,
        },
    ]


def _candidate(tmp_path: Path) -> dict:
    skill = _skill(tmp_path)
    return propose_self_created_nerve(
        agent="ADA",
        name="learned-safe-probe",
        pattern_id="repeated-safe-audit",
        observations=_observations(),
        skill_dir=skill,
        output_dir=tmp_path / "ledger",
        root=tmp_path,
        created_at="2026-07-24T12:00:00+00:00",
    )


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canary(candidate: dict, root: Path, **overrides) -> dict:
    support = root / "skills" / "seal-nerves-self-creation" / "scripts"
    support.mkdir(parents=True, exist_ok=True)
    launcher = support / "effective_a2_canary.py"
    worker = support / "effective_a2_worker.py"
    launcher.write_text("launcher\n", encoding="utf-8")
    worker.write_text("worker\n", encoding="utf-8")
    validator = root / "memory" / "nerves_self_created.py"
    validator.parent.mkdir(parents=True, exist_ok=True)
    validator.write_text("validator\n", encoding="utf-8")
    source = (
        root
        / "research/flywire_results/nerves_self_created/source.json"
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("{}\n", encoding="utf-8")
    candidate_path = (
        root
        / "research/flywire_results/nerves_self_created"
        / f"{candidate['candidate_id']}.candidate.json"
    )
    candidate_path.write_text(
        json.dumps(candidate, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    mission = compile_self_created_mission(
        candidate,
        objective="Verify the learned read-only pattern in an isolated canary.",
        root=root,
    )
    mission_path = (
        root
        / "research/flywire_results/nerves_self_created"
        / f"{candidate['candidate_id']}.mission.json"
    )
    mission_path.write_text(
        json.dumps(mission, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    skill_script = (
        root / candidate["skill"]["path"] / "scripts" / "probe.py"
    )
    issued = datetime.now(timezone.utc)
    unit = (
        f"seal-self-created-{candidate['candidate_id']}-"
        "0123456789ab.service"
    )
    invocation_id = "0123456789abcdef0123456789abcdef"
    proc = Path("/proc") / str(os.getpid())
    executable = (proc / "exe").resolve(strict=True)
    process_snapshot = {
        "pid": os.getpid(),
        "starttime_ticks": int(
            (proc / "stat").read_text(encoding="utf-8").split()[21]
        ),
        "exe": str(executable),
        "exe_sha256": _file_sha(executable),
    }
    worker_boundary = {
        "af_inet_denied": True,
        "home_read_denied": True,
        "persistent_write_denied": True,
        "service_restart_denied": True,
        "signal_denied": True,
        "credential_env_names": [],
    }
    worker_record = {
        "schema": "seal.nerves.self_created_effect_worker.v1",
        "ok": True,
        "skill_result": {"result_sha256": _sha("canary-evidence")},
        "candidate_file_sha256": _file_sha(candidate_path),
        "mission_file_sha256": _file_sha(mission_path),
        "boundary": worker_boundary,
    }
    canary = {
        "schema": "seal.nerves.self_created_canary.v6",
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "skill_bundle_sha256": candidate["skill"]["bundle_sha256"],
        "passed": True,
        "risk_class": "A2_READ_ONLY",
        "allowed_tools": [],
        "network": "none",
        "mutations": 0,
        "invocation_id": invocation_id,
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(minutes=10)).isoformat(),
        "evidence_sha256": _sha("canary-evidence"),
        "effective_boundary": {
            "backend": "systemd_transient_system",
            "unit": unit,
            **worker_boundary,
            "forbidden_probe_absent_after": True,
            "protected_process_unchanged": True,
        },
        "artifacts": {
            "launcher": {
                "path": launcher.relative_to(root).as_posix(),
                "sha256": _file_sha(launcher),
            },
            "worker": {
                "path": worker.relative_to(root).as_posix(),
                "sha256": _file_sha(worker),
            },
            "source": {
                "path": source.relative_to(root).as_posix(),
                "sha256": _file_sha(source),
            },
            "skill_script": {
                "path": skill_script.relative_to(root).as_posix(),
                "sha256": _file_sha(skill_script),
            },
            "candidate": {
                "path": candidate_path.relative_to(root).as_posix(),
                "sha256": _file_sha(candidate_path),
            },
            "mission": {
                "path": mission_path.relative_to(root).as_posix(),
                "sha256": _file_sha(mission_path),
            },
            "validator": {
                "path": validator.relative_to(root).as_posix(),
                "sha256": _file_sha(validator),
            },
        },
        "protected_process_before": process_snapshot,
        "protected_process_after": process_snapshot,
        "systemd_attestation": {
            "unit": unit,
            "invocation_id": invocation_id,
            "properties_sha256": _sha("systemd-properties"),
            "worker_record_sha256": self_created._sha256_bytes(
                self_created._canonical_bytes(worker_record)
            ),
            "cgroup_empty": True,
        },
    }
    canary.update(overrides)
    canary["canary_sha256"] = _payload_hash(canary, "canary_sha256")
    FAKE_SYSTEMD_ATTESTATIONS[unit] = {
        "unit": unit,
        "invocation_id": invocation_id,
        "properties_sha256": _sha("systemd-properties"),
        "worker_record": worker_record,
        "worker_record_sha256": self_created._sha256_bytes(
            self_created._canonical_bytes(worker_record)
        ),
        "cgroup_empty": True,
        "properties": {
            "BindReadOnlyPaths": " ".join(
                [
                    f"{worker.resolve()}:/opt/nerves/worker.py:rbind",
                    f"{skill_script.resolve()}:/opt/nerves/skill.py:rbind",
                    f"{source.resolve()}:/opt/nerves/source.json:rbind",
                    f"{candidate_path.resolve()}:/opt/nerves/candidate.json:rbind",
                    f"{mission_path.resolve()}:/opt/nerves/mission.json:rbind",
                ]
            ),
            "ExecStart": (
                "argv[]=/usr/bin/python3 /opt/nerves/worker.py "
                "--skill-script /opt/nerves/skill.py "
                "--source /opt/nerves/source.json "
                "--candidate /opt/nerves/candidate.json "
                "--mission /opt/nerves/mission.json "
                f"--protected-pid {process_snapshot['pid']} ;"
            ),
        },
    }
    return canary


@pytest.fixture(autouse=True)
def _trusted_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    FAKE_SYSTEMD_ATTESTATIONS.clear()

    def fake_live(unit: str, invocation_id: str) -> dict:
        attestation = FAKE_SYSTEMD_ATTESTATIONS[unit]
        assert attestation["invocation_id"] == invocation_id
        return attestation

    monkeypatch.setattr(
        self_created,
        "_load_live_systemd_attestation",
        fake_live,
    )
    monkeypatch.setattr(
        self_created,
        "_verify_trusted_chat_authority",
        lambda *_args: None,
    )


def test_end_to_end_promotes_only_a2_metadata(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        canary=canary,
        reviewer_ref="chat_messages.id=117558",
        reviewed_at="2026-07-24T12:01:00+00:00",
        root=tmp_path,
    )
    mission = compile_self_created_mission(
        candidate,
        objective="Verify the learned read-only pattern in an isolated canary.",
        root=tmp_path,
    )
    assert mission["nerve_layer"] == "SELF_CREATED"
    assert mission["allowed_tools"] == []
    registry_path = tmp_path / "registry" / "active.json"
    registry = promote_self_created_nerve(
        candidate,
        review=review,
        canary=canary,
        william_approval_ref="chat_messages.id=117550",
        registry_path=registry_path,
        root=tmp_path,
    )
    assert len(registry["entries"]) == 1
    assert registry["entries"][0]["status"] == "active_a2"
    assert registry_path.stat().st_mode & 0o777 == 0o600
    assert registry_path.parent.stat().st_mode & 0o777 == 0o700
    assert load_active_self_created_nerves(registry_path)[0]["agent"] == "ADA"


def test_candidate_is_write_once_and_replay_is_idempotent(tmp_path: Path) -> None:
    first = _candidate(tmp_path)
    second = propose_self_created_nerve(
        agent="ADA",
        name="learned-safe-probe",
        pattern_id="repeated-safe-audit",
        observations=_observations(),
        skill_dir=tmp_path / "skills" / "learned-safe-probe",
        output_dir=tmp_path / "ledger",
        root=tmp_path,
        created_at="2026-07-24T12:00:00+00:00",
    )
    assert first == second
    artifact = tmp_path / "ledger" / f"{first['candidate_id']}.candidate.json"
    assert artifact.stat().st_mode & 0o777 == 0o600


def test_self_review_is_forbidden(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    with pytest.raises(SelfCreatedNerveError, match="independent_sibling"):
        build_sibling_review(
            candidate,
            reviewer="ADA",
            verdict="APPROVED",
            evidence_sha256=_sha("review"),
            root=tmp_path,
        )


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"allowed_tools": ["Bash"]}, "allowed_tools"),
        ({"network": "allowlist"}, "network"),
        ({"mutations": 1}, "mutations"),
        ({"passed": False}, "passed"),
    ],
)
def test_canary_boundary_fails_closed(
    tmp_path: Path,
    override: dict,
    field: str,
) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path, **override)
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        canary=_canary(candidate, tmp_path),
        reviewer_ref="chat_messages.id=117558",
        root=tmp_path,
    )
    with pytest.raises(SelfCreatedNerveError, match=f"canary_boundary_failed:{field}"):
        promote_self_created_nerve(
            candidate,
            review=review,
            canary=canary,
            william_approval_ref="chat_messages.id=117550",
            registry_path=tmp_path / "registry.json",
            root=tmp_path,
        )


@pytest.mark.parametrize(
    "field",
    [
        "af_inet_denied",
        "home_read_denied",
        "persistent_write_denied",
        "service_restart_denied",
        "signal_denied",
        "forbidden_probe_absent_after",
        "protected_process_unchanged",
    ],
)
def test_effective_canary_denials_are_mandatory(
    tmp_path: Path,
    field: str,
) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        canary=canary,
        reviewer_ref="chat_messages.id=117558",
        root=tmp_path,
    )
    canary["effective_boundary"][field] = False
    canary["canary_sha256"] = _payload_hash(canary, "canary_sha256")
    with pytest.raises(
        SelfCreatedNerveError,
        match=f"canary_effective_boundary_failed:{field}",
    ):
        promote_self_created_nerve(
            candidate,
            review=review,
            canary=canary,
            william_approval_ref="chat_messages.id=117550",
            registry_path=tmp_path / "registry.json",
            root=tmp_path,
        )


def test_declarative_v1_canary_is_not_promotable(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    valid_canary = _canary(candidate, tmp_path)
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        canary=valid_canary,
        reviewer_ref="chat_messages.id=117558",
        root=tmp_path,
    )
    canary = _canary(
        candidate,
        tmp_path,
        schema="seal.nerves.self_created_canary.v1",
    )
    with pytest.raises(
        SelfCreatedNerveError,
        match="canary_boundary_failed:schema",
    ):
        promote_self_created_nerve(
            candidate,
            review=review,
            canary=canary,
            william_approval_ref="chat_messages.id=117550",
            registry_path=tmp_path / "registry.json",
            root=tmp_path,
        )


def test_review_is_bound_to_exact_effective_canary(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        canary=canary,
        reviewer_ref="chat_messages.id=117558",
        root=tmp_path,
    )
    replacement = _canary(
        candidate,
        tmp_path,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=9)
        ).isoformat(),
    )
    with pytest.raises(
        SelfCreatedNerveError,
        match="review_canary_hash_mismatch",
    ):
        promote_self_created_nerve(
            candidate,
            review=review,
            canary=replacement,
            william_approval_ref="chat_messages.id=117550",
            registry_path=tmp_path / "registry.json",
            root=tmp_path,
        )


def test_fabricated_artifact_hashes_fail_closed(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)
    canary["artifacts"]["worker"]["sha256"] = "0" * 64
    canary["canary_sha256"] = _payload_hash(canary, "canary_sha256")
    with pytest.raises(
        SelfCreatedNerveError,
        match="canary_artifact_hash_mismatch:worker",
    ):
        build_sibling_review(
            candidate,
            reviewer="NEXUS",
            verdict="APPROVED",
            evidence_sha256=_sha("review"),
            canary=canary,
            reviewer_ref="chat_messages.id=117558",
            root=tmp_path,
        )


def test_caller_cannot_inject_authority_verifier(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        canary=canary,
        reviewer_ref="chat_messages.id=117558",
        root=tmp_path,
    )
    with pytest.raises(TypeError, match="authority_verifier"):
        promote_self_created_nerve(
            candidate,
            review=review,
            canary=canary,
            william_approval_ref="chat_messages.id=117574",
            authority_verifier=lambda *_args: True,  # type: ignore[call-arg]
            registry_path=tmp_path / "registry.json",
            root=tmp_path,
        )


def test_trusted_authority_verifier_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        canary=canary,
        reviewer_ref="chat_messages.id=117558",
        root=tmp_path,
    )
    def reject(*_args: object) -> None:
        raise SelfCreatedNerveError("trusted_authority_row_missing")

    monkeypatch.setattr(
        self_created,
        "_verify_trusted_chat_authority",
        reject,
    )
    with pytest.raises(SelfCreatedNerveError, match="trusted_authority_row_missing"):
        promote_self_created_nerve(
            candidate,
            review=review,
            canary=canary,
            william_approval_ref="chat_messages.id=117574",
            registry_path=tmp_path / "registry.json",
            root=tmp_path,
        )


def test_trusted_chat_authority_requires_exact_authenticated_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)
    issued_at = datetime.fromisoformat(canary["issued_at"])
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        canary=canary,
        reviewer_ref="chat_messages.id=7001",
        reviewed_at=(issued_at + timedelta(seconds=1)).isoformat(),
        root=tmp_path,
    )
    rows = [
        {
            "id": 7001,
            "sender_type": "user",
            "sender_name": "NEXUS",
            "channel": "web_chat",
            "content": (
                "NERVES_REVIEW_V1 "
                f"candidate_id={candidate['candidate_id']} "
                f"canary_sha256={canary['canary_sha256']} "
                "verdict=APPROVED"
            ),
            "created_at": issued_at + timedelta(seconds=1),
            "metadata": json.dumps({"session_user": "NEXUS"}),
        },
        {
            "id": 7002,
            "sender_type": "user",
            "sender_name": "William",
            "channel": "web_chat",
            "content": (
                "NERVES_APPROVAL_V1 "
                f"candidate_id={candidate['candidate_id']} "
                f"canary_sha256={canary['canary_sha256']} "
                f"review_sha256={review['review_sha256']} "
                "action=ACTIVATE_A2"
            ),
            "created_at": issued_at + timedelta(seconds=2),
            "metadata": json.dumps({"session_user": "William"}),
        },
    ]
    monkeypatch.setattr(
        self_created,
        "_fetch_chat_authority_rows",
        lambda _ids: rows,
    )
    REAL_VERIFY_TRUSTED_CHAT_AUTHORITY(
        candidate,
        review,
        canary,
        "chat_messages.id=7002",
    )

    rows[1]["content"] = "sigue con tus recomendaciones ADA, luz verde"
    with pytest.raises(
        SelfCreatedNerveError,
        match="trusted_william_statement_invalid",
    ):
        REAL_VERIFY_TRUSTED_CHAT_AUTHORITY(
            candidate,
            review,
            canary,
            "chat_messages.id=7002",
        )


def test_fabricated_systemd_attestation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)

    def missing_unit(*_args: object) -> dict:
        raise SelfCreatedNerveError("canary_systemd_worker_record_invalid")

    monkeypatch.setattr(
        self_created,
        "_load_live_systemd_attestation",
        missing_unit,
    )
    with pytest.raises(
        SelfCreatedNerveError,
        match="canary_systemd_worker_record_invalid",
    ):
        build_sibling_review(
            candidate,
            reviewer="NEXUS",
            verdict="APPROVED",
            evidence_sha256=_sha("review"),
            canary=canary,
            reviewer_ref="chat_messages.id=117558",
            root=tmp_path,
        )


def test_systemd_bind_sources_must_match_hashed_artifacts(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)
    unit = canary["effective_boundary"]["unit"]
    properties = FAKE_SYSTEMD_ATTESTATIONS[unit]["properties"]
    properties["BindReadOnlyPaths"] = properties[
        "BindReadOnlyPaths"
    ].replace(
        str(
            (
                tmp_path
                / "skills/seal-nerves-self-creation/scripts"
                / "effective_a2_worker.py"
            ).resolve()
        ),
        "/tmp/fake_worker.py",
    )
    with pytest.raises(
        SelfCreatedNerveError,
        match="canary_systemd_source_binding_invalid",
    ):
        build_sibling_review(
            candidate,
            reviewer="NEXUS",
            verdict="APPROVED",
            evidence_sha256=_sha("review"),
            canary=canary,
            reviewer_ref="chat_messages.id=117558",
            root=tmp_path,
        )


def test_three_observations_in_two_windows_are_mandatory(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    with pytest.raises(SelfCreatedNerveError, match="candidate_schema_invalid"):
        propose_self_created_nerve(
            agent="ADA",
            name="learned-safe-probe",
            pattern_id="repeated-safe-audit",
            observations=_observations()[:2],
            skill_dir=skill,
            output_dir=tmp_path / "ledger",
            root=tmp_path,
        )
    same_day = _observations()
    same_day[2]["observed_at"] = "2026-07-22T22:00:00+00:00"
    with pytest.raises(SelfCreatedNerveError, match="two_time_windows"):
        propose_self_created_nerve(
            agent="ADA",
            name="learned-safe-probe",
            pattern_id="repeated-safe-audit",
            observations=same_day,
            skill_dir=skill,
            output_dir=tmp_path / "ledger-2",
            root=tmp_path,
        )


def test_skill_drift_invalidates_reviewed_candidate(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    (tmp_path / "skills" / "learned-safe-probe" / "SKILL.md").write_text(
        "drift\n",
        encoding="utf-8",
    )
    with pytest.raises(SelfCreatedNerveError, match="skill_bundle_hash_mismatch"):
        compile_self_created_mission(
            candidate,
            objective="This must fail because the reviewed skill changed.",
            root=tmp_path,
        )


def test_william_approval_is_required_for_activation(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    canary = _canary(candidate, tmp_path)
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        canary=canary,
        reviewer_ref="chat_messages.id=117558",
        root=tmp_path,
    )
    with pytest.raises(SelfCreatedNerveError, match="william_approval_required"):
        promote_self_created_nerve(
            candidate,
            review=review,
            canary=canary,
            william_approval_ref="",
            registry_path=tmp_path / "registry.json",
            root=tmp_path,
        )
