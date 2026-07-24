from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from tools import nerves_a2_soak_monitor as soak


NOW = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)


def _root(tmp_path: Path) -> Path:
    for relative in soak.RELEASE_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n", encoding="utf-8")
    return tmp_path


def _show(_unit: str, prop: str) -> str:
    return {
        "ActiveState": "active",
        "UnitFileState": "enabled",
        "LastTriggerUSec": "Fri 2026-07-24 17:59:00 UTC",
        "Result": "success",
        "ExecMainStatus": "0",
    }[prop]


def _canary(
    path: Path,
    fingerprint: str,
    recorded_at: datetime,
    *,
    agent: str = "ADA",
) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    value = {
        "schema": "seal.nerves.a2-soak-canary.v1",
        "canary_id": f"real-a2-canary-{agent.lower()}",
        "release_fingerprint": fingerprint,
        "recorded_at": recorded_at.isoformat(),
        "kind": "A2_ROUTE_CANARY",
        "ok": True,
        "risk_class": "A2_READ_ONLY",
        "mutations": 0,
        "evidence_sha256": hashlib.sha256(b"evidence").hexdigest(),
        "agent": agent,
        "signals_seen": 1,
        "missions_created": 1,
        "missions_coalesced": 0,
        "duplicate_side_effects": 0,
        "worker_successes": 1,
        "worker_failures": 0,
        "evidence_attempts": 1,
        "evidence_rejections": 0,
        "false_wakes": 0,
        "dead_letters": 0,
        "verified_effects": 1,
        "estimated_cost_units": 1,
        "predicted_utility": 0.8,
        "actual_utility": 1.0,
        "confidence": 0.8,
        "outcome": 1,
        "human_corrections": 0,
        "harm_avoided": 1,
        "harm_avoided_method": "sentinel_hash_unchanged",
        "principal_ack_latency_ms": 1000 if agent == "ADA" else None,
    }
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_soak_starts_private_and_passes_only_after_24h_with_canary(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path / "workspace")
    state_path = tmp_path / "state/state.json"
    canary_dir = tmp_path / "canaries"
    first = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=canary_dir,
        root=root,
        show=_show,
        now=NOW,
    )
    assert first["status"] == "SOAKING"
    assert state_path.stat().st_mode & 0o777 == 0o600
    for agent in soak.REQUIRED_A2_ROUTES:
        _canary(
            canary_dir / f"{agent.lower()}.json",
            first["release_fingerprint"],
            NOW,
            agent=agent,
        )
    final = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=canary_dir,
        root=root,
        show=_show,
        now=NOW + timedelta(hours=24, seconds=1),
    )
    assert final["status"] == "PASSED"
    assert final["last_sample"]["non_vacuous_canaries"] == 5
    assert final["last_sample"]["metrics"]["principal_ack_p95_ms"] == 1000


def test_failed_unit_is_sticky(tmp_path: Path) -> None:
    root = _root(tmp_path / "workspace")
    state_path = tmp_path / "state/state.json"

    def failed_show(unit: str, prop: str) -> str:
        if unit == "seal-nerves-daemon.service" and prop == "Result":
            return "exit-code"
        return _show(unit, prop)

    state = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=tmp_path / "canaries",
        root=root,
        show=failed_show,
        now=NOW,
    )
    assert state["status"] == "FAILED"
    later = soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=tmp_path / "canaries",
        root=root,
        show=_show,
        now=NOW + timedelta(minutes=5),
    )
    assert later["status"] == "FAILED"


def test_release_drift_fails_closed(tmp_path: Path) -> None:
    root = _root(tmp_path / "workspace")
    state_path = tmp_path / "state/state.json"
    soak.sample(
        release_id="release-1",
        state_path=state_path,
        canary_dir=tmp_path / "canaries",
        root=root,
        show=_show,
        now=NOW,
    )
    (root / soak.RELEASE_FILES[0]).write_text("drift\n", encoding="utf-8")
    with pytest.raises(soak.SoakError, match="release_changed"):
        soak.sample(
            release_id="release-1",
            state_path=state_path,
            canary_dir=tmp_path / "canaries",
            root=root,
            show=_show,
            now=NOW + timedelta(minutes=5),
        )


def test_private_canary_and_boundary_are_required(tmp_path: Path) -> None:
    root = _root(tmp_path / "workspace")
    fingerprint = soak.release_fingerprint(root)
    canary = tmp_path / "canaries/bad.json"
    _canary(canary, fingerprint, NOW)
    value = json.loads(canary.read_text(encoding="utf-8"))
    value["mutations"] = 1
    canary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(soak.SoakError, match="canary_boundary_failed"):
        soak._canaries(NOW, fingerprint, canary_dir=canary.parent)
