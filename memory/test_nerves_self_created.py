from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from nerves_self_created import (
    SelfCreatedNerveError,
    _payload_hash,
    build_sibling_review,
    compile_self_created_mission,
    load_active_self_created_nerves,
    promote_self_created_nerve,
    propose_self_created_nerve,
)


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


def _canary(candidate: dict, **overrides) -> dict:
    canary = {
        "schema": "seal.nerves.self_created_canary.v1",
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate["candidate_sha256"],
        "skill_bundle_sha256": candidate["skill"]["bundle_sha256"],
        "passed": True,
        "risk_class": "A2_READ_ONLY",
        "allowed_tools": [],
        "network": "none",
        "mutations": 0,
        "evidence_sha256": _sha("canary-evidence"),
    }
    canary.update(overrides)
    canary["canary_sha256"] = _payload_hash(canary, "canary_sha256")
    return canary


def test_end_to_end_promotes_only_a2_metadata(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
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
        canary=_canary(candidate),
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
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        root=tmp_path,
    )
    with pytest.raises(SelfCreatedNerveError, match=f"canary_boundary_failed:{field}"):
        promote_self_created_nerve(
            candidate,
            review=review,
            canary=_canary(candidate, **override),
            william_approval_ref="chat_messages.id=117550",
            registry_path=tmp_path / "registry.json",
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
    review = build_sibling_review(
        candidate,
        reviewer="NEXUS",
        verdict="APPROVED",
        evidence_sha256=_sha("review"),
        root=tmp_path,
    )
    with pytest.raises(SelfCreatedNerveError, match="william_approval_required"):
        promote_self_created_nerve(
            candidate,
            review=review,
            canary=_canary(candidate),
            william_approval_ref="",
            registry_path=tmp_path / "registry.json",
            root=tmp_path,
        )
