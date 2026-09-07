from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import stat

import pytest

from memory.nerves_mission_shadow import (
    MissionCompileError,
    ShadowMissionLedger,
    compile_jarvis_integrity_mission,
    latest_actionable_episode,
    write_shadow_manifest,
)


def _artifact(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "jarvis.jsonl"
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _finding(ts: str = "2026-07-23T16:00:00+00:00") -> dict:
    return {
        "ts": ts,
        "agent": "JARVIS",
        "action": "integrity_pulse",
        "state": "FINDING",
        "findings": ["REGRESIÓN en --diff: seal-example ROTO"],
        "detail": "synthetic",
        "status": "issue",
    }


def _compile(path: Path, record: dict, anchor: str = "genesis") -> dict:
    return compile_jarvis_integrity_mission(
        record,
        artifact_path=path,
        episode_anchor=anchor,
        workspace_root=path.parent,
        now=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )


def test_latest_actionable_episode_resets_after_green() -> None:
    first = _finding("2026-07-23T10:00:00+00:00")
    second = _finding("2026-07-23T10:02:00+00:00")
    green = {**_finding("2026-07-23T10:05:00+00:00"), "state": "GREEN", "findings": []}
    third = _finding("2026-07-23T10:10:00+00:00")
    record, anchor = latest_actionable_episode([first, second, green, third])
    assert record["ts"] == third["ts"]
    assert anchor == green["ts"]


def test_compile_produces_schema_valid_read_only_mission(tmp_path: Path) -> None:
    record = _finding()
    path = _artifact(tmp_path, [record])
    mission = _compile(path, record)
    assert mission["agent"] == "JARVIS"
    assert mission["nerve_layer"] == "AGENT_ROLE"
    assert mission["risk_class"] == "A2_READ_ONLY"
    assert mission["scope"]["network"] == "none"
    assert mission["rollback"]["required"] is False
    assert mission["skills"][0]["sha256"]


def test_non_actionable_green_fails_closed(tmp_path: Path) -> None:
    record = {**_finding(), "state": "GREEN", "findings": []}
    path = _artifact(tmp_path, [record])
    with pytest.raises(MissionCompileError, match="not_actionable"):
        _compile(path, record)


def test_explicit_validation_canary_admits_only_clean_green(
    tmp_path: Path,
) -> None:
    record = {**_finding(), "state": "GREEN", "findings": []}
    path = _artifact(tmp_path, [record])
    mission = compile_jarvis_integrity_mission(
        record,
        artifact_path=path,
        episode_anchor="p5-validation",
        validation_canary=True,
        workspace_root=path.parent,
    )
    assert mission["drive"] == "proactive"
    assert mission["risk_class"] == "A2_READ_ONLY"
    assert "healthy JARVIS" in mission["objective"]

    with pytest.raises(MissionCompileError, match="requires_GREEN"):
        compile_jarvis_integrity_mission(
            _finding(),
            artifact_path=path,
            episode_anchor="p5-validation",
            validation_canary=True,
            workspace_root=path.parent,
        )


def test_same_episode_coalesces_to_one_mission(tmp_path: Path) -> None:
    first = _finding("2026-07-23T10:00:00+00:00")
    repeated = _finding("2026-07-23T10:02:00+00:00")
    path = _artifact(tmp_path, [first, repeated])
    first_mission = _compile(path, first)
    repeated_mission = _compile(path, repeated)
    assert first_mission["mission_id"] == repeated_mission["mission_id"]
    ledger = ShadowMissionLedger(tmp_path / "missions.jsonl")
    opened = ledger.open_or_join(first_mission)
    joined = ledger.open_or_join(repeated_mission)
    assert opened.created is True
    assert joined.created is False
    assert joined.sequence == opened.sequence == 1
    assert ledger.verify().ok


def test_green_boundary_starts_new_episode(tmp_path: Path) -> None:
    finding = _finding()
    path = _artifact(tmp_path, [finding])
    before = _compile(path, finding, "genesis")
    after = _compile(path, finding, "2026-07-23T11:00:00+00:00")
    assert before["mission_id"] != after["mission_id"]


def test_ledger_is_owner_only_and_tamper_evident(tmp_path: Path) -> None:
    record = _finding()
    artifact = _artifact(tmp_path, [record])
    mission = _compile(artifact, record)
    path = tmp_path / "missions.jsonl"
    ledger = ShadowMissionLedger(path)
    ledger.open_or_join(mission)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace("A2_READ_ONLY", "A3_REVERSIBLE_WRITE"), encoding="utf-8")
    assert not ledger.verify().ok


def test_manifest_is_immutable_owner_only_and_idempotent(tmp_path: Path) -> None:
    record = _finding()
    artifact = _artifact(tmp_path, [record])
    mission = _compile(artifact, record)
    manifest_dir = tmp_path / "manifests"
    first = write_shadow_manifest(mission, manifest_dir)
    second = write_shadow_manifest(mission, manifest_dir)
    assert first == second
    assert stat.S_IMODE(manifest_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    tampered = {**mission, "risk_class": "A3_REVERSIBLE_WRITE"}
    with pytest.raises(MissionCompileError, match="content_mismatch"):
        write_shadow_manifest(tampered, manifest_dir)
