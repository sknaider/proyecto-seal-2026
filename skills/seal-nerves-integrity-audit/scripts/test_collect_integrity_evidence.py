from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("collect_integrity_evidence.py")
SPEC = importlib.util.spec_from_file_location("collect_integrity_evidence", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _checks(*, failed_user: str = "", identity_rc: int = 0) -> list[dict]:
    def item(label: str, returncode: int = 0, stdout: str = "") -> dict:
        return {
            "label": label,
            "returncode": returncode,
            "stdout_tail": stdout,
            "stderr_tail": "",
        }

    return [
        item("dependency_diff"),
        item("dependency_identity", identity_rc),
        item("dependency_absolute"),
        item("failed_user_units", stdout=failed_user),
        item("failed_system_units"),
    ]


def test_failed_soul_unit_overrides_curated_green() -> None:
    checks = _checks(
        failed_user="seal-instinct-cron.service loaded failed failed"
    )
    assert MODULE._classify(checks) == "real_regression"


def test_unrelated_failed_os_unit_does_not_create_soul_regression() -> None:
    checks = _checks(
        failed_user="update-notifier-crash.service loaded failed failed"
    )
    assert MODULE._classify(checks) == "healthy"


def test_identity_instrument_failure_is_unknown_not_green() -> None:
    assert MODULE._classify(_checks(identity_rc=2)) == "instrument_unavailable"


def test_mission_scope_rejects_write_risk(tmp_path: Path) -> None:
    mission = {
        "agent": "JARVIS",
        "specialty": "architecture_integrity_audit",
        "risk_class": "A3_REVERSIBLE_WRITE",
        "scope": {"network": "none"},
        "expected_evidence": ["typed"],
        "termination_conditions": ["stop"],
    }
    path = tmp_path / "mission.json"
    path.write_text(json.dumps(mission), encoding="utf-8")
    with pytest.raises(MODULE.MissionScopeError):
        MODULE._load_mission(path)
